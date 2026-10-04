#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""五期「闭环补齐」整合报告生成器 —— 一份自包含 HTML（纯 Python，无第三方依赖）。

用户要求（五期方案 §7.2 / `plan/L10.md` §1.1）：

    把「开发方案 / 开发计划 / 开发完成后总结 / 开发部署 / 开发详细测试方案 / 测试后截图」
    整合成**一份** HTML 文件；**必须自包含**（不依赖网络、不依赖外部图片文件 —— 截图以
    base64 内嵌）；**用脚本生成，不许手工拼字符串**，脚本随仓库提交、**可重跑**。

本脚本就是那个脚本。六部分的来源：

    ① 开发方案           plan/00五期方案/AI炒股智能体系统_五期(闭环补齐)_开发方案与开发计划.md（整篇）
    ② 开发计划           同一份方案的 §五（阶段划分、顺序与依赖）+ §十（阶段一览表）
    ③ 开发完成后总结     docs/开发文档/L01-*.md … L09-*.md（每段：一句话 + 全文折叠）
    ④ 开发部署           docs/开发文档/L10-*.md 里标题含「发版 / 镜像 / 演示站 / 部署 / 验收」的小节
    ⑤ 开发详细测试方案   各段成果文档里标题含「测试」的小节（命令 / 期望 / 实测）
    ⑥ 测试后截图         docs/开发文档/L10-截图/ 下的图片（base64 内嵌）

重跑一致
--------
输出里**不含**生成时刻等随时间变化的量：日期由 `--date` 传入（默认取「今天（上海）」），
其余全部来自输入文件。**同一 `--date` + 同一输入 → 逐字节相同的输出**（已用 sha256 实测）。

用法::

    python3 scripts/gen_wuqi_report.py                 # 默认日期 = 今天（上海）
    python3 scripts/gen_wuqi_report.py --date 2026-10-05
    python3 scripts/gen_wuqi_report.py --plan-dir /path/to/plan/00五期方案

退出码：0 成功；非 0 = 输入缺失 / 渲染失败（**不产出半成品 HTML**）。
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import html as _html
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs" / "开发文档"
SHOT_DIR = DOCS / "L10-截图"
DEFAULT_PLAN_DIR = Path("/mnt/mixplode/hunter-dev/stock-ai-loop/plan/00五期方案")
PLAN_FILE = "AI炒股智能体系统_五期(闭环补齐)_开发方案与开发计划.md"

SEGMENTS = [
    ("L01", "提案闭环接通"),
    ("L02", "单一事实来源"),
    ("L03", "决策出身证"),
    ("L04", "自有策略服务"),
    ("L05", "账本补齐"),
    ("L06", "凭证分离与允许名单"),
    ("L07", "发布适配器与待核实"),
    ("L08", "控制通道与MCP"),
    ("L09", "采集补齐"),
]


