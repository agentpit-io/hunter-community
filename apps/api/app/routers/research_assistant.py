"""投研助手 · LLM 对话式（P1-P3 · 2026-07-24）

替代前端 intentRouter 的关键词匹配为 Gemini Flash 意图分类 + 一句话建议。
支持：
  - 单轮 chat（P1）
  - 多轮 chat（P3：加载 session 最近 4 轮作为上下文）
  - 会话列表 / 详情 / 重置 / 删除（P3）

数据表见 database.py: assistant_sessions / assistant_messages
"""
import json
import os
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from openai import OpenAI
from pydantic import BaseModel

from app.services.database import get_conn

router = APIRouter()

# 有效意图白名单 · 5 智能体 + chat（V2 新增，通用对话）
VALID_INTENTS = {"research", "scout", "kpred", "hold", "event", "chat"}

# 30 天保留策略：查询时 filter，实际数据不删（便于回查）
HISTORY_RETENTION_DAYS = 30

# LLM 单次超时（秒），超时前端降级到关键词
LLM_TIMEOUT_SEC = int(os.getenv("ASSISTANT_TIMEOUT", "20"))

# 多轮时载入的最近历史消息数（4 轮 = 8 条 user+assistant）
MAX_HISTORY_MESSAGES = 8

# V2：摘要压缩阈值（未压缩消息数超此值 → 触发异步压缩）
SUMMARY_TRIGGER_COUNT = 16

# 模型配置（走 OneAPI，可通过环境变量切换）
MODEL_ROUTE    = os.getenv("ASSISTANT_MODEL_ROUTE",    "gemini-3.8-flash")  # 意图分类主模型（快）
MODEL_CHAT     = os.getenv("ASSISTANT_MODEL_CHAT",     "gemini-3.8-flash")        # 通用对话（更强推理）
MODEL_COMPRESS = os.getenv("ASSISTANT_MODEL_COMPRESS", "gemini-3.8-flash")  # 摘要压缩（便宜）

# chat reply 上限（字符），用户要求 1200 字以给出完整方案
CHAT_REPLY_MAX_CHARS = 1200


