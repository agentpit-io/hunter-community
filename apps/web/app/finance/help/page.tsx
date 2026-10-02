'use client'
/**
 * 安全与帮助页（一期 M6 · M-07）。
 *
 * 视觉基线：`plan/ref/原型/05-安全与帮助.html`。四块：
 * 三条底线（只模拟 / 唯一账本 / 随时能停）· 三步就能用起来 · 紧急控制 · 常见问题。
 *
 * **两条不能破的口径**（`05 §3.1` M-07 验收项）：
 *   · 「只模拟」的措辞与方案一致：**不出现「与实盘完全一致」**这种话 ——
 *     模拟撮合按公开快照价成交，不可能逐笔复刻真实排队明细，说了就是骗人。
 *   · FAQ **不含人机协作四条**（运行模式 / 选股池 / 我来下单 / 接管），
 *     底线**只有三条** —— 第 4 条（人的介入要留痕可归因）是二期的事，
 *     一期没有人介入，写它反而是空话。
 */
import Link from 'next/link'
import { ShieldCheck, Lock, Pause, Zap, AlertTriangle, HelpCircle } from 'lucide-react'
import { Card, CardHead, Chip, Note } from '../_ui'
import { Grid, SectionTitle } from '../_parts'

const PROMISES = [
  {
    icon: <ShieldCheck className="w-5 h-5" />, t: '只做模拟，不接实盘',
    d: '账户里的钱是虚拟的。系统里没有任何开关、参数或接口能连到真实券商下单，也没有配置任何交易凭证。\n模拟撮合按公开行情快照价成交，不可能逐笔复刻真实排队的成交明细 —— 所以这里只说「按当时快照价成交」，<b>不承诺与真实成交逐笔一致</b>。',
  },
  {
    icon: <Lock className="w-5 h-5" />, t: '唯一账本，AI 碰不到钱',
    d: '只有「模拟账本服务」能改资金和持仓。AI 只能提出「我想买 / 卖什么」的建议，真正的撮合与记账由确定性程序完成，它改不了历史、也改不了余额。\n每一笔成交都保留当时的行情快照凭证，可按编号回放核对。',
  },
  {
    icon: <Pause className="w-5 h-5" />, t: '随时能停，停得干净',
    d: '一个总开关暂停全部自动操作：关掉之后它连委托都不会产生（不是「下了单再拒掉」）。\n已有持仓保持不变，历史记录与报告全部保留 —— 停的是后续操作，不是清仓，也不是删除。',
  },
]

const FAQ = [
  { q: '这个账户里的钱是真的吗？', a: '不是。全部是虚拟资金。所有交易都发生在模拟环境里，用于研究和演示，不会动用你的任何真实资产。' },
  { q: 'AI 会不会乱下单？', a: '不会绕过规则。AI 只能提出买卖「意图」，每一笔都要先过确定性的安全规则（单票上限、止损止盈、每日下单上限等）。规则由独立的账本服务执行，AI 和策略都无法修改或跳过。' },
  { q: '如果程序崩了或网断了会怎样？', a: '任务的进度会被持久化记录：重启后接着原来的任务继续，不会重复下单、也不会把过期的信号补着下单。已经成交的订单和回执都能对得上。' },
  { q: '报告里的数字会不会是编的？', a: '不会。收益、净值、回撤等数字全部由账本和确定性指标代码算出，AI 只负责把这些数字解释成人话。而且每份报告发布前会做一次「回读校验」：正文里每个数字都要能在事实表里找到对应的一行，对不上就不发布。' },
  { q: '为什么有一笔交易被「拦住」了？', a: '多半是触碰了你设定的风险上限，比如某只股票买完后占比会超过上限，或当日下单次数到了上限。这是保护机制在起作用，属于正常行为。' },
  { q: '我能换策略或改风险吗？', a: '可以随时换。换策略、调风险档位都即时生效（策略切换从下一次决策开始用新的）。风控参数只能收紧、不能放宽；要放宽必须你明确确认，并且会记进参数变更日志。' },
  { q: '初始资金在哪里设置？为什么我改不了？', a: '在首次使用的设置向导里选档位时就定了：① 个人玩玩 ¥10,000、② 个人资产管理 ¥100,000、③ 资产运营 ¥1,000,000，三选一，金额不可修改；币种固定人民币。因为初始资金是收益率的分母，项目开启即锁定，事后改动会让历史收益、胜率、回撤全部失真。想换金额或换档位，走「开新项目」：当前项目关闭并封存、旧账本留着可回看，新项目从零开始计。' },
  { q: '为什么只做 A 股，不港股美股一起做？', a: '本版本刻意收口在 A 股。A 股自带一整套独立规则 —— 交易时段、T+1、100 股整手、涨跌停、佣金与印花税 —— 把这五条做准，模拟结果才可信。港股、美股先不开放，也不申请任何交易权限。' },
  { q: '成交价会不会是它自己编的？', a: '不会。每笔成交价一律取委托到达那一刻的行情快照价（带数据源时间戳与盘口），并保存凭证编号，事后可按编号回放核对。需要说清楚的是：模拟撮合按公开快照价成交，不可能逐笔复刻真实排队明细，所以我们说「按当时快照价成交」，<b>不承诺与真实成交逐笔一致</b>。' },
  { q: '它和「AI 对话」「持仓报告」这些旧功能什么关系？', a: '那些是原有的研究工具，继续保留。自动交易是在它们之上新增的一条主线：由智能体自动采集、判断、模拟交易、并生成报告。' },
]

