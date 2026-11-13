'use client'
import React from 'react'
import Link from 'next/link'
import { Activity, ArrowLeft } from 'lucide-react'
import { BrokerConnectorPanel } from '../BrokerConnectorPanel'

export default function BrokerSettingsStandalonePage() {
  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg)', fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif' }}>
      <header style={{ borderBottom: '1px solid var(--border)', padding: '12px 24px', display: 'flex', alignItems: 'center', gap: 12 }}>
        <Link href="/workbench" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, color: 'var(--text)', textDecoration: 'none' }}>
          <Activity size={18} style={{ color: 'var(--blue)' }} />
          <span style={{ fontWeight: 600 }}>猎鹿人 · Hunter-AtomCode v3.4.0</span>
        </Link>
        <span style={{ flex: 1 }} />
        <Link href="/settings" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 13, color: 'var(--blue)', textDecoration: 'none' }}>
          <ArrowLeft size={14} /> 返回通用设置
        </Link>
      </header>
      <main style={{ maxWidth: 960, margin: '0 auto', padding: '28px 24px' }}>
        <BrokerConnectorPanel />
      </main>
    </div>
  )
}
