"""skill_loader 单测 · SKILL.md 能否正确加载、manifest 与 get_skill 是否可用

原来的三条加载用例把 HUNTER_SKILL_ROOT 指向 `.hunter/skills`,期望 SaaS 版的 5 个 SKILL
(ah-arbitrage-check 等)。这个目录在开源仓库里从来没有过(7fc2028 迁移时就没带过来),三条一直是红的。现在:
  - 加载 / manifest / get_skill 的行为用 tmp_path 现造的 skills 目录测,不依赖仓库里有哪些 SKILL;
  - 另一条加载真实的内置 SKILL,逐个对照 frontmatter 的 name,**不写死名单** —— 增删内置 SKILL 不用改这里。
    仓库里读根目录的 `skills/`;在 api 容器里跑(/app 是 apps/api)读 `$HUNTER_SKILLS_DIR`(/opt/hunter-skills);
    都找不到就 skip。
"""
import os
import re
from pathlib import Path

import pytest
import yaml

from app.services.agent import skill_loader


@pytest.fixture(autouse=True)
def _isolate_skill_cache():
    """_SKILLS 是模块级缓存:每条用例前清空,结束后还原,不串到别的用例"""
    saved = dict(skill_loader._SKILLS)
    skill_loader._SKILLS.clear()
    yield
    skill_loader._SKILLS.clear()
    skill_loader._SKILLS.update(saved)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def tmp_skill_root(tmp_path, monkeypatch):
    """现造一个确定性的 skills 目录,HUNTER_SKILL_ROOT 指过去(设了它就只搜这一个目录)"""
    root = tmp_path / "skills"
    # 写了 name → key 用它(不是目录名);带 hunter: 嵌套段(真实 SKILL 都有),不能影响 name / description
    _write(root / "alpha" / "SKILL.md",
           "---\nname: alpha-scan\ndescription: 技术面扫描 · 均线与形态\nhunter:\n"
           "  display_name: 形态扫描\n  needs_tools:\n    - uzi_stock_deep_analysis\n---\n\n"
           "# Alpha\n\n先看均线排列,再看形态。\n")
    # 没写 name → 用目录名;description 带引号 → 去掉引号
    _write(root / "beta" / "SKILL.md",
           '---\ndescription: "没写 name 时用目录名"\nversion: 1.0.0\n---\n# Beta\n正文\n')
    # 没有 frontmatter → 目录名当 key、描述为空、整篇都是正文
    _write(root / "gamma" / "SKILL.md", "# Gamma\n\n没有 frontmatter,整篇都是正文。\n")
    # 不该被加载的:不叫 SKILL.md、直接放在根目录、多嵌了一层
    _write(root / "alpha" / "README.md", "---\nname: readme\ndescription: x\n---\n")
    _write(root / "SKILL.md", "---\nname: root-level\ndescription: x\n---\n")
    _write(root / "nested" / "deep" / "SKILL.md", "---\nname: too-deep\ndescription: x\n---\n")
    monkeypatch.setenv("HUNTER_SKILL_ROOT", str(root))
    return root


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


def test_load_all_skills_reads_each_skill_dir(tmp_skill_root):
    skills = skill_loader.load_all_skills()
    # 只认 <root>/<目录>/SKILL.md 这一层
    assert set(skills) == {"alpha-scan", "beta", "gamma"}

    a = skills["alpha-scan"]
    assert a.description == "技术面扫描 · 均线与形态"
    assert a.meta["name"] == "alpha-scan"
    assert Path(a.path).resolve() == (tmp_skill_root / "alpha" / "SKILL.md").resolve()
    assert a.body.lstrip().startswith("# Alpha")
    assert "description:" not in a.body          # 正文不带 frontmatter

    b = skills["beta"]
    assert b.name == "beta"
    assert b.description == "没写 name 时用目录名"

    g = skills["gamma"]
    assert g.description == ""
    assert g.meta == {}
    assert g.body == "# Gamma\n\n没有 frontmatter,整篇都是正文。\n"


