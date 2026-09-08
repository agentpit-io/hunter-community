// ⚠️ 本文件是 huntercode(agentpit-io/huntercode · dev 分支)plugins/hunter-lang.ts 的**部署副本**,
// 通过 docker-compose.yml 的 bind mount 送进 opencode 容器 —— 与 hunter-mcp-context.ts 同一套路。
// 镜像里没有这个文件(它是新加的),而服务器上 GHCR 登录已过期、拉不动私镜像,所以只能挂进去。
//
// 改动只在 huntercode 仓做,改完再把文件整个拷过来(保留本段头注释),不要在这里单独改。
// 当前对应 huntercode 提交:6c1c0e9(2026-09-08 · 新建 + 修块注释提前闭合导致的静默加载失败)
/**
 * hunter-lang · 面向用户文本的语言守卫（出口强校验）
 *
 * ## 为什么需要它
 *
 * 铁律 A10（`agentpit/doc/02开源产品维护交接/05-部署铁律与事故清单.md`）：
 * 猎鹿人回答里英文只能是专业名词，不能是短语/句子/段落；而且
 * **prompt 里的中文约束单独用无效，必须 prompt + 出口强校验两道一起上**。
 *
 * 2026-09-07 那次修的是 hunter-api 侧：守卫下沉到 `ToolResult` 出口和
 * `llm_json_call`。但 chat 的主回复**根本不走那两个出口** —— 它由
 *
 * ⚠️ 注意上一行不要写成星号星号斜杠 chat:块注释里出现 `*` 紧跟 `/`
 * 会提前闭合注释,整个文件变成语法错误,而 opencode **静默跳过加载失败的
 * plugin** —— 表现是守卫像不存在一样,日志里连一行报错都没有。
 * (2026-09-08 就是这么白排查了一轮:文件挂进去了、路径也对、就是不生效。)
 * opencode 容器自己产出，经 llm-shim → BFF → 前端，中间一道守卫都没有。
 *
 * 于是 2026-09-08 复发：用户「用 initiating-coverage 分析 GOOG」，
 * 而那个 SKILL 的正文是英文的，模型读完就整段用英文回答：
 *     "I can help you create an equity research initiation report for
 *      Alphabet Inc. (GOOG). This involves 5 separate tasks..."
 * 用户看到满屏英文。**SKILL 是什么语言，不该决定回答是什么语言。**
 *
 * ## 判据和翻译都不在这里实现
 *
 * 有人会想在这个文件里用 TypeScript 写一遍"有没有英文散文"的判断。**不要。**
 * 那套判据（连续英文词 run ≥ 3 且含 ≥ 2 个功能词 / run ≥ 12 / 整段无中文
 * 且 ≥ 4 词）连同功能词封闭集、翻译兜底、8 条反误伤用例，已经在
 * `agents/text_sanitizer.py` + `agents/translation.py` 里了。再写一遍就是
 * 同一件事两处实现，改一处漏一处（交接稿 §9 铁律 3）。
 *
 * 所以这里只做搬运：把整段文本发给 hunter-api 的 `/api/internal/lang/guard`，
 * 拿净化/翻译后的中文回来。
 *
 * ## 挂在哪个 hook
 *
 * `experimental.text.complete` —— 它在 `text-end` 时触发，此刻整段文本已完整，
 * 而且**返回值会覆盖最终 part**（见 packages/opencode/src/session/processor.ts
 * 的 text-end 分支）。流式 delta 已经发出去了，所以用户可能看到英文一闪，
 * 但落库和最终显示的是中文版。这是能在 opencode 侧统一拦截的唯一位置。
 *
 * ## 失败一律放行
 *
 * 守卫挂了不能让用户收不到回答 —— 超时/网络错/端点 500 都原样返回原文。
 * 宁可偶尔漏一段英文，也不要因为守卫本身把整条回复吞掉。
 */
import type { Plugin } from "@opencode-ai/plugin"

const API = process.env.HERMES_API_URL || "http://api:8000"
const KEY = process.env.HUNTER_INTERNAL_KEY || ""

// 翻译要调一次 LLM，给足时间；但不能拖到用户以为卡死。
// 超时就放行原文（见文件头「失败一律放行」）。
const TIMEOUT_MS = 30_000

// 太短的文本不值得走一趟 HTTP：一两个词构不成"英文散文"，
// 判据本身也要求 run ≥ 3 词。
const MIN_LEN = 24

export const server: Plugin = async (_input, _options) => {
  if (!KEY) {
    // 没配 key 的话每条消息都会 401，刷满日志还白等一次超时。
    console.log("[hunter-lang] HUNTER_INTERNAL_KEY 未设置 · 语言守卫关闭")
    return {}
  }
  console.log(`[hunter-lang] 语言守卫已挂载 → ${API}/api/internal/lang/guard`)

  return {
    "experimental.text.complete": async (input: any, output: { text: string }) => {
      const raw = output?.text || ""
      if (raw.length < MIN_LEN) return

      const ctl = new AbortController()
      const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS)
      try {
        const r = await fetch(`${API}/api/internal/lang/guard`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Hunter-Internal-Key": KEY,
          },
          body: JSON.stringify({ text: raw }),
          signal: ctl.signal,
        })
        if (!r.ok) {
          console.log(`[hunter-lang] 守卫返回 ${r.status} · 放行原文`)
          return
        }
        const j: any = await r.json()
        if (!j?.changed) return

        // text 为空 = 净化后没有可用中文、翻译也失败。
        // **不要把空串写回去**，那样用户看到的是一条空回复，比英文更糟；
        // 也不要把原文透出去 —— 这里选择原文，是因为 chat 主回复没有
        // 「规则文案」可以兜底（不像短评那种有真实数字可陈述）。
        // 留下 warn 让这种情况能被发现。
        if (!j.text) {
          console.log("[hunter-lang] ⚠ 净化+翻译都拿不到中文 · 放行原文 ·",
                      raw.slice(0, 80))
          return
        }
        console.log(`[hunter-lang] 命中英文散文 · 已替换为中文 · ${raw.length}→${j.text.length}`)
        output.text = j.text
      } catch (e: any) {
        console.log(`[hunter-lang] 守卫异常(${e?.name || e}) · 放行原文`)
      } finally {
        clearTimeout(timer)
      }
    },
  }
}

export default server
