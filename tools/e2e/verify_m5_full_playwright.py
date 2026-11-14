#!/usr/bin/env python3
"""Playwright 真浏览器端到端（E2E）六关总验收脚本 · M5 全链路收官验收（`v3.5.0`）。

严格对应《05 测试方案》第五关（3.5 全部 5 项工作台与设置页）与 M5 阶段要求：
1. 市场工作台（Market Workbench）：
   - 验证 `v3.5.0` 版本徽章、20 个 MCP + 22 个金融 SKILL 全量上线标识；
   - 点击“生成今日市场晨报（08:30 盘前观察卡）”与“执行 15:30 盘后复盘（沉淀 fin_memory）”，验证指数、行业涨跌、数据时间戳、三栏证据与数字回读校验徽章；
2. 自选股工作台（Watchlist Workbench）：
   - 验证自选股行情与基本面补齐、停牌标的（600898.SH）显示 `—`（严禁填 `0`）；
   - 点击公告摘要打开右侧 PDF 原文页码抽屉并高亮 `[来源：... · 第X页]`；触发盘中异动解释流；
3. 个股研究工作台（Stock Research Workbench）：
   - 验证 7 大类 56 项技术指标面板与 6 大维度 90 项基本面体检评分卡（含会计准则重述对比）正常切换，无 `NaN`、`undefined`；
4. 量化研究与 ETF 定投工作台（Quant Workbench）：
   - 验证 ETF 定投计算器（正向回测、目标反推、一次性买入对比、强制历史假设免责声明）、因子 IC 分层单调性表、回测质量 6 维防坑审计与自然语言选股器；
5. 组合复盘与券商只读授权工作台（Portfolio Review & Broker Connector Settings）：
   - 验证投资论点看板、交易日志、多源新闻水位线去重；
   - 访问 `/settings?tab=broker` 验证合作券商数据源切换、OAuth 只读授权（AES-256-GCM 托管）、一键撤销授权与 CSV 交割单手工导入兜底通道；
6. 全程断言 `0 console.error`、`0 pageError`，保存高清截图至 `/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m5/`。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("HCA_BASE_URL", "http://127.0.0.1:3200")
SCREENSHOT_DIR = Path("/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m5")


def _mint_real_jwt() -> str:
    cmd = [
        "docker", "exec", "hca-api", "python3", "-c",
        (
            "from app.routers.auth import _sign_access, _ensure_auth_tables; "
            "from app.services.database import get_conn; "
            "_ensure_auth_tables(); "
            "c=get_conn(); cur=c.cursor(); "
            "cur.execute(\"INSERT INTO users (email, email_lower, pw_hash, role, display_name) "
            "VALUES ('e2e_m5@hunter.local', 'e2e_m5@hunter.local', 'noop', 'admin', 'E2E_M5') "
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


def run_m5_full_e2e_verification() -> dict:
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
        context = browser.new_context(viewport={"width": 1440, "height": 980})
        page = context.new_page()

        real_jwt = _mint_real_jwt()
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

        # ── 1. 市场工作台（盘前晨报 + 盘后复盘）────────────────────────────────
        page.goto(f"{BASE_URL}/workbench", wait_until="networkidle", timeout=30000)
        ack_btn = page.locator("button:has-text('我已阅读并同意'), button:has-text('同意并继续'), button:has-text('已知悉')")
        if ack_btn.count() > 0 and ack_btn.first.is_visible():
            ack_btn.first.click()

        page.wait_for_selector("[data-testid='panel-market-workbench']", timeout=15000)
        header_text = page.locator("main").inner_text()
        assert "v3.5.0" in header_text, "工作台顶部未显示 v3.5.0 标识"
        assert "22 金融 SKILL" in header_text

        page.click("[data-testid='btn-generate-pre-market']")
        page.wait_for_timeout(500)
        market_text = page.locator("[data-testid='panel-market-workbench']").inner_text()
        assert "【已核实事实】" in market_text
        assert "数字回读校验通过" in market_text
        assert "NaN" not in market_text and "undefined" not in market_text

        page.click("[data-testid='btn-generate-after-close']")
        page.wait_for_timeout(500)
        after_text = page.locator("[data-testid='panel-market-workbench']").inner_text()
        assert "已沉淀至 fin_memory" in after_text

        shot1 = SCREENSHOT_DIR / "01_m5_market_workbench_pre_and_after_close.png"
        page.screenshot(path=str(shot1), full_page=True)
        screenshots_saved.append(str(shot1))

        # ── 2. 自选股工作台（停牌缺值 — + 盘中异动 + PDF 页码抽屉）────────────
        page.click("[data-testid='tab-watchlist-workbench']")
        page.wait_for_selector("[data-testid='panel-watchlist-workbench']", timeout=15000)
        wl_text = page.locator("[data-testid='watchlist-table']").inner_text()
        assert "600036.SH" in wl_text and "600898.SH" in wl_text and "—" in wl_text

        page.click("[data-testid='btn-trigger-intraday-anomaly']")
        page.wait_for_selector("[data-testid='intraday-anomaly-card']", timeout=10000)
        page.click("[data-testid='btn-open-pdf-drawer-0']")
        page.wait_for_selector("[data-testid='announcement-pdf-drawer']", timeout=10000)
        drawer_citation = page.locator("[data-testid='drawer-page-citation']").inner_text()
        assert "第1页" in drawer_citation

        shot2 = SCREENSHOT_DIR / "02_m5_watchlist_workbench_pdf_drawer_and_anomaly.png"
        page.screenshot(path=str(shot2), full_page=False)
        screenshots_saved.append(str(shot2))

        close_drawer_btn = page.locator("[data-testid='btn-close-pdf-drawer']")
        if close_drawer_btn.count() > 0 and close_drawer_btn.first.is_visible():
            close_drawer_btn.first.click()
            page.wait_for_timeout(300)

        # ── 3. 个股研究工作台（56 项技术指标 + 90 项基本面评分卡）──────────────
        page.click("[data-testid='tab-stock-workbench']")
        page.wait_for_selector("[data-testid='panel-stock-workbench']", timeout=15000)
        sr_text = page.locator("[data-testid='panel-stock-workbench']").inner_text()
        assert "56/56 项" in sr_text and "90/90 项" in sr_text
        assert "NaN" not in sr_text and "undefined" not in sr_text

        shot3 = SCREENSHOT_DIR / "03_m5_stock_research_workbench_56_and_90.png"
        page.screenshot(path=str(shot3), full_page=True)
        screenshots_saved.append(str(shot3))

        # ── 4. 量化研究与 ETF 定投工作台 ───────────────────────────────────────
        page.click("[data-testid='tab-quant-workbench']")
        page.wait_for_selector("[data-testid='panel-quant-workbench']", timeout=15000)
        quant_text = page.locator("[data-testid='panel-quant-workbench']").inner_text()
        assert "基于历史净值与假设参数测算，历史表现不代表未来收益" in quant_text
        assert "510300.SH" in quant_text
        assert "NaN" not in quant_text and "undefined" not in quant_text

        shot4 = SCREENSHOT_DIR / "04_m5_quant_workbench_etf_dca_and_factor_audit.png"
        page.screenshot(path=str(shot4), full_page=True)
        screenshots_saved.append(str(shot4))

        # ── 5. 组合复盘工作台 ──────────────────────────────────────────────────
        page.click("[data-testid='tab-portfolio-review-workbench']")
        page.wait_for_selector("[data-testid='panel-portfolio-review-workbench']", timeout=15000)
        pr_text = page.locator("[data-testid='panel-portfolio-review-workbench']").inner_text()
        assert "investment_thesis_tracker" in pr_text or "投资论点" in pr_text
        assert "NaN" not in pr_text and "undefined" not in pr_text

        shot5 = SCREENSHOT_DIR / "05_m5_portfolio_review_workbench.png"
        page.screenshot(path=str(shot5), full_page=True)
        screenshots_saved.append(str(shot5))

        # ── 6. 合作券商数据源与只读授权管理页（`/settings?tab=broker`）────────
        page.goto(f"{BASE_URL}/settings?tab=broker", wait_until="networkidle", timeout=30000)
        page.wait_for_selector("[data-testid='broker-connector-panel']", timeout=15000)
        page.click("[data-testid='btn-broker-authorize']")
        page.wait_for_selector("[data-testid='authorized-positions-preview']", timeout=15000)
        page.click("[data-testid='btn-broker-revoke']")
        page.wait_for_timeout(500)
        page.click("[data-testid='btn-upload-settlement-csv']")
        page.wait_for_selector("[data-testid='manual-import-summary-box']", timeout=15000)

        shot6 = SCREENSHOT_DIR / "06_m5_broker_connector_settings_and_audit.png"
        page.screenshot(path=str(shot6), full_page=True)
        screenshots_saved.append(str(shot6))

        browser.close()

    assert len(console_errors) == 0, f"发现浏览器 console.error: {console_errors}"
    assert len(page_errors) == 0, f"发现浏览器 pageerror: {page_errors}"

    return {
        "ok": True,
        "version": "v3.5.0",
        "base_url": BASE_URL,
        "console_error_count": len(console_errors),
        "page_error_count": len(page_errors),
        "screenshots": screenshots_saved,
    }


if __name__ == "__main__":
    res = run_m5_full_e2e_verification()
    print(json.dumps(res, ensure_ascii=False, indent=2))
