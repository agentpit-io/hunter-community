"""验收用的假行情服务（M4 起就在用，M7 收进仓库留档）。

**它是测试替身，不是数据源。** 真实数据源只有两个：`apps/api` 的官方链路
（`providers.data_source`）与腾讯免费通道 —— 可用性实测写在 M7 成果文档里。
这个替身存在的理由只有一个：**故障注入需要控制"那一刻的报价是什么"**
（比如让快照时刻落在一个已知的、日历里标了交易时段的分钟上），
真实行情给不了这种控制。

用法（compose 之外单独起，挂在 hunter-community 的网络上）：

    docker run -d --name m4-fake-quote --network hunter-community_default \
      -e FAKE_QUOTE_TS="2026-09-30 10:00:00" -e FAKE_QUOTE_PRICE=10.00 \
      -e FAKE_QUOTE_PREV_CLOSE=9.80 \
      -v "$PWD/scripts/fake_quote_server.py:/fake_quote.py:ro" \
      python:3.11-slim python /fake_quote.py

`ts` 是**数据源时刻**：必须落在 `fin_market_calendar` 里那天的交易时段内，
否则风控第 1 条会（正确地）拒掉委托。事件时间不填本机时间 —— 与真实数据源同一条规矩。
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

TS = os.environ.get("FAKE_QUOTE_TS", "2026-09-30 10:00:00")
PRICE = os.environ.get("FAKE_QUOTE_PRICE", "10.00")
PREV = os.environ.get("FAKE_QUOTE_PREV_CLOSE", "9.80")


class H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        code = self.path.rstrip("/").split("/")[-1].split("?")[0]
        body = json.dumps({
            "code": code, "name": "示例标的", "source": "fake",
            "price": float(PRICE), "prev_close": float(PREV),
            "open": float(PREV), "high": float(PRICE), "low": float(PREV),
            "volume": 1000000, "amount": 10000000.0,
            "bid1": round(float(PRICE) - 0.01, 2), "bid1v": 1000,
            "ask1": round(float(PRICE) + 0.01, 2), "ask1v": 1000,
            "ts": TS, "market": "A", "asset_type": "stock",
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


HTTPServer(("0.0.0.0", 18080), H).serve_forever()
