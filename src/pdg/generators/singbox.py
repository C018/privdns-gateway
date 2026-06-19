"""生成 sing-box 配置 (Path A: 普通监听 + sniff_override, 复用上游 5GPN 的 DNS)。

已在 JP 实测的写法 (详见 docs/production-notes.md):
- 目标 sing-box 1.12.x。⚠️ 1.13 移除了 sniff_override_destination, 勿升级
  (1.13 的 `action: sniff` 不覆盖目标地址, 实测出口会去连服务器自己 → 走不通)。
- 上游 DNS 把代理域名 spoof 到服务器自己的 IP, 所以 sing-box 用 `direct` inbound +
  sniff + sniff_override_destination 靠嗅到的 SNI 拨号 (普通监听, 不需要 tproxy/nftables)。
- 443 入口同时收 TCP+UDP(QUIC, 需出口 SS2022 支持 UDP); 80 入口收 TCP。
"""

from __future__ import annotations

import json

from ..model import Config
from ..rules.compiler import CompiledTable


def _build_outbound(tag: str, config: Config) -> dict:
    ob = config.outbounds[tag]
    if ob.type == "shadowsocks":
        p = ob.params
        # 不设 network → 同时支持 TCP+UDP(QUIC)。
        return {
            "type": "shadowsocks",
            "tag": tag,
            "server": p.get("server", "CHANGE_ME"),
            "server_port": int(p.get("server_port", 0)),
            "method": p.get("method", "2022-blake3-aes-128-gcm"),
            "password": p.get("password", "CHANGE_ME"),
        }
    if ob.type == "block":
        return {"type": "block", "tag": tag}
    # direct (含内置 jp)
    return {"type": "direct", "tag": tag}


def _sniff_inbound(tag: str, port: int, *, udp: bool) -> dict:
    ib = {
        "type": "direct",
        "tag": tag,
        "listen": "0.0.0.0",
        "listen_port": port,
        "sniff": True,
        "sniff_override_destination": True,
        "sniff_timeout": "300ms",
    }
    if not udp:
        ib["network"] = "tcp"
    return ib


def generate(table: CompiledTable, config: Config) -> str:
    by_outbound = table.matchers_by_outbound()

    # 只发出实际用到的出口 + final (避免 1.12 中已弃用的 block 等无谓出现)。
    used = set(by_outbound) | {table.final_outbound}
    outbounds = [_build_outbound(tag, config) for tag in sorted(used)]

    # route 规则: 每个出口一条, 聚合其匹配器 (跳过空键)。
    rules = []
    for tag, m in by_outbound.items():
        rule: dict = {}
        for key in ("domain", "domain_suffix", "domain_keyword", "domain_regex"):
            if m[key]:
                rule[key] = m[key]
        if rule:
            rule["outbound"] = tag
            rules.append(rule)

    conf = {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": [
            _sniff_inbound("in-https", config.https_port, udp=config.quic),  # 443: TCP(+QUIC)
            _sniff_inbound("in-http", config.http_port, udp=False),          # 80: TCP
        ],
        "outbounds": outbounds,
        "route": {
            "rules": rules,
            "final": table.final_outbound,
            "auto_detect_interface": True,
        },
    }
    return json.dumps(conf, indent=2, ensure_ascii=False) + "\n"
