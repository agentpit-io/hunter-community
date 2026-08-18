import type { Plugin } from "@opencode-ai/plugin"

/**
 * hunter-mcp-context · 持仓建议 Phase 0 + 多租户方案 A（2026-08）
 *
 * 职责：`tool.execute.before` hook 里，把当前 session 的用户 ID 注入到 hunter 系
 * MCP tool 的 args 里（字段名 `_hermes_user_id`）· MCP 侧转成 X-Hunter-User-Id
 * header 反调 hermes-api 内部端点。
 *
 * 三层 user_id 解析（优先级从高到低）:
 *   1. sessionUsers[sessionID] · hunter-auth chat.message hook 存入（若 opencode
 *      未来修好 metadata 传递 · 优先命中）
 *   2. HTTP 反查 hermes-api /api/internal/session/{sid}/user · 从 chat_session_owner
 *      表拿真实 user_id · in-memory cache 到 sessionUsers 供本 session 后续复用
 *   3. fallback_user_id · 本地开发 / 首次调用未落 owner 时兜底
 *
 * 命中规则（tool 名前缀匹配）：
 *   watchlist_*  → hunter 系 · stock_quickview / stock_news / watchlist_digest
 *   portfolio_*  → hunter 系 · portfolio_rebalance / portfolio_stress
 *   update_risk_profile → hunter 系
 * 其他 tool（read/write/bash/truesource_../kronos_../akshare_..）一律不动。
 *
 * 对应文档:
 *   doc/codex/持仓建议/06-架构断层诊断-opencode-vs-orchestrator.md §5 方案 A
 *   doc/codex/自选股整合/02-多租户身份映射方案.md
 */

interface HermesJwtPayload {
  sub: string
  email: string
  role: string
}

// 与 hunter-auth / hunter-budget 共享 · sessionID → verified user
declare global {
  // eslint-disable-next-line no-var
  var __hunter_session_users: Map<string, HermesJwtPayload> | undefined
}
const sessionUsers = (globalThis.__hunter_session_users ??= new Map())

// 需要注入 user_id 的 hunter tool 名单
const HUNTER_TOOLS = new Set([
  // watchlist_mcp
  "stock_quickview",
  "stock_news",
  "watchlist_digest",
  // 2026-08-14 · community 加的写工具 · 让 chat NL "加XX到自选" 能真的落库,
  // 不加进这个 Set 的话,tool.execute.before hook 不注入 _hermes_user_id,
  // 下游 api 拿不到 X-Hunter-User-Id → 401 unauthorized,LLM 会告诉你"请登录"。
  "watchlist_add",
  // 2026-08-17 · 批量版 · 用户上传截图 OCR 抽出多只时一次性落库
  "watchlist_add_batch",
  // 2026-08-18 · 方案 A · 多时段横向排序 · 替代『逐股 stock_deep_analysis』的低效路径
  // 同样必须走 X-Hunter-User-Id · 否则 api 侧走鉴权分支返 "需要登录后才能对自选股排序"
  "watchlist_rank",
  // portfolio_mcp
  "portfolio_rebalance",
  "portfolio_stress",
  "update_risk_profile",
  // uzi_mcp · Sprint 3 P2 · hunter-UZI-Skill 深度分析
  "stock_deep_analysis",
  // hunter_capability_mcp(_12 Step 3)· 把原来只有 HTTP 接口、模型够不着的
  // 自有能力包成了 MCP。同样必须进这个 Set —— 不然 /api/internal/cap/* 拿不到
  // X-Hunter-User-Id,日志里是 user_id=(none),后续要按用户计量就没抓手。
  "kpred",
  "truesource_brief",
  "truesource_scout",
  // hunter_user_mcp · 用户自定义 MCP 的两个壳工具
  // 不注入 user_id 的话桥接器拿不到身份, 用户配的数据源等于不存在
  "list_my_sources",
  "invoke",
])

