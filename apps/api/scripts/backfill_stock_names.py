"""补股票名 —— 免费,一次搞定。

`stocks` 表里 301 只有 271 只的 `name` 就是代码本身:seed 的时候拿代码
填了名字字段。于是策略工作台右边的「今日 Top」显示两遍代码
(002384 / 002384 · A股),用户根本认不出是哪家公司。

腾讯行情接口一次可查多只、免 key,返回里就带名字。注意它是 **GBK**,
按 UTF-8 解会得到乱码 —— 而乱码会**照样写进库**,比报错更难发现。

用法:
  docker compose exec -T api python3 /app/scripts/backfill_stock_names.py
"""
import sys
import time
import urllib.request

sys.path.insert(0, "/app")

from app.services.database import get_conn                    # noqa: E402

_UA = {"User-Agent": "Mozilla/5.0"}
_BATCH = 50


def _prefixed(code: str) -> str:
    c = str(code).zfill(6)
    return ("sh" if c[0] in "69" else "sz") + c


def fetch_names(codes: list[str]) -> dict[str, str]:
    q = ",".join(_prefixed(c) for c in codes)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    raw = op.open(urllib.request.Request(f"http://qt.gtimg.cn/q={q}", headers=_UA),
                  timeout=25).read().decode("gbk", "replace")
    out = {}
    for line in raw.strip().split(";"):
        if '="' not in line:
            continue
        parts = line.split('="')[1].split("~")
        if len(parts) < 3:
            continue
        name, code = parts[1].strip(), parts[2].strip()
        # 名字里出现替换字符说明解码错了 —— **宁可不写,也不要把乱码存进库**
        if not name or not code or "�" in name:
            continue
        out[code] = name
    return out


def main() -> int:
    # 从**有因子数据的票**取,而不是只看 stocks 表已有的行。
    #
    # 踩过的坑:中证500 那 500 只进了 index_component / klines / factor_value,
    # 但**从来没进过 stocks 表** —— 而查名字是查 stocks 的。
    # 于是界面上它们显示两遍代码,而这个脚本报"没有需要补名字的股票"。
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""SELECT DISTINCT f.code FROM factor_value f
                    LEFT JOIN stocks s ON s.code = f.code AND s.market='A'
                    WHERE s.code IS NULL OR s.name IS NULL OR s.name='' OR s.name=f.code""")
    todo = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    if not todo:
        print("没有需要补名字的股票")
        return 0
    print(f"{len(todo)} 只需要补名字")

    fixed = 0
    for i in range(0, len(todo), _BATCH):
        batch = todo[i:i + _BATCH]
        try:
            names = fetch_names(batch)
        except Exception as e:                                # noqa: BLE001
            print(f"  [{i}] 取名字失败: {type(e).__name__} {str(e)[:60]}")
            continue
        conn = get_conn(); cur = conn.cursor()
        try:
            for code, name in names.items():
                # 不在 stocks 里的直接插入 —— 光 UPDATE 的话中证500 那批
                # 永远补不上(它们压根没有这一行)
                cur.execute(
                    """INSERT INTO stocks (code, name, market, exchange, asset_type, enabled, user_id)
                       VALUES (%s, %s, 'A', %s, 'stock', TRUE, '')
                       ON CONFLICT (code, user_id) DO UPDATE SET name = EXCLUDED.name""",
                    (code, name, "SH" if code[0] in "69" else "SZ"))
                fixed += cur.rowcount
            conn.commit()
        finally:
            cur.close(); conn.close()
        print(f"  [{min(i+_BATCH, len(todo))}/{len(todo)}] 已补 {fixed}")
        time.sleep(0.3)

    print(f"\n完成 · 补上 {fixed} 只")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
