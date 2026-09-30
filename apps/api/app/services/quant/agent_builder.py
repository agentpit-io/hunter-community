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
SCHEMA.update({
    "ema_compare": ("买入：快EMA高于慢EMA", {"fast":("快EMA交易日",2,250,True), "slow":("慢EMA交易日",2,250,True)}),
    "sma_compare": ("买入：快SMA高于慢SMA", {"fast":("快SMA交易日",2,250,True), "slow":("慢SMA交易日",2,250,True)}),
    "price_ema": ("买入：收盘高于EMA", {"days":("EMA交易日",2,250,True)}),
    "breakout_distance": ("买入：突破前期高点且限制追高", {"days":("枢轴回看交易日",2,252,True), "max_pct":("高出枢轴上限%",0,100,False)}),
    "breakeven": ("卖出：曾盈利后跌回买入价，全部退出", {"trigger_pct":("启动保本的盈利%",.1,1000,False)}),
    "reduce_loss": ("卖出：相对买入价亏损时减仓，每条仅一次", {"pct":("相对买入价亏损%",.1,100,False), "size_pct":("卖出当时剩余持仓%",.1,100,False)}),
    "reduce_profit": ("卖出：相对买入价盈利时减仓，每条仅一次", {"pct":("相对买入价盈利%",.1,1000,False), "size_pct":("卖出当时剩余持仓%",.1,100,False)}),
    "ma_exit_offset": ("卖出：收盘跌破SMA一定比例", {"days":("SMA交易日",2,250,True), "pct":("低于均线%",0,100,False)}),
    "market_trend": ("特殊：标普500收盘高于快SMA且快SMA高于慢SMA，仅限制开仓", {"fast":("标普快SMA交易日",2,250,True), "slow":("标普慢SMA交易日",2,250,True)}),
})
BUY = {"breakout", "ma_above", "volume", "ema_compare", "sma_compare", "price_ema", "breakout_distance"}
SELL = {"stop", "take_profit", "ma_exit", "time_exit", "trailing", "breakeven", "reduce_loss", "reduce_profit", "ma_exit_offset"}
REQUIRED = {"stop", "position", "risk", "holdings"}
EXECUTION = "按日线收盘判断，下一可交易日收盘模拟成交；无盘中保证，跳空可能超过止损。只做多、不加仓；买入条件全部满足，特殊市场规则仅限制新开仓，不强制清仓。卖出硬止损最高优先级，其余数字越小越先执行；同优先级全清优于减仓，再按规则顺序。每股每日只执行一条卖出信号，分批规则每次持仓各触发一次，比例按当时剩余持仓计算，向下取整且至少一股。费用与滑点按回测设置。"


def category(kind):
    return "buy" if kind in BUY else "sell" if kind in SELL else "special"



TEMPLATES = {
 "ema_compare":"日线 EMA({fast}) > EMA({slow}) 时允许买入",
 "sma_compare":"日线 SMA({fast}) > SMA({slow}) 时允许买入",
 "price_ema":"日线收盘价 > EMA({days}) 时允许买入",
 "ma_above":"收盘价 > SMA({days}) 时允许买入",
 "breakout":"收盘价 > 前{days}日最高价（不含当日）时允许买入",
 "breakout_distance":"收盘突破前{days}日最高价，且高出不超过{max_pct}%时允许买入",
 "volume":"当日成交量 ≥ 前{days}日均量 × {ratio}（均量不含当日）",
 "stop":"收盘相对买入成交价亏损 ≥ {pct}%：全部卖出，硬止损最优先",
 "take_profit":"收盘相对买入成交价盈利 ≥ {pct}%：全部卖出",
 "breakeven":"持仓最高收盘价曾盈利 ≥ {trigger_pct}%后，收盘跌回买入价：全部卖出",
 "reduce_loss":"收盘相对买入价亏损 ≥ {pct}%：卖出剩余持仓{size_pct}%，本阶段只执行一次",
 "reduce_profit":"收盘相对买入价盈利 ≥ {pct}%：卖出剩余持仓{size_pct}%，本阶段只执行一次",
 "ma_exit":"收盘跌破 SMA({days})：全部卖出",
 "ma_exit_offset":"收盘价 < SMA({days}) × (1 − {pct}%)：全部卖出",
 "trailing":"收盘较持仓最高收盘价回撤 ≥ {pct}%：全部卖出",
 "time_exit":"持仓满{days}个交易日：全部卖出",
 "market_trend":"标普500收盘 > SMA({fast}) > SMA({slow}) 才允许新开仓；已有持仓按卖出规则处理",
 "position":"单只股票买入金额不超过总资产{pct}%",
 "risk":"单笔初始止损风险不超过总资产{pct}%",
 "holdings":"最多同时持有{count}只股票",
}


