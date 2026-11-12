-- ════════════════════════════════════════════════════════════════════════
-- 0052_publish_adapter.sql · 智能炒股 · 五期 L07 · 发布适配器 + UNKNOWN 待核实
--
-- 依据：`plan/00五期方案/…五期(闭环补齐)…md` §四 `L07` / §6.1（**只做适配器接口 +
--       站内 + 通用 Webhook + 文件落盘，不接付费/要注册的平台**）；
--       `plan/00五期方案/AI_炒股智能体系统_开源组件整合与技术实施方案.md`
--       §9.1（发布流程）/ §9.2（发布约束：「发布结果不明时进入待核实状态，不盲目重发」）
--       / §11.2（「发布回执不明 → 核实外部状态；无法确认时保留 UNKNOWN」）
--       / §14.1 第 7 项（「内容已发布，但返回超时或网络断开」）；`plan/L07.md` §一。
--
-- 现状（方案 §2.1 第 5 条实测）：发布动作**内嵌在生成流程里**、渠道是写死的字符串
--   `'in_app'`；回执表 `fin_publish_receipt` 有 `UNKNOWN`（`0023:373` CHECK + `:379` 部分索引），
--   但全仓**没有一行代码写它**，「待核实 / 重发」逻辑**无命中**。
--
-- 本迁移**只做加法**（`ADD COLUMN IF NOT EXISTS`），把独立发布步骤需要的字段补齐：
--   · `target`        —— 这次发到哪（webhook URL / 落盘路径）；待核实时要靠它回查。
--   · `resend_of`     —— 重发时指向被重发的那次（留痕：谁 / 何时 / 为何）。
--   · `resend_by`     —— 谁发起了重发。
--   · `resend_reason` —— 为什么重发。
--   · `resend_at`     —— 什么时候重发的。
--
-- 口径与约束：
--   · 一行回执 = 一个 (report_id, channel) 的**现行状态**（receipt_id 由 `report_id:channel`
--     推导，稳定）；重复提交就地更新，不制造第二份。
--   · `UNKNOWN` 是**合法终态候选** —— 待核实；核实后落 `SUCCESS` / `FAILED`，写 `resolved_at`。
--     没有核实、也查不出外部状态时**保留 UNKNOWN**（§11.2）。
--   · `UNKNOWN` 状态下**再次提交同一份报告要拒绝**（除非显式带重发标记）—— 这是代码层规则，
--     不在表上强制（本表只存事实，判据唯一实现在 `app/services/fin/publish/`）。
--
-- 类型口径沿用 `0023`：时刻 TIMESTAMPTZ；文本 TEXT。全表**不出现 float / double / REAL**。
-- 本迁移**不带 `BEGIN;` / `COMMIT;`**（`app/migrate.py` 已把每个文件包在独立事务里）；
--   可重复执行。
-- ════════════════════════════════════════════════════════════════════════


-- ── 发布回执：补「发到哪 + 重发留痕」列（L07）─────────────────────────────
ALTER TABLE fin_publish_receipt
  ADD COLUMN IF NOT EXISTS target        TEXT,
  ADD COLUMN IF NOT EXISTS resend_of     TEXT,
  ADD COLUMN IF NOT EXISTS resend_by     TEXT,
  ADD COLUMN IF NOT EXISTS resend_reason TEXT,
  ADD COLUMN IF NOT EXISTS resend_at     TIMESTAMPTZ;

COMMENT ON COLUMN fin_publish_receipt.target IS
  '这次发到哪：webhook 的目标 URL / 文件落盘路径 / in_app 为 NULL。待核实时靠它回查外部状态。';
COMMENT ON COLUMN fin_publish_receipt.resend_of IS
  '重发时指向被重发的那次提交（receipt_id）—— 留痕用。NULL = 首次提交。';
COMMENT ON COLUMN fin_publish_receipt.resend_by IS
  '谁发起了这次重发（user_id / 内部调用方标识）。NULL = 首次提交。';
COMMENT ON COLUMN fin_publish_receipt.resend_reason IS
  '为什么重发（人工填写）。NULL = 首次提交。';
COMMENT ON COLUMN fin_publish_receipt.resend_at IS
  '什么时候重发的。NULL = 首次提交。';
