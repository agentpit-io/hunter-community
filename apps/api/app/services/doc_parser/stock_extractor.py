"""从上传文档（文件名 + 正文前 3 万字）中自动识别关联的 A 股 / 港股 / 美股标的。"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any

from loguru import logger

# A 股 6 位代码（沪主板 60xxxx、科创板 688xxx、深主板 00xxxx、创业板 30xxxx）
_CN_CODE_RE = re.compile(r"(?<![0-9])((?:60[0135]\d{3}|688\d{3}|00[0123]\d{3}|30[01]\d{3}))(?![0-9])")
# 港股代码（如 00700.HK / 0700.HK / 9988.HK，或括号内的 00700 / 09988）
_HK_CODE_RE = re.compile(r"(?<![0-9])(\d{4,5})\.HK\b|[(（](0\d{4})[)）]", re.IGNORECASE)
# 美股常见括注格式（如 (NVDA) / (AAPL.US) / NASDAQ: GOOG）
_US_TICKER_RE = re.compile(
    r"(?:NASDAQ|NYSE|AMEX)\s*[:：]\s*([A-Z]{1,5})\b|[(（]([A-Z]{2,5})(?:\.US|\.O|\.N)?[)）]"
)

# 排除极易误判为代码的年份或常见纯数字（如 202401、202501、000000、100000 等）
_FALSE_POSITIVE_CODES = {
    "000000", "100000", "200000", "300000", "600000",
    "202001", "202002", "202003", "202004", "202005", "202006", "202007", "202008", "202009", "202010", "202011", "202012",
    "202101", "202102", "202103", "202104", "202105", "202106", "202107", "202108", "202109", "202110", "202111", "202112",
    "202201", "202202", "202203", "202204", "202205", "202206", "202207", "202208", "202209", "202210", "202211", "202212",
    "202301", "202302", "202303", "202304", "202305", "202306", "202307", "202308", "202309", "202310", "202311", "202312",
    "202401", "202402", "202403", "202404", "202405", "202406", "202407", "202408", "202409", "202410", "202411", "202412",
    "202501", "202502", "202503", "202504", "202505", "202506", "202507", "202508", "202509", "202510", "202511", "202512",
    "202601", "202602", "202603", "202604", "202605", "202606", "202607", "202608", "202609", "202610", "202611", "202612",
}

_US_STOP_WORDS = {
    "PDF", "USD", "RMB", "CNY", "HKD", "EPS", "ROE", "ROA", "ROIC", "TTM",
    "YOY", "QOQ", "MOM", "CAGR", "EBITDA", "EBIT", "GAAP", "IFRS", "CEO",
    "CFO", "CTO", "COO", "IPO", "ETF", "LOF", "FOF", "OTC", "MACD", "KDJ",
    "RSI", "BOLL", "EMA", "SMA", "VWAP", "NAV", "AUM", "PE", "PB", "PS",
    "PEG", "DCF", "WACC", "IRR", "NPV", "CSV", "XLS", "XLSX", "DOC", "DOCX",
}

# 内置高频龙头股票字典（确保即使在无数据库或离线单测环境下也能 100% 精准识别）
_BUILTIN_STOCKS: list[tuple[str, str, str]] = [
    # A 股核心标的
    ("600519", "贵州茅台", "CN_A"),
    ("000858", "五粮液", "CN_A"),
    ("000568", "泸州老窖", "CN_A"),
    ("600809", "山西汾酒", "CN_A"),
    ("300750", "宁德时代", "CN_A"),
    ("002594", "比亚迪", "CN_A"),
    ("601899", "紫金矿业", "CN_A"),
    ("601318", "中国平安", "CN_A"),
    ("600036", "招商银行", "CN_A"),
    ("601166", "兴业银行", "CN_A"),
    ("000001", "平安银行", "CN_A"),
    ("600900", "长江电力", "CN_A"),
    ("601088", "中国神华", "CN_A"),
    ("601857", "中国石油", "CN_A"),
    ("600028", "中国石化", "CN_A"),
    ("601919", "中远海控", "CN_A"),
    ("000333", "美的集团", "CN_A"),
    ("000651", "格力电器", "CN_A"),
    ("600276", "恒瑞医药", "CN_A"),
    ("300760", "迈瑞医疗", "CN_A"),
    ("603259", "药明康德", "CN_A"),
    ("688981", "中芯国际", "CN_A"),
    ("601138", "工业富联", "CN_A"),
    ("002475", "立讯精密", "CN_A"),
    ("300308", "中际旭创", "CN_A"),
    ("300502", "新易盛", "CN_A"),
    ("002463", "沪电股份", "CN_A"),
    ("688041", "海光信息", "CN_A"),
    ("688256", "寒武纪", "CN_A"),
    ("603019", "中科曙光", "CN_A"),
    ("002230", "科大讯飞", "CN_A"),
    ("300059", "东方财富", "CN_A"),
    ("600030", "中信证券", "CN_A"),
    ("601012", "隆基绿能", "CN_A"),
    ("300274", "阳光电源", "CN_A"),
    ("002466", "天齐锂业", "CN_A"),
    ("002460", "赣锋锂业", "CN_A"),
    ("600309", "万华化学", "CN_A"),
    ("002027", "分众传媒", "CN_A"),
    ("002001", "新和成", "CN_A"),
    ("601600", "中国铝业", "CN_A"),
    ("000933", "神火股份", "CN_A"),
    ("601888", "中国中免", "CN_A"),
    # 港股核心标的
    ("00700", "腾讯控股", "HK"),
    ("09988", "阿里巴巴", "HK"),
    ("03690", "美团", "HK"),
    ("01810", "小米集团", "HK"),
    ("09618", "京东集团", "HK"),
    ("09999", "网易", "HK"),
    ("09888", "百度集团", "HK"),
    ("01024", "快手", "HK"),
    ("02015", "理想汽车", "HK"),
    ("09868", "小鹏汽车", "HK"),
    ("09866", "蔚来", "HK"),
    ("00941", "中国移动", "HK"),
    ("00883", "中国海洋石油", "HK"),
    ("01211", "比亚迪股份", "HK"),
    ("00981", "中芯国际", "HK"),
    ("01378", "中国宏桥", "HK"),
    ("02899", "紫金矿业", "HK"),
    ("00388", "香港交易所", "HK"),
    ("01299", "友邦保险", "HK"),
    ("02318", "中国平安", "HK"),
    # 美股核心标的
    ("NVDA", "英伟达", "US"),
    ("AAPL", "苹果", "US"),
    ("MSFT", "微软", "US"),
    ("GOOG", "谷歌", "US"),
    ("GOOGL", "Alphabet", "US"),
    ("AMZN", "亚马逊", "US"),
    ("META", "Meta", "US"),
    ("TSLA", "特斯拉", "US"),
    ("TSM", "台积电", "US"),
    ("AVGO", "博通", "US"),
    ("AMD", "超威半导体", "US"),
    ("INTC", "英特尔", "US"),
    ("NFLX", "奈飞", "US"),
    ("BABA", "阿里巴巴(美股)", "US"),
    ("PDD", "拼多多", "US"),
    ("JD", "京东(美股)", "US"),
    ("BIDU", "百度(美股)", "US"),
    ("COIN", "Coinbase", "US"),
    ("PLTR", "Palantir", "US"),
    ("SMCI", "超微电脑", "US"),
]

_STOCK_DICT_CACHE: tuple[dict[str, str], dict[str, dict[str, str]]] | None = None


def _guess_market(code: str) -> str:
    c = code.upper().strip()
    if c.endswith(".HK") or (c.isdigit() and len(c) == 5):
        return "HK"
    if c.isdigit() and len(c) == 6:
        return "CN_A"
    return "US"


def _normalize_market(m: str, code: str) -> str:
    m_up = (m or "").upper().strip()
    if m_up in ("CN", "A", "SH", "SZ", "BJ", "CN_A"):
        return "CN_A"
    if m_up in ("HK", "HKEX"):
        return "HK"
    if m_up in ("US", "NASDAQ", "NYSE", "AMEX"):
        return "US"
    return _guess_market(code)


def _load_stock_dict() -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """加载本地已知股票名称字典（内置高频龙头 + config.py + 数据库 stocks / stocks_catalog）。"""
    global _STOCK_DICT_CACHE
    if _STOCK_DICT_CACHE is not None:
        return _STOCK_DICT_CACHE

    name_to_code: dict[str, str] = {}
    code_to_info: dict[str, dict[str, str]] = {}

    for code, name, market in _BUILTIN_STOCKS:
        code_to_info[code] = {"code": code, "name": name, "market": market}
        # 若同名有多地上市（如紫金矿业 A/H），名称优先映射到先登记的 A 股代码
        if name not in name_to_code:
            name_to_code[name] = code

    # 尝试从 config.py 补充
    try:
        import config  # type: ignore

        for item in getattr(config, "DEFAULT_STOCKS", []) or []:
            c = str(item.get("code", "")).strip()
            n = str(item.get("name", "")).strip()
            m = _normalize_market(str(item.get("market", "")), c)
            if c and n:
                code_to_info.setdefault(c, {"code": c, "name": n, "market": m})
                name_to_code.setdefault(n, c)
    except Exception:
        pass

    # 尝试从数据库 stocks_catalog / stocks 补充（若连接失败则静默跳过，保证单测无 DB 也可跑）
    try:
        from app.services.database import get_conn

        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT code, name, market FROM stocks WHERE enabled = TRUE LIMIT 2000")
            for c, n, m in cur.fetchall():
                c_s = str(c or "").strip()
                n_s = str(n or "").strip()
                if c_s and n_s:
                    mkt = _normalize_market(str(m or ""), c_s)
                    code_to_info.setdefault(c_s, {"code": c_s, "name": n_s, "market": mkt})
                    if len(n_s) >= 2:
                        name_to_code.setdefault(n_s, c_s)
        except Exception:
            conn.rollback()
        try:
            cur.execute("SELECT code, name, market FROM stocks_catalog LIMIT 6000")
            for c, n, m in cur.fetchall():
                c_s = str(c or "").strip()
                n_s = str(n or "").strip()
                if c_s and n_s:
                    mkt = _normalize_market(str(m or ""), c_s)
                    code_to_info.setdefault(c_s, {"code": c_s, "name": n_s, "market": mkt})
                    if len(n_s) >= 2:
                        name_to_code.setdefault(n_s, c_s)
        except Exception:
            conn.rollback()
        conn.close()
    except Exception as e:
        logger.debug("[stock_extractor] DB catalog load skipped: {}", e)

    _STOCK_DICT_CACHE = (name_to_code, code_to_info)
    return _STOCK_DICT_CACHE


def extract_stocks_from_text(filename: str, text_sample: str, max_results: int = 8) -> list[dict[str, Any]]:
    """扫描文件名与正文样本，返回按提及频次排序的股票列表：
    [{"code": "600519", "name": "贵州茅台", "market": "CN_A", "mentions": 18}, ...]
    """
    # 文件名权重最高（乘 5 倍计频，因为研报/年报标题通常直接点名主角公司）
    combined = ((filename or "") + "\n") * 5 + (text_sample[:30_000] if text_sample else "")
    counts: Counter[str] = Counter()

    name_to_code, code_to_info = _load_stock_dict()

    # 1. 匹配本地已知股票名称字典
    for name, code in name_to_code.items():
        if len(name) >= 2 and name in combined:
            occ = combined.count(name)
            if occ > 0:
                counts[code] += occ * 2  # 中文名称命中置信度极高

    # 2. 匹配 6 位 A 股代码
    for m in _CN_CODE_RE.finditer(combined):
        c = m.group(1)
        if c in _FALSE_POSITIVE_CODES:
            continue
        # 排除明显是年份+月份的数字（2018xx ~ 2029xx）
        if c.startswith(("201", "202")):
            continue
        if c in code_to_info:
            counts[c] += 2
        else:
            # 若不在已知字典中，仅当上下文带括号或「代码/证券/股票/.SH/.SZ」时才计入，避免纯数字噪音
            start = max(0, m.start() - 12)
            end = min(len(combined), m.end() + 12)
            ctx = combined[start:end]
            if re.search(r"[（(]|代码|股票|证券|\.SH|\.SZ", ctx, re.IGNORECASE):
                counts[c] += 1

    # 3. 匹配 .HK 港股代码
    for m in _HK_CODE_RE.finditer(combined):
        raw_hk = m.group(1) or m.group(2) or ""
        if not raw_hk:
            continue
        hk_code = raw_hk.zfill(5)
        if hk_code in code_to_info:
            counts[hk_code] += 3
        else:
            counts[hk_code] += 2
            code_to_info[hk_code] = {"code": hk_code, "name": f"{hk_code}.HK", "market": "HK"}

    # 4. 匹配美股 Ticker
    for m in _US_TICKER_RE.finditer(combined):
        ticker = (m.group(1) or m.group(2) or "").strip().upper()
        if ticker and ticker not in _US_STOP_WORDS:
            if ticker in code_to_info:
                counts[ticker] += 3

    results: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for code, score in counts.most_common(max_results * 2):
        info = code_to_info.get(code, {"code": code, "name": code, "market": _guess_market(code)})
        # 若同一个公司名称由于名称匹配+H股代码同时出现，优先保留同时显式命中代码的条目或高分条目
        dedup_key = f"{info['code']}"
        if dedup_key in seen_names:
            continue
        seen_names.add(dedup_key)
        results.append({
            "code": info["code"],
            "name": info["name"],
            "market": info["market"],
            "mentions": int(score),
        })
        if len(results) >= max_results:
            break
    return results