# ════════════════════════════════════════════════════════════════════════
# 一 · 极简 Markdown → HTML（够用即可，本仓文档的写法都在支持范围内）
# ════════════════════════════════════════════════════════════════════════

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(text: str) -> str:
    """行内：先转义，再还原 `code` / **粗体** / 链接（外链保留 <a>，仓内路径变 <code>）。"""
    out = _html.escape(text, quote=False)

    def _code(m: re.Match) -> str:
        return f"<code>{m.group(1)}</code>"

    def _link(m: re.Match) -> str:
        label, href = m.group(1), m.group(2)
        if href.startswith(("http://", "https://")):
            return f'<a href="{_html.escape(href, quote=True)}">{label}</a>'
        return f"<code>{label}</code>"  # 仓内相对路径：不做出站链接

    # 转义后 ` 与 * 不变化；链接的 [] () 也不变化，故可直接在转义串上匹配。
    out = _INLINE_CODE.sub(_code, out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _LINK.sub(_link, out)
    return out


def md_to_html(md: str) -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    def _flush_para(buf: list[str]) -> None:
        if buf:
            out.append("<p>" + "<br>".join(_inline(x) for x in buf) + "</p>")
            buf.clear()

    para: list[str] = []
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 围栏代码块
        if stripped.startswith("```"):
            _flush_para(para)
            lang = stripped[3:].strip()
            i += 1
            body: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # 跳过收尾 ```
            cls = f' class="lang-{_html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{_html.escape(chr(10).join(body))}</code></pre>")
            continue

        # 水平线
        if stripped in ("---", "***", "___"):
            _flush_para(para)
            out.append("<hr>")
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            _flush_para(para)
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2).strip())}</h{lvl}>")
            i += 1
            continue

        # 表格：当前行以 | 开头且下一行是分隔行
        if stripped.startswith("|") and i + 1 < n and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            _flush_para(para)
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{_inline(c)}</th>" for c in header)
            trs = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows
            )
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>")
            continue

        # 引用块（连续 > 行合并成一块）
        if stripped.startswith(">"):
            _flush_para(para)
            body = []
            while i < n and lines[i].strip().startswith(">"):
                body.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + md_to_html("\n".join(body)) + "</blockquote>")
            continue

        # 列表（有序 / 无序），支持一层缩进
        if re.match(r"^\s*([-*+]|\d+\.)\s+", line) and stripped:
            _flush_para(para)
            ordered = bool(re.match(r"^\s*\d+\.\s+", line))
            items: list[str] = []
            while i < n and re.match(r"^\s*([-*+]|\d+\.)\s+\S", lines[i]):
                text = re.sub(r"^\s*([-*+]|\d+\.)\s+", "", lines[i])
                sub: list[str] = []
                i += 1
                while i < n and lines[i].startswith(("  ", "\t")) and lines[i].strip() and not re.match(
                    r"^\s*([-*+]|\d+\.)\s+\S", lines[i]
                ):
                    sub.append(lines[i].strip())
                    i += 1
                item = _inline(text)
                if sub:
                    item += "<br>" + "<br>".join(_inline(s) for s in sub)
                items.append(f"<li>{item}</li>")
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue

        # 空行
        if not stripped:
            _flush_para(para)
            i += 1
            continue

        para.append(line.rstrip())
        i += 1

    _flush_para(para)
    return "\n".join(out)


# ════════════════════════════════════════════════════════════════════════
# 二 · 从文档里切小节
# ════════════════════════════════════════════════════════════════════════

_H2 = re.compile(r"^##\s+(.*)$")


def split_h2(md: str) -> list[tuple[str, str]]:
    """把 markdown 按 `## ` 切成 (标题, 正文) —— 一级标题的引言归到第一节。"""
    parts: list[tuple[str, str]] = []
    cur_title: str | None = None
    cur: list[str] = []
    for line in md.split("\n"):
        m = _H2.match(line)
        if m:
            if cur_title is not None:
                parts.append((cur_title, "\n".join(cur).strip()))
            cur_title = m.group(1).strip()
            cur = []
        else:
            cur.append(line)
    if cur_title is not None:
        parts.append((cur_title, "\n".join(cur).strip()))
    return parts


def head_block(md: str) -> str:
    """标题下的引言块（到第一个 `---` 或第一个 `## ` 为止）—— 阶段代号 / 依据 / 完成时间。"""
    lines = md.split("\n")
    buf: list[str] = []
    started = False
    for line in lines:
        if line.startswith("# ") and not started:
            started = True
            continue
        if not started:
            continue
        if line.strip() == "---" or line.startswith("## "):
            break
        buf.append(line)
    return "\n".join(buf).strip()


def summary_block(md: str) -> str:
    """③ 用的「这一段解决了什么」：取第一节正文；没有 `## ` 就退回引言块。"""
    secs = split_h2(md)
    if secs:
        return secs[0][1]
    return head_block(md)


