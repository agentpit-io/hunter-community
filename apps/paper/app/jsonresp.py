"""金额一律 `Decimal`：JSON 序列化把它写成**字符串**，绝不经过 `float`。

为什么不用 FastAPI 默认的 `jsonable_encoder`：它把 `Decimal` 转成 `float`
（`100000.0000 → 100000.0`），大额金额与多位小数会在这最后一步丢精度 ——
那正是「金额不用浮点」（`09 §六-8`）要防的事。这里用 `str(Decimal)` 原样输出，
消费方（前端 / 报告）按字符串拿到精确值。

`datetime` 输出 ISO 8601（带时区偏移），与 `apps/api` 的口径一致。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi.responses import JSONResponse


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"无法序列化 {type(value).__name__}")


class LedgerJSONResponse(JSONResponse):
    """全站默认响应类型：`Decimal → str`，不引入浮点。"""

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            default=json_default,
        ).encode("utf-8")
