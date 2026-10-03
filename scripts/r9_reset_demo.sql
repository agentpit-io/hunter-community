-- R9 演示数据复位（**只针对演示项目** prj_b7791191b3af462791ed496b）
-- 目的：让 scripts/r9_browser_check.mjs 的 rich 阶段可以重跑（apply 会把 P1 从 passed 变成 applied）。
-- 触发器的 disable/enable 只在这一段里出现，跑完立刻恢复。
ALTER TABLE fin_evolution_plan  DISABLE TRIGGER fin_evolution_plan_immutable;
ALTER TABLE fin_evolution_event DISABLE TRIGGER fin_evolution_event_immutable;

DELETE FROM fin_evolution_plan  WHERE proposal_id LIKE 'evp_r9demo%';
DELETE FROM fin_evolution_event WHERE proposal_id LIKE 'evp_r9demo%'
                                   OR payload->>'project_id' = 'prj_b7791191b3af462791ed496b';
DELETE FROM fin_evolution_shadow_event WHERE validation_id LIKE 'evp_r9demo%';
DELETE FROM fin_evolution_reinject_task WHERE proposal_id LIKE 'evp_r9demo%';
DELETE FROM fin_evolution_proposal WHERE proposal_id LIKE 'evp_r9demo%';

ALTER TABLE fin_evolution_plan  ENABLE TRIGGER fin_evolution_plan_immutable;
ALTER TABLE fin_evolution_event ENABLE TRIGGER fin_evolution_event_immutable;

-- 配置回到 apply 之前：撤掉追加的候选版本、重新激活原条目、hold_days_max 回到 3
UPDATE fin_param
   SET strategies = (
         SELECT jsonb_agg(CASE WHEN e->>'key' = 'ma_momentum'
                               THEN jsonb_set(e, '{active}', 'true'::jsonb) ELSE e END)
           FROM jsonb_array_elements(strategies) e
          WHERE e->>'key' NOT LIKE 'ma_momentum#%'
       ),
       hold_days_max = 3
 WHERE project_id = 'prj_b7791191b3af462791ed496b';

-- change_log 也清掉（它记的是每次真实生效；本轮反复重跑，不清理会让报告里的证据混在一起）
DELETE FROM fin_param_change_log WHERE project_id = 'prj_b7791191b3af462791ed496b';

-- 清掉浏览器实测写进来的条目（statement 前缀可辨认）
DELETE FROM fin_experience_evidence
 WHERE experience_id IN (SELECT experience_id FROM fin_experience
                          WHERE statement LIKE '人机混合写入实测条目%' OR statement LIKE '调试写入条目%');
DELETE FROM fin_experience WHERE statement LIKE '人机混合写入实测条目%' OR statement LIKE '调试写入条目%';

SELECT 'proposals' AS t, count(*) FROM fin_evolution_proposal WHERE project_id='prj_b7791191b3af462791ed496b'
UNION ALL SELECT 'events', count(*) FROM fin_evolution_event WHERE payload->>'project_id'='prj_b7791191b3af462791ed496b'
UNION ALL SELECT 'experiences', count(*) FROM fin_experience WHERE project_id='prj_b7791191b3af462791ed496b';
