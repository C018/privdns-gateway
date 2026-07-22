#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Issue 3 回归: 内核热切必须"确认新核稳定运行后才删旧核备份(.prev)"。
#   A. 配置 check 失败      → 还原旧核(内容/sha 一致)、无 .prev 残留、return 1、不报"已装并重启"
#   B. check 过但重启不稳定 → 同上(旧实现此时 .prev 已删 → 无核可退, 正是本 issue)
#   C. 全过                 → 新核就位、.prev 已删、return 0、报"已装并重启"
#   mihomo 与 sing-box 两内核对称覆盖。
#   D. 快照含内核二进制 + 回滚能按内容还原(不依赖联网重下)。
# 沙箱化: PDG_CORE_BINDIR 指到临时目录; systemctl is-active 依"当前装的是新核还是旧核"作答。
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
pass=0; nfail=0
ok(){ echo "[OK]   $1"; pass=$((pass+1)); }
bad(){ echo "[FAIL] $1"; nfail=$((nfail+1)); }

xt(){ sed -n "/^$1(){/,/^}/p" "$ROOT/deploy/bot/pdg.sh"; }
eval "$(xt _core_bindir)"; eval "$(xt _core_config_check)"; eval "$(xt _core_kernel_stable)"
eval "$(xt _core_restore_prev)"; eval "$(xt _core_swap_verify)"; eval "$(xt _pdg_apply_snapshot_tree)"

c_g(){ echo "$*"; }; c_y(){ echo "$*"; }
sleep(){ :; }
BIN="$WORK/bin"; export PDG_CORE_BINDIR="$BIN"
# is-active: 装的是新核 → 用 NEW_ACTIVE 模拟其死活; 旧核一律 active(还原后应恢复)
systemctl(){
  if [[ "${1:-}" == is-active ]]; then
    if grep -q NEWKERNEL "$BIN/${2:-}" 2>/dev/null; then echo "${NEW_ACTIVE:-active}"; else echo active; fi
  fi
  return 0
}

setup(){ # $1=svc $2=新核 check 退出码
  rm -rf "$BIN"; mkdir -p "$BIN"
  printf '#!/bin/sh\n# OLDKERNEL\nexit 0\n' > "$BIN/$1"; chmod 755 "$BIN/$1"
  OLDSHA=$(sha256sum "$BIN/$1" | cut -d' ' -f1)
  printf '#!/bin/sh\n# NEWKERNEL\nexit %s\n' "$2" > "$WORK/new-$1"; chmod 755 "$WORK/new-$1"
  NEWSHA=$(sha256sum "$WORK/new-$1" | cut -d' ' -f1)
}
cursha(){ sha256sum "$BIN/$1" | cut -d' ' -f1; }

for svc in mihomo sing-box; do
  # ── A. 配置 check 失败 → 还原旧核 ──
  setup "$svc" 3; NEW_ACTIVE=active
  rc=0; out=$(_core_swap_verify "$svc" "$WORK/new-$svc" "$BIN" vTEST 2>&1) || rc=$?
  { [[ "$rc" != 0 ]] && [[ "$(cursha "$svc")" == "$OLDSHA" ]] && [[ ! -e "$BIN/$svc.prev" ]] \
    && ! grep -q '已装并重启' <<<"$out"; } \
    && ok "$svc: check 失败 → 旧核按 sha 还原 + 无 .prev 残留 + 非0 + 不报已装" \
    || bad "$svc A: rc=$rc sha=$(cursha "$svc") prev=$([[ -e "$BIN/$svc.prev" ]] && echo 有 || echo 无) out=$out"

  # ── B. check 过但新核重启后不 active → 仍能退回旧核(旧实现此处 .prev 已删) ──
  setup "$svc" 0; NEW_ACTIVE=failed
  rc=0; out=$(_core_swap_verify "$svc" "$WORK/new-$svc" "$BIN" vTEST 2>&1) || rc=$?
  { [[ "$rc" != 0 ]] && [[ "$(cursha "$svc")" == "$OLDSHA" ]] && [[ ! -e "$BIN/$svc.prev" ]] \
    && ! grep -q '已装并重启' <<<"$out"; } \
    && ok "$svc: 重启后不稳定 → 旧核按 sha 还原 + 非0 + 不报已装(核心回归)" \
    || bad "$svc B: rc=$rc sha=$(cursha "$svc") prev=$([[ -e "$BIN/$svc.prev" ]] && echo 有 || echo 无) out=$out"

  # ── C. 全过 → 新核就位, .prev 删掉, 报已装并重启 ──
  setup "$svc" 0; NEW_ACTIVE=active
  rc=0; out=$(_core_swap_verify "$svc" "$WORK/new-$svc" "$BIN" vTEST 2>&1) || rc=$?
  { [[ "$rc" == 0 ]] && [[ "$(cursha "$svc")" == "$NEWSHA" ]] && [[ ! -e "$BIN/$svc.prev" ]] \
    && grep -q '已装并重启' <<<"$out"; } \
    && ok "$svc: 全过 → 新核按 sha 就位 + .prev 已删 + 报已装并重启" \
    || bad "$svc C: rc=$rc sha=$(cursha "$svc") out=$out"
done

# ── D. 快照含内核二进制, 且回滚能按内容还原(网络无关) ──
grep -q 'usr/local/bin/mihomo usr/local/bin/sing-box' "$ROOT/deploy/bot/pdg.sh" \
  && ok "cmd_snapshot cand 已含两内核二进制(回滚不依赖联网重下)" || bad "D1: 快照 cand 缺内核二进制"

TREE="$WORK/tree"; DEST="$WORK/dest"; mkdir -p "$TREE/usr/local/bin" "$DEST"
printf '#!/bin/sh\n# SNAPSHOT-OLDKERNEL\nexit 0\n' > "$TREE/usr/local/bin/mihomo"
SNAPSHA=$(sha256sum "$TREE/usr/local/bin/mihomo" | cut -d' ' -f1)
printf 'usr/local/bin/mihomo\n' > "$WORK/members"
mkdir -p "$DEST/usr/local/bin"; printf 'BROKEN-NEW\n' > "$DEST/usr/local/bin/mihomo"
if _pdg_apply_snapshot_tree "$TREE" "$WORK/members" "$DEST" \
   && [[ "$(sha256sum "$DEST/usr/local/bin/mihomo" | cut -d' ' -f1)" == "$SNAPSHA" ]]; then
  ok "回滚落盘: 快照里的内核二进制按 sha 覆盖回坏内核"
else bad "D2: 回滚未还原内核二进制"; fi

echo "────────────────────────────────────────"
echo "通过 $pass, 失败 $nfail"
[[ "$nfail" == 0 ]]
