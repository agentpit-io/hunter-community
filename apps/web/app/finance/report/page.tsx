'use client'
/**
 * 每日报告页（一期 M6 · M-06）。
 *
 * 视觉基线：`plan/ref/原型/03-每日报告.html`。三块：
 *   ① 今日复盘正文（谁写的、用的哪个模型，如实标注）
 *   ② 「今天的自我总结」三栏（做对了 / 没做好 / 明天怎么改）
 *   ③ 历史报告列表
 *
 * **每个数字可追溯**：正文下面列出这份报告的事实行（`fin_report_fact`），
 * 每行带 `source_ref`（从哪个账本行算的）与 `computed_by`（哪段代码算的）。
 * **无报告日显示空态而不是空白**（M-17 验收项）。
 */
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { FileText, Check, TriangleAlert, Sparkles, ListTree, ExternalLink } from 'lucide-react'
import { Card, CardHead, Chip, Note, finFetch } from '../_ui'
import { Grid, SectionTitle, Tbl, EmptyState, ErrorState, LoadingCard } from '../_parts'
import { useFinPage } from '../_data'

type Fact = { metric_key: string; value: number | null; unit: string | null; source_ref: string; computed_by: string }
type Report = {
  report: {
    report_id: string; trade_date: string; status: string; analysis_text: string | null
    self_review: { did_well?: string; did_bad?: string; change_tomorrow?: string } | null
    llm_provider: string | null; llm_model: string | null; prompt_version: string | null
    artifact_ref: string | null; created_at: string
  }
  facts: Fact[]
  receipts: { channel: string; status: string; attempted_at: string }[]
  validate?: { numbers_inspected: number; violations: any[]; trace: any[]; ok: boolean } | null
}
type Payload = {
  project_id: string
  items: { report_id: string; trade_date: string; status: string; llm_provider: string | null; llm_model: string | null; has_artifact: boolean; artifact_ref: string | null; fact_count: number }[]
  latest: Report | null
  empty_state: { reason: string } | null
}

const SELF_LABEL: Array<[keyof NonNullable<Report['report']['self_review']>, string, string]> = [
  ['did_well', '做对了', '有依据的自我肯定'],
  ['did_bad', '没做好', '错了也要写下来'],
  ['change_tomorrow', '明天怎么改', '改进要能落到规则上'],
]

/** 报告正文是纯文本（AI 写的），按空行分段渲染。**不做 markdown 解析** —— 少一层改写，少一处走样。 */
function Paragraphs({ text }: { text: string }) {
  const parts = text.split(/\n{2,}/).map(s => s.trim()).filter(Boolean)
  return (
    <div className="text-sm leading-relaxed flex flex-col gap-3" style={{ color: '#3A342C' }}>
      {parts.map((p, i) => (
        <p key={i} className="m-0" style={{ whiteSpace: 'pre-wrap' }}>{p}</p>
      ))}
    </div>
  )
}

