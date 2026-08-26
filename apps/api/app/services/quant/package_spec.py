"""数据包的表结构约定 —— **打包端和导入端共用这一份**。

方案见 doc/开源hunter-community/01详细工作目录/11量化策略/
      23_20260824_数据包分发方案.md

## 为什么要单独一个模块

CSV 里没有列名,靠**位置**对应。打包端和导入端各写一份列顺序的话,
改一处忘另一处 → 数据照样导进去,只是价格列里装的是成交量。

**这种错不会报任何异常** —— 界面上看只是"数字不对",而回测跑出来
是一条完全错误但形状正常的曲线。所以两边必须引用同一份定义。

(同类的坑这个项目里出现过:`LOCAL_ONLY` 因子名单曾经在回填脚本和
定时任务里各存一份,加了新因子只改一处,表现是"回填补了、每日没算"。)
"""
from __future__ import annotations

# 与 sql/20260824_data_center.sql 对应。
# 用户代码版本低于这个值时拒绝导入 —— 旧代码导新包会报一堆
# "某某表不存在",而根因和报错之间隔着好几层。
SCHEMA_VERSION = "20260824"


class Vol:
    """一卷的定义。

    cols     导出/导入的列,**顺序即 CSV 的列序**
    conflict 主键冲突时的判定列
    update   冲突时要更新的列(通常是 cols 减去 conflict)
    """

    def __init__(self, table: str, cols: list[str], conflict: list[str],
                 update: list[str] | None = None):
        self.table = table
        self.cols = cols
        self.conflict = conflict
        self.update = update if update is not None else [
            c for c in cols if c not in conflict]

    @property
    def col_sql(self) -> str:
        return ", ".join(self.cols)

    @property
    def conflict_sql(self) -> str:
        return ", ".join(self.conflict)

    @property
    def update_sql(self) -> str:
        return ", ".join(f"{c} = EXCLUDED.{c}" for c in self.update)


# ⚠ `index_component` 的股票代码列叫 **stock_code** 不是 code ——
# 第一版打包脚本按 code 写,直接 UndefinedColumn。列名一律以
# `\d 表名` 查出来的为准,不按印象写。
KLINE = Vol("klines",
            ["code", "period", "ts", "open", "high", "low", "close", "volume"],
            ["code", "period", "ts"])

FINANCIAL = Vol("financial_metric",
                ["code", "report_date", "metric_key", "value"],
                ["code", "report_date", "metric_key"])

# stocks 的主键是 (code, user_id)。包里不带 user_id ——
# 那是每个实例自己的东西。导入时统一填 '' (全局股票),
# 不会覆盖用户自己加的自选(那些 user_id 是他的 uuid)。
STOCKS = Vol("stocks",
             ["code", "name", "market", "exchange", "asset_type"],
             ["code", "user_id"],
             ["name", "market", "exchange", "asset_type"])

INDEX_COMPONENT = Vol("index_component",
                      ["index_code", "stock_code", "effective_from",
                       "effective_to", "weight"],
                      ["index_code", "stock_code", "effective_from"])

INDUSTRY = Vol("stock_industry", ["code", "l1", "l2"], ["code"])

# 文件名前缀 → 卷定义。导入端靠这个认出每个文件该进哪张表。
BY_FILE_PREFIX = {
    "klines-": KLINE,
    "financial": FINANCIAL,
    "meta-stocks": STOCKS,
    "meta-index_component": INDEX_COMPONENT,
    "meta-stock_industry": INDUSTRY,
}


def vol_of(filename: str) -> Vol | None:
    """按文件名认卷。认不出返回 None —— **不猜**。

    认错的后果是往错误的表里灌数据,而 COPY 的列数对不上时会报错、
    列数恰好对上时会静默写错。宁可跳过并告诉用户"这个文件不认识"。
    """
    for prefix, vol in BY_FILE_PREFIX.items():
        if filename.startswith(prefix):
            return vol
    return None
