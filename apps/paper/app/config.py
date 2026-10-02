"""Paper Service 配置。

三条**必须在启动时成立**的前置（不成立就起不来，见 `selfcheck.py`）：

1. `PAPER_MODE == "PAPER"` —— 模式固定，不接受请求参数切换（`01方案 §7.4`）。
2. `HUNTER_INTERNAL_KEY` 非空 —— 服务间鉴权的唯一口令（`08 §3.1`）。
3. 能连上账本库、且连的是**只增不改**的运行期角色（`fin_paper_rw`）。

账本库连接串的优先级：`PAPER_DATABASE_URL` → `FIN_DATABASE_URL` → `DATABASE_URL`。
单独给一个变量名，是为了让 `paper` 容器与 `api` 容器指向不同的角色时互不污染。
"""

from __future__ import annotations

import os

# 唯一合法的运行模式。任何别的值 = 拒绝启动。
PAPER_MODE_REQUIRED = "PAPER"

# 服务间鉴权头。与 `apps/api` 的 `app/routers/internal_*.py` 同一把口令、同一个头名。
INTERNAL_KEY_HEADER = "X-Hunter-Internal-Key"


def paper_mode() -> str:
    return (os.getenv("PAPER_MODE") or "").strip()


def internal_key() -> str:
    return (os.getenv("HUNTER_INTERNAL_KEY") or "").strip()


def database_url() -> str:
    for name in ("PAPER_DATABASE_URL", "FIN_DATABASE_URL", "DATABASE_URL"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    # 本地默认：与 apps/api 同库同角色不同名。**不写密码**，靠环境变量注入。
    return "postgresql://fin_paper_rw@localhost:5432/hunter"


def expected_db_role() -> str:
    """账本库的运行期角色名。自检用它区分「运行期角色」与「管理员连接」。"""
    return (os.getenv("PAPER_DB_ROLE") or "fin_paper_rw").strip()


# ── 实盘相关字段黑名单（`08 §八-1`：出现在请求参数里就报错，不是忽略）───────
# 判据是**归一化后的键名精确匹配**，不是子串匹配 —— 子串匹配会把 `realized_pnl`
# 这类正常字段误伤。要加新的实盘说法，往这个集合里加，别在别处另写一份判断。
LIVE_FIELD_DENYLIST = frozenset(
    {
        "live",
        "live_mode",
        "livetrade",
        "real",
        "real_mode",
        "real_account",
        "real_trade",
        "realtrade",
        "broker",
        "broker_id",
        "broker_account",
        "brokerage",
        "brokerage_account",
        "account_no",
        "account_number",
        "trade_account",
        "fund_account",
        "stock_account",
        "trade_password",
        "trading_password",
        "counterparty",
        "order_channel",
    }
)
