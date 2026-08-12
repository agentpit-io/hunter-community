# Hunter Community · 原生模式启动器（免 Docker · 免重启 · 不碰系统环境）
#
# 设计约束（这几条是这个脚本存在的理由，改动时别破坏）：
#   1. 所有运行时（Python/Node/opencode/Postgres）下载到 .runtime/，不装到系统
#   2. 不写注册表、不改系统 PATH、不需要管理员权限、不需要重启
#   3. 卸载 = 删项目文件夹
#   4. 端口全部走 .env 里的非标准端口，避开用户本机可能已有的服务
#
# 用法：双击项目根目录的 start.bat，或在 PowerShell 里跑 scripts\native\start.ps1

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # 关掉 Invoke-WebRequest 的进度条，快 10 倍

# ── 路径 ────────────────────────────────────────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path
$Runtime   = Join-Path $Root '.runtime'
$LogDir    = Join-Path $Root '.runtime\logs'
$PyDir     = Join-Path $Runtime 'python'
$NodeDir   = Join-Path $Runtime 'node'
$OcDir     = Join-Path $Runtime 'opencode'
$PgDir     = Join-Path $Runtime 'postgres'
$PgData    = Join-Path $Runtime 'pgdata'

# ── 版本（集中在这里，方便升级）──────────────────────────────────────────
$PY_VER   = '3.11.9'
# 必须 Node 22+，不能降到 20：
# 依赖链 isomorphic-dompurify → jsdom → html-encoding-sniffer → @exodus/bytes
# 最后那个是纯 ESM 包，用 CommonJS 的 require() 加载它需要 Node 22.12+ 的
# require(esm) 支持。Node 20 下 next build 预渲染首页时必然 ERR_REQUIRE_ESM。
# apps/web/Dockerfile 用的就是 node:22-alpine —— 但 package.json 没写 engines，
# 这个隐性要求从代码里看不出来，是实测撞出来的。
$NODE_VER = '22.12.0'
$PG_VER   = '16.4-1'
$OC_VER   = 'v1.18.16'

$PY_URL   = "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-embed-amd64.zip"
$PIP_URL  = 'https://bootstrap.pypa.io/get-pip.py'
$NODE_URL = "https://nodejs.org/dist/v$NODE_VER/node-v$NODE_VER-win-x64.zip"
$PG_URL   = "https://get.enterprisedb.com/postgresql/postgresql-$PG_VER-windows-x64-binaries.zip"
$OC_URL   = "https://github.com/anomalyco/opencode/releases/download/$OC_VER/opencode-windows-x64.zip"

# ── 输出helpers ─────────────────────────────────────────────────────────
function Say($msg)  { Write-Host "  $msg" -ForegroundColor Gray }
function Step($msg) { Write-Host "`n[$script:StepNo/$script:StepTotal] $msg" -ForegroundColor Cyan; $script:StepNo++ }
function Ok($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  !!  $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "`nFAILED: $msg" -ForegroundColor Red; Write-Host "`n日志在 $LogDir"; Read-Host "`n按回车退出"; exit 1 }

$script:StepNo = 1
$script:StepTotal = 8

Write-Host ""
Write-Host "  Hunter Community · 原生模式" -ForegroundColor White
Write-Host "  不装 Docker · 不改系统环境 · 全部装在 .runtime\ 下" -ForegroundColor DarkGray
Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray

New-Item -ItemType Directory -Force -Path $Runtime, $LogDir | Out-Null

# 下载：已存在就跳过，支持断点重来
function Fetch($url, $dest, $label) {
    if (Test-Path $dest) { Say "$label 已下载，跳过"; return }
    Say "下载 $label ..."
    $tmp = "$dest.part"
    try {
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 600
        Move-Item -Force $tmp $dest
    } catch {
        if (Test-Path $tmp) { Remove-Item -Force $tmp }
        Die "下载 $label 失败: $($_.Exception.Message)`n  URL: $url"
    }
}

