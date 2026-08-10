"""AdventureX 展位活动服务层。

职责：
  - ax_event 表读写（两级奖励状态机）
  - 核销码 / 初始密码生成
  - 账号密码邮件发送（Resend SMTP，与 agentpit 主站同账号）
"""
import json
import os
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from loguru import logger

from app.services.database import get_conn

CST = timezone(timedelta(hours=8))

# 生成密码/核销码的字符集：去掉 0O1Il 等易混淆字符
_PW_ALPHABET   = "abcdefghjkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_ALPHABET = "ACDEFGHJKLMNPQRSTUVWXYZ23456789"

# ── Resend SMTP（与 agentpit 主站 lib/email.ts 同一账号） ─────────────────────
AX_SMTP_HOST = os.getenv("AX_SMTP_HOST", "smtp.resend.com")
AX_SMTP_PORT = int(os.getenv("AX_SMTP_PORT", "465"))
AX_SMTP_USER = os.getenv("AX_SMTP_USER", "resend")
AX_SMTP_PASS = os.getenv("AX_SMTP_PASS", "")
AX_MAIL_FROM = os.getenv("AX_MAIL_FROM", "hello@agentpit.io")
AX_MAIL_FROM_NAME = os.getenv("AX_MAIL_FROM_NAME", "猎鹿人 Hunter")


def gen_password(length: int = 8) -> str:
    return "".join(secrets.choice(_PW_ALPHABET) for _ in range(length))


def gen_redeem_code() -> str:
    """生成唯一核销码 AX-XXXX（查表防碰撞）。"""
    conn = get_conn()
    cur = conn.cursor()
    for _ in range(20):
        code = "AX-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
        cur.execute(
            "SELECT 1 FROM ax_event WHERE level1_code = %s OR level2_code = %s",
            (code, code),
        )
        if not cur.fetchone():
            conn.close()
            return code
    conn.close()
    # 20 次都碰撞（几乎不可能），退化为 6 位
    return "AX-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


# ── ax_event 行读写 ───────────────────────────────────────────────────────────

_ROW_FIELDS = (
    "openid, user_id, email, nickname, registered_at, "
    "level1_stock_code, level1_stock_name, level1_report_id, level1_done_at, "
    "level1_code, level1_redeemed_at, level1_redeemed_by, "
    "level2_positions, level2_done_at, level2_code, level2_redeemed_at, level2_redeemed_by, "
    "member_months, member_expires_at, "
    "features_used, features_updated_at, level1_email_sent_at, unlock_track, "
    "created_at, updated_at"
)


def _row_to_dict(row) -> dict:
    keys = [k.strip() for k in _ROW_FIELDS.split(",")]
    d = dict(zip(keys, row))
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.astimezone(CST).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(d.get("level2_positions"), str):
        d["level2_positions"] = json.loads(d["level2_positions"])
    if isinstance(d.get("features_used"), str):
        d["features_used"] = json.loads(d["features_used"])
    return d


def get_ax_row(openid: str = "", user_id: str = "") -> dict | None:
    conn = get_conn()
    cur = conn.cursor()
    if openid:
        cur.execute(f"SELECT {_ROW_FIELDS} FROM ax_event WHERE openid = %s", (openid,))
    else:
        cur.execute(
            f"SELECT {_ROW_FIELDS} FROM ax_event WHERE user_id = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def upsert_registration(openid: str, user_id: str, email: str, nickname: str) -> None:
    """注册完成（或老用户首次进入活动）时落一行。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO ax_event (openid, user_id, email, nickname, registered_at)
           VALUES (%s, %s, %s, %s, NOW())
           ON CONFLICT (openid) DO UPDATE SET
               user_id = EXCLUDED.user_id,
               email   = EXCLUDED.email,
               nickname = CASE WHEN EXCLUDED.nickname <> '' THEN EXCLUDED.nickname ELSE ax_event.nickname END,
               registered_at = COALESCE(ax_event.registered_at, NOW()),
               updated_at = NOW()""",
        (openid, user_id, email, nickname),
    )
    conn.commit()
    conn.close()


