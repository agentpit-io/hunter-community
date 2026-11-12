"""补齐回测证据；保留原文，不从报告期猜披露日，不修改交易数据库。"""
import argparse,gzip,hashlib,json,re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from pathlib import Path
import requests
import pymupdf
from collect_reference_history import hk_submissions

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',required=True)
    ap.add_argument('--report',required=True)
    ap.add_argument('--output',required=True)
    args=ap.parse_args()
    root=Path(args.input); out=Path(args.output); out.mkdir(parents=True,exist_ok=True)
    report=json.loads(Path(args.report).read_text())
    codes=sorted({c for t in report['tests'] if t['market']=='HK' and t['family']=='value_hold' for c in t['coverage']})
    def extract(code):
        path=out/(code+'-en-filings.json')
        inventory=json.loads(path.read_text()) if path.exists() else hk_submissions(code,'2024-01-01','2026-10-09',language='en')
        path.write_text(json.dumps(inventory,ensure_ascii=False),encoding='utf-8')
        if not inventory['complete']: raise ValueError('Incomplete HKEX inventory')
        vendor=json.load(gzip.open(root/'batch'/('HK-'+code+'-financial.json.gz'),'rt'))['data']
        rows=[]; evidence=[]
        for item in inventory['records']:
            title=' '.join(item['TITLE'].split())
            if not re.search(r'(annual|full.year|fiscal.year|year ended).*results|results.*(annual|full.year|fiscal.year|year ended)',title,re.I): continue
            if re.search(r'profit warning|alert|forecast|subsidiar|preview|general meeting|poll results|voting results|presentation|proposed',title,re.I): continue
            years=re.findall(r'20[12]\d',title)
            if not years: continue
            year=max(years)
            url='https://www1.hkexnews.hk'+item['FILE_LINK']
            if not url.lower().endswith('.pdf'): continue
            key=code+'-'+str(item['NEWS_ID'])
            pdf=out/(key+'.pdf')
            try:
                if not pdf.exists():
                    response=requests.get(url,timeout=(8,30)); response.raise_for_status()
                    if not response.content.startswith(b'%PDF'): raise ValueError('Not PDF')
                    pdf.write_bytes(response.content)
                reader=pymupdf.open(pdf)
                pages=[re.sub(r'[ \t]+',' ',p.get_text()) for p in reader]
                with gzip.open(out/(key+'.txt.gz'),'wt',encoding='utf-8') as f: f.write('\n'.join(pages))
                candidates=[r for r in vendor if str(r['REPORT_DATE'])[:4]==year and r.get('START_DATE') and str(r['START_DATE'])[:10].endswith('01-01') and str(r['REPORT_DATE'])[:10].endswith('12-31')]
                # Fiscal years other than Dec 31 also require a >=330-day annual interval.
                candidates += [r for r in vendor if str(r['REPORT_DATE'])[:4]==year and r.get('START_DATE') and (datetime.fromisoformat(str(r['REPORT_DATE'])[:10])-datetime.fromisoformat(str(r['START_DATE'])[:10])).days>=330 and r not in candidates]
                for row in candidates:
                    value=row.get('BASIC_EPS')
                    if value is None: continue
                    verified=[]
                    for page_no,text in enumerate(pages,1):
                        for match in re.finditer(r'(?:earnings|income|loss)[\s()/\-]{0,10}(?:loss|earnings)?\s*per\s*(?:ordinary\s*)?share',text,re.I):
                            chunk=text[max(0,match.start()-60):match.end()+1600]
                            values=[float(n.replace(',','').replace('(','-').replace(')','')) for n in re.findall(r'\(?-?\d[\d,]*\.\d+\)?',chunk)]
                            # Preserve only a numeric match; original column review remains required.
                            if any(abs(n-float(value))<=max(.005,abs(float(value))*.0005) for n in values):
                                verified.append(dict(page=page_no,snippet=chunk))
                        # NetEase's wide statement prints the per-share label after numeric columns.
                        token=f'{float(value):.2f}'
                        if code=='09999' and token in text and re.search('per share',text,re.I) and re.search('Basic',text):
                            offset=text.index(token)
                            verified.append(dict(page=page_no,snippet=text[max(0,offset-900):offset+600],wide_table=True))
                    if verified:
                        published=datetime.strptime(item['DATE_TIME'],'%d/%m/%Y %H:%M')
                        rows.append(dict(kind='financial',market='HK',code=code,metric='eps_basic',value=float(value),
                            period_end=str(row['REPORT_DATE'])[:10],available_after=(published.date()+timedelta(days=1)).isoformat(),
                            disclosed_on=published.date().isoformat(),source='hkex_original_eps_match',source_id=key,url=url,
                            sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),evidence=verified,
                            pit_status='annual_eps_numeric_match_original_requires_column_review'))
                # These original rows differ from the later restated / capitalized vendor series.
                corrections={'01211-11582671':(13.84,'13.84'),
                             '00175-11577975':(1.638,'163.80')}
                if key in corrections:
                    value,token=corrections[key]
                    proof=[dict(page=i+1,snippet=text[max(0,text.index(token)-250):text.index(token)+350])
                           for i,text in enumerate(pages) if token in text and re.search('earnings per share',text,re.I)]
                    if not proof: raise ValueError('Original correction requires exact EPS row')
                    published=datetime.strptime(item['DATE_TIME'],'%d/%m/%Y %H:%M')
                    rows.append(dict(kind='financial',market='HK',code=code,metric='eps_basic',value=value,
                        period_end=year+'-12-31',available_after=(published.date()+timedelta(days=1)).isoformat(),
                        disclosed_on=published.date().isoformat(),source='hkex_original_eps_corrected',source_id=key,
                        url=url,sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),evidence=proof,
                        pit_status='original_annual_eps_reviewed',correction_reason='original EPS before later restatement/capitalization; original units converted only'))
                evidence.append(dict(key=key,title=title,url=url,published=item['DATE_TIME'],year=year,matched=len(rows),status='parsed'))
            except Exception as exc:
                evidence.append(dict(key=key,title=title,url=url,status='error',error=str(exc)[:160]))
        (out/(code+'-audit.json')).write_text(json.dumps(dict(records=rows,reports=evidence),ensure_ascii=False,indent=2),encoding='utf-8')
        print(code,'reports',len(evidence),'matched',len(rows),flush=True)
        return rows
    with ThreadPoolExecutor(max_workers=3) as pool: rows=sum(list(pool.map(extract,codes)),[])
    (out/'hk-pit-financials.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print('HK matched',len(rows),'codes',len({r['code'] for r in rows}),flush=True)

if __name__=='__main__': main()
