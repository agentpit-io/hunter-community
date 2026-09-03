"""ChatOrchestrator · 主对话调度器（P1 版）

流程：
  1. emit `session` 事件
  2. Gemini function_call decision → emit `router_decision`
  3. 若无 tool_calls：直接 stream 汇总（简单闲聊路径）
  4. 若有 tool_calls：
       并行 dispatch 每个 tool
       为每个 tool emit `tool_use`（started）/ `tool_result`（done or error）
  5. 把 tool_results 塞回 messages，Gemini 3.5-Flash stream 汇总
  6. emit `message_delta`* + `message_end`

失败保护：function calling 抛异常 → 走 fallback（关键词路由 + 单一 sub-agent）
"""
from __future__ import annotations
import asyncio
import json
import os
import time
import uuid
from typing import Any, AsyncGenerator, Optional
from loguru import logger

from . import telemetry as _tel
from .event_schemas import validate_event
from .fallback import keyword_route_to_tool
from .stream_bus import SSEEvent, StreamBus
from .tool_registry import ToolCall, ToolRegistry, ToolResult, new_tool_call, load_all_tools

from app.services.online_analysis.llm_client import get_client


# ─── 常见港股 5 位代码 / A 股 6 位代码正则（用于股票码切换检测）───
import re as _re
_STOCK_CODE_RE = _re.compile(r"(?<!\d)(\d{6}|\d{5})(?!\d)")


# ─────────────────────────────── 常量 ───────────────────────────────
MODEL_ROUTER = os.getenv("AGENT_MODEL_ROUTER", "gemini-3.5-flash")
MODEL_ROUTE_LITE = os.getenv("AGENT_MODEL_ROUTE_LITE", "gemini-3.5-flash")

# 简易成本估算（¥/1k tokens，粗略；见 OneAPI 指南 v2026-08-02）
_COST_PER_1K = 0.05


