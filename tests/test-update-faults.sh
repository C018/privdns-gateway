#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Issue 2 回归: cmd_update 关键步骤失败必须**立即回滚 + 返回非0 + 不打印"✅ 已更新"**。
# 覆盖故障注入: git reset 失败 / 必需文件安装失败 / __migrate 非0 / 内核更新失败 /
#              daemon-reload 失败; 以及正常路径仍走到"✅ 已更新"。
# 沙箱化: 抽出 cmd_update, 打桩全部外部副作用(git/install/systemctl/内核/快照/回滚),
#         用环境开关注入单点故障, 断言"是否调用了 cmd_rollback"与"是否谎报成功"。
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
pass=0; nfail=0
ok(){ echo "[OK]   $1"; pass=$((pass+1)); }
bad(){ echo "[FAIL] $1"; nfail=$((nfail+1)); }

sed -n '/^cmd_update(){/,/^}/p' "$ROOT/deploy/bot/pdg.sh" > "$WORK/upd.sh"

mkdir -p "$WORK/repo/.git"          # 让 [[ -d $REPO_DIR/.git ]] 为真, 跳过 clone

cat > "$WORK/harness.sh" <<'EOF'
REPO_DIR="$WORK/repo"; REPO_URL="file:///dev/null"; ENVF="$WORK/none.env"
need_root(){ :; }; _lock(){ :; }
c_g(){ echo "$*"; }; c_y(){ echo "$*"; }
sleep(){ :; }
_pdg_platform(){ echo android; }
_pdg_core(){ echo singbox; }
pdg_fetch_release_tags(){ return 0; }
# 全桩 git: 只控制 reset 成败, 其余给出稳定输出
git(){
  local a=("$@"); [[ "${a[0]:-}" == "-C" ]] && a=("${a[@]:2}")
  case "${a[0]:-}" in
    reset)     [[ -n "${FAIL_RESET:-}" ]] && return 1; return 0;;
    rev-parse) echo "0000000000000000000000000000000000000000";;
    tag)       echo "v9.9.9";;
    describe)  echo "v9.9.9";;
    log)       :;;
    *)         return 0;;
  esac
}
# 必需文件安装: FAIL_INSTALL 命中目标子串则失败
install(){ local last="${*: -1}"; [[ -n "${FAIL_INSTALL:-}" && "$*" == *"${FAIL_INSTALL}"* ]] && return 1; return 0; }
# __migrate 经 `bash /usr/local/bin/pdg __migrate` 调用 → 拦 bash 函数
bash(){ [[ "$*" == *__migrate* ]] && return "${MIGRATE_RC:-0}"; command bash "$@"; }
_update_core_binary(){ [[ -n "${FAIL_CORE:-}" ]] && return 1; return 0; }
systemctl(){ [[ "${1:-}" == daemon-reload && -n "${FAIL_RELOAD:-}" ]] && return 1; return 0; }
python3(){ case "$*" in *py_compile*) return 0;; *doctor*) echo ""; return 0;; *) command python3 "$@";; esac; }
sing-box(){ return 0; }
mihomo(){ return 0; }
nft(){ return 0; }
# 快照: 造真文件让门通过; 回滚: 只记录被调用(并返回0, 便于观察上层是否谎报成功)
cmd_snapshot(){ _PDG_SNAP_CREATED="$WORK/snap"; mkdir -p "$_PDG_SNAP_CREATED"; : | gzip > "$_PDG_SNAP_CREATED/snap.tar.gz"; return 0; }
cmd_rollback(){ echo "ROLLBACK_CALLED $*"; return 0; }
EOF

export WORK
run(){ # $1=额外环境赋值串(NAME=VALUE, 无空格); 运行 cmd_update, 打印 "<rc>|<输出>"
  local rc=0 out
  # shellcheck disable=SC2086  # $1 需按词拆成 env 的 NAME=VALUE 参数
  out=$(env $1 bash -c "source '$WORK/harness.sh'; source '$WORK/upd.sh'; cmd_update" 2>&1) || rc=$?
  printf '%s\n' "$rc|$out"
}

assert_success(){ # 正常路径: rc0 + 有"✅ 已更新" + 无 ROLLBACK
  local r; r=$(run "$1"); local rc="${r%%|*}" out="${r#*|}"
  { [[ "$rc" == 0 ]] && grep -q '✅ 已更新' <<<"$out" && ! grep -q ROLLBACK_CALLED <<<"$out"; } \
    && ok "正常路径: 走到 ✅ 已更新, 未回滚" || bad "happy: rc=$rc out=$out"
}
assert_fail_rollback(){ # 故障路径: rc非0 + 有 ROLLBACK + 无"✅ 已更新"
  local desc="$1" env="$2" r; r=$(run "$env"); local rc="${r%%|*}" out="${r#*|}"
  { [[ "$rc" != 0 ]] && grep -q ROLLBACK_CALLED <<<"$out" && ! grep -q '✅ 已更新' <<<"$out"; } \
    && ok "$desc → 回滚 + 非0 + 不谎报成功" || bad "$desc: rc=$rc out=$out"
}

assert_success ""
assert_fail_rollback "git reset 失败"        "FAIL_RESET=1"
assert_fail_rollback "必需文件(bot.py)安装失败" "FAIL_INSTALL=/opt/pdg-bot/bot.py"
assert_fail_rollback "必需文件(report.py)安装失败" "FAIL_INSTALL=report.py"
assert_fail_rollback "必需文件(pdg 主脚本)安装失败" "FAIL_INSTALL=/usr/local/bin/pdg"
assert_fail_rollback "__migrate 迁移非0"       "MIGRATE_RC=1"
assert_fail_rollback "内核二进制更新失败"       "FAIL_CORE=1"
assert_fail_rollback "daemon-reload 失败"      "FAIL_RELOAD=1"

echo "────────────────────────────────────────"
echo "通过 $pass, 失败 $nfail"
[[ "$nfail" == 0 ]]