// 有些 MCP host 会把 tool 名前缀化成 "{server}_{tool}"（opencode 默认加，前缀剥离）
function stripServerPrefix(name: string): string {
  // hunter_cap_ 是 _12 Step 3 新加的 MCP(gen-config.py 里注册成 hunter_cap)。
  // 漏了它的话 hunter_cap_kpred 剥不掉前缀 → 匹配不到白名单 → 不注入身份,
  // api 侧日志是 user_id=(none)。加白名单和加前缀**两处都要改**,少一处都不生效。
  for (const prefix of ["watchlist_", "portfolio_", "uzi_", "usermcp_", "hunter_cap_"]) {
    if (name.startsWith(prefix)) {
      const rest = name.slice(prefix.length)
      // watchlist_stock_quickview → stock_quickview（如果剥掉后依然命中，采用短名）
      if (HUNTER_TOOLS.has(rest)) return rest
    }
  }
  return name
}

// hermes-api 反查配置（与 watchlist_mcp/portfolio_mcp 一致）
const HERMES_API =
  process.env.HERMES_API_URL || "http://172.17.0.1:8000"
const INTERNAL_KEY =
  process.env.HUNTER_INTERNAL_KEY || "hunter-internal-2026"

/**
 * HTTP 反查 hermes-api chat_session_owner 表 · 拿到当前 session 归属的 user_id。
 * · 成功 → 返回 uid（大小写敏感）
 * · 未登录 / session 无归属 (404) → 返回 null
 * · 网络异常 → 返回 null · 由 fallback 兜底
 */
async function fetchUserIdBySession(sessionID: string): Promise<string | null> {
  try {
    const r = await fetch(
      `${HERMES_API}/api/internal/session/${encodeURIComponent(sessionID)}/user`,
      { headers: { "X-Hunter-Internal-Key": INTERNAL_KEY } },
    )
    if (r.status === 404) {
      return null
    }
    if (!r.ok) {
      console.warn(
        `[hunter-mcp-context] session reverse-lookup HTTP ${r.status} · session=${sessionID.slice(0, 12)}`,
      )
      return null
    }
    const d = (await r.json()) as { user_id?: string }
    return d.user_id || null
  } catch (e) {
    console.warn(
      `[hunter-mcp-context] session reverse-lookup fail · session=${sessionID.slice(0, 12)} · ${(e as Error).message}`,
    )
    return null
  }
}

export const server: Plugin = async (_input, options) => {
  const opts = (options ?? {}) as Record<string, unknown>
  const fallbackUser =
    (opts.fallback_user_id as string | undefined) ||
    process.env.HUNTER_FALLBACK_USER_ID ||
    ""

  console.log(
    `[hunter-mcp-context] loaded · tools=${HUNTER_TOOLS.size} · fallback_user=${fallbackUser || "none"} · hermes_api=${HERMES_API}`,
  )

  return {
    "tool.execute.before": async (input, output) => {
      const toolName = stripServerPrefix(input.tool)
      const isHunter = HUNTER_TOOLS.has(toolName)
      if (!isHunter) return

      // 1. hunter-auth 已存 (未来 opencode 修好 metadata 会命中)
      let payload = sessionUsers.get(input.sessionID)
      let uid: string | undefined = payload?.sub
      let source: "sessionUsers" | "reverse-lookup" | "fallback" | "none" =
        uid ? "sessionUsers" : "none"

      // 2. 未命中 · 反查 hermes-api chat_session_owner 表
      if (!uid) {
        const fetched = await fetchUserIdBySession(input.sessionID)
        if (fetched) {
          uid = fetched
          source = "reverse-lookup"
          // 缓存 · 本 session 后续 tool 调用不再反查
          sessionUsers.set(input.sessionID, {
            sub: fetched,
            email: "",
            role: "USER",
          })
        }
      }

      // 3. 仍无 · 用 fallback（本地开发 · 首次调用未落 owner）
      if (!uid && fallbackUser) {
        uid = fallbackUser
        source = "fallback"
      }

      console.log(
        `[hunter-mcp-context] hook · tool=${input.tool} → ${toolName} · session=${input.sessionID.slice(0, 12)} · source=${source} · uid=${(uid || "").slice(0, 8)}`,
      )

      if (!uid) {
        console.warn(
          `[hunter-mcp-context] tool=${input.tool} session=${input.sessionID.slice(0, 12)} · 无 user_id · MCP 会返回未登录错误`,
        )
        return
      }

      output.args = output.args || {}
      if (!output.args._hermes_user_id) {
        output.args._hermes_user_id = uid
      }
    },
  }
}

export default server
