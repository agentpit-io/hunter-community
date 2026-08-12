# 原生模式（免 Docker）

在 Windows 上不装 Docker 也能跑 Hunter Community。双击项目根目录的 `start.bat` 即可。

```
git clone https://github.com/agentpit-io/hunter-community
cd hunter-community
双击 start.bat          ← 就这一步，等着就行
```

跑完浏览器自动打开 `http://localhost:3100`，首次访问引导注册（**第一个注册的账号自动是管理员**）。

---

## 为什么有这个模式

Docker 模式在 Windows 上的门槛是**必须重启电脑**：Docker Desktop 依赖 WSL2，
而 `wsl --install` 强制要求重启，部分机器还得进 BIOS 开虚拟化。这一步没法脚本化。

原生模式把 Python / Node / opencode / PostgreSQL 的**绿色版**下载到项目里的
`.runtime/`，全程不需要管理员权限、不写注册表、不改系统 PATH、不重启。

**卸载 = 删掉 `.runtime/` 文件夹。** 系统里不留任何痕迹。

## 装了什么、放在哪

| 组件 | 版本 | 位置 |
|---|---|---|
| Python | 3.11.9（embeddable）| `.runtime/python/` |
| Node.js | **22.12.0**（不能降到 20，见下）| `.runtime/node/` |
| opencode | v1.18.16（官方 Windows 二进制）| `.runtime/opencode/` |
| PostgreSQL | 16.4（免安装版）| `.runtime/postgres/` + `.runtime/pgdata/` |
| Redis | **不装** —— 换成进程内内存实现 | 见 `sitecustomize.py` |

首次约 2.5G（含依赖），之后重跑会跳过已下载的部分。

## 端口

| 服务 | 端口 |
|---|---|
| Web | 3100 ← 浏览器开这个 |
| API | 8100 |
| PostgreSQL | 5442 |
| opencode | 3901 |

被占用时脚本会直接报错并指出占用进程，改 `.env` 里的端口即可。

## 常用命令

```powershell
# 启动（也用于改完 .env 后重启）
.\start.bat

# 停止全部服务
powershell -ExecutionPolicy Bypass -File scripts\native\stop.ps1

# 看日志
.runtime\logs\
```

## 要配什么

**只看行情 / 自选股 / 持仓：什么都不用配**（中国大陆网络下 akshare 直接可用）。

**想用 chat**：编辑 `.env` 三行，然后重启

```bash
LLM_BASE_URL=https://api.openai.com/v1     # 或任何 OpenAI 兼容网关
LLM_API_KEY=sk-xxxx
LLM_DEFAULT_MODEL=gpt-4o-mini
```

**海外网络**：akshare 调的是国内站点会被拒，改 `DATA_SOURCE_PROVIDER=yfinance`。

---

## 实现上的坑（改这些脚本前先读，都是实测踩出来的）

按踩到的顺序：

1. **`.ps1` 必须存成 UTF-8 with BOM。** Windows PowerShell 5.1 默认按 ANSI/GBK 读脚本，
   中文注释会变乱码进而报语法错。改完记得保持 BOM。
2. **`uvloop` 在 Windows 装不了**（Unix-only，无 wheel 也无法编译）。它只是 uvicorn 的
   可选加速依赖，脚本里的 `$WinSkip` 会在安装前把它从 requirements 里滤掉。
3. **`pg_ctl start` 不能用 `-Wait`。** 它 fork 出的 postgres 服务端继承了重定向的
   stdout/stderr 并一直持有，调用方会一直等（实测卡满 5 分钟，而数据库其实早就起来了）。
   改成发出去就不管，靠轮询端口判断成败。
4. **不要用 `& 原生命令 2>&1 | Out-File`。** PS 5.1 会把原生程序 stderr 的每一行包装成
   ErrorRecord，配合 `$ErrorActionPreference='Stop'` 直接终止脚本 —— npm 正是把进度写
   stderr 的。统一走 `RunLogged`（Start-Process + 文件重定向）。
5. **`Start-Process -ArgumentList` 接数组时不给含空格的项加引号。** 会导致
   `-o "-p 5442 -h 127.0.0.1"` 被拆散，也会让带空格的项目路径全断。用 `QuoteArgs` 处理。
6. **必须 `npm ci`，不能 `npm install`。** 后者会重解依赖树拉到更新的传递依赖
   （实测拉进纯 ESM 的 `@exodus/bytes` 导致构建失败），而且**会改写仓库里的
   `package-lock.json`**，让用户 clone 下来跑一次就产生脏改动。
7. **不能用 `.next/BUILD_ID` 判断构建成功。** 预渲染失败时 BUILD_ID 已经写下了，但
   `.next` 是残缺的（缺 `prerender-manifest.json`），`next start` 会直接 ENOENT 崩掉。
   判据用 `prerender-manifest.json`，且重建前先清掉上次残局。
8. **Node 必须 22+。** 依赖链 `isomorphic-dompurify → jsdom → html-encoding-sniffer →
   @exodus/bytes` 最后那个是纯 ESM，`require()` 它需要 Node 22.12+ 的 require(esm) 支持。
   Node 20 下 `next build` 预渲染首页必然 `ERR_REQUIRE_ESM`。
9. **web 直接起 `next`，不要经过 npm。** `node → npm → next-server` 三层进程里，真正占
   3100 端口的是最里层的孙进程，按 PID 停止时容易漏掉，下次启动就报端口占用。
