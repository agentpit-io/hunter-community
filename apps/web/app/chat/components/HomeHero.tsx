'use client'
import { useEffect, useState } from 'react'
import { TrendingUp, BarChart3, Cpu, PenLine, ArrowRight } from 'lucide-react'
import { HUNTER, HUNTER_LOGO } from '../../lib/hunter-theme'
import { listSkills, type SkillItem } from '../lib/skillClient'

interface QuickCard {
  key: string
  icon: React.ReactNode
  title: string
  desc: string
  prompt: string
  skillKey?: string
}

// SKILL 不足 4 项时兜底的静态卡 · 与 hunter-home-design.png 一致
const FALLBACK_CARDS: QuickCard[] = [
  {
    key: 'seed:maotai',
    icon: <TrendingUp size={14} />,
    title: '茅台近期走势如何？',
    desc: '分析技术面与基本面',
    prompt: '分析贵州茅台近期走势，并结合估值和基本面给出判断',
  },
  {
    key: 'seed:ali-vs-tencent',
    icon: <BarChart3 size={14} />,
    title: '阿里 vs 腾讯',
    desc: '业务对比与估值分析',
    prompt: '对比阿里巴巴与腾讯控股的业务、估值与风险',
  },
  {
    key: 'seed:semi',
    icon: <Cpu size={14} />,
    title: '半导体行业政策',
    desc: '梳理最新政策与影响',
    prompt: '梳理近期半导体行业政策、供需和核心受益方向',
  },
  {
    key: 'seed:600519',
    icon: <PenLine size={14} />,
    title: '为 600519 起草评论',
    desc: '生成投资观点与逻辑',
    prompt: '为股票 600519 起草一份包含多空逻辑的投资论点',
  },
]

interface Props {
  onPick: (prompt: string, skillKey?: string) => void
}

export default function HomeHero({ onPick }: Props) {
  const [skills, setSkills] = useState<SkillItem[]>([])

  useEffect(() => {
    // 空态首屏加载 SKILL · 拉失败悄悄降级到兜底
    listSkills()
      .then((d) => setSkills((d.items || []).filter((s) => s.enabled).slice(0, 4)))
      .catch(() => setSkills([]))
  }, [])

  // 优先用 SKILL · 不足 4 张用兜底补齐（去重按 title）
  const skillCards: QuickCard[] = skills.map((s) => ({
    key: s.key,
    icon: <span style={{ fontSize: 14 }}>{s.icon || '⭐'}</span>,
    title: s.name,
    desc: s.hint || s.prompt_tpl.slice(0, 24),
    prompt: s.prompt_tpl,
    skillKey: s.key,
  }))
  const usedTitles = new Set(skillCards.map((c) => c.title))
  const filler = FALLBACK_CARDS.filter((c) => !usedTitles.has(c.title))
  const cards = [...skillCards, ...filler].slice(0, 4)

  return (
    <div
      style={{
        width: '100%',
        display: 'flex',
        justifyContent: 'center',
        padding: '50px 24px 12px',
      }}
    >
      <div style={{ width: 'min(1040px, 100%)' }}>
        {/* Hero · 圆形 logo + Serif 标题 + 副标 */}
        <div style={{ textAlign: 'center', marginBottom: 26 }}>
          <img
            src={HUNTER_LOGO}
            alt="Hunter"
            width={80}
            height={80}
            style={{
              width: 80,
              height: 80,
              minWidth: 80,
              minHeight: 80,
              borderRadius: '50%',
              objectFit: 'cover',
              display: 'block',
              margin: '0 auto 20px',
              boxShadow: '0 14px 30px rgba(181,107,45,.18)',
            }}
          />
          <h1
            style={{
              margin: 0,
              fontFamily: HUNTER.SERIF,
              fontSize: 34,
              letterSpacing: '.01em',
              color: HUNTER.INK,
              fontWeight: 400,
            }}
          >
            您好，我是猎鹿人 Hunter
          </h1>
          <p
            style={{
              textAlign: 'center',
              color: HUNTER.INK_F,
              fontSize: 14,
              margin: '10px 0 0',
            }}
          >
            专注中长期研究与深度分析，为你的投资与决策提供可靠洞见
          </p>
        </div>

        {/* Quick cards 4 列 */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: 12,
            marginBottom: 26,
          }}
          className="hunter-hero-grid"
        >
          {cards.map((c) => (
            <button
              key={c.key}
              type="button"
              onClick={() => onPick(c.prompt, c.skillKey)}
              style={{
                textAlign: 'left',
                background: 'rgba(255,255,255,.9)',
                border: `1px solid ${HUNTER.LINE}`,
                borderRadius: 14,
                padding: '16px 16px 14px',
                minHeight: 86,
                cursor: 'pointer',
                transition: 'transform .18s ease, border-color .18s ease, box-shadow .18s ease',
                boxShadow: '0 4px 18px rgba(40,35,27,.025)',
                fontFamily: 'inherit',
                color: HUNTER.INK,
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'translateY(-2px)'
                e.currentTarget.style.borderColor = '#c8b39f'
                e.currentTarget.style.boxShadow = '0 12px 24px rgba(40,35,27,.06)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)'
                e.currentTarget.style.borderColor = HUNTER.LINE
                e.currentTarget.style.boxShadow = '0 4px 18px rgba(40,35,27,.025)'
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  fontSize: 14,
                  fontWeight: 650,
                }}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                  <span style={{ color: HUNTER.COPPER3, flexShrink: 0 }}>{c.icon}</span>
                  <span
                    style={{
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {c.title}
                  </span>
                </span>
                <ArrowRight size={13} style={{ color: HUNTER.INK_F, flexShrink: 0 }} />
              </div>
              <div style={{ fontSize: 12, color: HUNTER.INK_F, marginTop: 8 }}>{c.desc}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
