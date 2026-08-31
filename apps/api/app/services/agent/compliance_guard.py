"""合规兜底 · 输出侧硬拦截。

方案见 doc/开源hunter-community/04开源比赛/
      2026-08-31_真生产化技术方案与开发计划.md §2.6

## 为什么 SYSTEM_PROMPT 不够

prompt 是**软约束** —— 模型大部分时候会听,但:
  · 用户说"别废话直接告诉我买不买"时容易被带偏
  · 换模型(gemini / deepseek / 本地模型)时遵守程度不一样
  · 长对话到后面,前面的约束权重会衰减

合规是持牌门槛问题,**不能只靠模型自觉**。所以这里在输出侧再拦一道:
命中黑名单就改写,不管模型当时怎么想的。

## 设计取舍:改写而不是拒答

发现违规词的处理有三种:
  ① 整条拒答      —— 用户什么都拿不到,体验最差,而且他会换个说法再问
  ② 原样放行 + 警告 —— 违规内容还是发出去了,等于没拦
  ③ **改写违规词 + 补免责**  ← 选这个

改写保留了回答的有用部分,只把"必涨"这种话术拿掉。
用户拿到的仍是完整分析,只是不再有绝对化承诺。

## 不做的事

**不做语义级判断。** 只匹配明确的绝对化词汇和仓位数字 ——
"这只票基本面很好"该不该算违规,是个见仁见智的判断,
交给正则会误伤大量正常表达。宁可漏,不可滥。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

log = logging.getLogger(__name__)

# ── 灰度开关 · 生产出问题可立即关 ─────────────────────────
# - strict:改写 + 打标 + 追加免责 · 默认
# - permissive:只追加免责 · 不改写敏感词 · 温和推出用
# - off:完全关闭 · 灾备
COMPLIANCE_MODE = os.getenv("HUNTER_COMPLIANCE_MODE", "strict").lower()

# ── 绝对化用词 · 命中就改写 ──────────────────────────────
# 每条 (正则, 替换成什么)。替换词保留原意的"方向",只去掉"确定性"
_REWRITE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"必涨|一定涨|肯定涨|铁定涨|稳涨"), "偏多"),
    (re.compile(r"必跌|一定跌|肯定跌|铁定跌|稳跌"), "偏空"),
    (re.compile(r"稳赚|保证收益|保本保收益|无风险套利|零风险"), "潜在收益(不保证)"),
    (re.compile(r"无风险"), "风险相对较低"),
    (re.compile(r"闭眼买|闭着眼买|无脑买|梭哈|全仓杀入"), "可关注"),
    (re.compile(r"抄底"), "逢低关注"),
    (re.compile(r"翻倍(?!的可能|概率)"), "大幅上涨"),
]

# ── 具体仓位数字 · 改成方向性表述 ────────────────────────
# 「仓位 60%」「建议 30% 仓位」「满仓」「半仓」
_POSITION = [
    (re.compile(r"(仓位|持仓|配置)\s*(达到|到|设为|设置为|建议)?\s*\d{1,3}\s*%"), "适度配置"),
    (re.compile(r"\d{1,3}\s*%\s*(仓位|持仓|资金)"), "适度配置"),
    (re.compile(r"满仓|重仓杀入|全仓"), "超配"),
    (re.compile(r"半仓"), "标配"),
    (re.compile(r"空仓"), "观望"),
]

# ── 需要免责的信号词 ─────────────────────────────────────
_ADVICE = re.compile(r"建议|推荐|应该买|应该卖|值得(买|持有|关注)|可以考虑|不妨")

DISCLAIMER = "\n\n> 以上基于公开数据分析,仅供研究参考,不构成投资建议。"


def scan(text: str) -> list[str]:
    """只检不改 —— 给日志和监控用,不改变输出。"""
    hits = []
    for pat, _ in _REWRITE + _POSITION:
        for m in pat.finditer(text or ""):
            hits.append(m.group(0))
    return hits


def apply(
    text: str,
    *,
    add_disclaimer: bool = True,
    user_id=None,
    session_id: str | None = None,
    model: str | None = None,
) -> tuple[str, list[str]]:
    """改写违规表述 + 按需补免责。返回 (处理后文本, 命中的词)。

    **不拒答、不截断** —— 见模块头的取舍说明。

    **调用点**:由 `orchestrator._persist()` 在落库前调用(assistant 回答
    最终定稿、进 assistant_messages 之前),见该函数 docstring 的取舍说明。

    灰度三档(`HUNTER_COMPLIANCE_MODE`):
      · off —— 完全放行,原文返回(灾备)
      · permissive —— 只补免责,不改写敏感词(温和推出)
      · strict —— 改写 + 打标 + 补免责 + 落 violation 日志(默认)

    `user_id / session_id / model` 仅供 strict 命中时写 violation 日志,
    调用方不传也不影响改写本身。
    """
    if not text:
        return text, []

    # ① off · 灾备 —— 一个字都不动
    if COMPLIANCE_MODE == "off":
        return text, []

    # ② permissive · 只补免责,跳过全部 replace/rewrite
    if COMPLIANCE_MODE == "permissive":
        out = text
        if add_disclaimer and _ADVICE.search(out) and "不构成投资建议" not in out:
            out += DISCLAIMER
        return out, []

    # ③ strict(默认)· 全走
    hits = scan(text)
    out = text
    for pat, rep in _REWRITE:
        out = pat.sub(rep, out)
    for pat, rep in _POSITION:
        out = pat.sub(rep, out)

    if hits:
        # 命中要能查 —— 静默改写的话,以后想知道"模型多久违规一次"没有数据
        log.warning("[compliance] 改写了 %d 处绝对化表述: %s",
                    len(hits), "、".join(sorted(set(hits))[:5]))
        # 落库供后续 fine-tune 数据 + metrics · 失败不阻塞主流程
        _log_violation(user_id, session_id, hits, text, out, model)

    if add_disclaimer and _ADVICE.search(out) and "不构成投资建议" not in out:
        out += DISCLAIMER

    return out, hits


def _log_violation(user_id, session_id, violations, original, fixed, model):
    """写 compliance_violation_log 表 · 供后续 fine-tune 数据 + metrics。

    复用 `app.services.database.get_conn`(psycopg2 · DATABASE_URL)。
    写库失败**绝不阻塞主流程** —— 合规改写本身已完成,日志丢了就丢了。
    若当前在事件循环里,丢线程池 fire-and-forget,避免阻塞;否则同步写。
    """
    def _write():
        try:
            from app.services.database import get_conn
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO compliance_violation_log
                            (user_id, session_id, violations,
                             original_text, fixed_text, model, mode)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            user_id,
                            session_id,
                            list(violations or []),
                            (original or "")[:2000],
                            (fixed or "")[:2000],
                            model,
                            COMPLIANCE_MODE,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:                                  # noqa: BLE001
            log.warning("[compliance] violation 落库失败(已忽略): %s", e)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _write()                     # 无事件循环 · 同步写
    else:
        loop.run_in_executor(None, _write)   # fire-and-forget · 不阻塞事件循环
