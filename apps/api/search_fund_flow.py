from ddgs import DDGS
import json

try:
    with DDGS() as ddgs:
        results = list(ddgs.text('紫金矿业 601899 5月8日 主力资金净流入', max_results=10))
        for r in results:
            print(r['title'])
            print(r['body'])
            print("---")
except Exception as e:
    print(f"Error: {e}")
