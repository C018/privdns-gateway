# 架构

## 约束与路线选择

当前网络条件（已确认）：

1. 手机不开 VPN
2. 手机只能通过【一个内网地址】访问 JP
3. 手机不能直接访问 HK/TW
4. JP 与 HK/TW 之间无内网，只能走公网
5. HK/TW 已部署 SS2022

因此**不能**采用：手机端 Tailscale/WireGuard/Clash/sing-box、fake-ip 本地接管、
手机直连 HK/TW 内网、DNS 返回多个策略 VIP、依赖 fake-ip 池。

正确路线：

> 所有需要代理的域名，DNS 都返回 **JP 唯一内网 IP**；
> JP 上的 sing-box 根据 sniff 到的域名，再分流到 HK / TW / JP 出口。

## DNS 层 (dnsdist)

JP 用 dnsdist 对外提供加密 DNS：Android DoT 853，iOS DoH 8443。

| 域名分类 | DNS 行为 |
|---|---|
| 命中代理规则 | 返回 JP 唯一内网 IP (A)，AAAA → NODATA，HTTPS/SVCB → 空 |
| DIRECT | 返回真实 IP（手机直连，不经 JP） |
| BLOCK | NXDOMAIN |

- 代理域名只返回 A；屏蔽 AAAA 避免 IPv6 绕过，屏蔽 HTTPS/SVCB 降低 ECH/HTTP3/QUIC 复杂度。
- TTL 60–120s，方便改规则后快速生效（`pdg.conf` 默认 120）。

dnsdist 适合对外提供 DoT/DoH，也方便做 DNS 层规则控制 —— 因此 DNS 层继续用它。

## 流量层 (sing-box)

```
手机访问 chatgpt.com
  → DNS 返回 JP 唯一内网 IP
  → 手机连 JP:443, TLS SNI = chatgpt.com
  → JP sing-box sniff 域名
  → 匹配 AI 策略 → 转发到 TW SS2022 outbound
```

### 为什么直接用 sing-box（而非 HAProxy → sniproxy）
HK/TW 已部署 SS2022，直接 `JP sing-box → HK/TW SS2022` 即可：
链路全程加密、HK/TW 不必开放 sniproxy 80/443、不易变成 open SNI proxy、
规则/UDP/QUIC/fallback 更好扩展、复用现有 SS2022、后续 TG Bot 管理更自然。

### 关键：透明入口，不是代理协议入口
手机访问 `JP:443` 时**不是**在使用 HTTP/SOCKS 代理协议，
所以 sing-box **不能**用普通 `mixed` inbound 当客户端代理入口。
必须用透明入口：`tproxy` / `redirect` 配合 `nftables`。

```
手机 → JP内网IP:443
  → nftables TPROXY 把连接交给 sing-box
  → sing-box sniff TLS SNI / HTTP Host
  → 按规则转发到 hk-ss2022 / tw-ss2022 / jp / direct
```

分工：dnsdist 负责 DNS 引导；nftables/tproxy 负责把普通连接送进 sing-box；
sing-box 负责 sniff 与分流。**务必验证**普通 HTTPS 连接没被误当成代理连接处理。

> **实测注 (Path A, 见 docs/production-notes.md)**：当 DNS 把代理域名 spoof 到 **JP 服务器自己的 IP**
> 时（5GPN 的做法），连接本就发到本机，因此 **不需要 tproxy/nftables**——sing-box 直接用 `direct`
> inbound 普通监听 80/443 + `sniff` + `sniff_override_destination` 靠嗅到的 SNI 拨号即可。
> tproxy 那套是给「目标是其它 IP、需透明拦截」的拓扑准备的。⚠️ 用 sing-box **1.12.x**：1.13 移除了
> `sniff_override_destination`，其 `action: sniff` 不覆盖目标地址，会走不通。

## 规则系统

- 上层规则兼容 Surge 风格（`DOMAIN` / `DOMAIN-SUFFIX` / `DOMAIN-KEYWORD` / `DOMAIN-REGEX` / `RULE-SET` / `FINAL`）。
- Surge `.list` ≠ sing-box 原生 rule-set，本项目做**转换**：Surge `.list` → 解析 → 同时生成 dnsdist DNS 规则与 sing-box route 规则。
- V1 暂不支持客户端侧能力（`PROCESS-NAME` / `USER-AGENT` / `URL-REGEX` / `IP-CIDR` / `GEOIP` / `DEST-PORT` / `SRC-IP`）——
  手机不开 VPN，服务端 DNS/SNI 网关无法可靠处理这些。解析时这类规则会被跳过并计数告警。

