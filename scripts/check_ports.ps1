# 宿主机端口占用检测（Windows PowerShell）。
#
# 读取仓库根 .env 里的 5 个 *_HOST_PORT（没有 .env 或没设置就用默认值），
# 逐个检查是否已有进程在监听。环境变量优先于 .env。
#
# 用法（在仓库根目录执行）：
#   powershell -ExecutionPolicy Bypass -File scripts/check_ports.ps1
#
# 退出码：全部空闲 0；有冲突 1。
#
# 注意：如果冲突的端口是本项目自己已经 `docker compose up` 起来的容器，
# 占用进程会显示为 com.docker.backend 之类，这种情况可以忽略。

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5 按控制台代码页输出，中文和 ✓/✗ 会乱码，这里统一成 UTF-8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root '.env'

# 读 .env 里的某个键（只解析 KEY=VALUE 行，不执行 .env 内容）。
function Read-EnvValue([string]$Key, [string]$Default) {
    $fromProcess = [Environment]::GetEnvironmentVariable($Key)
    if ($fromProcess) { return $fromProcess }
    if (Test-Path $EnvFile) {
        $line = Get-Content $EnvFile | Where-Object { $_ -match "^\s*$Key\s*=" } | Select-Object -Last 1
        if ($line) {
            $v = ($line -replace "^\s*$Key\s*=\s*", '') -replace '\s+#.*$', ''
            $v = $v.Trim().Trim('"', "'")
            if ($v) { return $v }
        }
    }
    return $Default
}

# 返回占用端口的进程描述；端口空闲时返回 $null。
function Get-PortOwner([int]$Port) {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $conn) { return $null }
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    if ($proc) { return "$($proc.ProcessName) (pid $($conn.OwningProcess))" }
    return "pid $($conn.OwningProcess)"
}

$services = @(
    @{ Name = 'web';      Var = 'WEB_HOST_PORT';      Default = '3100' },
    @{ Name = 'api';      Var = 'API_HOST_PORT';      Default = '8100' },
    @{ Name = 'postgres'; Var = 'POSTGRES_HOST_PORT'; Default = '5442' },
    @{ Name = 'redis';    Var = 'REDIS_HOST_PORT';    Default = '6479' },
    @{ Name = 'opencode'; Var = 'OPENCODE_HOST_PORT'; Default = '3921' }
)

$conflicts = 0
foreach ($s in $services) {
    $raw = Read-EnvValue $s.Var $s.Default
    $port = 0
    if (-not [int]::TryParse($raw, [ref]$port)) {
        $conflicts++
        Write-Host "✗ $($s.Name): $($s.Var) 的值 '$raw' 不是端口号，请修改 .env 中的 $($s.Var)"
        continue
    }
    $owner = Get-PortOwner $port
    if ($owner) {
        $conflicts++
        Write-Host "✗ $($s.Name) 端口 $port 已被占用：$owner"
        Write-Host "    → 修改 .env 中的 $($s.Var)（改成一个空闲端口）"
    } else {
        Write-Host "✓ $($s.Name) 端口 $port 空闲"
    }
}

# 改了 API 端口且配置了 NEXT_PUBLIC_API_URL：前端地址要同步改，并重新构建 web
$apiPort = Read-EnvValue 'API_HOST_PORT' '8100'
$apiUrl = Read-EnvValue 'NEXT_PUBLIC_API_URL' ''
if ($apiPort -ne '8100' -and $apiUrl) {
    Write-Host ''
    Write-Host "提醒：你改了 API_HOST_PORT，且 .env 里设置了 NEXT_PUBLIC_API_URL（$apiUrl）。"
    Write-Host '      请把 NEXT_PUBLIC_API_URL 改成新的 API 地址，然后重新构建 web：'
    Write-Host '      docker compose build web; docker compose up -d web'
}

if ($conflicts -gt 0) {
    Write-Host ''
    Write-Host "发现 $conflicts 个端口冲突，请按上面的提示修改 .env 后再 docker compose up -d。"
    exit 1
}

Write-Host ''
Write-Host '所有端口空闲。'
exit 0
