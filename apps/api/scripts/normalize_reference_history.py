import argparse, csv, gzip, hashlib, importlib.util, json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

parser = argparse.ArgumentParser(description='Normalize an archived reference snapshot without database writes.')
parser.add_argument('--input',required=True)
parser.add_argument('--end',default='2026-10-09')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
work = Path(args.input)
module = importlib.util.spec_from_file_location('reference',root/'app/services/reference_history.py')
r = importlib.util.module_from_spec(module)
module.loader.exec_module(r)
universe = json.loads((work/'reference-universe.json').read_text(encoding='utf-8'))
coverage = {(market,code): {'market':market,'code':code,'financial_rows':0,
    'financial_dated':0,'dividend_rows':0,'dividend_signal_ready':0,
    'dividend_cash_ready':0,'event_rows':0,'hk_filings_complete':None}
    for market,codes in universe.items() for code in codes}
records = []
seen = set()

def put(kind,market,code,row,source,path):
    if (market,code) not in coverage:
        return
    stamp = row.get('period_end') or row.get('ex_date') or row.get('published_on')
    if not stamp or not '2023-01-01' <= stamp <= args.end:
        return
    if row.get('disclosed_on') and row['disclosed_on'] > args.end:
        return
    fingerprint = hashlib.sha256(json.dumps([kind,market,code,row,source],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if fingerprint in seen:
        return
    seen.add(fingerprint)
    entry = dict(kind=kind,market=market,code=code,source=source,raw_file=path,**{k:v for k,v in row.items() if k not in ('market','code')})
    records.append(entry)
    c = coverage[(market,code)]
    c[kind+'_rows'] += 1
    if kind == 'financial' and entry.get('available_after'):
        c['financial_dated'] += 1
    if kind == 'dividend':
        c['dividend_signal_ready'] += int(entry['signal_ready'])
        c['dividend_cash_ready'] += int(entry['cash_credit_ready'])

for path in sorted((work/'batch').glob('*.json.gz')):
    with gzip.open(path,'rt',encoding='utf-8') as f:
        envelope = json.load(f)
    market,code,kind,source = (envelope[k] for k in ('market','code','kind','source'))
    data = envelope['data']
    if kind == 'financial':
        rows = r.sec_financials(code,data) if market == 'US' else r.vendor_financials(market,code,data)
        for row in rows:
            put('financial',market,code,row,source,path.name)
    elif kind in ('dividend','dividend_supplement'):
        if kind == 'dividend_supplement':
            rows = data['records']
        elif market == 'US':
            rows = (((data.get('data') or {}).get('dividends') or {}).get('rows') or [])
        else:
            rows = data
        for row in rows:
            normalized = r.dividend(market,code,row)
            normalized['split_adjusted'] = data.get('splitAdjusted',False) if kind == 'dividend_supplement' else None
            put('dividend',market,code,normalized,source,path.name)
    elif kind == 'filings':
        if market == 'HK':
            coverage[(market,code)]['hk_filings_complete'] = data['complete']
        for row in data['records']:
            if market == 'US':
                published = r.day(row.get('filingDate'))
                accession = row['accessionNumber']
                url = f"https://www.sec.gov/Archives/edgar/data/{int(data['cik'])}/{accession.replace('-','')}/{row['primaryDocument']}"
                title = row['form']
                evidence_time = row.get('acceptanceDateTime')
            else:
                parsed = datetime.strptime(row['DATE_TIME'],'%d/%m/%Y %H:%M')
                published = parsed.date().isoformat()
                accession = row['NEWS_ID']
                url = 'https://www1.hkexnews.hk' + row['FILE_LINK']
                title = row['TITLE']
                evidence_time = parsed.isoformat()+'+08:00'
            put('event',market,code,dict(published_on=published,
                available_after=r.next_day(published),title=title,url=url,
                source_id=accession,published_time=evidence_time),source,path.name)

existing = work/'arena-reference-history/company_event.json.gz'
for row in json.load(gzip.open(existing,'rt',encoding='utf-8')):
    published = r.day(row['event_date'])
    put('event','CN_A',row['company_id'],dict(published_on=published,
        available_after=r.next_day(published),title=row['title'],url=row['url'],
        source_id=str(row['id']),event_type=row['event_type'],
        pit_status='announcement_date_vendor_metadata'),row['source'],existing.name)

out = work/'reference-ready'
out.mkdir(exist_ok=True)
with gzip.open(out/'normalized-reference.jsonl.gz','wt',encoding='utf-8') as f:
    for row in records:
        f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
with (out/'coverage.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer = csv.DictWriter(f,fieldnames=list(next(iter(coverage.values()))))
    writer.writeheader()
    writer.writerows(coverage.values())
summary = {}
for market in universe:
    rows = [v for k,v in coverage.items() if k[0]==market]
    summary[market] = {'instruments':len(rows)}
    for kind in ('financial','dividend','event'):
        summary[market][kind+'_rows'] = sum(v[kind+'_rows'] for v in rows)
        summary[market][kind+'_instruments'] = sum(v[kind+'_rows']>0 for v in rows)
    summary[market]['financial_dated_instruments'] = sum(v['financial_dated']>0 for v in rows)
    summary[market]['dividend_signal_ready_instruments'] = sum(v['dividend_signal_ready']>0 for v in rows)
    summary[market]['dividend_cash_ready_instruments'] = sum(v['dividend_cash_ready']>0 for v in rows)
    if market=='HK':
        summary[market]['complete_filings'] = sum(v['hk_filings_complete'] is True for v in rows)
(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2))
print('total normalized records',len(records))
