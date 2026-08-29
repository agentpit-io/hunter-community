// /legal/disclaimer · 完整版免责声明(§3.D.3.4)
// 复赛评委可通过全站底栏进入 · 或从首访弹层"完整版"链接进入
export const metadata = { title: '免责声明 · Hunter Community' }

export default function DisclaimerPage() {
  return (
    <main className="min-h-screen py-10 px-4"
          style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <article className="max-w-2xl mx-auto space-y-6">
        <header>
          <h1 className="text-2xl font-bold">免责声明</h1>
          <p className="text-xs mt-2 opacity-60">
            最后更新:2026-08-29 · 版本 v1.0 · Hunter Community
          </p>
        </header>

        <section className="space-y-3 text-sm leading-relaxed">
          <h2 className="text-lg font-semibold">一、产品性质</h2>
          <p>
            Hunter Community(下称"本工具")是基于 Apache 2.0 协议发布的开源投资研究工具,
            提供数据聚合、AI 分析、走势预测、量化策略回测等能力,
            仅供个人学习和研究使用。本工具不是持牌金融机构,不提供投资顾问服务、
            不代客交易、不承担任何盈亏结果。
          </p>

          <h2 className="text-lg font-semibold">二、非投资建议</h2>
          <p>
            本工具所有输出(包括但不限于股票分析报告、K 线预测、策略回测结果、AI 对话内容)
            <b>均不构成任何形式的投资建议、要约、要约邀请或推荐</b>。
            使用者据此作出的任何投资决策及产生的盈亏,均由使用者自行承担。
          </p>

          <h2 className="text-lg font-semibold">三、历史业绩不代表未来收益</h2>
          <p>
            所有回测结果、历史命中率、K 线预测评估指标均基于历史数据统计推断,
            不构成对未来收益的任何承诺或保证。金融市场受多种复杂因素影响,
            过去的表现无法预测未来。
          </p>

          <h2 className="text-lg font-semibold">四、数据来源与准确性</h2>
          <p>
            本工具使用公开可获取的行情数据源(包括 akshare、Tushare、
            自建 finance-data 中台等)。数据可能因上游延迟、
            接口调整、网络中断等原因存在错误、缺失或滞后。
            AI 模型输出为统计推断,可能存在事实错误或偏差。
            <b>关键决策前请以交易所官方数据为准</b>。
          </p>

          <h2 className="text-lg font-semibold">五、概率与不确定性</h2>
          <p>
            本工具输出的置信区间、三类概率、Brier score 等校准指标,
            是模型对自身不确定性的估计,不代表真实市场未来的概率分布。
            实际市场可能出现区间之外的极端事件(俗称"黑天鹅")。
          </p>

          <h2 className="text-lg font-semibold">六、交易成本模型</h2>
          <p>
            量化回测中的交易成本模型(佣金、印花税、滑点、规费)基于 2026 年
            公开标准零售参数整理,是简化模型。实盘中的成交价格、
            成交时机、流动性冲击、大单执行等因素会导致实际成本
            与回测模型显著不同。
          </p>

          <h2 className="text-lg font-semibold">七、开源特性</h2>
          <p>
            本工具源代码 100% 开源,托管于{' '}
            <a href="https://github.com/agentpit-io/hunter-community" target="_blank"
               rel="noopener noreferrer" style={{ color: '#f59e0b' }}
               className="underline">GitHub · agentpit-io/hunter-community</a>。
            用户可自主部署、审计、修改代码。使用本工具即表示您理解并同意
            Apache 2.0 协议的相关条款。
          </p>

          <h2 className="text-lg font-semibold">八、风险自担</h2>
          <p>
            <b>市场有风险 · 投资需谨慎 · 决策由使用者自行承担</b>。
            使用本工具前,请充分了解自身的风险承受能力,并根据自身情况谨慎决策。
            如有必要,请咨询持牌投资顾问。
          </p>

          <h2 className="text-lg font-semibold">九、责任限制</h2>
          <p>
            在法律允许的最大范围内,本工具作者、贡献者、部署方不对使用者
            或第三方因使用本工具而产生的任何直接、间接、附带、特殊或后果性损失
            承担责任,包括但不限于利润损失、数据损失、业务中断等。
          </p>

          <h2 className="text-lg font-semibold">十、条款变更</h2>
          <p>
            本免责声明可能随时更新。重大变更时,系统将提示您重新确认。
            继续使用本工具即表示您接受更新后的条款。
          </p>
        </section>

        <footer className="pt-6 border-t text-xs opacity-60"
                style={{ borderColor: 'var(--border)' }}>
          <p>© 2026 Hunter Community · Licensed under Apache License 2.0</p>
          <p className="mt-1">
            <a href="/legal/privacy" style={{ color: 'inherit' }} className="hover:underline">
              隐私政策
            </a>
            {' · '}
            <a href="/" style={{ color: 'inherit' }} className="hover:underline">
              返回首页
            </a>
          </p>
        </footer>
      </article>
    </main>
  )
}
