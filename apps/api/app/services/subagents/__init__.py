"""Sub-agent 封装层：把 5 个专家 (research/scout/quant/hold/event) 包成统一签名。

签名：async def invoke_xxx(**args) -> tuple[summary: dict, detail_ref: dict | None]
"""
