"""Generate the deployable help page from doc/产品说明书.md.

Requires Python-Markdown: pip install Markdown
Run from the repository root: python3 scripts/generate-help.py
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


def decorate_app_links(html: str) -> str:
    """Turn relative app links (/workbench, /chat, /settings, /strategies, /finance, /library) into smart environment-aware app-link buttons/cards."""
    # 1. Convert "直达功能入口：..." list items into prominent launch bars
    def launch_bar_repl(match: re.Match) -> str:
        inner = match.group(1)
        inner = re.sub(
            r'<a\s+href="(/[^"]+)"[^>]*>(.*?)</a>',
            r'<a class="direct-launch-btn app-link" href="#" data-path="\1" data-route="\1" target="_blank" rel="noopener">\2 ↗</a>',
            inner,
        )
        inner = inner.replace(" · ", " ")
        return (
            '<li class="direct-launch-item">'
            '<div class="direct-launch-card">'
            '<span class="direct-launch-label">🎯 本节功能新窗口直达：</span>'
            f'<div class="direct-launch-actions">{inner}</div>'
            '</div>'
            '</li>'
        )

    html = re.sub(
        r'<li><strong>直达功能入口</strong>：\s*(.*?)</li>',
        launch_bar_repl,
        html,
        flags=re.DOTALL,
    )

    # 2. Ensure all remaining internal app route links open in a new tab with smart environment routing
    def inline_app_link_repl(match: re.Match) -> str:
        href = match.group(1)
        label = match.group(2)
        if href.startswith("/help"):
            return match.group(0)
        suffix = "" if "↗" in label else " ↗"
        return f'<a class="inline-app-link app-link" href="#" data-path="{href}" data-route="{href}" target="_blank" rel="noopener">{label}{suffix}</a>'

    html = re.sub(
        r'<a\s+href="(/(?:workbench|chat|settings|strategies|finance|library)[^"]*)"\s*>(.*?)</a>',
        inline_app_link_repl,
        html,
    )
    return html


def inject_env_choice_card(html: str) -> str:
    """Insert the dual-column Environment Entry Choice Card right below the Chapter 1 H2 heading."""
    card_html = (
        '<div class="env-choice-section" id="env-choice-guide">'
        '<div class="env-choice-header">'
        '<span class="env-choice-badge">🧭 环境入口选择说明卡片 · 双通道直达（100% 防 404）</span>'
        '<span class="env-choice-status" id="current-env-indicator">正在智能检测当前访问环境...</span>'
        '</div>'
        '<p class="env-choice-intro">'
        '本帮助中心同时服务于<strong>个人电脑本地私有部署实例</strong>与<strong>官方公网在线演示站</strong>。'
        '页面内置客户端智能环境感知引擎：当您在本地实例打开本页时，全页所有功能按钮自动跳往您的本地环境；'
        '当您在 AgentPit 官网（<code>www.agentpit.io</code>）浏览本说明书时，点击任意功能入口将<strong>智能优先打开官方免安装在线演示站</strong>，并在右下角提供一键切换本地 <code>localhost:3100</code> 通道！'
        '</p>'
        '<div class="env-choice-grid">'
        '  <div class="env-card env-card-local">'
        '    <div class="env-card-head">'
        '      <span class="env-card-icon">🖥️</span>'
        '      <div>'
        '        <h4>本地部署环境（个人私有数据不出盘）</h4>'
        '        <span class="env-tag env-tag-green">100% 本地隐私 · 零实盘风险</span>'
        '      </div>'
        '    </div>'
        '    <p class="env-card-desc">适合已在自己电脑（Mac / Windows / Linux）通过一键脚本或 Docker 启动 HunterCode 的股民。您的 API Key、自选股、持仓交割单与复盘记忆全部保存在本机硬盘。</p>'
        '    <div class="env-url-box"><code>http://localhost:3100/workbench</code></div>'
        '    <div class="env-card-actions">'
        '      <a class="env-btn env-btn-local" href="http://localhost:3100/workbench" target="_blank" rel="noopener">🖥️ 一键打开本地投研工作台 ↗</a>'
        '      <a class="env-btn env-btn-sub" href="http://localhost:3100/chat" target="_blank" rel="noopener">💬 本地 AI 对话 (:3100/chat) ↗</a>'
        '    </div>'
        '  </div>'
        '  <div class="env-card env-card-demo">'
        '    <div class="env-card-head">'
        '      <span class="env-card-icon">🚀</span>'
        '      <div>'
        '        <h4>官方免安装在线演示站（公网即点即用）</h4>'
        '        <span class="env-tag env-tag-copper">免安装体验 · 实盘行情直连</span>'
        '      </div>'
        '    </div>'
        '    <p class="env-card-desc">适合正在浏览官网说明书、尚未在本地安装，或希望直接在浏览器体验 08:30 晨报、56+90 项个体检、ETF 定投计算与 AI 自动模拟盘的用户。</p>'
        '    <div class="env-url-box"><code>https://hunter-community.agentpit.io/workbench</code></div>'
        '    <div class="env-card-actions">'
        '      <a class="env-btn env-btn-demo" href="https://hunter-community.agentpit.io/workbench" target="_blank" rel="noopener">🚀 一键打开在线演示站工作台 ↗</a>'
        '      <a class="env-btn env-btn-sub" href="https://hunter-community.agentpit.io/chat" target="_blank" rel="noopener">💬 在线演示 AI 对话 ↗</a>'
        '    </div>'
        '  </div>'
        '</div>'
        '</div>'
    )
    return re.sub(
        r'(<h2 id="[^"]*">第 1 章[^<]*</h2>)',
        r'\1\n' + card_html,
        html,
        count=1,
    )


def normalize_markdown_lists(md_text: str) -> str:
    """Normalize 2-space indented fenced code blocks and sublists inside bullet items for Python-Markdown."""
    lines = md_text.splitlines()
    out: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                out.append("")
            else:
                in_fence = False
                out.append("")
            continue
        if in_fence:
            out.append("        " + stripped)
            continue
        if line.startswith("  - "):
            out.append("    - " + line[4:])
        elif line.startswith("  ") and not line.startswith("    ") and stripped:
            out.append("    " + stripped)
        else:
            out.append(line)
    return "\n".join(out)


def wrap_screenshot_images(html: str) -> str:
    """Wrap <img src="/help/images/..." alt="..."> into interactive figure cards with lightbox trigger."""
    pattern = re.compile(
        r'(?:<p>\s*)?<img\s+alt="([^"]*)"\s+src="(/help/images/[^"]+)"\s*/?>(?:\s*</p>)?'
        r'|(?:<p>\s*)?<img\s+src="(/help/images/[^"]+)"\s+alt="([^"]*)"\s*/?>(?:\s*</p>)?'
    )

    def replacer(match: re.Match) -> str:
        if match.group(2):
            alt_text = match.group(1)
            src_url = match.group(2)
        else:
            src_url = match.group(3)
            alt_text = match.group(4)
        return (
            f'<figure class="shot-card" onclick="openLightbox(\'{src_url}\', \'{alt_text}\')">'
            f'<div class="shot-bar">'
            f'<span class="shot-dots"><i></i><i></i><i></i></span>'
            f'<span class="shot-title">{alt_text}</span>'
            f'<span class="shot-zoom">🔍 点击图片放大全屏查看</span>'
            f'</div>'
            f'<div class="shot-img-wrap">'
            f'<img src="{src_url}" alt="{alt_text}" loading="lazy" />'
            f'</div>'
            f'</figure>'
        )

    return pattern.sub(replacer, html)


def wrap_code_prompts(html: str) -> str:
    """Wrap <pre><code...>...</code></pre> with a header bar containing a one-click copy button."""
    pattern = re.compile(r'<pre><code(?:\s+class="[^"]*")?>(.*?)</code></pre>', re.DOTALL)

    def replacer(match: re.Match) -> str:
        code_inner = match.group(1).strip()
        return (
            '<div class="prompt-box">'
            '<div class="prompt-bar">'
            '<span class="prompt-tag">💬 对话框提问口令（可直接粘贴到 <a class="app-link" href="#" data-path="/chat" data-route="/chat" target="_blank" rel="noopener" style="color:#ffbe7b;text-decoration:underline;">/chat ↗</a>）</span>'
            '<div style="display:flex;gap:8px;align-items:center;">'
            '<button type="button" class="copy-btn" onclick="copyPrompt(this)">📋 一键复制提问口令</button>'
            '<a class="copy-btn app-link" href="#" data-path="/chat" data-route="/chat" target="_blank" rel="noopener" style="text-decoration:none;">🚀 打开 /chat 提问 ↗</a>'
            '</div>'
            '</div>'
            f'<pre><code>{code_inner}</code></pre>'
            '</div>'
        )

    return pattern.sub(replacer, html)


def extract_toc_items(html: str) -> str:
    """Extract H2 and H3 headings in document order to build a multi-level sidebar TOC."""
    pattern = re.compile(r'<(h[23])\s+id="([^"]+)">([^<]+)</\1>')
    items = []
    for level, anchor, label in pattern.findall(html):
        cls = "toc-h2" if level == "h2" else "toc-h3"
        items.append(
            f'<a class="toc-link {cls}" data-target="{escape(anchor)}" href="#{escape(anchor)}">{escape(label)}</a>'
        )
    return "\n".join(items)


def find_step_anchors(html: str) -> dict[str, str]:
    """Locate actual heading anchors generated by Python-Markdown for the 6-step flow cards."""
    h2_list = re.findall(r'<h2 id="([^"]+)">([^<]+)</h2>', html)
    anchors = {
        "step0": "1-3",
        "step1": "2-0830-0925-3",
        "step2": "3-0930-1500-pdf-56-90",
        "step3": "4-1530",
        "step4": "5-etf-ai",
        "step5": "5-etf-ai",
    }
    for anchor, title in h2_list:
        if "第 1 章" in title:
            anchors["step0"] = anchor
        elif "第 2 章" in title:
            anchors["step1"] = anchor
        elif "第 3 章" in title:
            anchors["step2"] = anchor
        elif "第 4 章" in title:
            anchors["step3"] = anchor
        elif "第 5 章" in title:
            anchors["step4"] = anchor
            anchors["step5"] = anchor
    h3_list = re.findall(r'<h3 id="([^"]+)">([^<]+)</h3>', html)
    for anchor, title in h3_list:
        if "5.1" in title:
            anchors["step4"] = anchor
        elif "5.2" in title:
            anchors["step5"] = anchor
    return anchors


def main() -> None:
    source = normalize_markdown_lists(SOURCE.read_text(encoding="utf-8"))
    md = markdown.Markdown(
        extensions=["extra", "toc", "sane_lists"],
        extension_configs={"toc": {"permalink": False}},
    )
    raw_html = github_links(md.convert(source))
    toc = extract_toc_items(raw_html)
    anchors = find_step_anchors(raw_html)
    content = inject_env_choice_card(decorate_app_links(wrap_code_prompts(wrap_screenshot_images(raw_html))))

    page = f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="猎鹿人 HunterCode (v3.5.2) 产品操作说明 · 股民日常炒股全流程图文指南">
  <title>帮助中心 (v3.5.2) · 猎鹿人 HunterCode 股民日常炒股图文指南</title>
  <link rel="icon" href="/icon.png" type="image/png" />
  <link rel="apple-touch-icon" href="/icon.png" />
  <style>
    :root {{
      color-scheme: light;
      --ink: #1e1915;
      --muted: #6e6257;
      --line: #dfd3c0;
      --paper: #fffdfa;
      --bg: #f6f1e8;
      --copper: #b56527;
      --copper-dark: #8c4814;
      --copper-soft: #f8efe4;
      --green: #16794b;
      --green-bg: #e7f6ee;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--bg);
      font: 15.5px/1.8 -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    }}
    a {{ color: var(--copper-dark); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}

    /* Top Sticky Navigation */
    .top {{
      position: sticky;
      top: 0;
      z-index: 40;
      display: flex;
      gap: 20px;
      align-items: center;
      padding: 12px max(24px, calc((100vw - 1440px) / 2));
      background: rgba(255, 253, 250, 0.95);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(14px);
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-right: auto;
      color: var(--ink);
      font-weight: 800;
      font-size: 16px;
      white-space: nowrap;
      text-decoration: none !important;
    }}
    .brand img {{ width: 28px; height: 28px; border-radius: 50%; border: 1px solid var(--line); }}
    .brand-badge {{
      font-size: 12px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--copper-soft);
      color: var(--copper-dark);
      border: 1px solid #e6cdb2;
    }}
    .top nav {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .top nav a {{
      padding: 6px 13px;
      border-radius: 8px;
      font-size: 14px;
      font-weight: 600;
      color: #4b3f35;
      transition: all 0.15s ease;
    }}
    .top nav a:hover {{
      background: var(--copper-soft);
      color: var(--copper-dark);
      text-decoration: none;
    }}
    .top nav a.nav-primary {{
      background: var(--copper);
      color: #fff;
    }}
    .top nav a.nav-primary:hover {{
      background: var(--copper-dark);
      color: #fff;
    }}

    /* Hero Banner + 6-Step Visual Flow Grid */
    .hero-banner {{
      max-width: 1440px;
      margin: 26px auto 0;
      padding: 0 24px;
    }}
    .hero-card {{
      background: linear-gradient(135deg, #241d18 0%, #3a2b20 55%, #593a20 100%);
      color: #fdf8f2;
      border-radius: 20px;
      padding: 32px 36px 30px;
      box-shadow: 0 14px 38px rgba(36, 25, 16, 0.14);
      border: 1px solid rgba(255, 255, 255, 0.1);
    }}
    .hero-top {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: flex-start;
      gap: 20px;
      margin-bottom: 24px;
    }}
    .hero-title-group h1 {{
      margin: 8px 0 10px;
      font-size: clamp(24px, 2.6vw, 34px);
      line-height: 1.3;
      color: #fff;
      letter-spacing: -0.02em;
    }}
    .hero-eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 4px 12px;
      border-radius: 999px;
      background: rgba(245, 166, 91, 0.18);
      border: 1px solid rgba(245, 166, 91, 0.4);
      color: #f9c997;
      font-size: 13px;
      font-weight: 600;
    }}
    .hero-sub {{
      margin: 0;
      max-width: 78ch;
      color: #dfd3c3;
      font-size: 14.5px;
      line-height: 1.7;
    }}
    .hero-pills {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .hero-pill {{
      padding: 7px 14px;
      border-radius: 10px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.14);
      font-size: 13px;
      color: #f7ede2;
      white-space: nowrap;
    }}
    .hero-pill strong {{ color: #ffbe7b; }}

    .flow-section-title {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin: 0 0 12px;
      font-size: 14px;
      font-weight: 700;
      color: #f6cca2;
      letter-spacing: 0.02em;
    }}
    .flow-grid {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
    }}
    .flow-step {{
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      padding: 14px 15px;
      border-radius: 14px;
      background: rgba(255, 253, 250, 0.09);
      border: 1px solid rgba(255, 255, 255, 0.15);
      color: #fff;
      text-decoration: none !important;
      transition: transform 0.16s ease, background 0.16s ease, border-color 0.16s ease;
    }}
    .flow-step:hover {{
      transform: translateY(-3px);
      background: rgba(255, 253, 250, 0.16);
      border-color: #f3a963;
    }}
    .flow-step-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
      margin-bottom: 6px;
    }}
    .flow-badge {{
      font-size: 11.5px;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 6px;
      background: #d97930;
      color: #fff;
    }}
    .flow-time {{
      font-size: 11.5px;
      color: #e6cbb1;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    .flow-step h3 {{
      margin: 4px 0 6px;
      font-size: 15px;
      color: #fff;
      line-height: 1.35;
    }}
    .flow-step p {{
      margin: 0 0 10px;
      font-size: 12.5px;
      line-height: 1.5;
      color: #dfd2c4;
    }}
    .flow-actions {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-top: auto;
    }}
    .flow-go {{
      font-size: 12px;
      font-weight: 600;
      color: #ffbe7b;
      text-decoration: none !important;
    }}
    .flow-go:hover {{
      color: #fff;
      text-decoration: underline !important;
    }}
    .flow-launch {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 5px 9px;
      border-radius: 8px;
      background: rgba(217, 121, 48, 0.32);
      border: 1px solid rgba(255, 190, 123, 0.5);
      color: #fff !important;
      font-size: 11.8px;
      font-weight: 700;
      text-decoration: none !important;
      transition: all 0.15s ease;
    }}
    .flow-launch:hover {{
      background: #d97930;
      border-color: #ffd7b0;
    }}

    /* Environment Entry Choice Card (Chapter 1) */
    .env-choice-section {{
      margin: 18px 0 28px;
      padding: 20px 22px;
      border-radius: 16px;
      background: linear-gradient(135deg, #fdf8f0 0%, #f7ecde 100%);
      border: 2px solid #dfbe98;
      box-shadow: 0 8px 24px rgba(140, 72, 20, 0.08);
    }}
    .env-choice-header {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }}
    .env-choice-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 12px;
      border-radius: 999px;
      background: var(--copper-dark);
      color: #fff;
      font-size: 13px;
      font-weight: 700;
    }}
    .env-choice-status {{
      font-size: 12.5px;
      font-weight: 700;
      padding: 4px 12px;
      border-radius: 999px;
      background: #fff;
      color: var(--copper-dark);
      border: 1px solid #dec2a2;
    }}
    .env-choice-intro {{
      margin: 0 0 16px;
      font-size: 14px;
      color: #493b2f;
      max-width: none !important;
    }}
    .env-choice-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .env-card {{
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      padding: 18px 20px;
      border-radius: 14px;
      background: #fff;
      border: 1px solid #dfd2c0;
      box-shadow: 0 4px 12px rgba(40, 26, 14, 0.05);
    }}
    .env-card-local {{
      border-top: 4px solid var(--green);
    }}
    .env-card-demo {{
      border-top: 4px solid var(--copper);
    }}
    .env-card-head {{
      display: flex;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 10px;
    }}
    .env-card-icon {{
      font-size: 26px;
      line-height: 1;
    }}
    .env-card-head h4 {{
      margin: 0 0 4px;
      font-size: 16px;
      color: #1f1812;
    }}
    .env-tag {{
      display: inline-block;
      font-size: 11.5px;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 6px;
    }}
    .env-tag-green {{
      background: var(--green-bg);
      color: var(--green);
    }}
    .env-tag-copper {{
      background: var(--copper-soft);
      color: var(--copper-dark);
    }}
    .env-card-desc {{
      margin: 0 0 12px;
      font-size: 13.5px;
      line-height: 1.6;
      color: #5c4f43;
    }}
    .env-url-box {{
      margin-bottom: 12px;
      padding: 6px 10px;
      border-radius: 8px;
      background: #f6f0e6;
      border: 1px dashed #d5c3ad;
      font-size: 13px;
    }}
    .env-card-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: auto;
    }}
    .env-btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 7px 14px;
      border-radius: 9px;
      font-size: 13px;
      font-weight: 700;
      text-decoration: none !important;
      transition: all 0.15s ease;
    }}
    .env-btn-local {{
      background: var(--green);
      color: #fff !important;
    }}
    .env-btn-local:hover {{
      background: #105e39;
      transform: translateY(-1px);
    }}
    .env-btn-demo {{
      background: var(--copper);
      color: #fff !important;
    }}
    .env-btn-demo:hover {{
      background: var(--copper-dark);
      transform: translateY(-1px);
    }}
    .env-btn-sub {{
      background: #f4ebe0;
      color: #4b3d30 !important;
      border: 1px solid #d9c7b2;
    }}
    .env-btn-sub:hover {{
      background: #e9dac8;
    }}

    /* Smart Environment Toast (Bottom-Right Lightweight Notification) */
    .smart-nav-toast {{
      position: fixed;
      right: 22px;
      bottom: 22px;
      z-index: 95;
      max-width: 430px;
      padding: 16px 18px;
      border-radius: 14px;
      background: rgba(30, 24, 19, 0.96);
      color: #fdf7f0;
      border: 1px solid rgba(245, 166, 91, 0.55);
      box-shadow: 0 16px 42px rgba(0, 0, 0, 0.35);
      backdrop-filter: blur(10px);
      font-size: 13.5px;
      line-height: 1.6;
      display: none;
      animation: toastSlideUp 0.22s ease-out;
    }}
    .smart-nav-toast.show {{
      display: block;
    }}
    @keyframes toastSlideUp {{
      from {{ opacity: 0; transform: translateY(14px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    .smart-toast-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 6px;
      font-weight: 800;
      color: #ffbe7b;
      font-size: 14px;
    }}
    .smart-toast-close {{
      background: transparent;
      border: 0;
      color: #cbbba8;
      cursor: pointer;
      font-size: 14px;
      padding: 2px 6px;
    }}
    .smart-toast-close:hover {{ color: #fff; }}
    .smart-toast-local-link {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      margin-top: 8px;
      padding: 5px 11px;
      border-radius: 8px;
      background: rgba(22, 121, 75, 0.35);
      border: 1px solid #43b581;
      color: #9ef0c6 !important;
      font-weight: 700;
      font-size: 12.8px;
      text-decoration: none !important;
    }}
    .smart-toast-local-link:hover {{
      background: var(--green);
      color: #fff !important;
    }}

    /* Direct Launch Action Cards & Inline App Links */
    .direct-launch-item {{
      list-style: none;
      margin: 10px 0 14px -18px;
    }}
    .direct-launch-card {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      padding: 12px 16px;
      border-radius: 12px;
      background: linear-gradient(90deg, #fff6ec 0%, #f9efe2 100%);
      border: 1px solid #e2c29d;
      box-shadow: 0 4px 12px rgba(181, 101, 39, 0.08);
    }}
    .direct-launch-label {{
      font-size: 13.5px;
      font-weight: 800;
      color: #7c3f10;
      white-space: nowrap;
    }}
    .direct-launch-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }}
    .direct-launch-btn {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 6px 14px;
      border-radius: 9px;
      background: var(--copper);
      color: #fff !important;
      font-size: 13.5px;
      font-weight: 700;
      text-decoration: none !important;
      box-shadow: 0 2px 6px rgba(140, 72, 20, 0.22);
      transition: all 0.15s ease;
    }}
    .direct-launch-btn:hover {{
      background: var(--copper-dark);
      transform: translateY(-1px);
      box-shadow: 0 4px 10px rgba(140, 72, 20, 0.3);
    }}
    .inline-app-link {{
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 2px 9px;
      margin: 1px 2px;
      border-radius: 6px;
      background: #f8efe4;
      border: 1px solid #e0c3a2;
      color: var(--copper-dark) !important;
      font-size: 13.5px;
      font-weight: 700;
      text-decoration: none !important;
      transition: all 0.14s ease;
    }}
    .inline-app-link:hover {{
      background: var(--copper);
      border-color: var(--copper);
      color: #fff !important;
    }}

    /* Layout Grid */
    .wrap {{
      display: grid;
      grid-template-columns: 275px minmax(0, 1fr);
      gap: 32px;
      max-width: 1440px;
      margin: auto;
      padding: 28px 24px 96px;
    }}
    aside {{
      align-self: start;
      position: sticky;
      top: 76px;
      max-height: calc(100vh - 96px);
      overflow-y: auto;
      padding: 18px 14px;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: 0 6px 20px rgba(59, 35, 15, 0.04);
    }}
    .aside-title {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin: 0 8px 12px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--line);
      font-weight: 800;
      font-size: 14.5px;
      color: var(--ink);
    }}
    aside a.toc-link {{
      display: block;
      padding: 6px 10px;
      margin: 2px 0;
      border-radius: 8px;
      color: var(--muted);
      font-size: 13.5px;
      line-height: 1.45;
      transition: all 0.12s ease;
    }}
    aside a.toc-h2 {{
      font-weight: 700;
      color: #2f261f;
      margin-top: 8px;
    }}
    aside a.toc-h3 {{
      padding-left: 20px;
      font-size: 12.8px;
      border-left: 2px solid transparent;
    }}
    aside a.toc-link:hover {{
      background: #f3e9dc;
      color: var(--copper-dark);
      text-decoration: none;
    }}
    aside a.toc-link.active {{
      background: var(--copper-soft);
      color: var(--copper-dark);
      font-weight: 700;
      border-left-color: var(--copper);
    }}

    /* Main Article Styling */
    main {{
      min-width: 0;
      padding: 38px clamp(24px, 4.5vw, 68px) 76px;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 20px;
      box-shadow: 0 8px 30px rgba(59, 35, 15, 0.04);
    }}
    h1, h2, h3 {{
      line-height: 1.35;
      letter-spacing: -0.02em;
      scroll-margin-top: 84px;
    }}
    h1 {{
      font-size: clamp(26px, 2.8vw, 36px);
      margin: 0 0 16px;
      color: #1c1612;
    }}
    h2 {{
      font-size: 23px;
      margin: 52px 0 18px;
      padding: 14px 18px;
      background: linear-gradient(90deg, #f8efe4 0%, rgba(248, 239, 228, 0.2) 100%);
      border-left: 4px solid var(--copper);
      border-radius: 0 12px 12px 0;
      color: #251c15;
    }}
    h3 {{
      font-size: 19px;
      margin: 34px 0 12px;
      color: #2d2219;
    }}
    p, li {{ max-width: 88ch; }}
    ul, ol {{ padding-left: 22px; }}
    li {{ margin: 6px 0; }}
    hr {{
      border: 0;
      border-top: 1px dashed var(--line);
      margin: 36px 0;
    }}
    blockquote {{
      margin: 20px 0;
      padding: 16px 22px;
      border-left: 4px solid var(--copper);
      background: #f6ede2;
      color: #5e3e24;
      border-radius: 0 12px 12px 0;
      font-size: 15px;
    }}
    blockquote p {{ margin: 0; max-width: none; }}

    /* Tables */
    table {{
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      margin: 18px 0 28px;
      font-size: 14px;
      border: 1px solid var(--line);
      border-radius: 12px;
      overflow: hidden;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 11px 14px;
      text-align: left;
      vertical-align: top;
    }}
    tr:last-child td {{ border-bottom: none; }}
    th {{
      background: #f3e9dc;
      font-weight: 700;
      color: #2c221a;
    }}
    tbody tr:nth-child(even) {{ background: #fcf9f4; }}
    tbody tr:hover {{ background: #f7efe4; }}

    /* Inline code & Prompt Copy Box */
    code {{
      padding: 2px 6px;
      background: #f2ebe1;
      color: #7c3f10;
      border-radius: 5px;
      font: 13.5px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace;
    }}
    .prompt-box {{
      margin: 16px 0 26px;
      border-radius: 12px;
      overflow: hidden;
      border: 1px solid #3d332c;
      background: #231e1a;
      box-shadow: 0 6px 18px rgba(25, 18, 12, 0.12);
    }}
    .prompt-bar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 14px;
      background: #312923;
      border-bottom: 1px solid #433931;
    }}
    .prompt-tag {{
      font-size: 12.5px;
      font-weight: 600;
      color: #e6cbb1;
    }}
    .copy-btn {{
      cursor: pointer;
      padding: 5px 12px;
      border-radius: 7px;
      border: 1px solid rgba(245, 166, 91, 0.45);
      background: rgba(181, 101, 39, 0.28);
      color: #ffd7b0;
      font-size: 12.5px;
      font-weight: 600;
      transition: all 0.15s ease;
    }}
    .copy-btn:hover {{
      background: var(--copper);
      color: #fff;
    }}
    .copy-btn.copied {{
      background: var(--green);
      border-color: var(--green);
      color: #fff;
    }}
    .prompt-box pre {{
      margin: 0;
      padding: 16px 18px;
      overflow-x: auto;
      background: transparent;
      color: #f8f2ea;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    .prompt-box pre code {{
      padding: 0;
      background: none;
      color: inherit;
      font-size: 14px;
      line-height: 1.65;
    }}

    /* Screenshot Figure Cards */
    .shot-card {{
      margin: 18px 0 26px;
      border: 1px solid #d5c5ae;
      border-radius: 14px;
      overflow: hidden;
      background: #fff;
      box-shadow: 0 10px 28px rgba(45, 30, 15, 0.09);
      cursor: zoom-in;
      transition: transform 0.18s ease, box-shadow 0.18s ease;
    }}
    .shot-card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 14px 36px rgba(45, 30, 15, 0.15);
    }}
    .shot-bar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 9px 16px;
      background: #f4ece0;
      border-bottom: 1px solid #dfd2bf;
      font-size: 13px;
    }}
    .shot-dots {{
      display: inline-flex;
      gap: 6px;
      align-items: center;
    }}
    .shot-dots i {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
      background: #d9c9b5;
    }}
    .shot-dots i:nth-child(1) {{ background: #e57368; }}
    .shot-dots i:nth-child(2) {{ background: #f0b849; }}
    .shot-dots i:nth-child(3) {{ background: #5bbd72; }}
    .shot-title {{
      font-weight: 700;
      color: #3a2e25;
      margin-right: auto;
    }}
    .shot-zoom {{
      font-size: 12px;
      font-weight: 600;
      color: var(--copper-dark);
      background: #fff8f0;
      padding: 3px 10px;
      border-radius: 999px;
      border: 1px solid #e3c8ab;
      white-space: nowrap;
    }}
    .shot-img-wrap {{
      background: #1a1614;
      line-height: 0;
    }}
    .shot-card img {{
      width: 100%;
      height: auto;
      display: block;
    }}

    /* Fullscreen Lightbox Modal */
    .lightbox {{
      position: fixed;
      inset: 0;
      z-index: 100;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 28px;
      background: rgba(18, 14, 11, 0.88);
      backdrop-filter: blur(8px);
      cursor: zoom-out;
    }}
    .lightbox.open {{ display: flex; }}
    .lightbox-inner {{
      max-width: 95vw;
      max-height: 92vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 12px;
    }}
    .lightbox-inner img {{
      max-width: 95vw;
      max-height: 84vh;
      border-radius: 12px;
      border: 1px solid rgba(255, 255, 255, 0.2);
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
      background: #fff;
    }}
    .lightbox-caption {{
      color: #f8efe6;
      font-size: 14px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 16px;
    }}
    .lightbox-close {{
      padding: 4px 12px;
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.16);
      color: #fff;
      font-size: 12.5px;
      border: 1px solid rgba(255, 255, 255, 0.25);
      cursor: pointer;
    }}

    @media (max-width: 1180px) {{
      .flow-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    }}
    @media (max-width: 960px) {{
      .wrap {{ display: block; padding: 16px 14px 64px; }}
      aside {{ position: static; max-height: none; margin-bottom: 18px; }}
      main {{ padding: 24px 18px 44px; }}
      .hero-banner {{ padding: 0 14px; margin-top: 16px; }}
      .hero-card {{ padding: 22px 20px; }}
      .flow-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .env-choice-grid {{ grid-template-columns: 1fr; }}
      .top {{ padding: 10px 14px; }}
      table {{ display: block; overflow-x: auto; }}
    }}
    @media (max-width: 600px) {{
      .flow-grid {{ grid-template-columns: 1fr; }}
    }}
    @media print {{
      .top, aside, .lightbox, .smart-nav-toast {{ display: none !important; }}
      .wrap {{ display: block; padding: 0; }}
      main {{ border: 0; box-shadow: none; }}
    }}
  </style>
</head>
<body>
  <header class="top">
    <a class="brand app-link" href="#" data-path="/chat" data-route="/chat" target="_blank" rel="noopener">
      <img src="/logo-hunter.png" alt="猎鹿人">
      <span>猎鹿人 · HunterCode</span>
      <span class="brand-badge">v3.5.2 帮助中心</span>
    </a>
    <nav aria-label="核心工作区导航（智能环境识别 · 新窗口打开）">
      <a class="nav-primary app-link" href="#" data-path="/chat" data-route="/chat" target="_blank" rel="noopener">返回 AI 对话 ↗</a>
      <a class="app-link" href="#" data-path="/workbench" data-route="/workbench" target="_blank" rel="noopener">投研工作台 ↗</a>
      <a class="app-link" href="#" data-path="/strategies/index.html" data-route="/strategies/index.html" target="_blank" rel="noopener">策略中心 ↗</a>
      <a class="app-link" href="#" data-path="/finance/overview" data-route="/finance/overview" target="_blank" rel="noopener">智能体自动炒股 ↗</a>
      <a class="app-link" href="#" data-path="/settings?tab=llm" data-route="/settings?tab=llm" target="_blank" rel="noopener">⚙️ 设置 ↗</a>
      <a href="https://github.com/agentpit-io/hunter-community" target="_blank" rel="noopener">GitHub 开源仓库 ↗</a>
    </nav>
  </header>

  <section class="hero-banner" aria-label="股民日常炒股 6 步总流程导航">
    <div class="hero-card">
      <div class="hero-top">
        <div class="hero-title-group">
          <span class="hero-eyebrow">🦌 私人证券研究所 · 零实盘交易风险 · 100% 本地隐私优先 · v3.5.2 正式版</span>
          <h1>猎鹿人 HunterCode 股民日常炒股全流程操作手册</h1>
          <p class="hero-sub">
            不用懂编程、不用背命令！跟着交易日从早到晚的时间表走：早上 8:30 看三栏早报，盘中盯异动翻 PDF 公告原文、做 56 项技术 + 90 项基本面全身检查，下午 15:30 收盘复盘归档，周末算 ETF 定投跑回测。点击下方任意步骤卡片可查看图文教程，点击「🚀 直达按钮」自动识别当前环境并在新窗口打开对应功能（官网预览自动直达在线演示站，本地环境直达本机实例）！
          </p>
        </div>
        <div class="hero-pills">
          <div class="hero-pill"><strong>12 张</strong> 实机高清截图</div>
          <div class="hero-pill"><strong>22 个</strong> 内置金融 SKILL</div>
          <div class="hero-pill"><strong>69 个</strong> 真实数据工具</div>
          <div class="hero-pill"><strong>0 实盘下单</strong> 物理级安全</div>
        </div>
      </div>

      <div class="flow-section-title">
        <span>🧭 股民日常炒股 6 步总流程图（可跳章节看图文，或点下方按钮在新窗口直达功能页）</span>
        <span>支持点击正文中任意截图全屏放大查看</span>
      </div>
      <div class="flow-grid">
        <div class="flow-step">
          <div>
            <div class="flow-step-head">
              <span class="flow-badge">准备篇</span>
              <span class="flow-time">首次 3 分钟</span>
            </div>
            <h3>装好上手与 4 大入口</h3>
            <p>配好大模型 Key、验证行情连通，3 秒认清对话、工作台、策略与模拟盘。</p>
          </div>
          <div class="flow-actions">
            <a class="flow-go" href="#{escape(anchors['step0'])}">📖 查看第 1 章图文 →</a>
            <a class="flow-launch app-link" href="#" data-path="/settings?tab=llm" data-route="/settings?tab=llm" target="_blank" rel="noopener">🚀 直达设置 (/settings) ↗</a>
          </div>
        </div>

        <div class="flow-step">
          <div>
            <div class="flow-step-head">
              <span class="flow-badge">第一步 · 盘前</span>
              <span class="flow-time">08:30 - 09:25</span>
            </div>
            <h3>08:30 盘前市场晨报</h3>
            <p>一键出三栏观察卡（事实/推断/待核实），带数字回读校验防小作文忽悠。</p>
          </div>
          <div class="flow-actions">
            <a class="flow-go" href="#{escape(anchors['step1'])}">📖 查看第 2 章图文 →</a>
            <a class="flow-launch app-link" href="#" data-path="/workbench?tab=market" data-route="/workbench?tab=market" target="_blank" rel="noopener">🚀 直达市场工作台 ↗</a>
          </div>
        </div>

        <div class="flow-step">
          <div>
            <div class="flow-step-head">
              <span class="flow-badge">第二步 · 盘中</span>
              <span class="flow-time">09:30 - 15:00</span>
            </div>
            <h3>盯异动 · 翻公告 · 诊个股</h3>
            <p>自选股异动归因、PDF 公告页码抽屉、56 项技术 + 90 项基本面体检。</p>
          </div>
          <div class="flow-actions">
            <a class="flow-go" href="#{escape(anchors['step2'])}">📖 查看第 3 章图文 →</a>
            <a class="flow-launch app-link" href="#" data-path="/workbench?tab=watchlist" data-route="/workbench?tab=watchlist" target="_blank" rel="noopener">🚀 直达自选股/个股工作台 ↗</a>
          </div>
        </div>

        <div class="flow-step">
          <div>
            <div class="flow-step-head">
              <span class="flow-badge">第三步 · 盘后</span>
              <span class="flow-time">15:30 以后</span>
            </div>
            <h3>盘后复盘与持仓体检</h3>
            <p>15:30 复盘自动存本地经验库，核对买入理由变没变，安全导入交割单。</p>
          </div>
          <div class="flow-actions">
            <a class="flow-go" href="#{escape(anchors['step3'])}">📖 查看第 4 章图文 →</a>
            <a class="flow-launch app-link" href="#" data-path="/workbench?tab=portfolio_review" data-route="/workbench?tab=portfolio_review" target="_blank" rel="noopener">🚀 直达组合复盘工作台 ↗</a>
          </div>
        </div>

        <div class="flow-step">
          <div>
            <div class="flow-step-head">
              <span class="flow-badge">第四步 · 周末</span>
              <span class="flow-time">定投与选股</span>
            </div>
            <h3>ETF 定投计算与防坑回测</h3>
            <p>算清每月定投真实 XIRR 年化收益，大白话选股 + 6 维回测防造假审计。</p>
          </div>
          <div class="flow-actions">
            <a class="flow-go" href="#{escape(anchors['step4'])}">📖 查看 5.1 节图文 →</a>
            <a class="flow-launch app-link" href="#" data-path="/workbench?tab=quant" data-route="/workbench?tab=quant" target="_blank" rel="noopener">🚀 直达量化与 ETF 定投 ↗</a>
          </div>
        </div>

        <div class="flow-step">
          <div>
            <div class="flow-step-head">
              <span class="flow-badge">第五步 · 演练</span>
              <span class="flow-time">全天候模拟</span>
            </div>
            <h3>策略中心与 AI 自动炒股</h3>
            <p>官方多因子策略库 + AI 智能体每日 6 大交易时点全自动模拟盘练手。</p>
          </div>
          <div class="flow-actions">
            <a class="flow-go" href="#{escape(anchors['step5'])}">📖 查看 5.2 节图文 →</a>
            <a class="flow-launch app-link" href="#" data-path="/strategies/index.html" data-route="/strategies/index.html" target="_blank" rel="noopener">🚀 直达策略中心 ↗</a>
          </div>
        </div>
      </div>
    </div>
  </section>

  <div class="wrap">
    <aside aria-label="本页章节目录">
      <div class="aside-title">
        <span>📖 说明书目录</span>
        <a href="#top" onclick="window.scrollTo({{top:0,behavior:'smooth'}});return false;" style="font-size:12px;font-weight:600;">↑ 回到顶部</a>
      </div>
      {toc}
    </aside>
    <main id="article-main">
      {content}
    </main>
  </div>

  <div id="lightbox" class="lightbox" role="dialog" aria-modal="true" aria-label="截图全屏预览" onclick="closeLightbox()">
    <div class="lightbox-inner" onclick="event.stopPropagation()">
      <img id="lightbox-img" src="" alt="" />
      <div class="lightbox-caption">
        <span id="lightbox-title"></span>
        <button type="button" class="lightbox-close" onclick="closeLightbox()">按 ESC 或点击此处关闭 ✕</button>
      </div>
    </div>
  </div>

  <div id="smart-nav-toast" class="smart-nav-toast" role="status" aria-live="polite">
    <div class="smart-toast-head">
      <span>🌐 官网预览模式 · 双通道智能导航</span>
      <button type="button" class="smart-toast-close" onclick="closeSmartToast()" aria-label="关闭提示">✕</button>
    </div>
    <div id="smart-toast-body">
      当前处于官网预览模式，已为您打开在线演示站。如果您已在本地启动 Hunter，亦可点击：
    </div>
    <div>
      <a id="smart-toast-local-anchor" class="smart-toast-local-link" href="http://localhost:3100/workbench" target="_blank" rel="noopener">
        🖥️ 打开本地 localhost:3100/workbench ↗
      </a>
    </div>
  </div>

  <script>
    var DEMO_ORIGIN = 'https://hunter-community.agentpit.io';
    var LOCAL_ORIGIN = 'http://localhost:3100';
    var smartToastTimer = null;

    function detectAppEnvironment() {{
      var hostname = (window.location.hostname || '').toLowerCase();
      var port = window.location.port || '';
      var hasCustomPort = port !== '' && port !== '80' && port !== '443';
      var isLocalOrSelfHosted = (
        hostname === 'localhost' ||
        hostname === '127.0.0.1' ||
        hasCustomPort ||
        hostname.indexOf('hunter-community.agentpit.io') !== -1
      );
      var isOfficialMarketingSite = (
        hostname === 'agentpit.io' ||
        hostname === 'www.agentpit.io'
      );
      return {{
        hostname: hostname,
        port: port,
        isLocalOrSelfHosted: isLocalOrSelfHosted,
        isOfficialMarketingSite: isOfficialMarketingSite || !isLocalOrSelfHosted
      }};
    }}

    function showSmartToast(routePath) {{
      var toast = document.getElementById('smart-nav-toast');
      var localAnchor = document.getElementById('smart-toast-local-anchor');
      if (!toast || !localAnchor) return;
      var normalized = routePath && routePath.charAt(0) === '/' ? routePath : '/' + (routePath || 'workbench');
      localAnchor.href = LOCAL_ORIGIN + normalized;
      localAnchor.textContent = '[打开本地 localhost:3100' + normalized + ']';
      toast.classList.add('show');
      if (smartToastTimer) clearTimeout(smartToastTimer);
      smartToastTimer = setTimeout(function() {{
        toast.classList.remove('show');
      }}, 9000);
    }}

    function closeSmartToast() {{
      var toast = document.getElementById('smart-nav-toast');
      if (toast) toast.classList.remove('show');
    }}

    function initSmartAppLinks() {{
      var env = detectAppEnvironment();
      var indicator = document.getElementById('current-env-indicator');
      if (indicator) {{
        if (env.isLocalOrSelfHosted) {{
          var hostLabel = window.location.host || env.hostname || '本地实例';
          indicator.textContent = '✅ 当前已连接实例：' + hostLabel + '（点击按钮直接打开当前实例）';
        }} else {{
          indicator.textContent = '🌐 当前处于官网预览模式（点击功能按钮将优先打开在线演示站，并支持切换本地 :3100）';
        }}
      }}

      var appLinks = Array.prototype.slice.call(document.querySelectorAll('a.app-link[data-path]'));
      appLinks.forEach(function(link) {{
        var routePath = link.getAttribute('data-path') || link.getAttribute('data-route') || '/workbench';
        if (routePath.charAt(0) !== '/') {{
          routePath = '/' + routePath;
        }}
        if (env.isLocalOrSelfHosted) {{
          link.setAttribute('href', routePath);
        }} else {{
          link.setAttribute('href', DEMO_ORIGIN + routePath);
        }}
        link.addEventListener('click', function(e) {{
          var currentEnv = detectAppEnvironment();
          if (!currentEnv.isLocalOrSelfHosted) {{
            e.preventDefault();
            var targetUrl = DEMO_ORIGIN + routePath;
            window.open(targetUrl, '_blank', 'noopener');
            showSmartToast(routePath);
          }}
        }});
      }});
    }}

    initSmartAppLinks();

    function openLightbox(src, title) {{
      var box = document.getElementById('lightbox');
      var img = document.getElementById('lightbox-img');
      var cap = document.getElementById('lightbox-title');
      img.src = src;
      img.alt = title || '';
      cap.textContent = title || '';
      box.classList.add('open');
    }}

    function closeLightbox() {{
      var box = document.getElementById('lightbox');
      box.classList.remove('open');
    }}

    document.addEventListener('keydown', function(e) {{
      if (e.key === 'Escape') {{
        closeLightbox();
        closeSmartToast();
      }}
    }});

    function copyPrompt(btn) {{
      var box = btn.closest('.prompt-box');
      if (!box) return;
      var codeEl = box.querySelector('code');
      if (!codeEl) return;
      var text = codeEl.innerText || codeEl.textContent || '';
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(text).then(function() {{
          showCopied(btn);
        }}).catch(function() {{
          fallbackCopy(text, btn);
        }});
      }} else {{
        fallbackCopy(text, btn);
      }}
    }}

    function fallbackCopy(text, btn) {{
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {{ document.execCommand('copy'); }} catch (e) {{}}
      document.body.removeChild(ta);
      showCopied(btn);
    }}

    function showCopied(btn) {{
      var orig = btn.getAttribute('data-orig') || btn.textContent;
      btn.setAttribute('data-orig', orig);
      btn.textContent = '✅ 已复制提问口令';
      btn.classList.add('copied');
      setTimeout(function() {{
        btn.textContent = orig;
        btn.classList.remove('copied');
      }}, 2000);
    }}

    // Scroll spy for sidebar TOC
    (function() {{
      var links = Array.prototype.slice.call(document.querySelectorAll('aside a.toc-link'));
      if (!links.length) return;
      var targets = links.map(function(link) {{
        var id = link.getAttribute('data-target');
        return document.getElementById(id);
      }}).filter(Boolean);

      function onScroll() {{
        var scrollPos = window.scrollY + 120;
        var currentId = targets[0] ? targets[0].id : '';
        for (var i = 0; i < targets.length; i++) {{
          if (targets[i].offsetTop <= scrollPos) {{
            currentId = targets[i].id;
          }}
        }}
        links.forEach(function(link) {{
          if (link.getAttribute('data-target') === currentId) {{
            link.classList.add('active');
          }} else {{
            link.classList.remove('active');
          }}
        }});
      }}
      window.addEventListener('scroll', onScroll, {{ passive: true }});
      onScroll();
    }})();
  </script>
</body>
</html>'''
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(page, encoding="utf-8")
    print(f"Generated {OUTPUT.relative_to(ROOT)} from {SOURCE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