### 单一规则源（核心）
DNS 与 sing-box 必须由同一份 `rules.conf` 生成，否则两者会不一致。
`pdg` 的编译器把 `rules.conf`（含展开后的 RULE-SET）编译成一张有序表 `CompiledTable`，
再分别投影成 dnsdist 的 dns_mode 分组（spoof/direct/block）与 sing-box 的出口分组。

### 出口与 DNS 行为 (dns_mode)
每个出口有一个 `dns_mode`，决定其域名的 DNS 行为：

- `spoof`：返回 JP 内网 IP，流量进 JP 再分流（远程 SS2022 出口 + 默认 `jp`）
- `direct`：返回真实 IP，手机直连，不经 JP
- `block`：NXDOMAIN

项目默认（墙内推荐）：**全代理 + 国内直连** —— `Final → jp (spoof)`，未命中域名默认走 JP 代理
（不会漏：没列的国际站也能用），再用 `China` / `ChinaMedia` 规则集把国内站 `direct`。
dnsdist 在全代理模式下「默认 spoof、仅排除 direct 域名」。

> 另一种模型是**白名单**：把 `policies.conf` 的 `Final` 改为 `direct`，只代理显式列出的域名、其余直连。
> 国内天然快，但漏列的国际站在墙内会连不上。dnsdist 会自动切到「只 spoof 白名单」逻辑。

## 来源分流与 WiFi（墙内内网卡场景）

实际部署：用户在墙内，手机靠一张**内网出口卡**把移动流量经运营商内网送到 JP 私网
（家里 WiFi 若有路由器隧道，同理）。普通 WiFi 没有这条内网路径。

因此存在**两条独立链路**，地址不同：

| 链路 | 终点 | 用什么地址 | 谁能到 |
|---|---|---|---|
| DNS | JP dnsdist (DoT/DoH) | JP **公网** | 内网卡 / WiFi 都能查 |
| 流量入口 | JP sing-box | JP **私网** (spoof 目标) | 仅内网卡 / 隧道 |

> **DNS 必须公网暴露**。若 DNS 也只走私网，普通 WiFi 上连 DNS 都查不了，
> Android 严格 Private DNS 是 fail-closed → **整机没网**。

### 按 DNS 查询来源分流
DNS 公网后，dnsdist 能看到查询来源 IP，据此决定要不要 spoof（`internal_src_cidr`）：

- 来源 ∈ 内网段（内网卡 / 隧道，**地址固定**）→ 代理域名 spoof 成 JP 私网 IP → 走代理。
- 来源 = 其他（普通 WiFi）→ 代理域名返回**真实 IP** → 直连。

效果：**用内网卡/家里 WiFi 时全功能代理；切普通 WiFi 自动全直连、不至于没网。**
判据是「内网段源 IP」而非「家里动态公网 IP」，所以**家宽动态 IP 不影响**，无需 DDNS。

> 要点：让家里 WiFi 也走代理，需在家用路由器上把 JP 的 DNS/流量入口地址**路由进隧道**，
> 使其查询从隧道私网源到达 JP（落进 `internal_src_cidr`）。

### Google / Play（墙内必须代理）
所有流量堆 JP 出口会拖死 Play（`gvt1.com` 下载源）。本项目把 Google 系分到 **HK 出口**，
并靠「屏蔽 HTTPS/SVCB + 丢 UDP 443 强制回落 TCP」规避 QUIC 问题。
隧道侧还需 **MSS clamp**，否则大下载（app 更新）会因 MTU 黑洞卡死——详见部署文档。

## 端口规划 (V1 建议)

| 端口 | 服务 | 用途 |
|---|---|---|
| 853/tcp | dnsdist DoT | Android Private DNS |
| 8443/tcp | dnsdist DoH | iOS DoH |
| 80/tcp | sing-box (经 nftables TPROXY) | HTTP 透明入口 |
| 443/tcp | sing-box (经 nftables TPROXY) | HTTPS 透明入口 |

DoH 不要与流量入口抢同一个 443（除非用独立 IP / 独立域名 + 明确反代）。

## UDP / QUIC (V1 策略)
先跑稳 TCP 80/443；UDP 443 暂不处理或在 JP 侧丢弃让客户端回落 TCP。
确认 HK/TW SS2022 的 UDP 能力后，V5 再做透明 UDP / QUIC。
