"""Download immutable HKEX earnings announcements; preserve dates and hashes.

Optional pypdf extraction is evidence text, never a free-form numeric parser.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import threading
from datetime import datetime, timedelta

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--cn-documents',help='Optional archived company_document JSON gzip')
    parser.add_argument('--skip-text',action='store_true',help='Archive originals without PDF text extraction')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True,exist_ok=True)
    jobs = {}
    if args.cn_documents:
        with gzip.open(args.cn_documents,'rt',encoding='utf-8') as f:
            documents = json.load(f)
        for row in documents:
            if not re.search('(权益分派|利润分配)实施公告$',row['title']):
                continue
            key = 'CN_A-'+row['company_id']+'-'+row['ann_id']
            jobs[key] = dict(code=row['company_id'],title=row['title'],
                published_at=row['ann_date'],publication_precision='date',
                url='https://static.cninfo.com.cn/finalpage/'+row['ann_date']+'/'+row['ann_id']+'.PDF',
                source_id=row['ann_id'],expected_sha256=row.get('sha256'))
    for path in Path(args.input).glob('HK-*-filings.json.gz'):
        with gzip.open(path,'rt',encoding='utf-8') as f:
            envelope = json.load(f)
        if not envelope['data']['complete']:
            raise ValueError('HKEX filing inventory is incomplete')
        for row in envelope['data']['records']:
            if not re.search('業績公佈|業績公告|全年業績|年度業績|中期業績',row['TITLE']):
                continue
            link = row['FILE_LINK']
            if not link.lower().endswith('.pdf'):
                continue
            key = envelope['code']+'-'+str(row['NEWS_ID'])
            jobs[key] = dict(code=envelope['code'],title=row['TITLE'],
                published_at=datetime.strptime(row['DATE_TIME'],'%d/%m/%Y %H:%M').isoformat()+'+08:00',
                url='https://www1.hkexnews.hk'+link,source_id=row['NEWS_ID'])
    lock = threading.Lock()
    manifest = {}
    def run(item):
        key, metadata = item
        try:
            path = output / (key+'.pdf')
            if path.exists():
                raw = path.read_bytes()
            else:
                response = requests.get(metadata['url'],headers={'User-Agent':'Hunter Research hunter@agentpit.io'},timeout=(8,30))
                if response.status_code == 404 and metadata.get('expected_sha256'):
                    # Vendor publication dates and CNINFO storage dates can differ.
                    # Validate the recovered original against the archived source hash.
                    date = datetime.fromisoformat(metadata['published_at'])
                    for offset in (1,-1,2,-2):
                        candidate = 'https://static.cninfo.com.cn/finalpage/'+(date+timedelta(days=offset)).date().isoformat()+'/'+metadata['source_id']+'.PDF'
                        recovered = requests.get(candidate,timeout=(8,30))
                        if recovered.status_code == 200 and hashlib.sha256(recovered.content).hexdigest() == metadata['expected_sha256']:
                            response = recovered
                            metadata['url'] = candidate
                            break
                response.raise_for_status()
                raw = response.content
                if not raw.startswith(b'%PDF') or len(raw)>30_000_000:
                    raise ValueError('Not a supported PDF or report exceeds 30 MB')
                temporary = path.with_suffix('.pdf.part')
                temporary.write_bytes(raw)
                temporary.replace(path)
            if not raw.startswith(b'%PDF'):
                raise ValueError('Cached file is not a PDF')
            metadata.update(status='downloaded',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
            if metadata.get('expected_sha256') and metadata['sha256'] != metadata['expected_sha256']:
                metadata['source_hash_matches'] = False
            elif metadata.get('expected_sha256'):
                metadata['source_hash_matches'] = True
            if args.skip_text:
                metadata['text_extraction'] = 'deferred'
                return
            try:
                from pypdf import PdfReader
                reader = PdfReader(BytesIO(raw))
                text = '\n'.join(page.extract_text() or '' for page in reader.pages)
                with gzip.open(output/(key+'.txt.gz'),'wt',encoding='utf-8') as f:
                    f.write(text)
                metadata.update(pages=len(reader.pages),text_chars=len(text))
            except Exception as exc:
                metadata['text_error']=type(exc).__name__
        except Exception as exc:
            metadata.update(status='error',error=type(exc).__name__,message=str(exc)[:160])
        finally:
            with lock:
                manifest[key] = metadata
                (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
                print(len(manifest),'/',len(jobs),key,metadata['status'],flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(run,jobs.items()))
    print('Reports',len(manifest),'errors',sum(v['status']=='error' for v in manifest.values()))


if __name__=='__main__':
    main()
