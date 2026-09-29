"""个人策略创建：受限规则、逐条确认、版本快照。AI 不生成可执行代码。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import date

# 标签、参数范围和执行含义同源，缺参数绝不补成可交易规则。
SCHEMA = {
    "breakout": ("买入：收盘突破前期最高价", {"days": ("回看交易日", 2, 252, True)}),
    "ma_above": ("买入：收盘高于均线", {"days": ("均线交易日", 2, 250, True)}),
    "volume": ("买入：成交量达到前期均量倍数", {"days": ("均量交易日", 2, 250, True), "ratio": ("倍数", .1, 20, False)}),
    "stop": ("卖出：相对买入价止损", {"pct": ("亏损百分比", .1, 8, False)}),
    "take_profit": ("卖出：相对买入价止盈", {"pct": ("盈利百分比", .1, 1000, False)}),
    "ma_exit": ("卖出：收盘跌破均线", {"days": ("均线交易日", 2, 250, True)}),
    "time_exit": ("卖出：达到持有交易日", {"days": ("持有交易日", 1, 252, True)}),
    "trailing": ("卖出：从持仓最高收盘价回撤", {"pct": ("回撤百分比", .1, 8, False)}),
    "position": ("单票资金上限", {"pct": ("总资产百分比", .1, 25, False)}),
    "risk": ("单笔初始风险上限", {"pct": ("总资产百分比", .01, 2, False)}),
    "holdings": ("最多同时持仓", {"count": ("股票数量", 1, 20, True)}),
    "pending": ("待补充或暂不支持", {}),
}
BUY = {"breakout", "ma_above", "volume"}
REQUIRED = {"stop", "position", "risk", "holdings"}
EXECUTION = "收盘生成信号，下一交易日收盘模拟成交；停牌未成交订单保留；止损也有延迟和跳空风险。只做多、不加仓，买入条件全部满足，卖出条件任一满足。美股手续费按现有阶梯式规则，滑点由回测设置给出。"


def catalog():
    return [{"type": k, "label": label, "fields": [dict(key=n, label=v[0], min=v[1], max=v[2], integer=v[3]) for n, v in fields.items()]}
            for k, (label, fields) in SCHEMA.items()]


def clean_rule(raw):
    if not isinstance(raw, dict) or raw.get("type") not in SCHEMA:
        raise ValueError("规则类型暂不支持，请重新识别或选择支持的类型")
    kind = raw["type"]
    params = raw.get("params", {})
    fields = SCHEMA[kind][1]
    if not isinstance(params, dict) or set(params) != set(fields):
        raise ValueError("规则参数不完整，请逐项补充")
    for key, (_, lo, hi, integer) in fields.items():
        val = params[key]
        if type(val) not in (int, float) or not math.isfinite(val) or not lo <= val <= hi or (integer and int(val) != val):
            raise ValueError(f"{fields[key][0]}必须在 {lo}～{hi} 范围内" + ("且为整数" if integer else ""))
    source = str(raw.get("source", "")).strip()
    if not source or len(source) > 2000:
        raise ValueError("每条规则需要保留原始描述（不超过两千字）")
    return dict(type=kind, params=dict(params), source=source, confirmed=raw.get("confirmed") is True)


def validate_rules(rows, confirmed=False):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 30:
        raise ValueError("请提供一至三十条规则")
    rows = [clean_rule(r) for r in rows]
    if confirmed:
        if any(not r["confirmed"] or r["type"] == "pending" for r in rows):
            raise ValueError("请先补齐并逐条确认规则")
        kinds = [r["type"] for r in rows]
        if not REQUIRED.issubset(kinds) or not BUY.intersection(kinds):
            raise ValueError("还需买入条件、止损、单票资金上限、单笔风险上限和最多持仓规则")
        if any(kinds.count(k) != 1 for k in REQUIRED):
            raise ValueError("止损、仓位、风险及持仓上限各只能保留一条")
    return rows


def recognize(text, single=False):
    if not isinstance(text, str) or not text.strip() or len(text) > 6000:
        raise ValueError("请输入策略描述，最多六千字")
    from app.services.online_analysis.llm_client import llm_json_call
    from app.services.quant.screen_nl import model_name
    prompt = ("你是策略规则翻译器。只输出JSON对象，rules数组中每项为type、params、source。"
              "source必须逐字引用用户原文的一段，不得改写；params只能使用source中明确出现的阿拉伯数字，不得猜参数。"
              "覆盖原文所有要求，不支持的条件、歧义、缺参数、中文数字均用pending保留原文且params为空。"
              "不得把复杂条件近似成简单条件。多个买入条件为且，卖出条件为或；只支持全仓卖出，不支持加仓、分批卖出、盘中执行或自动优化。"
              "volume均量不含信号当日，breakout为前N日最高价不含当日。"
              + ("这是单条重新识别，只返回一项；包含多个独立要求时返回pending。" if single else "每个独立要求一项。")
              + "支持的类型参数如下：" + json.dumps(catalog(), ensure_ascii=False))
    data, meta = llm_json_call(prompt, text, model=model_name(), max_tokens=4500, temperature=0, retry_on_parse_fail=False)
    if data is None and meta.get("error") == "no_api_key":
        raise ValueError("尚未配置 AI 模型，请先在模型设置中完成配置；也可以手动添加规则")
    if data is None:
        raise ValueError("AI 识别失败：请在设置中检查模型配置与连接，或稍后重试。原规则未改变。")
    rows = data.get("rules")
    if not isinstance(rows, list) or not rows or len(rows) > 30 or (single and len(rows) != 1):
        raise ValueError("AI 没有返回有效规则，请补充描述后重试")
    out = []
    for raw in rows:
        source = raw.get("source", "") if isinstance(raw, dict) else ""
        if not source or source not in text:
            raise ValueError("AI 未能准确引用原文，本次结果已丢弃，请重试")
        nums = {float(n) for n in re.findall(r"(?<![\d.])-?\d+(?:\.\d+)?", source)}
        try:
            row = clean_rule(raw)
            if any(float(v) not in nums for v in row["params"].values()):
                raise ValueError("模型添加了未提供的数字")
        except (ValueError, TypeError):
            row = dict(type="pending", params={}, source=source)
        row["confirmed"] = False
        out.append(row)
    return {"rules": out, "tokens": {k: meta.get(k) for k in ("tokens_in", "tokens_out")}}


def transaction(fn):
    from app.services.quant import agent_run as ar
    conn = ar._conn()
    try:
        with conn.cursor() as cur:
            value = fn(cur, ar)
        conn.commit()
        return value
    finally:
        conn.close()


def key(uid, ident):
    try: ident = str(uuid.UUID(ident))
    except (ValueError, TypeError, AttributeError): raise ValueError("策略编号无效")
    return f"builder:{uid}:{ident}"


def read(uid, ident):
    def get(c, ar):
        k = key(uid, ident)
        c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (k,))
        cfg = ar._meta_get(c, k)
        if cfg:
            changed = False
            for run in cfg.get("runs", []):
                if run["status"] in ("queued", "running"):
                    c.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", (run["id"],))
                    if c.fetchone()[0]:
                        run.update(status="failed", error="服务重启或工作进程中断，请重新提交回测；规则与旧结果已保留")
                        changed = True
            if changed: ar._meta_set(c, k, cfg)
        return cfg
    result = transaction(get)
    if not result: raise ValueError("策略不存在或不属于当前账号")
    return result


def list_for(uid):
    def get(c, ar):
        c.execute("SELECT value FROM agent_meta WHERE key LIKE %s ORDER BY key", (f"builder:{uid}:%",))
        return [dict(id=v["id"], name=v["name"], version=v["version"]) for (v,) in c.fetchall()]
    return transaction(get)


def data_range():
    def get(c, ar):
        from app.services.quant.rs_history import BENCH_CODE
        c.execute("SELECT MIN(trade_date),MAX(trade_date) FROM rs_daily WHERE market='us' AND code=%s", (BENCH_CODE,))
        first, last = c.fetchone()
        return dict(first=str(first) if first else None, last=str(last) if last else None)
    return transaction(get)


def resolve_pool(uid, ref):
    from app.services.quant import screen_source
    from app.services import screen_saved
    if not isinstance(ref, dict): raise ValueError("请选择股票池")
    if ref.get("kind") == "preset":
        found = next((p for p in screen_source.PRESETS if p["key"] == ref.get("id")), None)
    elif ref.get("kind") == "saved":
        found = next((p for p in screen_saved.list_for(uid) if str(p["id"]) == str(ref.get("id"))), None)
    else: found = None
    if not found: raise ValueError("股票池不存在或已删除，请刷新列表")
    if ref.get("script") != found["script"]:
        raise ValueError("股票池脚本已更新或未加载，请刷新筛选策略列表后重新选择")
    return {"name": found["name"], "market": found["market"], "script": found["script"], "ref": {k:ref[k] for k in ("kind", "id")}}


def save(uid, body):
    name = str(body.get("name", "")).strip()
    if not name or len(name) > 30: raise ValueError("策略名称需为一至三十字")
    rules = validate_rules(body.get("rules"))
    source_text = str(body.get("source_text", ""))
    if len(source_text) > 8000: raise ValueError("原始描述过长")
    ident = body.get("id") or str(uuid.uuid4())
    pool = resolve_pool(uid, body.get("pool"))
    def put(c, ar):
        k = key(uid, ident)
        c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (k,))
        old = ar._meta_get(c, k)
        if body.get("id") and not old: raise ValueError("策略不存在或无权修改")
        if old and body.get("version") != old["version"]: raise ValueError("策略已在其他页面修改，请重新加载")
        history = (old or {}).get("history", [])
        if old: history = history + [{k: old.get(k) for k in ("version", "name", "rules", "pool", "source_text")}]
        cfg = dict(id=ident, name=name, version=(old or {}).get("version", 0)+1, source_text=source_text,
                   rules=rules, pool=pool, execution=EXECUTION, runs=(old or {}).get("runs", []), history=history)
        ar._meta_set(c, k, cfg)
        return cfg
    return transaction(put)


def submit(uid, ident, body):
    cfg = read(uid, ident)
    validate_rules(cfg["rules"], confirmed=True)
    if body.get("version") != cfg["version"]: raise ValueError("规则版本已变化，请重新确认")
    if body.get("execution_confirmed") is not True: raise ValueError("请确认成交口径和原文核对")
    if cfg["pool"]["market"] != "us": raise ValueError("首版个人策略回测仅支持美股；其他市场可保存规则，暂不能执行")
    try: start, end = date.fromisoformat(body["start"]), date.fromisoformat(body["end"])
    except (KeyError, TypeError, ValueError): raise ValueError("请输入有效回测起止日期")
    from app.services.quant.screen_quota import today_sh
    if start >= end or (end-start).days > 370 or end > today_sh(): raise ValueError("回测区间需按先后顺序且不超过一年，也不能晚于今天")
    initial, slippage = body.get("initial"), body.get("slippage_bps")
    if type(initial) not in (int, float) or not math.isfinite(initial) or not 1000 <= initial <= 10000000: raise ValueError("初始资金应在一千至一千万之间")
    if type(slippage) not in (int, float) or not math.isfinite(slippage) or not 0 <= slippage <= 100: raise ValueError("滑点需为零至一百个基点")
    snapshot = {k: cfg[k] for k in ("name", "version", "rules", "pool", "execution", "source_text")}
    snapshot.update(start=str(start), end=str(end), initial=initial, slippage_bps=slippage)
    run = dict(id=str(uuid.uuid4()), status="queued", snapshot=snapshot,
               hash=hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest(), progress=0)
    from app.services.quant import agent_run as ar
    conn = ar._conn()
    try:
        with conn.cursor() as c:
            # 与现有个人回测共用资源锁，防止同时占满虚拟机。
            c.execute("SELECT pg_try_advisory_lock(%s)", (719280061,))
            if not c.fetchone()[0]: raise ValueError("已有个人回测正在执行，请稍后重试")
            c.execute("SELECT pg_advisory_lock(hashtext(%s))", (run["id"],))
            k = key(uid, ident)
            c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (k,))
            current = ar._meta_get(c, k)
            if current["version"] != cfg["version"]: raise ValueError("规则版本已变化，请重新提交")
            current["runs"] = current.get("runs", []) + [run]
            ar._meta_set(c, k, current)
        conn.commit()
        return conn, k, run
    except Exception:
        conn.close()
        raise


def run_job(conn, k, run):
    from app.services.quant import agent_run as ar
    def persist(progress=None):
        if progress is not None: run["progress"] = progress
        with conn.cursor() as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (k,))
            cfg = ar._meta_get(c, k)
            cfg["runs"] = [run if r["id"] == run["id"] else r for r in cfg["runs"]]
            ar._meta_set(c, k, cfg)
        conn.commit()
    try:
        from app.services.quant.agent_builder_engine import calculate
        run["status"] = "running"
        persist()
        run["result"] = calculate(run["snapshot"], persist)
        run["status"] = "done"
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("个人策略回测失败")
        conn.rollback()
        run["status"] = "failed"
        run["error"] = str(e) if isinstance(e, ValueError) else "回测失败，请检查数据或联系管理员"
    finally:
        try: persist()
        finally: conn.close()
