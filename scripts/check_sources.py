#!/usr/bin/env python3
"""校验数据源注册表与现实是否还对得上。

`_13` §3.1 的教训:**凡是"某某清单"必须有自动校验**,否则清单和代码各漂各的。
一天之内漂了四次 —— system prompt 的工具清单、SKILL 的 tools 字段、
finance-data 凭证解析、身份注入白名单,每一次都是"两个地方写了同一件事"。

`source_catalog.py` 是第三份这样的清单,所以它一出生就得配这个脚本。

四项检查:
  1. key 唯一 —— 重复 key 会让 source_health 的统计静默合并到一起
  2. **代码里记录的 key 都在注册表里** ← 最要紧的一条。少一个字母,观测数据
     就永远进不了任何一个源的窗口,而且不报错
  3. 字段自洽 —— available=False 必须给出 unavailable_reason(否则 UI 上
     只能显示"不可用"三个字,用户无从判断该不该去申请 key)
  4. 探活 —— available=True 且有 endpoint 的源,真打一次

用法:
    docker compose exec -T api python - < scripts/check_sources.py   # 1~3(离线)
    python scripts/check_sources.py --probe                          # 追加 4(要网络)

退出码非 0 = 有问题,可以直接进 CI。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):      # 中文 Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 容器内跑(/app)与仓库里跑(apps/api)都要能 import
for cand in ("/app", str(Path(__file__).resolve().parent.parent / "apps" / "api")):
    if cand not in sys.path and Path(cand).is_dir():
        sys.path.insert(0, cand)

from app.services import source_catalog as catalog     # noqa: E402

API_ROOT = Path(sys.path[0]) / "app"
errors: list[str] = []
warns: list[str] = []


def check_unique_keys() -> None:
    seen: dict[str, int] = {}
    for s in catalog.CATALOG:
        seen[s.key] = seen.get(s.key, 0) + 1
    for k, n in seen.items():
        if n > 1:
            errors.append(f"key 重复 {n} 次: {k} —— 健康统计会被静默合并")
    print(f"  [1/4] key 唯一性  {len(catalog.CATALOG)} 条, 重复 {sum(1 for n in seen.values() if n > 1)}")


def check_recorded_keys() -> None:
    """扫代码里所有写死的 source key,确认注册表里有。

    这是本脚本存在的主要理由:`source_health.record("a.qoute", ...)` 拼错一个字母
    不会报错、不会崩、什么都不会发生 —— 观测数据静静地流进一个谁也不会去看的
    key 里。跟今天修的其它静默失败是同一个形状。
    """
    known = {s.key for s in catalog.CATALOG}

    # ① 显式写在 record()/observe() 调用里的
    pat = re.compile(r'(?:source_health\.record|_sh\.record|_health|sh\.record|observe)'
                     r'\(\s*["\']([a-z_]+\.[a-z_]+)["\']')
    found: dict[str, set[str]] = {}
    for py in API_ROOT.rglob("*.py"):
        # 注册表自己里面 32 个 key 全是字面量,不排除的话扫描 ② 会把定义当成观测点,
        # 结果永远是"32/32 有观测点" —— 一个永远通过的检查等于没有检查
        if py.name == "source_catalog.py":
            continue
        try:
            txt = py.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in pat.finditer(txt):
            found.setdefault(m.group(1), set()).add(str(py.relative_to(API_ROOT)))
        # ② 作为参数传下去的(如 _fetch_chart(..., "hk.kline"))—— 正则盯调用名会漏,
        #    所以再扫一遍"字面量恰好等于某个已注册 key"的情况
        for k in known:
            if f'"{k}"' in txt or f"'{k}'" in txt:
                found.setdefault(k, set()).add(str(py.relative_to(API_ROOT)))

    # ③ 经路径映射表记录的(finance_data_client 那一张表)—— 这些 key 在代码里
    #    根本不以调用参数形式出现,只能从表本身取
    try:
        from app.services.finance_data_client import _SOURCE_BY_PATH
        for _, k in _SOURCE_BY_PATH:
            found.setdefault(k, set()).add("services/finance_data_client.py:_SOURCE_BY_PATH")
    except Exception as e:
        warns.append(f"读不到 _SOURCE_BY_PATH,第 3 类观测点没检查到: {e}")

    unknown = {k: v for k, v in found.items() if k not in known}
    for key, files in sorted(unknown.items()):
        errors.append(f"代码里记录了未注册的 key {key!r} —— 出现在 {', '.join(sorted(files))}")
    # 反向:注册了但没有任何观测点 —— 不算错(有些源本来就还没接线),但状态会永远
    # 停在 unknown,UI 上得说清楚是"没探过"而不是"坏了"
    never = sorted(k for k in known if k not in found)
    print(f"  [2/4] key 对得上  有观测点 {len(known) - len(never)}/{len(known)}, "
          f"未注册 {len(unknown)}")
    if never:
        warns.append(f"注册了但没有观测点(状态恒为 unknown,共 {len(never)} 个): "
                     + ", ".join(never))


def check_fields() -> None:
    bad = 0
    for s in catalog.CATALOG:
        if not s.available and not s.unavailable_reason:
            errors.append(f"{s.key} available=False 却没写 unavailable_reason —— "
                          f"UI 上只能显示'不可用',用户不知道该不该去申请 key")
            bad += 1
        if s.available and s.unavailable_reason:
            warns.append(f"{s.key} available=True 却带着 unavailable_reason,可能是改了一半")
        if s.requires_key and s.tier is catalog.SourceTier.FREE_STABLE:
            warns.append(f"{s.key} 标了免费稳定源却又 requires_key=True")
    print(f"  [3/4] 字段自洽    问题 {bad}")


def check_probe() -> None:
    """探活:available=True 且有 endpoint 的源真打一次。

    只在 --probe 时跑 —— 它会消耗上游配额,不该在每次 CI 里无脑执行。
    """
    import time
    import httpx
    from app.services import finance_data_auth as _auth

    targets = [s for s in catalog.CATALOG
               if s.available and s.endpoint and s.provider == "finance-data"]
    base = _auth.data_url()
    headers = _auth.data_headers()
    ok = fail = 0
    for s in targets:
        path = s.endpoint.replace("{symbol}", "600519.SH").replace("{code}", "600519")
        t0 = time.perf_counter()
        try:
            r = httpx.get(f"{base}{path}", headers=headers, timeout=15.0)
            good = r.status_code == 200
        except Exception as e:
            good, r = False, e
        ms = int((time.perf_counter() - t0) * 1000)
        if good:
            ok += 1
            print(f"        ✓ {s.key:18} {ms:>5}ms")
        else:
            fail += 1
            detail = getattr(r, "status_code", type(r).__name__)
            print(f"        ✗ {s.key:18} {ms:>5}ms  {detail}")
            warns.append(f"{s.key} 探活失败({detail}) —— 注册表说它可用")
    print(f"  [4/4] 探活        {ok} 通 / {fail} 不通")


def main() -> int:
    print("检查数据源注册表 (source_catalog.py)")
    check_unique_keys()
    check_recorded_keys()
    check_fields()
    if "--probe" in sys.argv:
        check_probe()
    else:
        print("  [4/4] 探活        跳过(加 --probe 启用 · 会消耗上游配额)")

    print()
    for w in warns:
        print(f"  ⚠ {w}")
    for e in errors:
        print(f"  ✗ {e}")
    if errors:
        print(f"\n不通过:{len(errors)} 个错误")
        return 1
    print(f"\n通过{'(' + str(len(warns)) + ' 条提醒)' if warns else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
