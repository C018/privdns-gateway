#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 真功能测试(非静态): 真起 sing-box, 验证本项目的核心链路 ——
#   「单入口 + 按 TLS SNI 把流量分到不同出口」。
#
# 做法(全本地、可在 CI / 干净机跑, 仅需 python3 + 官方 sing-box):
#   1) 起 3 个本地 mock SOCKS5 当"出口", 各自记录收到的目标域名;
#   2) 用 direct 入口(开 sniff, 与生产同款)起 sing-box, 按域名规则分到出口 A/B、其余走 final;
#   3) 按不同 SNI 发 TLS ClientHello 到入口, 断言每个 SNI 被嗅探并路由到正确出口。
#
# 退出码 0 = 通过; 非 0 = 失败(并打印 sing-box 输出便于排查)。
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=lib/versions.sh
source "$ROOT/lib/versions.sh"

WORK="$(mktemp -d)"
PIDS=()
cleanup(){ for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done; rm -rf "$WORK"; }
trap cleanup EXIT
fail(){ echo "[FAIL] $*" >&2; exit 1; }
note(){ echo "[*] $*"; }

case "$(uname -m)" in
  x86_64) ARCH=amd64 ;; aarch64|arm64) ARCH=arm64 ;;
  *) fail "不支持的架构: $(uname -m)" ;;
esac

# ── 1. 取 sing-box(优先用 PATH 里的 1.12; 否则按钉死 SHA256 下载)──
if command -v sing-box >/dev/null && sing-box version 2>/dev/null | grep -q "version 1.12"; then
  SB="$(command -v sing-box)"; note "用现有 sing-box: $SB ($(sing-box version 2>/dev/null | head -1))"
else
  note "下载 sing-box $SINGBOX_VER ($ARCH)…"
  curl -fsSL "https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VER}/sing-box-${SINGBOX_VER}-linux-${ARCH}.tar.gz" \
       -o "$WORK/sb.tgz" || fail "sing-box 下载失败"
  pdg_verify_sha256 "$WORK/sb.tgz" "${PDG_SHA256[singbox-$ARCH]:-}" "sing-box $SINGBOX_VER ($ARCH)" \
    || fail "sing-box SHA256 校验失败"
  tar -xzf "$WORK/sb.tgz" -C "$WORK"
  SB="$(echo "$WORK"/sing-box-*/sing-box)"
fi

# ── 2. 起 3 个 mock SOCKS5 出口 ──
LOGA="$WORK/a.log"; LOGB="$WORK/b.log"; LOGD="$WORK/d.log"
: > "$LOGA"; : > "$LOGB"; : > "$LOGD"
python3 "$HERE/mock_socks.py" 11080 "$LOGA" & PIDS+=($!)
python3 "$HERE/mock_socks.py" 11081 "$LOGB" & PIDS+=($!)
python3 "$HERE/mock_socks.py" 11082 "$LOGD" & PIDS+=($!)

# ── 3. 写 sing-box 测试配置: direct 入口开 sniff, 按域名分流, 其余走 final ──
cat > "$WORK/sb.json" <<'JSON'
{
  "log": { "level": "error" },
  "inbounds": [
    { "type": "direct", "tag": "in", "listen": "127.0.0.1", "listen_port": 18443,
      "sniff": true, "sniff_override_destination": true, "sniff_timeout": "300ms" },
    { "type": "direct", "tag": "in-gms", "network": "tcp", "listen": "127.0.0.1", "listen_port": 15228,
      "sniff": true, "sniff_override_destination": true, "sniff_timeout": "300ms" }
  ],
  "outbounds": [
    { "type": "socks", "tag": "exitA",       "server": "127.0.0.1", "server_port": 11080, "version": "5" },
    { "type": "socks", "tag": "exitB",       "server": "127.0.0.1", "server_port": 11081, "version": "5" },
    { "type": "socks", "tag": "exitDefault", "server": "127.0.0.1", "server_port": 11082, "version": "5" }
  ],
  "route": {
    "rules": [
      { "domain_suffix": ["alpha.test"], "outbound": "exitA" },
      { "domain_suffix": ["beta.test"],  "outbound": "exitB" },
      { "domain_suffix": ["mtalk.google.com"], "outbound": "exitB" }
    ],
    "final": "exitDefault"
  }
}
JSON

"$SB" check -c "$WORK/sb.json" || fail "sing-box check 未通过(配置无效)"
"$SB" run -c "$WORK/sb.json" > "$WORK/sb.out" 2>&1 & PIDS+=($!)

# 等入口端口就绪
ready=0
for _ in $(seq 1 50); do
  if python3 -c 'import socket,sys; s=socket.socket(); s.settimeout(.2); sys.exit(0 if s.connect_ex(("127.0.0.1",18443))==0 else 1)'; then ready=1; break; fi
  sleep 0.1
done
[[ "$ready" == 1 ]] || { cat "$WORK/sb.out" >&2; fail "sing-box 入口 :18443 未就绪"; }

# ── 4. 三个 SNI, 断言落到正确出口(只比对 host, 端口随入口口子) ──
check_case(){  # $1=SNI $2=期望日志文件 $3=出口名 [$4=入口端口, 默认 18443]
  local sni="$1" log="$2" name="$3" port="${4:-18443}"
  python3 "$HERE/sni_client.py" 127.0.0.1 "$port" "$sni"
  for _ in $(seq 1 30); do grep -q "^${sni}:" "$log" 2>/dev/null && { note "  $sni → $name ✓"; return 0; }; sleep 0.1; done
  echo "---- sing-box 输出 ----" >&2; cat "$WORK/sb.out" >&2
  fail "SNI=$sni 未按预期到达 $name (A='$(tr '\n' ' ' <"$LOGA")' B='$(tr '\n' ' ' <"$LOGB")' D='$(tr '\n' ' ' <"$LOGD")')"
}

note "用例: 按 SNI 分流"
check_case alpha.test "$LOGA" "exitA(域名规则)"
check_case beta.test  "$LOGB" "exitB(域名规则)"
check_case gamma.test "$LOGD" "exitDefault(final 兜底)"

note "用例: GMS 推送端口入站(模拟 :5228, mtalk 经嗅探按域名分流)"
check_case mtalk.google.com "$LOGB" "exitB(GMS 入站+域名规则)" 15228

# 反向断言: 命中规则的 SNI 不应串到别的出口
grep -q alpha.test "$LOGB" "$LOGD" 2>/dev/null && fail "alpha.test 串到了错误出口"
grep -q beta.test  "$LOGA" "$LOGD" 2>/dev/null && fail "beta.test 串到了错误出口"
grep -q mtalk.google.com "$LOGA" "$LOGD" 2>/dev/null && fail "mtalk.google.com 串到了错误出口"

echo
echo "✅ 功能测试通过: TLS SNI 嗅探 + 按域名多出口分流 + final 兜底 + GMS 端口入站 均正确。"
