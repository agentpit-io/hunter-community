"""R10 · 经验 → 决策「通电」演示：负向经验命中 → halted；拿掉可见性 → 恢复出单。"""
import os, sys
sys.path.insert(0, "/app"); sys.path.insert(0, "/scripts")
import psycopg2.extras
from app.services.fin import memory as M
import memory_gate as GATE
PID=os.environ["R10_PID"]; UID=os.environ["R10_UID"]; MARKET="HK"; CODE="00700"
snap = M.query(project_id=PID, user_id=UID, freeze=True, for_decision=True, purpose="decision")
sid, items = snap["memory_snapshot_id"], snap["items"]
blk = GATE.blocking_experience(items, market=MARKET, code=CODE)
print("A) 冻结经验集 snapshot=%s 共 %d 条" % (sid, len(items)))
print("A) blocking_experience(HK/00700) -> %s" % (blk and {k: blk[k] for k in ('experience_id','polarity','kind','status','symbols')}))
print("A) halted = %s" % bool(blk))
# 拿掉可见性（等价于「删掉」—— 零开关：查不到就是查不到）
c = M.get_conn()
try:
    with c.cursor() as cur:
        cur.execute("UPDATE fin_experience SET exposure_scope='holdout_only' "
                    "WHERE symbols @> ARRAY[%s] AND kind='verified' AND polarity='refute'", ("HK:00700",))
    c.commit()
finally:
    c.close()
snap2 = M.query(project_id=PID, user_id=UID, freeze=True, for_decision=True, purpose="decision")
blk2 = GATE.blocking_experience(snap2["items"], market=MARKET, code=CODE)
print("B) 拿掉可见性后 items=%d 条 · blocking_experience -> %s · halted = %s" % (len(snap2["items"]), blk2, bool(blk2)))
# 复原
c = M.get_conn()
try:
    with c.cursor() as cur:
        cur.execute("UPDATE fin_experience SET exposure_scope='searchable' "
                    "WHERE symbols @> ARRAY[%s] AND kind='verified' AND polarity='refute'", ("HK:00700",))
    c.commit()
finally:
    c.close()
print("C) 已复原 exposure_scope='searchable'")
