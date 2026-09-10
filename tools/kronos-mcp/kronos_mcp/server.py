#!/usr/bin/env python3
"""Kronos MCP —— 把清华 Kronos 走势预测变成 AI 能直接调的工具。

## 它做什么

Kronos 是一个 K 线时序模型:给它一个股票代码,它预测未来 N 根 K 线的
开高低收和成交量。A 股 / 美股 / 港股都能问。

这个 MCP 把它包成两个工具,让 Claude / Cursor 这类客户端能直接调:

    kronos_health()                    先确认 key 是通的
    kronos_predict("600519", 10)       预测未来 10 根 K 线

## 为什么必须要 API key

上游是我们自己跑的 GPU 服务,一次推理 30-70 秒。没有 key 的话:

  · 任何人都能白嫖 GPU,几个爬虫就能把服务打满
  · 没有任何计量,出问题查不到是谁
  · 我们没法给正常用户保证可用性

所以**这个 MCP 不做无 key 模式**,也做不了 —— 上游 nginx 直接返 401。
没配 key 时它不会假装成功、不会返回样例数据,而是明确告诉你去哪申请。

申请:https://hunter.agentpit.io/dev/api-keys · API 类型选 **KRONOS**
拿到的 key 形如 `oapk_` + 32 位。工作日内通常几小时审批。

## 跑起来

    # 方式一 · uvx(推荐 · 不用装)
    KRONOS_API_KEY=oapk_xxx uvx kronos-mcp

    # 方式二 · pip
    pip install kronos-mcp
    KRONOS_API_KEY=oapk_xxx kronos-mcp

Claude Desktop / Cursor 的配置(`claude_desktop_config.json`):

    {
      "mcpServers": {
        "kronos": {
          "command": "uvx",
          "args": ["kronos-mcp"],
          "env": { "KRONOS_API_KEY": "oapk_xxx" }
        }
      }
    }

## 环境变量

    KRONOS_API_KEY     必填 · oapk_ 开头的 KRONOS 类型 key
    KRONOS_URL         可选 · 默认 https://kronos.agentpit.io
                       自建部署或走别的网关时改这个
    KRONOS_TIMEOUT     可选 · 默认 180 秒。GPU 推理 30-70s,别设太紧
    KRONOS_MCP_TRANSPORT  可选 · stdio(默认)| streamable-http | sse
    KRONOS_MCP_HOST/PORT  仅远程传输时用 · 默认 0.0.0.0:8932
"""
from __future__ import annotations

import json
import os

import httpx
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("kronos-mcp")

DEFAULT_URL = "https://kronos.agentpit.io"
APPLY_URL = "https://hunter.agentpit.io/dev/api-keys"

# GPU 推理 30-70s 是常态,不是异常。connect 短、read 长 ——
# 连不上要快速失败,连上了就得耐心等。
_TIMEOUT = httpx.Timeout(
    connect=5.0,
    read=float(os.getenv("KRONOS_TIMEOUT", "180")),
    write=5.0,
    pool=5.0,
)


def _base_url() -> str:
    return (os.getenv("KRONOS_URL") or DEFAULT_URL).rstrip("/")


def _key() -> str:
    return (os.getenv("KRONOS_API_KEY") or "").strip()


def _no_key_error() -> str:
    """没配 key 时的回话。

    **不返回假数据。**告诉模型确切的下一步 —— 它没法替用户去申请 key,
    但它可以把这段话原样转达给用户,用户照着做就行。
    """
    return json.dumps({
        "error": "missing_api_key",
        "message": "没有配置 KRONOS_API_KEY,无法调用 Kronos。",
        "how_to_fix": [
            f"1. 打开 {APPLY_URL} 登录",
            "2. 点「申请 API Key」· API 类型选 KRONOS",
            "3. 拿到 oapk_ 开头的 key(只显示一次,立即复制)",
            "4. 把它设成环境变量 KRONOS_API_KEY 后重启这个 MCP",
        ],
        "note": "审批通常在工作日内几小时。这个服务没有免 key 模式 —— "
                "上游是自建 GPU,一次推理 30-70 秒,开放匿名调用会被打满。",
    }, ensure_ascii=False)


