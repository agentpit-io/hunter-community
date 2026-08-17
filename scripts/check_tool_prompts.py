#!/usr/bin/env python3
"""校验工具的入口模板(`prompt_tpl`)—— `_22` 步 1 的配套。

**这个脚本盯的是一件很容易悄悄烂掉的事:**

`prompt_tpl` 为空的工具不出现在能力列表里。这个设计本身是对的
(编一句烂模板比留空更糟:用户点了得到答非所问的结果,然后再也不点
这一类了)。但它有个副作用 —— **留空是静默的**。新加一个工具忘了写模板,
表现是"这个功能上线了但没人找得到",而且没有任何报错。

在 `_22` 之前,13 个工具里有 5 个就是这么消失的,包括老板自己要的
「一句话加自选股」。这个脚本让那种消失变成一条可见的提醒。

检查项:
  1. 模板里的占位符必须是 `{中文}` 形式 —— 与 SKILL 的惯例一致,
     用户一眼看出该替换什么。写成 `{code}` 或 `{0}` 都不行
  2. 模板不能太短 —— "查行情" 这种缺主语的句子填进输入框,
     模型不知道查谁的行情
  3. internal_only 的工具**不该**写模板(写了说明分类想错了)
  4. 报告还有几个工具没有模板(warn,不是 error —— 允许暂时留空)

用法:
    docker compose exec -T api python - < scripts/check_tool_prompts.py

退出码非 0 = 有问题,可直接进 CI。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

for cand in ("/app", str(Path(__file__).resolve().parent.parent / "apps" / "api")):
    if cand not in sys.path and Path(cand).is_dir():
        sys.path.insert(0, cand)

from app.services import tool_catalog as tc      # noqa: E402

errors: list[str] = []
warns: list[str] = []

# SKILL 的 prompt_tpl 用的就是 `{股票}` 这种中文占位。两边一致,
# 因为合并入口之后它们在同一个列表里,占位符风格不同会很刺眼
_PLACEHOLDER = re.compile(r"\{([^}]*)\}")
_CJK = re.compile(r"[一-鿿]")


def check_placeholder_style() -> None:
    for t in tc.CATALOG:
        for ph in _PLACEHOLDER.findall(t.prompt_tpl or ""):
            if not _CJK.search(ph):
                errors.append(
                    f"{t.key}: 占位符 {{{ph}}} 不是中文 —— "
                    f"用户看到 {{code}} 不知道该填什么,写成 {{股票}} 才明确"
                )


def check_not_too_short() -> None:
    """太短的模板缺主语,填进输入框模型不知道对谁做。"""
    for t in tc.CATALOG:
        tpl = (t.prompt_tpl or "").strip()
        if tpl and len(tpl) < 6:
            errors.append(
                f"{t.key}: 模板 {tpl!r} 太短 —— 填进输入框后模型"
                f"多半不知道要对谁做这件事"
            )


def check_internal_has_no_tpl() -> None:
    for t in tc.CATALOG:
        if t.internal_only and t.prompt_tpl:
            errors.append(
                f"{t.key}: 标了 internal_only 却又写了模板。"
                f"二选一 —— 要么它是给人点的(去掉 internal_only),"
                f"要么是给模型用的(去掉模板)"
            )


def check_coverage() -> None:
    missing = [t.key for t in tc.CATALOG
               if not t.prompt_tpl and not t.internal_only]
    if missing:
        warns.append(
            f"{len(missing)} 个工具还没有入口模板,用户在能力列表里点不到:\n    "
            + "\n    ".join(missing)
            + "\n  留空是允许的(没想好用户会怎么说),但别让它变成长期状态"
        )


def main() -> int:
    check_placeholder_style()
    check_not_too_short()
    check_internal_has_no_tpl()
    check_coverage()

    total = len(tc.CATALOG)
    pick = tc.pickable()
    internal = [t for t in tc.CATALOG if t.internal_only]
    print(f"工具 {total} 个 · 能力列表可见 {len(pick)} · "
          f"仅模型可用 {len(internal)} · 缺模板 "
          f"{total - len(pick) - len(internal)}")

    print("\n能力列表里会出现的:")
    for t in pick:
        print(f"  {t.name:<14} {t.prompt_tpl}")
    if internal:
        print("\n仅模型可用(不进列表):")
        for t in internal:
            print(f"  {t.name:<14} {t.summary[:40]}")

    for w in warns:
        print(f"\n⚠️  {w}")
    for e in errors:
        print(f"\n❌ {e}")

    if errors:
        print(f"\n失败 · {len(errors)} 个问题")
        return 1
    print("\n✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