SYSTEM_PROMPT = """你是猎鹿人 Hunter 的 AI 投研助手，帮助个人 A 股投资者做研究决策。

【任务边界 · 严格执行】
只回答与投资、股票、公司经营、行业分析、宏观经济相关的问题。
其他话题（天气、编程、闲聊、生活）一律礼貌拒绝：
  reply="我是猎鹿人投研助手，主要陪你研究股票和市场。这个话题我不太擅长，换个投资问题吧～"
  intent="chat"

【猎鹿人 5 位专业智能体】
- research（📊 深度研究）：AI 分析师 3 分钟建立完整认知（基本面 + 技术面 + Kronos 预测 + 分类信号）
- scout（🔍 一手情报）：实时采集机构调研、研发扩张、北向资金、AI 搜索，Gemini 汇总
- kpred（🎯 量化择时）：清华 Kronos 金融大模型预测 5/10/20 日走势，5 档评级
- hold（🛡 持仓研判）：Bull vs Bear 3 轮辩论 + 综合裁判 + 风险裁判 → BUY/HOLD/SELL
- event（⚡ 事件解读）：突发事件对该股的影响分析

【意图分类 · 两种类型】

  === 类型 A：能路由到专业智能体 ===
  - 该股能不能拿 / 该不该继续持有 → intent=hold
  - 什么时候买/卖 / 找入场时机 → intent=kpred
  - 最近新闻/事件/公告/利好利空 → intent=event
  - 机构调研/北向/主力动向 → intent=scout
  - 值不值得研究 / 基本面 / 综合了解 → intent=research
  reply 100-200 字，简短建议为主，主推那个智能体
  suggested_action 填该智能体信息 + additional_actions 填 1-2 个相关次选

  === 类型 B：开放性/探讨性问题 · intent="chat" ===
  - "AI/大模型会不会替代 XX 行业/公司"
  - "你怎么看 XX 板块/公司/行业"
  - "宏观经济/政策/汇率对该股影响"
  - "十年后 XX 会怎样 / 长期赛道判断"
  - "两只股票哪个更好"（跨对比）
  - 用户想深入探讨、追问、辩论
  reply 400-1200 字，深度回答（结构：核心观点 + 论据 1/2/3 + 实用建议）
  suggested_action=null；additional_actions=[]

【示例】
Q: "茅台还能拿吗" → {"intent":"hold", ...}
Q: "什么时候买入好" → {"intent":"kpred", ...}
Q: "最近有啥利好" → {"intent":"event", ...}
Q: "机构持仓多少" → {"intent":"scout", ...}
Q: "综合了解一下这只股" → {"intent":"research", ...}
Q: "用友软件会被 AI 大模型替代吗" → {"intent":"chat", "reply":"用友作为国内 ERP 龙头..."}
Q: "长期赛道你怎么看" → {"intent":"chat", "reply":"..."}
Q: "宏观降息对该股影响" → {"intent":"chat", "reply":"..."}
Q: "两只股票哪个更好" → {"intent":"chat", "reply":"..."}
Q: "今天天气如何" → {"intent":"chat","reply":"我是猎鹿人投研助手，主要陪你研究股票和市场..."}

【类型 B 回答结构（推荐）】
【核心观点】1 句话结论
【论据 1】数据/事实/逻辑
【论据 2】...
【论据 3】...（可选）
【实用建议】一句话具体行动

【回答约束通用】
- 全程中文，用户视角，避免专业术语堆砌
- 若用户没提供股票 → reply 里友好提示先选一只
- 若问题过于模糊 → needs_more_info=true，reply 包含 1 个反问
- 若明确希望"综合分析" → meta_agent_hint=true
- 严禁具体价格预测（"茅台会涨到 XXX 元"），可以说趋势方向

【严格 JSON 输出】
{
  "intent": "research|scout|kpred|hold|event|chat",
  "reply": "...",
  "suggested_action": null | {"mode":"...","label":"...","reason":"..."},
  "additional_actions": [{"mode":"...","label":"...","reason":"..."}],
  "confidence": 0-1,
  "needs_more_info": false,
  "meta_agent_hint": false
}"""


# ── Pydantic 请求体 ─────────────────────────────────────────────

class ChatBody(BaseModel):
    query: str
    stock_code: str | None = None
    stock_name: str | None = None
    session_id: str | None = None
    context: dict | None = None  # {has_thesis, in_watchlist}


# ── 内部辅助函数 ───────────────────────────────────────────────

def _build_user_msg(query: str, stock_code: str | None, stock_name: str | None,
                    context: dict | None) -> str:
    """把 query 拼上股票上下文，让 LLM 更有针对性"""
    if not stock_code:
        return f"【用户问题】\n{query}\n\n（用户当前未选股票，请引导先选一只）"

    ctx_lines = [f"当前研究：{stock_name or stock_code}（{stock_code}）"]
    if context:
        if context.get("has_thesis"): ctx_lines.append("- 用户已录入持仓逻辑")
        if context.get("in_watchlist"): ctx_lines.append("- 该股在用户自选股中")
    return f"【上下文】\n" + "\n".join(ctx_lines) + f"\n\n【用户问题】\n{query}"


def _looks_like_chat_question(q: str, has_history: bool = False) -> bool:
    """启发式：问题很可能是开放性 chat（用 gemini-3.5-flash 深度回答）

    规则（任一命中即为 chat）：
      1. 已有历史消息 → 追问模式，保持用 chat 模型（延续上文语境）
      2. query 长度 > 30 字 → 复杂问题
      3. 含开放/对比/宏观类关键词
    """
    if has_history:  # 追问自动继承 chat 模式
        return True
    if len(q) > 30:
        return True
    chat_kws = ["怎么看", "你觉得", "会不会", "是否", "为啥", "为什么",
                "对比", "哪个", "十年", "长期", "宏观", "赛道", "护城河",
                "替代", "颠覆", "讨论", "谈谈", "讲讲", "分析下", "情况呢",
                "如何", "什么样", "看看", "怎样"]
    return any(kw in q for kw in chat_kws)