def complete_level1(openid: str, stock_code: str, stock_name: str,
                    report_id: int | None) -> dict:
    """第一级完成（通关送礼）：生成核销码 + 立即开 3 个月 pro 会员（幂等：已完成直接返回原码）。"""
    row = get_ax_row(openid=openid)
    if row and row["level1_code"]:
        return row
    code = gen_redeem_code()
    expires = datetime.now(CST) + timedelta(days=90)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE ax_event SET
               level1_stock_code = %s, level1_stock_name = %s, level1_report_id = %s,
               level1_done_at = NOW(), level1_code = %s,
               member_months = GREATEST(member_months, 3),
               member_expires_at = GREATEST(COALESCE(member_expires_at, %s), %s),
               updated_at = NOW()
           WHERE openid = %s""",
        (stock_code, stock_name, report_id, code, expires, expires, openid),
    )
    conn.commit()
    conn.close()
    return get_ax_row(openid=openid)


def complete_level2_by_features(openid: str) -> dict:
    """V4 二级完成（新轨道 features）：无持仓，直接发核销码 + 3 个月会员。
    幂等：已有 level2_code 直接返回。
    """
    row = get_ax_row(openid=openid)
    if row and row["level2_code"]:
        return row
    code = gen_redeem_code()
    expires = datetime.now(CST) + timedelta(days=90)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE ax_event SET
               level2_done_at = COALESCE(level2_done_at, NOW()),
               level2_code = %s,
               member_months = GREATEST(member_months, 3),
               member_expires_at = GREATEST(COALESCE(member_expires_at, %s), %s),
               unlock_track = 'features',
               updated_at = NOW()
           WHERE openid = %s""",
        (code, expires, expires, openid),
    )
    conn.commit()
    conn.close()
    return get_ax_row(openid=openid)


# ── 功能体验追踪（V4）─────────────────────────────────────────────────────────

def add_feature_used(openid: str, feature_id: str) -> tuple[int, bool]:
    """把 feature_id 加入 features_used（去重）。
    返回 (assigned_used_count, unlocked_now) — 只统计"分配集合内"已完成的数量。
    unlocked_now 表示本次调用是否恰好达阈（4 项全部完成）。
    """
    from app.services.ax_features import (
        AX_FEATURE_IDS, AX_UNLOCK_THRESHOLD, get_assigned_ids,
    )
    if feature_id not in AX_FEATURE_IDS:
        return 0, False
    row = get_ax_row(openid=openid)
    if not row:
        return 0, False
    used = row.get("features_used") or []
    if feature_id not in used:
        used.append(feature_id)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """UPDATE ax_event SET
                   features_used = %s::jsonb,
                   features_updated_at = NOW(),
                   updated_at = NOW()
               WHERE openid = %s""",
            (json.dumps(used), openid),
        )
        conn.commit()
        conn.close()
    assigned = get_assigned_ids(openid)
    used_in_assigned = len(set(used) & assigned)
    unlocked_now = used_in_assigned >= AX_UNLOCK_THRESHOLD and not row.get("level2_code")
    return used_in_assigned, unlocked_now


def get_features_summary(openid: str) -> dict:
    """给前端 /api/ax/features 用：该用户被分配的 4 项任务清单 + 已体验标记 + 进度。"""
    from app.services.ax_features import (
        AX_UNLOCK_THRESHOLD, get_assigned_features,
    )
    row = get_ax_row(openid=openid)
    used = set((row.get("features_used") or []) if row else [])
    assigned = get_assigned_features(openid)
    assigned_ids = {f["id"] for f in assigned}
    items = [{**f, "used": f["id"] in used} for f in assigned]
    return {
        "features": items,
        "used_count": len(used & assigned_ids),
        "total_count": AX_UNLOCK_THRESHOLD,
        "unlocked": bool(row and row.get("level2_code")),
        "level2_code": (row or {}).get("level2_code", ""),
        "level2_redeemed_at": (row or {}).get("level2_redeemed_at"),
    }


