"""核验冻结结果并导出112位真实净值曲线；无现金等待补线。"""
import argparse,hashlib,json,math
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--report',required=True); ap.add_argument('--output',required=True)
    args=ap.parse_args(); raw=Path(args.report).read_bytes(); report=json.loads(raw)
    horses={}; evidence_count=0
    for r in report['tests']:
        z=r['cost_1x']; assert z['metrics'] and z['equity_curve']
        for scenario in ('cost_1x','cost_2x'):
            for trade in r[scenario]['trades']:
                assert trade['fill_date']>trade['signal_date']
                e=trade.get('reference_evidence') or {}
                for key in ('financial_available','earnings_available'):
                    if e.get(key): assert e[key]<=trade['signal_date']
                for dividend in e.get('observed_dividends',[]):
                    assert dividend['known_after']<=trade['signal_date']
                    assert dividend['ex_date']<=trade['signal_date']
                if e: evidence_count+=1
        points=[]
        for p in z['equity_curve']:
            v=None if p['equity'] is None else (p['equity']/r['capital']-1)*100
            assert v is None or math.isfinite(v)
            assert not points or p['date']>points[-1]['date']
            points.append(dict(date=p['date'],returnPct=v))
        final=next(p['returnPct'] for p in reversed(points) if p['returnPct'] is not None)
        assert abs(final-z['metrics']['total_return']*100)<1e-8
        assert r['horse'] not in horses
        horses[r['horse']]=dict(family=r['family'],market=r['market'],coverageComplete=r['coverage_complete'],
            returnPct=z['metrics']['total_return']*100,drawdownPct=z['metrics']['max_drawdown']*100,
            fills=len(z['trades']),points=points,cashWait=False)
    assert len(horses)==112
    out=dict(start=report['start'],end=report['end'],version=report['version'],sourceSha256=hashlib.sha256(raw).hexdigest(),
        historySha256=report['data_fingerprint'],ruleFingerprint=report['rule_fingerprint'],
        referenceFingerprints=report['reference_fingerprints'],productionActivation=False,horses=horses)
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print('112 actual curves; audited reference-evidence fills:',evidence_count)

if __name__=='__main__': main()