def _parse_llm_json(raw: str) -> dict:
    """容错解析 LLM JSON 输出。
    Gemini 3.5-flash 有时会带 ```json ... ``` markdown 包裹或前后加解释文字。
    """
    import re
    if not raw:
        raise ValueError("empty LLM output")
    text = raw.strip()

    # 1. 剥离 markdown 代码块 ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        text = m.group(1)
    else:
        # 2. 提取第一个 { 到最后一个 } 之间的 JSON（去掉前后解释文本）
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            text = text[first:last + 1]

    return json.loads(text)


def _call_llm(messages: list[dict], model: str, max_tokens: int = 1200,
              allow_fallback: bool = True) -> dict:
    """调 Gemini · 结构化 JSON 输出。
    双向降级：模型 A JSON 解析失败 → 换模型 B 重试；两个都失败才抛异常"""
    api_key = os.getenv("ONE_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "LLM 未配置（ONE_API_KEY 缺失）")
    base_url = os.getenv("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1")
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=LLM_TIMEOUT_SEC)

    def _do(m: str, mt: int) -> str:
        resp = client.chat.completions.create(
            model=m, messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3, max_tokens=mt,
        )
        return resp.choices[0].message.content or ""

    raw = _do(model, max_tokens)
    try:
        return _parse_llm_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        if not allow_fallback:
            raise
        # 双向降级：chat 模型失败 → 用 route；route 失败 → 用 chat（因两者 JSON mode 都不 100% 稳定）
        alt_model = MODEL_ROUTE if model == MODEL_CHAT else MODEL_CHAT
        logger.warning("[assistant] {} 输出非 JSON: {} · 降级到 {} 重试",
                       model, str(e)[:80], alt_model)
        try:
            raw2 = _do(alt_model, max_tokens)
            return _parse_llm_json(raw2)
        except (json.JSONDecodeError, ValueError) as e2:
            logger.warning("[assistant] 降级 {} 也失败: {} · 抛出", alt_model, str(e2)[:80])
            raise


def _call_llm_plain(messages: list[dict], model: str, max_tokens: int = 800) -> str:
    """调 Gemini · 纯文本输出（用于摘要压缩，不需 JSON）"""
    api_key = os.getenv("ONE_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "LLM 未配置")
    base_url = os.getenv("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1")
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=LLM_TIMEOUT_SEC)
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=0.2, max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def _sanitize_llm_output(data: dict) -> dict:
    """LLM 输出白名单校验 + 兜底"""
    # intent 校验（未识别 → 兜到 chat，比强行分类更保守）
    if data.get("intent") not in VALID_INTENTS:
        data["intent"] = "chat"

    intent = data["intent"]

    if intent == "chat":
        # chat 意图：无路由 CTA
        data["suggested_action"] = None
        data["additional_actions"] = []
    else:
        # 5 智能体意图：suggested_action 白名单
        sa = data.get("suggested_action") or {}
        # 智能体路由目标只能是 5 智能体之一（chat 不能作路由目标）
        route_targets = VALID_INTENTS - {"chat"}
        if sa.get("mode") not in route_targets:
            data["suggested_action"] = {
                "mode": intent,
                "label": "去做深度研究",
                "reason": "先建立完整认知",
            }
        aa = data.get("additional_actions") or []
        data["additional_actions"] = [
            a for a in aa if isinstance(a, dict) and a.get("mode") in route_targets
        ][:3]

    # reply 长度限制（chat 上限 800 字，其他类型 500 字）
    reply = str(data.get("reply") or "")
    max_chars = CHAT_REPLY_MAX_CHARS if intent == "chat" else 500
    if len(reply) > max_chars:
        reply = reply[:max_chars] + "…"
    data["reply"] = reply

    # 其他布尔
    data["confidence"] = float(data.get("confidence") or 0.7)
    data["needs_more_info"] = bool(data.get("needs_more_info", False))
    data["meta_agent_hint"] = bool(data.get("meta_agent_hint", False))
    return data


# ── DB 操作 ────────────────────────────────────────────────────

def _new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:16]}"


