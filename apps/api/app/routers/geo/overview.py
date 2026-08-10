"""地缘冲突风险总览(公开市场数据, 读库) —— GET /api/geo/overview
背离度解读框架: 运费ETF与船东股背离为正且扩大=市场定价"航道关闭"(运价涨货量塌);
背离收敛/为负=定价"绕行"(船东量价齐升)。Redis缓存10分钟。"""
from fastapi import APIRouter

from app.services.geo import findata_geo
from app.services.gm.yahoo_hk import _cache_get, _cache_set

router = APIRouter()


@router.get("/overview")
async def geo_overview():
    ck = "geo:overview"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    div = findata_geo.divergence(60)
    jwc = findata_geo.jwc_events(6)
    note = None
    if div:
        d = div["latest"]["div"]
        if d >= 10:
            note = "运费ETF大幅跑赢船东股: 市场在定价航道'关闭'(运价涨但货量塌), 追高油运股需警惕"
        elif d <= -10:
            note = "船东股跑赢运费ETF: 市场在定价'绕行', 船东量价齐升逻辑成立"
        else:
            note = "运费与船东股走势基本同步, 暂无显著地缘定价信号"

    result = {
        "divergence": div, "jwc": jwc, "note": note,
        "sources": ["背离度: BWET vs FRO/DHT/INSW/TNK/STNG 每日自算",
                    "战争险区域: Lloyd's JWC 通函(官方)"],
        "disclaimer": "数据研究 · 不构成投资建议",
    }
    _cache_set(ck, result, 600)
    return result
