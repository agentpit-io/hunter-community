// /legal/privacy · 简版隐私政策(§3.D.3.4)
export const metadata = { title: '隐私政策 · Hunter Community' }

export default function PrivacyPage() {
  return (
    <main className="min-h-screen py-10 px-4"
          style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <article className="max-w-2xl mx-auto space-y-6">
        <header>
          <h1 className="text-2xl font-bold">隐私政策</h1>
          <p className="text-xs mt-2 opacity-60">
            最后更新:2026-08-29 · 版本 v1.0 · Hunter Community
          </p>
        </header>

        <section className="space-y-3 text-sm leading-relaxed">
          <h2 className="text-lg font-semibold">一、开源自部署优先</h2>
          <p>
            Hunter Community 设计目标是让用户在自己的机器上部署运行,
            数据默认不离开您的部署环境。以下描述基于官方演示环境
            <code>hunter-community.agentpit.io</code>,自部署用户不适用。
          </p>

          <h2 className="text-lg font-semibold">二、收集的信息</h2>
          <ul className="list-disc list-inside space-y-1 pl-2">
            <li>账号信息:邮箱、密码(argon2id 哈希 · 不存明文)、显示名</li>
            <li>使用日志:请求路径、时间、状态码(用于排障)</li>
            <li>自选股:您添加到 watchlist 的股票代码</li>
            <li>预测/回测参数:您手动配置的策略参数</li>
            <li>不收集:实名信息、身份证号、支付信息(本工具无付费功能)</li>
          </ul>

          <h2 className="text-lg font-semibold">三、数据使用</h2>
          <p>
            收集的信息仅用于:提供产品功能、排查故障、改进模型。
            <b>不用于任何第三方营销、不出售给任何机构、不用于用户画像</b>。
          </p>

          <h2 className="text-lg font-semibold">四、第三方服务</h2>
          <p>
            官方演示环境涉及以下第三方数据源:akshare(公开行情)、
            自建 finance-data 中台(内网服务)、Kronos K 线预测模型(内网)、
            LLM 服务(用户可选 OneAPI / OpenAI 兼容接口)。
            这些数据源不接收您的账号信息,仅接收匿名化查询请求。
          </p>

          <h2 className="text-lg font-semibold">五、Cookie 与本地存储</h2>
          <p>
            使用 localStorage 存储 JWT access token(1 小时)与 refresh token(30 天),
            仅用于维持登录状态。不使用第三方 cookie、不追踪跨站行为。
          </p>

          <h2 className="text-lg font-semibold">六、数据删除</h2>
          <p>
            如需删除账号及全部数据,请通过 GitHub issue 联系我们。
            自部署用户可直接删除对应 PostgreSQL 数据库中的用户记录。
          </p>

          <h2 className="text-lg font-semibold">七、合规</h2>
          <p>
            本工具遵循开源社区通行的数据最小化原则。
            如您所在地区有更严格的数据保护法规(如 GDPR),
            自部署是获得完全合规的最佳方式。
          </p>
        </section>

        <footer className="pt-6 border-t text-xs opacity-60"
                style={{ borderColor: 'var(--border)' }}>
          <p>© 2026 Hunter Community · Licensed under Apache License 2.0</p>
          <p className="mt-1">
            <a href="/legal/disclaimer" style={{ color: 'inherit' }} className="hover:underline">
              免责声明
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
