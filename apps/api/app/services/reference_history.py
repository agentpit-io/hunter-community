"""Pure normalizers for archived reference data; missing evidence stays missing."""
from datetime import datetime, timedelta
import math
import re


def day(value):
    if value is None:
        return None
    text = str(value).strip()
    if text in ('', '--', 'None', 'NaT'):
        return None
    for fmt, size in (('%Y-%m-%d',10),('%Y/%m/%d',10),('%m/%d/%Y',10),('%Y%m%d',8),('%b %d, %Y',len(text))):
        try:
            return datetime.strptime(text[:size],fmt).date().isoformat()
        except ValueError:
            pass
    return None


def next_day(value):
    parsed = day(value)
    return (datetime.fromisoformat(parsed).date()+timedelta(days=1)).isoformat() if parsed else None


def number(value):
    if value is None or isinstance(value,bool):
        return None
    try:
        result = float(str(value).replace(',','').replace('$','').strip())
        return result if math.isfinite(result) else None
    except (ValueError,TypeError):
        return None


def dividend(market, code, row):
    cash = currency = None
    if market == 'CN_A':
        announced, ex = day(row.get('公告日期')), day(row.get('除权除息日'))
        value = number(row.get('派息'))
        cash = value / 10 if value is not None else None
        currency = 'CNY'
        payment = None  # 红股上市日 is NOT the cash payment date.
        record = day(row.get('股权登记日'))
        implemented = row.get('进度') == '实施'
    elif market == 'HK':
        announced, ex = day(row.get('最新公告日期')), day(row.get('除净日'))
        payment, record = day(row.get('发放日')), None
        text = str(row.get('分红方案',''))
        match = re.fullmatch(r'每股派(?:发)?(港币|港元|美元|人民币)([\d.]+)元(?:[（(].*[）)])?',text)
        if match:
            currency = {'港币':'HKD','港元':'HKD','美元':'USD','人民币':'CNY'}[match[1]]
            cash = number(match[2])
        implemented = None
    elif market == 'US':
        announced, ex = day(row.get('declarationDate')), day(row.get('exOrEffDate'))
        payment, record = day(row.get('paymentDate')), day(row.get('recordDate'))
        currency = row.get('currency')
        cash = number(row.get('amount')) if row.get('type') == 'Cash' else None
        implemented = None
    else:
        raise ValueError('Unknown market')
    return dict(market=market,code=code,announced_on=announced,ex_date=ex,
        record_date=record,payment_date=payment,currency=currency,cash_per_share=cash,
        available_after=next_day(announced),implemented=implemented,
        historical_payment_available_after=next_day(payment),
        signal_ready=announced is not None and ex is not None and cash is not None and cash>0,
        cash_credit_ready=payment is not None and ex is not None and cash is not None and cash>0)


CN_FIELDS = {'EPSJB':'eps_basic','BPS':'book_value_per_share','ROEJQ':'roe_pct',
             'ZCFZL':'debt_ratio_pct','TOTALOPERATEREVE':'revenue',
             'PARENTNETPROFIT':'net_income','TOTAL_SHARE':'shares_outstanding'}
HK_FIELDS = {'BASIC_EPS':'eps_basic','BPS':'book_value_per_share','ROE_AVG':'roe_pct',
             'DEBT_ASSET_RATIO':'debt_ratio_pct','OPERATE_INCOME':'revenue',
             'HOLDER_PROFIT':'net_income'}
SEC_FIELDS = {
    'Revenues':'revenue','RevenueFromContractWithCustomerExcludingAssessedTax':'revenue',
    'SalesRevenueNet':'revenue','Revenue':'revenue','NetIncomeLoss':'net_income',
    'ProfitLoss':'net_income','StockholdersEquity':'equity','Equity':'equity',
    'Assets':'assets','EarningsPerShareBasic':'eps_basic',
    'EarningsPerShareDiluted':'eps_diluted','BasicEarningsLossPerShare':'eps_basic',
    'DilutedEarningsLossPerShare':'eps_diluted','CommonStockSharesOutstanding':'shares_outstanding',
    'CommonStockDividendsPerShareDeclared':'dividend_per_share_reported',
    'CommonStockDividendsPerShareCashPaid':'dividend_per_share_reported',
}


def vendor_financials(market,code,rows):
    mapping = CN_FIELDS if market == 'CN_A' else HK_FIELDS
    out = []
    for row in rows:
        period = day(row.get('REPORT_DATE'))
        notice, update = day(row.get('NOTICE_DATE')), day(row.get('UPDATE_DATE'))
        # A later revision cannot be silently treated as available on original notice date.
        availability = max(notice,update) if notice and update else notice
        for field, metric in mapping.items():
            value = number(row.get(field))
            if value is None or period is None:
                continue
            out.append(dict(market=market,code=code,metric=metric,value=value,
                period_end=period,period_start=day(row.get('START_DATE')),currency=row.get('CURRENCY'),
                source_field=field,disclosed_on=notice,updated_on=update,
                available_after=next_day(availability),
                pit_status='vendor_date_not_independently_verified' if notice else 'missing_disclosure_date'))
    return out


def sec_financials(code,data):
    out = []
    for taxonomy, concepts in data.get('facts',{}).items():
        for concept, body in concepts.items():
            metric = SEC_FIELDS.get(concept)
            if not metric:
                continue
            for unit, rows in body.get('units',{}).items():
                for row in rows:
                    filed, end = day(row.get('filed')), day(row.get('end'))
                    value = number(row.get('val'))
                    if not filed or not end or value is None:
                        continue
                    out.append(dict(market='US',code=code,metric=metric,value=value,
                        period_start=day(row.get('start')),period_end=end,unit=unit,
                        disclosed_on=filed,available_after=next_day(filed),
                        accession=row.get('accn'),form=row.get('form'),taxonomy=taxonomy,
                        source_field=concept,pit_status='sec_filing_dated'))
    return out


def known_by(record, asof):
    """Conservative daily availability only; not a validator of values or revisions."""
    stamp = day(asof)
    return bool(stamp and record.get('available_after') and record['available_after'] <= stamp)
