"""Paper Service 配置。

四条**必须在启动时成立**的前置（不成立就起不来，见 `selfcheck.py`）：

1. `PAPER_MODE == "PAPER"` —— 模式固定，不接受请求参数切换（`01方案 §7.4`）。
2. **行情读取凭证**（`READ_KEY_ENV`）非空 —— 拉行情打 api 时用（`snapshot/source.py`）。
3. **下单执行凭证**（`EXEC_KEY_ENV`）回退后非空 —— 服务间鉴权的口令（`08 §3.1`）。
   `HUNTER_EXEC_KEY` **没配时自动沿用读取凭证**（老部署平滑升级，L11）；配了就用自己的。
4. 能连上账本库、且连的是**只增不改**的运行期角色（`fin_paper_rw`）。

**两把钥匙分开（L06 · `plan/L06.md` §1.1）**：行情读取与下单执行是**两个不同的
环境变量**（`READ_KEY_ENV` ≠ `EXEC_KEY_ENV`）。但 L11 起，执行钥匙**没配时回退**
到读取凭证 —— 老部署不必改 `.env` 就能升级；想真正分开（我们自己就是分开的）就配
`HUNTER_EXEC_KEY`。见下面两个常量。

账本库连接串的优先级：`PAPER_DATABASE_URL` → `FIN_DATABASE_URL` → `DATABASE_URL`。
单独给一个变量名，是为了让 `paper` 容器与 `api` 容器指向不同的角色时互不污染。
"""

from __future__ import annotations

import os

# 唯一合法的运行模式。任何别的值 = 拒绝启动。
PAPER_MODE_REQUIRED = "PAPER"

# 服务间鉴权头。与 `apps/api` 的 `app/routers/internal_*.py` 同一把口令、同一个头名。
INTERNAL_KEY_HEADER = "X-Hunter-Internal-Key"

# ── 两把钥匙（L06）。**只有这两个常量是「钥匙取自哪个环境变量」的单一事实** ────
#   行情 / 数据读取：paper 打 api 的行情端点时带它（`snapshot/source.py`）。
#     ⚠️ 这个名字**沿用既有的内网口令**：api 的内网数据面 / 工具通道（opencode、web、
#     部署向导）读的是同一个变量。把它整仓改名要牵动 opencode 的 MCP 副本、web 入口、
#     部署向导与所有既有部署 —— 不在本段范围（见成果文档「偏离方案的决策与原因」）。
#   下单执行：paper 的**执行门**校验它（`security.require_internal_key`）。
READ_KEY_ENV = "HUNTER_INTERNAL_KEY"
EXEC_KEY_ENV = "HUNTER_EXEC_KEY"


def paper_mode() -> str:
    return (os.getenv("PAPER_MODE") or "").strip()


def read_key() -> str:
    """行情 / 数据读取凭证（`READ_KEY_ENV`）。**没有默认值**（缺 = 拒绝启动）。"""
    return (os.getenv(READ_KEY_ENV) or "").strip()


def exec_key() -> str:
    """下单执行凭证（`EXEC_KEY_ENV`）。

    **老部署平滑升级（L11）**：`HUNTER_EXEC_KEY` **没配（或为空）时自动沿用**
    `read_key()` —— 老 `.env` 一个字不用改就能升级，两把钥匙先当成同一把。
    配了 `HUNTER_EXEC_KEY` 就**用自己的**（不被回退盖掉）：想让「行情读取」与
    「下单执行」真正用两把不同的钥匙（我们自己就是这么跑的），照常配即可。

    与 `read_key()` **取自不同的环境变量** —— 这是 L06「两把钥匙分开」的落点：
    配了执行钥匙时，拿得到行情读取凭证 ≠ 能下单。回退只是「没配时的默认 = 读取凭证」，
    **不是**「谁都能下单」。
    """
    return (os.getenv(EXEC_KEY_ENV) or "").strip() or read_key()


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
