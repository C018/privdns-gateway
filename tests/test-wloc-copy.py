#!/usr/bin/env python3
"""WLOC「手机端」文案一致性回归。

README 与 TG Bot 必须给出**同一套**三步手机端操作(顺序、措辞、路径都一致), 且:
  · 标题各自用本媒介的粗体(README = Markdown **…**, Bot = parse_mode 兼容 <b>…</b>);
  · 三项分行(README 用 Markdown 列表项, Bot 用 \\n 分隔);
  · 只出现在 iOS/WLOC 语境, 不进 Android 菜单/安装流程。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
readme = (ROOT / "README.md").read_text(encoding="utf-8")
bot = (ROOT / "deploy/bot/pdg-bot.py").read_text(encoding="utf-8")

pass_n = 0
def ok(m):
    global pass_n; print("[OK]  ", m); pass_n += 1


ITEMS = [
    "首次 / 后续无法定位时：设置 → 通用 → 传输或还原 iPhone → 还原 → 还原位置与隐私 → 重启手机",
    "关闭再开启定位服务：设置 → 隐私与安全性 → 定位服务",
    "控制中心关 Wi-Fi（把图标点灰，不是在设置里关）",
]
TITLE = "手机端（全程用内网卡）："

# ── 标题: 各自媒介的粗体 ──
assert f"**{TITLE}**" in readme, "README 标题需为 Markdown 粗体"
ok("README: 标题 Markdown 粗体")
assert f"<b>{TITLE}</b>" in bot, "Bot 标题需为 parse_mode 兼容粗体"
ok("Bot: 标题 <b> 粗体(parse_mode 兼容)")

# ── 三项: 两边逐条同措辞, 且各自分行 ──
for i, it in enumerate(ITEMS, 1):
    assert re.search(r"^- " + re.escape(it) + r"$", readme, re.M), f"README 缺第{i}项或未独占一行: {it}"
    assert f"· {it}\\n" in bot or f"· {it}\"" in bot, f"Bot 缺第{i}项: {it}"
ok("README: 三项各自独占一个 Markdown 列表项(渲染分行)")
ok("Bot: 三项以 · 开头并各自 \\n 分行")

# ── 顺序一致 ──
r_pos = [readme.index(it) for it in ITEMS]
b_pos = [bot.index(it) for it in ITEMS]
assert r_pos == sorted(r_pos), "README 三项顺序与约定不符"
assert b_pos == sorted(b_pos), "Bot 三项顺序与约定不符"
ok("README / Bot 三项顺序一致(首次还原 → 定位服务 → 控制中心关 Wi-Fi)")

# ── 关键措辞不得被改写 ──
for kw in ("全程用内网卡", "控制中心关 Wi-Fi", "还原位置与隐私", "隐私与安全性 → 定位服务"):
    assert kw in readme and kw in bot, f"关键措辞被改写/缺失: {kw}"
ok("关键措辞保留(全程用内网卡 / 控制中心关 Wi-Fi / 还原位置与隐私 / 定位服务路径)")

# ── 平台隔离: 这套文案只在 iOS/WLOC 语境, 不进 Android 菜单/安装 ──
for p in ("install.sh", "deploy/bot/pdg.sh"):
    txt = (ROOT / p).read_text(encoding="utf-8")
    for it in ITEMS:
        assert it not in txt, f"{p} 不应包含 WLOC 手机端文案: {it}"
ok("install.sh / pdg.sh 不含该文案(不进 Android 菜单与安装流程)")

# Bot 里该文案只出现在 WLOC 菜单一处
assert bot.count(ITEMS[2]) == 1, "Bot 中 WLOC 手机端文案出现多处(应只在 WLOC 菜单)"
ok("Bot 中该文案仅 WLOC 菜单一处")

print(f"\n通过 {pass_n} 项断言")
