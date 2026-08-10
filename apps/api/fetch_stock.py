import requests

url = "http://push2his.eastmoney.com/api/qt/stock/fflow/kline/get"
params = {
    "lmt": "0",
    "klt": "101", # daily
    "secid": "1.601899", # Shanghai Zijin
    "fields1": "f1,f2,f3,f7",
    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
}

resp = requests.get(url, params=params)
data = resp.json()
print(data['data']['klines'][-1])

# Also let's just get the latest quote for change %
quote_url = "http://push2.eastmoney.com/api/qt/stock/get"
quote_params = {
    "secid": "1.601899",
    "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f169,f170,f43,f170,f169"
}
q_resp = requests.get(quote_url, params=quote_params)
print(q_resp.json()['data'])