export default function FinanceReportPage() {
  const router = useRouter()
  const { data, loading, error, noProject, reload } = useFinPage<Payload>('/reports')
  const [picked, setPicked] = useState<Report | null>(null)
  const [pickedErr, setPickedErr] = useState('')
  const [showFacts, setShowFacts] = useState(false)

  async function pick(reportId: string) {
    setPickedErr('')
    try {
      const r = await finFetch<Report>(`/reports/${encodeURIComponent(reportId)}`)
      const v = await finFetch<Report['validate']>(`/reports/${encodeURIComponent(reportId)}/validate`)
      setPicked({ ...r, validate: v })
      setShowFacts(false)
    } catch (e: any) {
      if (e?.status === 401) { router.push('/login'); return }
      setPickedErr(e?.message || '读不到这份报告')
    }
  }

  if (loading) return <Card><CardHead title="每日报告" sub="正在从账本读取" /><div className="p-4"><LoadingCard title="加载中…" /></div></Card>
  if (noProject) return (
    <div className="max-w-3xl"><Card><CardHead title="每日报告" sub="还没有进行中的项目" />
      <div className="p-5"><EmptyState title="先在设置向导里开一个项目" desc="每个交易日收盘后，它会给这个项目写一份复盘。" /></div>
    </Card></div>
  )
  if (error || !data) return (
    <div className="max-w-3xl"><Card><CardHead title="每日报告" sub="读取失败" />
      <div className="p-5"><ErrorState title="读不到报告" onRetry={reload}
        desc={`${error || '接口没有返回内容'}。报告正文与事实行都来自账本，取不到就不显示上一份顶替。`} /></div>
    </Card></div>
  )

  const shown = picked || data.latest
  const sr = shown?.report.self_review || null

  return (
    <div className="flex flex-col gap-6 max-w-6xl">
      {pickedErr && <Note tone="warn">{pickedErr}</Note>}

      {!shown ? (
        <Card>
          <CardHead title="每日报告" sub="还没有报告" />
          <div className="p-5">
            <EmptyState title="这个项目还没有生成过报告"
              desc={data.empty_state?.reason || '每个交易日收盘后会生成一份。'} />
          </div>
        </Card>
      ) : (
        <Grid cols={2}>
          {/* 正文 */}
          <Card>
            <CardHead icon={<FileText className="w-4 h-4" />}
              title={`${picked ? '历史报告' : '今日报告'} · ${shown.report.trade_date}`}
              sub="由 AI 根据当天真实成交与账户变化生成 · 结论附证据"
              right={<Chip tone={shown.report.status === 'failed' ? 'amber' : 'ok'}>{shown.report.status}</Chip>} />
            <div className="p-4 flex flex-col gap-3">
              {shown.report.status === 'failed' && (
                <Note tone="warn">
                  这份报告<b>没有通过回读校验</b>（正文里的数字对不上账本），所以它没有被发布。
                  下面显示的是失败时落库的内容，供排查用。
                </Note>
              )}
              {shown.report.analysis_text
                ? <Paragraphs text={shown.report.analysis_text} />
                : <EmptyState title="这份报告没有正文" desc="analysis_text 为空。可能是生成时模型不可用且兜底文案也为空。" />}

              <div className="flex gap-4 flex-wrap pt-3 text-[11px]" style={{ borderTop: '1px dashed rgba(216,205,186,.9)', color: 'var(--text-muted)' }}>
                <span>事实行 {shown.facts.length} 条</span>
                <span>写手：{shown.report.llm_provider || '—'}{shown.report.llm_model ? ` · ${shown.report.llm_model}` : ''}</span>
                <span>回读校验：{shown.validate ? `${shown.validate.numbers_inspected} 个数字 · 违规 ${shown.validate.violations.length} 处` : '—'}</span>
              </div>
              {shown.report.llm_provider === 'fallback' && (
                <Note tone="copper">
                  这份报告是<b>降级文案</b>（当时模型不可用）：只复述账本数字，没有 AI 写的分析。
                  这不是「AI 写的」，页面上不会把它说成 AI 的分析。
                </Note>
              )}
              <Note tone="copper">
                报告里的每一个数字都来自账本与指标代码；AI 只负责把数字解释成人话，<b>不负责编数字</b>。
                下面「逐数字追溯」可以一条条核对。
              </Note>
            </div>
          </Card>

          {/* 本期数字 + 事实行 */}
          <div className="flex flex-col gap-4">
            <Card>
              <CardHead icon={<ListTree className="w-4 h-4" />} title="逐数字追溯"
                sub="正文里的每个数字对应哪一行事实 · 事实来自哪张表哪一列"
                right={<button onClick={() => setShowFacts(v => !v)} className="text-xs font-semibold" style={{ color: '#8A5A18', cursor: 'pointer' }}>
                  {showFacts ? '收起' : '展开'}
                </button>} />
              <div className="p-4">
                {showFacts ? (
                  shown.facts.length === 0
                    ? <EmptyState title="没有事实行" desc="这份报告没有落任何事实行 —— 那本身就是异常，正文不该有数字。" />
                    : <Tbl
                      head={['指标', { t: '值', r: true }, '从哪来（source_ref）', '哪段代码算的']}
                      rows={shown.facts.map(f => [
                        <span className="font-mono text-xs">{f.metric_key}</span>,
                        <span className="font-mono text-xs">{f.value === null ? '—' : `${f.value}${f.unit || ''}`}</span>,
                        <span className="font-mono text-[11px]" style={{ color: 'var(--text-muted)' }}>{f.source_ref}</span>,
                        <span className="font-mono text-[11px]" style={{ color: 'var(--text-muted)' }}>{f.computed_by}</span>,
                      ])} />
                ) : (
                  <div className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                    共 <b>{shown.facts.length}</b> 行事实，每行带 `source_ref`（从哪个账本行算出来）与
                    `computed_by`（哪段代码算的）。展开可以看到全部；也可以调
                    <code className="mx-1 text-[11px]">/reports/{'{id}'}/validate</code>
                    重跑一遍回读校验。
                    {shown.validate && (
                      <div className="mt-2">
                        <Chip tone={shown.validate.violations.length ? 'amber' : 'ok'}>
                          {shown.validate.violations.length ? `有 ${shown.validate.violations.length} 处对不上` : '全部数字都能追溯'}
                        </Chip>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </Card>

            {shown.facts.length > 0 && (
              <Card>
                <CardHead title="本期关键数字" sub="取自同一份事实行 · 单位与账本一致" />
                <div className="p-4 grid grid-cols-2 gap-x-4">
                  {shown.facts.slice(0, 12).map(f => (
                    <div key={f.metric_key} className="flex items-baseline justify-between gap-2 py-1.5 text-xs"
                      style={{ borderBottom: '1px dashed rgba(216,205,186,.75)' }}>
                      <span className="font-mono" style={{ color: 'var(--text-muted)' }}>{f.metric_key}</span>
                      <b style={{ color: 'var(--text)' }}>{f.value === null ? '—' : `${f.value}${f.unit || ''}`}</b>
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </div>
        </Grid>
      )}

      {/* 自我总结三栏 */}
      {shown && (
        <div>
          <SectionTitle title="今天的自我总结" sub="报告是「发生了什么」，这一块是「我怎么看我自己」" />
          <Grid cols={3}>
            {SELF_LABEL.map(([key, title, sub]) => (
              <Card key={key}>
                <CardHead icon={key === 'did_well' ? <Check className="w-4 h-4" /> : key === 'did_bad' ? <TriangleAlert className="w-4 h-4" /> : <Sparkles className="w-4 h-4" />}
                  title={title} sub={sub} />
                <div className="p-4 text-sm leading-relaxed" style={{ color: '#5C5348', whiteSpace: 'pre-wrap' }}>
                  {sr?.[key] || <span style={{ color: 'var(--text-muted)' }}>这一栏为空 —— 没有写就是没有写，不拿套话填。</span>}
                </div>
              </Card>
            ))}
          </Grid>
          <div className="mt-3">
            <Note tone="copper">
              自我总结由 AI 写，但每条都必须附依据（哪一天、哪一笔、哪个凭证编号）；<b>无依据的自我表扬会被回读校验挡下</b>。
            </Note>
          </div>
        </div>
      )}

      {/* 历史报告 */}
      <div>
        <SectionTitle title="历史报告" sub={`共 ${data.items.length} 份 · 点开可看全文`} />
        <Card>
          {data.items.length === 0 ? (
            <div className="p-4"><EmptyState title="还没有历史报告" desc="第一个交易日收盘后，这里会出现第一条记录。" /></div>
          ) : (
            <Tbl
              head={['日期', '状态', '事实行', '写手', '产物']}
              rows={data.items.map(it => {
                const on = (picked?.report.report_id || data.latest?.report.report_id) === it.report_id
                return [
                  <button onClick={() => pick(it.report_id)} className="font-semibold"
                    style={{ color: on ? '#8A5A18' : 'var(--text)', textDecoration: 'underline', cursor: 'pointer' }}>
                    {it.trade_date}
                  </button>,
                  <Chip tone={it.status === 'failed' ? 'amber' : it.status === 'validated' || it.status === 'published' ? 'ok' : 'slate'}>{it.status}</Chip>,
                  `${it.fact_count} 条`,
                  <span className="text-xs">{it.llm_provider || '—'}{it.llm_model ? ` · ${it.llm_model}` : ''}</span>,
                  it.has_artifact
                    ? <span className="inline-flex items-center gap-1 text-xs" style={{ color: '#8A5A18' }}><ExternalLink className="w-3 h-3" />已发布</span>
                    : <span className="text-xs" style={{ color: 'var(--text-muted)' }}>未发布</span>,
                ]
              })}
              foot={<span>报告 id 由 `(project_id, trade_date)` 推导，同一天重跑覆盖同一行，不产生第二份。</span>} />
          )}
        </Card>
      </div>

      <Note tone="copper">
        本页所有内容来自 <code className="text-[11px]">GET /api/v1/fin/reports</code> 与
        <code className="mx-1 text-[11px]">/reports/{'{id}'}/validate</code>；页面不生成也不改写任何数字。
      </Note>
    </div>
  )
}
