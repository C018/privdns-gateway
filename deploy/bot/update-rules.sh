#!/usr/bin/env bash
# 更新 geosite 规则库: 重新下载 geosite.dat → 解析 → 重载 mosdns。
# 依赖本机能解析 DNS (resolv.conf 指向 127.0.0.1=mosdns)。
set -euo pipefail
cd /tmp
curl -fsSL -o /tmp/geosite.dat \
  https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat
python3 /opt/pdg-bot/parse-geosite.py /tmp/geosite.dat /etc/mosdns/rules
rm -f /tmp/geosite.dat
systemctl restart mosdns
echo "geosite 规则库已更新并重载 mosdns"
