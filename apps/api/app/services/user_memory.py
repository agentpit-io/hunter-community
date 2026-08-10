"""用户记忆体 —— 画像读写 + 对话浓缩 + system prompt 组装。

两条线:
  ① 画像(user_profile)—— 用户主动设置的偏好,结构化,可统计
  ② 记忆(user_memory) —— 系统从对话里浓缩的事实,JSONB,结构会演进

⚠️ 与老板文档里的 hunter-memory 不是一回事:
   那个记的是「投资论点 + 关键假设」,按季度用另类数据核对;
   这里记的是「用户偏好 + 对话习惯」,每次对话作 context。两者互补。

浓缩的三条红线(写在这里,改代码时先读):
  1. 只记事实,不记推断 —— 记"问过 8 次茅台",不记"很看好茅台"
  2. 持仓只在用户主动说时记,且标注为用户自述,不作为投资建议依据
  3. 用户可查看/编辑/清空 —— 隐私底线,必须留控制权
"""
import json
import logging
import re
import time
from collections import Counter
from datetime import date

from app.services.database import get_conn

log = logging.getLogger(__name__)

# 记忆权重每次浓缩后按此系数衰减。
# 依据:InKH(arXiv 2606.01886)测出"陈旧记忆"是财经 Agent 记忆系统里收益最大的一项
# (使用率降 96.58%,且收益在行情剧变后最明显)。人三个月前偏好半导体,行情换了
# 早换赛道,不衰减的话画像会一直往旧方向引。
DECAY = 0.9
# 低于此权重的条目直接丢弃,避免长期积累无意义的尾巴
MIN_WEIGHT = 0.3
# 成熟度门控:样本量不到这个数不注入 system prompt。
# 防的是"随口问了一次茅台,画像就开始往白酒引"。
MIN_SIGNAL = 3

# 画像枚举 —— 前端选项与后端校验的唯一来源
RISK_STYLES = ["conservative", "steady", "balanced", "active", "aggressive"]
HORIZONS = ["intraday", "week", "month", "quarter", "year"]
MARKETS = ["A", "HK", "US"]
CAP_PREFS = ["large", "mid", "small", "any"]
WEIGHTS = ["fundamental", "technical", "flow", "news"]
VERBOSITY = ["brief", "points", "detailed"]

_LABEL = {
    "conservative": "保守", "steady": "稳健", "balanced": "平衡",
    "active": "积极", "aggressive": "激进",
    "intraday": "日内", "week": "数周", "month": "数月",
    "quarter": "一季度以上", "year": "一年以上",
    "A": "A股", "HK": "港股", "US": "美股",
    "large": "大盘股", "mid": "中盘股", "small": "小盘股", "any": "不限市值",
    "fundamental": "基本面", "technical": "技术面", "flow": "资金面", "news": "消息面",
    "brief": "一句话结论", "points": "要点式", "detailed": "详细分析",
}

DEFAULT_PROFILE = {
    "risk_style": "", "max_drawdown": None, "horizon": "", "markets": [],
    "sectors": [], "cap_pref": "", "weight_order": [], "verbosity": "",
    "taboos": [], "onboarded": False,
}

_FIELDS = ("risk_style", "max_drawdown", "horizon", "markets", "sectors",
           "cap_pref", "weight_order", "verbosity", "taboos", "onboarded")


# ── 画像 ────────────────────────────────────────────────

def get_profile(user_id: str) -> dict:
    out = dict(DEFAULT_PROFILE)
    try:
        c = get_conn(); cur = c.cursor()
        cur.execute("SELECT " + ", ".join(_FIELDS) + " FROM user_profile WHERE user_id = %s",
                    (user_id,))
        row = cur.fetchone(); c.close()
        if row:
            for k, v in zip(_FIELDS, row):
                out[k] = list(v) if isinstance(v, list) else v
    except Exception as e:
        log.warning("[memory] 读画像失败: %s", e)
    return out


def save_profile(user_id: str, patch: dict) -> dict:
    allowed = {k: v for k, v in patch.items() if k in _FIELDS and v is not None}
    if not allowed:
        return get_profile(user_id)
    cols = ", ".join(allowed)
    ph = ", ".join(["%s"] * len(allowed))
    upd = ", ".join(f"{k} = EXCLUDED.{k}" for k in allowed)
    c = get_conn(); cur = c.cursor()
    cur.execute(f"""INSERT INTO user_profile (user_id, {cols}) VALUES (%s, {ph})
                    ON CONFLICT (user_id) DO UPDATE SET {upd}, updated_at = NOW()""",
                [user_id] + list(allowed.values()))
    c.commit(); c.close()
    return get_profile(user_id)