def mark_level1_email_sent(openid: str) -> None:
    """标记分析报告邮件已发送（防重发）。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE ax_event SET level1_email_sent_at = NOW(), updated_at = NOW() "
        "WHERE openid = %s AND level1_email_sent_at IS NULL",
        (openid,),
    )
    conn.commit()
    conn.close()


def complete_level2(openid: str, positions: list[dict]) -> dict:
    """第二级完成：记录持仓 + 核销码 + 会员升 3 个月（取高不叠加）。

    幂等：已有 level2_code 时不重复发码，但持仓列表合并更新。
    """
    row = get_ax_row(openid=openid)
    already = bool(row and row["level2_code"])
    code = row["level2_code"] if already else gen_redeem_code()
    expires = datetime.now(CST) + timedelta(days=90)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE ax_event SET
               level2_positions = %s,
               level2_done_at = COALESCE(level2_done_at, NOW()),
               level2_code = %s,
               member_months = GREATEST(member_months, 3),
               member_expires_at = GREATEST(COALESCE(member_expires_at, %s), %s),
               updated_at = NOW()
           WHERE openid = %s""",
        (json.dumps(positions, ensure_ascii=False), code, expires, expires, openid),
    )
    conn.commit()
    conn.close()
    return get_ax_row(openid=openid)


# ── 展位后台（工作人员） ──────────────────────────────────────────────────────

def redeem_code(code: str, operator_email: str) -> dict:
    """按核销码核销。返回 {ok, level, row, already} 或 {ok:False, error}。"""
    code = code.strip().upper()
    if not code.startswith("AX-"):
        code = "AX-" + code
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT {_ROW_FIELDS} FROM ax_event WHERE level1_code = %s OR level2_code = %s",
        (code, code),
    )
    raw = cur.fetchone()
    if not raw:
        conn.close()
        return {"ok": False, "error": f"核销码 {code} 不存在"}
    row = _row_to_dict(raw)
    level = 1 if row["level1_code"] == code else 2
    already = bool(row["level1_redeemed_at"] if level == 1 else row["level2_redeemed_at"])
    if not already:
        col_at, col_by = (
            ("level1_redeemed_at", "level1_redeemed_by") if level == 1
            else ("level2_redeemed_at", "level2_redeemed_by")
        )
        cur.execute(
            f"UPDATE ax_event SET {col_at} = NOW(), {col_by} = %s, updated_at = NOW() "
            "WHERE openid = %s",
            (operator_email, row["openid"]),
        )
        conn.commit()
    conn.close()
    return {"ok": True, "level": level, "already": already,
            "row": get_ax_row(openid=row["openid"])}


