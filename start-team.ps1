param(
  [Alias('Port')]
  [int]$BackendPort = 8002,
  [int]$FrontendPort = 5177,
  [int]$StartupTimeoutSeconds = 90,
  [switch]$DryRun,
  [switch]$SkipWorker,
  [switch]$SkipFrontend,
  [switch]$SkipFrontendInstall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'
$backendPython = Join-Path $backendDir '.venv\Scripts\python.exe'
$envFile = Join-Path $backendDir '.env'
$workerScript = Join-Path $root 'deploy\dev-worker.ps1'
$logsDir = Join-Path $root 'logs'
$backendOutLog = Join-Path $logsDir 'team-backend.out.log'
$backendErrLog = Join-Path $logsDir 'team-backend.err.log'
$frontendOutLog = Join-Path $logsDir 'team-frontend.out.log'
$frontendErrLog = Join-Path $logsDir 'team-frontend.err.log'
$backendBaseUrl = "http://127.0.0.1:$BackendPort"
$frontendUrl = "http://127.0.0.1:$FrontendPort/"
$backendReadyUrl = "$backendBaseUrl/api/health"
$frontendApiReadyUrl = "$($frontendUrl.TrimEnd('/'))/api/health"

if (-not (Test-Path $backendDir)) {
  throw "backend directory not found: $backendDir"
}

if (-not (Test-Path $frontendDir)) {
  throw "frontend directory not found: $frontendDir"
}

if (-not (Test-Path $backendPython)) {
  throw "Python virtual environment not found: $backendPython"
}

if (-not (Test-Path $envFile)) {
  throw "Team config file not found: $envFile`nCopy deploy\env\team.env.example to backend\.env and fill in DATABASE_URL and other secrets."
}

function Import-EnvFile {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  Get-Content -LiteralPath $Path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) {
      return
    }

    $key, $value = $line.Split('=', 2)
    $key = $key.Trim()
    $value = $value.Trim()
    $firstChar = if ($value.Length -gt 0) { $value.Substring(0, 1) } else { '' }
    $lastChar = if ($value.Length -gt 0) { $value.Substring($value.Length - 1, 1) } else { '' }
    $quotedWithDouble = $firstChar -eq [char]34 -and $lastChar -eq [char]34
    $quotedWithSingle = $firstChar -eq [char]39 -and $lastChar -eq [char]39
    if ($value.Length -ge 2 -and ($quotedWithDouble -or $quotedWithSingle)) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    if ($key -and -not [Environment]::GetEnvironmentVariable($key, 'Process')) {
      [Environment]::SetEnvironmentVariable($key, $value, 'Process')
    }
  }
}

function Test-MySqlConfig {
  if ($env:DATABASE_URL -and $env:DATABASE_URL -match '^mysql\+pymysql://') {
    return $true
  }

  $databaseLine = Get-Content -LiteralPath $envFile -ErrorAction Stop |
    Where-Object { $_ -match '^\s*DATABASE_URL\s*=\s*mysql\+pymysql://' } |
    Select-Object -First 1

  return $null -ne $databaseLine
}

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

function Test-FrontendApiReady {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Url
  )

  try {
    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.Method = 'GET'
    $request.Accept = 'application/json'
    $request.Timeout = 1500
    $request.ReadWriteTimeout = 1500
    $response = $request.GetResponse()
    try {
      return $response.StatusCode -eq [System.Net.HttpStatusCode]::OK
    } finally {
      $response.Close()
    }
  } catch {
    return $false
  }
}

function Wait-HttpReady {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Url,
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
    [System.Diagnostics.Process]$Process
  )

  if ($null -eq $Process -or $Process.HasExited) {
    return
  }

  Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
}

Import-EnvFile -Path $envFile

if (-not (Test-MySqlConfig)) {
  throw "Team mode requires a MySQL DATABASE_URL in $envFile"
}

