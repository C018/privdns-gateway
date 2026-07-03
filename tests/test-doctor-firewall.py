#!/usr/bin/env python3
"""Static + dynamic regression for doctor firewall port coverage."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
checks_src = (ROOT / "deploy/bot/checks.py").read_text(encoding="utf-8")

assert '{"53", "80", "81", "443", "853", "5228", "5229", "5230", "8445"}' in checks_src, (
    "doctor firewall leak detection must include TG SOCKS5 8445 and GMS 5228-5230"
)
assert "53/80/81/443/853/5228-5230/8445" in checks_src, (
    "doctor firewall OK text should mention 8445 and 5228-5230"
)

# 动态: 端口区间写法(如 5228-5230)对全网开放也要被识别为泄露; 限内网来源则不报。
spec = importlib.util.spec_from_file_location("pdg_checks", ROOT / "deploy/bot/checks.py")
checks = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(checks)

checks._run = lambda cmd: (0, "chain input {\n tcp dport { 22 } accept\n"
                              " tcp dport { 5228-5230 } accept\n}", "")
st, _, msg = checks.check_nft()
assert st == "fail" and "5228" in msg and "5230" in msg, (st, msg)

checks._run = lambda cmd: (0, "chain input {\n tcp dport { 22 } accept\n"
                              " ip saddr 172.22.0.0/16 tcp dport { 53, 80-81, 443, 853, 5228-5230, 8445 } accept\n}", "")
st, _, msg = checks.check_nft()
assert st == "ok", (st, msg)

# 宽区间对全网开放: 不枚举也要把落在区间内的敏感端口全报出来
checks._run = lambda cmd: (0, "chain input {\n tcp dport { 1-65535 } accept\n}", "")
st, _, msg = checks.check_nft()
assert st == "fail", (st, msg)
for p in ("53", "443", "5228", "5230", "8445"):
    assert p in msg, (p, msg)

# 宽区间但限定内网来源: 不算泄露
checks._run = lambda cmd: (0, "chain input {\n ip saddr 172.22.0.0/16 tcp dport { 1-65535 } accept\n}", "")
st, _, msg = checks.check_nft()
assert st == "ok", (st, msg)

# ── check_gms: sing-box 三入站 + 防火墙内网放行 → ok; 任一缺失 → warn(不 fail) ──
import json, tempfile

NFT_OK = ("chain input {\n ip saddr 172.22.0.0/16 tcp dport { 53, 80-81, 443, 853, 5228-5230, 8445 } accept\n}")
NFT_NO_GMS = ("chain input {\n ip saddr 172.22.0.0/16 tcp dport { 53, 80-81, 443, 853, 8445 } accept\n}")

def gms_case(ports, nft_out):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"inbounds": [{"type": "direct", "listen_port": p} for p in ports]}, f)
        path = f.name
    checks.SB = path
    checks._run = lambda cmd: (0, nft_out, "")
    return checks.check_gms()

st, _, msg = gms_case([443, 80, 5228, 5229, 5230], NFT_OK)
assert st == "ok" and "5228-5230" in msg, (st, msg)

st, _, msg = gms_case([443, 80, 5229, 5230], NFT_OK)          # sing-box 缺 5228
assert st == "warn" and "pdg" in msg, (st, msg)

st, _, msg = gms_case([443, 80, 5228, 5229, 5230], NFT_NO_GMS)  # 防火墙缺 5228-5230
assert st == "warn", (st, msg)

st, _, msg = gms_case([443, 80], NFT_NO_GMS)                    # 双缺也只 warn, 不 fail
assert st == "warn", (st, msg)

print("doctor-firewall regression OK")
