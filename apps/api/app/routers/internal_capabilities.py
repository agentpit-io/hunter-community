"""平台自有能力的 MCP 桥接 · /api/internal/cap/*

`_12` §7 Step 3 的后端部分。

**要解决的问题**:K线预测 / 多空辩论 / 在线分析 / 情报 这四个是我们最强的能力,
但它们只有 HTTP 接口、**不是 MCP 工具** —— 模型手上够不着。表现是:
用户点侧栏卡片能出富卡片,在对话里说"帮我预测茅台走势"却经常降级成"我无法获取"。

本文件把它们暴露成 `/api/internal/cap/*`,再由
`scripts/opencode-mcp/hunter_capability_mcp.py` 包成 MCP 工具。
与 internal_tools.py / internal_uzi.py 同一套路:共享 secret 鉴权,不走 JWT。

**为什么不让 MCP 直接打公开接口**:
  · `/api/kpred/*` 虽在白名单里(无需 JWT),但没有用户身份,后续要按 key 计量就没抓手
  · `/api/truesource/*` 和 debate 需要 JWT,MCP 侧没有用户 token
  · 统一走 /api/internal 这一层,身份由 hunter-mcp-context plugin 注入的
    X-Hunter-User-Id 带进来,与已有的 watchlist/portfolio/uzi 保持一致
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

router = APIRouter(prefix="/internal/cap", tags=["mcp-bridge-capability"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")


def _auth(request: Request) -> str:
    """校验共享 secret · 返回 X-Hunter-User-Id(可空,由各能力自己决定是否必需)。"""
    if request.headers.get("X-Hunter-Internal-Key", "") != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")
    uid = request.headers.get("X-Hunter-User-Id", "").strip()
    logger.info("[internal.cap] path={} user_id={}", request.url.path, uid or "(none)")
    return uid


# ─────────────────────────────────────────────────────────────
# ① K 线预测(Kronos)· 感知最强的一个
# ─────────────────────────────────────────────────────────────

class KpredIn(BaseModel):
    code: str
    days: int = 10


@router.post("/kpred")
async def cap_kpred(body: KpredIn, request: Request):
    _auth(request)
    days = max(1, min(body.days, 30))
    # 直接复用公开路由的实现,不做 HTTP 自调 —— 少一跳、少一处超时配置
    from app.routers.kpred import get_kpred
    try:
        return await get_kpred(body.code.strip(), days=days, request=request)
    except HTTPException as e:
        # 把 HTTP 错误转成结构化 body · MCP 会把它交给模型,模型好转述给用户
        return {"error": "kpred_failed", "status": e.status_code, "message": str(e.detail)[:300]}
    except Exception as e:
        logger.exception("[internal.cap] kpred failed code={}", body.code)
        return {"error": "kpred_failed", "message": f"{type(e).__name__}: {e}"[:300]}


# ─────────────────────────────────────────────────────────────
# ② 情报 / 发现(TrueSource)
# ─────────────────────────────────────────────────────────────

class BriefIn(BaseModel):
    symbols: str          # 逗号分隔


@router.post("/truesource_brief")
async def cap_truesource_brief(body: BriefIn, request: Request):
    _auth(request)
    from app.routers.truesource import _proxy
    try:
        return await _proxy("/api/hunter/daily-brief", {"symbols": body.symbols.strip()})
    except HTTPException as e:
        return {"error": "truesource_failed", "status": e.status_code,
                "message": str(e.detail)[:300]}
    except Exception as e:
        logger.exception("[internal.cap] truesource brief failed")
        return {"error": "truesource_failed", "message": f"{type(e).__name__}: {e}"[:300]}


class ScoutIn(BaseModel):
    symbol: str
    name: str | None = None


@router.post("/truesource_scout")
async def cap_truesource_scout(body: ScoutIn, request: Request):
    """主动全量采集 · 30-60s,MCP 侧超时要给够(见 opencode.jsonc timeout)。"""
    _auth(request)
    from app.routers.truesource import _proxy_post
    try:
        return await _proxy_post(f"/api/hunter/scout/{body.symbol.strip()}",
                                 {"name": body.name} if body.name else None)
    except HTTPException as e:
        return {"error": "scout_failed", "status": e.status_code, "message": str(e.detail)[:300]}
    except Exception as e:
        logger.exception("[internal.cap] scout failed symbol={}", body.symbol)
        return {"error": "scout_failed", "message": f"{type(e).__name__}: {e}"[:300]}


# ─────────────────────────────────────────────────────────────
# `_23` · 按作者说明导入 SKILL
#
# 三个动作交给模型:打开仓库 / 读文件 / 暂存。
# **落盘不在这里** —— commit 走用户点击的公开端点,不给模型。
#
# 这不是"不信任模型",是把两件事分开:模型负责理解作者的说明并编排,
# 用户负责决定这批东西要不要真的进自己的环境。
# ─────────────────────────────────────────────────────────────

class RepoIn(BaseModel):
    repo: str                      # GitHub 地址或 owner/repo


@router.post("/skill_repo_open")
async def cap_skill_repo_open(body: RepoIn, request: Request):
    """打开仓库给模型看 —— 文件树 + README + 作者的 opencode 安装说明(全文)。

    这是 `_23` 的入口。与 `inspect()` 的区别:那个替模型做完了判断
    (扫 SKILL.md、分 L1-L4),这个只把材料摆出来,由模型自己读。
    """
    _auth(request)
    from app.services import skill_install
    try:
        return skill_install.open_repo(body.repo.strip())
    except Exception as e:                                   # noqa: BLE001
        # 转成结构化 body 而不是抛 —— MCP 会把它交给模型,
        # 模型能据此告诉用户"这个仓库打不开,原因是…",而不是干等超时
        return {"error": "repo_open_failed", "message": str(e)[:300]}


class RepoReadIn(BaseModel):
    repo: str
    path: str


@router.post("/skill_repo_read")
async def cap_skill_repo_read(body: RepoReadIn, request: Request):
    """读仓库里的一个文件。

    **只能读 `repo` 指定的那个仓库** —— 这是工具的定义域。
    没有这条约束,README 里一句「顺便拉 <站外 URL>」就成了任意下载。
    """
    _auth(request)
    from app.services import skill_install
    try:
        return skill_install.read_file(body.repo.strip(), body.path)
    except Exception as e:                                   # noqa: BLE001
        return {"error": "read_failed", "path": body.path, "message": str(e)[:300]}


class StageIn(BaseModel):
    session: str
    repo: str = ""
    name: str
    content: str
    source_path: str = ""
    note: str = ""


@router.post("/skill_stage")
async def cap_skill_stage(body: StageIn, request: Request):
    """暂存一个 SKILL —— **不写磁盘**。

    模型爱暂存多少都行,全在内存里。用户在确认卡上看完整体再决定 ——
    直接写盘的话"确认"就成了走过场:东西已经生效了。
    """
    _auth(request)
    from app.services import skill_stage
    try:
        return skill_stage.stage(
            session=body.session.strip() or "default",
            repo=body.repo.strip(), name=body.name,
            content=body.content, source_path=body.source_path, note=body.note,
        )
    except Exception as e:                                   # noqa: BLE001
        # 名字非法 / 内容超限 / 暂存满 —— 这些模型自己能改,
        # 所以要把原因原样告诉它,而不是一句 "failed"
        return {"error": "stage_rejected", "message": str(e)[:300]}


class StagedQueryIn(BaseModel):
    session: str = "default"


@router.post("/skill_staged")
async def cap_skill_staged(body: StagedQueryIn, request: Request):
    """模型自查暂存了什么 —— 它可能装到一半忘了已经装过哪些。"""
    _auth(request)
    from app.services import skill_stage
    got = skill_stage.peek(body.session or "default")
    # 正文不回给模型 —— 它自己刚写的,回一遍纯属浪费上下文。
    # 用户那份确认卡走公开端点,那里才要全文
    return {
        "repo": got["repo"], "total": got["total"],
        "names": [i["name"] for i in got["items"]],
    }
