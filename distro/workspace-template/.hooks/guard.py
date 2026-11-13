#!/usr/bin/env python3
"""HCA 研究守卫 · PreToolUse · 本发行版唯一的硬约束。

## 为什么必须是 hook，而不是权限档

M0 §4 把四档权限逐条实测过，结论是**没有一档能在无人值守下守住投研工作区**：

* `build`（默认）：工作区内写文件**直接放行、连权限都不弹**（`write_approval.rs`
  的判定顺序里"全部目标在工作区内"排在前面）。持仓文件一手就能改。
* `accept_edits`：比 build 更松，工作区**外**的写也放行。
* `plan`：能把 4 个写类工具拦死，但**拦不住 bash 的写**（`echo x > f` 实测落盘），
  而且它会在每一轮请求尾部注入 `PLAN_MODE_REMINDER_BODY`
  （"不要写实现、给个方案然后停下来等用户批准"）—— 那是写给编码场景的，
  投研助手照做就变成"我打算去查行情，请批准"。
* `bypass`：全放行。

而 M0 §7.4 实测：**在 `bypass` 档下 PreToolUse hook 的 deny 依然生效** ——
hook middleware 排在所有审批门之前。所以本发行版取 `build` 档 + 这个 hook。

## 判定

0. **M4 补齐的三个口子**（都是读上游源码 + 单测查出来的，见 `docs/hooks-design.md` §4）：
   * `bash_start` —— 上游 `tools/mod.rs:210` 无条件注册 `bash_start`/`bash_poll`/`bash_kill`
     三个后台 shell 工具，参数名和 `bash` 一样是 `command`。M2/M3 的 guard 只判
     `tool == "bash"`，所以「把命令丢到后台跑」是一条**完整绕过**：同一条
     `rm -rf holdings/` 用 `bash` 被拒、用 `bash_start` 直接放行。现在两者同判。
   * 取数库直接调（待办池 P1-20）—— `INLINE_NET_RE` 只认 `requests`/`httpx`/`urllib`/
     `aiohttp`/`socket`，而 `import akshare as ak; ak.stock_zh_a_daily(...)` 是 akshare
     自己发 HTTP，正则看不见。现在按**库名**拦（`DATA_LIB_RE`）。
   * 包装命令与 `sh -c` —— `env`/`timeout`/`nohup`/`xargs` 这类前缀会把真正的首词顶到
     第二个 token（`env X=1 rm -rf /` 的首词是 `env`，旧版判不到 `rm`）；
     `sh -c "rm -rf x"` 的整条子命令是一个**引号内的 token**，旧版压根没看。
     现在会剥包装、并对 `-c` 的子命令递归判一次。
   另外把「工作区外路径」的判据从「以 `/`、`~`、`../` 开头」放宽到
   「任何含 `/` 的 token 都折成绝对路径再判」—— 旧版漏掉
   `cat holdings/../../etc/passwd` 这种中间带 `..` 的写法。

1. **写类工具**（write_file / edit_file / search_replace / parallel_edit_files）
   —— 只放行 `reports/` 与 `theses/` 两个目录，其余一律 deny。
   放行这两个目录是**按研究需要**定的：研报和投资论点就是这套工作流的交付物，
   `.atomcode.md` 让模型把报告写进 `reports/`，全拦死等于让它只能把报告糊在对话里。
   `holdings/`（持仓）与 `factors/`（因子定义）是用户资产，模型不许改；
   `scripts/` 也拦 —— "自己写个 python 去抓数据"正是要压住的那条编码路径。
2. **bash / bash_start** —— 只允许只读/纯计算，**判据是白名单**（M5 改）。
   首词不在 `BASH_ALLOW` 里一律 deny；在名单里的还要再过这几道：
   危险命令、包管理/版本控制、网络抓取（curl/wget/nc…，取数一律走 MCP）、
   写重定向 / tee / sed -i、`find -exec`、带 `open(...,'w')` 之类写文件的内联脚本、
   `import akshare` 这类**直接调取数库**的内联脚本，
   以及**解释器只许跑 `-c` 内联代码**（脚本文件与管道这一层看不见内容）。

   **为什么从 denylist 改成 allowlist**：M5 一次性又找出 12 条彼此无关的绕过
   （`python3 x.py` / `cat x.py | python3` / `base64 -d | sh` / `find -exec` /
   `awk 'BEGIN{system()}'` / `source` / `eval` / `perl -e` / `node` / `./x.py` …）。
   问题不在"这次漏了几条"，而在 denylist **永远只覆盖已经想到的那些**；
   而第 1 条本来就允许往 `reports/` 与 `theses/` 写，漏网一条就是完整绕过：
   先 `write_file reports/fetch.py`，再执行它。
   改白名单的代价实测很低 —— 翻部署中的 `guard.jsonl`，模型真实发出的 37 次 bash
   里，该拦的只有 12 条（4 次 `import akshare`、4 次跑 /opt/hca 下的 MCP 源码、
   3 次内联发 HTTP、1 次 curl）；**另外 25 条是正当用途**，其中 20 条是
   `python3 -c` 读文件/解析 JSON/算数 —— 白名单本来就放行 `-c` 内联，照样能用。
   25 条（args_head 没被截断的）原样重放，与「按意图该不该拦」不符 0 条。
3. **任何工具** —— 参数里出现工作区外的路径就 deny（含只读工具）。
4. **hunter 系 MCP** —— 补 `_hermes_user_id`（见下）。

## 顺带解决 P0-5：hunter 系 MCP 的用户身份

`uzi` / `watchlist` / `portfolio` / `hunter_cap` / `hunter_user` 这几个薄代理
从**工具参数**里取 `_hermes_user_id`（opencode 那边由 hunter-mcp-context 插件注入），
AtomCode 没有等价插件点。这里用 PreToolUse 的 `hookSpecificOutput.updatedInput`
把用户 id 补进参数 —— 上游 `cc_hooks.rs:795-810` 会拿它整体替换
`call.arguments`，且只给 `updatedInput`、不给 `permissionDecision` 时折叠结果是
`Proceed`（`cc_hooks.rs:832` 的 `_ => BeforeOutcome::Proceed`），不会多弹一次权限。

### 身份从哪来（M3 改）

M2 用的是容器级环境变量 `HUNTER_USER_ID` —— 那是**单用户评测环境**的简化。
网页上线之后每个登录用户是不同的 hermes user_id，继续用一个容器常量
等于所有人共用一份持仓与自选，**那是数据串户，不是体验问题**。

所以优先**按会话查**：hook 事件里带 `session_id`，拿它调
`GET {HERMES_API_URL}/api/internal/session/{sid}/user`（带 `X-Hunter-Internal-Key`）。
这个端点在 hunter-community 1.2.0 的 api 镜像里**已经存在**
（`apps/api/app/routers/internal_tools.py`，当初就是给 hunter-mcp-context 插件写的），
所以 api 侧零改动。归属表 `chat_session_owner` 是服务端权威，浏览器改不了；
`session_id` 由 daemon 自己填，模型也伪造不了。

查不到就回落到 `HUNTER_USER_ID`（单机 / 离线部署仍然走这条），
两条都没有就**不注入** —— 让下游 MCP 自己报「缺用户身份」，
而不是默默用别人的账本。

## 契约

stdin 一条 JSON（`tool_input` 是**解析好的对象**）；决策看 stdout **最后一行** JSON。
本脚本恒 exit 0 —— 上游对"exit 2 且无输出"的处理是当成脚本坏了并放行，
用 stdout 表达语义更直白。安全（总控红线 3）：只做 `json.loads`，不 eval，
不把输入拼进命令行。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys

# ⚠️ `urllib.request` 与 `shlex` **故意不在这里 import**。
# 测试机实测（2 核、容器内、机器有负载）：python3 空转 82 ms，
# 多一个 `import urllib.request` 变 498 ms、多一个 `import shlex` 变 141 ms。
# 这个 hook **每次工具调用都要跑一遍**，那 400 多毫秒是白付的 ——
# 身份反查只在 `mcp__{hunter 系}__*` 上才发生，切词只在 bash 上才发生。
# 所以两个都挪进真正用到它们的函数里（M4 优化，前后数字见报告 §3.2）。

EVENT = "PreToolUse"

# 身份反查（见文件头「身份从哪来」）。超时给得很短：guard 的 timeout_ms 是 5000，
# 反查是本地 compose 内网的一跳，慢到 2 秒就说明 api 有问题，宁可回落也不要拖垮对话。
HERMES_API_URL = (os.environ.get("HERMES_API_URL") or "").rstrip("/")
HUNTER_INTERNAL_KEY = os.environ.get("HUNTER_INTERNAL_KEY") or ""
LOOKUP_TIMEOUT_S = 2.0
# 反查结果缓存在工作区里 —— hook 是**一次调用一个进程**，进程内缓存活不过一次调用。
LOOKUP_CACHE_TTL_S = 300
# 查不到身份时也记一笔（I2）。理由：没有会话归属记录的会话（运维在容器里手工发起、
# 评测探针自己 POST /sessions 建的那种）**每一次工具调用**都会重新走一遍
# 「import urllib（530 ms）+ 打一次 api」，然后照样落到 HUNTER_USER_ID 兜底。
# 只对 404（api 明确说没有这条记录）记负缓存，网络错误不记 —— 那是临时故障。
# TTL 比正缓存短得多：会话是可能**稍后**才被登记的，60 秒后重新问一次。
LOOKUP_NEG_TTL_S = 60
# 这几个薄代理从工具参数里取 `_hermes_user_id`（M0 §5 / 待办池 P0-5）
# 需要补用户身份的薄代理（P0-5）：server 名 → 注入到哪个参数名。
#
# `hcapack`（I2 的组合工具）也在里面 —— 它内部打的就是 watchlist / portfolio
# 同一批 `/api/internal/*` 接口，不注入的话只能退回容器级 HUNTER_USER_ID，
# 多用户网页部署下就是串户。
#
# ⚠️ 它的参数名**没有下划线前缀**：hcapack 跑在 mcp 2.x 上，那一版的
# `func_metadata` 直接拒绝 `_` 开头的参数名
# （`InvalidSignature: Parameter _hermes_user_id ... cannot start with '_'`，
# I2 实测，server 起都起不来）。其余几个跑在 mcp 1.x 上，沿用原来的名字。
HUNTER_MCP_UID_FIELD = {
    "uzi": "_hermes_user_id",
    "watchlist": "_hermes_user_id",
    "portfolio": "_hermes_user_id",
    "hunter_cap": "_hermes_user_id",
    "hunter_user": "_hermes_user_id",
    "fin_memory": "_hermes_user_id",
    "hcapack": "hermes_user_id",
    "broker_account_readonly": "_hermes_user_id",
}
HUNTER_MCP_SERVERS = tuple(HUNTER_MCP_UID_FIELD)

# 阶段 M4：broker_account_readonly 工具对应的只读 OAuth Scope 映射
BROKER_ACCOUNT_TOOL_SCOPE = {
    "get_accounts": "account.read",
    "get_account_summary": "summary.read",
    "get_positions": "positions.read",
    "get_trades": "trades.read",
}

# 阶段 M4（TC-SEC-03）：严禁任何实盘交易委托、撤单、改单或银证转账工具调用
FORBIDDEN_LIVE_ORDER_RE = re.compile(
    r"(?:^|__)(?:place_order|submit_order|cancel_order|modify_order|transfer_cash|"
    r"live_order|execute_trade|buy_stock|sell_stock|margin_trade|short_sell|"
    r"broker_trade|broker_order)(?:$|__)",
    re.IGNORECASE,
)

# 写类工具：只有这两个目录放行
WRITE_TOOLS = {"write_file", "edit_file", "search_replace", "parallel_edit_files"}
# shell 工具。`bash_start` 是上游 tools/mod.rs:210 无条件注册的后台版，参数同样叫
# `command`（bash.rs:453 的 schema）——**必须和 bash 同判**，否则后台跑就绕过了。
# `bash_poll` / `bash_kill` 只收 job id，不带命令，不用判。
BASH_TOOLS = {"bash", "bash_start"}
WRITABLE_DIRS = ("reports", "theses")

# 参数里可能藏路径的键（上游工具的真实字段名）
PATH_KEYS = ("file_path", "path", "filename", "file", "dir", "directory")

# bash 第一个词命中就拒。分三类，理由不同，报错里会说清是哪一类。
BASH_DENY = {
    # 破坏性 / 改文件系统
    "rm": "删文件", "rmdir": "删目录", "mv": "移动/改名", "cp": "复制写入",
    "dd": "块写入", "truncate": "截断文件", "shred": "擦除", "ln": "建链接",
    "chmod": "改权限", "chown": "改属主", "chgrp": "改属组", "install": "安装文件",
    "mkfs": "格式化", "mount": "挂载", "umount": "卸载",
    # 提权 / 系统
    "sudo": "提权", "su": "提权", "doas": "提权", "pkexec": "提权",
    "systemctl": "操作系统服务", "service": "操作系统服务", "docker": "操作容器",
    "kill": "杀进程", "pkill": "杀进程", "killall": "杀进程",
    "crontab": "改定时任务", "at": "改定时任务", "reboot": "重启", "shutdown": "关机",
    # 装包 / 版本控制（投研工作区不做这些）
    "pip": "装包", "pip3": "装包", "uv": "装包", "npm": "装包", "pnpm": "装包",
    "yarn": "装包", "apt": "装包", "apt-get": "装包", "cargo": "装包",
    "git": "版本控制", "make": "构建", "cmake": "构建",
    # 网络抓取：取数一律走 MCP 工具，不许自己写爬虫
    "curl": "网络抓取", "wget": "网络抓取", "nc": "网络", "ncat": "网络",
    "telnet": "网络", "ssh": "网络", "scp": "网络", "sftp": "网络",
    "rsync": "网络/复制", "ftp": "网络",
}

# 内联脚本里出现这些就按"要写文件/要联网"处理
INLINE_WRITE_RE = re.compile(
    r"""(?x)
    # open(..., 'w'/'a'/'x'/'r+'/'wb'/'a+' …)。
    # ⚠️ 这里原来写的是 `[waxr]\+?` —— 把 **`'r'` 这个只读模式也算成写**了
    # （`\+?` 是可选的）。实测拦住过模型正当的只读用法：
    #     python3 -c "with open('.eval/x.sse', 'r', errors='ignore') as f: ..."
    #     python3 -c "with open('.atomcode/skills/deep_analysis/SKILL.md','r') as f: print(...)"
    # 只有 w / a / x（可带 b、t、+）和 **r 带 +** 才是写。
    open\s*\(\s*[^)]*['"](?:[rbt+]*[wax][rbt+]*|r[bt]*\+[bt]*)['"]
  | \.to_csv\s*\( | \.to_excel\s*\( | \.write_text\s*\( | \.write_bytes\s*\(
  | shutil\.(copy|move|rmtree)
  | os\.(remove|unlink|rmdir|rename|makedirs|mkdir)
    """
)
INLINE_NET_RE = re.compile(
    r"""(?x)
    \brequests\.(get|post|put|delete|head)\b
  | \bhttpx\.(get|post|put|delete|head|Client|AsyncClient)\b
  | \burllib\.request\b | \burlopen\b | \baiohttp\b
  | \bsocket\.(socket|create_connection)\b
    """
)
# 待办池 P1-20：**按库名**拦直接取数。
# `INLINE_NET_RE` 认的是"谁在发 HTTP"，而 akshare / tushare 这类库是自己在内部发，
# 脚本里一个 `requests` 字样都没有 —— M3 的 Playwright 真拍到过模型这么绕
# （`import akshare as ak; ak.stock_zh_a_daily(...)`，数字是真的但不进 MCP 审计）。
# 两种写法都要拦：`import X` / `from X import` 和 `X.func(` / `别名.func(`（含 `ak.`）。
DATA_LIBS = ("akshare", "tushare", "yfinance", "baostock", "efinance", "adata",
             "pywencai", "jqdatasdk", "rqdatac", "mootdx", "qstock", "akshare_one")
DATA_LIB_RE = re.compile(
    r"(?:\b(?:import|from)\s+(?:" + "|".join(DATA_LIBS) + r")\b)"
    r"|(?:\b(?:" + "|".join(DATA_LIBS) + r")\s*\.\s*[A-Za-z_])"
    # `import akshare as ak` 之后用的是别名；上面第一条已经拦住 import 本身，
    # 这里再补最常见的裸别名调用（`ak.` / `ts.pro_api` / `yf.download`），
    # 免得有人只写 `from akshare import *` 之外的怪写法时漏掉。
    r"|(?:\bak\s*\.\s*[a-z_]{3,})"
    r"|(?:\byf\s*\.\s*(?:download|Ticker)\b)"
)

# 包装命令：真正要判的首词在它后面。`env X=1 rm -rf /` 的首词是 `env`，
# 旧版据此放行 —— 这是一条实打实的绕过。剥到真首词再判。
WRAPPER_CMDS = {"env", "nohup", "timeout", "stdbuf", "nice", "ionice", "setsid",
                "command", "exec", "time", "watch", "xargs", "parallel"}
# `sh -c "……"` 的子命令是一个**引号内的 token**，不参与切词，所以要递归判一次。
SHELL_CMDS = {"sh", "bash", "dash", "zsh", "ksh", "ash", "busybox"}
# python 解释器：`python3 -m pip install …` 的首词是 python，旧版判不到 pip。
PY_CMDS = {"python", "python2", "python3", "pypy", "pypy3"}
PY_MODULE_DENY = {"pip": "装包", "ensurepip": "装包", "venv": "建虚拟环境",
                  "http.server": "起 HTTP 服务", "smtpd": "起服务",
                  "compileall": "写字节码", "zipapp": "打包", "pdb": "调试器"}

# 发行版自己的实现目录：daemon 二进制、9 个 MCP server 的源码、两个 venv。
# 模型没有任何正当理由碰它，而**绕过 MCP 层直接跑 server 源码**正好从这里走
# （待办池 P1-18；M3 的 Playwright 实测过一次真实发生：模型没调
# `mcp__watchlist__stock_quickview`，而是 `read_file /opt/hca/mcp/watchlist_mcp.py`
# 之后用 `/opt/hca/venv-hunter/bin/python -c "sys.path.insert(...); import ..."`
# 把同一份数据取了出来。数字是真的，但走不到 MCP 层就 ——
#   · 拿不到 `_hermes_user_id` 注入，多用户下会取错人的账本；
#   · 不进 MCP 审计，来源追溯断了；
#   · 前端收到的是 `bash` 而不是 `watchlist_stock_quickview`，富卡片直接退化成通用卡。
# 原来的路径检查漏掉它，是因为只查了首词**之后**的 token：`/opt/hca/venv-hunter/bin/python`
# 是首词（basename 归一成 `python`，在白名单里），而 `sys.path.insert('/opt/hca/mcp')`
# 藏在引号里，压根不是一个 token。所以这里对**整条命令原文**匹配。
DISTRO_PRIVATE_RE = re.compile(r"/opt/hca(?:/|\b)")

# ── 白名单（M5）───────────────────────────────────────────────────────────
#
# 为什么从 denylist 改成 allowlist：M5 一次性又找出 12 条绕过，而它们彼此毫无关系 ——
#     python3 reports/x.py      sh notes/x.sh          cat x.py | python3
#     echo <b64> | base64 -d | sh                      echo <b64> | base64 -d | python3
#     find . -exec python3 {} ;  awk 'BEGIN{system(…)}'  . reports/x.sh
#     source reports/x.sh        eval "$(cat x.sh)"     perl -e 'system(…)'
#     node reports/x.js          ./reports/x.py
# denylist 的问题不在于"这次漏了几条"，而在于**它永远只覆盖已经想到的那些**。
# 而 write_file 本来就允许往 reports/ 与 theses/ 写，任何一条漏网就是完整绕过。
#
# 换成白名单的代价实测很低：翻部署中的 guard.jsonl，模型真实发出的 37 次 bash 里
# 该拦的只有 12 条（akshare 4 / 跑 /opt/hca 下 MCP 源码 4 / 内联发 HTTP 3 / curl 1）；
# 另外 25 条是正当用途，20 条是 `python3 -c` 读文件算数 —— 白名单放行 `-c` 内联。
# 25 条原样重放，与「按意图该不该拦」不符 0 条（证据见 docs/evidence/M5/）。
# 研究工作区的 bash 本来就只该用来做点只读查看 —— 取数调 MCP 工具，
# 计算用 `python3 -c`，留档用 write_file。
#
# 只收**不会写文件、不会联网、不会执行外部代码**的东西。
# 刻意不收：awk / perl / node / ruby / php / lua / Rscript（都能 system() 出去）、
# sed（GNU 的 `e` 标志能执行命令、`w` 能写文件）、tar / zip / gzip（能写）、
# eval / source / `.`（执行看不见的东西）、tee（写）。
BASH_ALLOW = {
    # 看文件与目录
    "ls", "cat", "head", "tail", "wc", "stat", "file", "du", "df", "tree",
    "basename", "dirname", "realpath", "readlink", "pwd", "find",
    # 找内容
    # ack 刻意不收：它是 perl 脚本，`--pager` / `--match` 一族能把内容交给外部命令，
    # 而容器里本来就没装（`command -v ack` → MISSING）。真要用 grep / rg 够了。
    "grep", "egrep", "fgrep", "rg",
    # 文本加工（只读，不落盘）
    "sort", "uniq", "cut", "tr", "nl", "tac", "rev", "paste", "join", "comm",
    "column", "fold", "expand", "unexpand", "diff", "cmp", "strings", "od", "xxd",
    # 小工具
    "echo", "printf", "date", "seq", "expr", "bc", "jq", "yq", "true", "false",
    "test", "sleep", "which", "type", "env", "uname", "id", "whoami",
    "md5sum", "sha1sum", "sha256sum", "cksum",
    # 解释器：另有更严的判据（只许 -c / -m / 问版本），见 check_bash
    *SHELL_CMDS, *PY_CMDS,
    # 包装命令：real_head 会剥掉它们，这里收进来只是为了"后面没东西"那种退化情形
    *WRAPPER_CMDS,
}

# 白名单里这几个命令**自带输出文件参数**，不用 `>` 就能写盘（M5 复核补）。
#
# 形式一：输出文件是某个选项的值。
WRITE_OPT_CMDS = {
    "sort": {"-o", "--output"},          # sort -o FILE：实测能整个覆盖持仓账本
    "tree": {"-o", "--output"},          # tree -o FILE
    "yq":   {"-i", "--inplace", "--in-place"},   # yq -i 原地改（sed -i 那一类）
}
# 形式二：第二个操作数就是输出文件（`uniq [输入 [输出]]`、`xxd [输入 [输出]]`）。
# 值是"这个命令里会吃掉后面一格的短选项"，不列全就会误伤（`xxd -l 100 a.bin`）。
SECOND_OPERAND_WRITES = {
    "uniq": {"-f", "-s", "-w", "--skip-fields", "--skip-chars", "--check-chars"},
    "xxd":  {"-c", "-g", "-l", "-o", "-s", "-seek"},
}

# 白名单里还有命令带**执行外部命令**的选项 —— 和「自带输出文件参数」是同一类错。
# ripgrep 的 `--pre CMD` 会对每个输入文件跑一遍 CMD，开发机上实证过：
#     $ echo 'print("PWNED-BY-RG-PRE")' > x.py
#     $ rg --pre python3 --pre-glob '*.py' 'PWNED' .
#     ./x.py:PWNED-BY-RG-PRE          ← 这一行是 python3 x.py 的**执行输出**
# 部署中的容器里没装 rg（`command -v rg` → MISSING），所以当前不可利用；
# 但白名单是随工作区模板走的，换个基础镜像就有了。
EXEC_OPT_CMDS = {
    "rg": {"--pre", "--pre-glob", "--hostname-bin"},
}

# `find` 的这几个动作能执行任意命令或删文件，白名单收了 find 也要单独拦。
FIND_EXEC_OPTS = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprintf",
                  "-fprint", "-fprint0", "-fls"}

# 命令分隔符：命中就开一个新"段"，每段单独判首词。
# `(` `)` 也算分隔符，这样 `$(rm -rf x)` 里的 rm 会被当成一段的首词抓到。
SEPARATORS = {";", "|", "||", "&&", "&", "\n", "(", ")", "$("}


def out(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def decide(decision: str, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": EVENT,
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def rewrite(new_input: dict) -> dict:
    """只改参数、不表态 —— 折叠结果是 Proceed（cc_hooks.rs:832）。"""
    return {"hookSpecificOutput": {"hookEventName": EVENT, "updatedInput": new_input}}


def _cache_path(workspace: str) -> str:
    return os.path.join(workspace, ".atomcode", "session-user.json")


def _cache_read(workspace: str, sid: str):
    """缓存里的 uid。**没有记录返回 None，记着「查不到」返回空串** —— 两者不一样：
    None 要去打接口，空串是 60 秒内刚问过、别再问了。"""
    try:
        with open(_cache_path(workspace), encoding="utf-8") as f:
            rec = json.load(f).get(sid)
    except Exception:  # noqa: BLE001  缓存坏了就当没有，重新查
        return None
    if not isinstance(rec, dict):
        return None
    uid = rec.get("uid") or ""
    ttl = LOOKUP_CACHE_TTL_S if uid else LOOKUP_NEG_TTL_S
    if (datetime.datetime.now(datetime.timezone.utc).timestamp() - rec.get("at", 0)) > ttl:
        return None
    return uid


def _cache_write(workspace: str, sid: str, uid: str) -> None:
    try:
        d = os.path.join(workspace, ".atomcode")
        os.makedirs(d, exist_ok=True)
        path = _cache_path(workspace)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception:  # noqa: BLE001
            data = {}
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        data[sid] = {"uid": uid, "at": now}
        # 只留最近 200 条，免得这个文件无限长
        if len(data) > 200:
            keep = sorted(data.items(), key=lambda kv: kv[1].get("at", 0), reverse=True)[:200]
            data = dict(keep)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001  写不进缓存不影响本次判定
        pass


def lookup_user(session_id: str, workspace: str) -> str:
    """session_id → hermes user_id。查不到返回空串，**不抛异常、不猜**。"""
    sid = (session_id or "").strip()
    if not sid or not HERMES_API_URL or not HUNTER_INTERNAL_KEY:
        return ""
    # ⚠️ 缓存必须查在 import 之前（I2）。这三个 import 在容器里实测要 **530 ms**
    # （`python -X importtime`：urllib.request 累计 532 ms，主要是 http.client →
    # email.parser 那一串），而 guard 是**每一次工具调用**都要跑一遍的。
    # 原先的顺序是先 import 再查缓存 —— 缓存命中率再高也照样每次付这 530 ms。
    cached = _cache_read(workspace, sid)
    if cached is not None:
        return cached
    import urllib.error  # noqa: PLC0415  见文件头的延迟导入说明
    import urllib.parse
    import urllib.request
    url = "{}/api/internal/session/{}/user".format(HERMES_API_URL, urllib.parse.quote(sid, safe=""))
    req = urllib.request.Request(url, headers={"X-Hunter-Internal-Key": HUNTER_INTERNAL_KEY})
    try:
        with urllib.request.urlopen(req, timeout=LOOKUP_TIMEOUT_S) as r:
            uid = str((json.loads(r.read().decode("utf-8")) or {}).get("user_id") or "").strip()
    except urllib.error.HTTPError as e:
        # 404 = 这个会话没有归属记录（运维在容器里手工发起的那种），是正常情况
        if e.code != 404:
            print("[guard] 身份反查 HTTP {}".format(e.code), file=sys.stderr)
        else:
            _cache_write(workspace, sid, "")     # 负缓存，60 秒内不再问（见 LOOKUP_NEG_TTL_S）
        return ""
    except Exception as e:  # noqa: BLE001
        print("[guard] 身份反查失败：{}".format(type(e).__name__), file=sys.stderr)
        return ""
    if uid:
        _cache_write(workspace, sid, uid)
    return uid


def norm(path: str, workspace: str) -> str:
    """把一个可能是相对路径的字符串折成绝对路径（纯词法，不碰文件系统）。"""
    p = os.path.expanduser(path.strip())
    if not os.path.isabs(p):
        p = os.path.join(workspace, p)
    return os.path.normpath(p)


def inside(path: str, root: str) -> bool:
    p, r = os.path.normpath(path), os.path.normpath(root)
    return p == r or p.startswith(r.rstrip("/") + "/")


def in_writable(path: str, workspace: str) -> bool:
    return any(inside(path, os.path.join(workspace, d)) for d in WRITABLE_DIRS)


def collect_paths(value, acc: list) -> None:
    """递归收参数里所有看起来是路径的字符串。"""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in PATH_KEYS and isinstance(v, str) and v.strip():
                acc.append(v)
            else:
                collect_paths(v, acc)
    elif isinstance(value, list):
        for v in value:
            collect_paths(v, acc)


def looks_like_path(tok: str) -> bool:
    """bash 参数里哪些 token 当路径看。

    M4 放宽：旧版只认「以 `/` `~` `../` 开头」，于是
    `cat holdings/../../etc/passwd` 整个漏过去（中间才出现 `..`）。
    现在**任何含 `/` 的 token**都折成绝对路径再判 —— 反正折完还在工作区内的
    一律放行，所以 `print(1/2)`、`2026/09/22`、`s/a/b/` 这类误判是无害的
    （它们折出来都在工作区里面），而 `x/../../etc` 会被 normpath 拆穿。
    另外单独认 `..`（`cd ..`）。
    """
    t = tok.strip()
    if not t or t.startswith("-"):
        return False
    return "/" in t or t == ".." or t.startswith("~")


def lex(command: str):
    """按 shell 语法切词，**引号内的内容不参与切分**。

    用 `punctuation_chars=True` 让 `;` `|` `&&` `>` `(` 这些操作符成为独立 token，
    这样 `python3 -c "import statistics;print(...)"` 里那个引号内的 `;` 不会
    被当成命令分隔符（第一版用正则切，正是栽在这里）。
    """
    import shlex  # noqa: PLC0415  见文件头的延迟导入说明
    sh = shlex.shlex(command, posix=True, punctuation_chars=True)
    sh.whitespace_split = True
    return list(sh)  # 引号不配对时抛 ValueError


def is_redirect(tok: str) -> bool:
    """写重定向的操作符 token。`>&` / `>>&` 是复制 fd（`2>&1`），不算写文件。"""
    return ">" in tok and set(tok) <= set("<>&0123456789") and not tok.endswith("&")


def head_name(tok: str) -> str:
    """命令首词归一：剥掉 `$` 反引号引号，再取 basename。"""
    return os.path.basename(tok.strip("$`'\"\\"))


def real_head(seg: list, start: int):
    """剥掉包装命令，返回 (真首词 basename, 它在 seg 里的下标)。

    `env X=1 timeout 5 rm -rf /` → ("rm", 4)。剥的时候跳过选项（`-xxx`）与
    `timeout` 的时长参数（纯数字/带单位）；剥不动就返回当前这个。
    """
    i = start
    guard_rounds = 0
    while i < len(seg) and guard_rounds < 6:
        guard_rounds += 1
        head = head_name(seg[i])
        if head not in WRAPPER_CMDS:
            return head, i
        j = i + 1
        while j < len(seg):
            t = seg[j]
            if t.startswith("-"):
                j += 1
                continue
            if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?[smhd]?", t):  # timeout 的时长
                j += 1
                continue
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", t):    # env 的 VAR=值
                j += 1
                continue
            break
        if j >= len(seg):
            return head, i          # 包装命令后面没东西了，就按它自己判
        i = j
    return head_name(seg[i]) if i < len(seg) else "", min(i, len(seg) - 1)


def check_bash(command: str, workspace: str, depth: int = 0):
    """返回 deny 理由，None 表示放行。

    `depth` 只在递归判 `sh -c "……"` 的子命令时 +1，防止
    `sh -c 'sh -c "sh -c …"'` 把进程转晕（超过 3 层直接拒，说不清就不放行）。
    """
    if depth > 3:
        return "bash 里嵌套了太多层 `-c` 子命令，判定不了，研究工作区不放行。"
    if DATA_LIB_RE.search(command):
        return ("内联脚本直接调取数库（akshare / tushare 这类）。这条路取到的数"
                "**不进 MCP 层**：拿不到用户身份、没有来源可追溯、界面上也认不出是哪个工具。"
                "行情 / 财务 / 新闻 / 龙虎榜 / 筛选都有现成的 MCP 工具，请调工具。")
    if INLINE_WRITE_RE.search(command):
        return ("内联脚本里有写文件的调用。研究结论请直接回答，需要留档就用 "
                "write_file 写进 reports/ 或 theses/，不要绕过审计用 bash 落盘。")
    if INLINE_NET_RE.search(command):
        return ("内联脚本在自己发 HTTP 请求。行情 / 财务 / 新闻 / 龙虎榜都有现成的 "
                "MCP 工具，请调工具，不要写爬虫 —— 自己抓的数据没有来源可追溯。")
    if DISTRO_PRIVATE_RE.search(command):
        return ("bash 里出现了 /opt/hca —— 那是本发行版自己的实现目录（daemon 二进制、"
                "MCP server 源码、venv），不是数据。直接跑 MCP server 的源码等于绕开 MCP 层："
                "取不到用户身份、不进审计、界面上也认不出是哪个工具。"
                "要哪份数据就调对应的 MCP 工具。")

    if "`" in command:
        # 反引号命令替换：shlex 不把 ` 当特殊字符，`echo \`rm -rf x\`` 里的 rm 会被
        # 当成 echo 的参数而漏掉。`$(...)` 走的是 punctuation_chars，首词照样会判，
        # 所以这里只堵反引号这一种写法，成本低、说得清。
        return ("bash 里有反引号命令替换，研究工作区不放行（判定不了里面真正执行的是什么）。"
                "需要命令替换请用 $(...)。")

    try:
        toks = lex(command)
    except ValueError:
        return "bash 命令引号不配对，解析不了 —— 研究工作区不放行解析不出来的命令。"

    segments, cur = [], []
    devnull_idx: set[int] = set()
    for idx, t in enumerate(toks):
        if idx in devnull_idx:
            continue                          # `> /dev/null` 的目标本身不是参数
        if is_redirect(t):
            # `> /dev/null` / `2> /dev/null` 不是写文件，是**丢弃输出**。
            # 一律按写重定向拦掉是误伤：模型真实发出的命令里就有
            # `python3 -c "..." 2>/dev/null || python3 -c "..."` 这种写法
            # （从容器 guard.jsonl 里重放时撞到的）。只放行目标恰好是 /dev/null 的。
            nxt = toks[idx + 1] if idx + 1 < len(toks) else ""
            if nxt.strip("'\"") == "/dev/null":
                devnull_idx.add(idx + 1)      # 目标 token 不当成命令参数看
                continue
            return ("bash 里有写重定向（> / >>）。这条路绕过 write_file 的审批与差异审阅，"
                    "研究工作区一律不许（丢弃输出的 `> /dev/null` 除外）。"
                    "要留档就用 write_file 写 reports/ 或 theses/。")
        if t in SEPARATORS:
            segments.append(cur)
            cur = []
        else:
            cur.append(t)
    segments.append(cur)

    for seg in segments:
        # 跳过 VAR=值 前缀
        i = 0
        while i < len(seg) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", seg[i]):
            i += 1
        if i >= len(seg):
            continue
        # 先剥包装命令（env / timeout / nohup / xargs …），拿到真正要判的首词
        head, i = real_head(seg, i)
        # `sh -c "rm -rf x"`：子命令整条在引号里，切词切不开，递归判一次
        if head in SHELL_CMDS:
            sub = None
            for k in range(i + 1, len(seg)):
                if seg[k] == "-c" and k + 1 < len(seg):
                    sub = seg[k + 1]
                    break
            if sub:
                why = check_bash(sub, workspace, depth + 1)
                if why:
                    return "`{} -c` 的子命令被拒：{}".format(head, why)
                continue
        # **解释器只许跑内联代码，不许跑脚本文件、不许从管道读**（M5 补）。
        #
        # 上面那几条正则（DATA_LIB_RE / INLINE_NET_RE / INLINE_WRITE_RE）判的是
        # **命令行文本**。脚本在文件里的时候，正则什么也看不见 —— 实测这五条全部放行：
        #     python3 reports/x.py            sh notes/x.sh
        #     cat notes/x.py | python3        echo <b64> | base64 -d | sh
        #     echo <b64> | base64 -d | python3
        # 而 write_file 本来就允许往 reports/ 与 theses/ 写。两步接起来就是完整绕过：
        # 先把 `import akshare; …` 写进 reports/fetch.py，再 `python3 reports/fetch.py`
        # —— 取到的数不进 MCP 层，拿不到用户身份、不进审计、界面上也认不出来源。
        # 这正是 M3 §5 与 M4（P1-20）对**内联**命令堵住、对**脚本文件**漏掉的同一类。
        #
        # 判据：有 `-c`（内联，正则看得见）或 `-m`（模块，下面单独判）或只是问版本
        # 就放行；其余一律拒 —— 包括**光秃秃一个解释器**（那就是在从管道 / stdin 读）。
        if head in PY_CMDS or head in SHELL_CMDS:
            rest = seg[i + 1:]
            info_only = bool(rest) and all(t in ("--version", "-V", "--help", "-h") for t in rest)
            if not ("-c" in rest or "-m" in rest or info_only):
                what = "什么参数都没给（那就是在从管道或 stdin 读脚本）" if not rest \
                       else "要跑的是脚本文件 `{}`".format(next((t for t in rest if not t.startswith("-")), "?"))
                return ("bash 想用 `{}` 跑外部脚本：{}。研究工作区只允许 `-c` 的内联代码 —— "
                        "脚本在文件里或从管道进来时，这一层看不到它到底做什么，"
                        "而取数、写文件、发请求这三件事都必须走能追溯的路："
                        "取数调 MCP 工具，留档用 write_file 写 reports/ 或 theses/。"
                        .format(head, what))

        # `python3 -m pip install …`：首词是 python，旧版判不到 pip
        if head in PY_CMDS:
            for k in range(i + 1, len(seg)):
                if seg[k] == "-m" and k + 1 < len(seg):
                    mod = seg[k + 1]
                    why_m = PY_MODULE_DENY.get(mod)
                    if why_m:
                        return ("bash 跑 `python -m {}`（{}）在研究工作区被禁。"
                                .format(mod, why_m))
                    break
        if head == "tee":
            return "bash 用 tee 写文件。要留档请用 write_file 写 reports/ 或 theses/。"
        # 白名单里有几个"只读工具"其实自带**输出文件**参数 —— 不用 `>` 就能写盘，
        # 上面那道写重定向的闸完全看不见它们。M5 实测（部署中的容器里 sort/uniq 都在）：
        #     sort -o holdings/positions.json reports/evil.txt   → 持仓账本被整个覆盖
        #     uniq reports/evil.txt holdings/positions.json      → 同上
        # 这和「解释器跑脚本文件」是同一类错：白名单只管住了"首词是谁"，
        # 管不住"这个命令自己会不会写"。
        eopts = EXEC_OPT_CMDS.get(head)
        if eopts:
            for t in seg[i + 1:]:
                if t.split("=", 1)[0] in eopts:
                    return ("bash 里 `{} {}` 会把文件内容交给一个外部命令去执行"
                            "（不是查看，是执行）。研究工作区的 bash 只做只读查看 —— "
                            "取数请调 MCP 工具，计算用 `python3 -c`。"
                            .format(head, t.split("=", 1)[0]))
        opts = WRITE_OPT_CMDS.get(head)
        if opts:
            for t in seg[i + 1:]:
                if t.split("=", 1)[0] in opts:
                    return ("bash 里 `{} {}` 会把结果写成文件 —— 不用 `>` 也一样是写盘。"
                            "研究工作区的 bash 只做只读查看，要留档请用 write_file "
                            "写 reports/ 或 theses/。".format(head, t.split("=", 1)[0]))
        takes_val = SECOND_OPERAND_WRITES.get(head)
        if takes_val is not None:
            operands, k = [], i + 1
            while k < len(seg):
                t = seg[k]
                if t == "--":
                    operands.extend(seg[k + 1:])
                    break
                if t.startswith("-") and t != "-":
                    # 吃一个值的短选项要跳两格，否则 `xxd -l 100 a.bin` 会把 100
                    # 当成操作数、把 a.bin 当成"输出文件"而误伤
                    k += 2 if (t in takes_val) else 1
                    continue
                operands.append(t)
                k += 1
            if len(operands) >= 2:
                return ("bash 里 `{0}` 的第二个参数 `{1}` 是**输出文件**"
                        "（`{0} [输入 [输出]]`），不用 `>` 也会写盘。"
                        "研究工作区的 bash 只做只读查看，要留档请用 write_file "
                        "写 reports/ 或 theses/。".format(head, operands[1]))
        if head in ("sed", "perl") and any(t.startswith("-i") for t in seg[i + 1:]):
            return f"bash 用 {head} -i 原地改文件。研究工作区不许用 bash 改文件。"
        if head == "find":
            hit = [t for t in seg[i + 1:] if t in FIND_EXEC_OPTS]
            if hit:
                return ("bash 里 `find {}` 能执行任意命令或删文件，研究工作区不放行。"
                        "只用 find 找路径，要看内容就 grep，要算就 `python3 -c`。"
                        .format(hit[0]))
        why = BASH_DENY.get(head)
        if why:
            return (f"bash 命令 `{head}`（{why}）在研究工作区被禁。"
                    "取数据请调 MCP 工具；需要计算就用只读的 python 表达式。")
        # **白名单兜底**：不在名单里一律拒。denylist 永远只覆盖已经想到的那些，
        # 而漏网一条就是完整绕过（M5 一次找出 12 条，见 BASH_ALLOW 上面的说明）。
        if head not in BASH_ALLOW:
            return (f"bash 命令 `{head}` 不在研究工作区的白名单里。"
                    "这里的 bash 只用来做只读查看（ls / cat / grep / find / wc 这类）——"
                    "取数据请调 MCP 工具，计算用 `python3 -c`，留档用 write_file "
                    "写 reports/ 或 theses/。确实需要放开某个命令，请改 .hooks/guard.py "
                    "的 BASH_ALLOW 并说明理由。")
        for t in seg[i + 1:]:
            if looks_like_path(t) and not inside(norm(t, workspace), workspace):
                return f"bash 参数 `{t}` 指向工作区外。研究会话只在 {workspace} 内活动。"
    return None


def record_start(workspace: str, sid: str, tool: str, args: dict) -> None:
    """给 audit hook 留一条「开始时刻 + 参数摘要」。

    为什么由 guard 写、而不是再挂一个 PreToolUse 的 audit hook：
    **PostToolUse 的 payload 里既没有 `tool_input` 也没有耗时**
    （上游 `cc_hooks.rs:897-903` 只有 session_id / tool_name / tool_response / cwd），
    而且**两个事件都不带 call_id**，没有任何字段能把 pre 与 post 配起来。
    guard 本来就在每次 PreToolUse 跑一遍、手里正好有 `tool_input`，
    再挂一个 hook 等于每次工具调用多起一个 python 进程（实测一次约 40 ms）。

    一个 key 一个文件 + `os.replace` 原子换名，是为了避开「读改写」竞争：
    M2 实测一轮里出现过 5 组并行工具调用，多个 guard 进程同时改一个 json
    会丢记录（丢了就只能写 `—`，但**绝不能配错**）。
    同一 (会话, 工具) 的并行调用会互相覆盖 —— 这时耗时按最后一次开始算，
    audit 里标 `duration_src="guard-start(may-overlap)"`，不假装精确。
    """
    if not sid or not tool:
        return
    try:
        d = os.path.join(workspace, ".atomcode", "tool-start")
        os.makedirs(d, exist_ok=True)
        import hashlib
        key = hashlib.sha1(("{}\x1f{}".format(sid, tool)).encode("utf-8")).hexdigest()[:16]
        path = os.path.join(d, key + ".json")
        body = json.dumps({
            "at": datetime.datetime.now(datetime.timezone.utc).timestamp(),
            "session_id": sid,
            "tool": tool,
            # 参数摘要：只留键名与截断后的原文，**不做任何加工**
            "args_keys": sorted(args.keys())[:20],
            "args_head": json.dumps(args, ensure_ascii=False)[:300],
        }, ensure_ascii=False)
        tmp = path + ".tmp{}".format(os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)
        # 顺手清理超过 1 小时的残留（deny / 超时 / daemon 重启都会留下孤儿）
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        for name in os.listdir(d):
            fp = os.path.join(d, name)
            try:
                if now - os.path.getmtime(fp) > 3600:
                    os.remove(fp)
            except OSError:
                pass
    except Exception:  # noqa: BLE001  写不进就写不进，audit 那边会记 duration_ms=null
        pass


def log(workspace: str, rec: dict) -> None:
    try:
        d = os.path.join(workspace, ".atomcode")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "guard.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001  写不进日志绝不能拖垮对话
        pass


def _broker_oauth_state_path(workspace: str) -> str:
    return os.path.join(workspace, ".atomcode", "broker-oauth.json")


def check_broker_oauth_scope(uid: str, tool_subname: str, workspace: str) -> tuple[bool, str]:
    """核验用户对 `broker_account_readonly.*` 的有效 OAuth 只读授权与 Scope（TC-SEC-01 / TC-SEC-02）。

    返回 `(allowed, reason_if_denied)`。
    """
    required_scope = BROKER_ACCOUNT_TOOL_SCOPE.get(tool_subname)
    if not required_scope:
        return False, (
            f"安全门卫刚性拦截：`broker_account_readonly.{tool_subname}` 不在允许的 4 个只读账户工具清单中，"
            "物理隔离实盘下单、撤单、改单与银证转账。"
        )
    if not uid:
        return False, (
            "安全门卫刚性拦截（403）：调用 `broker_account_readonly` 必须具备真实登录用户身份（_hermes_user_id），"
            "当前未识别到登录用户，已拒绝访问。"
        )

    # 1. 优先检查环境变量显式注入的授权状态（供红队测试与离线沙盒使用）
    env_status = (os.environ.get("HCA_BROKER_OAUTH_STATUS") or "").strip().lower()
    if env_status:
        if env_status != "authorized":
            return False, (
                f"安全门卫刚性拦截（403 尚未授权或授权已撤销）：用户 `{uid}` 的券商只读授权状态为 `{env_status}`，"
                f"禁止调用 `broker_account_readonly.{tool_subname}`。"
            )
        env_scopes_raw = os.environ.get("HCA_BROKER_OAUTH_SCOPES") or "account.read,summary.read,positions.read,trades.read"
        env_scopes = [s.strip() for s in env_scopes_raw.split(",") if s.strip()]
        if required_scope not in env_scopes:
            return False, (
                f"安全门卫刚性拦截（403 Scope 不足）：用户 `{uid}` 缺少所需只读 Scope `{required_scope}`（已授权：{env_scopes}）。"
            )
        return True, ""

    # 2. 检查工作区 `.atomcode/broker-oauth.json` 状态文件（若存在）
    state_file = _broker_oauth_state_path(workspace)
    if os.path.isfile(state_file):
        try:
            with open(state_file, encoding="utf-8") as f:
                all_states = json.load(f)
            user_rec = (all_states or {}).get(uid)
            if not isinstance(user_rec, dict) or user_rec.get("status") != "authorized" or not user_rec.get("token_cipher"):
                st = (user_rec or {}).get("status", "unauthorized")
                return False, (
                    f"安全门卫刚性拦截（403 尚未授权或授权已撤销）：用户 `{uid}` 当前状态为 `{st}`，"
                    f"禁止调用 `broker_account_readonly.{tool_subname}`。"
                )
            scopes = user_rec.get("scopes") or []
            if required_scope not in scopes:
                return False, (
                    f"安全门卫刚性拦截（403 Scope 不足）：用户 `{uid}` 缺少所需只读 Scope `{required_scope}`。"
                )
            return True, ""
        except Exception:  # noqa: BLE001
            return False, "安全门卫刚性拦截（403）：读取券商 OAuth 授权状态文件失败，默认关闭拒绝访问。"

    # 3. 调后端 `/api/internal/broker/oauth-verify` 实时核验数据库中的 AES-256-GCM 授权状态
    if HERMES_API_URL:
        import urllib.error  # noqa: PLC0415
        import urllib.parse
        import urllib.request

        qs = urllib.parse.urlencode({
            "user_id": uid,
            "scope": required_scope,
            "tool_name": tool_subname,
            "session_user_id": uid,
        })
        url = "{}/api/internal/broker/oauth-verify?{}".format(HERMES_API_URL, qs)
        req = urllib.request.Request(url, headers={"X-Hunter-Internal-Key": HUNTER_INTERNAL_KEY})
        try:
            with urllib.request.urlopen(req, timeout=LOOKUP_TIMEOUT_S) as r:
                body = json.loads(r.read().decode("utf-8")) or {}
                if body.get("authorized") is True:
                    return True, ""
                return False, str(body.get("message") or "安全门卫刚性拦截（403）：尚未开启券商只读授权或授权已撤销。")
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8")) or {}
                msg = err_body.get("message")
            except Exception:  # noqa: BLE001
                msg = None
            return False, msg or f"安全门卫刚性拦截（HTTP {e.code}）：尚未开启券商只读授权或授权已撤销。"
        except Exception:  # noqa: BLE001
            return False, "安全门卫刚性拦截（403）：无法连接券商授权核验服务，个人账户只读连接器默认关闭。"

    # 4. 默认关闭（Default Closed）：无任何授权证明时 100% 拒绝
    return False, (
        f"安全门卫刚性拦截（403）：`broker_account_readonly` 默认关闭，用户 `{uid}` 尚未完成券商 OAuth 只读授权。"
    )


def main() -> int:
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
        if not isinstance(ev, dict):
            raise ValueError
    except Exception:  # noqa: BLE001
        # 解析不了就放行：拿不准的时候宁可让下游的审批门去判，也不要静默拦住所有工具
        return 0

    tool = ev.get("tool_name") or ""
    args = ev.get("tool_input")
    args = args if isinstance(args, dict) else {}
    workspace = os.path.normpath(
        os.environ.get("HCA_WORKSPACE") or ev.get("cwd") or os.getcwd()
    )

    verdict = None
    reason = ""

    # TC-SEC-03：拦截任何试图发起实盘交易委托、撤单、改单或银证转账的工具调用
    if FORBIDDEN_LIVE_ORDER_RE.search(tool):
        reason = (
            f"安全门卫刚性拦截（TC-SEC-03）：严禁发起任何实盘委托下单、撤单、改单或银证转账操作（`{tool}`）！"
            "本平台所有券商连接器物理隔离实盘交易，仅支持只读投研分析与模拟演练（PAPER_MODE=PAPER）。"
        )
        verdict = decide("deny", reason)

    elif tool in WRITE_TOOLS:
        targets = []
        collect_paths(args, targets)
        if not targets:
            reason = f"`{tool}` 没给出可识别的目标路径，研究工作区不放行判定不了的写操作。"
            verdict = decide("deny", reason)
        else:
            bad = [t for t in targets if not in_writable(norm(t, workspace), workspace)]
            if bad:
                reason = (
                    f"研究工作区禁止用 `{tool}` 写 {', '.join(bad[:3])}。"
                    f"只有 {'/ '.join(WRITABLE_DIRS)}/ 两个目录可写："
                    "holdings/ 与 factors/ 是用户资产、scripts/ 属于写代码，都不许改。"
                    "结论请直接回答；要留档就写进 reports/ 或 theses/。"
                )
                verdict = decide("deny", reason)

    elif tool in BASH_TOOLS:
        cmd = args.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            reason = "{} 没有 command 参数。".format(tool)
            verdict = decide("deny", reason)
        else:
            why = check_bash(cmd, workspace)
            if why:
                reason = why
                verdict = decide("deny", reason)

    if verdict is None and not tool.startswith("mcp__"):
        # 任何工具（含只读）都不许碰工作区外的路径
        targets = []
        collect_paths(args, targets)
        outside = [t for t in targets if not inside(norm(t, workspace), workspace)]
        if outside:
            reason = (f"`{tool}` 的路径 {outside[0]} 在工作区外。"
                      f"研究会话只在 {workspace} 内活动。")
            verdict = decide("deny", reason)

    if verdict is None and tool.startswith("mcp__"):
        parts = tool.split("__")
        server = parts[1] if len(parts) > 1 else ""
        tool_subname = parts[2] if len(parts) > 2 else ""
        field = HUNTER_MCP_UID_FIELD.get(server)
        if field:
            uid = lookup_user(ev.get("session_id") or "", workspace)
            source = "session"
            if not uid:
                uid = (os.environ.get("HUNTER_USER_ID") or "").strip()
                source = "env"

            # M4 专项（TC-SEC-01 & TC-SEC-02）：针对 `broker_account_readonly` 强制覆写 `_hermes_user_id`、
            # 抹除模型伪造的 `user_id`，并在内核外核验该用户的有效 OAuth 只读授权 Scope
            if server == "broker_account_readonly":
                supplied_uid = args.get("user_id") or args.get(field)
                ok_oauth, deny_msg = check_broker_oauth_scope(uid, tool_subname, workspace)
                if not ok_oauth:
                    reason = deny_msg
                    verdict = decide("deny", reason)
                else:
                    new = {k: v for k, v in args.items() if k != "user_id"}
                    new[field] = uid
                    verdict = rewrite(new)
                    reason = "注入 {}（来源：{}）并通过 OAuth Scope 校验".format(field, source)
                    if supplied_uid and supplied_uid != uid:
                        reason += "；已强制覆写跨租户伪造身份 `{}` -> `{}`".format(supplied_uid, uid)
            else:
                # ⚠️ **无条件覆盖模型自己填的那一份**（I2 加严）。
                # 原先是「参数里已经有就不动」——那等于模型可以自己指定
                # `_hermes_user_id`，填上别人的 UUID 就能读到别人的持仓。
                # 身份只能由这个 hook 决定，模型填什么都不算数。
                supplied = args.get(field)
                if uid:
                    new = dict(args)
                    new[field] = uid
                    verdict = rewrite(new)
                    reason = "注入 {}（来源：{}）".format(field, source)
                    if supplied and supplied != uid:
                        reason += "；已覆盖模型自填的身份"
                elif supplied:
                    # 查不到身份，但模型自己填了一个 —— **必须摘掉**。
                    # 放过去就等于让模型用一个我们没验证过的身份去读账本。
                    new = {k: v for k, v in args.items() if k != field}
                    verdict = rewrite(new)
                    reason = "查不到用户身份；已摘掉模型自填的 {}".format(field)
                else:
                    # 不注入。下游 MCP 会自己报「缺用户身份」，
                    # 这比默默用别人的账本强得多。
                    reason = "查不到用户身份，未注入 {}".format(field)

    denied = bool(verdict) and "permissionDecision" in verdict.get("hookSpecificOutput", {})
    if not denied:
        # 只给**会真正执行**的调用留起始记录 —— 被拒的调用不会有 PostToolUse，
        # 留下来就成了孤儿，下一次同名工具调用会配到它身上、算出一个假的耗时。
        record_start(workspace, ev.get("session_id") or "", tool, args)

    if verdict is not None:
        out(verdict)
    log(workspace, {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
        "session_id": ev.get("session_id"),
        "tool": tool,
        "decision": ("deny" if denied else ("rewrite" if verdict else "proceed")),
        "reason": reason,
        "args_head": json.dumps(args, ensure_ascii=False)[:300],
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
