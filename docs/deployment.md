# 部署 (JP, Debian 12)

## 组件
- **dnsdist** — 对外 DoT/DoH，DNS 层 spoof/block（规则由 pdg 生成）。
- **sing-box** — 透明 tproxy 入口 + sniff + 分流（完整配置由 pdg 生成）。
- **nftables + 策略路由** — 把发往 JP 内网 IP 的 80/443 交给 sing-box。
- **pdg** — 控制面：编译规则、校验、reload、rollback、doctor。

## 安装步骤

```bash
sudo deploy/install.sh
```

会创建目录骨架（`/opt/pdg` `/etc/pdg` `/var/lib/pdg` `/var/log/pdg`）、把 pdg 装进
`/opt/pdg/venv` 并软链到 `/usr/local/bin/pdg`、装配置样例与 systemd 单元。

然后：

1. 编辑 `/etc/pdg/pdg.conf`：
   - `gateway.jp_internal_ip` = 手机能访问到的那个**唯一内网地址**
   - `[outbounds.hk-ss2022]` / `[outbounds.tw-ss2022]` 的 `server/server_port/method/password`
     必须与 HK/TW 服务端**完全一致**
2. 放置 DoT/DoH 证书：`/etc/pdg/tls/fullchain.pem`、`/etc/pdg/tls/privkey.pem`
3. 安装 `dnsdist` 与 `sing-box` 二进制
4. 生成并启动：

```bash
sudo pdg compile          # 生成 /etc/dnsdist/pdg-generated.lua + /etc/sing-box/config.json + /var/lib/pdg/out/pdg.nft
sudo pdg reload           # 校验 + reload (校验失败自动回滚)
sudo systemctl enable --now dnsdist sing-box pdg-tproxy
sudo pdg doctor
```

## 透明入口 (tproxy) 原理

`pdg-tproxy.service` 做两件事（幂等，可重复执行）：

```bash
# 1) 策略路由: 打了 fwmark 0x1 的包查表 100, 路由到本地 (lo), 由 sing-box tproxy 接管
ip rule add fwmark 0x1 lookup 100
ip route replace local 0.0.0.0/0 dev lo table 100
ip -6 rule add fwmark 0x1 lookup 100
ip -6 route replace local ::/0 dev lo table 100
# 2) nftables: 发往 JP 内网 IP 的 TCP 80/443 → TPROXY 到 sing-box, 并打 mark
nft -f /var/lib/pdg/out/pdg.nft
```

sing-box `tproxy` 入站开启 `sniff` + `sniff_override_destination`：
前者从 TLS ClientHello / HTTP 请求里取出真实域名，后者让出站按【域名】而非
目的 IP（JP 内网 IP 没意义）去连。**这一步必须验证**：普通 HTTPS 不能被当成代理协议。

## sing-box 版本说明
生成的配置使用 **inbound 级 `sniff` / `sniff_override_destination`**，适用 sing-box 1.8–1.10。
若用 1.11+，需迁移到 route 规则的 `action: sniff`（语义相同，写法不同）——
届时调整 `src/pdg/generators/singbox.py` 即可（单点改动，因为配置统一由它生成）。

## HK / TW SS2022 安全建议
SS2022 端口只放行 JP 公网 IP：

```
允许  JP 公网 IP   → SS2022 端口
拒绝  其他来源     → SS2022 端口
```

注意：`method` / `password` / 端口必须与 JP outbound 一致；若 SS2022 启用 UDP，
JP outbound 也要支持 UDP（V1 先跑稳 TCP，UDP 留到 V5）。

## 防火墙 (JP 对外)
- 放行 853 (DoT)、8443 (DoH) 给客户端网段。
- 放行 80/443 给客户端网段（流量入口）。
- DoT/DoH 的 dnsdist ACL 建议收窄到客户端网段，避免长期对全网开放成开放解析器。

## 墙内内网卡 / WiFi / MSS clamp

- **DNS 公网暴露**：dnsdist 的 DoT(853)/DoH(8443) 监听在 JP **公网** IP，
  这样内网卡和普通 WiFi 都能查 DNS（WiFi 才不会 fail-closed 没网）。
- **流量入口私网**：`pdg.conf` 的 `jp_internal_ip` 是 JP **私网** 地址，仅内网卡/隧道可达。
- **按来源分流**：`pdg.conf` 的 `internal_src_cidr` 填【内网卡运营商内网段 + 隧道段】。
  只有来自这些来源的代理域名才 spoof，普通 WiFi 自动降级直连。地址固定，**不受家宽动态公网 IP 影响**。
- **家里 WiFi 也要代理**：在家用路由器上把 JP 的 DNS 公网 IP 与私网入口 IP **路由进隧道**，
  使家里查询从隧道私网源到达 JP（落进 `internal_src_cidr`）。

### MSS clamp（修复 Play / 大下载卡死）
隧道压低了 MTU，不做 MSS clamp 会出现「小请求通、app 更新/大文件卡死」。
在**隧道接口**（如 WireGuard `wg0`）上 clamp：

```bash
nft add table inet mangle 2>/dev/null || true
nft 'add chain inet mangle forward { type filter hook forward priority mangle; }' 2>/dev/null || true
nft 'add rule inet mangle forward oifname "wg0" tcp flags syn tcp option maxseg size set rt mtu'
nft 'add rule inet mangle forward iifname "wg0" tcp flags syn tcp option maxseg size set rt mtu'
```

QUIC 由 DNS 层处理：代理域名屏蔽 HTTPS/SVCB 记录、JP 侧不接管 UDP 443 → 客户端回落 TCP。

## 运维
- 改规则：编辑 `/etc/pdg/rules.conf`（或 `pdg rule add/del/move`）→ `pdg reload`。
- 更新远程规则集：`pdg update-rules`（失败自动回退旧缓存）。
- 出问题：`pdg rollback` 回滚到上次产物；`pdg doctor` 体检。
- 备份：每次写入前旧产物存到 `/var/lib/pdg/backup/*.prev`。