def _get_session(session_id: str, user_id: str) -> dict | None:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT id, focus_stock_code, focus_stock_name, message_count, last_message_at
        FROM assistant_sessions WHERE id = %s AND user_id = %s
    """, (session_id, user_id))
    row = cur.fetchone()
    conn.close()
    if not row: return None
    return {
        "id": row[0], "focus_stock_code": row[1], "focus_stock_name": row[2],
        "message_count": row[3], "last_message_at": row[4],
    }


def _create_session(user_id: str, stock_code: str, stock_name: str) -> str:
    sid = _new_session_id()
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO assistant_sessions (id, user_id, focus_stock_code, focus_stock_name)
        VALUES (%s, %s, %s, %s)
    """, (sid, user_id, stock_code or "", stock_name or ""))
    conn.commit(); conn.close()
    return sid


def _touch_session(session_id: str) -> None:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE assistant_sessions
        SET message_count = message_count + 2,
            last_message_at = NOW(), updated_at = NOW()
        WHERE id = %s
    """, (session_id,))
    conn.commit(); conn.close()


def _save_message_pair(session_id: str, user_content: str, llm_output: dict) -> None:
    """一次插入 user + assistant 两条消息"""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO assistant_messages (session_id, role, content) VALUES (%s, 'user', %s)
    """, (session_id, user_content))
    cur.execute("""
        INSERT INTO assistant_messages (session_id, role, content, intent, suggested_mode, extra)
        VALUES (%s, 'assistant', %s, %s, %s, %s::jsonb)
    """, (
        session_id,
        llm_output.get("reply", ""),
        llm_output.get("intent"),
        (llm_output.get("suggested_action") or {}).get("mode"),
        json.dumps(llm_output, ensure_ascii=False),
    ))
    conn.commit(); conn.close()


def _load_history(session_id: str, limit: int = MAX_HISTORY_MESSAGES) -> list[dict]:
    """加载最近 N 条消息（按时间正序返回，供 LLM history）"""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT role, content FROM assistant_messages
        WHERE session_id = %s ORDER BY created_at DESC LIMIT %s
    """, (session_id, limit))
    rows = cur.fetchall()
    conn.close()
    # 倒序 → 正序（时间从早到晚）
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


def _load_history_with_summary(session_id: str) -> tuple[str, list[dict]]:
    """V2 多轮：返回 (summary_text, recent_messages)。
    只加载 summary_until_message_id 之后的消息作为滑窗；早期消息在 summary 里"""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT summary, summary_until_message_id FROM assistant_sessions WHERE id = %s", (session_id,))
    row = cur.fetchone()
    summary = row[0] if row else ""
    until_id = row[1] if row else 0

    cur.execute("""
        SELECT role, content FROM assistant_messages
        WHERE session_id = %s AND id > %s
        ORDER BY created_at DESC LIMIT %s
    """, (session_id, until_id, MAX_HISTORY_MESSAGES))
    rows = cur.fetchall()
    conn.close()
    history = [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    return summary, history


def _maybe_compress_history(session_id: str) -> None:
    """V2 多轮压缩：未压缩消息数 > 阈值 → LLM 压成 200-500 字 summary。
    同步执行；调用方用 asyncio.create_task 后台运行避免阻塞"""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT summary, summary_until_message_id FROM assistant_sessions WHERE id = %s", (session_id,))
    row = cur.fetchone()
    if not row:
        conn.close(); return
    prev_summary, until = row[0] or "", row[1] or 0

    # 统计未压缩消息数
    cur.execute("SELECT COUNT(*) FROM assistant_messages WHERE session_id = %s AND id > %s",
                (session_id, until))
    unpressed = cur.fetchone()[0] or 0
    if unpressed < SUMMARY_TRIGGER_COUNT:
        conn.close(); return

    # 取要压缩的消息（超出滑窗的部分）
    to_compress_limit = unpressed - MAX_HISTORY_MESSAGES
    if to_compress_limit <= 0:
        conn.close(); return
    cur.execute("""
        SELECT id, role, content FROM assistant_messages
        WHERE session_id = %s AND id > %s
        ORDER BY created_at LIMIT %s
    """, (session_id, until, to_compress_limit))
    old_msgs = cur.fetchall()
    conn.close()
    if not old_msgs:
        return
    last_id = old_msgs[-1][0]

    dialog_text = "\n".join([f"[{r[1]}] {r[2]}" for r in old_msgs])
    prompt_prefix = (f"【已有摘要】\n{prev_summary}\n\n" if prev_summary else "")
    compress_prompt = f"""{prompt_prefix}【新增对话】
{dialog_text}

