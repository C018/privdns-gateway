#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 更新快照 + 精确回滚回归(Item 10)。
#   A. cmd_rollback --dir 精确指定快照: 即使有更新的快照(index 0)也回滚到**指定**那份;
#      不带 --dir 时仍按 index 0(最近)。
#   B. cmd_rollback --git <ref>: 回滚后把 REPO_DIR 复位到该提交(还原仓库版本)。
#   C. 部分恢复失败(git ref 不存在)→ 不谎报"完全回滚", 打印"未完全回滚"并返回 1。
#   D. 静态: cmd_update 快照失败即中止; 用 --dir "$snap_dir" --git "$pre_sha"(非 cmd_rollback 0);
#      快照 cand 覆盖已装脚本 + 全部 unit; 越界守卫放行 usr/local/bin。
# 沙箱化: 覆写 _pdg_apply_snapshot_tree 落到沙箱(不碰真 /), 打桩 systemctl/nft/内核 check。
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
pass=0; nfail=0
ok(){ echo "[OK]   $1"; pass=$((pass+1)); }
bad(){ echo "[FAIL] $1"; nfail=$((nfail+1)); }

# ── 造两份快照(旧 A=OLD / 新 B=NEW), 各含 backend + 判别标记 ──────────────────
SNAP="$WORK/snaps"; mkdir -p "$SNAP"
mksnap(){ # $1=目录名 $2=标记
  local d="$SNAP/$1"; mkdir -p "$d/tree/etc/privdns-gateway"
  printf 'singbox\n' > "$d/tree/etc/privdns-gateway/backend"
  printf '%s\n' "$2" > "$d/tree/etc/privdns-gateway/snapid"
  tar czf "$d/snap.tar.gz" -C "$d/tree" etc 2>/dev/null; rm -rf "$d/tree"
}
mksnap A OLD; sleep 1; mksnap B NEW    # B 更新(mtime 更晚 → ls -t 里 index 0)

# ── 沙箱 REPO_DIR: 两提交的 git 仓库 ─────────────────────────────────────────
REPO="$WORK/repo"; mkdir -p "$REPO"
( cd "$REPO" && git init -q && git config user.email t@t && git config user.name t \
  && echo v1 > f && git add f && git commit -qm c1 && echo v2 > f && git add f && git commit -qm c2 )
GOOD_REF=$(git -C "$REPO" rev-parse HEAD~1)   # 第一提交
HEAD_REF=$(git -C "$REPO" rev-parse HEAD)

# ── 抽取 cmd_rollback + 打桩 ──────────────────────────────────────────────────
sed -n '/^cmd_rollback(){/,/^}/p' "$ROOT/deploy/bot/pdg.sh" > "$WORK/rollback.sh"
# 快照里不含 etc/sing-box/config.json 与 etc/nftables.conf → 内核/nft 校验分支被跳过,
# 无需真 sing-box/mihomo/nft 二进制(也就不必打桩带连字符的函数名)。
cat > "$WORK/harness.sh" <<EOF
SNAP_DIR="$SNAP"
REPO_DIR="$REPO"
need_root(){ :; }; _lock(){ :; }
c_g(){ echo "\$*"; }; c_y(){ echo "\$*"; }
_pdg_core(){ echo singbox; }
_pdg_core_svc(){ echo sing-box; }
_pdg_mktemp_dir(){ mktemp -d; }
_sb_panel_managed_on(){ return 1; }
_core_kernel_activate(){ return 0; }
systemctl(){ return 0; }
nft(){ return 0; }
# 覆写落盘: 不碰真 /, 把被应用快照的判别标记抄到沙箱, 供断言"回滚到了哪份"
APPLIED="$WORK/applied_snapid"
_pdg_apply_snapshot_tree(){ cat "\$1/etc/privdns-gateway/snapid" > "\$APPLIED" 2>/dev/null; return 0; }
EOF

