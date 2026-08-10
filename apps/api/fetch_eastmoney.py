import requests
import json

# Eastmoney API for stock info and fund flow
# secid for 601899.SH is 1.601899

url = "https://push2.eastmoney.com/api/qt/stock/get?ut=fa5fd1943c7b386f172d6893dbfba10b&fltt=2&invt=2&fields=f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f59,f60,f62,f116,f117,f137,f161,f162,f163,f164,f168,f169,f170,f171,f172,f173,f174,f175,f176,f177&secid=1.601899"
try:
    res = requests.get(url).json()
    # f43 is close, f169 is pct change, f170 is chg
    print("Eastmoney Stock Info:")
    print("Price:", res['data']['f43']/100)
    print("Pct Change:", res['data']['f170']/100, "%")
except Exception as e:
    print("Error fetching stock info:", e)

url_flow = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=0&klt=101&secid=1.601899&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
try:
    res_flow = requests.get(url_flow).json()
    klines = res_flow['data']['klines']
    print("\nFund Flow (last 3 days):")
    for k in klines[-3:]:
        print(k)
except Exception as e:
    print("Error fetching fund flow:", e)
