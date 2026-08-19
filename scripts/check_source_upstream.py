#!/usr/bin/env python3
"""校验每条数据源的 `upstream`(真实上游)是否都核实过了。

`_21` §1.2 / §2 的配套校验。为什么单独一个脚本而不是塞进 `check_sources.py`:
那个脚本查的是"注册表与代码对不对得上",这个查的是"注册表与**上游现实**
对不对得上" —— 后者的事实来源在**另一个仓库**(finance-data 的 collector),
混在一起会让失败信息指不明白该去哪个仓库查。

核心约束(这是整件事的意义所在):

    **upstream 为空 = 报错,而不是显示"未知"。**

因为按来源分组之后,upstream 直接决定这条源出现在 UI 的哪一组。留空的话
它会掉进"未核实来源"那一组 —— 看起来像个正常分组,实际是我们没查。
用户看不出区别,于是一个没人核实过的来源就这样混进了产品。
宁可 CI 红,也不要一个猜出来的上游。

同样的道理,**每条 upstream 都必须带行内证据注释**。没有证据的值
和猜的值在代码里长得一模一样,半年后没人分得清哪个是查过的。

用法:
    docker compose exec -T api python - < scripts/check_source_upstream.py
    python scripts/check_source_upstream.py

退出码非 0 = 有问题,可直接进 CI。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):      # 中文 Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

for cand in ("/app", str(Path(__file__).resolve().parent.parent / "apps" / "api")):
    if cand not in sys.path and Path(cand).is_dir():
        sys.path.insert(0, cand)

from app.services import source_catalog as catalog     # noqa: E402

errors: list[str] = []
warns: list[str] = []


def check_every_source_has_upstream() -> None:
    """最要紧的一条:不许留空。"""
    for s in catalog.CATALOG:
        if not s.upstream:
            errors.append(
                f"{s.key}: upstream 为空。"
                f"去 finance-data 仓库查这条数据实际由哪个 collector 写入、"
                f"那个 collector 打的是谁 —— 查不出来就先别加这条源,"
                f"不要填 'unknown' 蒙混过去"
            )


def check_upstream_is_known() -> None:
    """upstream 必须在 UPSTREAM_LABEL 里 —— 否则 UI 上显示的是裸 slug。"""
    for s in catalog.CATALOG:
        if s.upstream and s.upstream not in catalog.UPSTREAM_LABEL:
            errors.append(
                f"{s.key}: upstream={s.upstream!r} 不在 UPSTREAM_LABEL 里。"
                f"要么拼错了,要么这是个新来源 —— 新来源请同时补 "
                f"UPSTREAM_LABEL(中文名)和 UPSTREAM_ORDER(排序)"
            )


def check_order_covers_all() -> None:
    """UPSTREAM_ORDER 漏掉的来源会排到末尾。不算错,但要提醒。"""
    used = {s.upstream for s in catalog.CATALOG if s.upstream}
    missing = used - set(catalog.UPSTREAM_ORDER)
    if missing:
        warns.append(
            f"UPSTREAM_ORDER 没收录 {sorted(missing)} —— "
            f"它们会排在侧栏最末尾。想调位置就补进 UPSTREAM_ORDER"
        )
    # 只给用户接自己的源用的来源 —— 它们**本来就不会有官方源在用**,
    # 不该报 stale。`_24` §8.2② 新增了五个(腾讯/新浪/Finnhub/Polygon/
    # AlphaVantage),加上原来的 cls/tushare。
    #
    # 不加白名单的话每次跑校验都会看到一条"已经没有任何源在用"的告警 ——
    # 而告警一旦变成常态就没人看了,真出问题时也会被一起忽略。
    USER_ONLY = {"cls", "tushare", "tencent", "sina",
                 "finnhub", "polygon", "alphavantage", "custom"}
    stale = set(catalog.UPSTREAM_ORDER) - used - USER_ONLY
    if stale:
        warns.append(
            f"UPSTREAM_ORDER 里 {sorted(stale)} 已经没有任何源在用 —— "
            f"是删源时忘了清,还是准备接的新来源?"
        )


def check_has_evidence_comment() -> None:
    """每条 upstream= 后面必须跟证据注释。

    这条检查的是**源码文本**而不是运行时对象,因为注释在运行时不存在。
    形式约定:`upstream="xtick",  # main.py:164 ...`
    """
    src_file = Path(catalog.__file__)
    text = src_file.read_text(encoding="utf-8")
    # 匹配所有 upstream="..." 后面到行尾的内容
    bare = []
    for m in re.finditer(r'upstream="([a-z_]+)",(.*)$', text, re.M):
        up, rest = m.group(1), m.group(2).strip()
        if not rest.startswith("#") or len(rest) < 6:
            line = text[: m.start()].count("\n") + 1
            bare.append(f"{src_file.name}:{line} upstream={up!r}")
    if bare:
        errors.append(
            "以下 upstream 没有证据注释(格式:`upstream=\"x\",  # 证据`):\n    "
            + "\n    ".join(bare)
            + "\n  没有证据的值和猜的值长得一模一样,半年后没人分得清"
        )


def check_user_group_placeholder() -> None:
    """`grouped_by_upstream` 必须永远给出「你自己的」组,哪怕是空的。

    空组就是添加入口 —— 没有它,用户在这个页面上找不到任何
    "我可以加自己的" 的迹象,而那正是这次改造的主题。
    """
    groups = catalog.grouped_by_upstream(user_id="__check__")
    if not groups or groups[0].get("upstream") != "user":
        errors.append(
            "grouped_by_upstream() 的第一组不是「你自己的」。"
            "用户自配的源必须排在我们的前面 —— 排后面等于在 UI 上"
            "否认『用户可以脱离我们』这件事"
        )


def main() -> int:
    check_every_source_has_upstream()
    check_upstream_is_known()
    check_order_covers_all()
    check_has_evidence_comment()
    check_user_group_placeholder()

    total = len(catalog.CATALOG)
    filled = sum(1 for s in catalog.CATALOG if s.upstream)
    print(f"数据源 {total} 条 · 已核实上游 {filled} 条")

    # 注册表按上游的分布 —— 肉眼看分组是否合理。
    #
    # ⚠️ **直接读 CATALOG,不走 `grouped_by_upstream()`。**
    # `_24` §3.1 把官方货架撤了,那个函数现在只返回用户自己的源
    #(校验时没有用户,于是永远是一个空组)。继续走它的结果是:
    # 这段输出永远空白,而校验照样打印"✅ 全部通过" ——
    # 一个看起来在工作、实际什么都没检查的脚本,比没有更糟。
    from collections import Counter
    by_up = Counter(x.upstream or "(空)" for x in catalog.CATALOG)
    print("")
    print("注册表的上游分布(不是 UI 分组 —— UI 已撤架):")
    for up, n in by_up.most_common():
        label = catalog.UPSTREAM_LABEL.get(up, up)
        mk = "/".join(sorted({x.market.value for x in catalog.CATALOG
                              if (x.upstream or "(空)") == up}))
        print(f"  {label:<14} {n:<3} [{mk}]")

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