def preview(raw):
    rule = clean_rule(raw)
    if rule['type']=='pending':
        return dict(executable=False,questions=rule.get('questions',[]))
    from app.services.quant.agent_builder_engine import exit_candidates, indicator_rule
    k,p=rule['type'],rule['params']
    explanation=TEMPLATES[k].format(**p)
    steps=[]
    if k in SELL:
        if k=='breakeven': prices=[100,100*(1+p['trigger_pct']/100),100]
        elif k=='reduce_loss' or k=='stop': prices=[100,100*(1-p['pct']/100),100*(1-p['pct']/100)]
        elif k=='reduce_profit' or k=='take_profit': prices=[100,100*(1+p['pct']/100),100*(1+p['pct']/100)]
        elif k=='trailing': prices=[100,110,110*(1-p['pct']/100)]
        else: prices=[100,101,99]
        pos=dict(entry=100,high=100,age=0,fired=set())
        remaining=100
        for i,px in enumerate(prices):
            pos['high']=max(pos['high'],px);pos['age']=([0,max(0,p['days']-1),p['days']][i] if k=='time_exit' else i)
            bars=[[100,100,100,100,100]]*p.get('days',1)+[[px]*5]
            hits=exit_candidates([rule],bars,pos) if remaining else []
            shares=min(remaining,max(1,int(remaining*hits[0]['fraction']))) if hits else 0
            if hits: pos['fired'].add(0)
            remaining-=shares
            steps.append(dict(age=pos['age'],price=round(px,6),action=('发出卖出信号：'+str(shares)+'股') if shares else '无新卖出信号',remaining=remaining))
    elif k in BUY or k=='market_trend':
        n=max(p.get('days',2),p.get('slow',2))
        bars=[[100+i/10]*3+[100,100+i/10] for i in range(n+3)]
        if k=='volume':bars[-1][3]=100*p['ratio']
        steps=[dict(price=bars[-1][0],action='条件满足' if indicator_rule(rule,bars) else '条件未满足')]
    return dict(executable=True,explanation=explanation,priority=rule['priority'],steps=steps,
                note='规则演示：使用人为构造的价格和100股持仓，不是历史收益。这里展示触发信号；回测实际在下一交易日成交。')


