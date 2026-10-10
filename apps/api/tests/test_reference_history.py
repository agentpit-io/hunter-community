"""No database/network dependencies. Run directly with Python."""
import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[1] / 'app/services/reference_history.py'
spec = importlib.util.spec_from_file_location('reference_history',path)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

def test_history():
    cash = r.dividend('CN_A','600519',{'公告日期':'2025-06-01','除权除息日':'2025-06-10',
        '派息':280.242,'进度':'实施','红股上市日':'2025-06-11'})
    assert abs(cash['cash_per_share']-28.0242)<1e-10
    assert cash['payment_date'] is None and not cash['cash_credit_ready']
    assert not r.known_by(cash,'2025-06-01') and r.known_by(cash,'2025-06-02')
    foreign = r.dividend('HK','00700',{'最新公告日期':'2025-03-20',
        '除净日':'2025-05-16','分红方案':'每股派美元0.2元','发放日':'2025-06-01'})
    assert foreign['currency']=='USD' and foreign['cash_per_share']==.2
    unknown = r.dividend('HK','00001',{'分红方案':'每10股派1.3元'})
    assert unknown['cash_per_share'] is None and not unknown['signal_ready']
    revised = r.vendor_financials('CN_A','600519',[{'REPORT_DATE':'2024-12-31',
        'NOTICE_DATE':'2025-03-01','UPDATE_DATE':'2025-06-01','EPSJB':5}])[0]
    assert revised['available_after']=='2025-06-02' and not r.known_by(revised,'2025-03-02')
    missing = r.vendor_financials('HK','00700',[{'REPORT_DATE':'2024-12-31','BPS':5}])[0]
    assert not r.known_by(missing,'2025-06-01')
    facts = r.sec_financials('AAPL',{'facts':{'us-gaap':{'NetIncomeLoss':{'units':{
        'USD':[{'filed':'2025-05-01','end':'2025-03-31','val':123,'accn':'original'},
               {'filed':'2025-08-01','end':'2025-03-31','val':124,'accn':'revised'}]}}}}})
    assert len(facts)==2 and not r.known_by(facts[1],'2025-05-02')
    assert r.number(float('nan')) is None and r.number(True) is None
    assert r.day('Oct 01, 2025') == '2025-10-01'
    assert not r.known_by(cash,None)
    supplemented = r.dividend('US','KO',{'exOrEffDate':'2025-09-15',
        'amount':'.51','recordDate':'2025-09-15','paymentDate':'2025-10-01','type':'Cash'})
    assert supplemented['cash_credit_ready'] and not supplemented['signal_ready']
    assert supplemented['available_after'] is None
    assert supplemented['historical_payment_available_after']=='2025-10-02'

if __name__=='__main__':
    test_history()
    print('Reference normalization checks passed')
