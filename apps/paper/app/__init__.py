"""智能炒股 · Paper Service（唯一账本 + 确定性风控）。

与 `apps/api` 同栈（FastAPI / psycopg2），但**独立镜像、独立进程、独立库角色**：
它是全仓唯一持有 `fin_paper_rw` 连接的服务，账本的所有写入都从这里过。

设计依据：`plan/ref/01方案-技术基线.md` §七（模拟交易：以独立 Paper Service 为核心）
/ `plan/ref/08-开源产品集成部署方案.md` §二（`paper` 容器）/ §八（部署护栏）
/ `plan/ref/09-数据库结构方案.md` §4.5（委托成交账本）/ §4.6（估值与对账）。
"""