def _http_error(status: int, body: str) -> str:
    """把上游的 HTTP 状态码翻译成模型能据此行动的话。

    照搬 docs/kronos-direct-access.md 里的错误表 —— 那张表是实际踩出来的,
    尤其 403(key 有效但类型不对)这条,光看 "403 Forbidden" 根本猜不到。
    """
    table = {
        401: ("invalid_api_key",
              "key 无效、缺失或已被撤销。检查 KRONOS_API_KEY 是否复制完整"
              "(oapk_ 后面还有 32 位)。刚撤销的 key 服务端有最多 5 分钟缓存。"),
        403: ("wrong_key_type",
              f"key 本身有效,但它的类型不是 KRONOS(可能申请成了 KPRED / FIN_R1)。"
              f"到 {APPLY_URL} 重新申请一把 KRONOS 类型的。"),
        404: ("symbol_not_found",
              "找不到这个代码。A 股用 600519 或 600519.SH,美股用 ticker(AAPL),"
              "港股用 5 位数字(00700)。"),
        429: ("rate_limited",
              "触发速率限制。无效 key 每 IP 每分钟有硬顶(防洪水);"
              "如果你的 key 是有效的,大概率是 quota 打完了(默认 1000 次)。"),
        502: ("upstream_down", "上游 GPU 服务异常,通常是重启中,稍后重试。"),
        504: ("upstream_timeout", "上游超时。GPU 排队时会这样,稍后重试。"),
    }
    code, msg = table.get(status, ("http_error", f"上游返回 HTTP {status}。"))
    return json.dumps({
        "error": code, "http_status": status, "message": msg,
        "upstream_body": body[:300],
    }, ensure_ascii=False)


def _request(method: str, path: str, **kw) -> str | dict:
    """统一的请求出口。失败一律返回 JSON 字符串,成功返回 dict。

    调用方靠 isinstance 区分 —— 和 akshare-mcp 里 _resolve 同一套路,
    省掉一层异常包装。
    """
    key = _key()
    if not key:
        return _no_key_error()

    url = f"{_base_url()}{path}"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.request(
                method, url,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                **kw,
            )
    except httpx.TimeoutException:
        return json.dumps({
            "error": "timeout",
            "message": f"请求超时({_TIMEOUT.read:.0f}s)。GPU 推理本来就要 30-70 秒,"
                       f"排队时更久 —— 调大 KRONOS_TIMEOUT 再试。",
        }, ensure_ascii=False)
    except httpx.HTTPError as e:
        return json.dumps({
            "error": "network_error",
            "message": f"{type(e).__name__}: {str(e)[:200]}",
            "url": url,
            "hint": "连不上服务。检查网络,或用 KRONOS_URL 指到你自己的部署。",
        }, ensure_ascii=False)

    if r.status_code != 200:
        return _http_error(r.status_code, r.text)

    try:
        return r.json()
    except ValueError:
        return json.dumps({
            "error": "bad_response",
            "message": "上游返回的不是 JSON。",
            "body": r.text[:300],
        }, ensure_ascii=False)


@mcp.tool()
def kronos_health() -> str:
    """确认 Kronos 服务能连上、你的 key 是有效的。

    **遇到任何问题先调这个。**它能把「key 不对」和「代码不对」区分开 ——
    直接调 predict 失败时,这两种原因看起来是一样的。
    """
    res = _request("GET", "/health")
    if isinstance(res, str):
        return res
    return json.dumps({"ok": True, "upstream": res,
                       "base_url": _base_url()}, ensure_ascii=False)