def test_skills_manifest_lists_name_and_description(tmp_skill_root):
    # 还没加载 → 空串(orchestrator 据此不往 system prompt 里注入清单)
    assert skill_loader.skills_manifest() == ""
    skill_loader.load_all_skills()
    lines = skill_loader.skills_manifest().splitlines()
    for name, desc in [("alpha-scan", "技术面扫描 · 均线与形态"), ("beta", "没写 name 时用目录名")]:
        line = next((ln for ln in lines if f"**{name}**" in ln), None)
        assert line is not None, f"manifest 里没有 {name}"
        assert desc in line, f"{name} 那一行没带描述:{line}"
    assert any("**gamma**" in ln for ln in lines)


def test_get_skill_returns_body(tmp_skill_root):
    skill_loader.load_all_skills()
    s = skill_loader.get_skill("alpha-scan")
    assert s is not None
    assert "均线" in s.body
    assert skill_loader.get_skill("alpha") is None        # frontmatter 写了 name,就不认目录名
    assert skill_loader.get_skill("not-exist") is None


def _builtin_skills_dir():
    """内置 SKILL 目录:仓库里是根目录的 skills/;api 容器里 /app 是 apps/api,内置 SKILL 挂在
    $HUNTER_SKILLS_DIR(docker-compose.yml 设为 /opt/hunter-skills)。都没有就返回 None"""
    api_dir = Path(__file__).resolve().parents[2]          # apps/api(容器里是 /app)
    cands = []
    if api_dir.parent.name == "apps":
        cands.append(api_dir.parent.parent / "skills")
    if os.getenv("HUNTER_SKILLS_DIR"):
        cands.append(Path(os.environ["HUNTER_SKILLS_DIR"]))
    cands.append(Path("/opt/hunter-skills"))
    for c in cands:
        if c.is_dir() and any(c.glob("*/SKILL.md")):
            return c
    return None


def _frontmatter_name(p: Path):
    """用 YAML 读 frontmatter 顶层的 name(与 skill_loader 的简易解析互为对照);没有就返回 None"""
    m = re.match(r"---\s*\n(.*?)\n---\s*\n", p.read_text(encoding="utf-8"), re.DOTALL)
    if not m:
        return None
    fm = yaml.safe_load(m.group(1)) or {}
    assert isinstance(fm, dict), f"{p} 的 frontmatter 不是 YAML 映射"
    name = fm.get("name")
    return str(name) if name else None


def test_builtin_skills_all_loaded(monkeypatch):
    root = _builtin_skills_dir()
    if root is None:
        pytest.skip("没找到内置 SKILL 目录(仓库根的 skills/、$HUNTER_SKILLS_DIR、/opt/hunter-skills 都没有 */SKILL.md)")
    files = sorted(p.resolve() for p in root.glob("*/SKILL.md"))
    monkeypatch.setenv("HUNTER_SKILL_ROOT", str(root))
    skills = skill_loader.load_all_skills()

    # 每个 SKILL.md 都加载了,且各占一个 key(重名的话后来的会被丢掉,这里就对不上)
    assert sorted(Path(s.path).resolve() for s in skills.values()) == files

    manifest_lines = skill_loader.skills_manifest().splitlines()
    for p in files:
        key = _frontmatter_name(p) or p.parent.name
        s = skill_loader.get_skill(key)
        assert s is not None, f"{p} 应当以 {key!r} 为 key 加载,实际 key:{sorted(skills)}"
        assert Path(s.path).resolve() == p
        assert s.description, f"{p} 的 description 为空"
        assert s.body.strip(), f"{p} 的正文为空"
        line = next((ln for ln in manifest_lines if f"**{key}**" in ln), "")
        assert s.description in line, f"manifest 里 {key} 那一行没带描述:{line!r}"
