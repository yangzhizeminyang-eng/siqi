# 启动真实 Celery Worker（必须连接 Redis broker）。
#
# 用法（根目录 PowerShell）：
#   .\deploy\dev-worker.ps1
#
# 说明：
#   - 默认连接本机 Redis：redis://127.0.0.1:6379/0，可用环境变量覆盖。
#   - 真实 Worker 必须连接 broker；Redis 不可达时明确报错并给出启动 Redis 指引。
#   - eager 不是 Worker，不构成异步验收；本脚本不会静默起 eager。
#   - Worker 日志写 logs/celery-worker.out.log / .err.log（供 scripts/check_redis_worker.py 验收）。
param(
  [switch]$PoolSolo,
  [switch]$NoReuseCheck
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root 'backend'
$backendPython = Join-Path $backendDir '.venv\Scripts\python.exe'
if (-not (Test-Path $backendPython)) { $backendPython = 'python' }

$logsDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$outLog = Join-Path $logsDir 'celery-worker.out.log'
$errLog = Join-Path $logsDir 'celery-worker.err.log'

$env:USE_CELERY = if (-not $env:USE_CELERY) { 'true' } else { $env:USE_CELERY }
$env:USE_SQLALCHEMY = if (-not $env:USE_SQLALCHEMY) { 'true' } else { $env:USE_SQLALCHEMY }
$env:DATABASE_URL = if (-not $env:DATABASE_URL) { 'sqlite:///.data/app.db' } else { $env:DATABASE_URL }
$env:CELERY_BROKER_URL = if (-not $env:CELERY_BROKER_URL) { 'redis://127.0.0.1:6379/0' } else { $env:CELERY_BROKER_URL }
$env:CELERY_RESULT_BACKEND = if (-not $env:CELERY_RESULT_BACKEND) { 'redis://127.0.0.1:6379/1' } else { $env:CELERY_RESULT_BACKEND }
$env:REDIS_URL = if (-not $env:REDIS_URL) { 'redis://127.0.0.1:6379/2' } else { $env:REDIS_URL }

function Test-BrokerTcpReady {
  param(
    [Parameter(Mandatory = $true)]
    [string]$BrokerUrl
  )

  try {
    $uri = [System.Uri]$BrokerUrl
    $hostName = $uri.Host
    $port = if ($uri.Port -gt 0) { $uri.Port } else { 6379 }
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
      $connect = $client.BeginConnect($hostName, $port, $null, $null)
      if (-not $connect.AsyncWaitHandle.WaitOne(1000, $false)) {
        return $false
      }
      $client.EndConnect($connect)
      return $true
    } finally {
      $client.Close()
    }
  } catch {
    return $false
  }
}

function Test-CeleryWorkerOnline {
  $code = @'
try:
    from app.workers.celery_app import celery_app
    workers = celery_app.control.inspect(timeout=1).ping() or {}
    print("online" if any(workers.values()) else "offline")
except Exception as error:
    print(f"offline: {type(error).__name__}: {error}")
'@
  Push-Location $backendDir
  $previousPreference = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $result = @(& $backendPython -c $code 2>&1)
    if ($LASTEXITCODE -ne 0) {
      return $false
    }
    return (($result -join "`n") -match "online")
  } finally {
    $ErrorActionPreference = $previousPreference
    Pop-Location
  }
}

if (-not (Test-BrokerTcpReady -BrokerUrl $env:CELERY_BROKER_URL)) {
  Write-Host "[ERR] Redis broker 不可达：$($env:CELERY_BROKER_URL)。真实 Worker 必须连接 broker；eager 不是 Worker，不构成异步验收。"
  Write-Host '      请先启动 Redis：'
  Write-Host '        Docker Desktop：启动 redis-celery 容器'
  Write-Host '        或命令：docker start redis-celery'
  Write-Host '        首次创建：docker run -d --name redis-celery -p 6379:6379 redis:7-alpine'
  exit 2
}

if (-not $NoReuseCheck -and (Test-CeleryWorkerOnline)) {
  Write-Host "Celery Worker 已在线，复用已有 Worker。broker=$($env:CELERY_BROKER_URL)"
  Write-Host "日志：$outLog / $errLog"
  exit 0
}

$args0 = @('-m', 'celery', '-A', 'app.workers.celery_app', 'worker', '--loglevel=info')
if ($PoolSolo -or $IsWindows -or $env:OS) { $args0 += '--pool=solo' }

'' | Set-Content -Path $outLog
'' | Set-Content -Path $errLog
Write-Host "启动 Celery Worker（broker=$($env:CELERY_BROKER_URL)，日志：$outLog）..."
$p = Start-Process -FilePath $backendPython -ArgumentList $args0 -WorkingDirectory $backendDir `
  -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Write-Host "Worker PID=$($p.Id) 已启动。日志：$outLog / $errLog"

$workerDeadline = (Get-Date).AddSeconds(20)
do {
  Start-Sleep -Seconds 1
  if ($p.HasExited) {
    Write-Host "[ERR] Celery Worker 进程已退出，Exit code=$($p.ExitCode)"
    Write-Host "      请检查：$errLog"
    exit 1
  }
  if (Test-CeleryWorkerOnline) {
    Write-Host "Celery Worker 已上线，broker=$($env:CELERY_BROKER_URL)"
    Write-Host "日志：$outLog / $errLog"
    exit 0
  }
} while ((Get-Date) -lt $workerDeadline)

Write-Host "[ERR] Celery Worker 在 20 秒内没有响应 ping。"
Write-Host "      请检查：$outLog / $errLog"
exit 1
