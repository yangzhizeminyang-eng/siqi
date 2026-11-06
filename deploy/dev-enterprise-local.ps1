# 企业版本地运行脚本（APP_ENV=enterprise 完整形态，非降级）
#
# 与 start-dev.ps1 的区别：
#   - APP_ENV=enterprise：禁止静默 eager，必须真实 Redis + Celery；
#   - USE_MILVUS=true 且 RAG_MOCK_FALLBACK=false / MODEL_MOCK_FALLBACK=false：
#     检索或模型不可用即显式报错，不静默降级；
#   - 独立库 agent_enterprise，不碰团队版 agent_team；
#   - 后端默认 8003、前端 5178，与团队版（8000/5173）并行不冲突。
#
# 依赖（需先就绪）：
#   - MySQL：原生 127.0.0.1:3306，且已建库 agent_enterprise（首次见 -InitDatabase）
#   - Redis：127.0.0.1:6379
#   - Milvus：127.0.0.1:19530
#   - 模型与 Gitee 密钥：来自 backend/.env（本脚本不写任何密钥）
#
# 用法（项目根目录）：
#   .\deploy\dev-enterprise-local.ps1                    # 起后端 + Worker
#   .\deploy\dev-enterprise-local.ps1 -InitDatabase      # 首次：建库 + Alembic 迁移
#   .\deploy\dev-enterprise-local.ps1 -BackendOnly       # 只起后端
#   .\deploy\dev-enterprise-local.ps1 -FrontendOnly      # 只起前端（5178 → 8003）
param(
  [int]$BackendPort = 8003,
  [int]$FrontendPort = 5178,
  [switch]$InitDatabase,
  [switch]$BackendOnly,
  [switch]$FrontendOnly
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'
$python = Join-Path $backendDir '.venv\Scripts\python.exe'
$envFile = Join-Path $backendDir '.env'
$logsDir = Join-Path $root 'logs'

if (-not (Test-Path $python)) { throw "找不到后端虚拟环境：$python" }
if (-not (Test-Path $envFile)) { throw "找不到 $envFile（模型/Gitee 密钥来源）" }
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

# ---- 从 backend/.env 取 DATABASE_URL；严格限制为本机企业库（不回显凭据）----
$teamUrl = (Select-String -Path $envFile -Pattern '^DATABASE_URL=' | Select-Object -First 1).Line.Substring(13)
if (-not $teamUrl) { throw "backend/.env 缺少 DATABASE_URL" }
$configOut = $null
Push-Location $backendDir
try {
  $env:SOURCE_DATABASE_URL = $teamUrl
  $configOut = & $python -c "import os; from scripts.init_mysql import enterprise_database_url; print(enterprise_database_url(os.environ['SOURCE_DATABASE_URL']))"
  $configExitCode = $LASTEXITCODE
} finally {
  Remove-Item Env:SOURCE_DATABASE_URL -ErrorAction SilentlyContinue
  Pop-Location
}
if ($configExitCode -ne 0 -or -not $configOut) {
  throw "DATABASE_URL 未通过本地企业库校验（仅允许 loopback:3306 的 agent_team/agent_enterprise）。"
}
$enterpriseUrl = ($configOut | Out-String).Trim()

$env:APP_ENV = 'enterprise'
$env:DATABASE_URL = $enterpriseUrl
$env:USE_SQLALCHEMY = 'true'
# 企业版：真实异步主链路
$env:USE_CELERY = 'true'
$env:CELERY_BROKER_URL = if (-not $env:CELERY_BROKER_URL) { 'redis://127.0.0.1:6379/0' } else { $env:CELERY_BROKER_URL }
$env:CELERY_RESULT_BACKEND = if (-not $env:CELERY_RESULT_BACKEND) { 'redis://127.0.0.1:6379/1' } else { $env:CELERY_RESULT_BACKEND }
$env:REDIS_URL = if (-not $env:REDIS_URL) { 'redis://127.0.0.1:6379/2' } else { $env:REDIS_URL }
# 企业版：真实向量检索
$env:USE_MILVUS = 'true'
$env:MILVUS_URI = if (-not $env:MILVUS_URI) { 'http://127.0.0.1:19530' } else { $env:MILVUS_URI }
# 重要：本机 Milvus 的 collection 名为 code_<index_id>（无前缀）。
# 若设 MILVUS_COLLECTION_PREFIX=enterprise，会去找 enterprise_code_<index_id> 而找不到；
# 企业版 RAG_MOCK_FALLBACK=false 时直接抛错，不会静默降级。
$env:MILVUS_COLLECTION_PREFIX = ''
$env:RAG_MOCK_FALLBACK = 'false'
$env:MODEL_MOCK_FALLBACK = 'false'
$env:RAG_RETRIEVAL_MODE = if (-not $env:RAG_RETRIEVAL_MODE) { 'hybrid' } else { $env:RAG_RETRIEVAL_MODE }
$env:RAG_SYMBOL_BOOST = if (-not $env:RAG_SYMBOL_BOOST) { '1.0' } else { $env:RAG_SYMBOL_BOOST }
$env:APP_PORT = "$BackendPort"

# 本地一次性密钥：不落盘、不打印。每次启动重新生成（重启会使旧会话失效，属预期）。
if (-not $env:AUTH_JWT_SECRET) {
  $env:AUTH_JWT_SECRET = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
}
if (-not $env:AUTH_RECOVERY_CODE) {
  $env:AUTH_RECOVERY_CODE = -join ((1..24) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
}

function Test-PortOpen([string]$TargetHost, [int]$TargetPort) {
  try {
    $c = [System.Net.Sockets.TcpClient]::new()
    try {
      $t = $c.BeginConnect($TargetHost, $TargetPort, $null, $null)
      if (-not $t.AsyncWaitHandle.WaitOne(1200, $false)) { return $false }
      $c.EndConnect($t); return $true
    } finally { $c.Close() }
  } catch { return $false }
}

# ---- 依赖预检：企业版不允许静默降级，前置缺失就直接报错退出 ----
$missing = @()
if (-not (Test-PortOpen '127.0.0.1' 3306)) { $missing += 'MySQL 127.0.0.1:3306' }
if (-not (Test-PortOpen '127.0.0.1' 6379)) { $missing += 'Redis 127.0.0.1:6379' }
if (-not (Test-PortOpen '127.0.0.1' 19530)) { $missing += 'Milvus 127.0.0.1:19530' }
if ($missing.Count) {
  Write-Host '[ERR] 企业版依赖未就绪（不允许降级运行）：' -ForegroundColor Red
  $missing | ForEach-Object { Write-Host "      - $_" }
  Write-Host '      Redis/Milvus 可用 Docker 起；缺 Milvus 时企业 RAG 会直接失败而非降级。'
  exit 2
}

if ($InitDatabase) {
  Write-Host '[init] 通过逐项签名授权门禁初始化 agent_enterprise 并执行迁移 ...'
  $previousAdminUrl = [Environment]::GetEnvironmentVariable('MYSQL_ADMIN_URL', 'Process')
  $previousDatabaseName = [Environment]::GetEnvironmentVariable('DATABASE_NAME', 'Process')
  Push-Location $backendDir
  try {
    $env:SOURCE_DATABASE_URL = $enterpriseUrl
    $adminUrl = & $python -c "import os; from scripts.init_mysql import admin_database_url; print(admin_database_url(os.environ['SOURCE_DATABASE_URL']))"
    if ($LASTEXITCODE -ne 0 -or -not $adminUrl) { throw '无法构造本机 agent_enterprise 管理连接。' }
    $env:MYSQL_ADMIN_URL = ($adminUrl | Out-String).Trim()
    $env:DATABASE_NAME = 'agent_enterprise'
    & $python -m scripts.init_mysql
    if ($LASTEXITCODE -ne 0) { throw "init_mysql 授权校验或数据库初始化失败（exit=$LASTEXITCODE）。" }
  } finally {
    if ($null -eq $previousAdminUrl) { Remove-Item Env:MYSQL_ADMIN_URL -ErrorAction SilentlyContinue }
    else { $env:MYSQL_ADMIN_URL = $previousAdminUrl }
    if ($null -eq $previousDatabaseName) { Remove-Item Env:DATABASE_NAME -ErrorAction SilentlyContinue }
    else { $env:DATABASE_NAME = $previousDatabaseName }
    Remove-Item Env:SOURCE_DATABASE_URL -ErrorAction SilentlyContinue
    Pop-Location
  }
}

if (-not $FrontendOnly) {
  $outLog = Join-Path $logsDir 'enterprise-backend.out.log'
  $errLog = Join-Path $logsDir 'enterprise-backend.err.log'
  '' | Set-Content $outLog; '' | Set-Content $errLog
  $backend = Start-Process -FilePath $python `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$BackendPort") `
    -WorkingDirectory $backendDir -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
  Write-Host "后端 PID=$($backend.Id)  http://127.0.0.1:$BackendPort  日志：$outLog"

  $deadline = (Get-Date).AddSeconds(120)
  $ready = $false
  while ((Get-Date) -lt $deadline) {
    try {
      $r = Invoke-WebRequest -Uri "http://127.0.0.1:$BackendPort/api/health" -TimeoutSec 4 -UseBasicParsing
      Write-Host "后端健康：HTTP $($r.StatusCode)"
      Write-Host $r.Content
      $ready = $true; break
    } catch { Start-Sleep -Seconds 3 }
  }
  if (-not $ready) {
    Write-Host "[ERR] 后端 120 秒未就绪，检查 $errLog" -ForegroundColor Red
    exit 1
  }

  if (-not $BackendOnly) {
    Write-Host '启动真实 Celery Worker ...'
    & (Join-Path $root 'deploy\dev-worker.ps1')
  }
}

if (-not $BackendOnly) {
  $fOut = Join-Path $logsDir 'enterprise-frontend.out.log'
  $fErr = Join-Path $logsDir 'enterprise-frontend.err.log'
  $env:VITE_API_PROXY_TARGET = "http://127.0.0.1:$BackendPort"
  Start-Process -FilePath 'npm.cmd' `
    -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1', '--port', "$FrontendPort") `
    -WorkingDirectory $frontendDir -WindowStyle Hidden `
    -RedirectStandardOutput $fOut -RedirectStandardError $fErr | Out-Null
  Write-Host "前端：http://127.0.0.1:$FrontendPort  （/api 代理 → $BackendPort）日志：$fOut"
}

Write-Host ''
Write-Host '企业版登录（脱敏演示账号，生产必须替换）：'
Write-Host '  admin@company.com / Admin123!   （团队角色 admin + 企业角色 tenant_owner）'
Write-Host '  observer@company.com / Observer123!   （团队 observer + 企业 auditor）'
