#!/usr/bin/env python3
"""Static regression for doctor firewall port coverage."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
checks = (ROOT / "deploy/bot/checks.py").read_text(encoding="utf-8")

assert '{"53", "80", "81", "443", "853", "8445"}' in checks, (
    "doctor firewall leak detection must include the TG SOCKS5 port 8445"
)
assert "53/80/81/443/853/8445" in checks, (
    "doctor firewall OK text should mention the TG SOCKS5 port 8445"
)
