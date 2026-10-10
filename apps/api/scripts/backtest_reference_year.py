"""只读回放112位选手；真实时点参考数据，不激活生产策略。"""
import argparse,csv,gzip,hashlib,json,re,statistics,sys
from datetime import date,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services import stock_backtest as bt,stock_rules as rules
from app.services.stock_reference_signals import Engine,VERSION

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--prices',required=True); ap.add_argument('--reference',required=True)
    ap.add_argument('--supplement',required=True); ap.add_argument('--output',required=True)
    args=ap.parse_args(); prices=Path(args.prices); sup=Path(args.supplement); out=Path(args.output); out.mkdir(parents=True,exist_ok=True)
    baseline=json.loads((prices/'report.json').read_text(encoding='utf-8'))
    snapshot=json.loads((prices/'daily_snapshot.json').read_text(encoding='utf-8'))
    fees=json.loads((prices/'fees.json').read_text(encoding='utf-8')); lots=json.loads((prices/'lots.json').read_text(encoding='utf-8'))
    records=[json.loads(line) for line in gzip.open(args.reference,'rt',encoding='utf-8')]
    accession={(r['code'],r.get('accession')) for r in records if r['market']=='US' and r['kind']=='financial' and r['metric'] in ('net_income','eps_basic') and r.get('form') in ('10-K','10-Q','20-F','6-K')}
    announcements=set()
    for r in list(records):
        if r['kind']=='event':
            if r['market']=='US': r['earnings_event']=(r['code'],r['source_id']) in accession
            elif r['market']=='HK':
                title=r.get('title','')
                r['earnings_event']=bool(re.search('業績公佈|業績公告',title) and not re.search('預|警告|股東|附屬',title))
        elif r['market']=='CN_A' and r['kind']=='financial' and r.get('disclosed_on'):
            key=(r['code'],r['disclosed_on'])
            if key in announcements: continue
            announcements.add(key)
            published=date.fromisoformat(r['disclosed_on'])
            records.append(dict(kind='event',market='CN_A',code=r['code'],published_on=published.isoformat(),
                available_after=(published+timedelta(days=1)).isoformat(),source='financial_notice_date',source_id=':'.join(key),earnings_event=True))
    verified=json.loads((sup/'hk-pit-financials-reviewed.json').read_text(encoding='utf-8'))
    verified+=json.loads((sup/'cn-pit-financials-reviewed.json').read_text(encoding='utf-8'))
    assert all(r['pit_status']=='original_annual_eps_reviewed' for r in verified)
    records.extend(verified)
    fx=json.loads((sup/'fx.json').read_text(encoding='utf-8'))
    engines={m:Engine(records,m,fx=fx) for m in ['CN_A','HK','US']}
    for code in ['00073','00686']:
        bars=json.loads((sup/(code+'-bars.json')).read_text(encoding='utf-8'))
        rules.validate_bars(bars); snapshot['records']['HK:'+code]=bars
    (out/'daily_snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False),encoding='utf-8')
    report={k:v for k,v in baseline.items() if k!='tests'}
    report.update(version=VERSION,rule_fingerprint=rules.FINGERPRINT,reference_fingerprints={m:e.fingerprint for m,e in engines.items()},tests=[],
        data_fingerprint=hashlib.sha256((out/'daily_snapshot.json').read_bytes()).hexdigest(),
        limitations=baseline['limitations']+['Three previously blocked families use explicit new research rules; value uses profitability and price percentile, not PE/PB',
        'Adjusted-price NAV proxy; cash dividends are not credited again; not a complete cash corporate-action ledger',
        'HK annual EPS uses original publication dates; CN vendor revisions use conservative UPDATE_DATE',
        'Historical consensus not acquired; event rule is disclosed earnings plus subsequent volume/price confirmation'],
        input_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(args.reference),sup/'hk-pit-financials-reviewed.json',sup/'fx.json',out/'daily_snapshot.json',prices/'fees.json',prices/'lots.json']})
    for n,t in enumerate(baseline['tests'],1):
        result={k:v for k,v in t.items() if k not in ('cost_1x','cost_2x','equal_weight_baseline')}
        series={c:snapshot['records'].get(t['market']+':'+c,[]) for c in t['coverage']}
        result['active_key']=VERSION+'_'+t['family']
        result['production_activation']=False
        engine=engines[t['market']] if t['family'] in ('value_hold','div_lowvol','event_driven') else None
        result['reference_coverage']={c:dict(annual_financial=len(engines[t['market']].financial[c]),dividends=len(engines[t['market']].dividends[c]),earnings_events=len(engines[t['market']].events[c])) for c in series}
        result['coverage']={c:dict(bars=len(b),warmup=sum(v['ts']<baseline['start'] for v in b),test_bars=sum(baseline['start']<=v['ts']<=baseline['end'] for v in b),last=b[-1]['ts'] if b else None) for c,b in series.items()}
        result['full_history_symbols']=[c for c,v in result['coverage'].items() if v['warmup']>=130 and v['test_bars']>=220]
        result['coverage_complete']=len(result['full_history_symbols'])==len(series)
        for stress in [1,2]:
            z=bt.run(t['family'],series,capital=t['capital'],lot_sizes=lots[t['market']],fee_model=fees[t['market']],param=t['param'],
                start=baseline['start'],slippage=.001*stress,stress=stress,market=t['market'],reference_engine=engine)
            assert z['metrics'] is not None
            assert all(v['fill_date']>v['signal_date'] for v in z['trades'])
            result['cost_'+str(stress)+'x']=z
        result['equal_weight_baseline']=bt.equal_weight_baseline(series,capital=t['capital'],lot_sizes=lots[t['market']],fee_model=fees[t['market']],start=baseline['start'])
        report['tests'].append(result)
        print(n,t['horse'],t['family'],result['cost_1x']['metrics'],flush=True)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    def summarize(rows):
        returns=[r['cost_1x']['metrics']['total_return'] for r in rows]
        return dict(accounts=len(rows),measurable=sum(bool(r['cost_1x']['equity_curve']) for r in rows),traded=sum(bool(r['cost_1x']['trades']) for r in rows),
            positive=sum(v>0 for v in returns),avg_return=statistics.mean(returns),median_return=statistics.median(returns),
            stress_avg_return=statistics.mean(r['cost_2x']['metrics']['total_return'] for r in rows),
            fills=sum(len(r['cost_1x']['trades']) for r in rows),max_drawdown=max(r['cost_1x']['metrics']['max_drawdown'] for r in rows))
    summary=dict(total=summarize(report['tests']),families={f:summarize([r for r in report['tests'] if r['family']==f]) for f in rules.SPECS},
        markets={m:summarize([r for r in report['tests'] if r['market']==m]) for m in engines},start=report['start'],end=report['end'])
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    with (out/'horse-results.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f); w.writerow(['horse','market','family','return_pct','max_drawdown_pct','stress_return_pct','fills','price_coverage_complete'])
        for r in report['tests']:
            z=r['cost_1x']; w.writerow([r['horse'],r['market'],r['family'],z['metrics']['total_return']*100,z['metrics']['max_drawdown']*100,r['cost_2x']['metrics']['total_return']*100,len(z['trades']),r['coverage_complete']])
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__': main()
