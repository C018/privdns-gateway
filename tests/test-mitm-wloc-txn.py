#!/usr/bin/env python3
"""WLOC/MITM 应用**事务化**回归(Item 4)。

_mitm_transact 单锁内: 备份旧 mitm.json+hijack → 写新 → CA → 写 hijack → 渲染内核+校验+稳定active
→ pdg-mitm 稳定active → mosdns 重启+稳定active。任一步失败必须全量回滚, 且:
  · 绝不『返回失败但新态(enabled=true)已持久化』;
  · 绝不『服务失败却返回成功』。
故障注入: 锁占用(BUSY)/ CA 失败 / 内核校验失败 / pdg-mitm 起不来 / mosdns 起不来。
"""
import contextlib
import importlib.util as u
import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy" / "bot"))
spec = u.spec_from_file_location("pdg_bot", ROOT / "deploy/bot/pdg-bot.py")
bot = u.module_from_spec(spec); spec.loader.exec_module(bot)
import mitm_ca  # noqa: E402

pass_n = 0
def ok(m):
    global pass_n; print("[OK]  ", m); pass_n += 1


class Ctx:
    guard_ok = True
    apply_ret = (True, "")
    svc = {}            # unit -> bool(_svc_active)
    ca_raises = False
    sh_fail = set()     # units whose `systemctl restart` returns rc!=0


@contextlib.contextmanager
def _guard():
    yield Ctx.guard_ok


def _sh(cmd):
    rc = 1 if (len(cmd) >= 3 and cmd[1] == "restart" and cmd[2] in Ctx.sh_fail) else 0
    return types.SimpleNamespace(returncode=rc, stdout="active", stderr="")


def _ensure_ca():
    if Ctx.ca_raises:
        raise RuntimeError("openssl boom")
    return "/x/ca.crt"


def setup():
    tmp = tempfile.mkdtemp()
    bot.MITM_CONFIG = os.path.join(tmp, "mitm.json")
    bot.MITM_HIJACK_FILE = os.path.join(tmp, "mitm_hijack.txt")
    bot._platform = lambda: "ios"
    bot._core_backend = lambda: "mihomo"
    bot._cfg_guard = _guard
    bot._apply_sb_inner = lambda mod: Ctx.apply_ret
    bot._svc_active = lambda unit, **k: Ctx.svc.get(unit, True)
    bot.sh = _sh
    mitm_ca.ensure_ca = _ensure_ca
    mitm_ca.prewarm = lambda d: len(d or [])


OLD = {"enabled": False, "accuracy": 50, "active": "old", "locations": [{"name": "old", "lat": 1.0, "lon": 2.0}]}
NEW = {"enabled": True, "accuracy": 50, "active": "tokyo", "locations": [{"name": "tokyo", "lat": 35.6, "lon": 139.7}]}


def reset_old():
    """把盘上恢复成"关闭态"基线(mitm.json disabled + 空 hijack), 并清故障开关。"""
    bot._wloc_save(OLD)
    bot._atomic_write_text(bot.MITM_HIJACK_FILE, "")
    Ctx.guard_ok = True; Ctx.apply_ret = (True, ""); Ctx.svc = {}; Ctx.ca_raises = False; Ctx.sh_fail = set()


def enabled_on_disk():
    try:
        return json.load(open(bot.MITM_CONFIG)).get("wloc", {}).get("enabled")
    except FileNotFoundError:
        return None


def hijack_on_disk():
    try:
        return open(bot.MITM_HIJACK_FILE).read()
    except FileNotFoundError:
        return ""


def main():
    setup()

    # ── 成功路径: 全绿 → 落新态(enabled + hijack) ──
    reset_old()
    okr, msg = bot._mitm_transact(NEW)
    assert okr, msg
    assert enabled_on_disk() is True; ok("成功: mitm.json 持久化 enabled=true")
    assert "gs-loc.apple.com" in hijack_on_disk(); ok("成功: hijack 写入接管域名")

    # ── BUSY: 拿不到锁 → 返回失败, 盘上不变(仍旧态) ──
    reset_old(); Ctx.guard_ok = False
    okr, msg = bot._mitm_transact(NEW)
    assert okr is False and msg == bot.BUSY_MSG; ok("BUSY(锁占用): 返回失败且是 BUSY_MSG")
    assert enabled_on_disk() is False; ok("BUSY: 未落新态(enabled 仍 False)")

    # ── CA 失败 → 回滚, 不落 enabled ──
    reset_old(); Ctx.ca_raises = True
    okr, msg = bot._mitm_transact(NEW)
    assert okr is False and "CA" in msg; ok("CA 失败: 返回失败(提示 CA)")
    assert enabled_on_disk() is False and hijack_on_disk().strip() == ""; ok("CA 失败: mitm.json/hijack 回滚旧态(不留 enabled=true)")

    # ── 内核校验/应用失败 → 回滚 ──
    reset_old(); Ctx.apply_ret = (False, "mihomo -t 失败")
    okr, msg = bot._mitm_transact(NEW)
    assert okr is False and "内核" in msg; ok("内核应用失败: 返回失败")
    assert enabled_on_disk() is False; ok("内核失败: 回滚旧态(不留 enabled=true)")

    # ── pdg-mitm 起不来(active 检测 False)→ 回滚, 不谎报成功 ──
    reset_old(); Ctx.svc = {"pdg-mitm": False}
    okr, msg = bot._mitm_transact(NEW)
    assert okr is False and "pdg-mitm" in msg; ok("pdg-mitm 未稳定: 返回失败(不谎报成功)")
    assert enabled_on_disk() is False; ok("pdg-mitm 失败: 回滚旧态")

    # ── pdg-mitm restart 返回码非 0 → 回滚 ──
    reset_old(); Ctx.sh_fail = {"pdg-mitm"}
    okr, msg = bot._mitm_transact(NEW)
    assert okr is False; ok("pdg-mitm restart rc!=0: 返回失败")
    assert enabled_on_disk() is False; ok("pdg-mitm rc!=0: 回滚旧态")

    # ── mosdns 起不来 → 回滚 ──
    reset_old(); Ctx.svc = {"mosdns": False}
    okr, msg = bot._mitm_transact(NEW)
    assert okr is False and "mosdns" in msg; ok("mosdns 未稳定: 返回失败")
    assert enabled_on_disk() is False; ok("mosdns 失败: 回滚旧态(不留 enabled=true)")

    # ── 关闭(new=OLD-like disabled): 无域名 → 停 pdg-mitm, hijack 清空, 成功 ──
    bot._wloc_save(NEW); bot._atomic_write_text(bot.MITM_HIJACK_FILE, "domain:gs-loc.apple.com\n")  # 先造"开启态"
    Ctx.guard_ok = True; Ctx.apply_ret = (True, ""); Ctx.svc = {}; Ctx.ca_raises = False; Ctx.sh_fail = set()
    okr, msg = bot._mitm_transact({**NEW, "enabled": False})
    assert okr, msg
    assert enabled_on_disk() is False and hijack_on_disk().strip() == ""; ok("关闭: enabled=False + 清空 hijack(无域名不需 CA)")

    print(f"\n通过 {pass_n} 项断言")


if __name__ == "__main__":
    main()
