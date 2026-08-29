// 报告页水印(§3.D.3.2)· 每张报告卡右下角常驻
// 与全站底栏配合 · 保证:
//   1. 无论用户滚到哪 · 看到的报告卡都自带合规提示
//   2. 未来做分享页 · 截图/分享出去也带得走
// pointer-events-none 保证不影响点击
'use client'
export default function ComplianceWatermark({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`pointer-events-none select-none absolute
                    ${compact ? 'bottom-1 right-2 text-[9px]' : 'bottom-2 right-3 text-[10px]'}`}
         style={{ color: 'var(--text-muted)', opacity: 0.55 }}>
      仅供研究 · 非投资建议
    </div>
  )
}