SYSTEM_PROMPT = """你是猎鹿人 Hunter 的投研助手，服务个人 A/H/美股投资者。你的目标是给出"能立刻用来做决策"的回答。

# 【硬性语言规则 · 最高优先级】
- **跟随用户提问的语言**。用户用中文问 → 全程简体中文;用英文问 → 全程英文。
- **SKILL 模板是什么语言,不决定你回答的语言。**
  用户装的第三方 SKILL(如 GitHub 上导入的)模板往往是英文写的,里面有
  `Thesis Invalidation` / `INVESTMENT SIGNAL` / `Action: HOLD` / `Conviction`
  这类英文小标题和字段名。**照搬这些英文标题是错的** ——
  用户用中文提问,却在报告末尾读到一段英文,体验是断裂的。
  正确做法:按 SKILL 的**结构**输出,但把标题和内容都翻成用户的语言:
      Thesis Invalidation      → 论点失效条件
      If signal is BULLISH     → 若信号为看多
      INVESTMENT SIGNAL        → 投资信号
      Action / Conviction      → 操作建议 / 信心强度
      Re-run this analysis when → 何时需要重新分析
  专有名词(Piotroski F-Score、ROIC、WACC、EVA)保留原文,后面括号给中文,
  这类术语翻译反而更难懂。
- **禁止**把思考过程、英文推理、`Let's do...` / `The user is asking...` / `According to...` 等英文段落写进回复
- **禁止**在 message 里输出 tool 调用意图（如 "let's call research(...)"）——真的要调 tool 就发 tool_calls，别用文字描述
- 如果你觉得需要调工具就**直接调**，不要先说"我要调工具"

# 你的能力（tools）
## Sub-agent（分析型，需要 3-40 秒）
- research(code, question): 深度研究 · 投前认知 · 基本面/技术面/估值/风险综合
- scout(code, stock_name, days): 一手情报 · 近期机构调研/北向/公告/舆情汇总
- quant_predict(code, horizon): 量化择时 · Kronos + 8 因子预测 5/10/20 日走势
- hold_judge(code, stock_name, cost_price?, thesis?): 持仓研判 · 多空辩论 → BUY/HOLD/SELL
- event_interpret(code, stock_name, event_text?): 事件解读 · 单事件影响 or 近期热点

## MCP Tool（数据型，秒级）
- get_quote(code): 实时行情
- get_kline(code, days): 日 K 线统计（含均线/关键位/最近 20 根 bars）
- get_pe_history(code, years): PE 历史分位（贵/便宜的定性判断）
- get_fx(pair): HKDCNY / USDCNY 汇率
- get_ah_premium(code_a, code_h): A/H 溢价率（配合 ah-arbitrage-check skill）
- get_analyst_target(code): 分析师目标价（未接入时返回 NOT_IMPLEMENTED）
- get_earnings_consensus(code): EPS 一致预期（同上）

# 调度策略（严格执行）
1. 纯数据问（PE / 价格 / 涨跌幅） → 只调对应 MCP，别叠加
2. 综合投资问（能不能买 / 怎么样 / 值不值） → 并行 research + quant_predict + scout
3. 持仓问（还该拿吗 / 撤不撤 / 止损）→ 只调 hold_judge（cost_price 若有则传）
4. 事件问（XX 影响 / 最近利好利空）→ event_interpret（若用户描述了具体事件，把它 event_text）
5. 技术面 / K 线关键位 → 先 skill(technical-pattern-scan) 拿模板，再按模板调 quant_predict + get_kline
6. A/H 溢价 / A 股 vs H 股 → 先 skill(ah-arbitrage-check)，再 get_ah_premium
7. 简单闲聊 / 概念解释 → 不调工具，直接回

# 输出规范
- 汇总 200-800 字，第一段直接给结论（1-2 句），后面用小圆点分点
- 关键结论 / 数字用 **粗体**
- 结尾必须给「结论 + 下一步建议」两行
- 只用工具返回的事实，禁止臆造数字与价格
- 一个专家失败时明确说「因 X 不可用，本次结论不含 X 部分」，不要装作有
- 若 3 个专家结论互相矛盾，明确指出并给一个权重加权后的判断
- 用中文回答

# 【查不到这只股票时 · 最多试两次就收手】
- 消歧最多调 2 次工具。两次查不到就**直接说查不到**,别继续换工具换写法硬试。
- **不要把试错过程写出来。** 真实事故:用户问「帮我写一份 长鑫科技 的深度投研报告」,
  而长鑫科技(ChangXin Memory)未上市、查不到代码,模型在原地打转,
  把整个内心戏打印给了用户,连系统提示词都念了出来。
- 有些公司本来就查不到,**这不是故障**:未上市(长鑫科技、字节跳动、SpaceX)、
  已退市/长期停牌、或者用户说的根本不是一家公司(产品名/行业名)。
- 正确回答:直说没查到 → 知道原因就说清楚 → 给一条下一步
  (问是不是指某只相关的已上市标的,或请用户直接给代码)。
- **不要编数据**,也不要生成一份每项都写"暂无数据"的空报告 ——
  那种报告没有任何价值,不如一句实话。

# 【合规硬约束 · 优先级等同硬性语言规则】

这是持牌门槛问题,不是风格建议。以下五条**没有例外**,
用户明确要求你"别废话直接说买不买"时也一样执行。

## 1. 绝对化用词禁用
严禁出现:必涨 · 必跌 · 稳赚 · 保证收益 · 无风险 · 一定涨 · 肯定跌 · 铁定 ·
翻倍 · 抄底 · 梭哈,以及它们的任何变体。
也不要承诺具体收益率(如"两周涨 30%")。

## 2. 涉及买卖倾向时必须同时给风险
只要你的回答带有买入/卖出/加仓/减仓的倾向,**必须在同一段里**
列出至少 2 个反面因素,格式:
    支持因素:A · B
    风险因素:X · Y

只说好不说坏,比说错更糟 —— 用户会照着单边信息下注。

## 3. 不给具体仓位数字
禁止"仓位 60%"" 满仓"" 半仓"这类具体配置。
只能给方向:超配 / 标配 / 低配 / 观望。

**理由**:仓位取决于用户的总资产、负债、风险承受力和其它持仓,
这些我们一概不知道。给数字等于替一个我们不了解的人做资金决策。

## 4. 数据必须带来源和时间
引用任何数字都要说清哪来的、什么时候的,例如
"截至 2026-08-31 收盘(数据源:腾讯行情)"。

**没有时间戳的价格是有害的** —— 用户不知道这是今天的还是上周的。

## 5. 含建议的段落要有免责
包含"建议/推荐/应该/值得/可以考虑"的段落,末尾附一句:
"以上基于公开数据分析,仅供研究参考,不构成投资建议。"

一次回答里出现多次不必重复,末尾有一次即可。"""


