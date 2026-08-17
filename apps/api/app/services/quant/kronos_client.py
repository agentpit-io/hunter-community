"""Kronos 因子适配层 · 走 saas_gateway 复用 hunter kpred 网关
(2026-08-17 · Phase C C1.3)

设计要点:
- Kronos 是重模型 · 单次 predict 1-5s · 300 只批调需并发
- 复用 saas_gateway.kronos_url + kronos_headers · 与 kpred router 同一入口
- 每只票 15s 超时 · 失败跳过(不阻塞其他因子)
- 5 日预测收益率作为因子值 · IC 应显著为正
- 无 trade_date 参数 · Kronos 是 T-0 预测 · 只能拿"当前"预测
  (回填历史因子时 · 用当日 close 作为 base · 5 日预测收益率作为因子)
- 但历史回填困难 · Kronos 上游不支持"给定 T 日 · 预测 T+5" · 只能拿"今天预测"
- 折中方案:kronos 因子只在当日(今日)有值 · 历史期 factor_value 缺失
  · 回测组合中 kronos 权重存在时 · strategy_engine 需容忍缺失

用法:
  from app.services.quant.kronos_client import batch_get_kronos
  scores = batch_get_kronos(['600519', '000858'], horizon=5)
  # → {'600519': 0.023, '000858': -0.005}
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger(__name__)


# 双环境兼容:
# - hunter-community · 走 saas_gateway(需 hunter_key)
# - hermes-1 · 直接读 KRONOS_URL env var(生产内网直连或 gateway URL)
def _kronos_url() -> str:
    try:
        from app.services import saas_gateway as _gw
        return _gw.kronos_url()
    except ImportError:
        return os.getenv("KRONOS_URL", "http://136.110.39.14:8000").rstrip("/")


def _kronos_headers() -> dict:
    try:
        from app.services import saas_gateway as _gw
        return _gw.kronos_headers()
    except ImportError:
        return {}

_SEM = asyncio.Semaphore(3)   # 最多 3 并发 · Kronos 单请求 3-8s · sem 5 会 timeout
_TIMEOUT = httpx.Timeout(connect=3.0, read=45.0, write=5.0, pool=3.0)


async def _one_predict(client: httpx.AsyncClient, code: str, horizon: int = 5) -> Optional[float]:
    """单只票 Kronos 预测 · 返 (pred_close_end / last_close - 1)"""
    async with _SEM:
        try:
            r = await client.post(
                f"{_kronos_url()}/predict",
                json={"symbol": code, "pred_len": horizon},
                headers=_kronos_headers(),
            )
            if r.status_code != 200:
                log.warning(f"[kronos] {code} HTTP {r.status_code}: {r.text[:100]}")
                return None
            data = r.json()
            preds = data.get("predictions", [])
            last_close = float(data.get("last_close") or 0)
            if not preds or last_close <= 0:
                return None
            end_close = float(preds[-1].get("close") or 0)
            if end_close <= 0:
                return None
            return end_close / last_close - 1.0
        except Exception as e:
            log.warning(f"[kronos] {code}: {type(e).__name__}: {e}")
            return None


async def _batch_async(codes: list[str], horizon: int = 5) -> dict[str, float]:
    """并发调 Kronos · 只保留成功的"""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        tasks = [_one_predict(client, c, horizon) for c in codes]
        results = await asyncio.gather(*tasks, return_exceptions=False)
    out = {}
    for c, r in zip(codes, results):
        if isinstance(r, float):
            out[c] = r
    return out


def batch_get_kronos(codes: list[str], horizon: int = 5) -> dict[str, float]:
    """factor_engine 同步入口 · 内部启 asyncio · sem=5 · 15s 超时"""
    if not codes:
        return {}
    try:
        # 在 event loop 里(如从 FastAPI 调) · 用 new loop 隔离
        try:
            asyncio.get_running_loop()
            # 已在 loop · 用 thread 隔离(factor_engine 是同步的 · 但可能被 FastAPI async handler 调)
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(asyncio.run, _batch_async(codes, horizon))
                return fut.result(timeout=len(codes) * 20)
        except RuntimeError:
            # 没有 running loop · 直接 asyncio.run
            return asyncio.run(_batch_async(codes, horizon))
    except Exception as e:
        log.error(f"[kronos-batch] failed: {e}")
        return {}
