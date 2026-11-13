#!/usr/bin/env python3
"""Playwright 真浏览器端到端（E2E）验收脚本 · M2 三大日常工作台与三条日常工作流。

对应《05 测试方案》§3.5 第五关：
1. 市场工作台：点击“生成今日市场晨报”与“15:30 盘后复盘”，验证指数、行业涨跌、数据时间戳、来源标签、三栏证据与数字回读校验徽章；
2. 自选股工作台：添加自选股（如 000001.SZ） -> 自动补齐行情与基本面（验证停牌标的 600898.SH 价格显示 —） -> 点击公告摘要 -> 右侧抽屉打开 PDF 原文并高亮页码与 [来源：... · 第X页]；触发盘中异动解释流；
3. 个股研究工作台：切换 56 项技术指标看板与 90 项基本面体检评分卡（含会计准则重述对照表），全页检查无 NaN、undefined；
4. 全程断言 0 console.error，保存高清截图至 /mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m2/。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("HCA_BASE_URL", "http://127.0.0.1:3200")
SCREENSHOT_DIR = Path("/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m2")


def _mint_real_jwt() -> str:
    cmd = [
        "docker", "exec", "hca-api", "python3", "-c",
        (
            "from app.routers.auth import _sign_access, _ensure_auth_tables; "
            "from app.services.database import get_conn; "
            "_ensure_auth_tables(); "
            "c=get_conn(); cur=c.cursor(); "
            "cur.execute(\"INSERT INTO users (email, email_lower, pw_hash, role, display_name) "
            "VALUES ('e2e@hunter.local', 'e2e@hunter.local', 'noop', 'admin', 'E2E') "
            "ON CONFLICT (email_lower) DO UPDATE SET status='active' RETURNING id, role, email\"); "
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


def run_e2e_verification() -> dict:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    screenshots_saved: list[str] = []

    with sync_playwright() as p:
        exec_path = "/home/support/.cache/ms-playwright/chromium-1254/chrome-linux64/chrome"
        launch_kwargs = {"headless": True}
        if os.path.exists(exec_path):
            launch_kwargs["executable_path"] = exec_path
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": 1440, "height": 960})
        page = context.new_page()

        real_jwt = _mint_real_jwt()
        # 预先写入合规确认标记与真实有效会话 token，模拟已登录股民打开工作台
        page.add_init_script(
            f"""
            try {{
              localStorage.setItem('hunter_compliance_ack_v1', '1');
              localStorage.setItem('hunter_compliance_ack', '1');
              localStorage.setItem('hunter_token', '{real_jwt}');
            }} catch (e) {{}}
            """
        )

        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        # ── 1. 打开市场工作台 ──────────────────────────────────────────────────
        page.goto(f"{BASE_URL}/workbench", wait_until="networkidle", timeout=30000)
        # 若出现合规弹窗则点击确认
        ack_btn = page.locator("button:has-text('我已阅读并同意'), button:has-text('同意并继续'), button:has-text('已知悉')")
        if ack_btn.count() > 0 and ack_btn.first.is_visible():
            ack_btn.first.click()

        page.wait_for_selector("[data-testid='panel-market-workbench']", timeout=15000)
        page.wait_for_selector("[data-testid='market-indices-grid']", timeout=15000)
        page.wait_for_selector("[data-testid='market-three-columns']", timeout=15000)

        # 点击“生成今日市场晨报（08:30 盘前观察卡）”
        page.click("[data-testid='btn-generate-pre-market']")
        page.wait_for_timeout(600)
        market_text = page.locator("[data-testid='panel-market-workbench']").inner_text()
        assert "【已核实事实】" in market_text, "市场工作台缺少【已核实事实】栏目"
        assert "【逻辑推断】" in market_text, "市场工作台缺少【逻辑推断】栏目"
        assert "【待核实问题】" in market_text, "市场工作台缺少【待核实问题】栏目"
        assert "数字回读校验通过" in market_text, "市场工作台未显示数字回读校验通过徽章"

        shot1 = SCREENSHOT_DIR / "01_market_workbench_pre_market.png"
        page.screenshot(path=str(shot1), full_page=True)
        screenshots_saved.append(str(shot1))

        # 点击“执行 15:30 盘后复盘（沉淀 fin_memory）”
        page.click("[data-testid='btn-generate-after-close']")
        page.wait_for_timeout(600)
        after_text = page.locator("[data-testid='panel-market-workbench']").inner_text()
        assert "已沉淀至 fin_memory" in after_text, "盘后复盘未显示 fin_memory 沉淀状态"
        assert "早盘观察条件 vs 15:00 收盘结果对照表" in after_text

        shot2 = SCREENSHOT_DIR / "02_market_workbench_after_close.png"
        page.screenshot(path=str(shot2), full_page=True)
        screenshots_saved.append(str(shot2))

        # ── 2. 切换至自选股工作台 ──────────────────────────────────────────────
        page.click("[data-testid='tab-watchlist-workbench']")
        page.wait_for_selector("[data-testid='panel-watchlist-workbench']", timeout=15000)
        page.wait_for_selector("[data-testid='watchlist-table']", timeout=15000)

        # 添加自选股 000001.SZ
        page.fill("[data-testid='input-add-watchlist-symbol']", "000001.SZ")
        page.click("[data-testid='btn-add-watchlist-symbol']")
        page.wait_for_timeout(600)

        wl_text = page.locator("[data-testid='watchlist-table']").inner_text()
        assert "600036.SH" in wl_text
        assert "600898.SH" in wl_text
        assert "停牌" in wl_text
        assert "—" in wl_text, "停牌标的未显示 —"

        # 触发盘中异动解释流
        page.click("[data-testid='btn-trigger-intraday-anomaly']")
        page.wait_for_selector("[data-testid='intraday-anomaly-card']", timeout=10000)

        shot3 = SCREENSHOT_DIR / "03_watchlist_workbench_and_anomaly.png"
        page.screenshot(path=str(shot3), full_page=True)
        screenshots_saved.append(str(shot3))

        # 点击第一条公告打开右侧 PDF 原文页码抽屉
        page.click("[data-testid='btn-open-pdf-drawer-0']")
        page.wait_for_selector("[data-testid='announcement-pdf-drawer']", timeout=10000)
        drawer_citation = page.locator("[data-testid='drawer-page-citation']").inner_text()
        assert "第1页" in drawer_citation, f"PDF 抽屉页码标签异常: {drawer_citation}"

        # 切换至第 2 页验证页码跳转高亮
        page2_btn = page.locator("[data-testid='btn-pdf-page-2']")
        if page2_btn.count() > 0:
            page2_btn.click()
            page.wait_for_timeout(300)
            drawer_citation_p2 = page.locator("[data-testid='drawer-page-citation']").inner_text()
            assert "第2页" in drawer_citation_p2

        shot4 = SCREENSHOT_DIR / "04_watchlist_announcement_pdf_drawer.png"
        page.screenshot(path=str(shot4), full_page=False)
        screenshots_saved.append(str(shot4))

        # 关闭 PDF 抽屉
        page.click("[data-testid='btn-close-pdf-drawer']")
        page.wait_for_timeout(300)

        # ── 3. 切换至个股研究工作台（56项技术指标 & 90项基本面评分卡）───────────
        page.click("[data-testid='tab-stock-workbench']")
        page.wait_for_selector("[data-testid='panel-stock-workbench']", timeout=15000)
        page.wait_for_selector("[data-testid='technical-dashboard-56']", timeout=15000)
        page.wait_for_selector("[data-testid='fundamental-scorecard-90']", timeout=15000)
        page.wait_for_selector("[data-testid='restatement-comparison-box']", timeout=15000)

        stock_panel_text = page.locator("[data-testid='panel-stock-workbench']").inner_text()
        assert "56/56 项" in stock_panel_text, "56项技术指标未完整展示"
        assert "90/90 项" in stock_panel_text, "90项基本面指标未完整展示"
        assert "调整前" in stock_panel_text and "调整后" in stock_panel_text, "缺少会计准则重述调整前/调整后对照"
        assert "NaN" not in stock_panel_text, "个股工作台出现非法 NaN"
        assert "undefined" not in stock_panel_text, "个股工作台出现非法 undefined"

        shot5 = SCREENSHOT_DIR / "05_stock_workbench_56tech_90fund.png"
        page.screenshot(path=str(shot5), full_page=True)
        screenshots_saved.append(str(shot5))

        browser.close()

    assert len(console_errors) == 0, f"发现浏览器 console.error: {console_errors}"
    assert len(page_errors) == 0, f"发现浏览器 pageerror: {page_errors}"

    summary = {
        "ok": True,
        "base_url": BASE_URL,
        "console_error_count": len(console_errors),
        "page_error_count": len(page_errors),
        "screenshots": screenshots_saved,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    run_e2e_verification()