请把上述内容压缩为 200-500 字的中文要点摘要，保留：
- 用户关注的股票（代码 + 名字）
- 用户明确表达过的立场/担忧/偏好
- AI 给出过的关键建议 / 用户点过的智能体
- 有价值的探讨性观点

要点用客观陈述句，不用"用户说 X"这种叙述框架。"""

    try:
        new_summary = _call_llm_plain(
            [{"role": "user", "content": compress_prompt}],
            model=MODEL_COMPRESS, max_tokens=800,
        )
    except Exception as e:
        logger.warning("[assistant] 压缩失败 session={} err={}", session_id, e)
        return

    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        UPDATE assistant_sessions
        SET summary = %s, summary_until_message_id = %s, updated_at = NOW()
        WHERE id = %s
    """, (new_summary.strip(), last_id, session_id))
    conn.commit(); conn.close()
    logger.info("[assistant] 压缩完成 session={} compressed={} messages", session_id, len(old_msgs))


# ── API 端点 ───────────────────────────────────────────────────

@router.post("/research-assistant/chat")
async def chat(body: ChatBody, request: Request):
    """核心对话接口。支持单轮 + 多轮（传 session_id 时载入历史）"""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    q = (body.query or "").strip()
    if not q:
        raise HTTPException(400, "请输入你的问题")
    if len(q) > 500:
        raise HTTPException(400, "问题过长，请精简")

    # ── session 处理 ──
    session_id = body.session_id
    if session_id:
        sess = _get_session(session_id, user_id)
        if not sess:
            # 无效 session → 新建
            session_id = _create_session(user_id, body.stock_code or "", body.stock_name or "")
        # 若切了股 → 隐式续用同一 session（前端语义决定何时"重置"或"新建"）
    else:
        session_id = _create_session(user_id, body.stock_code or "", body.stock_name or "")

    # ── 构造 LLM messages（V2：融合 summary + 滑窗历史）──
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    summary, history = _load_history_with_summary(session_id)
    if summary:
        messages.append({
            "role": "system",
            "content": f"【本会话早期对话摘要 · 供你保持上下文】\n{summary}",
        })
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})

    # 拼当前 user query（带股票上下文）
    user_msg = _build_user_msg(q, body.stock_code, body.stock_name, body.context)
    messages.append({"role": "user", "content": user_msg})

    # V2：启发式选择模型 · chat 类问题（含追问）用更强的 gemini-3.5-flash
    has_history = len(history) > 0 or bool(summary)
    is_chat = _looks_like_chat_question(q, has_history=has_history)
    model_used = MODEL_CHAT if is_chat else MODEL_ROUTE
    # chat 意图需要更大 max_tokens 装 1200 字中文回答（约 2500-3500 tokens）+ JSON 字段
    max_tokens = 3500 if is_chat else 800

    logger.info("[assistant] 调 LLM model={} chat_mode={} has_history={} q={}...",
                model_used, is_chat, has_history, q[:30])

    # ── 调 LLM ──
    try:
        llm_output = _call_llm(messages, model=model_used, max_tokens=max_tokens)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("[assistant] LLM 两次调用均失败 user={} model={} q={}... err={}",
                       user_id, model_used, q[:30], str(e)[:120])
        # 兜底：构造一个 chat 意图的默认响应，避免 502 让前端触发关键词降级
        llm_output = {
            "intent": "chat",
            "reply": ("抱歉，我刚才思考的时候被卡住了 🙏\n\n"
                      "可能是问题比较复杂或者服务繁忙。你可以：\n"
                      "1. 稍等片刻后重试同一个问题\n"
                      "2. 把问题拆得更具体一些再问\n"
                      "3. 点下方任一专家（📊 深度研究 / 🔍 一手情报 / 🎯 量化择时 / "
                      "🛡 持仓研判 / ⚡ 事件解读）继续研究"),
            "suggested_action": None,
            "additional_actions": [],
            "confidence": 0.0,
            "needs_more_info": False,
            "meta_agent_hint": False,
            "model_used": model_used,
            "llm_failed": True,
        }

    llm_output = _sanitize_llm_output(llm_output)

    # ── 持久化 ──
    try:
        _save_message_pair(session_id, q, llm_output)
        _touch_session(session_id)
        if llm_output.get("intent") == "chat":
            # chat 计数（用于观察通用对话占比）
            conn = get_conn(); cur = conn.cursor()
            cur.execute("UPDATE assistant_sessions SET chat_mode_count = chat_mode_count + 1 WHERE id = %s",
                        (session_id,))
            conn.commit(); conn.close()
    except Exception as e:
        logger.warning("[assistant] 消息持久化失败（不影响返回）err={}", e)

    # ── V2：达阈值时触发异步摘要压缩（不阻塞返回）──
    import asyncio as _asyncio
    _asyncio.create_task(_asyncio.to_thread(_maybe_compress_history, session_id))

    return {"session_id": session_id, "model_used": model_used, **llm_output}


