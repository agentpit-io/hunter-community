#!/usr/bin/env python3
"""Playwright 全流程真实浏览器高清截图采集脚本 · HunterCode (v3.5.1) 产品操作说明书配图。

在 1440x900 视口下对 http://127.0.0.1:3200 采集 12 张核心日常炒股全流程高清截图：
01-setup-wizard.png
02-chat-workspace.png
03-chat-deep-analysis.png
04-morning-market-brief.png
05-intraday-watchlist-anomaly.png
06-announcement-pdf-drawer.png
07-stock-56tech-90fund.png
08-after-close-review.png
09-portfolio-thesis-journal.png
10-broker-readonly-auth.png
11-quant-etf-dca-strategies.png
12-strategy-and-auto-trade.png
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("HCA_BASE_URL", "http://127.0.0.1:3200")
OUT_DIR = Path("/mnt/mixplode/hca/apps/web/public/help/images")


def _mint_real_jwt() -> str:
    cmd = [
        "docker", "exec", "hca-api", "python3", "-c",
        (
            "from app.routers.auth import _sign_access, _ensure_auth_tables; "
            "from app.services.database import get_conn; "
            "_ensure_auth_tables(); "
            "c=get_conn(); cur=c.cursor(); "
            "cur.execute(\"INSERT INTO users (email, email_lower, pw_hash, role, display_name) "
            "VALUES ('help_guide@hunter.local', 'help_guide@hunter.local', 'noop', 'admin', '普通股民张先生') "
            "ON CONFLICT (email_lower) DO UPDATE SET status='active', display_name='普通股民张先生' RETURNING id, role, email\"); "
            "row=cur.fetchone(); c.commit(); c.close(); "
            "tok=_sign_access(str(row[0]), row[1], row[2]); "
            "import urllib.request, json; "
            "req=urllib.request.Request('http://127.0.0.1:8000/api/auth/compliance-ack', "
            "data=json.dumps({'version':'v1.0'}).encode(), "
            "headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}, method='POST'); "
            "urllib.request.urlopen(req, timeout=5).read(); "
            "print(tok)"
        ),
    ]
    return subprocess.check_output(cmd, text=True).strip()


def _dismiss_compliance(page) -> None:
    ack_btn = page.locator(
        "button:has-text('我已阅读并同意'), button:has-text('同意并继续'), button:has-text('已知悉')"
    )
    if ack_btn.count() > 0 and ack_btn.first.is_visible():
        ack_btn.first.click()
        page.wait_for_timeout(400)


def capture_all_screenshots() -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    results: list[dict] = []

    with sync_playwright() as p:
        exec_path = "/home/support/.cache/ms-playwright/chromium-1254/chrome-linux64/chrome"
        launch_kwargs = {"headless": True}
        if os.path.exists(exec_path):
            launch_kwargs["executable_path"] = exec_path
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)
        page = context.new_page()

        real_jwt = _mint_real_jwt()
        page.add_init_script(
            f"""
            try {{
              localStorage.setItem('hunter_compliance_ack_v1', '1');
              localStorage.setItem('hunter_compliance_ack', '1');
              localStorage.setItem('hunter_token', '{real_jwt}');
              localStorage.setItem('hunter_email', 'help_guide@hunter.local');
            }} catch (e) {{}}
            """
        )

        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        def save_shot(filename: str, full_page: bool = False) -> None:
            out_path = OUT_DIR / filename
            page.screenshot(path=str(out_path), full_page=full_page)
            size_kb = round(out_path.stat().st_size / 1024, 1)
            results.append({"file": filename, "size_kb": size_kb, "path": str(out_path)})
            print(f"[OK] {filename} ({size_kb} KB)")

        # ── 01. 首次打开配置向导 / 大模型与偏好设置 ─────────────────────────
        page.goto(f"{BASE_URL}/settings?tab=llm", wait_until="networkidle", timeout=30000)
        _dismiss_compliance(page)
        page.wait_for_timeout(1200)
        save_shot("01-setup-wizard.png", full_page=False)

        # ── 02. 核心 AI 对话投研主界面 (/chat) ─────────────────────────────
        page.goto(f"{BASE_URL}/chat", wait_until="networkidle", timeout=30000)
        _dismiss_compliance(page)
        page.wait_for_timeout(1800)
        save_shot("02-chat-workspace.png", full_page=False)

        # ── 03. 在 /chat 中对个股提问或查看深度分析与能力库 ────────────────
        # 打开能力库或展示带丰富投研问答的界面
        page.goto(f"{BASE_URL}/library", wait_until="networkidle", timeout=30000)
        _dismiss_compliance(page)
        page.wait_for_timeout(1500)
        save_shot("03-chat-deep-analysis.png", full_page=False)

        # ── 04. 早上看盘：投研工作台 -> 市场工作台 · 08:30 盘前晨报观察卡 ──
        page.goto(f"{BASE_URL}/workbench", wait_until="networkidle", timeout=30000)
        _dismiss_compliance(page)
        page.wait_for_selector("[data-testid='panel-market-workbench']", timeout=15000)
        page.click("[data-testid='btn-generate-pre-market']")
        page.wait_for_timeout(900)
        save_shot("04-morning-market-brief.png", full_page=False)

        # ── 08. 收盘复盘：投研工作台 -> 市场工作台 · 15:30 盘后复盘总结 ────
        page.click("[data-testid='btn-generate-after-close']")
        page.wait_for_timeout(900)
        # 滚动到盘后复盘卡片区域以便清晰展示
        after_card = page.locator("[data-testid='btn-generate-after-close']")
        if after_card.count() > 0:
            after_card.first.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
        save_shot("08-after-close-review.png", full_page=False)

        # ── 05. 盘中盯盘：自选股工作台 · 盘中异动解释 ───────────────────────
        page.click("[data-testid='tab-watchlist-workbench']")
        page.wait_for_selector("[data-testid='panel-watchlist-workbench']", timeout=15000)
        page.click("[data-testid='btn-trigger-intraday-anomaly']")
        page.wait_for_selector("[data-testid='intraday-anomaly-card']", timeout=10000)
        page.wait_for_timeout(700)
        save_shot("05-intraday-watchlist-anomaly.png", full_page=False)

        # ── 06. 盘中查公告：右侧 PDF 原文页码溯源抽屉 ──────────────────────
        page.click("[data-testid='btn-open-pdf-drawer-0']")
        page.wait_for_selector("[data-testid='announcement-pdf-drawer']", timeout=10000)
        page.wait_for_timeout(700)
        save_shot("06-announcement-pdf-drawer.png", full_page=False)

        close_btn = page.locator("[data-testid='btn-close-pdf-drawer']")
        if close_btn.count() > 0 and close_btn.first.is_visible():
            close_btn.first.click()
            page.wait_for_timeout(300)

        # ── 07. 盘中诊股：个股研究工作台 · 56 项技术 + 90 项基本面评分卡 ───
        page.click("[data-testid='tab-stock-workbench']")
        page.wait_for_selector("[data-testid='panel-stock-workbench']", timeout=15000)
        page.wait_for_timeout(800)
        save_shot("07-stock-56tech-90fund.png", full_page=False)

        # ── 09. 持仓与复盘：组合复盘工作台 · 投资论点看板与交易日志 ────────
        page.click("[data-testid='tab-portfolio-review-workbench']")
        page.wait_for_selector("[data-testid='panel-portfolio-review-workbench']", timeout=15000)
        page.wait_for_timeout(800)
        save_shot("09-portfolio-thesis-journal.png", full_page=False)

        # ── 10. 导入持仓：设置 -> 合作券商只读授权与交割单导入 ─────────────
        page.goto(f"{BASE_URL}/settings?tab=broker", wait_until="networkidle", timeout=30000)
        _dismiss_compliance(page)
        page.wait_for_selector("[data-testid='broker-connector-panel']", timeout=15000)
        page.click("[data-testid='btn-broker-authorize']")
        page.wait_for_selector("[data-testid='authorized-positions-preview']", timeout=15000)
        page.click("[data-testid='btn-upload-settlement-csv']")
        page.wait_for_selector("[data-testid='manual-import-summary-box']", timeout=15000)
        page.wait_for_timeout(700)
        save_shot("10-broker-readonly-auth.png", full_page=False)

        # ── 11. 定投规划与选股：量化研究与 ETF 定投工作台 ──────────────────
        page.goto(f"{BASE_URL}/workbench", wait_until="networkidle", timeout=30000)
        _dismiss_compliance(page)
        page.click("[data-testid='tab-quant-workbench']")
        page.wait_for_selector("[data-testid='panel-quant-workbench']", timeout=15000)
        page.wait_for_timeout(800)
        save_shot("11-quant-etf-dca-strategies.png", full_page=False)

        # ── 12. 进阶实战：策略中心与 AI 智能体模拟炒股 ─────────────────────
        page.goto(f"{BASE_URL}/strategies/index.html", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1200)
        save_shot("12-strategy-and-auto-trade.png", full_page=False)

        browser.close()

    return {
        "ok": True,
        "base_url": BASE_URL,
        "screenshots": results,
        "console_errors": console_errors,
        "page_errors": page_errors,
    }


if __name__ == "__main__":
    summary = capture_all_screenshots()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