def catalog():
    return [{"type": k, "label": label, "category": category(k), "template": TEMPLATES.get(k,""), "fields": [dict(key=n, label=v[0], min=v[1], max=v[2], integer=v[3]) for n, v in fields.items()]}
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
    if kind in ("ema_compare", "sma_compare", "market_trend") and params["fast"] >= params["slow"]:
        raise ValueError("快均线周期必须小于慢均线周期")
    priority = 0 if kind == "stop" else raw.get("priority", 10 if kind in SELL else 50)
    if type(priority) is not int or not 0 <= priority <= 100:
        raise ValueError("优先级需为零至一百的整数")
    out = dict(type=kind, params=dict(params), source=source, confirmed=raw.get("confirmed") is True,
               category=category(kind) if kind != "pending" else raw.get("category", "special"), priority=priority)
    if out["category"] not in ("buy","sell","special"): raise ValueError("规则分类无效")
    if kind == "pending":
        out["confirmed"] = False
        proposal = raw.get("proposal")
        if isinstance(proposal, dict) and proposal.get("type") in SCHEMA and proposal["type"] != "pending":
            target = proposal["type"]
            known = {k:v for k,v in (proposal.get("params") if isinstance(proposal.get("params"),dict) else {}).items() if k in SCHEMA[target][1] and type(v) in (int,float) and math.isfinite(v) and SCHEMA[target][1][k][1] <= v <= SCHEMA[target][1][k][2] and (not SCHEMA[target][1][k][3] or int(v)==v)}
            out["proposal"] = dict(type=target,params=known)
            out["category"] = category(target)
            out["questions"] = ["请确认执行口径：" + SCHEMA[target][0]] + ["请填写"+field[0] for k,field in SCHEMA[target][1].items() if k not in known]
        else:
            out["questions"] = ["这条描述尚不能转为已有规则，请明确指标、周期、触发条件和动作；不支持的语义不能直接回测。"]
    return out


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


LANGUAGES = {"text": "自然语言（中文或英文）", "python": "Python", "javascript": "JavaScript", "thinkscript": "ThinkScript", "pine": "Pine Script", "cpp": "C++", "rust": "Rust", "my": "My语言"}


def language_of(value="text"):
    if not isinstance(value, str) or value not in LANGUAGES:
        raise ValueError("请选择支持的输入语言")
    return value


def editor_metadata(body):
    description = body.get("description", "")
    assistant_prompt = body.get("assistant_prompt", "")
    if not isinstance(description, str) or len(description) > 100:
        raise ValueError("策略描述最多一百字，也可以留空")
    if not isinstance(assistant_prompt, str) or len(assistant_prompt) > 2000:
        raise ValueError("AI 输入最多两千字")
    return dict(description=description, assistant_prompt=assistant_prompt,
                source_language=language_of(body.get("source_language", "text")))


def recognition_prompt(text, single=False, language="text"):
    language = language_of(language)
    if not isinstance(text, str) or not text.strip() or len(text) > 6000:
        raise ValueError("请输入策略描述，最多六千字")
    prompt = ("你是策略规则翻译器。只输出JSON对象，rules数组中每项为type、params、source。"
              "输入语言为" + LANGUAGES[language] + "。代码仅供理解，不能执行。正确理解该语言指标、索引和百分比单位；无法无损映射为支持规则时返回pending，不得声称支持原代码运行。"
              "source必须逐字引用用户原文的一段，不得改写；params只能使用source中明确出现的阿拉伯数字，不得猜参数。"
              "覆盖原文所有要求，不支持的条件、歧义、缺参数均用pending保留原文且params为空。"
              "不得把复杂条件近似成简单条件。不支持加仓、盘中执行或自动优化。支持breakeven保本和reduce_loss/reduce_profit分批卖出；均以买入成交价为基准，size_pct按当时剩余持仓，每条只触发一次。"
              "EMA8>EMA21且收盘高于EMA8要拆为ema_compare和price_ema。跌破SMA下方比例用ma_exit_offset。"
              "含义明确的比均量增加40%可换算ratio=1.4；一半可换算size_pct=50，全部=100。但平均周期、成交口径、再跌5%的基准不明确时必须pending，不得猜20日或累计10%。"
              "pending可以额外返回proposal={type:最接近的受支持类型,params:原文明确的参数}，category取buy/sell/special；用户会补齐并核对。每条包含完整原文，不遗漏条件。"
              "市场趋势规则仅支持标普500双SMA及价格，必须说明指数与两周期；泛称大盘上升时返回pending建议market_trend且参数为空。"
              "volume均量不含信号当日，breakout为前N日最高价不含当日。"
              + ("这是单条重新识别，只返回一项；包含多个独立要求时返回pending。" if single else "每个独立要求一项。")
              + "支持的类型参数如下：" + json.dumps(catalog(), ensure_ascii=False))
    return prompt


def recognize(text, single=False, language="text"):
    from app.services.online_analysis.llm_client import llm_json_call
    from app.services.quant.screen_nl import model_name
    prompt = recognition_prompt(text, single, language)
    data, meta = llm_json_call(prompt, text, model=model_name(), max_tokens=4500, temperature=0, retry_on_parse_fail=False)
    return recognition_result(data, meta, text, single)


def connection_cause(exc):
    """只记录异常类名，不记录可能包含凭据、策略或代理地址的异常正文。"""
    import socket
    import ssl
    import httpx
    chain=[];seen=set();label='网络连接异常'
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc));chain.append(type(exc).__name__)
        if isinstance(exc,socket.gaierror): label='域名解析失败'
        elif isinstance(exc,ssl.SSLCertVerificationError): label='安全证书校验失败'
        elif isinstance(exc,httpx.ProxyError): label='代理连接异常'
        elif isinstance(exc,httpx.RemoteProtocolError): label='连接被服务端或中间网络关闭'
        elif isinstance(exc,httpx.ConnectTimeout): label='建立连接超时'
        exc=exc.__cause__
    return label, '/'.join(chain[:8])