@router.get("/research-assistant/sessions")
async def list_sessions(request: Request, limit: int = 30):
    """当前用户的会话列表（不含消息，用于历史入口）· 30 天内"""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")

    cutoff = datetime.now() - timedelta(days=HISTORY_RETENTION_DAYS)
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT id, focus_stock_code, focus_stock_name, message_count, last_message_at
        FROM assistant_sessions
        WHERE user_id = %s AND last_message_at >= %s AND message_count > 0
        ORDER BY last_message_at DESC LIMIT %s
    """, (user_id, cutoff, limit))
    rows = cur.fetchall()
    conn.close()

    items = [{
        "id": r[0],
        "focus_stock_code": r[1],
        "focus_stock_name": r[2],
        "message_count": r[3],
        "last_message_at": r[4].isoformat() if r[4] else None,
    } for r in rows]
    return {"items": items, "count": len(items)}


@router.get("/research-assistant/session/{session_id}")
async def get_session_detail(session_id: str, request: Request):
    """单会话完整消息（对话回显）"""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    sess = _get_session(session_id, user_id)
    if not sess:
        raise HTTPException(404, "会话不存在")

    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT role, content, intent, suggested_mode, extra, created_at
        FROM assistant_messages
        WHERE session_id = %s ORDER BY created_at
    """, (session_id,))
    rows = cur.fetchall()
    conn.close()

    messages = [{
        "role": r[0], "content": r[1], "intent": r[2], "suggested_mode": r[3],
        "extra": r[4], "created_at": r[5].isoformat() if r[5] else None,
    } for r in rows]

    return {
        "session": sess,
        "messages": messages,
    }


@router.post("/research-assistant/session/{session_id}/reset")
async def reset_session(session_id: str, request: Request):
    """重置对话：删除该 session 所有消息但保留 session 本身（focus 股票不变）"""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    sess = _get_session(session_id, user_id)
    if not sess:
        raise HTTPException(404, "会话不存在")

    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM assistant_messages WHERE session_id = %s", (session_id,))
    cur.execute("""
        UPDATE assistant_sessions
        SET message_count = 0, updated_at = NOW()
        WHERE id = %s
    """, (session_id,))
    conn.commit(); conn.close()
    return {"ok": True, "session_id": session_id}


@router.delete("/research-assistant/session/{session_id}")
async def delete_session(session_id: str, request: Request):
    """彻底删除会话 + 所有消息（用户主动清理）"""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    sess = _get_session(session_id, user_id)
    if not sess:
        raise HTTPException(404, "会话不存在")

    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM assistant_sessions WHERE id = %s", (session_id,))
    conn.commit(); conn.close()
    return {"ok": True}
