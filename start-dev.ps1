param(
  [switch]$DryRun,
  [switch]$SkipFrontendInstall,
  [switch]$SkipWorker
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'
$backendPython = Join-Path $backendDir '.venv\Scripts\python.exe'
$backendLogDir = Join-Path $root 'logs'
$backendOutLog = Join-Path $backendLogDir 'backend.out.log'
$backendErrLog = Join-Path $backendLogDir 'backend.err.log'
$frontendOutLog = Join-Path $backendLogDir 'frontend.out.log'
$frontendErrLog = Join-Path $backendLogDir 'frontend.err.log'
$backendUrl = 'http://127.0.0.1:8000/openapi.json'
$frontendUrl = 'http://127.0.0.1:5173/'
$startupTimeoutSeconds = 30
$backendPort = 8000
$frontendPort = 5173

function Test-HttpReady {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Url
  )

  try {
    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.Method = 'GET'
    $request.Timeout = 1000
    $request.ReadWriteTimeout = 1000
    $response = $request.GetResponse()
    $response.Close()
    return $true
  } catch {
    return $false
  }
}

function Wait-HttpReady {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Url,
    [Parameter(Mandatory = $false)]
    [int]$TimeoutSeconds = 10
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    if (Test-HttpReady -Url $Url) {
      return $true
    }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)

  return $false
}

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

function Get-PortUsage {
  param(
    [Parameter(Mandatory = $true)]
    [int]$Port
  )

  @(
    Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
      Where-Object { $_.State -in @('Listen', 'Bound') } |
      ForEach-Object {
        $process = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
        [pscustomobject]@{
          Address = $_.LocalAddress
          Port = $_.LocalPort
          State = $_.State
          ProcessId = $_.OwningProcess
          ProcessName = if ($process) { $process.ProcessName } else { 'unknown' }
        }
      } |
      Sort-Object ProcessId, Address -Unique
  )
}

function Get-IPv4PortUsage {
  param(
    [Parameter(Mandatory = $true)]
    [int]$Port
  )

  @(
    Get-PortUsage -Port $Port |
      Where-Object { $_.Address -in @('127.0.0.1', '0.0.0.0', '::', '[::]') }
  )
}

function Format-PortUsage {
  param(
    [Parameter(Mandatory = $true)]
    [object[]]$Usage
  )

  if (-not $Usage.Count) {
    return 'none'
  }

  return (($Usage | ForEach-Object {
    "{0}:{1} {2} (PID {3}, {4})" -f $_.Address, $_.Port, $_.State, $_.ProcessId, $_.ProcessName
  }) -join '; ')
}

function Initialize-LogFile {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  try {
    $stream = [System.IO.File]::Open(
      $Path,
      [System.IO.FileMode]::OpenOrCreate,
      [System.IO.FileAccess]::ReadWrite,
      [System.IO.FileShare]::None
    )
    $stream.SetLength(0)
    $stream.Close()
    return $Path
  } catch {
    $directory = Split-Path -Parent $Path
    $name = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $extension = [System.IO.Path]::GetExtension($Path)
    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $fallbackPath = Join-Path $directory ("{0}.{1}{2}" -f $name, $timestamp, $extension)
    '' | Set-Content -Path $fallbackPath
    return $fallbackPath
  }
}

function Stop-StartedProcess {
  param(
    [Parameter(Mandatory = $false)]
    [System.Diagnostics.Process]$Process
  )

  if ($null -eq $Process -or $Process.HasExited) {
    return
  }

  Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $backendPython)) {
  $backendPython = 'python'
}

if (-not (Test-Path $backendDir)) {
  throw "backend directory not found: $backendDir"
}

if (-not (Test-Path $frontendDir)) {
  throw "frontend directory not found: $frontendDir"
}

if (-not $SkipFrontendInstall) {
  $nodeModules = Join-Path $frontendDir 'node_modules'
  if (-not (Test-Path $nodeModules)) {
    Write-Host 'frontend/node_modules not found, running npm install...'
    & npm.cmd install --prefix $frontendDir
    if ($LASTEXITCODE -ne 0) {
      throw 'frontend dependency installation failed'
    }
  }
}

