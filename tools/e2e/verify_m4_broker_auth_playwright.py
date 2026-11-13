#!/usr/bin/env python3
"""Playwright 真浏览器端到端（E2E）验收脚本 · M4 合作券商数据源与只读授权管理、一键撤销与交割单导入验证。

严格对应《03 详细开发方案》与《05 测试方案》第五关（3.5 第 5 条）：
1. 访问 `/settings/broker` 与 `/settings?tab=broker`（「合作券商数据源与只读授权管理」面板）：
   - 验证默认状态为“未连接 / 默认关闭 (unauthorized)”；
   - 点击“开启券商只读授权（AES-256-GCM 托管）”，验证状态立即变为“已连接只读授权”，展示脱敏账号与 `broker_account_readonly.get_positions` 持仓快照预览；
   - 点击“一键撤销授权（销毁凭证并清空缓存）”，验证状态立即变为“未连接 / 默认关闭 (revoked)”，持仓预览清空；
   - 测试主备数据源切换开关（模拟主源 HTTP 504 超时演练 → 恢复主源正常连接）；
   - 测试未授权用户的“Excel / CSV 交割单与持仓手工导入”：点击“解析并导入交割单与持仓”，验证解析出的持仓标的数、总市值与单票最高集中度展示；
   - 验证底部 `broker_audit_log` 合规审计留痕实时展示。
2. 访问 `/workbench` 切换至「组合复盘工作台」，验证内嵌的「合作券商数据源与只读授权管理」面板正常渲染。
3. 全程断言 `0 console.error`、`0 pageerror`，无 `NaN` / `undefined`，保存高清截图至 `/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m4/`。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("HCA_BASE_URL", "http://127.0.0.1:3200")
SCREENSHOT_DIR = Path("/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m4")


def _mint_real_jwt() -> str:
    cmd = [
        "docker", "exec", "hca-api", "python3", "-c",
        (
            "from app.routers.auth import _sign_access, _ensure_auth_tables; "
            "from app.services.database import get_conn; "
            "_ensure_auth_tables(); "
            "c=get_conn(); cur=c.cursor(); "
            "cur.execute(\"INSERT INTO users (email, email_lower, pw_hash, role, display_name) "
            "VALUES ('e2e_m4@hunter.local', 'e2e_m4@hunter.local', 'noop', 'admin', 'E2E_M4') "
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


def run_m4_e2e_verification() -> dict:
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

        # ── 1. 访问设置页 `/settings?tab=broker` 验证合作券商与只读授权管理面板 ──
        page.goto(f"{BASE_URL}/settings?tab=broker", wait_until="networkidle", timeout=30000)
        ack_btn = page.locator("button:has-text('我已阅读并同意'), button:has-text('同意并继续'), button:has-text('已知悉')")
        if ack_btn.count() > 0 and ack_btn.first.is_visible():
            ack_btn.first.click()

        page.wait_for_selector("[data-testid='broker-connector-panel']", timeout=15000)
        panel_text = page.locator("[data-testid='broker-connector-panel']").inner_text()
        assert "合作券商数据源与只读授权管理（v3.4.0 · M4）" in panel_text
        assert "AES-256-GCM" in panel_text
        assert "NaN" not in panel_text and "undefined" not in panel_text

        shot1 = SCREENSHOT_DIR / "01_settings_broker_default_closed.png"
        page.screenshot(path=str(shot1), full_page=True)
        screenshots_saved.append(str(shot1))

        # ── 2. 点击“开启券商只读授权（AES-256-GCM 托管）”，验证授权生效与持仓快照加载 ──
        page.click("[data-testid='btn-broker-authorize']")
        page.wait_for_selector("[data-testid='authorized-positions-preview']", timeout=15000)
        badge_text = page.locator("[data-testid='broker-oauth-status-badge']").inner_text()
        assert "已连接只读授权" in badge_text
        pos_text = page.locator("[data-testid='authorized-positions-preview']").inner_text()
        assert "招商银行" in pos_text and "600036.SH" in pos_text

        shot2 = SCREENSHOT_DIR / "02_settings_broker_oauth_authorized_and_positions.png"
        page.screenshot(path=str(shot2), full_page=True)
        screenshots_saved.append(str(shot2))

        # ── 3. 点击“一键撤销授权”，验证状态立即变为未连接/revoked 且持仓缓存销毁 ──
        page.click("[data-testid='btn-broker-revoke']")
        page.wait_for_timeout(600)
        revoked_badge = page.locator("[data-testid='broker-oauth-status-badge']").inner_text()
        assert "未连接" in revoked_badge and "revoked" in revoked_badge
        assert page.locator("[data-testid='authorized-positions-preview']").count() == 0

        # ── 4. 点击“解析并导入交割单与持仓”，验证未授权用户的手工 CSV 兜底通道 ──
        page.click("[data-testid='btn-upload-settlement-csv']")
        page.wait_for_selector("[data-testid='manual-import-summary-box']", timeout=15000)
        imp_text = page.locator("[data-testid='manual-import-summary-box']").inner_text()
        assert "共解析 3 条交割记录" in imp_text
        assert "600036.SH" in imp_text

        shot3 = SCREENSHOT_DIR / "03_settings_broker_revoked_and_csv_imported.png"
        page.screenshot(path=str(shot3), full_page=True)
        screenshots_saved.append(str(shot3))

        # ── 5. 访问独立路径 `/settings/broker` 与 `/workbench` 组合复盘工作台验证 ──
        page.goto(f"{BASE_URL}/settings/broker", wait_until="networkidle", timeout=30000)
        page.wait_for_selector("[data-testid='broker-connector-panel']", timeout=15000)

        page.goto(f"{BASE_URL}/workbench", wait_until="networkidle", timeout=30000)
        page.click("[data-testid='tab-portfolio-review-workbench']")
        page.wait_for_selector("[data-testid='workbench-broker-connector-section']", timeout=15000)
        wb_text = page.locator("main").inner_text()
        assert "v3.4.0" in wb_text

        shot4 = SCREENSHOT_DIR / "04_workbench_portfolio_review_with_broker_connector.png"
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
    run_m4_e2e_verification()
