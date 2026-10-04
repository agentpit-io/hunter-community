"""文件落盘渠道 `file` —— 把报告 HTML 写到指定目录，记**路径与校验和**。

用途：把报告归档到共享目录 / 交给别的程序去搬。目标（`target`）：
  · 目录（`"…/reports"`，不以 `.html` 结尾）→ 文件名按报告 id 稳定生成 `<report_id>.html`；
  · 具体文件（以 `.html` / `.htm` 结尾）→ 直接用该路径。

落盘是本地的，正常**不存在「结果不明」**：写成功 = `SUCCESS`（`external_id` 是绝对路径，
`detail` 里带 `sha256=`），写失败（权限 / 磁盘）= `FAILED`。

`probe`（核实）重算磁盘上文件的 sha256 与回执里记的比对：一致 → `SUCCESS`；
文件没了 / 内容变了 → `FAILED`。核实用得上 —— 若将来落了网络盘、写完回执前进程挂了，
回执可能是 UNKNOWN，这时重算校验和就能落定。
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Optional

from .base import FAILED, SUCCESS, PublishResult, parse_target

_SHA_RE = re.compile(r"sha256=([0-9a-f]{64})")


def _dest(target_spec: dict, report: dict) -> Optional[str]:
    path = target_spec.get("dir") or target_spec.get("url") or target_spec.get("path")
    if not path:
        return None
    if path.endswith(".html") or path.endswith(".htm"):
        return os.path.abspath(path)
    return os.path.abspath(os.path.join(path, f"{report.get('report_id') or 'report'}.html"))


class FileAdapter:
    channel = "file"

    def submit(self, report: dict, target: Optional[str], *,
               html: Optional[str] = None, ctx: Optional[dict] = None) -> PublishResult:
        dest = _dest(parse_target(target), report)
        if not dest:
            return PublishResult(FAILED, None, "file 未配置目标目录，未落盘")
        if html is None:
            return PublishResult(FAILED, None, "没有可落盘的内容（报告无产物）", target=dest)

        data = html.encode("utf-8")
        checksum = hashlib.sha256(data).hexdigest()
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            return PublishResult(FAILED, None, f"落盘失败：{type(exc).__name__}: {str(exc)[:120]}",
                                 target=dest)
        return PublishResult(SUCCESS, dest, f"已落盘 · sha256={checksum}", target=dest)

    def probe(self, receipt: dict) -> Optional[str]:
        dest = receipt.get("external_id") or _dest(parse_target(receipt.get("target")),
                                                   {"report_id": receipt.get("report_id")})
        if not dest:
            return None
        m = _SHA_RE.search(receipt.get("detail") or "")
        if not m:
            return None      # 没记校验和 → 查不实，保留原状
        if not os.path.exists(dest):
            return FAILED
        try:
            with open(dest, "rb") as fh:
                actual = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            return None
        return SUCCESS if actual == m.group(1) else FAILED