# Secrets come from backend/.env. This script only sets fixed team runtime flags.
$env:APP_ENV = 'team'
$env:ENV_FILE = $envFile
$env:USE_SQLALCHEMY = 'true'
$env:USE_MILVUS = 'true'
$env:MILVUS_URI = if ($env:MILVUS_URI) { $env:MILVUS_URI } else { 'http://127.0.0.1:19530' }
$env:RAG_MOCK_FALLBACK = 'false'
$env:USE_CELERY = 'true'
$env:CELERY_BROKER_URL = if ($env:CELERY_BROKER_URL) { $env:CELERY_BROKER_URL } else { 'redis://127.0.0.1:6379/0' }
$env:CELERY_RESULT_BACKEND = if ($env:CELERY_RESULT_BACKEND) { $env:CELERY_RESULT_BACKEND } else { 'redis://127.0.0.1:6379/1' }
$env:REDIS_URL = if ($env:REDIS_URL) { $env:REDIS_URL } else { 'redis://127.0.0.1:6379/2' }
$env:TEST_RUNNER_MODE = 'docker'
$env:USE_GIT_CLONE_INDEX = 'true'

if ($BackendPort -lt 1 -or $BackendPort -gt 65535) {
  throw "BackendPort must be between 1 and 65535: $BackendPort"
}

if ($FrontendPort -lt 1 -or $FrontendPort -gt 65535) {
  throw "FrontendPort must be between 1 and 65535: $FrontendPort"
}

if ($StartupTimeoutSeconds -lt 10 -or $StartupTimeoutSeconds -gt 600) {
  throw "StartupTimeoutSeconds must be between 10 and 600: $StartupTimeoutSeconds"
}

if ($DryRun) {
  Write-Host 'Team startup configuration is valid:'
  Write-Host "  Backend: $backendBaseUrl"
  Write-Host "  Frontend: $(if ($SkipFrontend) { 'skip' } else { $frontendUrl })"
  Write-Host "  Frontend API proxy: $backendBaseUrl"
  Write-Host "  Startup timeout: $StartupTimeoutSeconds seconds"
  Write-Host "  APP_ENV: $($env:APP_ENV)"
  Write-Host "  DATABASE_URL: mysql+pymysql://***"
  Write-Host "  MILVUS_URI: $($env:MILVUS_URI)"
  Write-Host "  CELERY_BROKER_URL: $($env:CELERY_BROKER_URL)"
  Write-Host "  TEST_RUNNER_MODE: $($env:TEST_RUNNER_MODE)"
  Write-Host "  Worker: $(if ($SkipWorker) { 'skip' } else { 'auto-check/start' })"
  Write-Host "  Frontend dependencies: $(if ($SkipFrontend -or $SkipFrontendInstall) { 'skip install' } elseif (Test-Path (Join-Path $frontendDir 'node_modules')) { 'installed' } else { 'will install' })"
  exit 0
}

if (-not $SkipFrontend -and -not $SkipFrontendInstall) {
  $nodeModules = Join-Path $frontendDir 'node_modules'
  if (-not (Test-Path $nodeModules)) {
    Write-Host 'frontend/node_modules not found, running npm install...'
    & npm.cmd install --prefix $frontendDir
    if ($LASTEXITCODE -ne 0) {
      throw 'frontend dependency installation failed'
    }
  }
}

if (-not $SkipWorker) {
  if (-not (Test-Path $workerScript)) {
    throw "Worker script not found: $workerScript"
  }

  Write-Host 'Checking and starting the team Celery Worker...'
  $workerCommand = Get-Command pwsh.exe -ErrorAction SilentlyContinue
  $workerShell = if ($workerCommand) { $workerCommand.Path } else { $null }
  if (-not $workerShell) {
    $workerCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
    $workerShell = if ($workerCommand) { $workerCommand.Path } else { $null }
  }
  if (-not $workerShell) {
    throw 'PowerShell executable not found for starting the Celery Worker'
  }

  & $workerShell -NoProfile -ExecutionPolicy Bypass -File $workerScript
  if ($LASTEXITCODE -ne 0) {
    throw "Celery Worker failed to start. Exit code: $LASTEXITCODE"
  }
}

$backendProcess = $null
$frontendProcess = $null
$startupSucceeded = $false
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

