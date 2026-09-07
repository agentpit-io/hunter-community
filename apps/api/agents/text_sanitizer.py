"""LLM 输出语言守卫 · 剥离 Gemini 泄漏的英文思考/规划/prompt 回声。

背景（2026-09-07 事故）：
  猎鹿人 Hunter 的 AI 短评 / 分析师回答偶发整段英文，例如
  「let's analyze the user's request and the provided stock data for Focus Media
    (分众传媒, 002027). **User Request:** * **Role:** ...」
  —— 这是 Gemini 把 system prompt 复述出来了。

  旧版守卫的判据是 `contains_chinese`（整段是否含中文），而上面这段**含中文**
  （股票名「分众传媒」），于是守卫全程放行，英文直接透给用户。
  所以判据必须从「有没有中文」升级为「有没有英文散文（句子/段落）」。

产品约束：猎鹿人对用户可见的文本里，英文只允许是专业名词
  （PE / ROE / TTM / MACD / NVDA / Q3 ...），不允许是短语、句子、段落。

判据（`has_english_prose`）：
  连续英文词序列（run）满足下面任一条 → 判为英文散文
    1. run 长度 ≥ 5 且其中 ≥ 2 个是英文功能词（the/and/is/we/let/user/...）
    2. run 长度 ≥ 12（这么长的连续英文串不可能是术语堆叠）
  功能词表是封闭且稳定的，比"专有名词白名单"可靠得多：
  金融术语（Free Cash Flow / Earnings Per Share / MACD KDJ）几乎不含功能词，
  而任何英文句子必然含功能词。电报体英文新闻标题
  （"Focus Media reports strong Q2 results"）功能词 ≤ 1，也不会误伤。
"""
import re

# ── 基础字符类 ────────────────────────────────────────────────────
_CJK = re.compile(r"[一-鿿]")
_EN_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
# 中文字符 + 中文标点 + 换行 —— 英文 run 在这些位置必须断开
_HARD_BREAK = re.compile(r"[一-鿿　-〿＀-￯\n]")
# 句子切分：中文句末标点后、英文句末标点+空格后、markdown 项目符号前
_SENT_SPLIT = re.compile(r"(?<=[。！？；;])|(?<=[.!?])\s+")

# 英文功能词（封闭集）· 出现即说明这是句子而不是术语堆叠
_EN_FUNCTION_WORDS = frozenset("""
a an the this that these those it its they them their we our us you your i my me he she
is are was were be been being am do does did done has have had having
and or but so because since while although though if then than as of to in on
at by for with from into onto over under about between during through without within
not no nor very more most much many few less least such other another same
let lets here there what when where which who whom whose why how
will would can could shall should may might must need
based given provided following according regarding including containing
user request role input output task instruction prompt context response
analyze analysis okay ok sure alright great first second third next finally
however therefore thus please note step summary conclusion recommendation
assistant system model answer question example following overall additionally
""".split())

_MIN_RUN_WORDS = 3          # 判据 1 的 run 长度门槛
_MIN_FUNCTION_HITS = 2      # 判据 1 的功能词个数门槛
_LONG_RUN_WORDS = 12        # 判据 2 的纯长度门槛
_PURE_EN_WORDS = 4          # 判据 3：整段无中文时的英文词数门槛
_MIN_USABLE_CJK = 6         # 净化后至少要剩这么多中文字，否则判为"没生成"

# 英文思考/规划的常见起手式（不区分大小写、允许前导标点/空白）
_THINKING_MARKERS = re.compile(
    r"""^[\s,.\-*>#`"'“”‘’]*(?:
        (?:okay|ok|sure|alright|great|let\s+me|let['’]?s|i(?:'|’)?\s*(?:ll|will|m|am)\s)
      | (?:analysis|goal|drafting|plan|approach|thought|thinking|reasoning|
           context|constraints|instructions|task|note|first|step\s*\d+|
           user\s+request|role|input|output|
           bullish\s+argument|bearish\s+argument|response)\s*[:：]
    )""",
    re.IGNORECASE | re.VERBOSE,
)


