from duckduckgo_search import DDGS
import warnings
warnings.filterwarnings('ignore')

with DDGS() as ddgs:
    results = ddgs.text('紫金矿业 601899 "主力资金" 2026年5月8日 OR 2026年5月9日', max_results=5)
    for r in results:
        print(f"TITLE: {r['title']}")
        print(f"BODY: {r['body']}")
        print("---")