# 跑外部命令并把输出落到日志。
#
# 为什么不用 `& cmd ... 2>&1 | Out-File`：
# Windows PowerShell 5.1 会把原生程序写到 stderr 的**每一行**包装成 ErrorRecord，
# 配合脚本顶部的 $ErrorActionPreference='Stop' 就直接终止脚本 —— 哪怕那只是
# 一条进度信息。npm 正是把进度写 stderr 的，实测脚本一到 npm install 就静默退出。
# Start-Process 走独立进程 + 文件重定向，不经过 PS 的错误管道，也顺带切断了句柄继承。
function RunLogged($exe, $argList, $workDir, $logName) {
    $out = "$LogDir\$logName.log"
    $err = "$LogDir\$logName.err.log"
    $p = Start-Process -FilePath $exe -ArgumentList (QuoteArgs $argList) -WorkingDirectory $workDir `
        -NoNewWindow -Wait -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
    return $p.ExitCode
}

# Start-Process -ArgumentList 接数组时只是拿空格拼起来，**不会**给含空格的项加引号。
# 后果有两个，都实测踩过：
#   1. pg_ctl 的 -o "-p 5442 -h 127.0.0.1" 被拆散 → pg_ctl 把 5442 当成操作模式，报
#      "无效的操作模式 5442"
#   2. 用户把项目放在带空格的路径下（C:\Users\张三\我的文档\...）时，所有路径参数都会断
# 所以自己补引号。已经带引号的不重复加。
function QuoteArgs($argList) {
    return @($argList | ForEach-Object {
        $a = [string]$_
        if ($a -match '\s' -and $a -notmatch '^".*"$') { '"' + $a + '"' } else { $a }
    })
}

function Unzip($zip, $target, $label) {
    if (Test-Path $target) { Say "$label 已解压，跳过"; return }
    Say "解压 $label ..."
    $tmp = "$target.tmp"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    Move-Item -Force $tmp $target
}

# ═══ 1. Python ═══════════════════════════════════════════════════════════
Step "准备 Python $PY_VER"
$pyZip = Join-Path $Runtime "python-$PY_VER.zip"
Fetch $PY_URL $pyZip "Python ($PY_VER, 约 11MB)"
Unzip $pyZip $PyDir "Python"

$PyExe = Join-Path $PyDir 'python.exe'
if (-not (Test-Path $PyExe)) { Die "Python 解压后找不到 python.exe" }

# embeddable 版默认禁用 site-packages，必须放开 import site，否则 pip 装的包 import 不到
$pth = Get-ChildItem -Path $PyDir -Filter 'python*._pth' | Select-Object -First 1
if ($pth) {
    $c = Get-Content $pth.FullName -Raw
    if ($c -notmatch '(?m)^import site') {
        ($c -replace '(?m)^#\s*import site', 'import site').TrimEnd() + "`nimport site`n" |
            Set-Content -Path $pth.FullName -Encoding ASCII
        Say "已放开 embeddable Python 的 site-packages"
    }
}

if (-not (Test-Path (Join-Path $PyDir 'Scripts\pip.exe'))) {
    $getPip = Join-Path $Runtime 'get-pip.py'
    Fetch $PIP_URL $getPip 'get-pip.py'
    Say "安装 pip ..."
    $rc = RunLogged $PyExe @($getPip, '--no-warn-script-location') $Runtime 'pip-bootstrap'
    if ($rc -ne 0) { Die "pip 安装失败，详见 $LogDir\pip-bootstrap.log" }
}
Ok "Python 就绪"

# ═══ 2. Python 依赖 ══════════════════════════════════════════════════════
Step "安装 Python 依赖（首次约 3-5 分钟）"
$marker  = Join-Path $Runtime '.deps-installed'
$reqFile = Join-Path $Root 'apps\api\requirements.txt'
$reqHash = (Get-FileHash $reqFile -Algorithm MD5).Hash

# Windows 上装不了的包 —— 必须先剔掉，否则 pip 会尝试从源码编译然后失败：
#   uvloop  : 只支持 Unix，Windows 无 wheel 也无法编译（是 uvicorn 的可选加速依赖，
#             缺了 uvicorn 自动回落到标准 asyncio 事件循环，功能不受影响）
$WinSkip = @('uvloop')

if ((Test-Path $marker) -and ((Get-Content $marker -Raw).Trim() -eq $reqHash)) {
    Say "依赖未变化，跳过"
} else {
    $reqWin = Join-Path $Runtime 'requirements-win.txt'
    Get-Content $reqFile | Where-Object {
        $line = $_.Trim()
        if ($line -eq '' -or $line.StartsWith('#')) { return $true }
        $name = ($line -split '[=<>!~\[]')[0].Trim()
        if ($WinSkip -contains $name) { Say "跳过 $name（Windows 不支持）"; return $false }
        return $true
    } | Set-Content $reqWin -Encoding UTF8

    Say "pip install ...（输出在 $LogDir\pip-install.log）"
    # fakeredis 是原生模式专属：顶替 Redis 服务端，见 sitecustomize.py
    $rc = RunLogged $PyExe @('-m', 'pip', 'install', '--no-warn-script-location',
                             '-r', $reqWin, 'fakeredis') $Runtime 'pip-install'
    if ($rc -ne 0) { Die "依赖安装失败，详见 $LogDir\pip-install.log" }
    $reqHash | Set-Content $marker
}
Ok "Python 依赖就绪"

# ═══ 3. Node ════════════════════════════════════════════════════════════
Step "准备 Node.js $NODE_VER"
$nodeZip = Join-Path $Runtime "node-$NODE_VER.zip"
Fetch $NODE_URL $nodeZip "Node.js ($NODE_VER, 约 30MB)"
if (-not (Test-Path $NodeDir)) {
    Unzip $nodeZip "$NodeDir-raw" "Node.js"
    # zip 里套了一层 node-vX-win-x64/，拍平
    $inner = Get-ChildItem "$NodeDir-raw" -Directory | Select-Object -First 1
    Move-Item $inner.FullName $NodeDir
    Remove-Item -Recurse -Force "$NodeDir-raw"
}
$NodeExe = Join-Path $NodeDir 'node.exe'
$NpmCmd  = Join-Path $NodeDir 'npm.cmd'
if (-not (Test-Path $NodeExe)) { Die "Node 解压后找不到 node.exe" }
Ok "Node.js 就绪"

# ═══ 4. opencode ════════════════════════════════════════════════════════
Step "准备 opencode $OC_VER"
$ocZip = Join-Path $Runtime "opencode-$OC_VER.zip"
Fetch $OC_URL $ocZip "opencode ($OC_VER, 约 60MB)"
Unzip $ocZip $OcDir "opencode"
$OcExe = Get-ChildItem -Path $OcDir -Filter 'opencode.exe' -Recurse | Select-Object -First 1
if (-not $OcExe) { Die "opencode 解压后找不到 opencode.exe" }
$OcExe = $OcExe.FullName
Ok "opencode 就绪"

# ═══ 5. Postgres ════════════════════════════════════════════════════════
Step "准备 PostgreSQL $PG_VER"
$pgZip = Join-Path $Runtime "postgres-$PG_VER.zip"
Fetch $PG_URL $pgZip "PostgreSQL ($PG_VER, 约 300MB)"
if (-not (Test-Path $PgDir)) {
    Unzip $pgZip "$PgDir-raw" "PostgreSQL"
    $inner = Join-Path "$PgDir-raw" 'pgsql'
    if (Test-Path $inner) { Move-Item $inner $PgDir } else { Move-Item "$PgDir-raw" $PgDir }
    if (Test-Path "$PgDir-raw") { Remove-Item -Recurse -Force "$PgDir-raw" }
}
$PgBin     = Join-Path $PgDir 'bin'
$InitDbExe = Join-Path $PgBin 'initdb.exe'
$PgCtlExe  = Join-Path $PgBin 'pg_ctl.exe'
$PsqlExe   = Join-Path $PgBin 'psql.exe'
if (-not (Test-Path $InitDbExe)) { Die "PostgreSQL 解压后找不到 initdb.exe" }
Ok "PostgreSQL 就绪"

# ═══ 6. 配置 .env ════════════════════════════════════════════════════════
Step "生成配置"
$envFile = Join-Path $Root '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $Root '.env.example') $envFile
    Say "已从 .env.example 创建 .env"
}
$envText = Get-Content $envFile -Raw

# JWT_SECRET 还是仓库里的默认值就换成随机的 —— 那个默认值全世界都看得到
if ($envText -match '(?m)^JWT_SECRET=change-me') {
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $secret = ([Convert]::ToBase64String($bytes) -replace '[=/+]', '').Substring(0, 60)
    $envText = $envText -replace '(?m)^JWT_SECRET=.*', "JWT_SECRET=$secret"
    Say "已生成随机 JWT_SECRET"
}

function ReadEnv($key, $default) {
    if ($envText -match "(?m)^$key=(.*)$") { $v = $Matches[1].Trim(); if ($v) { return $v } }
    return $default
}
$WebPort = ReadEnv 'WEB_HOST_PORT'      '3100'
$ApiPort = ReadEnv 'API_HOST_PORT'      '8100'
$PgPort  = ReadEnv 'POSTGRES_HOST_PORT' '5442'
$PgUser  = ReadEnv 'POSTGRES_USER'      'hunter'
$PgPass  = ReadEnv 'POSTGRES_PASSWORD'  'hunter'
$PgDb    = ReadEnv 'POSTGRES_DB'        'hunter'
$OcPort  = '3901'

# 原生模式下各服务在同一台机器上，连接串必须指向 localhost 而不是容器名
# （compose 里写的是 postgres / api / opencode 这些容器名，原生模式下解析不了）
#
# NEXT_PUBLIC_API_URL 这个名字是对的，别改成 BASE：
#   前端代码读的是 process.env.NEXT_PUBLIC_API_URL（app/login/page.tsx 等 6 处），
#   而 .env.example 里写的却是 NEXT_PUBLIC_API_BASE —— 那个名字全项目没人读，是死配置。
#   设错名字的后果：API 常量取到空串，前端去请求 web 自己的 /api/auth/login，
#   Next.js 没这个路由 → 登录页报"网络错误，请稍后重试"。
#   （Docker 里同样没设这个变量，演示站能用是因为前面挂了 nginx 转发 /api/。）
# 注意它是 NEXT_PUBLIC_ 前缀 —— Next.js 在**构建期**把值烘进产物，所以必须在
# npm run build 之前就写进 .env 并注入环境变量。
$marker = '# ---- 以下由 scripts\native\start.ps1 自动写入（原生模式）----'
$appendLines = @(
    ''
    $marker
    "DATABASE_URL=postgresql://${PgUser}:${PgPass}@127.0.0.1:${PgPort}/${PgDb}"
    'REDIS_URL=redis://127.0.0.1:6379/0'
    "OPENCODE_URL=http://127.0.0.1:${OcPort}"
    "HERMES_API_URL=http://127.0.0.1:${ApiPort}"
    "NEXT_PUBLIC_API_URL=http://127.0.0.1:${ApiPort}"
)

# 先删掉上一次追加的那一段，否则每跑一次就多一份（实测跑 7 次 .env 里堆了 7 段）
$idx = $envText.IndexOf($marker)
if ($idx -ge 0) { $envText = $envText.Substring(0, $idx).TrimEnd() }
$envText = ($envText -replace '(?m)^DATABASE_URL=.*', '').TrimEnd()
$envText = $envText + "`n" + ($appendLines -join "`n") + "`n"
Set-Content -Path $envFile -Value $envText -Encoding UTF8 -NoNewline
Ok "配置就绪（Web :$WebPort · API :$ApiPort · DB :$PgPort）"

# 端口占用检查 —— 早失败好过起到一半才发现。
# 不查 Postgres 端口：上一次跑剩下的 postgres 还活着是正常的，下面会复用它。
foreach ($p in @($WebPort, $ApiPort, $OcPort)) {
    $inUse = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($inUse) {
        $owner = (Get-Process -Id $inUse[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
        Die "端口 $p 已被占用（PID $($inUse[0].OwningProcess) $owner）。先跑 scripts\native\stop.ps1，或改 .env 里的端口。"
    }
}

# ═══ 7. 启动 Postgres ════════════════════════════════════════════════════
Step "启动数据库"
if (-not (Test-Path (Join-Path $PgData 'PG_VERSION'))) {
    Say "首次初始化数据库 ..."
    $pwFile = Join-Path $Runtime '.pgpass.tmp'
    Set-Content -Path $pwFile -Value $PgPass -Encoding ASCII -NoNewline
    RunLogged $InitDbExe @('-D', $PgData, '-U', $PgUser, "--pwfile=$pwFile",
                           '-E', 'UTF8', '--locale=C') $Runtime 'pg-initdb' | Out-Null
    Remove-Item -Force $pwFile
    if (-not (Test-Path (Join-Path $PgData 'PG_VERSION'))) { Die "数据库初始化失败，详见 $LogDir\pg-initdb.log" }
}

# 已经在跑就不重复启动（脚本可以反复执行）
$alive = Get-NetTCPConnection -LocalPort $PgPort -State Listen -ErrorAction SilentlyContinue
if (-not $alive) {
    # 必须用 Start-Process 而不是直接 & 调用：
    # pg_ctl start 会把服务端进程 fork 出去，而那个子进程继承了当前控制台的
    # stdout/stderr 句柄并一直持有，导致 `& pg_ctl ... | Out-Null` 永远不返回
    # （实测卡死 4 分钟以上，但服务其实已经起来了）。
    # Start-Process + 重定向到文件切断了句柄继承，pg_ctl 才能正常返回。
    # 注意这里**不能**用 -Wait（RunLogged 就是 -Wait）：
    # pg_ctl 自己会很快退出，但它 fork 出的 postgres 服务端继承了重定向的
    # stdout/stderr 句柄并一直持有，导致调用方一直等下去（实测卡满 5 分钟，
    # 而这期间数据库其实早就起来了）。所以发出去就不管，靠下面轮询端口判断成败。
    $pgArgs = @('-D', $PgData, '-o', "-p $PgPort -h 127.0.0.1",
                '-l', "$LogDir\postgres.log", 'start')
    Start-Process -FilePath $PgCtlExe -ArgumentList (QuoteArgs $pgArgs) `
        -WorkingDirectory $Runtime -WindowStyle Hidden `
        -RedirectStandardOutput "$LogDir\pg-ctl.log" `
        -RedirectStandardError "$LogDir\pg-ctl.err.log" | Out-Null

    # 轮询端口而不是傻等固定秒数 —— 慢机器 3 秒不够，快机器 3 秒浪费
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        if (Get-NetTCPConnection -LocalPort $PgPort -State Listen -ErrorAction SilentlyContinue) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not (Get-NetTCPConnection -LocalPort $PgPort -State Listen -ErrorAction SilentlyContinue)) {
        Die "数据库启动超时，详见 $LogDir\postgres.log"
    }
} else {
    Say "数据库已在运行，复用"
}
$env:PGPASSWORD = $PgPass
$dbExists = & $PsqlExe -h 127.0.0.1 -p $PgPort -U $PgUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$PgDb'" 2>$null
if ($dbExists -ne '1') {
    RunLogged $PsqlExe @('-h', '127.0.0.1', '-p', $PgPort, '-U', $PgUser,
                         '-d', 'postgres', '-c', "CREATE DATABASE $PgDb") $Runtime 'pg-createdb' | Out-Null
    Say "已创建数据库 $PgDb"
}
Ok "数据库运行中 (:$PgPort)"

# ═══ 8. 启动三个服务 ═════════════════════════════════════════════════════
Step "启动服务"

# 让 sitecustomize.py 生效（把 Redis 换成内存实现），并让子进程读到 .env
$env:HUNTER_NATIVE_MODE = '1'
$env:PYTHONPATH         = $ScriptDir
$env:PYTHONUNBUFFERED   = '1'
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $k = $Matches[1]; $v = $Matches[2].Trim().Trim('"')
        if ($k -notmatch '^#') { [Environment]::SetEnvironmentVariable($k, $v, 'Process') }
    }
}

function Launch($name, $exe, $argList, $workDir) {
    $out = "$LogDir\$name.log"
    $p = Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory $workDir `
        -RedirectStandardOutput $out -RedirectStandardError "$LogDir\$name.err.log" `
        -WindowStyle Hidden -PassThru
    $p.Id | Set-Content (Join-Path $Runtime "$name.pid")
    Say "$name 已启动 (PID $($p.Id))"
    return $p
}

Launch 'api' $PyExe @('-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', $ApiPort) (Join-Path $Root 'apps\api') | Out-Null
Launch 'opencode' $OcExe @('serve', '--hostname', '127.0.0.1', '--port', $OcPort) $Root | Out-Null

# web 首次要装依赖 + 构建，之后跳过
$WebDir = Join-Path $Root 'apps\web'
$env:PATH = "$NodeDir;$env:PATH"
if (-not (Test-Path (Join-Path $WebDir 'node_modules'))) {
    Say "首次安装前端依赖（约 2-3 分钟）..."
    # 有锁文件就必须用 npm ci，不能用 npm install：
    #   · npm install 会解析出更新的传递依赖，实测拉进了纯 ESM 的 @exodus/bytes
    #     （isomorphic-dompurify → jsdom → html-encoding-sniffer 这条链），
    #     导致 next build 预渲染首页时 "require() of ES Module ... not supported" 而失败
    #   · 而且它会**改写仓库里的 package-lock.json** —— 用户 clone 下来跑一次就产生脏改动
    # Dockerfile 里用的也是 npm ci，保持一致。
    $lock = Join-Path $WebDir 'package-lock.json'
    $npmArgs = if (Test-Path $lock) { @('ci', '--no-audit', '--no-fund') }
               else { @('install', '--no-audit', '--no-fund') }
    if ($npmArgs[0] -eq 'ci') { Say "检测到 package-lock.json，用 npm ci 保证版本一致" }
    $rc = RunLogged $NpmCmd $npmArgs $WebDir 'npm-install'
    if ($rc -ne 0) { Die "前端依赖安装失败，详见 $LogDir\npm-install.log" }
}

# 构建成功的判据不能只看 BUILD_ID：预渲染阶段失败时 BUILD_ID 已经写下了，
# 但 .next 是残缺的（缺 prerender-manifest.json），next start 会直接 ENOENT 崩掉。
# 实测就踩了这个 —— 脚本以为构建成功，起了个必挂的 web。
$BuildOkMark = Join-Path $WebDir '.next\prerender-manifest.json'
if (-not (Test-Path $BuildOkMark)) {
    Say "构建前端（约 2-4 分钟）..."
    if (Test-Path (Join-Path $WebDir '.next')) {
        Remove-Item -Recurse -Force (Join-Path $WebDir '.next')   # 清掉上次的残局
    }
    $rc = RunLogged $NpmCmd @('run', 'build') $WebDir 'npm-build'
    if ($rc -ne 0 -or -not (Test-Path $BuildOkMark)) {
        Die "前端构建失败，详见 $LogDir\npm-build.err.log"
    }
}
# 直接起 next 的入口脚本，不经过 npm。
# 走 `node npm-cli.js run start` 会形成 node → npm → next-server 三层进程，
# 真正占着 3100 端口的是最里面那个孙进程，stop.ps1 按 PID 杀父进程杀不掉它，
# 下次启动就报"端口已被占用"。直接起 next 只有一层，起停都干净。
$NextBin = Join-Path $WebDir 'node_modules\next\dist\bin\next'
if (-not (Test-Path $NextBin)) { Die "找不到 next 可执行入口: $NextBin" }
Launch 'web' $NodeExe @($NextBin, 'start', '-p', $WebPort) $WebDir | Out-Null

# ── 等就绪 ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  等待服务就绪（首次启动 Next.js 要现编译路由，请耐心）..." -ForegroundColor Gray
$deadline = (Get-Date).AddMinutes(5)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$WebPort/" -UseBasicParsing -TimeoutSec 10
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 5
    Write-Host "." -NoNewline -ForegroundColor DarkGray
}
Write-Host ""

if (-not $ready) {
    Warn "5 分钟内没等到前端响应。服务可能还在编译，稍等再手动打开下面的地址。"
    Warn "若一直不行，看 $LogDir 下的日志。"
} else {
    Ok "全部就绪"
}

Write-Host ""
Write-Host "  ===================================================" -ForegroundColor Green
Write-Host "   打开浏览器访问:  http://localhost:$WebPort" -ForegroundColor White
Write-Host "   首次访问会引导你注册第一个账号（自动成为管理员）" -ForegroundColor DarkGray
Write-Host "  ===================================================" -ForegroundColor Green
Write-Host ""
Write-Host "   停止服务:  scripts\native\stop.ps1" -ForegroundColor DarkGray
Write-Host "   日志:      .runtime\logs\" -ForegroundColor DarkGray
Write-Host "   完全卸载:  删掉 .runtime 文件夹即可（不碰系统环境）" -ForegroundColor DarkGray
Write-Host ""

if ($ready) { Start-Process "http://localhost:$WebPort" }
Read-Host "按回车关闭本窗口（服务会继续在后台运行）"
