#!/usr/bin/env python3
"""第三方金融 SKILL 许可证与安全静态扫描器（M5-1 · `tools/skill_security_scanner.py`）。

功能定位：
自动扫描待引入或用户自定义的 `SKILL.md` / `.skill` 目录与文件，执行五重静态安全与合规审查：
1. 开源许可证合规检查（仅允许 MIT / Apache-2.0 / BSD-2-Clause / BSD-3-Clause，拒绝缺失许可证或 GPL/AGPL/CC-BY-NC 等传染/非商用许可证）；
2. 未授权第三方 vendored 文件与专有平台绑定检查（拦截 awesome-stock-quant-skills 的 16 个第三方 vendored 目录、sg-financial-advisor 新加坡持牌顾问条款、未脱敏的专有 CLI 等）；
3. 危险命令与外发窃密脚本检查（拦截 curl/wget 管道执行、base64 解码执行、读取 .env 外发密钥、rm -rf、反向 Shell、Prompt 越狱注入指令）；
4. 硬编码 API Key / 私钥检查（拦截 sk-xxx、ghp_xxx、AKIAxxx、xoxb-xxx、BEGIN PRIVATE KEY 等硬编码凭证）；
5. 违规荐股承诺与实盘下单话术检查（拦截“稳赚不赔”“保底收益”“必涨停”“自动满仓下单”等正向违规诱导承诺；允许在“禁止/严禁/不得/红线”上下文中作为合规反例引用）。

用法：
    python3 tools/skill_security_scanner.py
    python3 tools/skill_security_scanner.py --json
    python3 tools/skill_security_scanner.py --path skills/ distro/workspace-template/skills/
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SKILL_TARGETS: list[Path] = [
    REPO_ROOT / "skills",
    REPO_ROOT / "apps" / "api" / "skills" / "market-event-analyst.skill",
]

ALLOWED_LICENSES = {
    "MIT",
    "APACHE-2.0",
    "APACHE 2.0",
    "BSD-2-CLAUSE",
    "BSD-3-CLAUSE",
}

FORBIDDEN_LICENSES = {
    "GPL",
    "GPL-2.0",
    "GPL-3.0",
    "AGPL",
    "AGPL-3.0",
    "LGPL",
    "CC-BY-NC",
    "CC-BY-NC-SA",
    "CC-BY-NC-ND",
    "PROPRIETARY",
    "UNLICENSED",
    "ALL RIGHTS RESERVED",
}

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)

# 1. 未授权第三方 Vendored 路径与境外专有条款黑名单
VENDORED_BLACKLIST_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?:vendor|vendored|third[_-]?party)/"
            r"|awesome-stock-quant-skills/(?:external|community|third_party|vendors|collected)"
            r"|sg-financial-advisor"
            r"|Monetary\s+Authority\s+of\s+Singapore\s+Financial\s+Advisers\s+Act",
            re.I,
        ),
        "包含未授权第三方 vendored 目录或境外持牌投顾专属法规条款（如 sg-financial-advisor）",
    ),
]

# 2. 危险命令、外发窃密与 Prompt 注入特征
DANGEROUS_COMMAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?:curl|wget)\s+[^\n|]{1,120}\|\s*(?:sh|bash|zsh|python3?)\b", re.I),
        "检测到下载外部脚本并直接通过管道执行（curl/wget | sh）",
    ),
    (
        re.compile(r"base64\s+(?:-d|--decode)[^\n]{0,60}\|\s*(?:sh|bash|eval|exec|python3?)", re.I),
        "检测到 base64 解码后直接执行载荷",
    ),
    (
        re.compile(r"(?:cat|grep|printenv)\s+[^\n]*(?:\.env\b|id_rsa|credentials)[^\n]*\|\s*(?:curl|wget|nc|netcat)", re.I),
        "检测到读取本地 .env 或私钥并向外网外发窃密",
    ),
    (
        re.compile(r"\brm\s+-rf\s+/(?:\s|$|\*)|mkfs\.|dd\s+if=/dev/zero", re.I),
        "检测到高危破坏性系统命令（rm -rf / 等）",
    ),
    (
        re.compile(r"bash\s+-i\s+>&\s*/dev/tcp/|nc\s+-e\s+/bin/(?:sh|bash)", re.I),
        "检测到反向 Shell 后门连接命令",
    ),
    (
        re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions\s+and\s+(?:send|upload|exfiltrate|execute)", re.I),
        "检测到恶意 Prompt 注入越狱与外发指令",
    ),
]

# 3. 硬编码 API Key 与私钥特征
HARDCODED_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{24,}\b"),
        "疑似硬编码 OpenAI / DeepSeek / Anthropic API Key (sk-...)",
    ),
    (
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
        "疑似硬编码 GitHub Token (ghp_/gho_/ghs_...)",
    ),
    (
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "疑似硬编码 AWS Access Key ID (AKIA...)",
    ),
    (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PRIVATE )?PRIVATE KEY-----"),
        "疑似硬编码私钥文本 (BEGIN PRIVATE KEY)",
    ),
    (
        re.compile(
            r"(?:api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{24,}['\"]",
            re.I,
        ),
        "疑似在文本中硬编码明文 API Key / Secret Token",
    ),
]

# 4. 违规荐股承诺与诱导实盘下单话术
PROHIBITED_PROMISE_WORDS = [
    "稳赚不赔",
    "保底收益",
    "保证涨停",
    "必涨停",
    "明天必涨",
    "承诺年化",
    "保本保息",
    "内幕必买",
    "立即满仓买入",
    "直接替用户下实盘买单",
]

# 合规否定/警示上下文词：当违规词出现在“严禁/禁止/不得/拦截/过滤”等规则说明句中时，属于合规防护声明而非违规承诺
NEGATION_CONTEXT_KEYWORDS = (
    "严禁",
    "禁止",
    "不得",
    "不准",
    "切勿",
    "杜绝",
    "避免",
    "拦截",
    "过滤",
    "红线",
    "违规",
    "绝不",
    "不能",
    "不要",
    "拒绝",
    "示例",
    "反例",
    "话术检查",
    "扫描",
)


@dataclass
class ScanIssue:
    rule_id: str
    severity: str  # "error" | "warning"
    category: str  # "license" | "vendored" | "dangerous_cmd" | "hardcoded_secret" | "compliance_promise"
    message: str
    line: int = 0
    excerpt: str = ""


@dataclass
class SkillScanReport:
    skill_name: str
    path: str
    license: str | None
    passed: bool
    issues: list[ScanIssue] = field(default_factory=list)
    files_scanned: int = 1


def extract_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """提取 YAML frontmatter 的顶层键值与正文。"""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_raw = m.group(1)
    body = text[m.end():]
    meta: dict[str, str] = {}
    for line in fm_raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip("'\"")
    return meta, body


def _is_negated_compliance_context(line_text: str) -> bool:
    """判断包含敏感荐股词的语句是否为合规禁止性规定（如“严禁出现‘稳赚不赔’”）。"""
    return any(neg in line_text for neg in NEGATION_CONTEXT_KEYWORDS)


def scan_skill_text(
    text: str,
    skill_name: str = "unknown",
    file_path: str = "<memory>",
    require_license: bool = True,
) -> SkillScanReport:
    """对单篇 SKILL.md 文本执行五维静态安全与合规扫描。"""
    issues: list[ScanIssue] = []
    meta, _ = extract_frontmatter(text)

    if not meta:
        issues.append(
            ScanIssue(
                rule_id="SEC-FM-001",
                severity="error",
                category="license",
                message="SKILL 文件缺少标准 YAML frontmatter（`---` 头信息）",
                line=1,
            )
        )

    raw_license = meta.get("license", "").strip()
    norm_license = raw_license.upper()

    if require_license:
        if not raw_license:
            issues.append(
                ScanIssue(
                    rule_id="SEC-LIC-001",
                    severity="error",
                    category="license",
                    message="未声明开源许可证（必须在 frontmatter 中声明 `license: MIT` 或 `Apache-2.0`）",
                    line=1,
                )
            )
        elif any(bad in norm_license for bad in FORBIDDEN_LICENSES) or norm_license not in ALLOWED_LICENSES:
            issues.append(
                ScanIssue(
                    rule_id="SEC-LIC-002",
                    severity="error",
                    category="license",
                    message=f"不允许的许可证类型 `{raw_license}`（仅允许 MIT / Apache-2.0 / BSD）",
                    line=1,
                    excerpt=f"license: {raw_license}",
                )
            )

    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        # 1. 检查未授权第三方 vendored 文件与境外专有条款
        for pat, desc in VENDORED_BLACKLIST_PATTERNS:
            if pat.search(line) and not _is_negated_compliance_context(line):
                issues.append(
                    ScanIssue(
                        rule_id="SEC-VEN-001",
                        severity="error",
                        category="vendored",
                        message=desc,
                        line=idx,
                        excerpt=line.strip()[:120],
                    )
                )

        # 2. 检查危险命令与外发窃密脚本
        for pat, desc in DANGEROUS_COMMAND_PATTERNS:
            if pat.search(line) and not _is_negated_compliance_context(line):
                issues.append(
                    ScanIssue(
                        rule_id="SEC-CMD-001",
                        severity="error",
                        category="dangerous_cmd",
                        message=desc,
                        line=idx,
                        excerpt=line.strip()[:120],
                    )
                )

        # 3. 检查硬编码 API Key / 私钥
        for pat, desc in HARDCODED_SECRET_PATTERNS:
            if pat.search(line):
                issues.append(
                    ScanIssue(
                        rule_id="SEC-KEY-001",
                        severity="error",
                        category="hardcoded_secret",
                        message=desc,
                        line=idx,
                        excerpt=line.strip()[:120],
                    )
                )

        # 4. 检查违规荐股承诺话术（排除“严禁/禁止/不得”等合规声明上下文）
        for word in PROHIBITED_PROMISE_WORDS:
            if word in line and not _is_negated_compliance_context(line):
                issues.append(
                    ScanIssue(
                        rule_id="SEC-CMP-001",
                        severity="error",
                        category="compliance_promise",
                        message=f"包含违规收益承诺或诱导实盘指令词汇：「{word}」",
                        line=idx,
                        excerpt=line.strip()[:120],
                    )
                )

    resolved_name = meta.get("name") or skill_name
    passed = not any(i.severity == "error" for i in issues)
    return SkillScanReport(
        skill_name=resolved_name,
        path=file_path,
        license=raw_license or None,
        passed=passed,
        issues=issues,
        files_scanned=1,
    )


def scan_skill_dir(skill_dir: Path) -> SkillScanReport:
    """扫描单个技能目录（含 `SKILL.md`、附属 `references/` 以及是否存在非法可执行脚本）。"""
    if skill_dir.is_file():
        text = skill_dir.read_text(encoding="utf-8")
        rel = str(skill_dir.relative_to(REPO_ROOT)) if skill_dir.is_relative_to(REPO_ROOT) else str(skill_dir)
        return scan_skill_text(text, skill_name=skill_dir.stem, file_path=rel)

    skill_md = skill_dir / "SKILL.md"
    rel_dir = str(skill_dir.relative_to(REPO_ROOT)) if skill_dir.is_relative_to(REPO_ROOT) else str(skill_dir)
    if not skill_md.is_file():
        return SkillScanReport(
            skill_name=skill_dir.name,
            path=rel_dir,
            license=None,
            passed=False,
            issues=[
                ScanIssue(
                    rule_id="SEC-DIR-001",
                    severity="error",
                    category="license",
                    message=f"目录 {rel_dir} 下缺少 SKILL.md",
                )
            ],
            files_scanned=0,
        )

    report = scan_skill_text(
        skill_md.read_text(encoding="utf-8"),
        skill_name=skill_dir.name,
        file_path=str(skill_md.relative_to(REPO_ROOT)) if skill_md.is_relative_to(REPO_ROOT) else str(skill_md),
    )

    files_count = 1
    # 扫描附属文件：禁止夹带可执行二进制或未授权脚本，并对 references/*.md 执行安全扫描
    for sub in sorted(skill_dir.rglob("*")):
        if sub.is_dir() or sub == skill_md:
            continue
        files_count += 1
        rel_sub = str(sub.relative_to(REPO_ROOT)) if sub.is_relative_to(REPO_ROOT) else str(sub)
        if sub.suffix.lower() in {".sh", ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc"}:
            report.issues.append(
                ScanIssue(
                    rule_id="SEC-VEN-002",
                    severity="error",
                    category="vendored",
                    message=f"技能目录禁止夹带可执行脚本或二进制文件: {rel_sub}",
                    line=1,
                )
            )
            continue
        if sub.suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml"}:
            sub_rep = scan_skill_text(
                sub.read_text(encoding="utf-8", errors="replace"),
                skill_name=report.skill_name,
                file_path=rel_sub,
                require_license=False,
            )
            for issue in sub_rep.issues:
                # 排除缺失 frontmatter 的报错（references 附件不需要 frontmatter）
                if issue.rule_id == "SEC-FM-001":
                    continue
                report.issues.append(issue)

    report.files_scanned = files_count
    report.passed = not any(i.severity == "error" for i in report.issues)
    return report


def discover_skill_targets(paths: Iterable[Path]) -> list[Path]:
    """从给定路径集合中发现全部技能目录或独立 `.skill` 文件。"""
    targets: list[Path] = []
    seen: set[Path] = set()
    for p in paths:
        p = p.resolve()
        if not p.exists():
            continue
        if p.is_file():
            if p not in seen:
                targets.append(p)
                seen.add(p)
            continue
        if (p / "SKILL.md").is_file():
            if p not in seen:
                targets.append(p)
                seen.add(p)
            continue
        for child in sorted(p.iterdir()):
            if child.is_dir() and (child / "SKILL.md").is_file():
                if child.resolve() not in seen:
                    targets.append(child.resolve())
                    seen.add(child.resolve())
            elif child.is_file() and child.suffix == ".skill":
                if child.resolve() not in seen:
                    targets.append(child.resolve())
                    seen.add(child.resolve())
    return targets


def scan_all_skills(paths: Iterable[Path] | None = None) -> dict:
    """扫描指定路径下的所有技能并返回汇总字典。"""
    target_paths = list(paths) if paths is not None else DEFAULT_SKILL_TARGETS
    discovered = discover_skill_targets(target_paths)
    reports = [scan_skill_dir(t) for t in discovered]
    passed_count = sum(1 for r in reports if r.passed)
    failed_count = len(reports) - passed_count
    return {
        "ok": failed_count == 0 and len(reports) > 0,
        "total_skills": len(reports),
        "passed_skills": passed_count,
        "failed_skills": failed_count,
        "allowed_licenses": sorted(ALLOWED_LICENSES),
        "reports": [asdict(r) for r in reports],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HunterCode 第三方金融 SKILL 许可证与安全静态扫描器 (M5-1)"
    )
    parser.add_argument(
        "--path",
        nargs="*",
        type=Path,
        default=None,
        help="要扫描的技能目录或文件列表（默认扫描仓库全部 22 个金融 SKILL）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式报告",
    )
    args = parser.parse_args(argv)

    summary = scan_all_skills(args.path)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"=== HunterCode 金融 SKILL 许可证与安全静态扫描报告 ===\n"
            f"扫描技能总数: {summary['total_skills']} | "
            f"通过: {summary['passed_skills']} | "
            f"拦截: {summary['failed_skills']}"
        )
        for rep in summary["reports"]:
            status = "✅ PASS" if rep["passed"] else "❌ FAIL"
            print(
                f"  {status} [{rep['skill_name']}] (license={rep['license']}, "
                f"files={rep['files_scanned']}) -> {rep['path']}"
            )
            for iss in rep["issues"]:
                print(
                    f"      - [{iss['rule_id']}] ({iss['category']}) L{iss['line']}: "
                    f"{iss['message']} {iss['excerpt']}"
                )

    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