if ($DryRun) {
  Write-Host 'Dry run:'
  Write-Host "  Backend: $backendPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
  Write-Host '  Frontend: npm run dev -- --host 127.0.0.1 --port 5173'
  exit 0
}

New-Item -ItemType Directory -Force -Path $backendLogDir | Out-Null

$backendReady = Test-HttpReady -Url $backendUrl
$frontendReady = Test-HttpReady -Url $frontendUrl
$backendUsage = @(Get-IPv4PortUsage -Port $backendPort)
$frontendUsage = @(Get-IPv4PortUsage -Port $frontendPort)

if ($backendUsage.Count -and -not $backendReady) {
  $backendReady = Wait-HttpReady -Url $backendUrl -TimeoutSeconds 10
  if (-not $backendReady) {
    Write-Host "Backend port $backendPort is occupied but not HTTP-ready: $(Format-PortUsage -Usage $backendUsage)"
    Write-Host '请先停止占用该端口的旧进程，或修改启动端口后重试。'
    throw 'startup failed: backend port is occupied by an unhealthy process'
  }
}

if ($frontendUsage.Count -and -not $frontendReady) {
  $frontendReady = Wait-HttpReady -Url $frontendUrl -TimeoutSeconds 10
  if (-not $frontendReady) {
    Write-Host "Frontend port $frontendPort is occupied but not HTTP-ready: $(Format-PortUsage -Usage $frontendUsage)"
    Write-Host '请先停止占用该端口的旧进程，或修改启动端口后重试。'
    throw 'startup failed: frontend port is occupied by an unhealthy process'
  }
}

$backendProcess = $null
$frontendProcess = $null

# 后端运行时环境（与 backend/app/core/runtime_config.py 对齐）：默认开启业务持久化（SQLite）
$env:DATA_DIR = if (-not $env:DATA_DIR) { '.data' } else { $env:DATA_DIR }
$env:DATABASE_URL = if (-not $env:DATABASE_URL) { 'sqlite:///.data/app.db' } else { $env:DATABASE_URL }
$env:USE_SQLALCHEMY = if (-not $env:USE_SQLALCHEMY) { 'true' } else { $env:USE_SQLALCHEMY }
$env:USE_MILVUS = if (-not $env:USE_MILVUS) { 'true' } else { $env:USE_MILVUS }
$env:RAG_MOCK_FALLBACK = if (-not $env:RAG_MOCK_FALLBACK) { 'true' } else { $env:RAG_MOCK_FALLBACK }

$backendProcessId = 'reused'
$frontendProcessId = 'reused'
$workerStatus = 'skipped'

