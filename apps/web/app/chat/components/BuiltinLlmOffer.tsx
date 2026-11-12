'use client'

// 「用这把 key 同时开启内置大模型额度」· 挂在侧栏平台 key 弹窗的已解锁状态下
//
// 为什么要有:平台 key(工具 / 数据源)与大模型配置是两件事,弹窗只存前者。
// 2026-09-22 用户实报:填了 Hunter key,对话框里还是自己的 deepseek-flash,
// 以为内置 Gemini 没生效 —— 其实要再走一遍向导选第一张卡。这里把那一步折成一个按钮。
//
// 四种状态:
//   已在用内置额度   → 一行绿字,不打扰
//   .env 锁定        → 说清楚为什么 UI 改不了、改哪三行
//   自带 key         → 按钮 + 「会替换当前模型」的如实说明
//   读不到向导状态   → 什么都不显示(比如公网访问又没口令,那是向导的事)

import { useEffect, useState } from 'react'
import { Check, Loader2, Sparkles, AlertCircle } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import {
  adoptBuiltin, engineReady, getStatus, isFail, type LlmStatus,
} from '../../setup/lib/setupClient'

type Phase = 'loading' | 'hidden' | 'on' | 'locked' | 'offer' | 'running' | 'done' | 'fail'

const box = (bg: string, fg: string): React.CSSProperties => ({
  display: 'flex', gap: 8, padding: '10px 12px', margin: '14px 0',
  borderRadius: HUNTER.R_MD, background: bg, color: fg, fontSize: 12, lineHeight: 1.7,
})

export default function BuiltinLlmOffer() {
  const [phase, setPhase] = useState<Phase>('loading')
  const [llm, setLlm] = useState<LlmStatus | null>(null)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    void getStatus().then((s) => {
      if (isFail(s) || !s.authed) { setPhase('hidden'); return }
      const l = s.llm as LlmStatus
      setLlm(l)
      setPhase(l.builtin ? 'on' : l.env_locked ? 'locked' : 'offer')
    })
  }, [])

  const run = async () => {
    setPhase('running'); setMsg('')
    const r = await adoptBuiltin()
    if (isFail(r) || !r.ok) {
      setMsg(r.message || '切换失败'); setPhase('fail'); return
    }
    if (r.applied === false) {
      // 库里已经写好了,只是这台 opencode 不支持热更新 —— 如实说,不假装生效
      setMsg(`已保存,但对话引擎没能热更新(${r.apply_reason || '原因未知'})。`
        + '在部署目录执行 docker compose restart opencode 后生效(约 50 秒)。')
      setPhase('fail'); return
    }
    setPhase('done')
    // 等 opencode 真的换上新模型再刷新 —— 选择器与会话都要重新拉一遍模型
    const deadline = Date.now() + (r.expect_ready_seconds || 10) * 2000
    while (Date.now() < deadline) {
      const e = await engineReady()
      if (!isFail(e) && e.ready) break
      await new Promise((res) => setTimeout(res, 1500))
    }
    window.location.reload()
  }

  if (phase === 'loading' || phase === 'hidden') return null

  if (phase === 'on') {
    return (
      <div style={box(HUNTER.TAG_OK_BG, HUNTER.TAG_OK_FG)}>
        <Sparkles size={14} style={{ flexShrink: 0, marginTop: 3 }} />
        <span>对话也在用这把 key 的<b>内置大模型额度</b>(Gemini)。</span>
      </div>
    )
  }

  if (phase === 'locked') {
    return (
      <div style={box(HUNTER.TAG_WARN_BG, HUNTER.TAG_WARN_FG)}>
        <AlertCircle size={14} style={{ flexShrink: 0, marginTop: 3 }} />
        <span>
          这把 key 也能开通<b>内置大模型额度</b>(Gemini),但这台实例的大模型配置写在
          <code> .env </code>里,网页改不了。要改用内置额度,把 <code>.env</code> 里三行改成
          <code style={{ display: 'block', margin: '6px 0', whiteSpace: 'pre-wrap' }}>
            {'LLM_BASE_URL=https://hunter.agentpit.io/api/saas/llm/v1\n'
              + 'LLM_API_KEY=<这把 hunt_tools_ key>\nLLM_DEFAULT_MODEL=hunter-chat'}
          </code>
          然后 <code>docker compose up -d</code>(不是 restart)。
        </span>
      </div>
    )
  }

  return (
    <div style={{
      margin: '14px 0', padding: '12px 14px', borderRadius: HUNTER.R_MD,
      border: `1px solid ${HUNTER.LINE}`, background: '#fff',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13,
                    fontWeight: 600, color: HUNTER.INK, marginBottom: 6 }}>
        <Sparkles size={14} style={{ color: HUNTER.THEME }} />
        这把 key 还能直接用内置大模型(Gemini)
      </div>
      <div style={{ fontSize: 12, lineHeight: 1.7, color: HUNTER.INK_S, marginBottom: 10 }}>
        对话现在用的是你自己配的 <code>{llm?.model || '—'}</code>。开启后改用内置额度
        (日常对话 Gemini Flash、深度分析 Gemini Pro,每天免费额度),
        <b>会替换当前的大模型配置</b>;想切回自带 key,到 设置 → 大模型 →
        「重新运行初始化向导」重新填即可。会先做一次连通 / 对话 / 工具调用检测,
        没通过就不改。
      </div>
      <button
        onClick={run}
        disabled={phase === 'running' || phase === 'done'}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          padding: '8px 14px', fontSize: 13, fontWeight: 600,
          background: HUNTER.THEME, color: '#fff', border: 'none',
          borderRadius: HUNTER.R_MD,
          cursor: phase === 'running' || phase === 'done' ? 'default' : 'pointer',
          opacity: phase === 'running' || phase === 'done' ? 0.7 : 1,
        }}
      >
        {phase === 'running' ? <Loader2 size={14} className="animate-spin" />
          : phase === 'done' ? <Check size={14} /> : <Sparkles size={14} />}
        {phase === 'running' ? '检测中(约 10 秒)…'
          : phase === 'done' ? '已切换 · 正在刷新页面…' : '一键开启内置额度'}
      </button>
      {phase === 'fail' && msg && (
        <div style={{ fontSize: 12, color: HUNTER.UP, marginTop: 8, lineHeight: 1.6 }}>{msg}</div>
      )}
    </div>
  )
}
