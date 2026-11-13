# 参与贡献 · Contributing to HunterCode

[中文](#中文) · [English](#english)

---

## 中文

感谢你愿意为 HunterCode · Community Edition 出力。**最简单的贡献是一个 SKILL** —— 懂一种分析方法、会写 Markdown 就够了,不需要读懂代码。

### 三种贡献方式

| 方式 | 需要什么 | 从哪开始 |
|---|---|---|
| **① 写 SKILL** | 会写 Markdown,懂一种分析方法 | 带 [`skill-wanted`](https://github.com/agentpit-io/hunter-community/issues?q=is%3Aissue+is%3Aopen+label%3Askill-wanted) 标签的 issue,或你自己的方法论 |
| **② 文档 / 翻译 / 模板** | 会用 git | 带 [`good first issue`](https://github.com/agentpit-io/hunter-community/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) 标签的 issue |
| **③ 代码** | Python(FastAPI)或 TypeScript(Next.js) | 带 [`help wanted`](https://github.com/agentpit-io/hunter-community/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) 标签的 issue |

想做的事情没有对应 issue?改动较大(新功能、改架构、改数据库)时,请先开 issue 或到 [讨论区](https://github.com/agentpit-io/hunter-community/discussions) 说一下思路,避免做完才发现方向不合适。修错别字、补文档、修明显的 bug 可以直接提 PR。

### 本地开发

```bash
git clone https://github.com/<你的账号>/hunter-community
cd hunter-community
cp .env.example .env          # 至少填 JWT_SECRET 和大模型三项,见 README「5 分钟跑起来」
docker compose up -d
```

改完后怎么生效:

| 改了什么 | 怎么生效 |
|---|---|
| `skills/` 或 `user-skills/` 下的 SKILL | `docker compose restart opencode api`(opencode 只在启动时扫描一次,约 50 秒),再跑 `python scripts/check_skill_sync.py` 确认加载数量一致 |
| `scripts/opencode-mcp/` 下挂载进容器的 MCP / 插件 | `docker compose restart opencode` |
| `scripts/llm-shim/shim.py` | `docker compose restart llm-shim` |
| `.env` | `docker compose up -d`(`restart` 不会重读 `.env`) |
| `apps/api/` 代码 | `docker compose build api && docker compose up -d api` |
| `apps/web/` 代码 | `docker compose build web && docker compose up -d web` |

提 PR 前在本地跑和 CI 相同的检查(在仓库根目录依次执行;CI 用 Python 3.11 与 Node 22):

```bash
# 后端
cd apps/api
pip install -r requirements-dev.txt                       # 运行时依赖 + pytest
python -m compileall -q app main.py
python -c "import main"                                   # 导入冒烟:API 能不能起来
python -m pytest                                          # apps/api/tests 全部用例
cd ../..
python -m unittest discover -s scripts/llm-shim -p 'test_*.py'

# SKILL
python scripts/check_skill_tools.py --offline             # SKILL 引用的工具是否真实存在

# 前端
cd apps/web
npm ci
npx tsc --noEmit
npm run build
cd public/strategies && node render_check.js              # 在 node 里真跑一遍策略中心静态页的内联脚本
```

- `apps/api/tests` 里多数文件是脚本式用例(导入即执行,最后 `sys.exit`)。`pytest` 会把这样的文件放进子进程原样跑,每个文件算一个用例;也可以单独跑:`cd apps/api && PYTHONPATH=. python tests/test_xxx.py`。`test_screen_xlayer_official.py` 要分三步跑(联网、连库、用 node),pytest 里显示为跳过,命令见该文件头。
- CI 另有 `migrations` job,在空 Postgres 上连跑两遍迁移。`python -m pytest` 里 `test_migrate.py` 的真库用例没有 `TEST_DATABASE_URL` 时会跳过;本地要对真库验证,跑 `bash apps/api/tests/manual_migrate_check.sh`(需要 docker,用法见脚本头)。
- **Windows**:`uvloop` 不支持 Windows,`requirements.txt` 已用环境标记自动跳过它,其余依赖照常安装。控制台出现 `UnicodeEncodeError` 之类的编码报错时,先设 `PYTHONUTF8=1`(PowerShell:`$env:PYTHONUTF8 = "1"`)。

### SKILL 规范

一个 SKILL 就是一个目录加一个 `SKILL.md`,采用 [Anthropic Agent Skills](https://github.com/anthropics/skills) 标准格式:

```markdown
---
name: kline_breakout
description: 判断个股是否出现有效的 K 线突破。用户问「突破了吗」「能不能追」时使用。
hunter:
  display_name: K 线突破判断
  category: 事件与筛选
  prompt_tpl: 帮我看看 {股票} 是不是有效突破
  needs_tools:
    - watchlist_stock_quickview
---

# 正文写方法论
分几步、先看什么后看什么、什么情况下结论不成立。
```

- **`description` 最重要**:模型靠它决定什么时候调用这个 SKILL。写清「做什么」和「什么时候用」。
- **`needs_tools`** 只能写真实存在的工具名,提交前跑 `python scripts/check_skill_tools.py --offline`。
- **严禁编造数据**:方法论里要求模型调用工具取数,取不到就明确说取不到,不要让模型估算、填示例值。
- **命名**:目录名与 `name` 一致,小写字母加下划线。
- **放在哪**:提 PR 的 SKILL 放 `skills/<name>/`。`user-skills/` 是每个人本机自用的目录,已被 `.gitignore` 忽略,放进去的内容不会进入 PR。
- **PR 里附一次真实运行截图**:用一个真实股票代码跑一次,贴对话截图。
- 引用或改编了别人的方法论,在 frontmatter 写 `author` 与 `license`,并确认许可证允许。

### 其他规范

- **提交信息**:[Conventional Commits](https://www.conventionalcommits.org/),如 `feat(skill): add kline_breakout`、`fix(shim): ...`、`docs: ...`。
- **中英文 README 同步**:`README.md` 与 `README_EN.md` 保持章节 1:1。只会一种语言也没关系,在 PR 里说明,维护者会补另一份。
- **用户能看到的文案用中文**;代码注释写清「为什么」。
- **不要提交任何密钥**:CI 会做密钥扫描;`.env` 已被忽略,示例值写 `sk-xxxxx`。
- **一个 PR 做一件事**,便于 review 和回滚。

### PR 流程

1. Fork → 从 `main` 切分支 → 提交 → 向 `main` 发 PR,按模板填写
2. CI 通过(密钥扫描、后端导入与测试、前端构建)
3. 至少一位维护者 review 通过后合并

### 响应时间

| 类型 | 维护者首次回复 |
|---|---|
| 部署失败类 issue | 24 小时内 |
| 其他 issue | 72 小时内 |
| PR | 1 周内给出 review 意见 |

超时没人理,可以在 issue / PR 里直接 @ 维护者提醒。

### 署名

合并后,你会出现在:README「贡献者」区、`CHANGELOG.md` 对应版本的「贡献者」小节、该版本的 Release Notes。SKILL 贡献会注明 SKILL 名称。

### 报告问题

- **Bug / 部署失败**:[新建 issue](https://github.com/agentpit-io/hunter-community/issues/new/choose),选对应模板
- **使用问题**:[讨论区问答](https://github.com/agentpit-io/hunter-community/discussions/categories/q-a)
- **安全漏洞**:不要公开 issue,见 [SECURITY.md](./SECURITY.md)

---

## English

Thanks for helping out with HunterCode · Community Edition. **The easiest contribution is a SKILL** — if you know an analysis method and can write Markdown, that's enough; no need to understand the code.

### Three ways to contribute

| Path | What you need | Where to start |
|---|---|---|
| **① Write a SKILL** | Markdown and an analysis method | Issues labeled [`skill-wanted`](https://github.com/agentpit-io/hunter-community/issues?q=is%3Aissue+is%3Aopen+label%3Askill-wanted), or your own methodology |
| **② Docs / translation / templates** | git | Issues labeled [`good first issue`](https://github.com/agentpit-io/hunter-community/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) |
| **③ Code** | Python (FastAPI) or TypeScript (Next.js) | Issues labeled [`help wanted`](https://github.com/agentpit-io/hunter-community/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) |

No issue for what you want to do? For larger changes (new features, architecture, database), open an issue or post in [Discussions](https://github.com/agentpit-io/hunter-community/discussions) first so we can align before you build. Typos, docs and obvious bug fixes can go straight to a PR.

### Local development

```bash
git clone https://github.com/<you>/hunter-community
cd hunter-community
cp .env.example .env          # at least JWT_SECRET and the three LLM settings, see README "Deploy in 5 minutes"
docker compose up -d
```

How changes take effect:

| What you changed | How to apply |
|---|---|
| SKILLs under `skills/` or `user-skills/` | `docker compose restart opencode api` (opencode scans once at startup, ~50 s), then `python scripts/check_skill_sync.py` to confirm the loaded count matches |
| MCPs / plugins mounted from `scripts/opencode-mcp/` | `docker compose restart opencode` |
| `scripts/llm-shim/shim.py` | `docker compose restart llm-shim` |
| `.env` | `docker compose up -d` (`restart` does not re-read `.env`) |
| `apps/api/` code | `docker compose build api && docker compose up -d api` |
| `apps/web/` code | `docker compose build web && docker compose up -d web` |

Run the same checks as CI before opening a PR (run them in order from the repo root; CI uses Python 3.11 and Node 22):

```bash
# Backend
cd apps/api
pip install -r requirements-dev.txt                       # runtime deps + pytest
python -m compileall -q app main.py
python -c "import main"                                   # import smoke: does the API start
python -m pytest                                          # everything under apps/api/tests
cd ../..
python -m unittest discover -s scripts/llm-shim -p 'test_*.py'

# SKILLs
python scripts/check_skill_tools.py --offline             # do referenced tools actually exist

# Frontend
cd apps/web
npm ci
npx tsc --noEmit
npm run build
cd public/strategies && node render_check.js              # actually runs the strategy pages' inline scripts in node
```

- Most files in `apps/api/tests` are script-style tests (they run on import and end with `sys.exit`). `pytest` runs each such file as-is in a subprocess and counts it as one test; you can also run one on its own: `cd apps/api && PYTHONPATH=. python tests/test_xxx.py`. `test_screen_xlayer_official.py` has to run in three steps (network access, a database, node), so pytest reports it as skipped; the commands are in its header.
- CI also has a `migrations` job that runs the migrations twice against an empty Postgres. The real-database cases in `test_migrate.py` are skipped by `python -m pytest` when `TEST_DATABASE_URL` is not set; to check against a real database locally, run `bash apps/api/tests/manual_migrate_check.sh` (needs docker; usage is in the script header).
- **Windows**: `uvloop` doesn't support Windows, so `requirements.txt` skips it with an environment marker; everything else installs as usual. If the console throws encoding errors such as `UnicodeEncodeError`, set `PYTHONUTF8=1` first (PowerShell: `$env:PYTHONUTF8 = "1"`).

### SKILL guidelines

A SKILL is a directory with a `SKILL.md` in the [Anthropic Agent Skills](https://github.com/anthropics/skills) standard format (see the Chinese section above for a full example).

- **`description` matters most**: the model uses it to decide when to call the SKILL. Say what it does and when to use it.
- **`needs_tools`** must list real tool names; run `python scripts/check_skill_tools.py --offline` before submitting.
- **Never fabricate data**: the methodology should make the model fetch data via tools and say so plainly when data is unavailable — no estimates or placeholder values.
- **Naming**: directory name equals `name`, lowercase with underscores.
- **Where**: SKILLs submitted via PR go in `skills/<name>/`. `user-skills/` is for local personal use and is git-ignored, so anything there won't be part of a PR.
- **Attach a real run screenshot** to the PR, using a real ticker.
- If you adapt someone else's methodology, set `author` and `license` in the frontmatter and make sure the license allows it.

### Other conventions

- **Commit messages**: [Conventional Commits](https://www.conventionalcommits.org/), e.g. `feat(skill): add kline_breakout`, `fix(shim): ...`, `docs: ...`.
- **Keep READMEs in sync**: `README.md` and `README_EN.md` stay section-for-section aligned. If you only write one language, say so in the PR and a maintainer will handle the other.
- **User-facing text is in Chinese**; code comments should explain why.
- **Never commit secrets**: CI runs a secret scan; `.env` is git-ignored; use `sk-xxxxx` in examples.
- **One PR, one change**, so it's easy to review and revert.

### PR process

1. Fork → branch off `main` → commit → open a PR against `main` and fill in the template
2. CI passes (secret scan, backend import and tests, frontend build)
3. Merged after approval from at least one maintainer

### Response times

| Type | First maintainer response |
|---|---|
| Deployment-failure issues | within 24 hours |
| Other issues | within 72 hours |
| PRs | review within 1 week |

If we miss that, feel free to @ a maintainer on the issue or PR.

### Credit

Once merged, you're listed in the README "Contributors" section, the "Contributors" subsection of the matching `CHANGELOG.md` version, and that version's Release Notes. SKILL contributions name the SKILL.

### Reporting

- **Bugs / deployment failures**: [new issue](https://github.com/agentpit-io/hunter-community/issues/new/choose), pick the matching template
- **Usage questions**: [Discussions Q&A](https://github.com/agentpit-io/hunter-community/discussions/categories/q-a)
- **Security vulnerabilities**: no public issues — see [SECURITY.md](./SECURITY.md)