# ─────────────────────────────── Orchestrator ───────────────────────────────
class ChatOrchestrator:

    def __init__(self, user_id: str, session_id: str,
                 stock_code: Optional[str] = None,
                 stock_name: Optional[str] = None):
        self.user_id = user_id
        self.session_id = session_id
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.bus = StreamBus()
        self._client = get_client()
        # 保证所有 tool 都已注册（幂等）
        try:
            load_all_tools()
        except Exception as e:
            logger.warning("[orch] load_all_tools 失败: {}", e)

        # 累计成本 / token
        self._tokens_in = 0
        self._tokens_out = 0

        # 待落库的 tool bundle
        self._tool_bundle: list[ToolResult] = []
        self._assistant_content = ""
        self._router_reason = ""

        # 若 need_tool 但无股票上下文, orchestrator._decide_tools 会 set 此 flag,
        # summary 阶段直接吐固定提示, 不再走 LLM
        self._need_stock_hint = False

        # query 分类（needs_tool / general_finance / chat）· 影响 summary prompt
        self._category = "chat"

    # ────── 外部入口 ──────
    async def run(self, query: str, history: list[dict]) -> AsyncGenerator[SSEEvent, None]:
        overall_t0 = time.time()
        message_id = f"msg-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
        self._message_id = message_id

        # 上下文切换检测：如果 query 里出现新的股票码 → 覆盖 self.stock_code
        detected_code = _detect_new_stock_code(query, current=self.stock_code)
        if detected_code:
            self.stock_code = detected_code
            self.stock_name = None  # 名称待前端拉，这里不阻塞

        # 若仍无 stock_code, 尝试从 query 里抽中文股名解析 (如"茅台"→ 600519)
        if not self.stock_code:
            resolved = await _resolve_stock_by_name(query)
            if resolved:
                self.stock_code = resolved["code"]
                self.stock_name = resolved["name"]
                logger.info("[orch] 股名解析成功 query={}... → {} {}",
                             query[:20], self.stock_code, self.stock_name)

        yield self._emit("session", {
            "session_id": self.session_id,
            "message_id": message_id,
            "stock_context": ({"code": self.stock_code, "name": self.stock_name}
                              if self.stock_code else None),
        })

        # ─── 决策 ───
        decision_t0 = time.time()
        try:
            tool_calls, reason = await self._decide_tools(query, history)
        except Exception as e:
            logger.warning("[orch] function_call decision 失败: {}", e)
            _tel.error(self.user_id, self.session_id, "LLM_FAILED",
                        str(e)[:120], recoverable=True)
            async for evt in self._fallback_path(query, history, str(e)):
                yield evt
            return

        self._router_reason = reason
        _tel.router_decision(self.user_id, self.session_id, message_id,
                              tool_calls, reason,
                              int((time.time() - decision_t0) * 1000))
        yield self._emit("router_decision", {
            "reason": reason,
            "tool_calls": [tc.to_dict() for tc in tool_calls],
        })

        # ─── 并行调度 ───
        if tool_calls:
            async def _one(tc: ToolCall):
                yield self._emit("tool_use", tc.to_use_payload())
                res = await ToolRegistry.dispatch(tc, self.bus)
                self._tool_bundle.append(res)
                _tel.tool_result(
                    self.user_id, self.session_id, message_id,
                    tc.tool_id, tc.name, res.status, res.duration_ms,
                    error_code=(res.error or {}).get("code") if res.error else None,
                )
                yield self._emit("tool_result", res.to_dict())

            async for evt in self.bus.multiplex([_one(tc) for tc in tool_calls]):
                yield evt

        # ─── 汇总 ───
        try:
            async for chunk in self._stream_summary(query, history, tool_calls):
                self._assistant_content += chunk
                yield self._emit("message_delta", {"content": chunk})
        except Exception as e:
            logger.warning("[orch] summary 失败, 用模板兜底: {}", e)
            fallback_text = self._template_summary(tool_calls)
            self._assistant_content = fallback_text
            yield self._emit("message_delta", {"content": fallback_text})

        cost = round((self._tokens_in + self._tokens_out) / 1000 * _COST_PER_1K, 4)
        total_ms = int((time.time() - overall_t0) * 1000)
        _tel.message_end(self.user_id, self.session_id, message_id,
                          MODEL_ROUTER, self._tokens_in, self._tokens_out,
                          cost, total_ms, len(tool_calls))
        yield self._emit("message_end", {
            "finish_reason": "stop",
            "usage": {
                "model": MODEL_ROUTER,
                "tokens_in": self._tokens_in,
                "tokens_out": self._tokens_out,
                "cost_cny": cost,
            },
        })

        # ─── 落库（异步不阻塞流）──
        await self._persist(query)

    # ────── 决策 ──────
    async def _decide_tools(self, query: str, history: list[dict]) -> tuple[list[ToolCall], str]:
        if self._client is None:
            raise RuntimeError("LLM 网关未配置 (ONE_API_KEY 缺失)")

        tools = ToolRegistry.openai_tool_defs()
        if not tools:
            raise RuntimeError("no tool registered")

        # 动态注入 Skill manifest（Phase 3 起）
        try:
            from . import skill_loader as _sl
            manifest = _sl.skills_manifest()
        except Exception:
            manifest = ""
        system_content = SYSTEM_PROMPT
        if manifest:
            system_content = f"{SYSTEM_PROMPT}\n\n{manifest}"

        messages = [{"role": "system", "content": system_content}]
        messages.extend(history)
        messages.append({"role": "user", "content": self._enrich_query(query)})

        # 三态 query 分类（2026-08-04）：
        # - needs_tool: 投资决策类 → tool_choice="required" 强制调 subagent
        # - general_finance: 金融通识类（董事长/年报/战略）→ 短路：不调 LLM，直接进 summary
        # - chat: 其他 → tool_choice="auto" LLM 自定
        category = _classify_query(query)
        self._category = category
        need_tool = (category == "needs_tool")

        # general_finance 短路：跳过 decision LLM call，直接返回空 tool_calls
        # → 走 _stream_summary 时 extra_hint 会把"通识回答"要求塞给 LLM
        if category == "general_finance":
            return [], "金融通识问题 · 由主 LLM 直答"

        tc_mode = "required" if need_tool else "auto"

        # 用 asyncio.to_thread 避免阻塞
        def _do():
            return self._client.chat.completions.create(
                model=MODEL_ROUTER, messages=messages,
                tools=tools, tool_choice=tc_mode,
                temperature=0.2, max_tokens=4096,
            )
        resp = await asyncio.to_thread(_do)

        usage = resp.usage
        if usage:
            self._tokens_in += usage.prompt_tokens or 0
            self._tokens_out += usage.completion_tokens or 0

        msg = resp.choices[0].message
        calls_raw = msg.tool_calls

        # 无 tool_call 分支
        if not calls_raw:
            # 若明明是投资类问题，LLM 却"直接讲话"→ 兜底：按关键词路由构造一个 tool_call
            if need_tool:
                # 需要 code 的 tool 前置检查：无 stock_code 直接放弃兜底
                # 让 stream_summary 说"请先选一只股票"（用 need_stock 标记）
                if not self.stock_code:
                    logger.warning(
                        "[orch] LLM 该调工具但没调，且无 stock_code (query={}...) → 提示选股",
                        query[:40],
                    )
                    self._need_stock_hint = True
                    return [], "缺少股票上下文，请用户先选股"
                logger.warning(
                    "[orch] LLM 该调工具但没调 (query={}...) → 关键词兜底",
                    query[:40],
                )
                tool_name, _ = keyword_route_to_tool(query)
                if tool_name not in ToolRegistry.known_tools():
                    tool_name = "research"
                # research 传 question, 其他 sub-agent 只需要 code
                args = {"question": query} if tool_name == "research" else {}
                tc = new_tool_call(tool_name, args=args, ctx_code=self.stock_code,
                                   ctx_user_id=self.user_id)
                return [tc], f"LLM 未 fc，按关键词兜底路由到 {tool_name}"
            return [], "无需外部工具，直接回答"

        calls = [ToolCall.from_openai(tc, ctx_code=self.stock_code,
                                       ctx_user_id=self.user_id) for tc in calls_raw]
        # 过滤未注册的工具（LLM 有时会幻觉一个名字）
        known = set(ToolRegistry.known_tools())
        valid = [c for c in calls if c.name in known]
        if not valid:
            raise RuntimeError(f"LLM tool_calls 全部未注册: {[c.name for c in calls]}")

        # reason 截断：msg.content 有时是英文思考文字，前端展示要短
        raw_reason = (msg.content or "").strip()
        if raw_reason:
            # 若明显是英文思考（无中文字符）→ 用默认文案
            has_zh = any("一" <= ch <= "鿿" for ch in raw_reason)
            reason = raw_reason[:60] if has_zh else f"自动分派 {len(valid)} 个专家"
        else:
            reason = f"自动分派 {len(valid)} 个专家"
        return valid, reason

    # ────── 汇总 ──────
    async def _stream_summary(self, query: str, history: list[dict],
                                tool_calls: list[ToolCall]) -> AsyncGenerator[str, None]:
        # 需要选股场景：不走 LLM，直接吐固定提示
        if self._need_stock_hint:
            yield ("请先在顶部「📌 还没选股票」处选一只股票，"
                    "或者在问题里带上股票代码（如 `600519 能买吗`），"
                    "我才能给你个性化的分析。")
            return

        if self._client is None:
            yield "（LLM 未配置，本次仅能返回工具原始结果）"
            return

        cat = getattr(self, "_category", "chat")

        # general_finance 分支 · 用带 google_search 的 LLM 单独跑
        # 拿到实时事实（董事长履历、最新年报数字、公司战略公告等）
        if cat == "general_finance" and not tool_calls:
            async for chunk in self._stream_google_answer(query, history):
                yield chunk
            return

        # 根据 query 分类追加 summary 引导（针对 chat 场景）
        extra_hint = ""
        if cat == "chat" and not tool_calls:
            extra_hint = ("\n\n# 【本轮汇总规则】\n"
                          "用户问题不属于股票/公司/金融领域。请礼貌拒绝："
                          "「我是猎鹿人投研助手，主要陪你研究股票和市场，"
                          "这个话题我不太擅长，换个投资问题吧～」")

        messages = [{"role": "system", "content": SYSTEM_PROMPT + extra_hint}]
        messages.extend(history)
        messages.append({"role": "user", "content": self._enrich_query(query)})

        # 把 tool_results 转成 openai tool message
        if tool_calls:
            # 首先要塞 assistant 消息（含 tool_calls），格式与 openai 契约一致
            tool_calls_openai = []
            for tc in tool_calls:
                tool_calls_openai.append({
                    "id": tc.tool_id, "type": "function",
                    "function": {"name": tc.name,
                                  "arguments": json.dumps(tc.args, ensure_ascii=False)},
                })
            messages.append({"role": "assistant", "content": None,
                              "tool_calls": tool_calls_openai})
            # 每个 tool 结果
            for res in self._tool_bundle:
                payload = (res.summary if res.status == "ok"
                            else {"error": res.error})
                messages.append({
                    "role": "tool",
                    "tool_call_id": res.tool_call.tool_id,
                    "name": res.tool_call.name,
                    "content": json.dumps(payload, ensure_ascii=False),
                })

        def _stream():
            return self._client.chat.completions.create(
                model=MODEL_ROUTER, messages=messages,
                temperature=0.4, max_tokens=4096, stream=True,
            )
        stream = await asyncio.to_thread(_stream)
        # openai v1 sdk 返回 sync iterator；用线程转异步
        loop = asyncio.get_event_loop()

        def _next(it):
            try:
                return next(it)
            except StopIteration:
                return None

        it = iter(stream)
        while True:
            chunk = await loop.run_in_executor(None, _next, it)
            if chunk is None:
                break
            usage = getattr(chunk, "usage", None)
            if usage:
                self._tokens_in += usage.prompt_tokens or 0
                self._tokens_out += usage.completion_tokens or 0
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    # ────── general_finance 分支 · Gemini google_search 直答 ──────
    async def _stream_google_answer(self, query: str, history: list[dict]) -> AsyncGenerator[str, None]:
        """带 Gemini 原生 google_search 的直答（用于董事长/年报/战略/宏观类通识问题）。

        与 hermes 的 signal_monitor / push_content 保持相同用法：
            tools=[{"type": "google_search"}]
        注意：google_search 与 function calling tools 不能同用；此处专门为通识问题设计。
        """
        model = os.getenv("AGENT_MODEL_GENERAL_FINANCE",
                           os.getenv("SIGNAL_ANALYSIS_MODEL", "gemini-3.5-flash"))
        sys_prompt = SYSTEM_PROMPT + (
            "\n\n# 【本轮工作模式 · 通识 + 联网检索】\n"
            "- 这是**金融通识 / 背景解读**问题（公司治理、年报解读、公司战略、行业结构、宏观）\n"
            "- 你有 Google 搜索能力，遇到需要实时/最新事实（如最新年报数字、最近人事变动）请先搜再答\n"
            "- 回答 300-800 字，先给结论后给依据，用小圆点列出关键事实\n"
            "- 若搜索结果中有数字/日期，请标注来源（如：据 xxx 财报）\n"
            "- 结尾**必须**加一行：以上为通识分析，非投资建议，请以最新公告为准。"
        )
        messages: list[dict] = [{"role": "system", "content": sys_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": self._enrich_query(query)})

        def _stream():
            return self._client.chat.completions.create(
                model=model, messages=messages,
                tools=[{"type": "google_search"}],
                temperature=0.4, max_tokens=8192, stream=True,
            )
        try:
            stream = await asyncio.to_thread(_stream)
        except Exception as e:
            logger.warning("[orch] google_search 调用失败, 降级到纯 LLM: {}", e)
            # 降级：不带 google_search 再跑一次
            def _stream_fallback():
                return self._client.chat.completions.create(
                    model=model, messages=messages,
                    temperature=0.4, max_tokens=8192, stream=True,
                )
            stream = await asyncio.to_thread(_stream_fallback)

        loop = asyncio.get_event_loop()

        def _next(it):
            try:
                return next(it)
            except StopIteration:
                return None

        it = iter(stream)
        while True:
            chunk = await loop.run_in_executor(None, _next, it)
            if chunk is None:
                break
            usage = getattr(chunk, "usage", None)
            if usage:
                self._tokens_in += usage.prompt_tokens or 0
                self._tokens_out += usage.completion_tokens or 0
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def _template_summary(self, tool_calls: list[ToolCall]) -> str:
        """LLM 汇总失败时的模板兜底 —— 至少让用户看到工具原始结论"""
        if not self._tool_bundle:
            return "（暂时无法生成汇总，请稍后重试）"
        lines = ["以下是各专家/工具的返回结果（LLM 汇总不可用，暂用原始输出）：\n"]
        for r in self._tool_bundle:
            if r.status == "ok":
                lines.append(f"- **{r.tool_call.display_name or r.tool_call.name}**："
                              f"{json.dumps(r.summary, ensure_ascii=False)[:200]}")
            else:
                err = r.error or {}
                lines.append(f"- **{r.tool_call.display_name or r.tool_call.name}** ✗ "
                              f"{err.get('code', 'ERR')}: {err.get('message', '')}")
        return "\n".join(lines)

    # ────── fallback ──────
    async def _fallback_path(self, query: str, history: list[dict],
                              error_reason: str) -> AsyncGenerator[SSEEvent, None]:
        """function calling 全链路失败 → 关键词路由到单一 sub-agent"""
        yield self._emit("error", {
            "code": "LLM_FAILED",
            "message": f"AI 调度失败：{error_reason[:80]}；已降级到关键词路由",
            "recoverable": True, "fallback": "keyword_route",
        })
        tool_name, matched = keyword_route_to_tool(query)
        # 未注册的 fallback tool（比如 P1 只有 research）→ 兜底到 research
        if tool_name not in ToolRegistry.known_tools():
            tool_name = "research"
        tc = new_tool_call(tool_name,
                            args={"question": query} if tool_name == "research" else {},
                            ctx_code=self.stock_code,
                            ctx_user_id=self.user_id)
        yield self._emit("router_decision", {
            "reason": f"降级路径 · 关键词命中 = {matched}",
            "tool_calls": [tc.to_dict()],
        })
        yield self._emit("tool_use", tc.to_use_payload())
        res = await ToolRegistry.dispatch(tc, self.bus)
        self._tool_bundle.append(res)
        _tel.tool_result(self.user_id, self.session_id,
                          getattr(self, "_message_id", "-"),
                          tc.tool_id, tc.name, res.status, res.duration_ms,
                          error_code=(res.error or {}).get("code") if res.error else None)
        yield self._emit("tool_result", res.to_dict())

        # 生成简单文本回复（不走二次 LLM，避免又炸）
        text = self._template_summary([tc])
        self._assistant_content = text
        yield self._emit("message_delta", {"content": text})
        _tel.message_end(self.user_id, self.session_id,
                          getattr(self, "_message_id", "-"),
                          "fallback-keyword", 0, 0, 0.0, 0, 1, fallback=True)
        yield self._emit("message_end", {
            "finish_reason": "fallback",
            "usage": {"model": "fallback-keyword", "tokens_in": 0,
                      "tokens_out": 0, "cost_cny": 0.0},
        })
        await self._persist(query)

    # ────── 辅助 ──────
    def _enrich_query(self, q: str) -> str:
        if self.stock_code:
            ctx = f"【当前研究】{self.stock_name or self.stock_code}（{self.stock_code}）"
            return f"{ctx}\n\n{q}"
        return q

    def _emit(self, name: str, data: dict) -> SSEEvent:
        # 生产 SSE 前做一次 schema 校验（失败时降级为 warning，不阻塞流）
        try:
            validate_event(name, data)
        except Exception as e:
            logger.warning("[orch] SSE event {} payload 校验失败: {}", name, e)
        return SSEEvent(name=name, data=data)

    async def _persist(self, query: str) -> None:
        """异步落库到 assistant_messages.extra.agent_v2（不阻塞主流）

        **落库前过一遍合规拦截。** SYSTEM_PROMPT 里的合规块是软约束 ——
        模型大部分时候会听,但用户说"别废话直接说买不买"时容易被带偏,
        换模型时遵守程度也不一样。

        为什么拦在这里而不是流式输出时:流是一个 chunk 一个 chunk 发的,
        中途改写会让用户看到文字跳变。而**落库的这一份才是会被反复读到的**
        —— 历史记录、分享链 /p/{token}、导出报告,读的都是它。
        """
        content = self._assistant_content
        try:
            from app.services.agent import compliance_guard
            content, hits = compliance_guard.apply(
                content,
                user_id=self.user_id,
                session_id=self.session_id,
                model=MODEL_ROUTER,
            )
            if hits:
                logger.warning("[orch] session={} 合规改写 {} 处: {}",
                               self.session_id, len(hits),
                               "、".join(sorted(set(hits))[:5]))
        except Exception as e:                                # noqa: BLE001
            # 合规模块自己出错**不能拖垮落库** —— 宁可存原文,不能丢消息
            logger.warning("[orch] 合规检查异常(已跳过): {}", e)

        try:
            await asyncio.to_thread(_save_agent_v2, self.session_id, query,
                                     content, self._tool_bundle,
                                     self._router_reason,
                                     {"model": MODEL_ROUTER,
                                      "tokens_in": self._tokens_in,
                                      "tokens_out": self._tokens_out,
                                      "cost_cny": round((self._tokens_in + self._tokens_out) / 1000 * _COST_PER_1K, 4)})
        except Exception as e:
            logger.warning("[orch] persist 失败(不影响返回): {}", e)


# ─────────────────────────────── 持久化 ───────────────────────────────
def _save_agent_v2(session_id: str, user_content: str,
                    assistant_content: str, bundle: list[ToolResult],
                    router_reason: str, usage: dict) -> None:
    """写 assistant_messages 两条：user + assistant（extra.agent_v2 带 tool bundle）"""
    import json as _json
    from app.services.database import get_conn
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO assistant_messages (session_id, role, content) VALUES (%s, 'user', %s)
        """, (session_id, user_content))
        extra = {
            "agent_v2": {
                "router_reason": router_reason,
                "tool_bundle": {
                    "id": f"bundle-{uuid.uuid4().hex[:8]}",
                    "tools": [r.to_dict() for r in bundle],
                },
                "usage": usage,
            }
        }
        cur.execute("""
            INSERT INTO assistant_messages (session_id, role, content, intent, suggested_mode, extra)
            VALUES (%s, 'assistant', %s, %s, %s, %s::jsonb)
        """, (session_id, assistant_content, "agent_v2", None,
              _json.dumps(extra, ensure_ascii=False)))
        # 触碰 session 时间
        cur.execute("""
            UPDATE assistant_sessions SET last_message_at = NOW()
            WHERE id = %s
        """, (session_id,))
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────── query 分类 ───────────────────────────────
# "投资决策类"关键词 —— 命中即强制 tool_choice="required"（走 subagent）
_NEEDS_TOOL_KEYWORDS = (
    # 综合投资问（要具体买卖决策）
    "能买", "能不能买", "值不值", "值得买", "怎么样", "看看",
    # 持仓
    "还能拿", "该不该拿", "撤不撤", "止损", "持仓", "要不要卖", "拿不拿",
    # 择时
    "买点", "卖点", "什么时候买", "什么时候卖", "预测", "走势", "入场", "时机",
    # 事件影响
    "利好吗", "利空吗", "影响",
    # 一手情报
    "机构调研", "北向资金", "资金流", "主力", "大单",
    # 数据类
    "现价", "当前价", "涨了多少", "跌了多少", "成交量", "PE 分位", "pe 分位",
    # AH 溢价
    "AH 溢价", "A/H", "港股溢价", "港股折价",
    # 技术面
    "均线", "MACD", "RSI", "K 线", "关键位", "突破",
    # 板块轮动
    "板块轮动", "热点方向",
)

# "金融通识类"关键词 —— 命中即 tool_choice="none"，让主 LLM 用通识直接答
# （如"董事长变更/年报解读/公司战略/管理层"，这些非结构化决策，走 subagent 会杜撰数字）
_FINANCE_GENERAL_KEYWORDS = (
    # 公司治理
    "董事长", "总经理", "CEO", "CFO", "管理层", "高管", "换帅", "任命", "辞职",
    "离任", "变更", "股东", "大股东", "控股", "减持", "增持", "解禁", "限售",
    # 财报/公告解读
    "年报", "季报", "半年报", "中报", "财报", "财务", "业绩", "营收", "净利",
    "毛利率", "净利率", "ROE", "roe", "分红", "送股", "回购", "增发", "配股",
    # 战略/公司事件
    "战略", "并购", "收购", "重组", "剥离", "分拆", "扩张", "投产",
    "IPO", "ipo", "上市", "退市", "ST", "st", "摘帽",
    # 政策/合规
    "政策", "监管", "合规", "处罚", "调查", "立案",
    # 行业结构
    "赛道", "护城河", "竞争格局", "行业地位", "市占率",
    # 通识/背景
    "为什么", "为啥", "如何看待", "怎么看", "介绍一下", "讲讲", "背景",
    "历史", "发展", "由来", "起源", "定位",
    # 宏观
    "宏观", "美联储", "汇率", "利率", "CPI", "PPI", "GDP",
)


def _query_needs_tool(query: str) -> bool:
    """判断是否明确投资决策类问题（触发 tool_choice='required'）"""
    q = (query or "").strip().lower()
    if not q:
        return False
    return any(kw.lower() in q for kw in _NEEDS_TOOL_KEYWORDS)


def _query_is_finance_general(query: str) -> bool:
    """判断是否金融通识类问题（触发 tool_choice='none' + LLM 直答）"""
    q = (query or "").strip()
    if not q:
        return False
    return any(kw in q for kw in _FINANCE_GENERAL_KEYWORDS)


def _classify_query(query: str) -> str:
    """三分类：needs_tool > general_finance > chat（优先级降序）"""
    if _query_needs_tool(query):
        return "needs_tool"
    if _query_is_finance_general(query):
        return "general_finance"
    return "chat"


# ─────────────────────────────── 股名解析 ───────────────────────────────
# 从 query 里抽 2-4 字中文候选片段，查 stocks_catalog 找匹配
_ZH_NAME_RE = _re.compile(r"[一-鿿]{2,4}")

# 常见非股名的干扰词，避免误匹配（如"最近"、"能买"是常见问句词，非股名）
_STOP_WORDS = {
    "最近", "能买", "还能", "值得", "怎么", "什么", "现在", "今天", "明天",
    "多少", "价格", "涨了", "跌了", "板块", "行业", "分析", "研究", "预测",
    "机会", "风险", "建议", "看法", "情况", "如何", "为啥", "为什么",
    "北向", "机构", "调研", "利好", "利空", "影响", "事件", "新闻",
    "买点", "卖点", "时机", "走势", "突破", "关键", "位置",
    "投资", "股票", "个股", "持仓", "止损", "撤退",
    "均线", "MACD", "溢价", "折价",
}


async def _resolve_stock_by_name(query: str) -> dict | None:
    """从 query 里抽中文股名，查 stocks_catalog 找匹配。

    返回 {"code", "name"} 或 None。
    优先短片段（更精准）：先试 4 字→3 字→2 字。
    过滤常见问句词，避免"最近"命中"最"字股。
    """
    if not query:
        return None
    candidates = _ZH_NAME_RE.findall(query)
    if not candidates:
        return None
    # 按长度降序，长片段更可能是完整股名
    candidates = sorted(set(candidates), key=lambda x: -len(x))
    try:
        from agents.sentinel.stock_search import search as _stock_search
    except Exception as e:
        logger.warning("[orch] stock_search 导入失败: {}", e)
        return None

    for cand in candidates:
        if cand in _STOP_WORDS:
            continue
        try:
            results = await _stock_search(cand, limit=3)
        except Exception as e:
            logger.warning("[orch] stock_search({}) 失败: {}", cand, e)
            continue
        if not results:
            continue
        top = results[0]
        code = top.get("code") or ""
        name = top.get("name") or ""
        # 严格：仅当 candidate 是 name 前缀或完全等于 name 时才算命中
        # (避免 "板块" 误匹配到 "XX板块概念股")
        if cand == name or name.startswith(cand):
            return {"code": code, "name": name}
    return None


# ─────────────────────────────── 上下文切换检测 ───────────────────────────────
def _detect_new_stock_code(query: str, current: str | None) -> str | None:
    """从用户 query 里提取 6 位 A 股或 5 位港股代码，若非 current 则返回，用于切换上下文。

    只在明确出现完整代码时才切换（例如 "那 00700 呢"）。
    "茅台" / "宁德" 等文字股名不在此层识别（避免误伤）。
    """
    m = _STOCK_CODE_RE.search(query or "")
    if not m:
        return None
    code = m.group(1)
    # A 股必须以 60/68/00/30 开头，港股 5 位数字
    if len(code) == 6 and not (code.startswith(("60", "68", "00", "30", "83", "87", "43"))):
        return None
    if current and code == current:
        return None
    return code
