"""Trigger all push types once to verify correct title in keyword1."""
import asyncio, sys, os
sys.path.insert(0, '.')

async def main():
    from app.services.push_scheduler import _broadcast_and_log

    pushes = [
        ("watchlist_morning",  "自选股早报",       "📰 自选股早报\n贵州茅台(600519) 今日关注：盘前市场情绪偏多，主力资金动向待观察。\n紫金矿业(601899) 昨日主力净流出，今日需留意开盘走势。"),
        ("close_review",       "自选股收盘总结",   "📊 自选股收盘总结\n今日自选股：6只 3涨 3跌\n涨幅最大：新和成 +3.46%\n跌幅最大：紫金矿业 -5.33%\n主力净流入最多：新和成 0.35亿"),
        ("fundflow_topn",      "自选股异动追踪",   "📈 自选股异动追踪\n贵州茅台(600519) 主力净流出 2.87亿\n紫金矿业(601899) 主力净流出 1.68亿\n豪迈科技(002595) 主力净流入 0.21亿"),
        ("daily_news",         "每日新闻速递",     "📰 每日新闻速递\n贵州茅台：股东会将至，投资者回报成焦点\n紫金矿业：成交额超100亿元\n豪迈科技：高研发+高资本开支+高增长潜力"),
        ("weekly_summary",     "周度持仓汇总",     "📅 周度持仓汇总\n本周自选股表现：6只 2涨 4跌\n最佳：新和成 +3.46%\n最差：紫金矿业 -5.33%"),
        ("deep_analysis",      "深度分析报告",     "🔬 深度分析报告\n贵州茅台(600519)：当前估值处于历史中位，股息率约2.3%，建议持续关注季报业绩。"),
        ("price_alert",        "价格提醒·贵州茅台(600519)", "⚡ 价格提醒 · 贵州茅台（600519）\n价格跌至 1260.00，触发阈值 ≤ 1265.00\n今开 1272.00  昨收 1272.86  最高 1275.00  最低 1258.00"),
    ]

    for push_type, task_name, content in pushes:
        print(f"\n>>> {task_name} ({push_type})")
        try:
            await _broadcast_and_log(
                push_type=push_type,
                content=content,
                stocks=[],
                task_name=task_name,
                user_id="cmnhijcdl0000607z6vxzczez",
            )
            print(f"    ok")
        except Exception as e:
            print(f"    ERROR: {e}")
        await asyncio.sleep(2)

    print("\n=== All push types triggered ===")

asyncio.run(main())
