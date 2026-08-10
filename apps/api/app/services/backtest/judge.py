"""回测判定逻辑 —— 唯一真相源。

为什么单独抽出来:
    "命中/未命中"取决于判定参数(平盘带、误差阈值…),而个人版每个用户参数不同。
    同一条预测,A 用户算命中、B 用户算未命中都是对的。所以这些结论**不能提前存死**,
    必须在查询时按各人参数现场算。

    本模块是纯函数,无 IO、无状态,被三处调用:
      ① jobs.backtest_job      —— 每日入库时用全局参数算一遍(存 pred_backtest.dir_hit)
      ② user_store.*           —— 用户查看时用他自己的参数现场算
      ③ routers.backtest 校验  —— 对照 ①存的 与 ②算的 是否一致(参数相同时必须相同)

    ①保留存储是刻意的:PC admin 看板继续读存好的布尔值,行为零变化、零回归风险;
    ③的对照校验用来证明"搬家没搬丢东西"。

参数默认值与 config.DEFAULTS 保持一致,传 None 时退回默认,保证行为不变。
"""

# 与 config.DEFAULTS / jobs.py 模块常量一致的兜底值
D_FLAT_BAND = 0.5
D_REL_ERR_PCT = 20.0
D_ABS_ERR_PP = 1.5
D_REVERSAL_MIN = 1.0
D_STRENGTH_DELTA = 1.5


def _f(cfg: dict | None, key: str, default: float) -> float:
    if not cfg:
        return default
    v = cfg.get(key)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── 事后准确性判定 ──────────────────────────────────────────

def errors(pred_chg: float, real_chg: float) -> tuple[float, float]:
    """绝对误差(个百分点) 与 相对误差(%)。纯客观量,与参数无关,可安全落库。

    相对误差在实际涨跌≈0 时会爆炸,故封顶 999.9;
    若预测也≈0(两边都没动)则视为完全准确 0.0。
    """
    abs_err = abs(pred_chg - real_chg)
    if abs(real_chg) > 1e-6:
        rel_err = abs_err / abs(real_chg) * 100
    else:
        rel_err = 0.0 if abs_err < 1e-6 else 999.9
    return abs_err, rel_err


def dir_hit(pred_chg: float, real_chg: float, cfg: dict | None = None) -> bool:
    """方向是否命中。双方都落在平盘带内 → 都判"看平",也算命中。"""
    flat = _f(cfg, "flat_band", D_FLAT_BAND)
    both_flat = abs(pred_chg) < flat and abs(real_chg) < flat
    return both_flat or (pred_chg * real_chg > 0)


def amt_hit(abs_err: float, rel_err: float, cfg: dict | None = None) -> bool:
    """幅度是否命中。双阈值满足任一即算命中。

    单用相对误差在小涨跌时会失真:预测 0.5% 实际 0.1%,相对误差 400%,
    但实际只差 0.4 个百分点,不该判错 —— 绝对阈值就是这个兜底。
    """
    rel_thr = _f(cfg, "rel_err_pct", D_REL_ERR_PCT)
    abs_thr = _f(cfg, "abs_err_pp", D_ABS_ERR_PP)
    return (rel_err <= rel_thr) or (abs_err <= abs_thr)


def judge_row(pred_chg: float, real_chg: float, cfg: dict | None = None) -> dict:
    """一条到期预测的完整判定,返回客观量 + 按 cfg 算出的两个命中结论。"""
    ae, re_ = errors(pred_chg, real_chg)
    return {
        "abs_error": ae,
        "rel_error": min(re_, 999.9),
        "dir_hit": dir_hit(pred_chg, real_chg, cfg),
        "amt_hit": amt_hit(ae, re_, cfg),
    }


# ── 重叠一致性判定 ──────────────────────────────────────────

def verdict(prev: float, curr: float, cfg: dict | None = None) -> str:
    """相邻两次预测的关系: consistent / reversal / strengthen / weaken。

    判定顺序有讲究:
      1. 两次都在平盘带内 → 一致(±0.2% 变 ∓0.3% 不算改口)
      2. 方向相反 且 至少一侧幅度够大 → 反转(防 +0.1%→−0.1% 这种噪音)
      3. 幅度变化超过阈值 → 强化/弱化
    """
    flat = _f(cfg, "flat_band", D_FLAT_BAND)
    rev_min = _f(cfg, "reversal_min", D_REVERSAL_MIN)
    delta = _f(cfg, "strength_delta", D_STRENGTH_DELTA)

    if abs(prev) < flat and abs(curr) < flat:
        return "consistent"
    if prev * curr < 0 and max(abs(prev), abs(curr)) >= rev_min:
        return "reversal"
    if abs(curr - prev) >= delta:
        return "strengthen" if abs(curr) > abs(prev) else "weaken"
    return "consistent"
