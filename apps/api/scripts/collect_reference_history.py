"""Archive public financial/dividend/filing history; never writes a trading database.

Requires requests and akshare. Raw snapshots are resumable and checksummed.
Report periods, latest values and retrospective calendars are not PIT signals.
"""
import argparse
import concurrent.futures
from datetime import datetime, timezone
import gzip
import hashlib
import json
from io import StringIO
from pathlib import Path
import threading
import time

import requests

UA = {'User-Agent': 'Hunter Research hunter@agentpit.io'}
NASDAQ_HEADERS = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
                  'Origin': 'https://www.nasdaq.com', 'Referer': 'https://www.nasdaq.com/'}
_sec_lock = threading.Lock()
_sec_last = 0.0


def get_json(url, **kwargs):
    global _sec_last
    if 'sec.gov/' in url:
        with _sec_lock:
            time.sleep(max(0, .35 - (time.monotonic() - _sec_last)))
            _sec_last = time.monotonic()
    response = requests.get(url, timeout=(8, 25), **kwargs)
    response.raise_for_status()
    return response.json()


def sec_submissions(cik, start, end):
    root = get_json(f'https://data.sec.gov/submissions/CIK{int(cik):010d}.json', headers=UA)
    if int(root['cik']) != int(cik):
        raise ValueError('SEC entity mismatch')
    filings = root.get('filings', {})
    blocks = [filings.get('recent', {})]
    files = []
    for item in filings.get('files', []):
        if item['filingTo'] >= start and item['filingFrom'] <= end:
            blocks.append(get_json('https://data.sec.gov/submissions/' + item['name'], headers=UA))
            files.append(item['name'])
    records = {}
    for block in blocks:
        for i, day in enumerate(block.get('filingDate', [])):
            if start <= day <= end:
                row = {k: v[i] for k, v in block.items() if isinstance(v, list) and len(v) > i}
                records[row['accessionNumber']] = row
    return {'cik': cik, 'entityName': root.get('name'), 'tickers': root.get('tickers'),
            'supplementalFiles': files, 'records': list(records.values())}


def hk_submissions(code, start, end):
    response = requests.get('https://www1.hkexnews.hk/search/prefix.do',
        params={'callback':'cb','lang':'ZH','type':'A','name':code,'market':'SEHK'},
        headers=NASDAQ_HEADERS, timeout=(8,25))
    response.raise_for_status()
    text = response.text
    info = json.loads(text[text.index('(')+1:text.rindex(')')]).get('stockInfo', [])
    match = [r for r in info if str(r.get('code', '')).zfill(5) == code]
    if len(match) != 1:
        raise ValueError('HKEX stock identifier missing or ambiguous')
    params = {'sortDir':'0','sortByOptions':'DateTime','category':'0','market':'SEHK',
              'stockId':match[0]['stockId'],'documentType':'-1',
              'fromDate':start.replace('-',''),'toDate':end.replace('-',''),
              'title':'','searchType':'1','t1code':'-2','t2Gcode':'-2','t2code':'-2',
              'rowRange':'200','lang':'zh'}
    data = get_json('https://www1.hkexnews.hk/search/titleSearchServlet.do',
                    params=params,headers=NASDAQ_HEADERS)
    if str(data.get('hasNextRow','false')).lower() == 'true':
        total = int(data['recordCnt'])
        if 0 < total <= 10000:
            params['rowRange'] = str(total)
            data = get_json('https://www1.hkexnews.hk/search/titleSearchServlet.do',
                            params=params,headers=NASDAQ_HEADERS)
    rows = data.get('result')
    rows = json.loads(rows) if isinstance(rows,str) else (rows or [])
    # Keep response metadata: a truncated page is explicitly incomplete.
    return {'stock':match[0],'records':rows,'metadata':{k:v for k,v in data.items() if k!='result'},
            'complete':str(data.get('hasNextRow', 'false')).lower() != 'true'
                and len(rows) == int(data.get('recordCnt', -1))}


