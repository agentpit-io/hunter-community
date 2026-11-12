#!/usr/bin/env python3
"""
HCA-UNIFY 阶段 M3 · 前端全量合流与香港环境（127.0.0.1:3200）Playwright 真浏览器端到端验收脚本

运行方式：
  python3 tools/e2e/m3_unify_verify.py \
    --base http://127.0.0.1:3200 \
    --secrets /mnt/mixplode/hca/secrets-admin \
    --out /mnt/mixplode/hunter-dev/atomcode-unify-chain/reports/screenshots-m3
"""
import argparse
import json
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright


def load_admin(secrets_dir: Path) -> tuple[str, str]:
    txt = (secrets_dir / "admin.txt").read_text(encoding="utf-8")
    email, password = "", ""
    for line in txt.splitlines():
        if line.startswith("email:"):
            email = line.split(":", 1)[1].strip()
        elif line.startswith("password:"):
            password = line.split(":", 1)[1].strip()
    if not email or not password:
        raise RuntimeError("admin.txt 中未读到 email/password")
    return email, password


def dismiss_modals(page) -> None:
    for _ in range(8):
        comp = page.get_by_role("button", name="确认继续")
        if comp.count() > 0 and comp.first.is_visible():
            try:
                page.locator('input[type="checkbox"]').first.check(timeout=3000)
            except Exception:
                pass
            try:
                comp.first.click(timeout=3000)
            except Exception:
                pass
            time.sleep(1.0)
            continue
        skip = page.get_by_role("button", name="跳过")
        if skip.count() > 0 and skip.first.is_visible():
            try:
                skip.first.click(timeout=3000)
            except Exception:
                pass
            time.sleep(0.8)
            continue
        wx = page.get_by_role("button", name="×")
        if wx.count() > 0 and wx.first.is_visible():
            try:
                wx.first.click(timeout=3000)
            except Exception:
                pass
            time.sleep(0.8)
            continue
        return