async def recognize_async(text, single=False, language="text"):
    """独立异步连接：取消会关闭本次HTTP请求，不占用全局同步模型线程。"""
    import asyncio
    from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, APIStatusError
    from loguru import logger
    from app.services.online_analysis.llm_client import _resolve
    from app.services.online_analysis.prompts import parse_llm_json
    from app.services.lang_guard import ZH_ONLY_RULE
    from app.services.quant.screen_nl import model_name
    prompt = recognition_prompt(text, single, language)
    base, key_, configured_model = await asyncio.to_thread(_resolve)
    if not (base and key_ and configured_model):
        raise ValueError("尚未配置 AI 模型，请先在模型设置中完成配置；也可以手动添加规则")
    model = await asyncio.to_thread(model_name)
    import httpx
    arguments = dict(model=model, messages=[{"role":"system", "content":prompt+ZH_ONLY_RULE}, {"role":"user", "content":text}],
                     max_tokens=4500, temperature=0, response_format={"type":"json_object"})
    # 三次尝试共用路由90秒总时限；每次关闭旧连接，取消立即向上传播。
    retry_left = 2
    while True:
        try:
            async with AsyncOpenAI(base_url=base, api_key=key_, timeout=httpx.Timeout(75,connect=8,write=15,pool=8), max_retries=0) as client:
                completion = await client.chat.completions.create(**arguments)
            break
        except asyncio.CancelledError:
            raise
        except APITimeoutError as e:
            label, cause = connection_cause(e)
            logger.warning("[builder-ai] timeout cause={} remaining={}", cause, retry_left)
            if 'ConnectTimeout' in cause and retry_left:
                retry_left -= 1
                await asyncio.sleep(1 if retry_left else 3)
                continue
            raise ValueError("AI 识别失败：模型已配置，但本次响应超时，请稍后重试或分段识别。原规则未改变。")
        except APIConnectionError as e:
            label, cause = connection_cause(e)
            logger.warning("[builder-ai] connection cause={} remaining={}", cause, retry_left)
            if retry_left:
                retry_left -= 1
                await asyncio.sleep(1 if retry_left else 3)
                continue
            raise ValueError("AI 识别失败：暂时无法连接模型服务（"+label+"），已自动重连两次。原文和已有规则已保留，请稍后重试；持续失败请检查服务状态。")
        except APIStatusError as e:
            code = e.status_code
            logger.warning("[builder-ai] upstream HTTP {}", code)
            # 某些兼容网关不支持JSON模式，仅在明确拒绝该参数时降级。
            body_ = e.body if isinstance(e.body, dict) else {}
            error_ = body_.get("error", body_)
            message_ = str(error_.get("message", "")).lower() if isinstance(error_, dict) else ""
            if code in (400,422) and "response_format" in arguments and any(x in message_ for x in ("response_format", "json_object", "json mode")):
                arguments.pop("response_format")
                continue
            if code >= 500 and retry_left:
                retry_left -= 1
                await asyncio.sleep(1 if retry_left else 3)
                continue
            reason = {401:"模型服务拒绝凭据，请检查已绑定服务的授权状态",403:"当前授权无权使用此模型，请检查模型权限",402:"模型服务额度不足，请检查服务余额",404:"模型服务地址或模型名称不可用，请检查模型设置",429:"模型服务限流或额度已用完，请稍后重试并检查额度"}.get(code)
            if not reason: reason = "模型服务暂时不可用，请稍后重试" if code >= 500 else "模型服务拒绝本次请求，请检查模型兼容性"
            raise ValueError("AI 识别失败：" + reason + "。原规则未改变。")
        except Exception as e:
            logger.warning("[builder-ai] unexpected response type={}", type(e).__name__)
            raise ValueError("AI 识别失败：模型返回异常，未能读取结果。原规则未改变。")
    choices = getattr(completion, "choices", None)
    if not choices:
        raise ValueError("AI 返回内容格式无效：模型没有返回识别内容，请重试。原规则未改变。")
    first = choices[0]
    if getattr(first, "finish_reason", None) == "length":
        raise ValueError("AI 返回内容被截断，请减少单次规则数量或分段识别。原规则未改变。")
    content = getattr(getattr(first, "message", None), "content", None)
    data = parse_llm_json(content) if isinstance(content, str) else None
    if not isinstance(data, dict):
        raise ValueError("AI 返回内容格式无效：未生成可读取的规则，请重试或分段识别。原规则未改变。")
    usage = getattr(completion, "usage", None)
    meta = {"tokens_in":usage.prompt_tokens if usage else None, "tokens_out":usage.completion_tokens if usage else None}
    return recognition_result(data, meta, text, single)


