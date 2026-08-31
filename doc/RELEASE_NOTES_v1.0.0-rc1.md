# Hunter Community v1.0.0-rc1 · Release Notes

**发布时间**:2026-09-01
**发布类型**:Release Candidate · 供复赛评委验证
**关联复赛验证方案**:`doc/开源hunter-community/04开源比赛/2026-08-28_复赛验证方案-预测评估-交易成本-概率校准-免责声明.md`

## 30 秒版

复赛评委的 4 类优化建议 · **全部生产就绪**:

| 评委建议 | 我们的答案 | 状态 |
|---|---|---|
| A · 预测评估 | 每日 scheduler + accuracy/consistency/reversals/evolution 4 张卡 + 分享验证链 /p/{token} | 🟢 100% |
| B · 交易成本 | 三市场 broker preset(cn/hk/us)+ 毛/净并列 + 逐笔明细 + sqrt_impact 冲击 + CSV 导出 | 🟢 100% |
| C · 概率校准 | 点估计 + 80/95 区间 + 三类概率环 + Brier + reliability diagram | 🟢 100%(小王已交付) |
| D · 免责声明 | 合规四层 + 灰度开关 HUNTER_COMPLIANCE_MODE + violation 落库 | 🟢 100% |

## 演示脚本(15 分钟)

**Act 1 · 产品**(3 min)
1. 打开 https://hunter-community.agentpit.io · 首访弹合规声明
2. 侧栏三层:数据源 32 · 工具箱 13 · SKILL 23

**Act 2 · 预测评估**(4 min)
3. `/evaluation` · 头部 4 指标带
4. 反转清单 → 单股演变 → 因子归因
5. 复制任一预测分享链 · 无痕新窗打开

**Act 3 · 概率校准**(3 min)
6. `/kpred?symbol=600519` · 区间条 + 三类概率环
7. `/evaluation` → 校准 tab · reliability + Brier

**Act 4 · 交易成本**(3 min)
8. `/quant` · 跑一次 · 报告顶部并列毛/净
9. 切三个 preset · 展开逐笔明细 · 导出 CSV

**Act 5 · 合规**(2 min)
10. 报告右下角水印 · 页脚 · LLM 输出末尾自动合规语

## 生产环境

- URL:https://hunter-community.agentpit.io
- Basic auth:hunter / HunterCE-2026-init!
- 评审专用账号:judge-2026@hunter-community.demo / Judge-2026-Review!
- 5 容器全 healthy:web(3100) · api(8100) · opencode(3921) · postgres(5442) · redis(6479)

## 版本对照

- **v0.2.0**(2026-08-15):v1.0 之前 · 能力三层重构完成 · Kronos 曾接不上
- **v1.0.0-rc1**(2026-09-01)· 本 release:复赛 4 类优化全部生产就绪 · 每日 scheduler + Kronos 通 + 逐笔成本 + 合规灰度
- **v1.0.0**(计划 2026-09-05)· 观察 3 天稳定 + Grafana 后正式发

## 关键 commits

见 CHANGELOG.md v1.0.0-rc1 段。
