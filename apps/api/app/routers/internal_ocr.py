"""OCR · Bridge 内部端点 · 供 web BFF 拦截用户上传图片后调用

链路：
    浏览器上传截图 → apps/web BFF (/api/opencode/session/*/message 拦截 image parts)
                  → POST /api/internal/ocr/extract (本文件)
                  → 用 tesseract 抽文本
                  → BFF 拿到 text 后把 [图片 OCR 抽取内容]\n{text} 塞回 parts
                  → 转发到 opencode → LLM 看到纯文本 → 调 watchlist_add_batch

之所以 OCR 放后端而不是 BFF/opencode:
  · BFF 是 Node.js · 装 tesseract 或跑 Python 都不合适
  · opencode 只做 LLM 编排 · 加 OCR 依赖会污染镜像
  · api 已经装了 Python + 数据栈 · 加个 tesseract 二进制成本最低
"""
from __future__ import annotations
import base64
import io
import os
import re

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

router = APIRouter(prefix="/internal/ocr", tags=["ocr-bridge"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")


def _auth(request: Request) -> None:
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")


class OCRIn(BaseModel):
    # base64 编码的图片字节(不含 data:image/... 前缀 · BFF 侧剥掉)
    # 也接受完整 data URL · 自动兼容
    image_base64: str
    # 语言 · 默认中英混排 · 需要繁体加 chi_tra
    lang: str = "chi_sim+eng"


_DATA_URL_RE = re.compile(r"^data:image/[^;]+;base64,", re.IGNORECASE)


@router.post("/extract")
async def extract(body: OCRIn, request: Request):
    """从 base64 图片抽文本 · 返回 {text, lines[], engine}。

    lines[] 保留原始换行 · 前端截图里每行往往是一只股票 · 交给 LLM 前不要合并成一段。
    """
    _auth(request)

    b64 = _DATA_URL_RE.sub("", body.image_base64).strip()
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception as e:
        raise HTTPException(400, f"invalid base64: {e}")

    if len(raw) < 100:
        raise HTTPException(400, f"image too small ({len(raw)} bytes)")
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, f"image too large ({len(raw)} bytes > 10MB)")

    try:
        # 延迟 import · tesseract 二进制未装时启动阶段不炸,拉端点才报错
        from PIL import Image
        import pytesseract
    except ImportError as e:
        logger.exception("[ocr] pytesseract/Pillow import failed")
        raise HTTPException(500, f"OCR 依赖未就绪: {e}")

    try:
        img = Image.open(io.BytesIO(raw))
        # RGBA → RGB · tesseract 对 alpha 通道有时会画面异常
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")

        # 预处理链路 · 手机截图字号小、中英混排、股票代码是小字灰色 —— tesseract 直
        # 接吃原图漏一半代码。经验组合(2026-08 实测)最稳:
        #   ① 短边 <800 → 2x 放大(LANCZOS 抗锯齿)· 抬像素密度 · 数字最吃这个
        #   ② 灰度化 · RGB 会让 tesseract 分心去做颜色判别
        #   ③ PSM 6(单块统一文本) · 手机股票列表竖排短行,PSM 4/11/12 反而更差
        # 3x + 二值化实测**更差**(小字符会 blob 到一起),别加。
        min_side = min(img.width, img.height)
        if min_side < 800:
            img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
        proc = img.convert("L") if img.mode != "L" else img

        text = pytesseract.image_to_string(proc, lang=body.lang, config="--psm 6")
    except pytesseract.TesseractNotFoundError:
        logger.error("[ocr] tesseract binary not found · 需在 Dockerfile 装 tesseract-ocr")
        raise HTTPException(500, "OCR 引擎(tesseract)未安装")
    except Exception as e:
        logger.exception("[ocr] tesseract failed")
        raise HTTPException(500, f"OCR 失败: {type(e).__name__}: {e}")

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    logger.info("[ocr] extracted · bytes={} · lines={} · lang={}",
                len(raw), len(lines), body.lang)

    return {
        "text": text,
        "lines": lines,
        "engine": "tesseract",
        "lang": body.lang,
    }
