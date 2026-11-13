"""tools/build_skills.py 的单测。

跑法（仓库根）：

    python3 -m pytest tools/tests -q

测的是「转换器会不会把技能改坏」，所以除了 happy path，重点在三类回归：

* **不该动的别动**：`hunter:` 扩展字段、`references/` 里的 `{真实数字}`、
  `hunter.prompt_tpl` 里的 `{股票}` —— 这三处曾经是「无脑替换占位符」方案
  会写坏的地方。
* **幂等**：同样的输入跑两遍，第二遍必须 0 变化（`--check` 要能当 CI 门禁用）。
* **工具名映射**：`<服务>_<工具>` 与裸工具名两种写法都要能映射到
  `mcp__<服务>__<工具>`；映射不到时不能静默丢掉。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_skills as bs  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

TOOL_MAP = {
    "servers": {
        "uzi": ["stock_deep_analysis"],
        "watchlist": ["stock_news", "stock_quickview"],
        "portfolio": ["portfolio_rebalance", "update_risk_profile"],
        "hunter_cap": ["skill_stage"],
        "hunter_user": ["invoke"],
    }
}

SAMPLE = """---
name: demo
description: 演示技能
version: 1.0.0
metadata:
  tags: [finance]
hunter:
  display_name: 演示
  icon: Article
  category: 投研报告
  prompt_tpl: 帮我看看 {股票}
  needs_tools:
    - uzi_stock_deep_analysis
    - watchlist_stock_news
---

# 演示

