-- Hunter Community · 复赛 §3.A.2.3 · 每次预测生成可分享验证链
-- 让评委随手拿一个预测链就能自己核:预测时点快照 + 事后真实收盘 + 命中判定
-- Idempotent: 仅 ADD COLUMN IF NOT EXISTS · 严禁 DROP · 严禁改约束

ALTER TABLE pred_snapshot
  ADD COLUMN IF NOT EXISTS share_token    VARCHAR(16),  -- 短 ID · /p/{token} 分享路径
  ADD COLUMN IF NOT EXISTS input_snapshot JSONB;        -- 预测时的原始输入(K 线尾窗 + 因子快照 + 数据源版本)

-- share_token 唯一索引(允许多 NULL · 只对填了的行去重)
CREATE UNIQUE INDEX IF NOT EXISTS idx_pred_snap_share_token
  ON pred_snapshot(share_token) WHERE share_token IS NOT NULL;