if (-not $SkipWorker) {
  # Redis 已运行时，自动复用或启动真实 Worker；未运行时保留本地启动能力并明确提示。
  $env:USE_CELERY = if (-not $env:USE_CELERY) { 'true' } else { $env:USE_CELERY }
  $env:CELERY_BROKER_URL = if (-not $env:CELERY_BROKER_URL) { 'redis://127.0.0.1:6379/0' } else { $env:CELERY_BROKER_URL }
  $env:CELERY_RESULT_BACKEND = if (-not $env:CELERY_RESULT_BACKEND) { 'redis://127.0.0.1:6379/1' } else { $env:CELERY_RESULT_BACKEND }
  $env:REDIS_URL = if (-not $env:REDIS_URL) { 'redis://127.0.0.1:6379/2' } else { $env:REDIS_URL }

  if (Test-BrokerTcpReady -BrokerUrl $env:CELERY_BROKER_URL) {
    $workerScript = Join-Path $root 'deploy\dev-worker.ps1'
    $workerCommand = Get-Command pwsh.exe -ErrorAction SilentlyContinue
    $workerShell = if ($workerCommand) { $workerCommand.Path } else { $null }
    if (-not $workerShell) {
      $workerCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
      $workerShell = if ($workerCommand) { $workerCommand.Path } else { $null }
    }

    if ($workerShell -and (Test-Path $workerScript)) {
      Write-Host 'Redis 已就绪，自动检查并启动 Celery Worker...'
      $workerArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$workerScript`""
      $workerStart = Start-Process -FilePath $workerShell `
        -ArgumentList $workerArgs `
        -WorkingDirectory $root -WindowStyle Hidden -Wait -PassThru
      if ($workerStart.ExitCode -eq 0) {
        $workerStatus = 'started/reused'
      } else {
        $workerStatus = "failed(exit=$($workerStart.ExitCode))"
        Write-Host "Worker 自动启动失败（exit=$($workerStart.ExitCode)），请检查 logs/celery-worker.err.log"
      }
    } else {
      $workerStatus = 'script-not-found'
      Write-Host "Worker 启动脚本不存在：$workerScript"
    }
  } else {
    $workerStatus = 'redis-not-ready'
    Write-Host "未检测到 Redis（$($env:CELERY_BROKER_URL)），跳过 Worker 自动启动。启动 redis-celery 后重新执行 .\start-dev.ps1"
  }
}

if (-not $backendReady) {
  $backendOutLog = Initialize-LogFile -Path $backendOutLog
  $backendErrLog = Initialize-LogFile -Path $backendErrLog
  $backendProcess = Start-Process `
    -FilePath $backendPython `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $backendPort, '--reload') `
    -WorkingDirectory $backendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $backendOutLog `
    -RedirectStandardError $backendErrLog `
    -PassThru
  $backendProcessId = $backendProcess.Id
}

if (-not $frontendReady) {
  $frontendOutLog = Initialize-LogFile -Path $frontendOutLog
  $frontendErrLog = Initialize-LogFile -Path $frontendErrLog
  $frontendProcess = Start-Process `
    -FilePath 'npm.cmd' `
    -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1', '--port', $frontendPort) `
    -WorkingDirectory $frontendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $frontendOutLog `
    -RedirectStandardError $frontendErrLog `
    -PassThru
  $frontendProcessId = $frontendProcess.Id
}

$deadline = (Get-Date).AddSeconds($startupTimeoutSeconds)
while ((Get-Date) -lt $deadline) {
  $backendReady = Test-HttpReady -Url $backendUrl
  $frontendReady = Test-HttpReady -Url $frontendUrl

  if ($backendReady -and $frontendReady) {
    break
  }

  if ($null -ne $backendProcess -and $backendProcess.HasExited -and -not $backendReady) {
    break
  }

  if ($null -ne $frontendProcess -and $frontendProcess.HasExited -and -not $frontendReady) {
    break
  }

  Start-Sleep -Seconds 1
}

if (-not ($backendReady -and $frontendReady)) {
  Write-Host 'At least one service did not become HTTP-ready. Check the logs:'
  Write-Host "  Backend ready:  $backendReady ($backendUrl)"
  Write-Host "  Frontend ready: $frontendReady ($frontendUrl)"
  Write-Host "  Backend log: $backendOutLog"
  Write-Host "  Backend err: $backendErrLog"
  Write-Host "  Frontend log: $frontendOutLog"
  Write-Host "  Frontend err: $frontendErrLog"
  Stop-StartedProcess -Process $backendProcess
  Stop-StartedProcess -Process $frontendProcess
  throw 'startup failed: service health check timed out or the process exited'
}

Write-Host 'Started:'
Write-Host '  Backend: http://127.0.0.1:8000'
Write-Host '  Frontend: http://127.0.0.1:5173'
Write-Host "  Logs: $backendLogDir"
Write-Host "  Backend PID: $backendProcessId"
Write-Host "  Frontend PID: $frontendProcessId"
Write-Host "  Worker: $workerStatus"
Write-Host '  下次可直接执行 .\start-dev.ps1；如需跳过 Worker，使用 .\start-dev.ps1 -SkipWorker'