# ── 记忆 ────────────────────────────────────────────────

def get_memory(user_id: str) -> dict:
    try:
        c = get_conn(); cur = c.cursor()
        cur.execute("SELECT memory, session_count FROM user_memory WHERE user_id = %s", (user_id,))
        row = cur.fetchone(); c.close()
        if row:
            mem = row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")
            return {"memory": mem, "session_count": row[1]}
    except Exception as e:
        log.warning("[memory] 读记忆失败: %s", e)
    return {"memory": {}, "session_count": 0}


def save_memory(user_id: str, memory: dict, session_id: str = "", bump: bool = False) -> dict:
    before = get_memory(user_id)["memory"]
    c = get_conn(); cur = c.cursor()
    cur.execute("""INSERT INTO user_memory (user_id, memory, session_count)
                   VALUES (%s, %s::jsonb, %s)
                   ON CONFLICT (user_id) DO UPDATE SET
                     memory = EXCLUDED.memory,
                     session_count = user_memory.session_count + %s,
                     updated_at = NOW()""",
                (user_id, json.dumps(memory, ensure_ascii=False), 1 if bump else 0,
                 1 if bump else 0))
    cur.execute("""INSERT INTO user_memory_log (user_id, session_id, before, after)
                   VALUES (%s,%s,%s::jsonb,%s::jsonb)""",
                (user_id, session_id,
                 json.dumps(before, ensure_ascii=False),
                 json.dumps(memory, ensure_ascii=False)))
    c.commit(); c.close()
    return get_memory(user_id)


def clear_memory(user_id: str) -> None:
    save_memory(user_id, {}, session_id="(user-cleared)")


# ── 对话浓缩 ────────────────────────────────────────────

_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
# 用户自述持仓的说法。只在明确表达时才记,且标注"用户自述"
_HOLD_RE = re.compile(r"(我(?:已经)?(?:买|持有|建仓|加仓)了?|我的持仓|成本(?:价)?约?)")

# ── 股票名 ↔ 代码 词典(来自 stocks_catalog · 全 A 股 5500+) ──────────
#
# 为什么必须有:人说话是说"茅台"不是说"600519"。只认 6 位数字的话,
# 绝大多数真实对话什么都记不下来 —— 这是第一版最要命的缺口。
#
# 进程内缓存 1 小时:词典是静态数据,每次浓缩都查库既慢又没必要。
_DICT: dict = {"at": 0.0, "code2name": {}, "name2code": {}, "names": []}
_DICT_TTL = 3600

# 不能当股票简称的日常词/行业通用词。
# 这些词在 stocks_catalog 里可能唯一,但在自然语言里是普通名词,
# 用作简称会把无关对话误记成某只股票。
_GENERIC_WORDS = {
    # 行业与业务
    "电器", "银行", "证券", "保险", "地产", "医药", "医疗", "科技", "电子", "机械",
    "化工", "能源", "环保", "传媒", "文化", "旅游", "食品", "服饰", "家居", "汽车",
    "钢铁", "水泥", "建材", "农业", "牧业", "渔业", "电力", "燃气", "水务", "港口",
    "航空", "航运", "物流", "教育", "体育", "娱乐", "游戏", "软件", "网络", "通信",
    "智能", "生物", "材料", "装备", "重工", "轻工", "机电", "光电", "半导",
    # 公司名后缀
    "股份", "集团", "控股", "实业", "发展", "国际", "投资", "资源", "产业", "企业",
    "科创", "高科", "信息", "数据", "系统", "工程", "建设", "置业", "商业", "贸易",
    # 高频日常词
    "时代", "平安", "未来", "东方", "华夏", "中国", "世纪", "第一", "新华", "长城",
    "海洋", "阳光", "明星", "希望", "生活", "健康", "美好", "幸福", "力量", "精工",
}