export default function FinanceHelpPage() {
  return (
    <div className="flex flex-col gap-6 max-w-5xl">
      {/* 三条底线 */}
      <Grid cols={3}>
        {PROMISES.map(p => (
          <Card key={p.t}>
            <div className="p-4">
              <div className="w-11 h-11 rounded-xl flex items-center justify-center mb-3"
                style={{ background: 'rgba(63,107,64,.12)', color: '#33572F' }}>{p.icon}</div>
              <div className="text-base font-bold mb-1.5" style={{ color: 'var(--text)' }}>{p.t}</div>
              <div className="text-xs leading-relaxed whitespace-pre-line" style={{ color: '#5C5348' }}>{p.d}</div>
            </div>
          </Card>
        ))}
      </Grid>
      <div className="text-[11px] -mt-3" style={{ color: 'var(--text-muted)' }}>
        守住这三条，一个 AI 自动交易系统才值得信任。第 4 条底线（人的每一笔介入都要留痕且可归因）属于二期的人机协作，
        一期没有人介入，所以这页只有三条。
      </div>

      {/* 怎么用 + 怎么停 */}
      <Grid cols={2}>
        <Card>
          <CardHead icon={<Zap className="w-4 h-4" />} title="三步就能用起来" />
          <div className="p-4 flex flex-col gap-3">
            {[
              ['走一遍设置向导', '选档位（1 万 / 10 万 / 100 万），系统按档位写死本金与整套参数，六步配完就开始。'],
              ['选策略、调风险（可选）', '在「自动交易」页；先用默认配置也行。风控参数只能收紧。'],
              ['打开总开关', '它就按交易日自动跑起来了。你只需要事后看报告。'],
            ].map(([t, d], i) => (
              <div key={t} className="flex gap-3">
                <span className="w-6 h-6 shrink-0 rounded-full flex items-center justify-center text-xs font-extrabold"
                  style={{ background: 'rgba(176,106,50,.14)', color: '#8A5A18' }}>{i + 1}</span>
                <div>
                  <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{t}</div>
                  <div className="text-xs mt-0.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>{d}</div>
                </div>
              </div>
            ))}
            <div>
              <Link href="/finance/auto-trade" className="inline-flex items-center gap-1.5 rounded-[10px] px-4 py-2 text-sm font-semibold"
                style={{ background: 'var(--blue)', color: '#fff' }}>去自动交易页面</Link>
            </div>
          </div>
        </Card>

        <Card>
          <CardHead icon={<Pause className="w-4 h-4" />} title="紧急控制" sub="任何时候都能立刻停下" />
          <div className="p-4 flex flex-col gap-3">
            <div className="rounded-xl border p-3.5" style={{ borderColor: 'var(--border)' }}>
              <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>暂停全部自动交易</div>
              <div className="text-[11px] mt-1 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                「自动交易」页最上方那个总开关。关掉之后，自动交易的任务在出买卖意图之前就停下 ——
                <b>不会再产生任何新委托</b>，已有持仓保持不变。
              </div>
              <div className="mt-2"><Link href="/finance/auto-trade" className="text-xs font-semibold" style={{ color: '#8A5A18' }}>去关掉它 →</Link></div>
            </div>
            <Note tone="warn">
              这些都<b>只作用于模拟账户</b>，不会牵连你其他的真实账户或数据。一期只有总开关这一个控制手段 ——
              「只卖不买」「一键模拟清仓」排在二期，这页不写还没有的功能。
            </Note>
          </div>
        </Card>
      </Grid>

      {/* 谁负责什么 */}
      <div>
        <SectionTitle title="数据与执行是怎么分开的" sub="说人话版本，完整设计见方案文档" />
        <Grid cols={2}>
          <Card>
            <CardHead icon={<ShieldCheck className="w-4 h-4" />} title="两件永远分开的事" />
            <div className="p-4 flex flex-col gap-3">
              {[
                ['研究区：读数据、跑策略、提建议', '能读能算能提「我想买」，但改不了钱、改不了历史收益。'],
                ['执行区：审意图、模拟撮合、记账', '只按规则办事，不自己产生新观点、不改策略。'],
                ['外部新闻与网页按「不可信输入」处理', '不能因为某篇文章说买，就触发下单或改权限。'],
              ].map(([t, d]) => (
                <div key={t} className="flex gap-3">
                  <ShieldCheck className="w-4 h-4 mt-0.5 shrink-0" style={{ color: 'var(--green)' }} />
                  <div>
                    <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{t}</div>
                    <div className="text-xs mt-0.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>{d}</div>
                  </div>
                </div>
              ))}
            </div>
          </Card>
          <Card>
            <CardHead icon={<HelpCircle className="w-4 h-4" />} title="谁负责什么" />
            <div className="p-4 flex flex-col gap-2.5 text-sm">
              {[
                ['AI 智能体', '看盘、分析、提出买卖意图、写复盘', 'copper'],
                ['安全规则', '审核每一笔，超限就拒绝', 'ok'],
                ['模拟账本', '撮合、记账、算净值', 'slate'],
                ['运行调度', '按时启动、崩溃后恢复', 'slate'],
              ].map(([who, what, tone]) => (
                <div key={who} className="flex gap-3 items-center">
                  <span style={{ width: 90, flex: '0 0 auto' }}><Chip tone={tone as any}>{who}</Chip></span>
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{what}</span>
                </div>
              ))}
              <div className="mt-1">
                <Note tone="ok">一句话：<b>策略负责判断，AI 负责研究，执行服务负责可靠记账</b>，谁也不越界。</Note>
              </div>
            </div>
          </Card>
        </Grid>
      </div>

      {/* FAQ */}
      <div>
        <SectionTitle title="常见问题" sub="先看这里，多半能回答你的疑问" />
        <Card>
          <div className="p-4 flex flex-col">
            {FAQ.map((f, i) => (
              <div key={f.q} className="py-3" style={{ borderBottom: i === FAQ.length - 1 ? 'none' : '1px dashed rgba(216,205,186,.75)' }}>
                <div className="text-sm font-bold flex items-start gap-2" style={{ color: 'var(--text)' }}>
                  <AlertTriangle className="w-3.5 h-3.5 mt-1 shrink-0" style={{ color: 'var(--blue)' }} />{f.q}
                </div>
                <div className="text-xs mt-1.5 leading-relaxed" style={{ color: '#5C5348' }}>{f.a}</div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Note tone="copper">
        <b>还有问题？</b>在「AI 对话」里直接问。本页所有说明均以「只做模拟」为前提 ——
        没有任何一句话承诺模拟结果与实盘一致。
      </Note>
    </div>
  )
}
