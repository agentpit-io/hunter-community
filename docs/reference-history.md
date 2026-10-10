# 历史财务、分红与事件参考数据

2026-10-10：采集与归一化工具只写研究快照，不修改交易账户、策略注册或生产数据库。

## 工具

- `api/scripts/collect_reference_history.py`：输入市场到代码数组的 JSON，抓取 A 股财务/分红、港股财务/分红/披露易公告、SEC companyfacts/submissions、美股分红；每份原始响应保存来源、抓取时间、SHA256，支持断点续采。请求失败与有效空结果分开记录。
- `api/scripts/collect_hk_report_evidence.py`：保存披露易业绩 PDF，可通过 `--cn-documents` 加载已有 company_document 快照补抓巨潮分红实施公告。巨潮备用存储日期必须匹配原库文件哈希，不能猜测披露日期。`--skip-text` 只保存原件；文本解析不构成财务数值认证。
- `api/scripts/normalize_reference_history.py --input <快照目录> --end YYYY-MM-DD`：读取 `reference-universe.json`、`batch/`、`arena-reference-history/company_event.json.gz`，输出 `reference-ready/` 的压缩 JSONL、覆盖 CSV 与汇总。
- `api/app/services/reference_history.py`：纯函数归一化；`python api/tests/test_reference_history.py` 验证关键口径。

采集器运行环境需要 requests、pandas、akshare；PDF 文本解析可选 pypdf/fonttools。应放在独立研究环境，不为采集修改正在运行的生产依赖。

## 为什么不能拿到数值就回测

报告期结束日不是披露日。归一化保存原始披露日期与保守的次日可用日期；港股源财务表无披露日时保持为空。已有公告原件不等于每个财务指标已绑定到原件；绑定完成前，港股财务不得用于历史选股。

SEC 同一报告期的不同 filed/accession 版本分别保留，不能用后来修订值替换早期版本。A 股供应商 NOTICE_DATE/UPDATE_DATE 保留，更新时间晚于披露时间时用更晚时间；供应商日期和历史值仍未独立逐项验真。缺日期不推算，缺历史版本不宣称没有前视风险。

A 股派息字段是每十股金额，统一除以十；红股上市日不是现金到账日。港股保留实际币种，复杂送股/现金选择方案不硬转成现金。美股补充公开表没有宣告日且金额经过拆股调整，可记录历史支付，不能提前用于宣告事件信号。

`signal_ready`、`cash_credit_ready` 仅指字段齐全，不代表完整投资/会计口径已验证。不能直接在前复权价格收益上再加分红，否则可能重复计算；现金分红回测必须配套未复权价格、拆合股和持仓调整。

公告记录不是交易建议，SEC 的大量表单不全是经营事件。跨来源重复公告尚未统一去重；同期间多个营收标签也不能盲目加总。未来日历和最新盈利预期不能充当当时已知的历史预期。

当前股票池是现有选手股票池，未包含退市和历史成分变化，不能据此宣称没有生存者偏差。某股票没有抓到分红时是未知，不自动判为过去一年零分红。

## 本次快照

现有 finance-data 库导出加外部补抓，范围覆盖 204 个标的（A 股72、港股64、美股68）。归一化截至 2026-10-09：财务指标26,179条、分红1,410条、事件265,845条，共293,434条。财务/公告覆盖全部204标的，分红记录覆盖175标的，含源间重复记录，不能将总条数当作去重后的经济事件数量。

公开来源：SEC、HKEX、Sina/东方财富（经 AKShare）、Nasdaq、StockAnalysis。Nasdaq 不支持部分非 Nasdaq 标的分红历史，已补抓公开表，公开表有13项补抓失败，另从 Yahoo Chart 取回这13个标的的五年历史事件响应，其中CTVA新增15条截至截止日的历史分红记录；其余返回空事件不自动认定无历史分红。原来源失败与备用结果分别保留。生产策略未接入本快照，本次没有新增回测结论。
