import { redirect } from 'next/navigation'

/** 板块根 `/finance` → 总览。App Router 里用 server 端 redirect，刷新也稳。 */
export default function FinanceRoot() {
  redirect('/finance/overview')
}
