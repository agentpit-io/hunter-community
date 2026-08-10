import requests
import json
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
# 1表示净流入，-1表示净流出？还是什么
url = "https://push2.eastmoney.com/api/qt/stock/get?secid=1.601899&fields=f57,f58,f135,f136,f137,f138,f139,f140,f141,f142,f143,f144,f145,f146,f147,f148,f149,f43,f44,f45,f46,f47,f48,f60,f169,f170,f171,f133,f134,f135,f136,f137,f138,f139,f140,f141,f142,f143,f144,f145,f146,f147,f148,f149,f161,f162,f163,f111"
try:
    resp = requests.get(url, headers=headers)
    print("Eastmoney Realtime Quote:")
    print(resp.json())
except Exception as e:
    print(e)

# Let's get historical fund flow for 601899
url2 = "https://push2his.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=5&klt=101&secid=1.601899&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
try:
    resp2 = requests.get(url2, headers=headers)
    print("Eastmoney Fund Flow (f51=Date, f52=MainNetInflow):")
    data = resp2.json()
    for item in data['data']['klines']:
        print(item)
except Exception as e:
    print(e)
