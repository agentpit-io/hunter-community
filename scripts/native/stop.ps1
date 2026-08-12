# 停掉原生模式启动的所有服务（api / web / opencode / postgres）
$ErrorActionPreference = 'SilentlyContinue'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path
$Runtime   = Join-Path $Root '.runtime'

Write-Host "`n  停止 Hunter Community（原生模式）" -ForegroundColor White

# 递归杀整棵进程树。
# 只杀"父 + 直接子"是不够的：web 曾经是 node → npm → next-server 三层，
# 真正占端口的是最里层的孙进程，漏掉它下次启动就报"端口已被占用"。
function Stop-Tree($procId) {
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$procId" -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Tree $_.ProcessId }
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}

foreach ($name in @('web', 'api', 'opencode')) {
    $pidFile = Join-Path $Runtime "$name.pid"
    if (Test-Path $pidFile) {
        $procId = (Get-Content $pidFile -Raw).Trim()
        if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
            Stop-Tree $procId
            Write-Host "  已停止 $name (PID $procId)" -ForegroundColor Gray
        }
        Remove-Item -Force $pidFile
    }
}

# 兜底：pid 文件丢了 / 上次是异常退出时，按端口清掉残留
$Ports = @{ web = 3100; api = 8100; opencode = 3901 }
foreach ($name in $Ports.Keys) {
    $conn = Get-NetTCPConnection -LocalPort $Ports[$name] -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        $owner = Get-Process -Id $conn[0].OwningProcess -ErrorAction SilentlyContinue
        if ($owner -and $owner.ProcessName -match '^(node|python|opencode)$') {
            Stop-Tree $conn[0].OwningProcess
            Write-Host "  已清理残留 $name (端口 $($Ports[$name]) · PID $($conn[0].OwningProcess))" -ForegroundColor DarkGray
        }
    }
}

$PgCtl  = Join-Path $Runtime 'postgres\bin\pg_ctl.exe'
$PgData = Join-Path $Runtime 'pgdata'
if ((Test-Path $PgCtl) -and (Test-Path $PgData)) {
    & $PgCtl -D $PgData -m fast stop 2>&1 | Out-Null
    Write-Host "  已停止 postgres" -ForegroundColor Gray
}

Write-Host "  完成`n" -ForegroundColor Green
