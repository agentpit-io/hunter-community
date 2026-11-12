"""通用 Webhook 渠道 `webhook` —— POST 到一个**可配置的 URL**（本机起假接收端即可测）。

**不接任何要注册 / 要付费 / 要审资质的第三方内容平台**（`plan/五期追加规则 §6.1`）。
这里只做「把报告 POST 出去、按响应判三态」这件通用的事。

目标（`target`）两种写法（`base.parse_target`）：
  · `"https://host/path"`；
  · `{"url": "...", "status_url": "...", "headers": {...}, "timeout": 1.0}` ——
    `status_url` 是**核实**入口：超时后拿它回查对方到底收没收到（`§11.2`「核实外部状态」）。

三态判据（`01方案 §11.2`）：
  · 2xx → `SUCCESS`；
  · **连接被拒 / DNS 失败** → `FAILED`（**根本没发出去**，可安全重试）；
  · **超时 / 连接中断 / 非 2xx 之外说不清** → `UNKNOWN`（可能已发出，**不许盲目重发**）。
"""

from __future__ import annotations

import os
from typing import Optional

import httpx

from .base import FAILED, SUCCESS, UNKNOWN, PublishResult, parse_target, receipt_id_for

# 单次发布的默认超时（秒）。可用 env 或 target.timeout 覆盖 —— 超时越短越容易把
# 「其实已送达」判成 UNKNOWN，这是保守方向的取舍（宁可待核实，不可盲目重发）。
DEFAULT_TIMEOUT = float(os.getenv("FIN_PUBLISH_TIMEOUT_S", "5"))


def _payload(report: dict, html: Optional[str]) -> dict:
    return {
        "report_id": report.get("report_id"),
        "trade_date": str(report.get("trade_date") or ""),
        "market": report.get("market"),
        "currency": report.get("currency"),
        "title": f"每日报告 · {report.get('trade_date')} · {report.get('market')}",
        "channel": "webhook",
        # 内容（表达层产物）。站内已有产物时用它；没有再退回纯文字分析。
        "html": html,
        "analysis_text": report.get("analysis_text"),
        # 幂等键 = (报告, 渠道) 的回执 id —— 对方据此去重（`§11.2` 稳定请求标识）。
        "receipt_key": receipt_id_for(report.get("report_id") or "", "webhook"),
    }


class WebhookAdapter:
    channel = "webhook"

    def submit(self, report: dict, target: Optional[str], *,
               html: Optional[str] = None, ctx: Optional[dict] = None) -> PublishResult:
        spec = parse_target(target)
        url = spec.get("url") or spec.get("target")
        if not url:
            return PublishResult(FAILED, None, "webhook 未配置目标 URL，未发布")

        timeout = float(spec.get("timeout") or DEFAULT_TIMEOUT)
        headers = {"X-Hunter-Report": str(report.get("report_id") or ""),
                   "X-Idempotency-Key": receipt_id_for(report.get("report_id") or "", self.channel)}
        headers.update({str(k): str(v) for k, v in (spec.get("headers") or {}).items()})

        try:
            resp = httpx.post(str(url), json=_payload(report, html), headers=headers, timeout=timeout)
        except httpx.ConnectError as exc:
            # 连接被拒 / DNS 失败 —— 请求根本没发出去。
            return PublishResult(FAILED, None,
                                 f"连不上目标，未发布：{type(exc).__name__}: {str(exc)[:120]}",
                                 target=str(url))
        except (httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
            # 超时 / 对方中途断开 —— **可能已经送达**，判 UNKNOWN，进待核实。
            return PublishResult(UNKNOWN, None,
                                 f"结果不明（{timeout:g}s 内没拿到回执，可能已送达）：{type(exc).__name__}",
                                 target=str(url))
        except httpx.HTTPError as exc:
            return PublishResult(UNKNOWN, None,
                                 f"结果不明（网络异常）：{type(exc).__name__}: {str(exc)[:120]}",
                                 target=str(url))

        if 200 <= resp.status_code < 300:
            return PublishResult(SUCCESS, _external_id(resp), f"HTTP {resp.status_code} 已送达",
                                 target=str(url))
        return PublishResult(FAILED, None, f"HTTP {resp.status_code}（对方拒收，未发布）",
                             target=str(url))

    def probe(self, receipt: dict) -> Optional[str]:
        """核实：GET `status_url?receipt=<receipt_id>`，按对方回的 `received` 落定。

        返回 `SUCCESS` / `FAILED` = 查清；`None` = 查不出（没配 `status_url`、对方不可达、
        返回体看不懂）—— **保留 UNKNOWN**（`§11.2`）。
        """
        spec = parse_target(receipt.get("target"))
        status_url = spec.get("status_url")
        if not status_url:
            return None
        try:
            resp = httpx.get(str(status_url), params={"receipt": receipt.get("receipt_id")},
                             timeout=float(spec.get("timeout") or DEFAULT_TIMEOUT))
            if resp.status_code // 100 != 2:
                return None
            body = resp.json()
        except Exception:  # noqa: BLE001 —— 查不出就是查不出，不当成失败
            return None
        if not isinstance(body, dict):
            return None
        got = body.get("received")
        if got is True:
            return SUCCESS
        if got is False:
            return FAILED
        return None


def _external_id(resp: httpx.Response) -> Optional[str]:
    ext = resp.headers.get("X-Receipt-Id")
    if ext:
        return ext
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return None
    if isinstance(body, dict):
        return body.get("receipt_id") or body.get("external_id")
    return None
