"""配置与路径解析。

支持两种运行场景:
  - 已安装 (生产 JP 机): /etc/pdg, /var/lib/pdg, /var/log/pdg
  - 仓库内开发: 直接用 ./config 与 ./var (无需 root)

解析优先级: 环境变量 PDG_ETC/PDG_VAR/PDG_LOG > /etc/pdg 存在则系统路径 > 仓库相对路径。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .model import Config, Outbound

# 内置出口的默认 DNS 行为。
BUILTIN_DNS_MODE = {"direct": "direct", "block": "block", "jp": "spoof"}
BUILTIN_OUTBOUND_TYPE = {"direct": "direct", "jp": "direct", "block": "block"}

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Paths:
    etc: Path
    var: Path
    log: Path

    @property
    def pdg_conf(self) -> Path:
        return self.etc / "pdg.conf"

    @property
    def rules_conf(self) -> Path:
        return self.etc / "rules.conf"

    @property
    def policies_conf(self) -> Path:
        return self.etc / "policies.conf"

    @property
    def cache_dir(self) -> Path:
        return self.var / "rulesets" / "cache"

    @property
    def backup_dir(self) -> Path:
        return self.var / "backup"

    @property
    def dnsdist_out(self) -> Path:
        # 系统场景写 /etc/dnsdist; 开发场景写 var/out
        sys_dir = Path("/etc/dnsdist")
        if self.etc == Path("/etc/pdg") and sys_dir.is_dir():
            return sys_dir / "pdg-generated.lua"
        return self.var / "out" / "pdg-generated.lua"

    @property
    def singbox_out(self) -> Path:
        sys_dir = Path("/etc/sing-box")
        if self.etc == Path("/etc/pdg") and sys_dir.is_dir():
            return sys_dir / "config.json"
        return self.var / "out" / "sing-box.config.json"

    @property
    def nftables_out(self) -> Path:
        return self.var / "out" / "pdg.nft"


def resolve_paths() -> Paths:
    env_etc = os.environ.get("PDG_ETC")
    if env_etc:
        etc = Path(env_etc)
        var = Path(os.environ.get("PDG_VAR", str(etc.parent / "var")))
        log = Path(os.environ.get("PDG_LOG", str(etc.parent / "log")))
        return Paths(etc=etc, var=var, log=log)

    if (Path("/etc/pdg/pdg.conf")).exists():
        return Paths(etc=Path("/etc/pdg"),
                     var=Path("/var/lib/pdg"),
                     log=Path("/var/log/pdg"))

    # 开发场景: 仓库内
    return Paths(etc=_REPO_ROOT / "config",
                 var=_REPO_ROOT / "var",
                 log=_REPO_ROOT / "var" / "log")


def load_config(paths: Paths) -> Config:
    """读取 pdg.conf + policies.conf, 合并为 Config。"""
    if not paths.pdg_conf.exists():
        raise FileNotFoundError(f"找不到主配置: {paths.pdg_conf}")

    with paths.pdg_conf.open("rb") as fh:
        raw = tomllib.load(fh)

    gw = raw.get("gateway", {})
    dns = raw.get("dns", {})
    dns_modes = raw.get("dns_modes", {})

    outbounds: dict[str, Outbound] = {}
    # 内置出口
    for tag, otype in BUILTIN_OUTBOUND_TYPE.items():
        mode = dns_modes.get(tag, BUILTIN_DNS_MODE[tag])
        outbounds[tag] = Outbound(tag=tag, type=otype, dns_mode=mode)
    # 用户定义的远程出口
    for tag, spec in raw.get("outbounds", {}).items():
        spec = dict(spec)
        mode = spec.pop("dns_mode", dns_modes.get(tag, "spoof"))
        otype = spec.pop("type", "shadowsocks")
        outbounds[tag] = Outbound(tag=tag, type=otype, dns_mode=mode, params=spec)

    policies = _load_policies(paths)

    return Config(
        jp_internal_ip=gw.get("jp_internal_ip", "10.0.0.1"),
        http_port=int(gw.get("http_port", 80)),
        https_port=int(gw.get("https_port", 443)),
        dot_port=int(dns.get("dot_port", 853)),
        doh_port=int(dns.get("doh_port", 8443)),
        upstream=list(dns.get("upstream", ["1.1.1.1", "8.8.8.8"])),
        spoof_ttl=int(dns.get("spoof_ttl", 120)),
        proxy_block_aaaa=bool(dns.get("proxy_block_aaaa", True)),
        proxy_block_https=bool(dns.get("proxy_block_https", True)),
        tls_cert=dns.get("tls_cert", ""),
        tls_key=dns.get("tls_key", ""),
        dns_hostname=dns.get("dns_hostname", ""),
        outbounds=outbounds,
        policies=policies,
        tproxy_port=int(gw.get("tproxy_port", 7895)),
        internal_src_cidr=list(gw.get("internal_src_cidr", [])),
        quic=bool(gw.get("quic", True)),
    )


def _load_policies(paths: Paths) -> dict[str, str]:
    """解析 policies.conf: `策略名 = 出口tag`。"""
    result: dict[str, str] = {}
    if not paths.policies_conf.exists():
        raise FileNotFoundError(f"找不到策略映射: {paths.policies_conf}")
    for lineno, line in enumerate(paths.policies_conf.read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"{paths.policies_conf}:{lineno} 格式错误 (应为 '策略 = 出口'): {line}")
        name, tag = (p.strip() for p in line.split("=", 1))
        if not name or not tag:
            raise ValueError(f"{paths.policies_conf}:{lineno} 策略或出口为空")
        result[name] = tag
    return result
