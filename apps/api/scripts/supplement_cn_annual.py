"""下载A股原始年报摘要，提取有明确年度列证据的基本每股收益。"""
import argparse,hashlib,json,re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from pathlib import Path
import pymupdf,requests

def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--report',required=True); ap.add_argument('--output',required=True)
    a=ap.parse_args(); out=Path(a.output); out.mkdir(parents=True,exist_ok=True)
    report=json.loads(Path(a.report).read_text())
    codes=sorted({c for t in report['tests'] if t['market']=='CN_A' and t['family']=='value_hold' for c in t['coverage']})
    def collect(code):
        rows=[]; audit=[]
        for year in [2024,2025]:
            rawpath=out/(code+'-'+str(year)+'-announcements.json')
            if rawpath.exists(): data=json.loads(rawpath.read_text())
            else:
                params={'sr':-1,'page_size':100,'page_index':1,'ann_type':'A','stock_list':code,
                    'begin_time':str(year+1)+'-01-01','end_time':str(year+1)+'-06-30'}
                r=requests.get('https://np-anotice-stock.eastmoney.com/api/security/ann',params=params,timeout=(8,25)); r.raise_for_status(); data=r.json()
                if not data.get('success'): raise ValueError('Announcement source failed')
                rawpath.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
            found=[r for r in data['data']['list'] if re.search(str(year)+r'年?年度报告摘要',r['title']) and not re.search('英文|取消|更正|修订',r['title'])]
            full=[r for r in data['data']['list'] if re.search(str(year)+r'年?年度报告(?:$|[（(])',r['title']) and not re.search('英文|取消|更正|修订',r['title'])]
            found+=full
            if not found:
                audit.append(dict(year=year,status='missing_original_summary',total_hits=data['data'].get('total_hits'))); continue
            for item in found:
                key=code+'-'+item['art_code']; pdf=out/(key+'.pdf'); url='https://pdf.dfcfw.com/pdf/H2_'+item['art_code']+'_1.pdf'
                try:
                    if not pdf.exists():
                        response=requests.get(url,timeout=(8,30)); response.raise_for_status()
                        if not response.content.startswith(b'%PDF'): raise ValueError('Not PDF')
                        pdf.write_bytes(response.content)
                    document=pymupdf.open(pdf); matches=[]
                    for i,page in enumerate(document):
                        if i>=20: break
                        text=page.get_text(); normalized=re.sub(r'[ \t]+',' ',text)
                        for minus in ['−','－','﹣']: normalized=normalized.replace(minus,'-')
                        normalized=re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])','',normalized)
                        for m in re.finditer(r'基本(?:和稀释|/稀释|及稀释)?每股收益(?:/亏损)?(?:\s*[（(](?=[^）)]*(?:元|股))[^）)]{0,30}[）)])?',normalized):
                            if re.search('计算|扣除',normalized[max(0,m.start()-25):m.start()]): continue
                            preceding=normalized[max(0,m.start()-2500):m.start()]
                            table_years=re.findall(r'20\d{2}',preceding)
                            snippet=normalized[m.start():m.end()+250]
                            value=re.search(r'[（(]?-?\d+\.\d+[）)]?',normalized[m.end():m.end()+120])
                            if not value or str(year) not in table_years: continue
                            number=float(value.group().replace('（','-').replace('(','-').replace('）','').replace(')',''))
                            matches.append(dict(page=i+1,value=number,snippet=snippet,header_excerpt=preceding[-1300:]))
                    if not matches: raise ValueError('Annual EPS row/header not unambiguous')
                    # First annual summary row precedes parent-only and non-recurring tables.
                    matches=[matches[0]]
                    stamp=item['notice_date'][:10]
                    rows.append(dict(kind='financial',market='CN_A',code=code,metric='eps_basic',value=matches[0]['value'],
                        period_end=str(year)+'-12-31',available_after=(datetime.fromisoformat(stamp).date()+timedelta(days=1)).isoformat(),
                        disclosed_on=stamp,source='original_annual_summary',source_id=key,url=url,
                        sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),evidence=matches,pit_status='original_annual_eps_requires_column_review'))
                    audit.append(dict(year=year,key=key,status='parsed'))
                except Exception as exc: audit.append(dict(year=year,key=key,status='error',error=str(exc)[:160]))
        (out/(code+'-cn-audit.json')).write_text(json.dumps(dict(records=rows,audit=audit),ensure_ascii=False,indent=2),encoding='utf-8')
        print(code,len(rows),audit,flush=True); return rows
    with ThreadPoolExecutor(max_workers=3) as pool: rows=sum(list(pool.map(collect,codes)),[])
    (out/'cn-pit-financials.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print('CN original rows',len(rows),'codes',len({r['code'] for r in rows}),flush=True)

if __name__=='__main__': main()
