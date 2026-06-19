"""编译器与生成器核心不变量测试 (标准库 unittest, 无需 pytest)。

运行: PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# 让 resolve_paths 用仓库内 config/, 产物写到临时 var。
os.environ["PDG_ETC"] = str(ROOT / "config")

from pdg.config import load_config, resolve_paths  # noqa: E402
from pdg.generators import dnsdist as gen_dnsdist  # noqa: E402
from pdg.generators import singbox as gen_singbox  # noqa: E402
from pdg.services import build_table  # noqa: E402


class CompilerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = resolve_paths()
        cls.config = load_config(cls.paths)
        cls.table = build_table(cls.config, cls.paths, allow_download=False)

    def test_proxy_domain_to_remote(self):
        cr = self.table.match("api.openai.com")   # 子域命中后缀
        self.assertEqual(cr.policy, "AI")
        self.assertEqual(cr.outbound, "tw-ss2022")
        self.assertEqual(cr.dns_mode, "spoof")

    def test_media_to_hk(self):
        self.assertEqual(self.table.match("youtube.com").outbound, "hk-ss2022")
        self.assertEqual(self.table.match("x.com").outbound, "hk-ss2022")

    def test_google_play_to_hk(self):
        # Play 下载源 gvt1.com 必须代理到 HK
        self.assertEqual(self.table.match("gvt1.com").outbound, "hk-ss2022")
        self.assertEqual(self.table.match("play.google.com").outbound, "hk-ss2022")

    def test_dnsdist_source_gated_full_proxy(self):
        # 全代理 + 配了 internal_src_cidr: 默认 spoof 按来源门控, 且排除 direct 域名
        lua = gen_dnsdist.generate(self.table, self.config)
        self.assertIn("NetmaskGroupRule(pdgInternal)", lua)
        self.assertIn("NotRule(SuffixMatchNodeRule(pdgDirect))", lua)  # 排除国内直连
        self.assertIn('pdgDirect:add("taobao.com")', lua)
        self.assertIn("SpoofAction({SPOOF_IP}", lua)

    def test_direct_domain_real_ip(self):
        cr = self.table.match("bilibili.com")
        self.assertEqual(cr.outbound, "direct")
        self.assertEqual(cr.dns_mode, "direct")

    def test_final_proxies_to_jp(self):
        # 全代理模式: 未命中域名默认走 JP 代理
        cr = self.table.match("nowhere.example")
        self.assertEqual(cr.rule.type, "FINAL")
        self.assertEqual(cr.outbound, "jp")
        self.assertEqual(cr.dns_mode, "spoof")

    def test_domestic_direct(self):
        # 国内站直连
        self.assertEqual(self.table.match("taobao.com").dns_mode, "direct")
        self.assertEqual(self.table.match("bilibili.com").dns_mode, "direct")

    def test_dnsdist_full_proxy_excludes_direct(self):
        # 全代理: 默认 spoof, 国内站进 direct 排除集; 代理域名不单独列出 (catch-all 覆盖)
        lua = gen_dnsdist.generate(self.table, self.config)
        self.assertIn("SpoofAction({SPOOF_IP}", lua)
        self.assertIn('pdgDirect:add("bilibili.com")', lua)
        self.assertNotIn('pdgDirect:add("openai.com")', lua)

    def test_singbox_pathA_inbounds(self):
        conf = json.loads(gen_singbox.generate(self.table, self.config))
        self.assertEqual(conf["route"]["final"], "jp")
        tags = {ob["tag"] for ob in conf["outbounds"]}
        self.assertTrue({"hk-ss2022", "tw-ss2022", "jp"} <= tags)

        inbounds = {ib["tag"]: ib for ib in conf["inbounds"]}
        self.assertIn("in-https", inbounds)
        self.assertIn("in-http", inbounds)
        # 443 入口: 必须 sniff + 覆盖目标, 且收 TCP+UDP(QUIC) → 无 network 限制
        https = inbounds["in-https"]
        self.assertTrue(https["sniff"])
        self.assertTrue(https["sniff_override_destination"])
        self.assertNotIn("network", https)
        # 80 入口仅 TCP
        self.assertEqual(inbounds["in-http"].get("network"), "tcp")
        # shadowsocks 出口不限 network (要支持 UDP/QUIC)
        hk = next(ob for ob in conf["outbounds"] if ob["tag"] == "hk-ss2022")
        self.assertNotIn("network", hk)

    def test_singbox_telegram_to_hk(self):
        conf = json.loads(gen_singbox.generate(self.table, self.config))
        hk_rule = next(r for r in conf["route"]["rules"] if r["outbound"] == "hk-ss2022")
        self.assertIn("t.me", hk_rule["domain_suffix"])
        self.assertIn("xn--ngstr-lra8j.com", hk_rule["domain_suffix"])


if __name__ == "__main__":
    unittest.main()
