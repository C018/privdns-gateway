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

---

## 阶段二：迁移到 Path B (mosdns) + TG 管理 bot（2026-06-19/20，已部署）

### DNS 层：dnsdist → mosdns
- mosdns v5.3.4 替换 5GPN 的 dnsdist（53/853，DoT 复用 dnsdist 证书）。dnsdist 已 `disable` 保留作回滚。
- 模型「国内白名单直连 + 其余一律代理（全代理兜底）」：`geosite_cn`(+apple+custom_direct) 直连真实解析；
  其余非国内 → A 劫持到服务器 IP、AAAA/HTTPS 置空。配置见 `deploy/mosdns/config.yaml`。
- 规则来自 geosite.dat（用 `deploy/bot/parse-geosite.py` 手写 protobuf 解析，无 v2dat 依赖）；全量覆盖，不再手补 Google CDN。
- 关键修正（教程避坑③）：AAAA/HTTPS 只对**代理域名**回空、对**直连域名**回真实，否则苹果系 / captive.apple.com 异常。
- ECS：国内 `139.226.48.0/24`，海外中性 `0.0.0.0`。
- 本机自身 DNS：`resolv.conf` → `127.0.0.1`(mosdns)+`1.1.1.1`，修好了原本空解析（bot 才能解析 api.telegram.org）。

### sing-box 调整
- 443 入口加 UDP 处理 QUIC（HK SS2022 支持 UDP）。
- ⚠️ **修复 UDP 自环**：QUIC 嗅探失败的包目标仍是服务器自身 IP → 落 jp 直连又发回自己 → 死循环刷屏（曾 3 分钟 9.5 万行日志）。
  解法：route 首条 `{"ip_cidr":["<本机IP>/32","127.0.0.0/8"],"action":"reject"}`。
- 分流：国内直连 / AI·加密货币 → tw / 其余国际 → hk（`route.final=hk`）。

### TG 管理 bot（`deploy/bot/`）
- `pdg-bot.py`：纯标准库 long-poll，仅认指定 user id；改 sing-box 前备份、`check` 失败自动回滚。
- 功能：状态 / 出口（列表·添加[ss/vmess/trojan/vless]·删除·设默认）/ 分流规则（增·删·域名→出口|direct）/
  规则集（Surge `.list` URL → sing-box 本地 rule_set，可刷新）/ 重启 / 更新规则库。
- systemd `pdg-bot.service`（token + allowed 只写本机，不进版本库）。
- 自定义直连写入 `/etc/mosdns/rules/custom_direct.txt`（已接入 `geosite_cn`）。

### 已知限制
- **Telegram App** 走硬编码 DC IP、不过 DNS 网关 → 始终走内网卡默认出口（日本），无法定向到 HK（除非真 VPN/IP 路由）。
- **TFO**：mosdns DoT 不支持也会优雅回落，Android/iOS 均不会因此断网。

---

## 阶段三：优化 + 功能扩展（2026-06-20，已部署）

### 优化
- **mosdns 加 `cache` 插件**（`lazy_cache`，size 8192 / lazy_ttl 86400）。只接在 `internal_sequence`
  开头（`$lazy_cache` → `jump has_resp`）：该分支按 `qname+qtype` 决定（直连真IP / 代理服IP / 置空），
  与来源无关，缓存安全；普通 WiFi 来源那条（回真实 IP）**不缓存**，避免跨来源污染。
  实测命中即时返回，降时延/上游压力。
- **停用 5GPN 残留 `china-dns-race-proxy`**（监听 127.0.0.1:5301，已被 mosdns 的 `local_upstream`=223.5.5.5
  取代，无引用）。`systemctl disable --now`，省内存/减面。
- **小内存实测**：sing-box ~34MB + mosdns ~31MB + pdg-bot ~24MB ≈ **90MB**；1GB 机 `available 795MB`，
  512MB 小鸡也够。最“虚胖”是 journald(mmap)，可 `SystemMaxUse=50M` 封顶。

### sing-box 加 clash_api + 故障切换组
- `experimental.clash_api`(127.0.0.1:9090，仅本机) + `cache_file`(/etc/sing-box/cache.db，持久化 urltest 选择)。
  官方 1.12.25 二进制自带 clash_api。
- 新增 `urltest` 故障切换组 **`auto` = [hk, tw, us]**（`url`=generate_204，interval 3m，tolerance 50）；
  自动选最快、成员故障自动切换。**故意不含 jp(direct)**——JP 本机直连延迟最低(14ms)会永远胜出，失去多出口意义。
  默认 `route.final` 仍 = hk；想要“最快+故障切换”把默认出口设成 auto 即可。

### bot v3 新功能（`deploy/bot/pdg-bot.py`）
- **端到端测出口**：`test_exits` 改用 clash_api `/proxies/{tag}/delay`（经各出口实测到 generate_204 的真实延迟），
  clash_api 不可用时回落旧的 JP→落地 TCP 握手。
- **流量统计**：`/connections` 汇总 累计上下行 + 活跃连接数 + 按出口(chains[0])分组。
- **故障切换组管理**：`add_group(名 成员…)` 建 urltest 组；`exit_tags` 纳入组(可作默认出口/规则目标)；
  删除出口时从各组成员清理、空组自动删、悬挂引用回落 final。
- **iOS 描述文件下发**：由 `/opt/pdg-bot/pdg-dot.mobileconfig.tmpl` 填 DoT host/IP/UUID → `sendDocument` 发到 DM。
  ⚠️ 模板 OnDemand 蜂窝规则探测 `http://<IP>:81/probe`，需另配一个**只对内网卡(172.22)放行的 :81→204** 端点才会在蜂窝下激活。
- **配置备份/恢复**：备份 = 打包 sing-box+mosdns+规则集 → `sendDocument`（含出口密码，注意保管）；
  恢复 = 收 `.tar.gz` → `sing-box check` 通过才应用 → 重启，失败回滚。main 循环新增 `document` 分支(仅 restore 态接收)。
- 修 `refresh_rulesets`：回填早期缺 `format/path` 的旧条目(否则刷新 KeyError)，顺带补齐 `count`。

### 定时刷新规则库
- `pdg-rules-update.timer`（每日 04:30 + 随机 30min）→ `scheduled-update.sh`：先 `update-rules.sh`(geosite)，
  再 `python3 -c "import bot; bot.refresh_rulesets()"`（模块可无 token import）。

### mosdns vs smartdns（结论）
- **必须 mosdns**：本项目把 DNS 当策略引擎（代理域名 A 改写成服务器 IP、按来源 IP 分支、按域名置空 AAAA/HTTPS、
  ECS 分治），smartdns 模型是“解析最快真实 IP”，做不到兜底改写与来源分支。smartdns 只适合藏在 mosdns 后面当国内加速上游。