def _stock_dict() -> dict:
    if _DICT["code2name"] and time.time() - _DICT["at"] < _DICT_TTL:
        return _DICT
    try:
        c = get_conn(); cur = c.cursor()
        cur.execute("SELECT code, name FROM stocks_catalog WHERE enabled AND name <> ''")
        rows = cur.fetchall(); c.close()
        c2n = {r[0]: r[1] for r in rows}
        n2c = {r[1]: r[0] for r in rows}

        # 简称:人说"茅台"不说"贵州茅台"。取全称后 2 字作简称,但要过两道关:
        #   ① 全市场唯一 —— "银行"同时指向工行/招行/平安银行,一律丢弃
        #   ② 不是日常词 —— "格力电器→电器"在词典里唯一,但用户说"我想买个电器"
        #      就会被记成格力;"宁德时代→时代"同理
        # 原则是宁可漏认也不能认错:认错会把别人的偏好写进你的画像,而且用户看不出来。
        short_cnt: Counter = Counter()
        for name in n2c:
            if len(name) >= 3:
                short_cnt[name[-2:]] += 1
        for name, code in list(n2c.items()):
            if len(name) < 3:
                continue
            s = name[-2:]
            if short_cnt[s] == 1 and s not in n2c and s not in _GENERIC_WORDS:
                n2c[s] = code

        # 长名优先匹配,避免"中际旭创"被更短的名字截胡
        _DICT.update({
            "at": time.time(), "code2name": c2n, "name2code": n2c,
            "names": sorted(n2c.keys(), key=len, reverse=True),
        })
        log.info("[memory] 股票词典载入 %d 条", len(c2n))
    except Exception as e:
        log.warning("[memory] 载入股票词典失败: %s", e)
    return _DICT


def extract_symbols(text: str) -> Counter:
    """从文本里认出股票 —— 6 位代码 + 中文名双通道,统一归一到代码。"""
    d = _stock_dict()
    found: Counter = Counter()
    for code in _CODE_RE.findall(text):
        if code in d["code2name"] or not d["code2name"]:
            found[code] += 1
    # 名字匹配。长名先扫,命中后从文本里挖掉,避免"贵州茅台"又被简称"茅台"数一遍
    rest = text
    for name in d["names"]:
        if name not in rest:
            continue
        found[d["name2code"][name]] += rest.count(name)
        rest = rest.replace(name, " ")
    return found

# 常见追问意图 → 归一化标签。用于统计"这人反复关心什么"
_TOPIC_PATTERNS = [
    ("估值", r"估值|市盈|PE|贵不贵|便宜"),
    ("走势预测", r"预测|未来.{0,3}天|走势|会涨|会跌"),
    ("财报", r"财报|业绩|营收|净利|季报|年报"),
    ("资金面", r"北向|主力|资金|龙虎榜|融资"),
    ("风险", r"风险|回撤|止损|套牢|亏"),
    ("行业对比", r"对比|相比|同行|竞品|谁更"),
]


