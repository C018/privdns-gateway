#!/usr/bin/env python3
"""平台文案/门控回归 + README 一致性(本次文案任务)。

覆盖:
  1. Android 客户端菜单不含 iOS 描述文件; 4. iOS 客户端菜单含 iOS 描述文件。
  2. Android 运维菜单不含 WLOC; 7. iOS+mihomo 可进入 WLOC。
  3. Android 状态显示 Android 私密 DNS; 5. iOS 状态显示 iOS 描述文件。
  6. iOS+sing-box 不能开启 WLOC, 提示切 mihomo。
  8. WLOC 开启时不能切回 sing-box。
  9. 故障组/内核切换/更新文案与 README 一致。
 10. 所有 README 相对链接存在。
"""
import importlib.util as u
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy" / "bot"))
spec = u.spec_from_file_location("pdg_bot", ROOT / "deploy/bot/pdg-bot.py")
bot = u.module_from_spec(spec); spec.loader.exec_module(bot)

pass_n = 0
def ok(m):
    global pass_n; print("[OK]  ", m); pass_n += 1


CAP = []   # 捕获 edit(chat, mid, text, kb)
def setup():
    CAP.clear()
    bot._dot_host = lambda: "dot.example.com"
    bot._server_ip = lambda: "203.0.113.1"
    bot._core_backend = lambda: "mihomo"
    bot.sh = lambda cmd: types.SimpleNamespace(returncode=0, stdout="active active active", stderr="")
    bot.load = lambda: {"outbounds": [{"type": "direct", "tag": "jp"}], "route": {"rules": [], "final": "jp"}}
    bot.exit_tags = lambda c: ["jp"]
    bot._rs_meta = lambda: {}
    bot._core_svc = lambda: "sing-box"
    bot._mitm_config = lambda: {}
    bot.edit = lambda chat, mid, text, kb=None: CAP.append(text)
    bot.send = lambda *a, **k: None
    bot.send_plain = lambda *a, **k: None
    bot.state = {}


def texts(kb):
    return [b["text"] for row in kb["inline_keyboard"] for b in row]


def cbs(kb):
    return [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]


def main():
    setup()

    # 1 / 4 客户端菜单
    bot._platform = lambda: "android"
    t, kb = bot._nav("client")
    assert "Android 私密 DNS" in t and "ios" not in cbs(kb)
    assert not any("iOS" in x or "描述文件" in x for x in texts(kb))
    assert "setdot" in cbs(kb) and "tgexit" in cbs(kb)
    ok("Android 客户端菜单: 无 iOS 描述文件, 显示 Android 私密 DNS + 公共项")
    bot._platform = lambda: "ios"
    t, kb = bot._nav("client")
    assert "ios" in cbs(kb) and "请生成并安装 iOS 描述文件" in t
    ok("iOS 客户端菜单: 含 iOS 描述文件入口")

    # 2 / 7 运维菜单 WLOC
    bot._platform = lambda: "android"
    _, opskb = bot._nav("ops")
    assert not any("位置改写" in x for x in texts(opskb))
    ok("Android 运维菜单: 无 WLOC")
    bot._platform = lambda: "ios"
    _, opskb = bot._nav("ops")
    assert any("位置改写" in x for x in texts(opskb))
    ok("iOS 运维菜单: 含 WLOC")

    # 3 / 5 状态 DoT 文案
    bot._platform = lambda: "android"
    assert "（Android 私密 DNS）" in bot.status_text() and "iOS 描述文件" not in bot.status_text()
    ok("Android 状态: DoT 显示 Android 私密 DNS")
    bot._platform = lambda: "ios"
    assert "（iOS 描述文件）" in bot.status_text()
    ok("iOS 状态: DoT 显示 iOS 描述文件")

    # 6 iOS + sing-box 不能开启 WLOC
    bot._platform = lambda: "ios"; bot._core_backend = lambda: "singbox"
    r_ok, msg = bot.wloc_enable(True)
    assert r_ok is False and "mihomo" in msg
    ok("iOS+sing-box: wloc_enable 被拒并提示切 mihomo")
    # 进入 WLOC 菜单也提示切 mihomo
    CAP.clear(); bot.handle_cb(1, 2, "wloc")
    assert CAP and "mihomo" in CAP[-1]
    ok("iOS+sing-box: 点 WLOC 入口提示需要 mihomo(不进菜单)")
    # 7 iOS + mihomo 可进入 WLOC 菜单
    bot._core_backend = lambda: "mihomo"; CAP.clear(); bot.handle_cb(1, 2, "wloc")
    assert CAP and "位置改写" in CAP[-1] and "需要 mihomo" not in CAP[-1]
    ok("iOS+mihomo: 正常进入 WLOC 菜单")

    # 8 WLOC 开启时不能切回 sing-box
    bot._mitm_config = lambda: {"wloc": {"enabled": True, "locations": [{"name": "x", "lat": 1, "lon": 2}], "active": "x"}}
    bot._core_backend = lambda: "mihomo"   # 当前 mihomo → 目标 singbox
    CAP.clear(); bot.handle_cb(1, 2, "switchcore")
    assert CAP and "请先关闭 WLOC" in CAP[-1]
    ok("WLOC 开启时切回 sing-box 被阻止(提示先关闭 WLOC)")

    # 9 文案与 README 一致
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    src = (ROOT / "deploy" / "bot" / "pdg-bot.py").read_text(encoding="utf-8")
    for phrase in ("按探测延迟选择出口，并在出口不可用时切换",   # 故障组
                   "mihomo 版本随 PrivDNS Gateway 发布更新",      # 内核切换(→mihomo)
                   "sing-box 固定使用 1.12.x",                    # 内核切换(→singbox)
                   "指定并校验过的内核版本"):                     # 更新页
        assert phrase in src, f"pdg-bot.py 缺文案: {phrase}"
    # README 与 Bot 语义一致: 共享关键短语都在两处出现
    for phrase in ("按探测延迟选择出口", "固定使用 1.12.x", "指定并校验过的"):
        assert phrase in readme, f"README 缺一致文案: {phrase}"
    ok("故障组/内核切换/更新文案在 Bot 与 README 中一致")

    # 10 README 相对链接存在
    import re
    miss = [l for l in re.findall(r"\]\(([^)]+)\)", readme)
            if not l.startswith("http") and not (ROOT / l).exists()]
    assert not miss, f"README 缺失链接: {miss}"
    ok("README 所有相对链接均存在")

    print(f"\n通过 {pass_n} 项断言")


if __name__ == "__main__":
    main()
