"""一手情报数据源(gm端): 美股SEC官方公告 + 港股披露易公告。

US: SEC EDGAR submissions API(官方免费)
  - ticker→CIK: https://www.sec.gov/files/company_tickers.json (缓存1天)
  - 公告: https://data.sec.gov/submissions/CIK{10位}.json 取 recent filings
  - SEC要求UA带联系方式
HK: 披露易 HKEXnews
  - stockId: https://www1.hkexnews.hk/search/prefix.do (缓存1天)
  - 公告: titleSearchServlet.do 返回JSON(中文标题+PDF链接)
统一返回 [{form, title, date, url}], Redis缓存30分钟。
"""
import json
import logging
import requests

from app.services.gm.yahoo_hk import _cache_get, _cache_set

log = logging.getLogger(__name__)

_SEC_UA = {"User-Agent": "Hunter Research hunter@agentpit.io"}
_FORMS_KEEP = {"8-K", "10-Q", "10-K", "6-K", "20-F", "S-1", "DEF 14A", "4"}


def _cik_of(ticker: str) -> str | None:
    key = "gm:sec:cikmap"
    m = _cache_get(key)
    if m is None:
        try:
            r = requests.get("https://www.sec.gov/files/company_tickers.json",
                             headers=_SEC_UA, timeout=20)
            r.raise_for_status()
            m = {v["ticker"].upper(): str(v["cik_str"]) for v in r.json().values()}
            _cache_set(key, m, 86400)
        except Exception as e:
            log.warning("sec cik map failed: %s", e)
            return None
    return m.get(ticker.upper())


def us_filings(ticker: str, limit: int = 10) -> list[dict]:
    ticker = ticker.upper()
    ck = f"gm:filings:US:{ticker}"
    cached = _cache_get(ck)
    if cached is not None:
        return cached[:limit]
    cik = _cik_of(ticker)
    if not cik:
        return []
    try:
        r = requests.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                         headers=_SEC_UA, timeout=20)
        r.raise_for_status()
        recent = (r.json().get("filings") or {}).get("recent") or {}
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accs = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])
        out = []
        for i in range(min(len(forms), 60)):
            if forms[i] not in _FORMS_KEEP:
                continue
            acc = accs[i].replace("-", "")
            out.append({
                "form": forms[i],
                "title": _form_label(forms[i]),
                "date": dates[i],
                "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{docs[i]}",
            })
            if len(out) >= 15:
                break
        _cache_set(ck, out, 1800)
        return out[:limit]
    except Exception as e:
        log.warning("sec filings %s failed: %s", ticker, e)
        return []


def _form_label(form: str) -> str:
    labels = {
        "8-K": "重大事件报告", "10-Q": "季度报告", "10-K": "年度报告",
        "6-K": "外国公司报告", "20-F": "外国公司年报", "S-1": "上市注册",
        "DEF 14A": "股东大会文件", "4": "内部人交易",
    }
    return labels.get(form, form)


def _hk_stock_id(code: str) -> str | None:
    code5 = code.zfill(5)
    key = f"gm:hkexid:{code5}"
    cached = _cache_get(key)
    if cached is not None:
        return cached or None
    try:
        r = requests.get("https://www1.hkexnews.hk/search/prefix.do",
                         params={"callback": "cb", "lang": "ZH", "type": "A",
                                 "name": code5, "market": "SEHK"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        txt = r.text
        j = json.loads(txt[txt.index("(") + 1: txt.rindex(")")])
        recs = j.get("stockInfo") or []
        sid = str(recs[0]["stockId"]) if recs else ""
        _cache_set(key, sid, 86400)
        return sid or None
    except Exception as e:
        log.warning("hkex prefix %s failed: %s", code, e)
        return None


def hk_filings(code: str, limit: int = 10) -> list[dict]:
    code5 = code.zfill(5)
    ck = f"gm:filings:HK:{code5}"
    cached = _cache_get(ck)
    if cached is not None:
        return cached[:limit]
    sid = _hk_stock_id(code5)
    if not sid:
        return []
    try:
        # 注意: 该接口只接受GET且lang=zh小写; DATE_TIME为 DD/MM/YYYY HH:mm
        r = requests.get("https://www1.hkexnews.hk/search/titleSearchServlet.do",
                         params={"sortDir": "0", "sortByOptions": "DateTime",
                                 "category": "0", "market": "SEHK", "stockId": sid,
                                 "documentType": "-1", "fromDate": "", "toDate": "",
                                 "title": "", "searchType": "1", "t1code": "-2",
                                 "t2Gcode": "-2", "t2code": "-2", "rowRange": "20",
                                 "lang": "zh"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        body = r.json()
        result = body.get("result")
        rows = json.loads(result) if isinstance(result, str) else (result or [])
        out = []
        for row in rows[:15]:
            raw_dt = (row.get("DATE_TIME") or "")[:10]
            p = raw_dt.split("/")
            date_iso = f"{p[2]}-{p[1]}-{p[0]}" if len(p) == 3 else raw_dt
            # SHORT_TEXT 更具体(含[股份購回]等分类), 去掉<br/>标记
            title = (row.get("SHORT_TEXT") or row.get("TITLE") or "")
            title = title.replace("<br/>", " ").replace("<br/>", " ").strip()
            link = row.get("FILE_LINK") or ""
            if link and not link.startswith("http"):
                link = "https://www1.hkexnews.hk" + link
            out.append({"form": "公告", "title": title, "date": date_iso, "url": link})
        _cache_set(ck, out, 1800)
        return out[:limit]
    except Exception as e:
        log.warning("hkex filings %s failed: %s", code, e)
        return []
