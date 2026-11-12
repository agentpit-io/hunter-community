"""智能炒股 · fin-worker —— Runtime Bridge 与 Temporal Workers（执行编排层）。

职责（`01方案 §5.1`「Runtime Bridge」一行）：工作流模板、参数校验、权限、
服务路由、运行编号。**不做策略分析**、**不碰账本**。

- 六个交易时点由 Temporal 调度（`05 §3.2` M-14）；
- 策略意图 → Paper Service 命令（M-15）；
- 「什么时候拉数据」也归这里（`01方案 §5.3`：上层调度只有一个权威）。

账本库 `hunter_fin` 的连接串**不存在于本服务**：它想改账本，只能打 `paper` 的 HTTP。
`tests/test_no_ledger_access.py` 有源码守卫钉住这一条。
"""

__version__ = "0.1.0"