run(){ bash -c "source '$WORK/harness.sh'; source '$WORK/rollback.sh'; cmd_rollback $1" 2>&1; }

# ── A. --dir 精确回滚(指到旧的 A, 而非 index0 的 B) ─────────────────────────
rm -f "$WORK/applied_snapid"; out=$(run "--dir '$SNAP/A'")
[[ "$(cat "$WORK/applied_snapid" 2>/dev/null)" == OLD ]] \
  && ok "--dir 指定旧快照 A → 精确回滚到 A(未被 index0 的 B 顶掉)" || bad "A: applied=$(cat "$WORK/applied_snapid" 2>/dev/null) out=$out"

# 不带 --dir → index 0(最近 = B)
rm -f "$WORK/applied_snapid"; out=$(run "0")
[[ "$(cat "$WORK/applied_snapid" 2>/dev/null)" == NEW ]] \
  && ok "无 --dir → 默认 index0 仍回滚到最近 B" || bad "A2: applied=$(cat "$WORK/applied_snapid" 2>/dev/null) out=$out"

# ── B. --git 复位仓库 ────────────────────────────────────────────────────────
git -C "$REPO" reset --hard -q "$HEAD_REF"
out=$(run "--dir '$SNAP/A' --git '$GOOD_REF'")
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$GOOD_REF" ]] \
  && echo "$out" | grep -q '已回滚并重启服务' && ok "--git: REPO_DIR 复位到指定提交 + 报完全回滚" || bad "B: HEAD=$(git -C "$REPO" rev-parse HEAD) out=$out"

# ── C. git ref 不存在 → 不谎报完全回滚, 返回 1 ───────────────────────────────
rc=0; out=$(run "--dir '$SNAP/A' --git 'deadbeefdeadbeef'") || rc=$?
{ echo "$out" | grep -q '未完全回滚' && [[ "$rc" == 1 ]]; } \
  && ok "git ref 失效 → 打印'未完全回滚'并返回 1(不谎报成功)" || bad "C: rc=$rc out=$out"
# 但快照本身仍已恢复(apply 成功)
[[ "$(cat "$WORK/applied_snapid" 2>/dev/null)" == OLD ]] && ok "  部分失败下配置快照仍已落盘(只是 git 未复位)" || bad "C2"

# ── D. 静态断言: cmd_update / cmd_snapshot / 越界守卫 ─────────────────────────
u="$ROOT/deploy/bot/pdg.sh"
grep -q '更新前快照失败, 中止更新' "$u" && ok "cmd_update: 快照失败即中止(不在无法回滚下继续)" || bad "D1: 缺快照失败中止"
grep -q 'cmd_rollback --dir "\$snap_dir" --git "\$pre_sha"' "$u" && ok "cmd_update: 回滚用精确 --dir+--git(非 cmd_rollback 0)" || bad "D2"
grep -q "pre_sha=.*git -C .*rev-parse HEAD" "$u" && ok "cmd_update: 记录升级前 Git SHA" || bad "D3"
for p in 'usr/local/bin/pdg' 'usr/local/bin/pdg-set-token' 'etc/systemd/system/mihomo.service' 'etc/systemd/system/pdg-mitm.service' '99-pdg-cert.sh'; do
  grep -q "$p" "$u" || bad "D4: 快照 cand 缺 $p"
done
grep -q "etc/systemd/system/mihomo.service etc/systemd/system/sing-box.service" "$u" && ok "cmd_snapshot cand: 覆盖已装脚本 + 内核/mitm/probe/health 全部 unit + cert hook" || bad "D4 汇总"
grep -q 'usr/local/bin)(/|$)' "$u" && ok "回滚越界守卫放行 usr/local/bin(否则装的脚本进不了快照)" || bad "D5: 守卫未放行 usr/local/bin"

echo "────────────────────────────────────────"
echo "通过 $pass, 失败 $nfail"
[[ "$nfail" == 0 ]]
