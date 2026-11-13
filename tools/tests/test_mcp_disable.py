"""I2 · `HCA_MCP_DISABLE`（MCP 子集开关）的单测。

两条底线：
  · **不设开关时一个字都不改** —— 原样铺过去，注释与 ${VAR} 都留着由 AtomCode 展开。
  · 剥注释不能把 URL 里的 `//` 当成注释剥掉（`https://…` 是最容易踩的那个）。
"""
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("hca_init", REPO / "deploy" / "daemon" / "hca-init.py")
hca_init = importlib.util.module_from_spec(spec)
sys.modules["hca_init"] = hca_init
spec.loader.exec_module(hca_init)

SAMPLE = '''{
  // 注释：这一行整行都该被剥掉
  "mcpServers": {
    "akshare":   {"command": "/opt/hca/venv/bin/akshare-mcp", "timeout_ms": 120000},
    "watchlist": {"command": "/opt/hca/venv-hunter/bin/python",
                  "env": {"HERMES_API_URL": "${HERMES_API_URL:-http://api:8000}"}},
    "kronos":    {"command": "/opt/hca/venv/bin/kronos-mcp"}  // 行尾注释
  }
}'''


class TestFilterMcp(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("HCA_MCP_DISABLE")
        os.environ.pop("HCA_MCP_DISABLE", None)

    def tearDown(self):
        os.environ.pop("HCA_MCP_DISABLE", None)
        if self._old is not None:
            os.environ["HCA_MCP_DISABLE"] = self._old

    def test_不设开关时原样返回(self):
        text, removed = hca_init.filter_mcp(SAMPLE)
        self.assertEqual(text, SAMPLE)
        self.assertEqual(removed, [])

    def test_去掉指定的server(self):
        os.environ["HCA_MCP_DISABLE"] = "akshare,kronos"
        text, removed = hca_init.filter_mcp(SAMPLE)
        self.assertEqual(sorted(removed), ["akshare", "kronos"])
        obj = json.loads(text)
        self.assertEqual(list(obj["mcpServers"]), ["watchlist"])

    def test_保留没被点名的server的完整配置(self):
        os.environ["HCA_MCP_DISABLE"] = "akshare"
        obj = json.loads(hca_init.filter_mcp(SAMPLE)[0])
        # ${VAR} 必须原样留着 —— 展开是 AtomCode 的事，预渲染会把密钥写进工作区
        self.assertEqual(obj["mcpServers"]["watchlist"]["env"]["HERMES_API_URL"],
                         "${HERMES_API_URL:-http://api:8000}")

    def test_点名不存在的server不炸(self):
        os.environ["HCA_MCP_DISABLE"] = "不存在的"
        text, removed = hca_init.filter_mcp(SAMPLE)
        self.assertEqual(removed, [])
        self.assertEqual(list(json.loads(text)["mcpServers"]), ["akshare", "watchlist", "kronos"])

    def test_剥注释不碰字符串里的双斜杠(self):
        src = '{"mcpServers": {"a": {"url": "https://x.example.com/v1"}}}  // 尾注释'
        os.environ["HCA_MCP_DISABLE"] = "b"
        obj = json.loads(hca_init.filter_mcp(src)[0])
        self.assertEqual(obj["mcpServers"]["a"]["url"], "https://x.example.com/v1")

    def test_解析不了就原样返回而不是把文件写坏(self):
        os.environ["HCA_MCP_DISABLE"] = "akshare"
        broken = '{"mcpServers": {'
        text, removed = hca_init.filter_mcp(broken)
        self.assertEqual(text, broken)
        self.assertEqual(removed, [])

    def test_真实的mcp_json能被剥注释并解析(self):
        real = (REPO / "distro" / "workspace-template" / ".mcp.json").read_text(encoding="utf-8")
        obj = json.loads(hca_init._strip_jsonc(real))
        self.assertGreaterEqual(len(obj["mcpServers"]), 20)
        os.environ["HCA_MCP_DISABLE"] = (
            "hcapack,akshare,kronos,truesource,screener,hunter_cap,runtime_control,fin_memory,"
            "broker_security,broker_market,broker_disclosure,broker_fundamental,"
            "broker_funds,broker_research,broker_education,broker_account_readonly"
        )
        text, removed = hca_init.filter_mcp(real)
        self.assertEqual(len(removed), 16)
        self.assertEqual(sorted(json.loads(text)["mcpServers"]),
                         ["hunter_user", "portfolio", "uzi", "watchlist"])


if __name__ == "__main__":
    unittest.main()


class TestFilterPersona(unittest.TestCase):
    """人设里按 MCP 开关裁剪的块。

    不裁的话，`HCA_MCP_DISABLE=hcapack` 时人设仍然写着「优先调
    `mcp__hcapack__stock_snapshot`」，而那个工具根本不在模型的工具清单里 ——
    等于叫它去调一个看不见的工具，白费一轮。
    """

    SRC = ("前言\n"
           "<!-- hca:if-mcp hcapack -->\n"
           "只有挂了 hcapack 才该看见的内容\n"
           "<!-- /hca:if-mcp -->\n"
           "后文\n")

    def test_没关任何server时原样保留(self):
        out, dropped = hca_init.filter_persona(self.SRC, set())
        self.assertIn("只有挂了 hcapack 才该看见的内容", out)
        self.assertNotIn("hca:if-mcp", out, "标记本身不该留给模型看")
        self.assertEqual(dropped, [])

    def test_关掉之后整块消失(self):
        out, dropped = hca_init.filter_persona(self.SRC, {"hcapack"})
        self.assertNotIn("hcapack 才该看见", out)
        self.assertIn("前言", out)
        self.assertIn("后文", out)
        self.assertEqual(dropped, ["hcapack"])

    def test_一块依赖多个server时关掉任意一个就裁(self):
        src = "a\n<!-- hca:if-mcp akshare, kronos -->\nX\n<!-- /hca:if-mcp -->\nb\n"
        self.assertNotIn("X", hca_init.filter_persona(src, {"kronos"})[0])
        self.assertIn("X", hca_init.filter_persona(src, {"truesource"})[0])

    def test_没有标记的人设一个字都不动(self):
        src = "## 一、你是谁\n正文\n"
        self.assertEqual(hca_init.filter_persona(src, {"hcapack"}), (src, []))

    def test_真实人设里的块能被正确裁掉(self):
        real = (REPO / "distro" / "workspace-template" / ".atomcode.md").read_text(encoding="utf-8")
        self.assertIn("hca:if-mcp hcapack", real)
        self.assertIn("hca:if-mcp runtime_control", real)
        self.assertIn("hca:if-mcp fin_memory", real)
        kept, _ = hca_init.filter_persona(real, set())
        cut, dropped = hca_init.filter_persona(real, {"hcapack", "runtime_control", "fin_memory"})
        self.assertIn("mcp__hcapack__stock_snapshot", kept)
        self.assertIn("mcp__runtime_control__runtime_workflow_start", kept)
        self.assertIn("mcp__fin_memory__fin_memory_query", kept)
        self.assertNotIn("mcp__hcapack__", cut)
        self.assertNotIn("mcp__runtime_control__", cut)
        self.assertNotIn("mcp__fin_memory__", cut)
        self.assertEqual(sorted(dropped), ["fin_memory", "hcapack", "runtime_control"])
        # 裁掉之后正文仍然连贯：第 4 条（akshare 三连）必须还在
        self.assertIn("akshare_search", cut)
        self.assertNotIn("hca:if-mcp", kept + cut)

    def test_disabled_mcp读环境变量(self):
        os.environ["HCA_MCP_DISABLE"] = "a, b ,"
        self.assertEqual(hca_init.disabled_mcp(), {"a", "b"})
        os.environ.pop("HCA_MCP_DISABLE")
        self.assertEqual(hca_init.disabled_mcp(), set())
