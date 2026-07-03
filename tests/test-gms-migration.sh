#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# GMS 推送端口迁移回归: 校验 pdg.sh 的 migrate_fw_gms / migrate_singbox_gms ——
# 「原装应补 5228-5230、自定义/非本项目形态应跳过、幂等、失败回滚」。
# 纯 bash + python3, nft/sing-box/systemctl 用函数打桩, 可在 CI 跑。
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

# 抽出被测函数; 外部命令与输出函数打桩
eval "$(sed -n '/^migrate_fw_gms(){/,/^}/p' "$ROOT/deploy/bot/pdg.sh")"
eval "$(sed -n '/^migrate_singbox_gms(){/,/^}/p' "$ROOT/deploy/bot/pdg.sh")"
c_g(){ :; }; c_y(){ :; }
NFT_RC=0; SB_RC=0; SVC_STATE=active
nft(){ return "$NFT_RC"; }
sing-box(){ return "$SB_RC"; }
systemctl(){ [[ "$1" == is-active ]] && echo "$SVC_STATE"; return 0; }

pass=0; nfail=0
ok(){ echo "[OK]   $1"; pass=$((pass+1)); }
bad(){ echo "[FAIL] $1"; nfail=$((nfail+1)); }

# ── 防火墙: 原装(inet pdg, 现行端口集) → 应补 5228-5230, 且幂等 ──
cat > "$WORK/fw" <<'EOF'
table inet pdg
delete table inet pdg
table inet pdg {
    chain input {
        type filter hook input priority 0; policy drop;
        iif "lo" accept
        ct state established,related accept
        tcp dport { 22 } accept
        ip saddr 172.22.0.0/16 tcp dport { 53, 80, 81, 443, 853, 8445 } accept
        ip saddr 172.22.0.0/16 udp dport { 53 } accept
        ip saddr 172.22.0.0/16 udp dport 443 reject
        ip protocol icmp accept
        ip6 nexthdr icmpv6 accept
    }
}
EOF
migrate_fw_gms "$WORK/fw"
if grep -q 'tcp dport { 53, 80, 81, 443, 853, 5228-5230, 8445 } accept' "$WORK/fw"; then
  ok "fw 原装 → 补 5228-5230"
else bad "fw 原装未补上 5228-5230"; fi
snap="$(cat "$WORK/fw")"
migrate_fw_gms "$WORK/fw"
[[ "$(cat "$WORK/fw")" == "$snap" ]] && ok "fw 幂等(二跑不变)" || bad "fw 二跑改动了文件"
grep -q 'udp dport 443 reject' "$WORK/fw" && ok "fw 其余行未被波及" || bad "fw 其它行被改动"

# ── 防火墙: 自定义端口集 → 跳过不动 ──
sed 's/{ 53, 80, 81, 443, 853, 5228-5230, 8445 }/{ 53, 443, 9999 }/' "$WORK/fw" > "$WORK/fw2"
snap="$(cat "$WORK/fw2")"
migrate_fw_gms "$WORK/fw2"
[[ "$(cat "$WORK/fw2")" == "$snap" ]] && ok "fw 自定义端口集 → 跳过" || bad "fw 自定义端口集被改动!"

# ── 防火墙: 还没迁到 inet pdg(老 inet filter)→ 跳过 ──
sed 's/inet pdg/inet filter/g; s/, 5228-5230,/,/' "$WORK/fw" > "$WORK/fw3"
snap="$(cat "$WORK/fw3")"
migrate_fw_gms "$WORK/fw3"
[[ "$(cat "$WORK/fw3")" == "$snap" ]] && ok "fw 未迁 inet pdg → 跳过" || bad "fw inet filter 被改动!"