def recognition_result(data, meta, text, single=False):
    if data is None and meta.get("error") == "no_api_key":
        raise ValueError("尚未配置 AI 模型，请先在模型设置中完成配置；也可以手动添加规则")
    if data is None:
        raise ValueError("AI 识别失败：请在设置中检查模型配置与连接，或稍后重试。原规则未改变。")
    if not isinstance(data, dict):
        raise ValueError("AI 返回内容格式无效，请重新识别。原规则未改变。")
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
            if row['type'] != 'pending' and ('盘中' in source or '再跌' in source):
                raise ValueError('日线执行或后续跌幅基准需要用户澄清')
            def allowed(k, v):
                if float(v) in nums: return True
                if k == "size_pct" and ((v == 50 and "一半" in source) or (v == 100 and "全部" in source)): return True
                if k == "ratio" and any(w in source for w in ("增加", "放大", "高于")):
                    return any(abs(v-(1+n/100)) < 1e-9 for n in nums)
                return False
            if any(not allowed(k,v) for k,v in row["params"].items()):
                raise ValueError("模型添加了未提供的数字")
        except (ValueError, TypeError):
            row = clean_rule(dict(type="pending", params={}, source=source, category=raw.get("category", category(raw.get("type")))))
        if row["type"] == "pending":
            proposal = raw.get("proposal")
            if proposal is None and raw.get('type') in SCHEMA and raw.get('type')!='pending':
                proposal = dict(type=raw['type'],params=raw.get('params',{}))
            if '盘中' in source or '再跌' in source:
                proposal = None
                row['questions'] = ['请明确采用日线收盘判断还是必须盘中执行；当前只支持前者。' if '盘中' in source else '请明确第二阶段相对买入价的累计跌幅和卖出剩余持仓比例；不会自动把再跌理解成累计跌幅。']
            if isinstance(proposal,dict) and proposal.get("type") in SCHEMA:
                known = proposal.get("params",{})
                if isinstance(known,dict):
                    proposal = dict(type=proposal["type"],params={k:v for k,v in known.items() if type(v) in (int,float) and v in nums})
                    row = clean_rule(dict(row,proposal=proposal))
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
    if not result or result.get("deleted"): raise ValueError("策略不存在、已删除或不属于当前账号")
    return result


def list_for(uid):
    def get(c, ar):
        c.execute("SELECT value FROM agent_meta WHERE key LIKE %s ORDER BY key", (f"builder:{uid}:%",))
        return [dict(id=v["id"], name=v["name"], version=v["version"]) for (v,) in c.fetchall() if not v.get("deleted")]
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
    editor = editor_metadata(body)
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
        if body.get("id") and (not old or old.get("deleted")): raise ValueError("策略不存在或无权修改")
        if old and body.get("version") != old["version"]: raise ValueError("策略已在其他页面修改，请重新加载")
        history = (old or {}).get("history", [])
        if old: history = history + [{k: old.get(k) for k in ("version", "name", "rules", "pool", "source_text", "description", "source_language", "assistant_prompt")}]
        cfg = dict(id=ident, name=name, version=(old or {}).get("version", 0)+1, source_text=source_text,
                   rules=rules, pool=pool, execution=EXECUTION, engine_version=2, runs=(old or {}).get("runs", []), history=history)
        cfg.update(editor)
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
    snapshot.update(editor_metadata(cfg))
    snapshot["engine_version"] = 2
    snapshot["execution"] = EXECUTION
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
            if current.get("deleted") or current["version"] != cfg["version"]: raise ValueError("策略已删除或规则版本已变化，请重新提交")
            current["runs"] = current.get("runs", []) + [run]
            ar._meta_set(c, k, current)
        conn.commit()
        return conn, k, run
    except Exception:
        conn.close()
        raise


def delete(uid, ident):
    read(uid, ident)  # 顺便识别已中断的回测，避免永久不能删除。
    def remove(c, ar):
        k = key(uid, ident)
        c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (k,))
        cfg = ar._meta_get(c, k)
        if not cfg or cfg.get("deleted"): raise ValueError("策略不存在或已删除")
        if any(r["status"] in ("queued", "running") for r in cfg.get("runs", [])):
            raise ValueError("此策略正在回测，请等待完成后再删除")
        cfg["deleted"] = True
        ar._meta_set(c, k, cfg)
        return {"deleted": True}
    return transaction(remove)


def filter_research(uid, board):
    if not uid: return board
    hidden = transaction(lambda c, ar: ar._meta_get(c, f"research-hidden:{uid}", []))
    return dict(board, lines=[ln for ln in board.get("lines", []) if ln["key"] not in hidden])


def delete_research(uid, line):
    from app.services.quant import agent_research
    def remove(c, ar):
        if not any(ln["key"] == line for ln in agent_research.all_lines(c)):
            raise ValueError("研究策略不存在")
        k = f"research-hidden:{uid}"
        c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (k,))
        hidden = ar._meta_get(c, k, [])
        if line not in hidden: hidden.append(line)
        ar._meta_set(c, k, hidden)
        return {"deleted": True}
    return transaction(remove)


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
