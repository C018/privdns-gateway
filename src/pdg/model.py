"""核心数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field

# V1 支持的规则类型 (服务端 DNS/SNI 网关可可靠处理的部分)。
RULE_TYPES = ("DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-REGEX")

# 出口的 DNS 行为。
#   spoof  → DNS 返回 JP 唯一内网 IP, 流量进 JP 再由 sing-box 分流
#   direct → DNS 返回真实 IP, 手机直接访问, 不经 JP
#   block  → NXDOMAIN
DNS_MODES = ("spoof", "direct", "block")


@dataclass(frozen=True)
class Rule:
    """一条域名规则。"""

    type: str          # RULE_TYPES 之一
    value: str         # 域名 / 后缀 / 关键字 / 正则
    policy: str        # 策略名 (见 policies.conf)
    source: str = "local"   # "local" 或来源 RULE-SET 的 url


@dataclass(frozen=True)
class RuleSetRef:
    """rules.conf 里的一条 RULE-SET 引用。"""

    url: str
    policy: str


@dataclass
class Outbound:
    """一个出口 (远程 SS2022 或内置 direct/block/jp)。"""

    tag: str
    type: str                       # shadowsocks / direct / block
    dns_mode: str                   # DNS_MODES 之一
    params: dict = field(default_factory=dict)


@dataclass
class Config:
    """合并后的运行期配置 (pdg.conf + policies.conf)。"""

    jp_internal_ip: str
    http_port: int
    https_port: int
    dot_port: int
    doh_port: int
    upstream: list[str]
    spoof_ttl: int
    proxy_block_aaaa: bool
    proxy_block_https: bool
    tls_cert: str
    tls_key: str
    dns_hostname: str
    outbounds: dict[str, Outbound]   # tag -> Outbound
    policies: dict[str, str]         # 策略名 -> 出口 tag
    tproxy_port: int = 7895          # sing-box 透明入口 (nftables TPROXY 目标端口)
    # 内网来源段: 哪些 DNS 查询来源算"走内网到 JP"(内网卡段/隧道段)。
    # 只有来自这些来源的代理域名才 spoof; 留空 = 不分来源, 一律 spoof。
    internal_src_cidr: list[str] = field(default_factory=list)
    # 443 入口是否同时收 UDP(QUIC)。Play/YouTube 重度用 QUIC, 需出口 SS2022 支持 UDP。
    quic: bool = True


@dataclass(frozen=True)
class CompiledRule:
    """规则编译后附带策略/出口/DNS 行为的结果。"""

    rule: Rule
    policy: str
    outbound: str
    dns_mode: str
