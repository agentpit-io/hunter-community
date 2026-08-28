"""数据中心接口 · /api/quant/data/*

方案见 doc/开源hunter-community/01详细工作目录/11量化策略/
      22_20260822_数据中心_技术方案.md §4.2

单独一个 router 而不是塞进 quant.py:数据下载和策略/回测是两件事,
quant.py 已经 600 多行了。
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, File, Request, UploadFile
from pydantic import BaseModel

from app.services.quant import data_center

log = logging.getLogger(__name__)

router = APIRouter(prefix="/quant/data", tags=["quant-data"])


def _uid(request: Request) -> str | None:
    u = getattr(request.state, "user_id", None)
    return str(u) if u else None


@router.get("/overview")
async def get_overview():
    """当前数据概览 · 数据页顶部那一排。

    `empty=true` 时前端提示"到「数据」页选一批股票下载" ——
    以前是开机自动下载,现在改成让用户自己决定(老板:
    「用户都不知道你就占用他的资源很不好」)。
    """
    return data_center.overview()


@router.post("/industries/sync")
async def sync_industries():
    """同步行业分类(新浪源 · 49 板块 · 约 1 分钟)。

    手工触发而不是自动跑 —— 和数据下载同一个原则:不替用户占资源。
    前端在「按行业选」但行业表是空的时候给一个「同步行业分类」按钮。
    """
    import asyncio as _aio
    from app.services.quant import industry_seed
    _aio.create_task(_aio.to_thread(industry_seed.seed))
    return {"ok": True, "message": "已开始同步 · 约 1 分钟 · 完成后刷新 /scopes"}


@router.get("/scopes")
async def get_scopes(request: Request):
    """可选范围 + 每个的股票数。前端拿它渲染①那一排和行业两级。"""
    return data_center.scopes(_uid(request))


class EstimateIn(BaseModel):
    scope: dict = {}                 # {kind, industries[], codes[]}
    span_months: int = 36            # 0 = 只补最新
    with_financial: bool = False
    keep_raw: bool = False


@router.post("/estimate")
async def post_estimate(body: EstimateIn, request: Request):
    """预估:只数 / 可跳过 / 耗时 / 磁盘。

    前端拖选项时是**本地算**的(速率写死在前端),点「开始下载」前
    调这个拿准确的可跳过数 —— 因为"哪些已经下过"只有后端知道。
    """
    return data_center.estimate(
        body.scope, body.span_months, body.with_financial,
        body.keep_raw, _uid(request),
    )


# ═══════════════════════════════════════════════════════════
# 下载任务
# ═══════════════════════════════════════════════════════════

class JobIn(BaseModel):
    scope: dict = {}
    span_months: int = 36
    with_financial: bool = False
    keep_raw: bool = False
    # 从哪里下:free(默认 · 腾讯/新浪)/ agentpit(平台付费源)/ custom(用户自己的)
    # **不传就是 free,老调用方行为一字不变**
    source: str | None = None
    custom: dict | None = None


@router.post("/jobs")
async def create_job(body: JobIn, request: Request):
    """建一个下载任务并开始跑。

    **同时只允许一个** —— 多个任务并发打同一个上游会互相拖慢并触发限流
    (实测:800 只不限速连着打,腾讯清一色 ReadTimeout)。
    """
    import asyncio as _aio
    from app.services.quant import data_job

    running = data_job.active_job()
    if running:
        return {"error": "job_running",
                "message": f"已经有一个任务在跑(#{running['id']})· 同时只允许一个",
                "job": running}

    est = data_center.estimate(body.scope, body.span_months, body.with_financial,
                              body.keep_raw, _uid(request))
    if not est["stocks"]:
        return {"error": "empty_scope", "message": est.get("note") or "这个范围没有股票"}

    # 通道校验放在建任务**之前** —— 让它跑起来再失败的话,
    # 用户已经等了几分钟,而且库里留下半截数据
    from app.services.quant import download_source as ds
    src = ds.normalize(getattr(body, "source", None))
    custom = getattr(body, "custom", None)
    bad = ds.validate(src, custom)
    if bad:
        return bad

    # 全都已经有数据了,不用下。
    #
    # **排在通道检查之后** —— 通道不可用是更根本的前提,而且"换个通道"
    # 比"换个时间范围"更可能是用户真正想做的事。
    #
    # 不挡的话会建一个空任务、瞬间跑完、在"最近任务"里留一条 done ——
    # 用户看到"完成"但什么都没发生,列表还被这种空任务塞满。
    # 实测:hs300 全部已下过时,连点几次就多出几条无意义记录。
    if not est.get("todo"):
        # **提示要看用户已经选了什么**。原来一律说"选「只补最新」",
        # 而用户明明已经选了它 —— 提示打转,看着像系统坏了。
        tip = ("已经是最新的了,没有要补的。"
               if (body.span_months or 0) <= 0 else
               "想补最近几天的话,把「下多长时间」选成「只补最新」。")
        return {"error": "nothing_to_do",
                "message": f"这 {est['stocks']} 只的数据都已经有了,不用重复下。{tip}"}


    # 通道信息塞进 scope 一起存 —— data_job 表不用加列(生产库改列有成本),
    # 而 scope 本来就是 jsonb
    scope = dict(body.scope or {})
    scope["source"] = src
    if src == ds.CUSTOM and custom:
        scope["custom"] = {"url": custom.get("url", ""), "key": custom.get("key", "")}

    jid = data_job.create(scope, body.span_months, body.with_financial,
                          body.keep_raw, est["stocks"], _uid(request))
    # 放线程里跑:一趟可能几小时,卡在事件循环里整个 API 就没响应了
    _aio.create_task(_aio.to_thread(data_job.run, jid))
    return {"ok": True, "job_id": jid, "estimate": est}


@router.get("/jobs")
async def list_jobs(limit: int = 20):
    from app.services.quant import data_job
    return {"jobs": data_job.recent(limit), "active": data_job.active_job()}


@router.get("/jobs/{job_id}")
async def get_job(job_id: int):
    from app.services.quant import data_job
    j = data_job.get(job_id)
    return j or {"error": "not_found"}


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: int):
    """暂停。worker 在另一个线程,没法直接打断 —— 它每 5 只回头读一次
    自己的 status,读到 paused 就自己退出。所以点了之后最多几秒生效。"""
    from app.services.quant import data_job
    ok = data_job.set_status(job_id, "paused", "用户暂停")
    return {"ok": ok}


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: int):
    """续跑 —— 已下载的不会重来(worker 开头会查 data_coverage 跳过)。"""
    import asyncio as _aio
    from app.services.quant import data_job

    running = data_job.active_job()
    if running:
        return {"error": "job_running",
                "message": f"已经有一个任务在跑(#{running['id']})"}
    if not data_job.set_status(job_id, "queued", "续跑"):
        return {"error": "not_found"}
    _aio.create_task(_aio.to_thread(data_job.run, job_id))
    return {"ok": True, "job_id": job_id}


# ═══════════════════════════════════════════════════════════
# 数据包导入
# ═══════════════════════════════════════════════════════════

@router.get("/packages")
async def list_packages():
    """列出 data-packages/ 里的数据包。

    识别不了的也列出来并说明为什么 —— 用户把包放进去却看不到它,
    比看到一条"这个文件不是数据包"更让人困惑。
    """
    from app.services.quant import package_import as pi
    return {"dir": str(pi.PACKAGE_DIR), "packages": pi.list_packages()}


@router.get("/packages/{file}")
async def inspect_package(file: str):
    """导入前的预检 —— 页面上那张确认卡靠它。"""
    from app.services.quant import package_import as pi
    return pi.inspect(file)


@router.post("/packages/upload")
async def upload_package(file: UploadFile = File(...)):
    """从浏览器上传数据包 —— 「浏览…」按钮走这条。

    ## 为什么是上传,而不是"让用户填个本地路径"

    浏览器**不允许网页读取本地路径**。用户点浏览选了文件,网页拿到的是
    文件对象,拿不到 `D:/下载/hunter-data.tar` 这样的路径;就算用户手打
    路径进来,网页也没权限去读。这是浏览器的安全边界,不是我们能改的。

    而且就算浏览器允许也没用:容器只挂了 `./data-packages`,
    宿主机的 `D:/下载/` **对容器根本不存在**。

    用户感知不到区别 —— 他看到的还是"点浏览、选文件、导进去了"。
    """
    import re, shutil, tempfile
    from app.services.quant import package_import as pi

    name = (file.filename or "").strip()
    # 用户可控的文件名直接拼进路径 = 目录穿越。只留最后一段并挡掉可疑字符
    name = re.sub(r"[^\w.\-]", "_", name.replace("\\", "/").split("/")[-1])
    if not name.endswith(".tar"):
        return {"error": "bad_type",
                "message": f"只支持 .tar 数据包 —— 你选的是「{file.filename}」"}

    pi.PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
    dest = pi.PACKAGE_DIR / name

    # **先写临时文件再改名。** 直接写目标名的话,上传中途断了会在目录里
    # 留下一个半截包,而列表会把它当成"损坏的数据包"列出来 —— 用户看到
    # 一条自己没放过的坏数据,完全不知道哪来的
    tmp = None
    size = 0
    try:
        with tempfile.NamedTemporaryFile(dir=pi.PACKAGE_DIR, prefix=".upload-",
                                         suffix=".part", delete=False) as f:
            tmp = f.name
            # **流式落盘,不能 await file.read() 一次读进内存** ——
            # 100 MB 的包会把内存打满,而且不报错,只是容器 OOM 被杀
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                size += len(chunk)
        if size == 0:
            return {"error": "empty", "message": "文件是空的 —— 可能没下载完"}
        shutil.move(tmp, dest)
        tmp = None
    except Exception as e:                                     # noqa: BLE001
        return {"error": type(e).__name__, "message": f"上传失败:{str(e)[:120]}"}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # 落盘之后立刻校验 —— 让用户当场知道这个包能不能用,
    # 而不是点了导入才发现不认识
    info = pi.inspect(name)
    if info.get("error"):
        return {"ok": True, "file": name, "bytes": size, "valid": False,
                "error": info["error"], "message": info.get("message")}
    return {"ok": True, "file": name, "bytes": size, "valid": True, "info": info}


class SourceTestIn(BaseModel):
    url: str
    key: str


@router.post("/source/test")
async def test_source(body: SourceTestIn):
    """测试用户自己填的数据源通不通。

    **这个按钮是必须的** —— 不测的话 Key 填错要跑十分钟才发现,
    而那十分钟里用户不知道是在下载还是卡住了。
    """
    from app.services.quant import download_source as ds
    return ds.test_connection(body.url, body.key)


class ImportIn(BaseModel):
    file: str


@router.post("/packages/import")
async def import_package(body: ImportIn, request: Request):
    """导入数据包。复用 data_job 的进度/暂停/取消。"""
    import asyncio as _aio
    from app.services.quant import data_job, package_import as pi

    running = data_job.active_job()
    if running:
        return {"error": "job_running",
                "message": f"已经有一个任务在跑(#{running['id']})· 同时只允许一个"}

    info = pi.inspect(body.file)
    if info.get("error"):
        return info

    jid = data_job.create({"kind": "package", "file": body.file},
                          0, False, False, len(info.get("volumes") or []),
                          _uid(request))
    _aio.create_task(_aio.to_thread(pi.run, jid, body.file))
    return {"ok": True, "job_id": jid, "info": info}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int):
    from app.services.quant import data_job
    return {"ok": data_job.set_status(job_id, "canceled", "用户取消")}