def list_participants(query: str = "", limit: int = 200) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    if query:
        q = f"%{query.strip()}%"
        cur.execute(
            f"""SELECT {_ROW_FIELDS} FROM ax_event
                WHERE email ILIKE %s OR nickname ILIKE %s
                   OR level1_code ILIKE %s OR level2_code ILIKE %s
                ORDER BY created_at DESC LIMIT %s""",
            (q, q, q, q, limit),
        )
    else:
        cur.execute(
            f"SELECT {_ROW_FIELDS} FROM ax_event ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_stats() -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT COUNT(*),
                  COUNT(*) FILTER (WHERE registered_at IS NOT NULL),
                  COUNT(*) FILTER (WHERE level1_done_at IS NOT NULL),
                  COUNT(*) FILTER (WHERE level2_done_at IS NOT NULL),
                  COUNT(*) FILTER (WHERE level1_redeemed_at IS NOT NULL),
                  COUNT(*) FILTER (WHERE level2_redeemed_at IS NOT NULL)
           FROM ax_event"""
    )
    total, registered, l1, l2, r1, r2 = cur.fetchone()
    conn.close()
    return {
        "total": total, "registered": registered,
        "level1_done": l1, "level2_done": l2,
        "level1_redeemed": r1, "level2_redeemed": r2,
        "conversion_pct": round(l2 * 100 / l1, 1) if l1 else 0.0,
    }


# ── 账号密码邮件 ──────────────────────────────────────────────────────────────

def send_account_email(to_email: str, password: str) -> bool:
    """发送账号密码邮件（同步阻塞，调用方用 asyncio.to_thread 包裹）。"""
    html = f"""\
<div style="max-width:560px;margin:0 auto;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#211C18;">
  <div style="background:linear-gradient(160deg,#252815 0%,#353A1A 55%,#282C14 100%);padding:28px 24px;border-radius:12px 12px 0 0;">
    <div style="color:#D4925A;font-size:22px;font-weight:bold;">猎鹿人 · Hunter</div>
    <div style="color:#EFE8DC;font-size:13px;margin-top:6px;">只对你说真话的 AI 投资管家</div>
  </div>
  <div style="background:#FFFDF9;border:1px solid #D8CDBA;border-top:none;padding:28px 24px;border-radius:0 0 12px 12px;">
    <p style="font-size:15px;">你好！欢迎参加 <b>AdventureX 展位体验</b>，你的专属账号已开通：</p>
    <div style="background:#F7F3EC;border:1px solid #D8CDBA;border-radius:8px;padding:16px 20px;margin:18px 0;">
      <div style="font-size:14px;margin-bottom:8px;">账号：<b>{to_email}</b></div>
      <div style="font-size:14px;">密码：<b style="font-size:18px;letter-spacing:1px;color:#B06A32;">{password}</b></div>
    </div>
    <p style="font-size:14px;line-height:1.8;">
      · 微信里：关注服务号后点底部「登陆注册」即可免密进入<br/>
      · 电脑上：访问 <a href="https://hunter.agentpit.io" style="color:#B06A32;">hunter.agentpit.io</a> 用上面的账号密码登录<br/>
      · 你导入的持仓，猎鹿人正在 7×24 盯盘，有异动第一时间告诉你
    </p>
    <p style="font-size:12px;color:#7A6F63;margin-top:24px;">
      我们不会向第三方分享你的邮箱。产品数据分析由 AI 生成，仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。
    </p>
  </div>
</div>"""
    text = (
        f"欢迎参加 AdventureX 展位体验！\n\n"
        f"你的猎鹿人 Hunter 账号：{to_email}\n初始密码：{password}\n\n"
        f"微信：服务号底部菜单「登陆注册」免密进入\n"
        f"电脑：https://hunter.agentpit.io 账号密码登录\n\n"
        f"（本邮件由系统发送，请勿回复）"
    )
    try:
        msg = MIMEText(html, "html", "utf-8")
        msg["Subject"] = Header("🎉 你的猎鹿人 Hunter 账号已开通 — AdventureX 专属体验", "utf-8")
        msg["From"] = formataddr((str(Header(AX_MAIL_FROM_NAME, "utf-8")), AX_MAIL_FROM))
        msg["To"] = to_email
        with smtplib.SMTP_SSL(AX_SMTP_HOST, AX_SMTP_PORT, timeout=15) as smtp:
            smtp.login(AX_SMTP_USER, AX_SMTP_PASS)
            smtp.sendmail(AX_MAIL_FROM, [to_email], msg.as_string())
        logger.info("[ax] 账号邮件已发送 to={}", to_email)
        return True
    except Exception as e:
        # 纯 HTML 失败时降级纯文本再试一次
        try:
            msg = MIMEText(text, "plain", "utf-8")
            msg["Subject"] = Header("你的猎鹿人 Hunter 账号已开通", "utf-8")
            msg["From"] = formataddr((str(Header(AX_MAIL_FROM_NAME, "utf-8")), AX_MAIL_FROM))
            msg["To"] = to_email
            with smtplib.SMTP_SSL(AX_SMTP_HOST, AX_SMTP_PORT, timeout=15) as smtp:
                smtp.login(AX_SMTP_USER, AX_SMTP_PASS)
                smtp.sendmail(AX_MAIL_FROM, [to_email], msg.as_string())
            return True
        except Exception as e2:
            logger.error("[ax] 邮件发送失败 to={}: {} / {}", to_email, e, e2)
            return False


# ── 分析报告邮件（V4 新增） ───────────────────────────────────────────────────

def _send_html_mail(to_email: str, subject: str, html: str, text_fallback: str) -> bool:
    """通用邮件发送，HTML 失败降级纯文本再试。"""
    try:
        msg = MIMEText(html, "html", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((str(Header(AX_MAIL_FROM_NAME, "utf-8")), AX_MAIL_FROM))
        msg["To"] = to_email
        with smtplib.SMTP_SSL(AX_SMTP_HOST, AX_SMTP_PORT, timeout=15) as smtp:
            smtp.login(AX_SMTP_USER, AX_SMTP_PASS)
            smtp.sendmail(AX_MAIL_FROM, [to_email], msg.as_string())
        logger.info("[ax] 邮件已发送 subject='{}' to={}", subject, to_email)
        return True
    except Exception as e:
        try:
            msg = MIMEText(text_fallback, "plain", "utf-8")
            msg["Subject"] = Header(subject, "utf-8")
            msg["From"] = formataddr((str(Header(AX_MAIL_FROM_NAME, "utf-8")), AX_MAIL_FROM))
            msg["To"] = to_email
            with smtplib.SMTP_SSL(AX_SMTP_HOST, AX_SMTP_PORT, timeout=15) as smtp:
                smtp.login(AX_SMTP_USER, AX_SMTP_PASS)
                smtp.sendmail(AX_MAIL_FROM, [to_email], msg.as_string())
            return True
        except Exception as e2:
            logger.error("[ax] 邮件失败 to={}: {} / {}", to_email, e, e2)
            return False


def _clip(text: str, limit: int = 500) -> str:
    """长文本软截断"""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _render_report_sections_html(result_full: dict | None) -> str:
    """把 final dict 里的 bull/bear/plan/stop_loss/sentinel 渲染成邮件正文段落。
    每段独立卡片，字段为空则不渲染。"""
    import html as _html
    if not result_full:
        return ""
    parts: list[str] = []

    def section(icon: str, title: str, body_html: str,
                accent: str = "#B06A32", bg: str = "#FFFDF9") -> str:
        return f"""
    <div style="background:{bg};border:1px solid #D8CDBA;border-left:3px solid {accent};
                border-radius:8px;padding:14px 18px;margin:12px 0;">
      <div style="font-size:13px;color:{accent};font-weight:700;letter-spacing:0.5px;margin-bottom:8px;">
        {icon} {title}
      </div>
      <div style="font-size:13.5px;color:#4B423A;line-height:1.9;white-space:pre-wrap;">{body_html}</div>
    </div>"""

    bull = _clip(result_full.get("bull_summary") or "", 800)
    if bull:
        parts.append(section("🐂", "利好证据", _html.escape(bull), accent="#A4332B", bg="#FDF7F5"))

    bear = _clip(result_full.get("bear_summary") or "", 800)
    if bear:
        parts.append(section("🐻", "风险提示", _html.escape(bear), accent="#3F6B40", bg="#F5F9F5"))

    plan = _clip(result_full.get("investment_plan") or "", 600)
    if plan:
        parts.append(section("💡", "操作建议", _html.escape(plan), accent="#B06A32", bg="#FBF1E4"))

    stop_loss = result_full.get("stop_loss")
    if stop_loss:
        try:
            sl = float(stop_loss)
            parts.append(f"""
    <div style="background:#FBEDEA;border:1px solid #E6C0BA;border-radius:8px;padding:12px 18px;margin:12px 0;
                display:flex;align-items:center;justify-content:space-between;">
      <span style="font-size:13px;color:#7A6F63;">🛡 止损参考价</span>
      <span style="font-size:20px;font-weight:800;color:#A4332B;font-family:Georgia,serif;">¥{sl:.2f}</span>
    </div>""")
        except (TypeError, ValueError):
            pass

    sentinel = _clip(result_full.get("sentinel_summary") or "", 500)
    if sentinel:
        parts.append(section("🛰", "情报摘要", _html.escape(sentinel), accent="#7C4A22", bg="#F7F3EC"))

    if result_full.get("sentinel_conflicts"):
        parts.insert(0, """
    <div style="background:#FBF1E4;border:1px solid #E6C89E;border-radius:8px;padding:10px 14px;margin:12px 0;
                color:#7C4A22;font-size:12px;line-height:1.7;">
      ⚠️ Sentinel 情报与技术面存在矛盾，置信度已被 AI 修正
    </div>""")

    return "".join(parts)


def _render_report_sections_text(result_full: dict | None) -> str:
    """text 版邮件分段"""
    if not result_full:
        return ""
    lines: list[str] = []

    def block(title: str, body: str) -> None:
        body = _clip(body or "", 800)
        if body:
            lines.append(f"\n【{title}】\n{body}\n")

    block("🐂 利好证据", result_full.get("bull_summary") or "")
    block("🐻 风险提示", result_full.get("bear_summary") or "")
    block("💡 操作建议", result_full.get("investment_plan") or "")
    stop_loss = result_full.get("stop_loss")
    if stop_loss:
        try:
            lines.append(f"\n🛡 止损参考价：¥{float(stop_loss):.2f}\n")
        except (TypeError, ValueError):
            pass
    sentinel = _clip(result_full.get("sentinel_summary") or "", 500)
    if sentinel:
        lines.append(f"\n【🛰 情报摘要】\n{sentinel}\n")
    if result_full.get("sentinel_conflicts"):
        lines.insert(0, "\n⚠️ Sentinel 情报与技术面存在矛盾，置信度已修正\n")
    return "".join(lines)


def send_analysis_email(
    to_email: str, stock_name: str, stock_code: str,
    decision: str, confidence: float | None, key_reason: str,
    report_url: str, redeem_code: str,
    account_password: str = "",  # 空表示老用户已知密码，邮件不再展示
    ax_page_url: str = "https://yiqihecheng.net/wx/ax",
    result_full: dict | None = None,  # V4.1：完整 final dict，直接嵌入 bull/bear/plan/stop/sentinel
) -> bool:
    """V4：分析完成后发送综合邮件（分析结论 + 账号密码 + 完整报告嵌入 + 核销码）。"""
    from app.services.ax_features import AX_UNLOCK_THRESHOLD as _AX_UNLOCK_N
    import html as _html
    dec_cn = {"BUY": "看好", "SELL": "谨慎", "HOLD": "中性"}.get(decision, "中性")
    dec_color = {"BUY": "#A4332B", "SELL": "#3F6B40"}.get(decision, "#4B423A")
    score = ""
    if confidence is not None:
        try:
            v = float(confidence)
            score = f"{int(v * 100 if v <= 1 else v)} / 100"
        except (TypeError, ValueError):
            pass
    account_block = ""
    if account_password:
        account_block = f"""
    <div style="background:#F7F3EC;border:1px solid #D8CDBA;border-radius:8px;padding:16px 20px;margin:18px 0;">
      <div style="font-size:13px;color:#7A6F63;margin-bottom:8px;letter-spacing:0.5px;">🔑 你的登录信息</div>
      <div style="font-size:14px;margin-bottom:6px;">账号：<b>{to_email}</b></div>
      <div style="font-size:14px;margin-bottom:10px;">密码：<b style="font-size:16px;letter-spacing:1px;color:#B06A32;">{account_password}</b></div>
      <div style="font-size:13px;line-height:1.9;color:#4B423A;">
        · 🖥️ 电脑登录：<a href="https://agentpit.io/" style="color:#B06A32;">https://agentpit.io/</a><br/>
        · 📱 手机免密：微信服务号菜单「登陆注册」
      </div>
    </div>"""
    key_reason_esc = _html.escape(key_reason or "")
    report_sections_html = _render_report_sections_html(result_full)
    html = f"""\
<div style="max-width:560px;margin:0 auto;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#211C18;">
  <div style="background:linear-gradient(160deg,#252815 0%,#353A1A 55%,#282C14 100%);padding:26px 24px;border-radius:12px 12px 0 0;">
    <div style="color:#D4925A;font-size:22px;font-weight:bold;">猎鹿人 · Hunter</div>
    <div style="color:#EFE8DC;font-size:13px;margin-top:6px;">只对你说真话的 AI 投资管家</div>
  </div>
  <div style="background:#FFFDF9;border:1px solid #D8CDBA;border-top:none;padding:26px 24px;border-radius:0 0 12px 12px;">
    <div style="font-size:13px;color:#B06A32;letter-spacing:0.5px;margin-bottom:4px;">🎯 AI 真相体检 · 分析已完成</div>
    <div style="font-size:18px;font-weight:700;margin-bottom:16px;">{stock_name or stock_code} <span style="font-size:14px;color:#7A6F63;font-weight:400;">{stock_code}</span></div>

    <div style="background:#FBF1E4;border:1px solid #E6C89E;border-radius:8px;padding:14px 18px;margin-bottom:14px;">
      <div style="display:flex;gap:16px;align-items:baseline;">
        <div style="font-size:24px;font-weight:800;color:{dec_color};">{dec_cn}</div>
        <div style="font-size:13px;color:#7A6F63;">{score}</div>
      </div>
      {"<div style='font-size:14px;color:#4B423A;line-height:1.8;margin-top:8px;white-space:pre-wrap;'>" + key_reason_esc + "</div>" if key_reason_esc else ""}
    </div>

    {report_sections_html}

    {account_block}

    <div style="background:#F0E9DA;border:1px dashed #B06A32;border-radius:8px;padding:14px 18px;margin:16px 0;">
      <div style="font-size:13px;color:#7A6F63;margin-bottom:6px;letter-spacing:0.5px;">🎫 体验礼核销（凭二维码到 AdventureX 展位领取）</div>
      <div style="font-size:14px;color:#4B423A;line-height:1.7;">📱 手机端点击微信服务号菜单，获取礼品核销二维码</div>
    </div>

    <div style="background:#F7F3EC;border:1px solid #D8CDBA;border-radius:8px;padding:14px 18px;margin-top:14px;">
      <div style="font-size:14px;font-weight:600;color:#211C18;margin-bottom:6px;">💎 升级奖励</div>
      <div style="font-size:13px;color:#4B423A;line-height:1.8;">完成全部 {_AX_UNLOCK_N} 项功能体验（2 项基础 + 2 项精选），可再解锁：<br/>· 高级伴手礼（会员时长已开通 3 个月）</div>
      <div style="margin-top:10px;"><a href="{ax_page_url}" style="color:#B06A32;font-weight:600;font-size:13px;">回活动页继续 →</a></div>
    </div>

    <div style="font-size:11px;color:#7A6F63;margin-top:22px;line-height:1.6;">
      产品数据分析由 AI 生成，仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。
    </div>
  </div>
</div>"""
    text = (
        f"你的 AI 体检报告出炉\n\n"
        f"{stock_name} ({stock_code})：{dec_cn} {score}\n"
        f"{key_reason}\n"
        + _render_report_sections_text(result_full)
        + "\n"
        + (f"账号：{to_email}\n密码：{account_password}\n电脑登录：https://agentpit.io/\n\n" if account_password else "")
        + f"体验礼核销：手机端点击微信服务号菜单，获取礼品核销二维码\n"
        f"完成全部功能体验可再升级为高级伴手礼（会员时长已开通 3 个月）\n回活动页继续：{ax_page_url}\n"
    )
    subj = f"【猎鹿人】{stock_name or stock_code} 分析报告" + ("｜账号已开通" if account_password else "")
    return _send_html_mail(to_email, subj, html, text)


def send_reward_email(
    to_email: str, redeem_code: str, member_months: int,
    ax_page_url: str = "https://yiqihecheng.net/wx/ax",
) -> bool:
    """V4：完成分配任务后发送股民礼解锁邮件（简洁风格）。"""
    from app.services.ax_features import AX_UNLOCK_THRESHOLD as _AX_UNLOCK_N
    html = f"""\
<div style="max-width:560px;margin:0 auto;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#211C18;">
  <div style="background:linear-gradient(160deg,#252815 0%,#353A1A 55%,#282C14 100%);padding:26px 24px;border-radius:12px 12px 0 0;">
    <div style="color:#D4925A;font-size:22px;font-weight:bold;">猎鹿人 · Hunter</div>
    <div style="color:#EFE8DC;font-size:13px;margin-top:6px;">只对你说真话的 AI 投资管家</div>
  </div>
  <div style="background:#FFFDF9;border:1px solid #D8CDBA;border-top:none;padding:28px 24px;border-radius:0 0 12px 12px;">
    <div style="font-size:20px;font-weight:700;margin-bottom:10px;">🎉 股民礼已解锁</div>
    <div style="font-size:14px;color:#4B423A;line-height:1.9;margin-bottom:16px;">恭喜你完成全部 {_AX_UNLOCK_N} 项功能体验，猎鹿人为你准备的：</div>
    <div style="background:#FBF1E4;border-left:3px solid #B06A32;padding:12px 16px;margin-bottom:16px;">
      <div style="font-size:15px;font-weight:600;">🎁 高级伴手礼 · {member_months} 个月会员</div>
    </div>
    <div style="background:#F0E9DA;border:1px dashed #B06A32;border-radius:8px;padding:14px 18px;margin:16px 0;">
      <div style="font-size:13px;color:#7A6F63;margin-bottom:6px;letter-spacing:0.5px;">🎫 股民礼核销码（凭码到展位领取）</div>
      <div style="font-size:22px;font-weight:800;letter-spacing:3px;color:#B06A32;font-family:ui-monospace,monospace;">{redeem_code}</div>
    </div>
    <div style="font-size:13px;color:#4B423A;line-height:1.9;">
      · 会员已自动开通 {member_months} 个月<br/>
      · 手机随时 <a href="{ax_page_url}" style="color:#B06A32;">回活动页</a> 查看核销状态
    </div>
    <div style="font-size:11px;color:#7A6F63;margin-top:22px;line-height:1.6;">
      产品数据分析由 AI 生成，仅供参考，不构成任何投资建议。
    </div>
  </div>
</div>"""
    text = (
        f"🎉 股民礼已解锁\n\n"
        f"恭喜完成全部 {_AX_UNLOCK_N} 项功能体验，你获得：\n"
        f"高级伴手礼 + {member_months} 个月会员\n\n"
        f"股民礼核销码：{redeem_code}\n凭码到 AdventureX 展位领取\n\n"
        f"活动页：{ax_page_url}\n"
    )
    return _send_html_mail(to_email, "【猎鹿人】🎉 你的股民礼已解锁", html, text)
