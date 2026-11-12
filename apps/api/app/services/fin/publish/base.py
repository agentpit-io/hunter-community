"""智能炒股 · 发布适配器（L07）· 基类与判据。

**这是「发布」这件事的唯一抽象层**（`01方案 §9.1` / §9.2 / §10.1 `publish.submit/get`）。

一、为什么要有适配器
    改造前发布是**内嵌在生成流程里的一步**、渠道是**写死的字符串**（`report.py` 的
    `_store_receipt`）。现在把发布**拆成独立一步**：生成 → 校验 → **发布**（可单独
    重试 / 核实），渠道由**适配器**决定，回执统一落 `fin_publish_receipt`。

二、只做三个渠道（`plan/五期追加规则 §6.1`，**不许放大**）
    · `in_app`  —— 站内（把现成行为原样搬进来，**行为不变**，老用例逐条不变）；
    · `webhook` —— 通用 Webhook（POST 到可配置 URL，本机起假接收端即可测）；
    · `file`    —— 文件落盘（写到指定目录，记路径与校验和）。
    **不接**任何要注册 / 要付费 / 要审资质的第三方内容平台。

三、状态语义（`01方案 §11.2`）
    · `SUCCESS` —— 明确成功（拿到 2xx / 文件已落盘且校验和一致）；
    · `FAILED`  —— 明确失败（连接被拒 / 非 2xx / 落盘失败）—— **没发出去，可安全重试**；
    · `UNKNOWN` —— **结果不明**（超时 / 回执解析不了 / 连接中断）—— 进**待核实队列**，
      **不许盲目重发**（可能已经发出去了）。核实 = 查外部状态 → 落 `SUCCESS`/`FAILED`；
      查不出就**保留 `UNKNOWN`**。

四、判据（`submit` 前先看回执现状）
    · 已有 `UNKNOWN` 且**没带重发标记** → **拒绝**（`PublishBlocked`）；
    · 已有 `SUCCESS` 且没带重发标记 → **幂等返回**（不重复发布，`01方案 §2`「不盲目重复发布」）；
    · 已有 `FAILED` → 允许重试（明确没发出去）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

# 三个合法状态（与 `0023` 的 CHECK 一致）。
SUCCESS = "SUCCESS"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"
STATUSES = (SUCCESS, FAILED, UNKNOWN)

# 三个渠道（`§6.1` 的边界）。新增渠道 = 加一个适配器 + 在这里登记，**不加基础设施**。
CHANNELS = ("in_app", "webhook", "file")


def receipt_id_for(report_id: str, channel: str) -> str:
    """回执主键 = `报告 + 渠道` 的稳定推导。

    **同一份报告同一个渠道只有一行回执**（重复提交就地更新，不制造第二份）。
    `in_app` 的算法与改造前**逐字节一致**（`report.py` 老实现是
    `rcp_<sha256(f"{report_id}:in_app")[:24]>`）—— 老用例与老数据都不动。
    """
    return f"rcp_{hashlib.sha256(f'{report_id}:{channel}'.encode()).hexdigest()[:24]}"


@dataclass
class PublishResult:
    """一次发布尝试的结果（适配器出参：**状态 + 回执**）。"""

    status: str                    # SUCCESS / FAILED / UNKNOWN
    external_id: Optional[str] = None   # 渠道侧标识（产物引用 / 落盘路径 / 对方回执号）
    detail: str = ""                    # 人话说明（写进回执 detail）
    target: Optional[str] = None        # 发到哪（URL / 路径）—— 待核实回查用

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"非法发布状态：{self.status!r}（只许 {STATUSES}）")


class PublishBlocked(RuntimeError):
    """发布被拒（`UNKNOWN` 下未带重发标记 / 报告未通过校验 / 渠道不存在）。

    `reason` 是给用户看的原文（路由把它翻成 409/400）。
    """

    def __init__(self, reason: str, *, code: str = "publish_blocked") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@runtime_checkable
class Adapter(Protocol):
    """发布适配器接口（**入参：报告 + 目标；出参：状态 + 回执**，三个渠道统一）。"""

    channel: str

    def submit(self, report: dict, target: Optional[str], *,
               html: Optional[str] = None, ctx: Optional[dict] = None) -> PublishResult:
        """把报告发到目标。**必须**用 `PublishResult` 三态之一返回，不许抛未分类异常。"""
        ...

    def probe(self, receipt: dict) -> Optional[str]:
        """核实：查外部状态。

        返回 `SUCCESS` / `FAILED` = **已查清**；返回 `None` = **查不出，仍按 UNKNOWN 保留**
        （`§11.2`：无法确认时保留 `UNKNOWN`）。`in_app` 与没有 `status_url` 的 webhook 走 `None`。
        """
        ...


def parse_target(target) -> dict:
    """把 target 归一成 dict。

    允许三种写法（回执 `target` 列存的是 `target_str()` 的结果 —— **规格字符串**，
    核实时靠它还原出 `status_url`）：
      · 字符串：`"https://..."` 或 `"/var/reports"`；
      · JSON 字符串（回执表里读回来的那种）：`'{"url": "...", "status_url": "..."}'`；
      · dict：`{"url": "...", "status_url": "...", "headers": {...}}`（webhook 用 `status_url` 回查）。
    """
    if target is None:
        return {}
    if isinstance(target, dict):
        return dict(target)
    s = str(target)
    if s.startswith("{"):
        import json
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    return {"url": s}


def target_str(target) -> Optional[str]:
    """回执 `target` 列的**唯一写入口**：dict → JSON 字符串，字符串原样，None → None。

    存规格字符串（而不是只存 URL）是为了 `probe` 能还原 `status_url` ——
    「结果不明」时得知道**去哪回查**。
    """
    if target is None:
        return None
    if isinstance(target, dict):
        import json
        return json.dumps(target, ensure_ascii=False, sort_keys=True)
    return str(target)