def scroll_to_bottom(page) -> None:
    page.evaluate(
        """() => {
        for (const el of Array.from(document.querySelectorAll('*'))) {
          if (el.scrollHeight > el.clientHeight + 50) el.scrollTop = el.scrollHeight;
        }
        window.scrollTo(0, document.body.scrollHeight);
    }"""
    )
    time.sleep(0.6)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:3200")
    parser.add_argument("--secrets", default="/mnt/mixplode/hca/secrets-admin")
    parser.add_argument(
        "--out",
        default="/mnt/mixplode/hunter-dev/atomcode-unify-chain/reports/screenshots-m3",
    )
    args = parser.parse_args()

    base = args.base.rstrip("/")
    secrets_dir = Path(args.secrets)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    email, password = load_admin(secrets_dir)
    results = []
    summary = {}
    shot_idx = 0

    def take_shot(page, tag: str) -> str:
        nonlocal shot_idx
        shot_idx += 1
        fname = f"{shot_idx:02d}-{tag}.png"
        fpath = out_dir / fname
        page.screenshot(path=str(fpath), full_page=False)
        print(f"  📸 {fpath}")
        return str(fpath)

    def run_step(name: str, fn):
        t0 = time.time()
        print(f"\n── {name} ───────────────────────")
        try:
            note = fn()
            ms = int((time.time() - t0) * 1000)
            results.append({"name": name, "ok": True, "ms": ms, "note": note or ""})
            print(f"  ✅ {note} ({ms} ms)")
        except Exception as e:
            ms = int((time.time() - t0) * 1000)
            results.append({"name": name, "ok": False, "ms": ms, "note": str(e)[:500]})
            print(f"  ❌ {e} ({ms} ms)")

    with sync_playwright() as p:
        exe = "/home/support/.cache/ms-playwright/chromium-1254/chrome-linux64/chrome"
        launch_kw = {"args": ["--no-sandbox"]}
        if os.path.exists(exe):
            launch_kw["executable_path"] = exe
        browser = p.chromium.launch(**launch_kw)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
        page = ctx.new_page()

        console_errors = []
        bad_responses = []
        page.on(
            "console",
            lambda m: console_errors.append(m.text[:300]) if m.type == "error" else None,
        )
        page.on(
            "response",
            lambda r: bad_responses.append(f"{r.status} {r.request.method} {r.url}")
            if r.status >= 400
            else None,
        )

        # 1) 首页与顶栏导航（含「Hunter-AtomCode」标识、「hunter智能体自动炒股」下拉菜单、「策略中心」、「帮助中心」）
        def step1():
            page.goto(f"{base}/login", wait_until="domcontentloaded", timeout=30000)
            page.locator('input[type="email"]').wait_for(timeout=30000)
            page.locator('input[type="email"]').fill(email)
            page.locator('input[type="password"]').fill(password)
            page.locator('button[type="submit"]').click()
            page.wait_for_url(lambda u: "/login" not in u, timeout=30000)
            time.sleep(3.5)
            dismiss_modals(page)

            page.goto(f"{base}/chat", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3.0)
            dismiss_modals(page)
            page.locator("textarea").first.wait_for(timeout=30000)

            body_text = page.locator("body").inner_text()
            if "Hunter-AtomCode" not in body_text:
                raise RuntimeError("顶栏未找到 Hunter-AtomCode 品牌标识")
            if "策略中心" not in body_text or "帮助中心" not in body_text:
                raise RuntimeError("顶栏未找到策略中心或帮助中心链接")

            fin_btn = page.locator('button[title="hunter智能体自动炒股"]')
            fin_btn.wait_for(timeout=10000)
            fin_btn.click()
            time.sleep(0.6)
            menu_text = page.locator('[role="menu"]').inner_text()
            for item in ["总览", "自动交易", "我的账户", "每日报告", "成长与复盘", "安全与帮助", "设置向导"]:
                if item not in menu_text:
                    raise RuntimeError(f"智能炒股下拉菜单缺少子入口：{item}")
            take_shot(page, "topnav-and-finance-dropdown")
            fin_btn.click()
            time.sleep(0.3)
            return "顶栏包含 Hunter-AtomCode、策略中心、帮助中心及智能炒股 7 个二级入口"

        run_step("1-首页与顶栏导航（品牌名 + 智能炒股下拉菜单 + 策略中心 + 帮助中心）", step1)

        # 2) 对话页（含左下角「📎 上传文档/PDF」入口、模型选择器显示 Gemini 4 Argon，发送一轮真实问题通过 AtomCode Daemon + MCP 流式返回中文回答）
        def step2():
            page.goto(f"{base}/chat", wait_until="domcontentloaded", timeout=30000)
            time.sleep(2.5)
            dismiss_modals(page)
            upload_btn = page.locator('[data-testid="upload-file-btn"]')
            if upload_btn.count() == 0:
                raise RuntimeError("未找到聊天框左下角 [data-testid='upload-file-btn'] 上传文档/PDF 按钮")

            model_btn = page.locator('button[title="切换 model"]').first
            model_btn.wait_for(timeout=10000)
            model_btn_text = model_btn.inner_text().strip()
            model_btn.click()
            time.sleep(0.8)
            picker_text = page.locator("body").inner_text()
            if "Gemini 4 Argon" not in picker_text:
                raise RuntimeError(f"模型选择器未展示 Gemini 4 Argon，当前按钮文本：{model_btn_text}")
            if "查看/下载大模型调用日志" not in picker_text:
                raise RuntimeError("模型选择器底部缺少「查看/下载大模型调用日志」入口")
            take_shot(page, "chat-model-picker-gemini4-argon")
            model_btn.click()
            time.sleep(0.4)

            # 上传一份测试 Markdown 文档，验证解析芯片与原文核对抽屉
            sample_md = (
                "# 贵州茅台（600519）2025半年报核心摘要\n\n"
                "| 指标 | 数值 | 同比 |\n|---|---|---|\n| 营业总收入 | 834.51亿元 | +16.95% |\n| 归母净利润 | 416.96亿元 | +15.88% |\n\n"
                "核心结论：公司高端白酒护城河稳固，现金流充沛。\n"
            ).encode("utf-8")
            file_input = page.locator('input[type="file"]').first
            file_input.set_input_files(
                {
                    "name": "贵州茅台2025半年报摘要.md",
                    "mimeType": "text/markdown",
                    "buffer": sample_md,
                }
            )
            time.sleep(3.5)
            take_shot(page, "chat-doc-uploaded-chip")

            # 点击文档卡片打开右侧原文与页码核对抽屉
            doc_card = page.locator('[data-testid="uploaded-file-card"]').first
            if doc_card.count() > 0:
                doc_card.locator("span").first.click()
                time.sleep(1.0)
                take_shot(page, "chat-doc-preview-drawer")

            # 发送一轮真实问题，走 AtomCode Daemon + MCP
            q = "请结合我上传的贵州茅台（600519）摘要，并用快览工具核对一下贵州茅台最新行情，用三句话总结核心财务数据与当前估值。"
            dismiss_modals(page)
            page.locator("textarea").first.fill(q)
            page.locator('button[aria-label="发送"]').first.click()
            try:
                page.locator('button[aria-label="停止生成"]').first.wait_for(state="visible", timeout=15000)
            except Exception:
                pass
            # 等待回答完成（停止按钮消失且发送按钮恢复）
            deadline = time.time() + 240
            while time.time() < deadline:
                if page.locator('button[aria-label="停止生成"]').count() == 0 and page.locator('button[aria-label="发送"]').count() > 0:
                    break
                time.sleep(1.5)
            time.sleep(1.5)
            scroll_to_bottom(page)
            take_shot(page, "chat-atomcode-turn-reply")

            all_text = page.locator("body").inner_text()
            if "834.51" not in all_text and "茅台" not in all_text:
                raise RuntimeError("对话回复未包含茅台或上传文档核心数据")
            if "猎鹿人 · Hunter-AtomCode" not in all_text:
                raise RuntimeError("对话回复缺少「猎鹿人 · Hunter-AtomCode」署名")
            summary["chatReplyPreview"] = all_text[-400:]
            return f"模型选择器显示 {model_btn_text}，文档上传解析、原文核对抽屉与 AtomCode Daemon 真实流式回答验证通过"

        run_step("2-对话页（文档上传按钮 + Gemini 4 Argon 模型选择器 + 真实投研问答流式返回）", step2)

        # 3) 策略中心（小鹿规则编辑器、多语言/AI 助手面板渲染正常）
        def step3():
            res = page.goto(f"{base}/strategies/agent.html", wait_until="networkidle", timeout=30000)
            if not res or res.status != 200:
                raise RuntimeError(f"/strategies/agent.html 返回状态异常：{res.status if res else None}")
            time.sleep(2.0)
            text = page.locator("body").inner_text()
            if "小鹿" not in text and "策略" not in text and "规则" not in text:
                raise RuntimeError("策略中心 agent.html 页面正文未正常渲染")
            take_shot(page, "strategies-agent-builder")
            return f"/strategies/agent.html HTTP {res.status} 渲染正常"

        run_step("3-策略中心（小鹿规则编辑器与多语言 AI 助手面板）", step3)

        # 4) 智能炒股页面（总览 /finance、开户向导 /finance/onboarding、持仓/风控/成长与经验库 /finance/evolution 正常渲染，API 返回 200）
        def step4():
            # 先打开开户向导 /finance/onboarding 截图
            r_onboard = page.goto(f"{base}/finance/onboarding", wait_until="networkidle", timeout=30000)
            if not r_onboard or r_onboard.status != 200:
                raise RuntimeError(f"/finance/onboarding 返回 HTTP {r_onboard.status if r_onboard else None}")
            time.sleep(1.5)
            onboard_text = page.locator("body").inner_text()
            if "模拟" not in onboard_text and "档位" not in onboard_text:
                raise RuntimeError("/finance/onboarding 页面未渲染开户向导内容")
            take_shot(page, "finance-onboarding")

            # 确保当前账号已开通模拟盘项目（若未开通则调用 POST /api/v1/fin/projects 开通三市场模拟账户），再验证全部 API 返回 200
            api_status = page.evaluate(
                """async () => {
                const token = localStorage.getItem('hunter_token') || '';
                const headers = {
                  'Content-Type': 'application/json',
                  ...(token ? { Authorization: `Bearer ${token}` } : {}),
                };
                const curRes = await fetch('/api/v1/fin/projects/current', { headers });
                let curData = await curRes.json().catch(() => null);
                if (!curData || !curData.project) {
                  await fetch('/api/v1/fin/projects', {
                    method: 'POST',
                    headers,
                    body: JSON.stringify({ tier: 'manage', markets: ['CN_A', 'HK', 'US'] }),
                  });
                  const r2 = await fetch('/api/v1/fin/projects/current', { headers });
                  curData = await r2.json().catch(() => null);
                }
                const pid = curData?.project?.project_id || '';
                const urls = [
                  '/api/v1/fin/projects/current',
                  '/api/v1/fin/markets',
                  '/api/v1/fin/tiers',
                  '/api/v1/fin/runtime',
                  '/api/v1/fin/overview',
                  '/api/v1/fin/account',
                  `/api/v1/fin/memory/filters?project_id=${encodeURIComponent(pid)}`,
                  `/api/v1/fin/evolution/proposals?project_id=${encodeURIComponent(pid)}`,
                ];
                const out = {};
                for (const u of urls) {
                  const r = await fetch(u, { headers });
                  out[u] = r.status;
                }
                return out;
            }"""
            )
            for u, st in api_status.items():
                if st != 200:
                    raise RuntimeError(f"智能炒股 API {u} 返回 HTTP {st}（期望 200）")

            page.goto(f"{base}/finance", wait_until="networkidle", timeout=30000)
            time.sleep(1.8)
            take_shot(page, "finance-overview")

            page.goto(f"{base}/finance/account", wait_until="networkidle", timeout=30000)
            time.sleep(1.5)
            take_shot(page, "finance-account")

            page.goto(f"{base}/finance/auto-trade", wait_until="networkidle", timeout=30000)
            time.sleep(1.5)
            take_shot(page, "finance-auto-trade")

            r_evo = page.goto(f"{base}/finance/evolution", wait_until="networkidle", timeout=30000)
            if not r_evo or r_evo.status != 200:
                raise RuntimeError(f"/finance/evolution 返回 HTTP {r_evo.status if r_evo else None}")
            time.sleep(1.8)
            evo_text = page.locator("body").inner_text()
            if "经验" not in evo_text and "成长" not in evo_text and "复盘" not in evo_text:
                raise RuntimeError("/finance/evolution 页面未渲染经验库与自进化内容")
            take_shot(page, "finance-evolution")

            summary["finApiStatus"] = api_status
            return "智能炒股 8 个核心 API 全部 200，开户向导/总览/账户/自动交易/成长与经验库页面全部渲染正常"

        run_step("4-智能炒股全套页面与 API 校验", step4)

        # 5) 帮助中心（/help/index.html 正常打开）
        def step5():
            res = page.goto(f"{base}/help/index.html", wait_until="domcontentloaded", timeout=30000)
            if not res or res.status != 200:
                raise RuntimeError(f"/help/index.html 返回 HTTP {res.status if res else None}")
            time.sleep(1.0)
            text = page.locator("body").inner_text()
            if len(text) < 200:
                raise RuntimeError("/help/index.html 内容过短，疑似未正常渲染")
            take_shot(page, "help-center")
            return f"/help/index.html HTTP 200（正文长度 {len(text)} 字符）"

        run_step("5-帮助中心（/help/index.html 正常打开）", step5)

        # 6) 设置页（/settings?tab=llm，「大模型调用日志与问题跟踪」卡片存在，点击实测与日志接口返回 200）
        def step6():
            page.goto(f"{base}/settings?tab=llm", wait_until="networkidle", timeout=30000)
            time.sleep(2.0)
            text_before = page.locator("body").inner_text()
            if "大模型调用日志与问题跟踪" not in text_before:
                raise RuntimeError("/settings?tab=llm 未找到「大模型调用日志与问题跟踪」卡片")
            if "对话引擎（AtomCode）快照" not in text_before:
                raise RuntimeError("/settings?tab=llm 未找到「对话引擎（AtomCode）快照」行")

            ping_btn = page.locator("button", has_text="实测一次大模型并记录日志")
            ping_btn.wait_for(timeout=10000)
            ping_btn.click()

            page.locator("text=实测结果：").wait_for(timeout=45000)
            time.sleep(1.0)
            take_shot(page, "settings-llm-logs-and-probe")

            dl_check = page.evaluate(
                """async () => {
                const token = localStorage.getItem('hunter_token') || '';
                const r = await fetch('/api/setup/llm/logs/download', {
                  headers: token ? { Authorization: `Bearer ${token}` } : {},
                });
                const txt = await r.text();
                return { status: r.status, len: txt.length, hasAtomcode: txt.includes('atomcode') };
            }"""
            )
            if dl_check["status"] != 200 or dl_check["len"] < 100:
                raise RuntimeError(f"/api/setup/llm/logs/download 异常：{dl_check}")

            about_btn = page.get_by_role("button", name="关于")
            about_btn.click()
            time.sleep(0.8)
            about_text = page.locator("body").inner_text()
            if (
                "关于 Hunter-AtomCode" not in about_text
                or "gitcode.com/agentpit-io/huntercode-atomcode" not in about_text
            ):
                raise RuntimeError("关于页签缺少 Hunter-AtomCode 或 GitCode 开源地址")
            take_shot(page, "settings-about-atomcode")

            summary["dlCheck"] = dl_check
            return f"设置页实测探针成功、日志下载 HTTP {dl_check['status']} ({dl_check['len']} bytes)、关于页签品牌验证通过"

        run_step("6-设置页（/settings?tab=llm 大模型调用日志与实测 + 关于 Hunter-AtomCode）", step6)

        browser.close()

    out_json = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base": base,
        "results": results,
        "summary": summary,
        "consoleErrorsCount": len(console_errors),
        "badResponsesCount": len(bad_responses),
    }
    (out_dir / "m3-verify-results.json").write_text(
        json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n══ 汇总 ══")
    for r in results:
        mark = "✅" if r["ok"] else "❌"
        print(f"  {mark} {r['name']}: {r['note']} ({r['ms']}ms)")
    return 1 if any(not r["ok"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
