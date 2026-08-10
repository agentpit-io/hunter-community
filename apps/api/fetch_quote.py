import requests
import json

# Get latest daily quote
url = "http://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.601899&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=1&end=20260509&lmt=5"
r = requests.get(url)
data = r.json()
print("K-line data:")
print(data['data']['klines'])