# ── 防火墙: nft -c 失败 → 还原 ──
sed 's/{ 53, 80, 81, 443, 853, 5228-5230, 8445 }/{ 53, 80, 81, 443, 853, 8445 }/' "$WORK/fw" > "$WORK/fw4"
snap="$(cat "$WORK/fw4")"
NFT_RC=1; migrate_fw_gms "$WORK/fw4"; NFT_RC=0
[[ "$(cat "$WORK/fw4")" == "$snap" ]] && ok "fw nft 校验失败 → 还原" || bad "fw 校验失败未还原!"

# ── sing-box: 本项目形态配置 → 补 3 个入站(在 :80 之后), 幂等 ──
cat > "$WORK/sb.json" <<'EOF'
{
  "inbounds": [
    { "type": "direct", "tag": "in-https", "listen": "0.0.0.0", "listen_port": 443,
      "sniff": true, "sniff_override_destination": true, "sniff_timeout": "300ms" },
    { "type": "direct", "tag": "in-http", "network": "tcp", "listen": "0.0.0.0", "listen_port": 80,
      "sniff": true, "sniff_override_destination": true, "sniff_timeout": "300ms" },
    { "type": "mixed", "tag": "tg-proxy", "listen": "0.0.0.0", "listen_port": 8445 }
  ],
  "outbounds": [ { "type": "direct", "tag": "jp" } ],
  "route": { "rules": [], "final": "jp" }
}
EOF
migrate_singbox_gms "$WORK/sb.json"
python3 - "$WORK/sb.json" <<'PY' && ok "sb 补入站(5228/5229/5230, 位于 :80 后)" || bad "sb 入站缺失或位置/字段不对"
import json, sys
c = json.load(open(sys.argv[1]))
ports = [i.get("listen_port") for i in c["inbounds"]]
assert ports == [443, 80, 5228, 5229, 5230, 8445], ports
for i in c["inbounds"]:
    if i.get("listen_port") in (5228, 5229, 5230):
        assert i["type"] == "direct" and i["sniff"] and i["sniff_override_destination"], i
        assert i["network"] == "tcp" and i["tag"] == "in-gms-%d" % i["listen_port"], i
PY
snap="$(cat "$WORK/sb.json")"
migrate_singbox_gms "$WORK/sb.json"
[[ "$(cat "$WORK/sb.json")" == "$snap" ]] && ok "sb 幂等(二跑不变)" || bad "sb 二跑改动了文件"

# ── sing-box: check 失败 → 还原不重启 ──
sed 's/"listen_port": 5228/"listen_port": 15228/; s/"listen_port": 5229/"listen_port": 15229/; s/"listen_port": 5230/"listen_port": 15230/; s/in-gms-5/in-gms-x5/g' "$WORK/sb.json" > "$WORK/sb2.json"
snap="$(cat "$WORK/sb2.json")"
SB_RC=1; migrate_singbox_gms "$WORK/sb2.json"; SB_RC=0
[[ "$(cat "$WORK/sb2.json")" == "$snap" ]] && ok "sb check 失败 → 还原" || bad "sb check 失败未还原!"

# ── sing-box: 重启后不 active → 还原 ──
cp "$WORK/sb2.json" "$WORK/sb3.json"
snap="$(cat "$WORK/sb3.json")"
SVC_STATE=failed; migrate_singbox_gms "$WORK/sb3.json"; SVC_STATE=active
[[ "$(cat "$WORK/sb3.json")" == "$snap" ]] && ok "sb 重启失败 → 还原" || bad "sb 重启失败未还原!"

# ── sing-box: 非本项目形态(无 sniff_override_destination)→ 跳过 ──
echo '{ "inbounds": [ { "type": "mixed", "listen_port": 1080 } ] }' > "$WORK/sb4.json"
snap="$(cat "$WORK/sb4.json")"
migrate_singbox_gms "$WORK/sb4.json"
[[ "$(cat "$WORK/sb4.json")" == "$snap" ]] && ok "sb 非本项目配置 → 跳过" || bad "sb 非本项目配置被改动!"

echo "────────────────────────────────────────"
echo "通过 $pass, 失败 $nfail"
[[ "$nfail" == 0 ]]
