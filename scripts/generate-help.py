"""Generate the deployable help page from doc/产品说明书.md.

Requires Python-Markdown: pip install Markdown
Run from the repository root: python scripts/generate-help.py
"""

from html import escape
from pathlib import Path
from urllib.parse import quote
import re

import markdown


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "doc" / "产品说明书.md"
OUTPUT = ROOT / "apps" / "web" / "public" / "help" / "index.html"


def github_links(text: str) -> str:
    """Make repository-relative links useful from the deployed static page."""
    base = "https://github.com/agentpit-io/hunter-community/blob/main/"
    return re.sub(
        r'href="\.\./([^"]+)"',
        lambda m: f'href="{base}{quote(m.group(1), safe="/")}"',
        text,
    )


def main() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    md = markdown.Markdown(extensions=["extra", "toc", "sane_lists"], extension_configs={"toc": {"permalink": False}})
    content = github_links(md.convert(source))
    headings = re.findall(r'<h2 id="([^"]+)">([^<]+)</h2>', content)
    toc = "\n".join(
        f'<a href="#{escape(anchor)}">{escape(label)}</a>' for anchor, label in headings
    )
    page = f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="HunterCode Community 产品说明书、日常投研工作流与量化扩展方案">
  <title>帮助中心 · HunterCode Community</title>
  <style>
    :root {{ color-scheme: light; --ink:#211c18; --muted:#766b60; --line:#ded2bf; --paper:#fffdfa; --bg:#f7f3ec; --copper:#ad6832; }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; color:var(--ink); background:var(--bg); font:15px/1.8 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif; }}
    a {{ color:#96551f; text-decoration:none; }} a:hover {{ text-decoration:underline; }}
    .top {{ position:sticky; top:0; z-index:10; display:flex; gap:24px; align-items:center; padding:10px max(24px,calc((100vw - 1340px)/2)); background:rgba(255,253,250,.94); border-bottom:1px solid var(--line); backdrop-filter:blur(12px); }}
    .brand {{ display:flex; align-items:center; gap:9px; margin-right:auto; color:var(--ink); font-weight:700; white-space:nowrap; }}
    .brand img {{ width:24px; height:24px; border-radius:50%; }} .top nav {{ display:flex; gap:22px; }}
    .wrap {{ display:grid; grid-template-columns:230px minmax(0,1fr); gap:36px; max-width:1340px; margin:auto; padding:36px 24px 90px; }}
    aside {{ align-self:start; position:sticky; top:80px; max-height:calc(100vh - 100px); overflow:auto; padding:18px 15px; background:var(--paper); border:1px solid var(--line); border-radius:14px; }}
    aside strong {{ display:block; margin:0 8px 10px; }} aside a {{ display:block; padding:6px 8px; border-radius:7px; color:var(--muted); line-height:1.4; }} aside a:hover {{ background:#f3e9dc; text-decoration:none; color:var(--ink); }}
    main {{ min-width:0; padding:32px clamp(20px,5vw,76px) 70px; background:var(--paper); border:1px solid var(--line); border-radius:18px; box-shadow:0 8px 30px rgba(59,35,15,.04); }}
    h1,h2,h3 {{ line-height:1.35; letter-spacing:-.02em; scroll-margin-top:72px; }} h1 {{ font-size:clamp(28px,3vw,40px); margin:0 0 12px; }} h2 {{ font-size:24px; margin:46px 0 16px; padding-top:9px; border-top:1px solid var(--line); }} h3 {{ font-size:18px; margin:28px 0 8px; }}
    p,li {{ max-width:85ch; }} blockquote {{ margin:18px 0; padding:12px 18px; border-left:3px solid var(--copper); background:#f5ede3; color:#704c31; border-radius:0 8px 8px 0; }} blockquote p {{ margin:0; }}
    table {{ width:100%; border-collapse:collapse; margin:16px 0 24px; font-size:14px; }} th,td {{ border-bottom:1px solid var(--line); padding:10px 12px; text-align:left; vertical-align:top; }} th {{ background:#f3ebe0; }}
    code {{ padding:2px 4px; background:#f3eee7; border-radius:4px; font:13px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace; }} pre {{ overflow:auto; padding:18px; border-radius:10px; background:#2a2521; color:#f7f1e8; }} pre code {{ padding:0; background:none; color:inherit; }}
    .meta {{ color:var(--muted); font-size:13px; }}
    @media(max-width:900px) {{ .wrap {{ display:block; padding:14px 12px 60px; }} aside {{ position:static; max-height:none; margin-bottom:14px; }} main {{ padding:24px 20px 40px; }} .top {{ padding:10px 16px; }} .top nav {{ gap:12px; font-size:13px; }} table {{ display:block; overflow-x:auto; }} }}
    @media print {{ .top,aside {{ display:none; }} .wrap {{ display:block; padding:0; }} main {{ border:0; box-shadow:none; }} }}
  </style>
</head>
<body>
  <header class="top"><a class="brand" href="/chat"><img src="/logo-hunter.png" alt="">猎鹿人 · Hunter</a><nav><a href="/chat">返回对话</a><a href="/strategies/index.html">策略中心</a><a href="https://github.com/agentpit-io/hunter-community/blob/main/doc/%E4%BA%A7%E5%93%81%E8%AF%B4%E6%98%8E%E4%B9%A6.md">Markdown 原文</a></nav></header>
  <div class="wrap"><aside aria-label="本页目录"><strong>帮助中心</strong>{toc}</aside><main>{content}</main></div>
</body></html>'''
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(page, encoding="utf-8")
    print(f"Generated {OUTPUT.relative_to(ROOT)} from {SOURCE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
