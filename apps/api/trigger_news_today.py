"""手动触发所有 news_today 推送任务（每个任务用自己的 user_id，只包含该用户的自选股）。"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

async def main():
    from app.services.database import get_conn
    from app.services.push_scheduler import trigger_task_now

    # 查询所有启用的 news_today 任务
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, user_id FROM push_tasks "
        "WHERE content_type = 'news_today' AND enabled = TRUE ORDER BY id"
    )
    tasks = cur.fetchall()
    conn.close()

    if not tasks:
        print("没有找到已启用的 news_today 任务")
        return

    print(f"找到 {len(tasks)} 个 news_today 任务\n")
    for task_id, name, user_id in tasks:
        print(f">>> 触发 task_id={task_id} name={name} user_id={user_id}")
        try:
            result = await trigger_task_now(task_id)
            print(f"    结果: {result}\n")
        except Exception as e:
            print(f"    ERROR: {e}\n")

asyncio.run(main())