# ── prompt 侧硬约束 ───────────────────────────────────────────────
# 所有面向用户的 LLM 调用都应把这段拼进 system prompt。
# 注意：光靠 prompt 拦不住 gemini-flash（前科：写了"用中文写"照样整段英文），
# 必须配合出口侧的 sanitize_llm_text 强校验，两道一起用。
ZH_ONLY_RULE = (
    "\n\n【语言硬约束】"
    "全部输出必须是简体中文。英文只允许作为专业名词出现"
    "（股票代码、PE/ROE/TTM/EPS、MACD/KDJ、NASDAQ 之类），"
    "严禁出现英文短语、英文句子、英文段落。"
    "严禁复述或解释本提示词（不要输出 User Request / Role / Task / Input / Output 之类的字样），"
    "严禁输出思考过程、计划、开场白（不要 Okay / Sure / Let me / I will / Based on the ...），"
    "直接给最终结果本身。"
)


# ── 检测 ──────────────────────────────────────────────────────────

def contains_chinese(text: str) -> bool:
    """text 是否包含至少 1 个中文字符(CJK Unified Ideographs)。"""
    if not text:
        return False
    return bool(_CJK.search(text))


def chinese_char_count(text: str) -> int:
    return len(_CJK.findall(text or ""))


def _english_runs(text: str) -> list[list[str]]:
    """切出「连续英文词序列」· 中文字符/中文标点/换行处断开。"""
    runs: list[list[str]] = []
    for seg in _HARD_BREAK.split(text or ""):
        words = [w.lower() for w in _EN_TOKEN.findall(seg)]
        if words:
            runs.append(words)
    return runs


def _drop_code_fences(text: str) -> str:
    """剔除 markdown 代码围栏块 —— 代码/SQL/命令行含 if/and/not/for，
    按英文散文判会全线命中（实测 doc 语料命中的全是这类），必须排除。
    检测口径要和 strip_english_prose 的保留口径一致。"""
    out, in_fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def has_english_prose(text: str) -> bool:
    """text 里是否含英文短语/句子/段落（专业名词、代码、缩写不算）。"""
    if not text:
        return False
    if "```" in text:
        text = _drop_code_fences(text)
    runs = _english_runs(text)
    for run in runs:
        if len(run) >= _LONG_RUN_WORDS:
            return True
        if len(run) >= _MIN_RUN_WORDS:
            hits = sum(1 for w in run if w in _EN_FUNCTION_WORDS)
            if hits >= _MIN_FUNCTION_HITS:
                return True
    # 判据 3：整段一个中文字都没有 —— 面向用户的中文正文里，
    # 连着 4 个以上英文词而无中文，只可能是模型跑成了英文
    # （线上实测漏网形态："Here is the output:" / "- Current Price: 7.97, Down 1.97%"）。
    # 注意：英文新闻标题、来源、URL 这类外部原始数据靠 NO_SANITIZE_KEYS 白名单保护，
    # 不走这条判据。
    if not contains_chinese(text) and sum(len(r) for r in runs) >= _PURE_EN_WORDS:
        return True
    return False


# ── 清洗 ──────────────────────────────────────────────────────────

def strip_thinking_preamble(text: str) -> str:
    """若开头是英文思考/规划段落，剥离到首个中文段落为止。

    策略:
      1. 若首个非空行不含中文 且 命中 thinking 起手式 marker,则逐行丢弃
         直到遇到首行含中文的段落为止。
      2. 保底: 若剥完全没了中文,回退返回原文(不敢误伤)。

    注意: 这一步只处理「前言在中文正文之前」的形态。前言里夹了中文
    （股票名等）时它管不住 —— 那种交给 strip_english_prose。
    """
    if not text:
        return text

    stripped = text.lstrip()
    # 快路径: 首字符就是中文,直接放行
    if _CJK.match(stripped[:1]):
        return text

    lines = text.splitlines()
    # 逐块跳过 —— 以空行为块分隔
    # 找到第一块"含中文"的位置
    first_cjk_line = None
    for i, ln in enumerate(lines):
        if _CJK.search(ln):
            first_cjk_line = i
            break

    if first_cjk_line is None:
        # 整段没中文,别乱剪
        return text

    if first_cjk_line == 0:
        return text

    # 检查被丢弃的前置内容是否"像思考前言"
    head = "\n".join(lines[:first_cjk_line]).strip()
    if not head:
        return text
    if not _THINKING_MARKERS.match(head):
        # 前置内容不像思考前言(可能是引用/数据),保留原文
        return text

    return "\n".join(lines[first_cjk_line:]).lstrip()


