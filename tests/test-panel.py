#!/usr/bin/env python3
"""Regression: 观测面板 (zashboard) 开关 + clash_api secret 适配。"""
import importlib.util
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pdg_bot", ROOT / "deploy/bot/pdg-bot.py")
bot = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bot)

# ── clash_get: 有 secret 带 Bearer, 无 secret 不带 ──────────────────────────
cap = {}
def fake_urlopen(req, timeout=None):
    cap["auth"] = req.get_header("Authorization")
    class R:
        def __enter__(s): return s
        def __exit__(s, *a): pass
        def read(s): return b'{"v":1}'
    return R()
bot.urllib.request.urlopen = fake_urlopen

bot.load = lambda: {"experimental": {"clash_api": {"external_controller": "127.0.0.1:9090"}}}
bot.clash_get("/version")
assert cap["auth"] is None, "无 secret 不该带 Authorization"
bot.load = lambda: {"experimental": {"clash_api": {"secret": "S3CR3T"}}}
bot.clash_get("/version")
assert cap["auth"] == "Bearer S3CR3T", ("有 secret 应带 Bearer", cap["auth"])
print("[OK]   clash_get 按 secret 带/不带 Bearer")

# ── set_panel(on): clash_api 改 0.0.0.0 + secret + external_ui, 生成一键链接 ──
_REAL_ENSURE = bot._ensure_zashboard        # 存真函数, 供后面 SHA 校验用例
bot._ensure_zashboard = lambda: (True, "")
bot._panel_cidr = lambda: "172.22.0.0/16"
fw = []
bot._panel_firewall = lambda on, cidr: fw.append((on, cidr))
bot._server_ip = lambda: "203.0.113.9"
cfg = {"experimental": {"clash_api": {"external_controller": "127.0.0.1:9090"}}}
def fake_apply(mod):
    mod(cfg); return True, ""
bot.apply_sb = fake_apply

ok, link = bot.set_panel(True)
assert ok, link
api = cfg["experimental"]["clash_api"]
assert api["external_controller"] == "0.0.0.0:9090", api
assert api["external_ui"] == bot.UI_DIST
sec = api["secret"]; assert sec and len(sec) >= 16
assert link == f"http://203.0.113.9:9090/ui/#/setup?hostname=203.0.113.9&port=9090&secret={sec}", link
assert bot._panel_on(cfg) is True
assert fw and fw[-1] == (True, "172.22.0.0/16"), "应放行内网卡段 → 9090"
print("[OK]   set_panel(on): clash_api 0.0.0.0+secret+external_ui + 一键链接 + 放行内网 9090")

# ── set_panel(off): 收回 127.0.0.1, 去掉 secret/external_ui, 撤防火墙 ──────────
ok, msg = bot.set_panel(False)
assert ok, msg
api = cfg["experimental"]["clash_api"]
assert api["external_controller"] == "127.0.0.1:9090"
assert "secret" not in api and "external_ui" not in api
assert bot._panel_on(cfg) is False
assert fw[-1] == (False, "172.22.0.0/16"), "应撤销 9090 放行"
print("[OK]   set_panel(off): 收回 127.0.0.1 + 去 secret/external_ui + 撤放行")

# ── 无内网段 → 拒绝开启(不裸奔) ──────────────────────────────────────────────
bot._panel_cidr = lambda: ""
ok, err = bot.set_panel(True)
assert not ok and "内网" in err, ("无内网段应拒绝", ok, err)
print("[OK]   读不到内网卡段 → 拒绝开启")

# ── _ensure_zashboard: SHA256 不符 → 拒绝(供应链校验)────────────────────────
bot._ensure_zashboard = _REAL_ENSURE        # 恢复真函数
bot._fetch_bytes = lambda url: b"not-a-real-zashboard-zip"
bot.UI_DIR = tempfile.mkdtemp(); bot.UI_DIST = os.path.join(bot.UI_DIR, "dist")
ok, err = bot._ensure_zashboard()
assert not ok and "SHA256" in err, ("SHA 不符应拒绝", ok, err)
print("[OK]   zashboard SHA256 不符 → 拒绝安装")

# ── 菜单/回调接线 ────────────────────────────────────────────────────────────
src = (ROOT / "deploy/bot/pdg-bot.py").read_text(encoding="utf-8")
assert '"callback_data": "panel"' in src, "运维菜单应有观测面板入口"
for cb in ('if data == "panel":', 'if data == "panel:on":', 'if data == "panel:off":'):
    assert cb in src, f"缺回调 {cb}"
print("[OK]   运维菜单 + panel/on/off 回调接线")

print("panel regression OK")