def condense(user_id: str, session_id: str, texts: list[str], symbols_hint: dict | None = None) -> dict:
    """把一段对话浓缩进用户记忆。

    刻意用规则而不是再调一次 LLM:
      · 浓缩本身要跑在每个会话上,调 LLM 成本和延迟都不划算
      · 规则提取只产出可核对的事实(提过哪些代码、问过哪类问题),
        不会像 LLM 那样"脑补"出用户没说过的倾向 —— 正好对上"只记事实不记推断"

    texts: 该会话里**用户说的话**(不含 assistant 回复,避免把模型的话当成用户偏好)
    """
    prev = get_memory(user_id)["memory"]
    joined = "\n".join(texts)
    today = date.today().isoformat()
    d = _stock_dict()

    def _decay(items: list, key: str) -> dict:
        """旧条目权重衰减 —— 近期行为权重更高,陈年数据自然淡出。"""
        out = {}
        for it in items or []:
            w = float(it.get("weight", it.get("count", 0))) * DECAY
            if w >= MIN_WEIGHT:
                out[it[key]] = {**it, "weight": round(w, 3)}
        return out

    # ① 提到的股票 —— 代码 + 中文名双通道
    sym_map = _decay(prev.get("mentioned_symbols"), "code")
    for code, n in extract_symbols(joined).items():
        e = sym_map.setdefault(code, {"code": code, "name": "", "count": 0,
                                      "weight": 0.0, "first_seen": today})
        e["count"] = int(e.get("count", 0)) + n
        e["weight"] = round(float(e.get("weight", 0)) + n, 3)
        e["last_seen"] = today
        # 名字从词典补(第一版这里一直是空的 —— 前端没传 symbols,后端也没查库)
        if not e.get("name"):
            e["name"] = d["code2name"].get(code) or (symbols_hint or {}).get(code, "")
    merged_symbols = sorted(sym_map.values(), key=lambda x: -x.get("weight", 0))[:30]

    # ② 反复出现的问题类型
    topic_map = _decay(prev.get("recurring_topics"), "topic")
    for label, pat in _TOPIC_PATTERNS:
        hits = len(re.findall(pat, joined))
        if not hits:
            continue
        e = topic_map.setdefault(label, {"topic": label, "count": 0, "weight": 0.0,
                                         "first_seen": today})
        e["count"] = int(e.get("count", 0)) + hits
        e["weight"] = round(float(e.get("weight", 0)) + hits, 3)
        e["last_seen"] = today
    merged_topics = sorted(topic_map.values(), key=lambda x: -x.get("weight", 0))[:10]

    # ③ 用户自述持仓(只在明确表达时记,标注来源)
    stated = list(prev.get("stated_positions") or [])
    if _HOLD_RE.search(joined):
        seen = {p.get("symbol") for p in stated}
        for line in texts:
            if not _HOLD_RE.search(line):
                continue
            for code in extract_symbols(line):
                if code in seen:
                    continue
                stated.append({
                    "symbol": code, "name": d["code2name"].get(code, ""),
                    "note": line.strip()[:80], "source": "用户自述", "first_seen": today,
                })
                seen.add(code)
        stated = stated[-20:]

    memory = {
        "mentioned_symbols": merged_symbols,
        "recurring_topics": merged_topics,
        "stated_positions": stated,
    }
    return save_memory(user_id, memory, session_id=session_id, bump=True)["memory"]


# ── system prompt 组装 ──────────────────────────────────

def build_system_prompt(user_id: str) -> str:
    """把画像 + 记忆压成一段 system prompt,由 BFF 随每条消息带给模型。

    刻意做得很短:每条消息都要带,长了纯烧 token。
    没设置过画像也没记忆的用户返回空串 —— 不注入,不影响原有行为。
    """
    p = get_profile(user_id)
    mem = get_memory(user_id)["memory"]
    lines: list[str] = []

    if p.get("risk_style"):
        s = f"风险偏好{_LABEL.get(p['risk_style'], p['risk_style'])}"
        if p.get("max_drawdown"):
            s += f",可接受回撤约{p['max_drawdown']}%"
        lines.append(s)
    if p.get("horizon"):
        lines.append(f"持有周期{_LABEL.get(p['horizon'], p['horizon'])}")
    if p.get("markets"):
        lines.append("关注" + "/".join(_LABEL.get(m, m) for m in p["markets"]))
    if p.get("sectors"):
        lines.append("偏好行业:" + "、".join(p["sectors"][:8]))
    if p.get("cap_pref") and p["cap_pref"] != "any":
        lines.append(_LABEL.get(p["cap_pref"], p["cap_pref"]))
    if p.get("weight_order"):
        lines.append("看重次序:" + " > ".join(_LABEL.get(w, w) for w in p["weight_order"]))
    if p.get("verbosity"):
        lines.append("回答风格:" + _LABEL.get(p["verbosity"], p["verbosity"]))
    if p.get("taboos"):
        lines.append("回避:" + "、".join(p["taboos"][:6]))

    # 成熟度门控:只有累计到 MIN_SIGNAL 次的条目才注入。
    # 防的是"随口问了一次茅台,画像就开始往白酒引" —— 一条偶发记录不该左右后续所有回答。
    syms = [s for s in (mem.get("mentioned_symbols") or []) if s.get("weight", 0) >= MIN_SIGNAL]
    if syms:
        top = "、".join(f"{s.get('name') or s['code']}" for s in syms[:5])
        lines.append(f"近期常看:{top}")
    topics = [t for t in (mem.get("recurring_topics") or []) if t.get("weight", 0) >= MIN_SIGNAL]
    if topics:
        lines.append("常问:" + "、".join(t["topic"] for t in topics[:4]))

    if not lines:
        return ""

    return (
        "【用户画像】以下是该用户的既有偏好,回答时据此调整侧重与详略,不要复述本段内容。\n"
        + "\n".join(f"- {x}" for x in lines)
        + "\n注意:画像仅用于调整表达方式,不构成投资建议依据;不得据此推荐买卖。"
    )