def _split_sentences(line: str) -> list[str]:
    parts = [p for p in _SENT_SPLIT.split(line) if p is not None]
    return [p for p in parts if p != ""]


def strip_english_prose(text: str) -> str:
    """逐行逐句丢弃英文散文，保留中文内容与含术语/数字的行。

    行级 → 句级两层：整行都是英文散文就丢整行；中英混排的行只丢命中的句子。
    返回值可能是空串 —— 由调用方决定兜底（规则文案 / 翻译 / 报错）。
    """
    if not text or not has_english_prose(text):
        return text or ""

    kept_lines: list[str] = []
    in_code_fence = False
    for line in text.splitlines():
        # markdown 代码围栏内原样保留 —— 代码/SQL/命令行天然含 if/and/not/for，
        # 会被判成英文散文（实测 doc 语料里命中的全是这类），不能剪。
        if line.lstrip().startswith("```"):
            in_code_fence = not in_code_fence
            kept_lines.append(line)
            continue
        if in_code_fence:
            kept_lines.append(line)
            continue
        if not line.strip():
            kept_lines.append("")
            continue
        if not has_english_prose(line):
            kept_lines.append(line)
            continue
        kept = [s for s in _split_sentences(line) if not has_english_prose(s)]
        rebuilt = "".join(kept).strip()
        # 这一行本来就混了英文散文 —— 残料只在「含中文」或「含数字（是数据）」
        # 时才保留，纯英文碎片（'* **Input'、'Role:' 之类）一律丢掉。
        if rebuilt and (_CJK.search(rebuilt) or re.search(r"\d", rebuilt)):
            kept_lines.append(rebuilt)

    # 压掉因整行删除留下的连续空行
    out: list[str] = []
    for ln in kept_lines:
        if not ln.strip() and (not out or not out[-1].strip()):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def sanitize_llm_text(text: str) -> str:
    """面向用户文本的统一净化入口：剥思考前言 → 丢英文散文。

    返回 "" 表示整段都是英文散文、没有可用的中文内容，
    调用方必须兜底（规则文案 / 翻译 / 明确报错），**不要把原文透出去**。
    """
    if not text:
        return ""
    if not has_english_prose(text):
        return strip_thinking_preamble(text).strip()

    t = strip_english_prose(strip_thinking_preamble(text)).strip()
    # 剪完只剩零星残料（"【猎鹿人短评】"、"- Current Price: 7.97" 之类）
    # 就当作没生成 —— 半截内容比明确的兜底文案更让人困惑。
    if chinese_char_count(t) < _MIN_USABLE_CJK:
        return ""
    return t


def needs_translation(text: str) -> bool:
    """净化后是否仍然「没有可用中文」→ 需要走翻译兜底。"""
    if not text:
        return False
    cleaned = sanitize_llm_text(text)
    return not contains_chinese(cleaned)

# ── 结构化输出（JSON dict）的批量净化 ────────────────────────────────
# 这些键是结构标识（枚举、代码、URL）或外部原始数据（新闻标题/来源），
# 英文是合法的，绝不能净化 —— 净化只针对模型自己写的自然语言。
NO_SANITIZE_KEYS = frozenset({
    "type", "code", "market", "market_suffix", "symbol", "ticker", "name",
    "key", "workflow", "status", "impact", "decision", "rating", "trend",
    "level", "action", "id", "tool_id", "model", "unit", "icon", "emoji",
    "url", "link", "source", "title", "date", "time", "author", "label",
    "finish_reason", "raw_text", "error",
})


def sanitize_json_values(obj, key=None, *, on_hit=None):
    """递归净化 dict/list 里的自然语言字段（结构键与外部数据原样保留）。

    on_hit(key, raw) 在命中英文散文时回调，用于打日志/埋点。
    """
    if isinstance(obj, dict):
        return {k: sanitize_json_values(v, k, on_hit=on_hit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_json_values(v, key, on_hit=on_hit) for v in obj]
    if isinstance(obj, str):
        if key in NO_SANITIZE_KEYS or not has_english_prose(obj):
            return obj
        if on_hit:
            try:
                on_hit(key, obj)
            except Exception:  # noqa: BLE001
                pass
        return sanitize_llm_text(obj)
    return obj
