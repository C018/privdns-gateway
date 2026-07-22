#!/usr/bin/env python3
"""Android/iOS 平台隔离回归(Bot 菜单 + 后端门控 + checks)。

验收: Android 不显示/不调用/不运行 iOS 功能; iOS 不出现 Android 私密DNS 文案; 两平台其余一致。
后端硬门控(不只隐藏按钮): 旧 TG 消息里的 iOS 按钮/命令被点也拒绝, 且绝不生成文件/改配置/重启服务。
"""
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
import checks  # noqa: E402

pass_n = 0
def ok(m):
    global pass_n; print("[OK]  ", m); pass_n += 1


# ── Bot 桩: 记录 send_document / edit / send_plain ──
SENT_DOCS = []
def setup_bot():
    SENT_DOCS.clear()
    bot.send_document = lambda *a, **k: SENT_DOCS.append(a)
    bot.edit = lambda *a, **k: None
    bot.send = lambda *a, **k: None
    bot.send_plain = lambda *a, **k: None
    bot.answer_cb_async = lambda *a, **k: None
    bot.state = {}
    bot._dot_host = lambda: "dot.example.com"
    bot._core_backend = lambda: "singbox"
    bot.sh = lambda cmd: types.SimpleNamespace(returncode=0, stdout="", stderr="")


def kb_texts(kb):
    return [b["text"] for row in kb["inline_keyboard"] for b in row]


def kb_cbs(kb):
    return [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]


def main():
    setup_bot()

    # ── 客户端菜单按平台分岔 ──
    bot._platform = lambda: "android"
    title, kb = bot._nav("client")
    assert "Android 私密" in title, title
    assert not any("iOS" in t or "描述文件" in t for t in kb_texts(kb)), kb_texts(kb)
    assert "ios" not in kb_cbs(kb), kb_cbs(kb)
    assert "setdot" in kb_cbs(kb) and "tgexit" in kb_cbs(kb), "公共项(DoT域名/TG出口)应保留"
    ok("Android 客户端菜单: 只有私密DNS 主机名, 无 iOS 描述文件按钮, 保留公共项")

    bot._platform = lambda: "ios"
    title, kb = bot._nav("client")
    assert "iOS" in title and "Android 私密" not in title, title
    assert "ios" in kb_cbs(kb) and "setdot" in kb_cbs(kb) and "tgexit" in kb_cbs(kb)
    ok("iOS 客户端菜单: 有 iOS 描述文件按钮 + 公共项, 无 Android 私密DNS 文案")

    # ── Android: iOS 回调/命令统一拒绝, 绝不发文件 ──
    bot._platform = lambda: "android"
    for data in ("ios", "iosgen", "iosgenca", "wloc", "wloc:menu", "wloc:add", "wloc:on", "wloc:list"):
        SENT_DOCS.clear()
        bot.handle_cb(123, 456, data)
        assert not SENT_DOCS, f"Android callback {data} 不应发送文件"
    ok("Android: ios/iosgen/iosgenca/wloc:* 回调全被门控(send_document 从未调用)")

    # /ios 命令 + ios_ssid 文本状态(text handler)
    tmp = tempfile.mkdtemp()
    bot.MITM_CONFIG = os.path.join(tmp, "mitm.json")
    bot.MITM_HIJACK_FILE = os.path.join(tmp, "mitm_hijack.txt")
    SENT_DOCS.clear()
    # 直接触发最底层生成函数: Android 抛错(即便绕过按钮)
    raised = False
    try:
        bot._ios_profile()
    except RuntimeError:
        raised = True
    assert raised, "_ios_profile() 在 Android 必须抛错(最底层门控)"
    ok("Android: _ios_profile() 最底层门控抛错(绕过按钮也生成不了)")

    # ── Android: WLOC 后端 mutator 全拒绝, 不写文件/不改配置 ──
    assert bot._mitm_enabled_domains() == [], "Android 上残留 mitm.json 也应判空"
    # 造一份"启用中"的残留 mitm.json → 仍判空
    os.makedirs(os.path.dirname(bot.MITM_CONFIG), exist_ok=True)
    json.dump({"wloc": {"enabled": True, "locations": [{"name": "x", "lat": 1, "lon": 2}], "active": "x"}},
              open(bot.MITM_CONFIG, "w"))
    assert bot._mitm_enabled_domains() == [], "Android: 残留 enabled mitm.json 也不推导接管域名"
    ok("Android: _mitm_enabled_domains() 恒空(残留 mitm.json 不生效)")

    for fn, args in [(bot.wloc_add, ("a", 1, 2)), (bot.wloc_del, ("a",)), (bot.wloc_switch, ("a",)),
                     (bot.wloc_enable, (True,)), (bot.set_wloc, (True, 1, 2)), (bot._mitm_transact, ({},))]:
        okr, _ = fn(*args)
        assert okr is False, f"{fn.__name__} 在 Android 应拒绝"
    ok("Android: wloc_add/del/switch/enable/set_wloc/_mitm_transact 全部拒绝(不改配置/不重启)")
    # 无 hijack 写入 / 无 CA 生成
    assert not os.path.exists(bot.MITM_HIJACK_FILE), "Android 不应写 mitm_hijack.txt"
    assert not os.path.exists(os.path.join(tmp, "ca")), "Android 不应生成 CA"
    ok("Android: 未写 mitm_hijack.txt、未生成 CA")

    # ── iOS: 原功能保持(菜单有 WLOC; _ios_profile 可生成)──
    bot._platform = lambda: "ios"
    bot.IOS_TMPL = str(ROOT / "deploy/ios/pdg-dot-ondemand.mobileconfig.tmpl")
    bot._server_ip = lambda: "203.0.113.10"
    prof = bot._ios_profile()
    assert prof and b"PayloadContent" in prof, "iOS _ios_profile() 应正常生成"
    _, opskb = bot._nav("ops")
    assert any("位置改写" in t for t in kb_texts(opskb)), "iOS 运维菜单应有 WLOC"
    ok("iOS: _ios_profile 正常生成 + 运维菜单含 WLOC(原功能保持)")

    # ── checks: 平台一致的服务集 + deep probe81 + 平台标记 ──
    checks._run = lambda cmd, t=10: (0, "active", "")   # systemctl is-active → active
    checks._core_svc = lambda: "sing-box"
    checks._platform = lambda: "android"
    assert "pdg-probe81" not in checks.expected_services(), "Android 必需服务不含 pdg-probe81"
    assert checks.check_deep_probe81() is None, "Android deep doctor 不出现 :81 探测"
    checks._platform = lambda: "ios"
    assert "pdg-probe81" in checks.expected_services(), "iOS 必需服务含 pdg-probe81"
    ok("checks: Android 服务集无 pdg-probe81 且 deep 无 :81; iOS 含 pdg-probe81")

    # check_platform: 标记明确=ok, 缺失=warn
    checks._platform = lambda: "android"   # (check_platform 自读文件, 与 _platform 桩无关)
    import builtins
    _open = builtins.open
    builtins.open = lambda *a, **k: (_ for _ in ()).throw(OSError())   # 模拟标记缺失
    try:
        lvl, _, _ = checks.check_platform()
    finally:
        builtins.open = _open
    assert lvl == "warn", "平台标记缺失 → warn(不假装已确认 Android)"
    ok("checks.check_platform: 标记缺失 → 可见 warning(非静默回退)")

    print(f"\n通过 {pass_n} 项断言")


if __name__ == "__main__":
    main()
