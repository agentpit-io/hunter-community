"""skill_loader 单测 · 内置 5 个 skill 是否能正确加载"""
import os
from pathlib import Path
import pytest


@pytest.fixture
def project_root_skills(monkeypatch):
    """把 HUNTER_SKILL_ROOT 指向仓库真实 .hunter/skills 目录"""
    root = Path(__file__).resolve().parents[3] / ".hunter" / "skills"
    monkeypatch.setenv("HUNTER_SKILL_ROOT", str(root))
    from app.services.agent import skill_loader
    skill_loader._SKILLS.clear()
    yield root


def test_parse_frontmatter_basic():
    from app.services.agent.skill_loader import parse_frontmatter
    fm, body = parse_frontmatter("---\nname: x\ndescription: y\n---\n# body\nhi")
    assert fm["name"] == "x"
    assert fm["description"] == "y"
    assert body.startswith("# body")


def test_parse_frontmatter_no_fm_returns_empty():
    from app.services.agent.skill_loader import parse_frontmatter
    fm, body = parse_frontmatter("just body")
    assert fm == {}
    assert body == "just body"


def test_load_all_skills_finds_five_builtin(project_root_skills):
    from app.services.agent.skill_loader import load_all_skills
    skills = load_all_skills()
    names = set(skills.keys())
    expected = {"ah-arbitrage-check", "earnings-expectation-diff",
                "kill-conditions-review", "technical-pattern-scan",
                "sector-rotation-view"}
    assert expected.issubset(names)


def test_skills_manifest_non_empty(project_root_skills):
    from app.services.agent.skill_loader import load_all_skills, skills_manifest
    load_all_skills()
    m = skills_manifest()
    assert "ah-arbitrage-check" in m
    # description 里可能是"A/H"或"AH"，只要 skill 名称在就说明 manifest 正确注入
    assert "溢价" in m or "套利" in m


def test_get_skill_returns_body(project_root_skills):
    from app.services.agent.skill_loader import load_all_skills, get_skill
    load_all_skills()
    s = get_skill("technical-pattern-scan")
    assert s is not None
    assert "技术面" in s.body or "均线" in s.body
