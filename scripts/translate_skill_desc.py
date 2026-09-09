#!/usr/bin/env python3
"""把**已经装好**的用户 SKILL 的英文说明补译成中文。

## 为什么需要它

能力库里那栏「说明」读的是 SKILL.md frontmatter 的 `description`,而第三方
SKILL 绝大多数是英文原文(实测 17 个存量里 14 个是英文)。用户要的是中文界面。

安装路径已经会自动翻译并写进 `hunter.description_zh` 了,但那**只对以后新装的
生效**。已经装在 `user-skills/` 里的存量得靠这个脚本补。

## 用法

代码不在镜像里(scripts/ 不参与 COPY),拷进容器跑:

    C=$(docker ps -qf name=community-api)
    docker cp scripts/translate_skill_desc.py $C:/tmp/
    docker exec $C python /tmp/translate_skill_desc.py --dry-run   # 先看要翻什么
    docker exec $C python /tmp/translate_skill_desc.py

## 边界

- **只往 `hunter:` 段写一行 `description_zh`,不动 `description` 原文**,
  也不动正文。这样文件丢给别的 Claude Code / opencode 仍然是标准格式,
  而且随时能对照原文。写入走 `skill_files.set_hunter_field`(最小文本插入)。
- **翻译失败保留英文**。说明栏空白等于零信息,比英文还糟 ——
  产品铁律"空的比假的好"针对的是编造的数字/指标,不适用于说明文字。
- 三种情况直接跳过,不花 token:
    · 说明本来就是中文
    · 说明只是个 slug(`morning-note` 这种,作者根本没写说明,翻了更奇怪)
    · 已经有 `description_zh`(除非 --force)
- 专业缩写与人名(ROIC / F-Score / SEC 10-K / Buffett)保留英文,
  数字和年限原样不动 —— 由 `translate_desc` 的 system prompt 约束。
"""
from __future__ import annotations

import argparse
import sys

from app.services import skill_files
from app.services.lang_guard import (
    contains_chinese,
    looks_like_slug,
    starts_with_chinese,
    translate_desc,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印要翻什么,不写文件")
    ap.add_argument("--force", action="store_true", help="已有 description_zh 也重翻")
    ap.add_argument("--only", default="", help="只处理这一个 skill(目录名)")
    args = ap.parse_args()

    root = skill_files.USER_SKILLS_DIR
    if not root.is_dir():
        print(f"用户 SKILL 目录不存在: {root}")
        return 1

    todo, skipped, done, failed = [], [], [], []
    existing_map: dict[str, str] = {}   # skill 名 -> 旧的 description_zh(用于删坏值)

    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        f = d / "SKILL.md"
        if not f.is_file():
            continue
        if args.only and d.name != args.only:
            continue
        try:
            fm, _body = skill_files._parse_frontmatter(f.read_text(encoding="utf-8"))
        except Exception as e:                # noqa: BLE001
            failed.append((d.name, f"解析失败: {e}"))
            continue

        desc = str(fm.get("description") or "").strip()
        h = fm.get("hunter") if isinstance(fm.get("hunter"), dict) else {}
        existing = str((h or {}).get("description_zh") or "").strip()

        # 已有译文但**开头不是中文** = 写坏了的值(2026-09-09 踩过:模型先吐一段
        # 英文内心戏再给译文,旧版校验只看"含不含中文"就放行了)。
        # 留着比没有更糟 —— 读取方会优先用它。所以当作没有,重翻;
        # 重翻仍失败的话下面会把这行删掉,回落英文原文。
        existing_map[d.name] = existing
        bad_existing = bool(existing) and not starts_with_chinese(existing)
        if bad_existing:
            print(f"  ⚠ {d.name} 已有译文是坏的(英文开头),将重翻")

        if not desc:
            skipped.append((d.name, "没有 description"))
            continue
        if existing and not bad_existing and not args.force:
            skipped.append((d.name, "已有中文说明"))
            continue
        if contains_chinese(desc):
            skipped.append((d.name, "说明本来就是中文"))
            continue
        if looks_like_slug(desc):
            # 作者没写说明,description 就等于 skill 名字。翻它只会得到奇怪的中文词。
            skipped.append((d.name, f"只是个名字,不是说明: {desc}"))
            continue

        todo.append((d, f, desc))

    print(f"扫描 {root} · 待翻 {len(todo)} · 跳过 {len(skipped)}")
    for n, why in skipped:
        print(f"  跳过 {n:28s} {why}")

    for d, f, desc in todo:
        print(f"\n--- {d.name}")
        print(f"  原文 {desc[:100]}")
        if args.dry_run:
            continue
        zh = translate_desc(desc)
        if zh == desc or not starts_with_chinese(zh):
            # 翻不出来就回落英文原文。如果原来存着一个写坏的值,
            # **必须删掉** —— 读取方会优先用它,留着就是把垃圾给用户看。
            if existing_map.get(d.name):
                skill_files.set_hunter_field(f, "description_zh", None)
                print("  ⚠ 翻译未生效 · 已删掉旧的坏值 · 回落英文原文")
            else:
                print("  ⚠ 翻译未生效,保留英文原文")
            failed.append((d.name, "翻译未生效 · 保留英文原文"))
            continue
        if skill_files.set_hunter_field(f, "description_zh", zh):
            done.append(d.name)
            print(f"  译文 {zh[:100]}")
        else:
            failed.append((d.name, "写入失败(frontmatter 解析不了)"))

    print(f"\n完成: 译好 {len(done)} · 失败 {len(failed)}")
    for n, why in failed:
        print(f"  失败 {n:28s} {why}")
    if args.dry_run:
        print("(--dry-run · 没有写任何文件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
