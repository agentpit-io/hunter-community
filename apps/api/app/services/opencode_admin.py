"""让 opencode 重新扫描 skill 目录。

**为什么需要**:opencode 只在实例启动时扫一次 skill 目录,之后缓存住
(`skill/index.ts` 里两个 `InstanceState`)。所以往 `user-skills/` 写完文件,
opencode 还看不到 —— 表现是「侧栏已经显示了,模型却说没有这个能力」。

实测过三条路(`_19` §1):
  · 文件出现在挂载目录      → 容器内 `ls` 立刻可见,opencode **认不到**
  · `POST /instance/dispose` → 返回 200 true,skill 列表**纹丝不动**
  · 重启容器                → 有效,**约 52 秒**

所以给 fork 加了 `POST /skill/refresh`(huntercode PR #1)。
**但用户的镜像版本我们控制不了** —— 旧镜像上这个端点是 404,
那时必须如实告诉用户"需要手动重启",不能假装成功。

> 一个静默 no-op 的 refresh 比没有 refresh 更糟:
> UI 报告保存成功,而模型手上还是旧列表,用户完全无从判断。
"""
from __future__ import annotations

import base64
import os

import httpx
from loguru import logger

_URL = os.getenv("OPENCODE_URL", "http://opencode:3901")
_USER = os.getenv("OPENCODE_SERVER_USERNAME", "")
_PASS = os.getenv("OPENCODE_SERVER_PASSWORD", "")

# 重扫本身很快(实测 <1 秒),但首次会真的读一遍磁盘,给宽一点
_TIMEOUT = 30.0


def _headers() -> dict:
    if not (_USER or _PASS):
        return {}
    token = base64.b64encode(f"{_USER}:{_PASS}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def refresh_skills() -> dict:
    """让 opencode 重扫 skill 目录。

    返回:
        {"ok": True,  "count": N}                    刷新成功,N = 重扫后的 skill 数
        {"ok": False, "needs_restart": True, ...}    端点不存在(镜像旧)或调用失败

    **永远不抛异常** —— 调用方在保存 SKILL 的主干上,刷新失败不该让保存回滚。
    文件已经写好了,重启一次就生效;把这件事**告诉用户**即可。
    """
    try:
        r = httpx.post(f"{_URL}/skill/refresh", headers=_headers(), timeout=_TIMEOUT)
    except Exception as e:
        logger.warning("[opencode_admin] refresh 请求失败: {}", e)
        return {"ok": False, "needs_restart": True,
                "reason": f"连不上 opencode({type(e).__name__})"}

    if r.status_code == 404:
        # 旧镜像没有这个端点 —— 这不是错误,是版本差异,措辞要区分开
        logger.info("[opencode_admin] /skill/refresh 不存在(镜像较旧)· 需手动重启")
        return {"ok": False, "needs_restart": True,
                "reason": "当前 opencode 镜像不支持热刷新"}
    if r.status_code != 200:
        logger.warning("[opencode_admin] refresh 返回 {}: {}", r.status_code, r.text[:120])
        return {"ok": False, "needs_restart": True,
                "reason": f"opencode 返回 HTTP {r.status_code}"}

    # ⚠️ **状态码 200 不等于端点存在**。
    # opencode 对未知路由返回 200 + SPA 的 HTML(实测:Content-Type text/html,
    # 正文是 <!doctype html>)。只看状态码的话,旧镜像上会被判成"刷新成功",
    # UI 报告已同步而模型手上还是旧列表 —— 正是这个模块开头说要避免的那件事。
    #
    # 所以**以能不能解析出一个数为准**:端点契约是返回重扫后的 skill 数。
    try:
        count = int(r.json())
    except Exception:
        logger.info("[opencode_admin] /skill/refresh 返回的不是数字"
                    "(content-type={})· 判定为镜像不支持,需手动重启",
                    r.headers.get("content-type", "?"))
        return {"ok": False, "needs_restart": True,
                "reason": "当前 opencode 镜像不支持热刷新(未知路由被 SPA 接管)"}
    logger.info("[opencode_admin] 刷新成功 · 现有 {} 个 skill", count)
    return {"ok": True, "count": count}


def restart_hint() -> str:
    """刷新不成功时给用户看的一句话。写清楚**为什么**,不只是让他敲命令。"""
    return ("新能力已保存,但当前 opencode 需要重启才能识别 —— "
            "它只在启动时扫描能力目录。在部署目录执行:"
            "docker compose restart opencode(约 50 秒)")
