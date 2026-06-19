# 生产部署记录 (JP VPS, 2026-06-19)

第一套真机落地，采用 **Path A**：复用已安装的 5GPN（dnsdist + ClouDNS 域名 + 172.22 内网卡），
只把流量层 **sniproxy + quic-proxy 换成 sing-box** 做多出口分流。

## 现网拓扑（实测确认）

- 手机用移动**内网卡**（源段 `172.22.0.0/16`），经卡商私网到达 JP **公网 IP**。
- JP **无 172.22 接口、无隧道、无 NAT**——它是「目的地主机」，不是网关。所以流量靠 **DNS 欺骗**引到 JP，
  不是全隧道（确认过：`ip_forward=1` 但无 172.22 地址、无 masquerade）。
- 5GPN 的 dnsdist：53 口仅放行 172.22；命中 `gfwList` 的域名 spoof 成服务器 IP → 进 sing-box。
- ClouDNS 仅托管域名（DoT 主机名 A 记录指向 JP），不参与解析逻辑。

## sing-box（关键经验）

- **版本 1.12.x**。⚠️ **1.13.0 移除了 `sniff_override_destination`**，升级即废——固定 1.12.x。
- 因为 DNS 把代理域名 spoof 到**服务器自己的 IP**，sing-box 必须用 SNI 拨号 →
  用 `direct` inbound + `sniff: true` + `sniff_override_destination: true`（普通监听，**不需要 tproxy/nftables**）。
  - 1.13 的 `action: sniff` **不覆盖目标地址**（实测出口连到 127.0.0.1），所以走不通；1.12 的 inbound 写法可以。
- `in-https` 监听 `0.0.0.0:443`，**不限 network → 同时收 TCP+UDP(QUIC)**；`in-http` 监听 80 (tcp)。
- 出口：`hk`/`tw` 为 SS2022（method `2022-blake3-aes-128-gcm`，**HK 实测支持 UDP**，QUIC 可走），`jp` 为 direct。
- 路由：AI/Binance→tw；Google/YouTube/媒体/TG→hk；默认→jp。配置见 `deploy/singbox/config.template.json`。

## DNS 层补丁（5GPN dnsdist）

5GPN 的 `gfwList`（`newSuffixMatchNode`）原本缺 `google.com`，导致 `android.clients.google.com`、
`play.google.com` 等 Play 关键域名未被代理。已在 `/etc/dnsdist/dnsdist.conf` 的 gfwList 块
（`googleapis.com` 那行后）追加：`google.com / gstatic.com / googleusercontent.com / ggpht.com /
gvt2.com / gvt3.com / android.com`，`systemctl restart dnsdist` 生效。
（注：`gfwlist.lua` 未被主配置 `dofile`，是无效文件，别往那加。）

## 验证结果

- ✅ YouTube、ChatGPT（TW 出口）、Google 全站、QUIC（UDP 经 HK）
- ✅ 经网关→HK 下载 dl.google.com 9MB 文件 @ ~8MB/s
- ✅ 安卓 `generate_204` 经 HK 返回 204（判网正常）
- ⚠️ **Google Play 更新仍卡「等待中」**：网络/传输已排除（上面全过），判定为 Play 对蜂窝/计费网络的
  队列策略，未解决；可日后把内网卡连接设为「非计费」再试。**网关本身确认可用。**

## 运维

- 固化：`systemctl enable sing-box` + `systemctl disable sniproxy quic-proxy`。
- 回滚：`systemctl stop sing-box && systemctl start sniproxy quic-proxy`。
- 日志：生产用 `warn`（busy 网关 info 会刷盘）。

## 待办 / 下一步

- 把 sing-box 生成器 (`src/pdg/generators/singbox.py`) 改成这套已验证写法（1.12 inbound sniff_override +
  普通监听，去掉 tproxy），让一键安装与实测一致。
- Google CDN 域名是手工逐个加（如 `xn--ngstr-lra8j.com`），易漏；考虑迁 **Path B（全代理+国内直连）**
  从根上免维护。
- Play「等待中」后续排查。