def pick_sections(md: str, keyword_re: str) -> list[tuple[str, str]]:
    return [(t, b) for t, b in split_h2(md) if re.search(keyword_re, t)]


# ════════════════════════════════════════════════════════════════════════
# 三 · 截图 → base64
# ════════════════════════════════════════════════════════════════════════

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def collect_shots(shot_dir: Path) -> list[dict]:
    if not shot_dir.is_dir():
        return []
    manifest: dict[str, str] = {}
    mf = shot_dir / "manifest.json"
    if mf.is_file():
        data = json.loads(mf.read_text(encoding="utf-8"))
        for item in data.get("shots", []):
            manifest[item["file"]] = item.get("caption", "")
    shots = []
    for p in sorted(shot_dir.iterdir()):
        if p.suffix.lower() not in _MIME:
            continue
        raw = p.read_bytes()
        shots.append(
            {
                "file": p.name,
                "caption": manifest.get(p.name, p.stem),
                "mime": _MIME[p.suffix.lower()],
                "b64": base64.b64encode(raw).decode("ascii"),
                "size": len(raw),
            }
        )
    return shots


# ════════════════════════════════════════════════════════════════════════
# 四 · 页面
# ════════════════════════════════════════════════════════════════════════

CSS = """
:root{--fg:#1a1d21;--muted:#5b6470;--line:#e3e6ea;--bg:#fff;--soft:#f6f8fa;--acc:#0b6bcb;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:16px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;}
.wrap{display:flex;max-width:1400px;margin:0 auto;align-items:flex-start}
nav{position:sticky;top:0;flex:0 0 240px;max-height:100vh;overflow:auto;padding:28px 14px 40px 22px;
 border-right:1px solid var(--line);font-size:14px}
nav b{display:block;margin:14px 0 6px;color:var(--muted);font-weight:600;letter-spacing:.04em}
nav a{display:block;color:var(--fg);text-decoration:none;padding:3px 0;opacity:.85}
nav a:hover{color:var(--acc);opacity:1}
main{flex:1 1 auto;min-width:0;padding:34px 42px 90px}
header.top{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:8px}
header.top h1{margin:0 0 6px;font-size:27px}
header.top .sub{color:var(--muted);font-size:14px}
h1{font-size:26px;margin:38px 0 14px;padding-top:8px}
h2{font-size:21px;margin:30px 0 12px;border-bottom:1px solid var(--line);padding-bottom:7px}
h3{font-size:17px;margin:22px 0 9px}
h4{font-size:15px;margin:18px 0 8px}
p{margin:9px 0}
blockquote{margin:12px 0;padding:9px 16px;background:var(--soft);border-left:4px solid #c8d1da;color:#333;border-radius:0 5px 5px 0}
blockquote p{margin:4px 0}
code{background:var(--soft);padding:1.5px 5px;border-radius:4px;font-family:"SF Mono",Menlo,Consolas,monospace;font-size:13px}
pre{background:#0e1116;color:#e6edf3;padding:14px 16px;border-radius:8px;overflow:auto;font-size:13px;line-height:1.6}
pre code{background:none;color:inherit;padding:0}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:14px;display:block;overflow:auto}
th,td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:600;white-space:nowrap}
hr{border:0;border-top:1px solid var(--line);margin:24px 0}
details{margin:14px 0;border:1px solid var(--line);border-radius:8px;padding:0 16px}
details>summary{cursor:pointer;padding:11px 0;font-weight:600;color:var(--acc);outline:none}
details[open]>summary{border-bottom:1px solid var(--line);margin-bottom:10px}
.seg{border:1px solid var(--line);border-radius:10px;padding:4px 20px 14px;margin:20px 0;background:#fff}
.pill{display:inline-block;background:var(--soft);border:1px solid var(--line);border-radius:20px;
 padding:1px 11px;font-size:12px;color:var(--muted);margin-left:8px;vertical-align:middle}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:20px}
figure{margin:0;border:1px solid var(--line);border-radius:10px;overflow:hidden}
figure img{display:block;width:100%;height:auto}
figcaption{padding:8px 12px;font-size:13px;color:var(--muted);background:var(--soft);border-top:1px solid var(--line)}
.note{background:#fff8e6;border:1px solid #f0d9a0;border-radius:8px;padding:11px 16px;font-size:14px;margin:14px 0}
.toc-top{columns:2;column-gap:30px;font-size:14px}
.toc-top a{color:var(--acc);text-decoration:none}
@media(max-width:900px){nav{display:none}main{padding:22px 18px 70px}}
"""