def supplementary_dividends(code):
    import pandas as pd
    url = f'https://stockanalysis.com/stocks/{code.lower().replace(".", "-")}/dividend/'
    response = requests.get(url,headers=UA,timeout=(8,25))
    response.raise_for_status()
    # Public tables only; no paid history endpoints or authentication.
    if 'does not pay a dividend' in response.text.lower() or 'does not currently pay a dividend' in response.text.lower():
        return {'records':[],'url':url,'status':'public_page_says_no_current_dividend',
                'htmlSha256':hashlib.sha256(response.content).hexdigest()}
    tables = pd.read_html(StringIO(response.text))
    rows = []
    for table in tables:
        if not {'Ex-Dividend Date','Cash Amount','Record Date','Pay Date'} <= set(table.columns):
            continue
        for raw in table.to_dict(orient='records'):
            rows.append({'exOrEffDate':raw['Ex-Dividend Date'],'amount':raw['Cash Amount'],
                         'recordDate':raw['Record Date'],'paymentDate':raw['Pay Date'],
                         'declarationDate':None,'currency':'USD','type':'Cash'})
    if not rows:
        raise ValueError('No supported public dividend table; not proof of zero dividends')
    return {'records':rows,'url':url,'splitAdjusted':True,
            'htmlSha256':hashlib.sha256(response.content).hexdigest(),
            'announcementDateMissing':True,'publicRowsOnly':True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--universe', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--start', default='2024-01-01')
    parser.add_argument('--end', default='2026-10-09')
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--supplement-dividends', action='store_true')
    args = parser.parse_args()
    universe = json.loads(Path(args.universe).read_text(encoding='utf-8'))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    import akshare as ak
    # Bound legacy AKShare requests too; standalone process only.
    original = requests.sessions.Session.request
    def bounded(self, *a, **kw):
        kw.setdefault('timeout', (8,25))
        return original(self,*a,**kw)
    requests.sessions.Session.request = bounded
    ticker_map = get_json('https://www.sec.gov/files/company_tickers.json', headers=UA)
    mapping = {v['ticker'].replace('-', '.'):v['cik_str'] for v in ticker_map.values()}
    (output / 'sec-tickers.json').write_text(json.dumps(ticker_map),encoding='utf-8')
    jobs = []
    def add(market, code, kind, source, fn):
        jobs.append((f'{market}-{code}-{kind}',market,code,kind,source,fn))
    for code in universe['CN_A']:
        suffix = 'SH' if code.startswith(('6','9')) else 'SZ'
        add('CN_A',code,'financial','eastmoney',lambda c=code,s=suffix:
            ak.stock_financial_analysis_indicator_em(symbol=f'{c}.{s}',indicator='按报告期'))
        add('CN_A',code,'dividend','sina_via_akshare',lambda c=code:
            ak.stock_history_dividend_detail(symbol=c,indicator='分红'))
    for code in universe['HK']:
        add('HK',code,'financial','eastmoney',lambda c=code:
            ak.stock_financial_hk_analysis_indicator_em(symbol=c,indicator='报告期'))
        add('HK',code,'dividend','eastmoney',lambda c=code:ak.stock_hk_dividend_payout_em(symbol=c))
        add('HK',code,'filings','hkex',lambda c=code:hk_submissions(c,args.start,args.end))
    for code in universe['US']:
        cik = mapping.get(code)
        if cik:
            add('US',code,'financial','sec_companyfacts',lambda c=cik:
                get_json(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(c):010d}.json',headers=UA))
            add('US',code,'filings','sec_submissions',lambda c=cik:sec_submissions(c,args.start,args.end))
        else:
            add('US',code,'financial','sec_companyfacts',lambda: (_ for _ in ()).throw(ValueError('No SEC CIK mapping')))
        add('US',code,'dividend','nasdaq',lambda c=code:
            get_json(f'https://api.nasdaq.com/api/quote/{c}/dividends',params={'assetclass':'stocks'},headers=NASDAQ_HEADERS))
        if args.supplement_dividends:
            path = output / f'US-{code}-dividend.json.gz'
            if path.exists():
                with gzip.open(path,'rt',encoding='utf-8') as f:
                    previous = json.load(f)['data']
                if not (((previous.get('data') or {}).get('dividends') or {}).get('rows')):
                    add('US',code,'dividend_supplement','stockanalysis_public_spglobal',
                        lambda c=code:supplementary_dividends(c))
    lock = threading.Lock()
    manifest = {}
    def run(job):
        key,market,code,kind,source,fn = job
        path = output / f'{key}.json.gz'
        started = time.monotonic()
        try:
            if path.exists():
                with gzip.open(path,'rt',encoding='utf-8') as f:
                    envelope = json.load(f)
            else:
                envelope = None
            if envelope is None or (source == 'hkex' and not envelope['data'].get('complete')):
                data = fn()
                if hasattr(data,'to_json'):
                    data = json.loads(data.to_json(orient='records',date_format='iso',force_ascii=False))
                envelope = {'market':market,'code':code,'kind':kind,'source':source,
                    'fetchedAt':datetime.now(timezone.utc).isoformat(),'data':data}
                raw = json.dumps(envelope,ensure_ascii=False,separators=(',',':')).encode()
                temporary = path.with_suffix('.part')
                with gzip.open(temporary,'wb') as f:
                    f.write(raw)
                temporary.replace(path)
            raw = json.dumps(envelope,ensure_ascii=False,separators=(',',':')).encode()
            result = {'status':'downloaded','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                      'seconds':round(time.monotonic()-started,2),'source':source}
        except Exception as exc:
            result = {'status':'error','error':type(exc).__name__,'message':str(exc)[:200],'source':source}
        with lock:
            manifest[key] = result
            (output / 'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            print(len(manifest),'/',len(jobs),key,result['status'],flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(args.workers,3))) as pool:
        list(pool.map(run,jobs))
    print('complete',len(manifest),'errors',sum(r['status']=='error' for r in manifest.values()),flush=True)


if __name__ == '__main__':
    main()
