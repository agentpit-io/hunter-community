import requests

url = "https://push2his.eastmoney.com/api/qt/stock/fflow/kline/get?secid=1.601899&klt=101&lmt=1&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
resp = requests.get(url)
data = resp.json()['data']
print("Name:", data['name'])
print("KLines:", data['klines'])