@mcp.tool()
def kronos_predict(symbol: str, pred_len: int = 10) -> str:
    """预测一只股票未来 N 根日 K 线。

    symbol    A 股 `600519` / `600519.SH`,美股 `AAPL`,港股 `00700`
    pred_len  预测多少根,1~30。默认 10

    返回每根预测 K 线的 date / open / high / low / close / volume,
    外加 `last_close`(最后一根真实收盘价)和 `expected_return`
    (末根预测收盘 / 最后真实收盘 − 1)—— 这个比例是最常用的那个数。

    ⚠️ **一次调用要 30-70 秒**(GPU 推理),这是正常的,不要因为慢就重试。
    要问多只票就依次调用,不要指望批量 —— 上游一次只接受一个代码。

    ⚠️ 这是模型的**统计外推,不是投资建议**。它没有读新闻、不知道停牌和财报,
    极端行情下会显著偏离。把它当成一个参考信号,不要当结论。
    """
    sym = (symbol or "").strip()
    if not sym:
        return json.dumps({"error": "bad_symbol",
                           "message": "symbol 不能为空"}, ensure_ascii=False)
    try:
        n = int(pred_len)
    except (TypeError, ValueError):
        return json.dumps({"error": "bad_pred_len",
                           "message": f"pred_len 要是整数,收到 {pred_len!r}"},
                          ensure_ascii=False)
    if not 1 <= n <= 30:
        return json.dumps({
            "error": "bad_pred_len",
            "message": f"pred_len 要在 1~30 之间,收到 {n}。"
                       f"预测越远越不可靠,超过 30 根没有参考价值。",
        }, ensure_ascii=False)

    res = _request("POST", "/predict", json={"symbol": sym, "pred_len": n})
    if isinstance(res, str):
        return res

    preds = res.get("predictions") or []
    last_close = res.get("last_close")

    out: dict = {
        "symbol": sym,
        "pred_len": n,
        "last_close": last_close,
        "predictions": preds,
    }

    # expected_return 只在两个数都真实存在且为正时才给。
    # 算不出就**不放这个字段**,不填 0 —— 0 会被读成「预测持平」,
    # 那是个结论,而真相是「没算出来」。
    try:
        lc = float(last_close)
        end = float(preds[-1].get("close"))
        if lc > 0 and end > 0:
            out["expected_return"] = round(end / lc - 1.0, 6)
            out["expected_return_pct"] = round((end / lc - 1.0) * 100, 2)
    except (TypeError, ValueError, IndexError, AttributeError):
        out["expected_return_note"] = (
            "算不出预期收益率 —— 上游没给 last_close 或末根预测收盘价")

    out["disclaimer"] = ("Kronos 是纯时序统计模型,不读新闻、不知停牌与财报。"
                         "输出是参考信号,不是投资建议。")
    return json.dumps(out, ensure_ascii=False)


def main() -> None:
    """入口 —— PyPI 的 console_scripts 指向这里。

    默认 stdio:Claude Desktop / Cursor / uvx 都走这个,是 MCP 的默认形态。
    streamable-http / sse 留给「跑成常驻服务、多个客户端连」的场景
    (比如接进 Hunter 的「能力 → 接入一个工具」)。MCP 规范已把 SSE 标记为
    过时,新部署优先用 streamable-http。
    """
    transport = (os.getenv("KRONOS_MCP_TRANSPORT") or "stdio").strip().lower()
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    # 远程传输 · host/port 在 mcp 2.x 里是 run() 的关键字参数,
    # 不再是 1.x 那个 mcp.settings.host/port
    host = os.getenv("KRONOS_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("KRONOS_MCP_PORT", "8932"))
    if transport in ("http", "streamable-http"):
        mcp.run(transport="streamable-http", host=host, port=port)
    elif transport == "sse":
        mcp.run(transport="sse", host=host, port=port)
    else:
        raise SystemExit(
            f"未知的 KRONOS_MCP_TRANSPORT={transport!r} —— "
            f"只支持 stdio(默认)/ streamable-http / sse")
        return

    # 远程传输 · host/port 在 mcp 2.x 里是 run() 的关键字参数,
    # 不再是 1.x 那个 mcp.settings.host/port
    host = os.getenv("KRONOS_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("KRONOS_MCP_PORT", "8932"))
    if transport in ("http", "streamable-http"):
        mcp.run(transport="streamable-http", host=host, port=port)
    elif transport == "sse":
        mcp.run(transport="sse", host=host, port=port)
    else:
        raise SystemExit(
            f"未知的 KRONOS_MCP_TRANSPORT={transport!r} —— "
            f"只支持 stdio(默认)/ streamable-http / sse")


if __name__ == "__main__":
    main()