调用 `uzi_stock_deep_analysis` 拿数据，再调 `watchlist_stock_news` 看新闻。
"""


@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """建一个 (src, dst, tool_map) 三件套，src 里放一个 demo 技能。"""
    src = tmp_path / "skills"
    (src / "demo").mkdir(parents=True)
    (src / "demo" / "SKILL.md").write_text(SAMPLE, encoding="utf-8")
    dst = tmp_path / "out"
    tm = tmp_path / "mcp-tools.json"
    tm.write_text(json.dumps(TOOL_MAP, ensure_ascii=False), encoding="utf-8")
    return src, dst, tm


# ── 工具名映射 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("uzi_stock_deep_analysis", "mcp__uzi__stock_deep_analysis"),
        ("watchlist_stock_news", "mcp__watchlist__stock_news"),
        # 工具名自己就带服务名前缀时，不能切成「服务 portfolio + 工具 rebalance」
        ("portfolio_portfolio_rebalance", "mcp__portfolio__portfolio_rebalance"),
        ("portfolio_rebalance", "mcp__portfolio__portfolio_rebalance"),
        ("portfolio_update_risk_profile", "mcp__portfolio__update_risk_profile"),
        # 裸工具名（SKILL 里偶尔这么写）
        ("stock_deep_analysis", "mcp__uzi__stock_deep_analysis"),
        # 共享前缀的两个服务不能串
        ("hunter_cap_skill_stage", "mcp__hunter_cap__skill_stage"),
        ("hunter_user_invoke", "mcp__hunter_user__invoke"),
    ],
)
def test_工具名映射(raw: str, expected: str) -> None:
    assert bs.map_tool_name(raw, TOOL_MAP["servers"]) == expected


def test_映射不到返回_None() -> None:
    assert bs.map_tool_name("不存在的工具", TOOL_MAP["servers"]) is None


def test_映射不到时原样保留且计入日志(tmp_path: Path) -> None:
    src = tmp_path / "skills"
    (src / "x").mkdir(parents=True)
    (src / "x" / "SKILL.md").write_text(
        "---\nname: x\ndescription: d\nhunter:\n  needs_tools:\n    - 查无此工具\n---\n\n正文\n",
        encoding="utf-8",
    )
    tm = tmp_path / "m.json"
    tm.write_text(json.dumps(TOOL_MAP), encoding="utf-8")
    logs, _ = bs.build(src, tmp_path / "out", tm, check=False)
    assert logs[0].unmapped == ["查无此工具"]
    out = (tmp_path / "out" / "x" / "SKILL.md").read_text(encoding="utf-8")
    assert "allowed-tools: 查无此工具" in out  # 不静默丢掉


# ── frontmatter ──────────────────────────────────────────────────────────────


def test_保留_hunter_扩展字段(workspace) -> None:
    src, dst, tm = workspace
    bs.build(src, dst, tm, check=False)
    out = (dst / "demo" / "SKILL.md").read_text(encoding="utf-8")
    for line in (
        "hunter:",
        "  display_name: 演示",
        "  icon: Article",
        "  category: 投研报告",
        "  prompt_tpl: 帮我看看 {股票}",  # ← 前端模板字段必须原样
        "    - uzi_stock_deep_analysis",
    ):
        assert line in out, line
    assert "version: 1.0.0" in out
    assert "  tags: [finance]" in out


def test_写入_allowed_tools_与_user_invocable(workspace) -> None:
    src, dst, tm = workspace
    bs.build(src, dst, tm, check=False)
    out = (dst / "demo" / "SKILL.md").read_text(encoding="utf-8")
    assert "allowed-tools: mcp__uzi__stock_deep_analysis mcp__watchlist__stock_news" in out
    assert "user-invocable: true" in out


def test_allowed_tools_顶格不被缩进键干扰() -> None:
    fm = "name: a\ndescription: b\nhunter:\n  display_name: c\n"
    got = bs.upsert_fm_line(fm, "allowed-tools", "t1 t2")
    assert got.splitlines()[2] == "allowed-tools: t1 t2"
    # 缩进的 display_name 不该被当成顶层 name
    assert bs.fm_top_level_key("  display_name: c", "name") is None


def test_重复写入只替换不重复(workspace) -> None:
    fm = "name: a\ndescription: b\nallowed-tools: old\n"
    got = bs.upsert_fm_line(fm, "allowed-tools", "new")
    assert got.count("allowed-tools:") == 1
    assert "allowed-tools: new" in got


def test_needs_tools_行内数组写法() -> None:
    fm = "name: a\nhunter:\n  needs_tools: [uzi_stock_deep_analysis, watchlist_stock_news]\n"
    assert bs.parse_needs_tools(fm) == ["uzi_stock_deep_analysis", "watchlist_stock_news"]


def test_没有_frontmatter_报错() -> None:
    with pytest.raises(bs.BuildError):
        bs.split_frontmatter("# 光秃秃的正文\n")


# ── 正文加工 ──────────────────────────────────────────────────────────────────


def test_正文工具名改成_atomcode_真名(workspace) -> None:
    src, dst, tm = workspace
    logs, _ = bs.build(src, dst, tm, check=False)
    out = (dst / "demo" / "SKILL.md").read_text(encoding="utf-8")
    body = out.split("---", 2)[2]
    assert "`mcp__uzi__stock_deep_analysis`" in body
    assert "`uzi_stock_deep_analysis`" not in body.split(bs.BLOCK_BEGIN)[0]
    assert ("uzi_stock_deep_analysis", "mcp__uzi__stock_deep_analysis") in logs[0].body_tool_rewrites


def test_正文占位符转_arguments() -> None:
    body, found = bs.convert_body_placeholders("分析 {股票} 的 {行业}\n")
    assert body == "分析 $ARGUMENTS 的 $ARGUMENTS\n"
    assert found == ["股票", "行业"]


def test_生成参数区块带_arguments(workspace) -> None:
    src, dst, tm = workspace
    bs.build(src, dst, tm, check=False)
    out = (dst / "demo" / "SKILL.md").read_text(encoding="utf-8")
    assert bs.BLOCK_BEGIN in out and bs.BLOCK_END in out
    assert "$ARGUMENTS" in out
    assert "本技能的入参是**股票**" in out
    # 工具名对照表
    assert "| `uzi_stock_deep_analysis` | `mcp__uzi__stock_deep_analysis` |" in out


def test_参数区块在没有占位符时用通用措辞() -> None:
    block = bs.build_args_block([], [])
    assert "$ARGUMENTS" in block
    assert "用户这次调用带的参数" in block


def test_删除旧区块保证幂等() -> None:
    body = "正文\n\n" + bs.build_args_block(["股票"], [])
    assert bs.strip_generated_block(body).strip() == "正文"


# ── references ───────────────────────────────────────────────────────────────


def test_references_逐字节复制且不替换占位符(tmp_path: Path) -> None:
    src = tmp_path / "skills"
    (src / "p" / "references").mkdir(parents=True)
    (src / "p" / "SKILL.md").write_text(
        "---\nname: p\ndescription: d\n---\n\n正文\n", encoding="utf-8"
    )
    ref = "打分时填 {真实数字}，缺失项写 {真实缺失的关键维度}\n"
    (src / "p" / "references" / "rule.md").write_text(ref, encoding="utf-8")
    tm = tmp_path / "m.json"
    tm.write_text(json.dumps(TOOL_MAP), encoding="utf-8")

    logs, _ = bs.build(src, tmp_path / "out", tm, check=False)
    got = (tmp_path / "out" / "p" / "references" / "rule.md").read_text(encoding="utf-8")
    assert got == ref
    assert logs[0].refs_copied == 1


def test_references_删掉的文件会被同步清掉(tmp_path: Path) -> None:
    src = tmp_path / "skills"
    (src / "p" / "references").mkdir(parents=True)
    (src / "p" / "SKILL.md").write_text("---\nname: p\ndescription: d\n---\n\n正文\n", encoding="utf-8")
    (src / "p" / "references" / "a.md").write_text("a\n", encoding="utf-8")
    tm = tmp_path / "m.json"
    tm.write_text(json.dumps(TOOL_MAP), encoding="utf-8")
    dst = tmp_path / "out"
    bs.build(src, dst, tm, check=False)
    stale = dst / "p" / "references" / "stale.md"
    stale.write_text("旧的\n", encoding="utf-8")
    bs.build(src, dst, tm, check=False)
    assert not stale.exists()


def test_源里删掉的技能目录会被清掉(workspace) -> None:
    src, dst, tm = workspace
    bs.build(src, dst, tm, check=False)
    (dst / "已删除的技能").mkdir()
    (dst / "已删除的技能" / "SKILL.md").write_text("x", encoding="utf-8")
    bs.build(src, dst, tm, check=False)
    assert not (dst / "已删除的技能").exists()


# ── 幂等与 --check ────────────────────────────────────────────────────────────


def test_幂等_跑两遍第二遍无变化(workspace) -> None:
    src, dst, tm = workspace
    logs1, _ = bs.build(src, dst, tm, check=False)
    assert logs1[0].changed is True
    logs2, diffs2 = bs.build(src, dst, tm, check=False)
    assert logs2[0].changed is False
    assert diffs2 == []


def test_check_模式不落盘(workspace) -> None:
    src, dst, tm = workspace
    logs, diffs = bs.build(src, dst, tm, check=True)
    assert logs[0].changed is True
    assert diffs
    assert not (dst / "demo" / "SKILL.md").exists()


def test_main_check_有差异返回_1(workspace) -> None:
    src, dst, tm = workspace
    rc = bs.main(["--src", str(src), "--dst", str(dst), "--tool-map", str(tm), "--check", "--quiet"])
    assert rc == 1
    assert bs.main(["--src", str(src), "--dst", str(dst), "--tool-map", str(tm), "--quiet"]) == 0
    rc = bs.main(["--src", str(src), "--dst", str(dst), "--tool-map", str(tm), "--check", "--quiet"])
    assert rc == 0


def test_strict_遇到映射不到返回_2(tmp_path: Path) -> None:
    src = tmp_path / "skills"
    (src / "x").mkdir(parents=True)
    (src / "x" / "SKILL.md").write_text(
        "---\nname: x\ndescription: d\nhunter:\n  needs_tools:\n    - 查无此工具\n---\n\n正文\n",
        encoding="utf-8",
    )
    tm = tmp_path / "m.json"
    tm.write_text(json.dumps(TOOL_MAP), encoding="utf-8")
    rc = bs.main(["--src", str(src), "--dst", str(tmp_path / "o"), "--tool-map", str(tm), "--strict", "--quiet"])
    assert rc == 2


# ── 仓库现状 ─────────────────────────────────────────────────────────────────


def test_仓库产物是最新的() -> None:
    """CI 门禁：仓库里 distro/workspace-template/.atomcode/skills/ 必须与 skills/ 同步。"""
    rc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "build_skills.py"), "--check", "--quiet"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert rc.returncode == 0, rc.stdout + rc.stderr


def test_仓库里六个技能都映射得到工具() -> None:
    logs, _ = bs.build(check=True)
    names = [x.name for x in logs]
    for required in [
        "deep_analysis", "investor_panel", "lhb_analyzer",
        "risk_profile", "trap_detector", "uzi",
        "daily_market_brief", "watchlist_daily_brief", "announcement_impact",
        "earnings_review", "technical_dashboard", "fundamental_scorecard",
        "portfolio_risk_check", "after_close_review",
        "factor_research", "etf_dca_planner", "investment_thesis_tracker",
        "stock_screener_builder", "news_signal_digest", "trading_journal",
        "backtest_quality_auditor",
    ]:
        assert required in names, f"缺少技能: {required}"
    assert sum(len(x.unmapped) for x in logs) == 0
    assert sum(len(x.allowed_tools) for x in logs) >= 8


def test_mcp_tools_注册表与_mcp_json_的_autoapprove_一致() -> None:
    """两边漂了就会出现「工具挂着但每次都弹权限」这种只有真跑才发现的问题。"""
    import re

    tm = json.loads((REPO_ROOT / "distro" / "mcp-tools.json").read_text(encoding="utf-8"))
    raw = (REPO_ROOT / "distro" / "workspace-template" / ".mcp.json").read_text(encoding="utf-8")
    mcp = json.loads(re.sub(r"^\s*//.*$", "", raw, flags=re.M))
    servers = mcp["mcpServers"]
    assert set(servers) == set(tm["servers"]), "服务清单不一致"
    for name, cfg in servers.items():
        assert sorted(cfg.get("autoApprove", [])) == sorted(tm["servers"][name]), name