try {
  $backendReady = Test-HttpReady -Url $backendReadyUrl
  $frontendReady = if ($SkipFrontend) { $true } else { Test-HttpReady -Url $frontendUrl }
  $backendUsage = @(Get-IPv4PortUsage -Port $BackendPort)
  $frontendUsage = @()
  if (-not $SkipFrontend) {
    $frontendUsage = @(Get-IPv4PortUsage -Port $FrontendPort)
  }

  if ($backendUsage.Count -and -not $backendReady) {
    $backendReady = Wait-HttpReady -Url $backendReadyUrl -TimeoutSeconds 10
    if (-not $backendReady) {
      Write-Host "Backend port $BackendPort is occupied but not HTTP-ready: $(Format-PortUsage -Usage $backendUsage)"
      Write-Host '请先停止占用该端口的旧进程，或修改启动端口后重试。'
      throw 'startup failed: backend port is occupied by an unhealthy process'
    }
  }

  if ($frontendUsage.Count -and -not $frontendReady) {
    $frontendReady = Wait-HttpReady -Url $frontendUrl -TimeoutSeconds 10
    if (-not $frontendReady) {
      Write-Host "Frontend port $FrontendPort is occupied but not HTTP-ready: $(Format-PortUsage -Usage $frontendUsage)"
      Write-Host '请先停止占用该端口的旧进程，或修改启动端口后重试。'
      throw 'startup failed: frontend port is occupied by an unhealthy process'
    }
  }

  if (-not $backendReady) {
    $backendOutLog = Initialize-LogFile -Path $backendOutLog
    $backendErrLog = Initialize-LogFile -Path $backendErrLog
    $backendProcess = Start-Process `
      -FilePath $backendPython `
      -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $BackendPort) `
      -WorkingDirectory $backendDir `
      -WindowStyle Hidden `
      -RedirectStandardOutput $backendOutLog `
      -RedirectStandardError $backendErrLog `
      -PassThru
  }

  if (-not $SkipFrontend -and -not $frontendReady) {
    $env:VITE_API_PROXY_TARGET = $backendBaseUrl
    $env:VITE_DATA_SOURCE = 'real'
    $frontendOutLog = Initialize-LogFile -Path $frontendOutLog
    $frontendErrLog = Initialize-LogFile -Path $frontendErrLog
    $frontendProcess = Start-Process `
      -FilePath 'npm.cmd' `
      -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1', '--port', $FrontendPort) `
      -WorkingDirectory $frontendDir `
      -WindowStyle Hidden `
      -RedirectStandardOutput $frontendOutLog `
      -RedirectStandardError $frontendErrLog `
      -PassThru
  }

  $deadline = (Get-Date).AddSeconds($startupTimeoutSeconds)
  $frontendApiReady = $SkipFrontend
  while ((Get-Date) -lt $deadline) {
    $backendReady = Test-HttpReady -Url $backendReadyUrl
    $frontendReady = if ($SkipFrontend) { $true } else { Test-HttpReady -Url $frontendUrl }
    $frontendApiReady = if ($SkipFrontend) { $true } else { Test-FrontendApiReady -Url $frontendApiReadyUrl }

    if ($backendReady -and $frontendReady -and $frontendApiReady) {
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

  if (-not ($backendReady -and $frontendReady -and $frontendApiReady)) {
    Write-Host 'At least one team service did not become HTTP-ready. Check the logs:'
    Write-Host "  Backend ready:  $backendReady ($backendReadyUrl)"
    Write-Host "  Frontend ready: $frontendReady ($frontendUrl)"
    Write-Host "  Frontend API proxy ready: $frontendApiReady ($frontendApiReadyUrl)"
    Write-Host "  Backend log: $backendOutLog"
    Write-Host "  Backend err: $backendErrLog"
    Write-Host "  Frontend log: $frontendOutLog"
    Write-Host "  Frontend err: $frontendErrLog"
    throw 'startup failed: team service health check timed out or the process exited'
  }

  $startupSucceeded = $true
} finally {
  if (-not $startupSucceeded) {
    Stop-StartedProcess -Process $backendProcess
    Stop-StartedProcess -Process $frontendProcess
  }
}

Write-Host 'Started team services:'
Write-Host "  Frontend: $(if ($SkipFrontend) { 'skipped' } else { $frontendUrl })"
Write-Host "  Backend:  $backendBaseUrl"
Write-Host "  API proxy: $backendBaseUrl"
Write-Host "  Backend PID: $(if ($null -ne $backendProcess) { $backendProcess.Id } else { 'reused' })"
Write-Host "  Frontend PID: $(if ($SkipFrontend) { 'skipped' } elseif ($null -ne $frontendProcess) { $frontendProcess.Id } else { 'reused' })"
Write-Host "  Worker: $(if ($SkipWorker) { 'skipped' } else { 'started/reused' })"
Write-Host "  Logs: $logsDir"
Write-Host 'Config source: backend/.env (secrets are not stored in this script)'
Write-Host '下次可直接执行 .\start-team.ps1；如需跳过 Worker，使用 .\start-team.ps1 -SkipWorker'
