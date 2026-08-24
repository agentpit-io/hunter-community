"""行业分类 seed · 落 stock_industry。

方案见 doc/开源hunter-community/01详细工作目录/11量化策略/
      22_20260822_数据中心_技术方案.md §3.2

## 为什么用新浪源而不是东财

试过三个:

    ak.stock_board_industry_name_em()        东财 · ConnectionError(连不通)
    ak.stock_board_industry_cons_ths()       同花顺 · **这个函数不存在**
    ak.stock_sector_spot() + stock_sector_detail()   新浪 · ✅ 49 板块 / 3024 家

东财那个接口和之前指数日线是同一个毛病(容器里连不通)。同花顺有板块
汇总但没有成分股接口。所以走新浪。

## 一级怎么归并

新浪给的是 49 个二级板块(玻璃行业 / 电子器件 / 船舶制造…)。
按方案归并成 7 个一级:科技 / 医药 / 消费 / 新能源 / 金融 / 制造 / 资源。

**映射表对着实测的板块名写,不是想象的名字** —— 写错的后果是那个板块
一只票都归不进去,而界面上只会看到"这个一级下面没有二级",
排查要翻到上游返回值。没匹配上的统一进「其他」并 log 出来,
下次上游加了新板块能立刻发现。
"""
from __future__ import annotations

import logging
import time
import warnings

from app.services.database import get_conn

log = logging.getLogger(__name__)

# 新浪 49 个板块 → 我们的一级。板块名是 stock_sector_spot() 实测 dump 的。
L1_OF: dict[str, str] = {
    # ── 科技 ──
    "电子信息": "科技", "电子器件": "科技", "IT行业": "科技",
    "通信行业": "科技", "仪器仪表": "科技",
    # ── 医药 ──
    "生物制药": "医药", "医疗行业": "医药", "医疗器械": "医药",
    # ── 消费 ──
    "食品行业": "消费", "酿酒行业": "消费", "商业百货": "消费",
    "纺织行业": "消费", "服装鞋类": "消费", "家具行业": "消费",
    "旅游酒店": "消费", "传媒娱乐": "消费", "农林牧渔": "消费",
    "文教休闲": "消费", "日用化工": "消费",
    "家电行业": "消费", "酒店旅游": "消费", "印刷包装": "消费",
    # ── 新能源 ──
    "电器行业": "新能源", "发电设备": "新能源", "环保行业": "新能源",
    "电力行业": "新能源",
    # ── 金融 ──
    "金融行业": "金融", "房地产": "金融",
    # ── 制造 ──
    "机械行业": "制造", "汽车制造": "制造", "船舶制造": "制造",
    "飞机制造": "制造", "纺织机械": "制造", "工程建筑": "制造",
    "交通运输": "制造", "供水供气": "制造", "综合行业": "制造",
    "公路桥梁": "制造", "摩托车": "制造",
    # ── 资源 ──
    "钢铁行业": "资源", "有色金属": "资源", "煤炭采选": "资源",
    "煤炭行业": "资源", "石油行业": "资源", "化工行业": "资源",
    "化纤行业": "资源", "农药化肥": "资源", "水泥行业": "资源",
    "玻璃行业": "资源", "建筑建材": "资源", "塑料制品": "资源",
    "陶瓷行业": "资源", "造纸行业": "资源", "开发区": "资源",
    "物资外贸": "资源",
    # ── 其他 ──
    # 这两个不是行业。**显式列出来**是为了让 unmapped 日志保持干净:
    # 不列的话每次 seed 都会报同样两个名字,真有新板块出现时反而被淹掉。
    "次新股": "其他", "其它行业": "其他",
}


def _boards():
    warnings.filterwarnings("ignore")
    import akshare as ak
    import concurrent.futures as cf
    ex = cf.ThreadPoolExecutor(max_workers=1)
    try:
        df = ex.submit(ak.stock_sector_spot).result(timeout=60)
        ex.shutdown(wait=False)
    except Exception as e:                                    # noqa: BLE001
        log.error("[industry] 拉板块列表失败: %s", type(e).__name__)
        return []
    return [(str(r["label"]), str(r["板块"])) for _, r in df.iterrows()]


def _cons(label: str) -> list[str]:
    warnings.filterwarnings("ignore")
    import akshare as ak
    import concurrent.futures as cf
    ex = cf.ThreadPoolExecutor(max_workers=1)
    try:
        df = ex.submit(lambda: ak.stock_sector_detail(sector=label)).result(timeout=60)
        ex.shutdown(wait=False)
    except Exception as e:                                    # noqa: BLE001
        log.warning("[industry] 拉 %s 成分失败: %s", label, type(e).__name__)
        return []
    col = "code" if "code" in df.columns else df.columns[1]
    out = []
    for x in df[col].tolist():
        c = str(x).strip()
        # 有的源给 sh600000,有的给 600000 —— 统一成 6 位
        c = c[-6:] if len(c) > 6 else c.zfill(6)
        if c.isdigit():
            out.append(c)
    return out


def seed() -> dict:
    """全量 seed。**一只票只归一个行业**(主键是 code)——
    新浪的板块之间有重叠,后来的会覆盖前面的,这是可接受的:
    行业只用来给用户批量选股票,不参与因子计算。"""
    boards = _boards()
    if not boards:
        return {"error": "拉不到板块列表"}

    unknown, total = [], 0
    conn = get_conn(); cur = conn.cursor()
    try:
        for label, name in boards:
            l1 = L1_OF.get(name)
            if not l1:
                # 上游加了新板块 —— 归到「其他」但要 log,不然默默丢掉
                unknown.append(name)
                l1 = "其他"
            codes = _cons(label)
            for c in codes:
                cur.execute(
                    """INSERT INTO stock_industry (code, l1, l2)
                       VALUES (%s,%s,%s)
                       ON CONFLICT (code) DO UPDATE
                         SET l1=EXCLUDED.l1, l2=EXCLUDED.l2, updated_at=now()""",
                    (c, l1, name))
                total += cur.rowcount
            conn.commit()
            log.info("[industry] %s(%s)· %d 只", name, l1, len(codes))
            time.sleep(0.2)          # 对上游客气一点
    finally:
        cur.close(); conn.close()

    if unknown:
        log.warning("[industry] 这些板块没有一级归类(暂归「其他」): %s", unknown)
    return {"boards": len(boards), "rows": total, "unmapped": unknown}