def _nav() -> str:
    return (
        "<nav><b>五期整合报告</b>"
        '<a href="#p1">① 开发方案</a><a href="#p2">② 开发计划</a>'
        '<a href="#p3">③ 开发完成后总结</a><a href="#p4">④ 开发部署</a>'
        '<a href="#p5">⑤ 开发详细测试方案</a><a href="#p6">⑥ 测试后截图</a>'
        "<b>分段（③）</b>"
        + "".join(f'<a href="#seg-{s}">{s}</a>' for s, _ in SEGMENTS)
        + "</nav>"
    )


def build(plan_dir: Path, date: str) -> str:
    plan_md_path = plan_dir / PLAN_FILE
    if not plan_md_path.is_file():
        raise SystemExit(f"找不到五期方案：{plan_md_path}\n用 --plan-dir 指定 plan/00五期方案 目录。")

    plan_md = plan_md_path.read_text(encoding="utf-8")
    plan_html = md_to_html(plan_md)

    # ② 开发计划：从方案里挑「阶段划分、顺序与依赖」+「阶段一览」
    plan_sections = split_h2(plan_md)
    plan_plan = [(t, b) for t, b in plan_sections if re.search(r"阶段划分|阶段一览", t)]
    plan_plan_html = "".join(f"<h3>{_inline(t)}</h3>{md_to_html(b)}" for t, b in plan_plan)

    # ③ + ⑤ 各段成果文档
    seg_blocks: list[str] = []
    test_blocks: list[str] = []
    for seg, name in SEGMENTS:
        f = DOCS / f"{seg}-{name}.md"
        if not f.is_file():
            raise SystemExit(f"缺少成果文档：{f}")
        md = f.read_text(encoding="utf-8")
        one = summary_block(md)
        seg_blocks.append(
            f'<div class="seg" id="seg-{seg}"><h3>{seg} · {name}</h3>'
            f'<blockquote>{md_to_html(head_block(md))}</blockquote>'
            f"<h4>这一段解决了什么（人话）</h4>{md_to_html(one)}"
            f'<details><summary>展开整段成果文档（{seg}-{name}.md）</summary>{md_to_html(md)}</details></div>'
        )
        for t, b in pick_sections(md, r"测试|用例"):
            test_blocks.append(f'<h3>{seg} · {_inline(t)}</h3>{md_to_html(b)}')

    # ④ 开发部署：从 L10 报告里挑部署 / 发版 / 验收相关小节
    l10 = sorted(DOCS.glob("L10-*.md"))
    if not l10:
        raise SystemExit("缺少 L10 成果文档（docs/开发文档/L10-*.md）—— ④ 开发部署 从它读。")
    l10_md = l10[0].read_text(encoding="utf-8")
    deploy_secs = pick_sections(l10_md, r"全量验收|发版|镜像|演示站|部署|回滚|遗留|守卫")
    deploy_html = "".join(f"<h3>{_inline(t)}</h3>{md_to_html(b)}" for t, b in deploy_secs)
    if not deploy_html:
        deploy_html = f"<p><em>（{l10[0].name} 里没有匹配到部署小节）</em></p>"

    # ⑥ 截图
    shots = collect_shots(SHOT_DIR)
    shots_html = (
        '<div class="grid">'
        + "".join(
            f'<figure><img alt="{_html.escape(s["caption"])}" src="data:{s["mime"]};base64,{s["b64"]}">'
            f'<figcaption>{_html.escape(s["caption"])} · {s["file"]} · {s["size"] // 1024} KB</figcaption></figure>'
            for s in shots
        )
        + "</div>"
        if shots
        else "<p><em>（尚无截图：把真浏览器截图放进 docs/开发文档/L10-截图/ 后重跑本脚本）</em></p>"
    )
    shots_total = sum(s["size"] for s in shots)

    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>智能炒股 · 五期「闭环补齐」整合报告</title>",
        f"<style>{CSS}</style>",
        "</head><body><div class='wrap'>",
        _nav(),
        "<main>",
        '<header class="top"><h1>智能炒股 · 五期「闭环补齐」整合报告</h1>'
        f'<div class="sub">报告日期 {_html.escape(date)} · 一份自包含 HTML（离线可读，截图已内嵌 base64）· '
        f"六部分：开发方案 / 开发计划 / 开发完成后总结 / 开发部署 / 开发详细测试方案 / 测试后截图</div></header>",
        f'<div class="toc-top"><a href="#p1">① 开发方案</a> · <a href="#p2">② 开发计划</a> · '
        f'<a href="#p3">③ 开发完成后总结</a> · <a href="#p4">④ 开发部署</a> · '
        f'<a href="#p5">⑤ 开发详细测试方案</a> · <a href="#p6">⑥ 测试后截图</a></div>',
        # ①
        '<h1 id="p1">① 开发方案</h1>'
        f'<p class="note">来源：<code>plan/00五期方案/{_html.escape(PLAN_FILE)}</code>（五期方案全文，未删改）。</p>',
        plan_html,
        # ②
        '<h1 id="p2">② 开发计划</h1>'
        '<p class="note">阶段顺序强制、不许跳段（红线 6）：每一段过了才开下一段。下表摘自五期方案。</p>',
        plan_plan_html,
        # ③
        '<h1 id="p3">③ 开发完成后总结</h1>'
        '<p class="note">每段为一段「一句话 + 整段成果文档（折叠）」。数字均为各段成果文档里的<b>实测</b>读数。</p>',
        "".join(seg_blocks),
        # ④
        '<h1 id="p4">④ 开发部署</h1>'
        f'<p class="note">来源：<code>{_html.escape(l10[0].name)}</code>（发版 / 镜像 / 演示站升级的真实读数）。</p>',
        deploy_html,
        # ⑤
        '<h1 id="p5">⑤ 开发详细测试方案</h1>'
        '<p class="note">各段成果文档里「测试 / 用例」小节的原文（命令 / 期望 / 实测数字）。</p>',
        "".join(test_blocks) if test_blocks else "<p><em>（没有提取到测试小节）</em></p>",
        # ⑥
        f'<h1 id="p6">⑥ 测试后截图</h1>'
        f'<p class="note">真浏览器（Playwright headless Chromium）截图，已 base64 内嵌（共 {len(shots)} 张 · '
        f"{shots_total // 1024} KB）—— 本文件离线打开也能看到图。</p>",
        shots_html,
        "</main></div></body></html>",
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gen_wuqi_report.py", description="生成五期整合报告 HTML")
    ap.add_argument("--plan-dir", default=str(DEFAULT_PLAN_DIR), help="plan/00五期方案 目录")
    ap.add_argument("--out", default=str(DOCS / "五期-闭环补齐-整合报告.html"))
    ap.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD（默认今天·上海）")
    args = ap.parse_args(argv)

    if args.date:
        date = args.date
    else:
        date = (_dt.datetime.utcnow() + _dt.timedelta(hours=8)).strftime("%Y-%m-%d")

    html_doc = build(Path(args.plan_dir), date)
    out = Path(args.out)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(html_doc, encoding="utf-8")
    os.replace(tmp, out)
    print(f"已生成 {out} · {len(html_doc.encode('utf-8'))} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
