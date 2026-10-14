'use client'
import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Sparkles, Check } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import {
  formatModelDisplayName,
  listAgents,
  listProviders,
  type AgentInfo,
  type ProviderInfo,
} from '../lib/opencodeClient'

// ═══ AgentPicker ═══════════════════════════════

interface AgentPickerProps {
  value: string
  onChange: (agentName: string) => void
}

export function AgentPicker({ value, onChange }: AgentPickerProps) {
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    listAgents().then(setAgents).catch(() => {})
  }, [])

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  // value 为空 = 没显式选过,走 opencode 默认 —— 显示"默认"而不是空白
  const current = agents.find((a) => a.name === value) || { name: value || '默认' }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        title="切换 agent"
        style={{
          padding: '5px 10px',
          background: 'transparent',
          border: 'none',
          color: HUNTER.INK_S,
          fontSize: 12,
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          borderRadius: 6,
          transition: 'background 0.1s',
          fontFamily: 'inherit',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.background = '#f8f6ef')}
        onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
      >
        <Sparkles size={11} />
        {current.name}
        <ChevronDown size={10} />
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            bottom: '100%',
            left: 0,
            marginBottom: 6,
            minWidth: 220,
            maxHeight: 320,
            overflowY: 'auto',
            background: '#fff',
            border: `1px solid ${HUNTER.LINE}`,
            borderRadius: 10,
            boxShadow: '0 8px 24px rgba(30,20,10,0.12)',
            padding: 4,
            zIndex: 100,
          }}
        >
          {agents.length === 0 && (
            <div style={{ padding: 12, fontSize: 12, color: HUNTER.INK_F, textAlign: 'center' }}>
              加载中...
            </div>
          )}
          {agents.map((a) => (
            <button
              key={a.name}
              type="button"
              onClick={() => {
                onChange(a.name)
                setOpen(false)
              }}
              style={{
                width: '100%',
                padding: '8px 10px',
                background: a.name === value ? '#f8f6ef' : 'transparent',
                border: 'none',
                borderRadius: 6,
                textAlign: 'left',
                cursor: 'pointer',
                fontFamily: 'inherit',
                display: 'flex',
                alignItems: 'flex-start',
                gap: 8,
              }}
              onMouseEnter={(e) => {
                if (a.name !== value) e.currentTarget.style.background = '#faf9f4'
              }}
              onMouseLeave={(e) => {
                if (a.name !== value) e.currentTarget.style.background = 'transparent'
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: HUNTER.INK }}>{a.name}</div>
                {a.description && (
                  <div
                    style={{
                      fontSize: 11,
                      color: HUNTER.INK_F,
                      marginTop: 2,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                    }}
                  >
                    {a.description}
                  </div>
                )}
              </div>
              {a.name === value && <Check size={13} style={{ color: HUNTER.THEME, marginTop: 2 }} />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ═══ ModelPicker ══════════════════════════════

interface ModelPickerProps {
  value: string // e.g. "oneapi/gemini-3.5-flash"
  onChange: (providerID: string, modelID: string, displayName: string) => void
}

interface FlatModel {
  providerID: string
  providerName: string
  modelID: string
  displayName: string
}

export function ModelPicker({ value, onChange }: ModelPickerProps) {
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    listProviders().then(setProviders).catch(() => {})
  }, [])

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  const flat: FlatModel[] = []
  providers.forEach((p) => {
    Object.entries(p.models).forEach(([mid, m]) => {
      flat.push({
        providerID: p.id,
        providerName: p.name,
        modelID: mid,
        displayName: m.name || mid,
      })
    })
  })

  const currentKey = value
  const current = flat.find((m) => `${m.providerID}/${m.modelID}` === currentKey)
  const rawMid = currentKey.split('/').slice(1).join('/') || currentKey
  const rawPid = currentKey.includes('/') ? currentKey.split('/')[0] : ''
  const label =
    current?.displayName ||
    (rawMid ? formatModelDisplayName(rawPid, rawMid, rawMid) : '') ||
    '选模型'

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        title="切换 model"
        style={{
          padding: '5px 10px',
          background: 'transparent',
          border: 'none',
          color: HUNTER.INK_S,
          fontSize: 12,
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          borderRadius: 6,
          transition: 'background 0.1s',
          fontFamily: 'inherit',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.background = '#f8f6ef')}
        onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
      >
        {label}
        <ChevronDown size={10} />
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            bottom: '100%',
            right: 0,
            marginBottom: 6,
            minWidth: 260,
            maxHeight: 360,
            overflowY: 'auto',
            background: '#fff',
            border: `1px solid ${HUNTER.LINE}`,
            borderRadius: 10,
            boxShadow: '0 8px 24px rgba(30,20,10,0.12)',
            padding: 4,
            zIndex: 100,
          }}
        >
          {providers.length === 0 && (
            <div style={{ padding: 12, fontSize: 12, color: HUNTER.INK_F, textAlign: 'center' }}>
              加载中...
            </div>
          )}
          {providers.map((p) => (
            <div key={p.id} style={{ marginBottom: 4 }}>
              <div
                style={{
                  padding: '6px 10px 3px',
                  fontSize: 10,
                  fontWeight: 600,
                  color: HUNTER.INK_F,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                }}
              >
                {p.name}
              </div>
              {Object.entries(p.models).map(([mid, m]) => {
                const isCur = `${p.id}/${mid}` === currentKey
                const disp = formatModelDisplayName(p.id, mid, m.name)
                const isArgon =
                  mid === 'hunter-chat' ||
                  mid === 'hunter-deep' ||
                  mid === 'gemini-4-argon' ||
                  disp.includes('Gemini 4 Argon')
                return (
                  <button
                    key={mid}
                    type="button"
                    onClick={() => {
                      onChange(p.id, mid, disp)
                      setOpen(false)
                    }}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      background: isCur ? '#f8f6ef' : 'transparent',
                      border: 'none',
                      borderRadius: 6,
                      textAlign: 'left',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                      fontSize: 13,
                      color: HUNTER.INK,
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 6,
                    }}
                    onMouseEnter={(e) => {
                      if (!isCur) e.currentTarget.style.background = '#faf9f4'
                    }}
                    onMouseLeave={(e) => {
                      if (!isCur) e.currentTarget.style.background = 'transparent'
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600 }}>{disp}</div>
                      {isArgon && (
                        <div style={{ fontSize: 11, color: HUNTER.INK_F, marginTop: 2 }}>
                          底层模型：Google Gemini 4 Argon（谷歌 4.0 旗舰推理 · 100 万上下文）
                        </div>
                      )}
                    </div>
                    {isCur && <Check size={12} style={{ color: HUNTER.THEME, marginTop: 2 }} />}
                  </button>
                )
              })}
            </div>
          ))}
          <div
            style={{
              borderTop: `1px solid ${HUNTER.LINE}`,
              marginTop: 4,
              paddingTop: 4,
            }}
          >
            <a
              href="/settings?tab=llm"
              style={{
                display: 'block',
                padding: '6px 10px',
                fontSize: 11.5,
                color: HUNTER.THEME,
                textDecoration: 'none',
                borderRadius: 6,
                fontWeight: 500,
              }}
            >
              📋 查看/下载大模型调用日志 →
            </a>
          </div>
        </div>
      )}
    </div>
  )
}
