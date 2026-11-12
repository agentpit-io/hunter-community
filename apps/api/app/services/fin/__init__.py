"""智能炒股 · 服务层。

一期（M1）只有「开户」这件事：把档位翻译成一套**写死**的本金与参数，
落进 `fin_project` / `fin_account` / `fin_param`。

- `tiers.py` —— 三档模板（本金 + 整套 `fin_param` 默认值），逐字段来自
  `plan/ref/03-新用户向导与操作参数方案.md` §二 / §四 / §五 / §六。**参数由服务端写死**，
  不从请求体取——客户端只能选档位，改不了任何一个数字。
- `store.py` —— 库访问（psycopg2 直连 `DATABASE_URL`，与 `app/services/database.py` 同口径）。

本包不接 `paper` / Temporal；账本写入要等 M2/M3。
"""
