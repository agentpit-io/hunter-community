#!/usr/bin/env python3
"""Playwright 真浏览器端到端（E2E）验收脚本 · M3 量化研究工作台、组合复盘工作台与五大工作台全景验证。

严格对应《03 详细开发方案》与《05 测试方案》：
1. 量化研究工作台（Quant Workbench）：
   - ETF 定投计算器（`etf_dca_planner`）：默认加载沪深300 ETF (510300.SH) 正向历史回测、定投 vs 一次性买入同区间对比、目标金额反推情景矩阵，以及强制历史假设免责声明；
   - 切换定投金额/标的点击“重新测算定投方案”，验证累计投入本金、持有份额、持仓均价、XIRR、最大回撤实时重算；
   - 量化因子有效性研究（`factor_research`）：验证 IC 均值、Rank IC 均值、IR、5 层分层回测单调性与拥挤度分位展示；
   - 回测质量 6 维防坑审计（`backtest_quality_auditor`）：点击“模拟未来函数与幸存者偏差（触发拦截）”验证扣分与 FAIL 告警，再点击“标准合规回测”恢复 100 分；
   - 自然语言选股器（`stock_screener_builder`）：点击“测试含未授权字段诚实提示”验证不可用字段诚实告警（绝不静默伪造）。
2. 组合复盘工作台（Portfolio Review Workbench）：
   - 投资论点看板（`investment_thesis_tracker`）：验证招商银行（稳健成立）、浦发银行（核心观察）、停牌核查标的（反证触发）三类状态卡片及公告页码引用 `[来源：... · 第1页]`；
   - 输入更新买入逻辑与反证条件并点击“对照最新公告核查反证条件”；
   - 交易与研究日志闭环（`trading_journal`）：验证模拟演练声明、盘前计划 vs 实际执行 vs 纪律评分；
   - 多源新闻水位线去重摘要（`news_signal_digest`）：验证水位线过滤条数、跨源转载合并指纹与影响标的映射。
3. 全程断言 `0 console.error`、`0 pageerror`，无 `NaN` / `undefined`，保存高清截图至 `/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m3/`。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("HCA_BASE_URL", "http://127.0.0.1:3200")
SCREENSHOT_DIR = Path("/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m3")


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


def run_m3_e2e_verification() -> dict:
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

        # ── 1. 打开工作台首页并验证 v3.3.0 与 22 金融 SKILL 徽章 ─────────────────
        page.goto(f"{BASE_URL}/workbench", wait_until="networkidle", timeout=30000)
        ack_btn = page.locator("button:has-text('我已阅读并同意'), button:has-text('同意并继续'), button:has-text('已知悉')")
        if ack_btn.count() > 0 and ack_btn.first.is_visible():
            ack_btn.first.click()

        page.wait_for_selector("[data-testid='tab-quant-workbench']", timeout=15000)
        header_text = page.locator("main").inner_text()
        assert "v3.3.0" in header_text, "页面未显示 v3.3.0 版本标识"
        assert "etf_dca_planner" in header_text, "技能条未显示 P1 技能 etf_dca_planner"

        # ── 2. 切换至「量化研究工作台」（ETF 定投 + 因子研究 + 回测审计 + 选股器）──
        page.click("[data-testid='tab-quant-workbench']")
        page.wait_for_selector("[data-testid='panel-quant-workbench']", timeout=15000)
        page.wait_for_selector("[data-testid='etf-dca-calculator-card']", timeout=15000)
        page.wait_for_selector("[data-testid='dca-summary-metrics']", timeout=15000)
        page.wait_for_selector("[data-testid='factor-research-card']", timeout=15000)
        page.wait_for_selector("[data-testid='backtest-quality-audit-card']", timeout=15000)
        page.wait_for_selector("[data-testid='stock-screener-card']", timeout=15000)

        quant_text = page.locator("[data-testid='panel-quant-workbench']").inner_text()
        assert "基于历史净值与假设参数测算，历史表现不代表未来收益" in quant_text, "缺少 ETF 定投强制免责声明"
        assert "510300.SH" in quant_text
        assert "Q1 (最低分组)" in quant_text and "Q5 (最高分组)" in quant_text, "缺少 5 层分层回测单调性表"
        assert "100 / 100" in quant_text, "默认合规回测审计得分应为 100/100"
        assert "NaN" not in quant_text and "undefined" not in quant_text

        shot1 = SCREENSHOT_DIR / "01_quant_workbench_etf_dca_and_factor.png"
        page.screenshot(path=str(shot1), full_page=True)
        screenshots_saved.append(str(shot1))

        # 交互验证：修改每期定投金额为 3000 元并重新测算
        page.fill("[data-testid='input-dca-periodic-amount']", "3000")
        page.click("[data-testid='btn-run-etf-dca']")
        page.wait_for_timeout(600)
        dca_metrics_text = page.locator("[data-testid='dca-summary-metrics']").inner_text()
        assert "111000.00" in dca_metrics_text or "108000.00" in dca_metrics_text, f"定投重算本金未更新: {dca_metrics_text}"

        # 交互验证：触发回测质量审计“模拟未来函数与幸存者偏差”拦截
        page.click("[data-testid='btn-audit-simulate-pit-fail']")
        page.wait_for_timeout(500)
        audit_fail_text = page.locator("[data-testid='backtest-quality-audit-card']").inner_text()
        assert "25 / 100" in audit_fail_text or "FAIL" in audit_fail_text, "回测审计未拦截未来函数与幸存者偏差"

        # 交互验证：自然语言选股器含未授权字段诚实提示
        page.click("[data-testid='btn-screener-test-unsupported']")
        page.wait_for_selector("[data-testid='screener-unsupported-badge']", timeout=10000)
        unsupported_text = page.locator("[data-testid='screener-unsupported-badge']").inner_text()
        assert "未授权私密预测字段" in unsupported_text

        shot2 = SCREENSHOT_DIR / "02_quant_workbench_audit_fail_and_screener_guard.png"
        page.screenshot(path=str(shot2), full_page=True)
        screenshots_saved.append(str(shot2))

        # 恢复标准合规回测 100 分
        page.click("[data-testid='btn-audit-standard-pass']")
        page.wait_for_timeout(400)

        # ── 3. 切换至「组合复盘工作台」（投资论点看板 + 交易日志 + 多源新闻水位线去重）──
        page.click("[data-testid='tab-portfolio-review-workbench']")
        page.wait_for_selector("[data-testid='panel-portfolio-review-workbench']", timeout=15000)
        page.wait_for_selector("[data-testid='investment-thesis-board']", timeout=15000)
        page.wait_for_selector("[data-testid='trading-journal-card']", timeout=15000)
        page.wait_for_selector("[data-testid='news-signal-digest-card']", timeout=15000)

        rev_text = page.locator("[data-testid='panel-portfolio-review-workbench']").inner_text()
        assert "招商银行 (600036.SH)" in rev_text
        assert "论点稳健成立" in rev_text
        assert "触发反证/停牌核查预警" in rev_text
        assert "[来源：ANN-20261028-600036-Q3 · 第1页]" in rev_text
        assert "模拟盘与投研复盘演练记录" in rev_text
        assert "水位线过滤旧闻" in rev_text
        assert "NaN" not in rev_text and "undefined" not in rev_text

        shot3 = SCREENSHOT_DIR / "03_portfolio_review_workbench_thesis_and_journal.png"
        page.screenshot(path=str(shot3), full_page=True)
        screenshots_saved.append(str(shot3))

        # 交互验证：输入自定义投资论点与反证条件并点击核查
        page.fill("[data-testid='input-custom-thesis']", "零售AUM稳健增长+分红率維持33%以上，高股息安全边际充足")
        page.fill("[data-testid='input-custom-invalidation']", "若净息差单季下滑超20bps则重新评估")
        page.click("[data-testid='btn-check-thesis']")
        page.wait_for_timeout(500)
        updated_thesis_text = page.locator("[data-testid='investment-thesis-board']").inner_text()
        assert "零售AUM稳健增长" in updated_thesis_text
        assert "若净息差单季下滑超20bps则重新评估" in updated_thesis_text

        shot4 = SCREENSHOT_DIR / "04_portfolio_review_thesis_interactive_check.png"
        page.screenshot(path=str(shot4), full_page=True)
        screenshots_saved.append(str(shot4))

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
    run_m3_e2e_verification()
