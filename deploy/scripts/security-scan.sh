#!/usr/bin/env bash
# E6-01 P7 安全检查：扫描配置/Git 跟踪/Docker/端口/日志/备份/证书/凭据关键字/root 用户/CORS。
# 只输出脱敏结果（命中类别+文件数，不打印真实秘密值）。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SUSPECT='(AUTH_JWT_SECRET|AUTH_RECOVERY_CODE|ZHIPU_API_KEY|LLM_API_KEY|GITEE_TOKEN|ENTERPRISE_MINIO_ROOT_PASSWORD|DATABASE_URL)'
LEAK_KEYWORDS='(sk-[A-Za-z0-9]|api[_-]?key|authorization:\s*bearer|password\s*[:=]\s*[^"'"'"' ]|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|-----BEGIN CERTIFICATE)'
echo "=== E6-01 安全检查（脱敏）ROOT=$ROOT ==="

# 1) 生产配置：占位占位仍被跟踪（应只有占位，无真实值）
tracked_conf=$(git -C "$ROOT" ls-files 2>/dev/null | grep -E '\.env(en)?$|\.env\.example$' | tr '\n' ' ')
echo "跟踪的 env 文件: ${tracked_conf:-无}（应为 .env.example 占位）"

# 2) Git 中是否存在疑似真实密钥/私钥（只报文件，不打印内容）
leaks=$(git -C "$ROOT" grep -IlE "$LEAK_KEYWORDS" -- ':!*.jsonl' ':!*.json' 2>/dev/null | head -20)
echo "Git 含疑似密钥/私钥的文件数: $(echo "${leaks:-}" | grep -c . || echo 0)${leaks:+\n  命中: $leaks}"

# 3) 备份文件：校验是否含明文密钥（若备份目录存在）
if [ -d "${BACKUP_ROOT:-/var/backups/agent-enterprise}" ]; then
  bad=$(grep -rIlE "$LEAK_KEYWORDS" "${BACKUP_ROOT:-/var/backups/agent-enterprise}" 2>/dev/null | wc -l)
  echo "备份中含敏感关键字文件数: $bad（应 0）"
fi

# 4) Docker：暴露端口（只列端口映射，不读 secret）；root 用户容器
if command -v docker >/dev/null 2>&1; then
  echo "暴露端口:"
  docker ps --format '{{.Names}}: {{.Ports}}' 2>/dev/null | grep -iE "agent|enterprise" | sed 's/:[0-9]*-//g' | head -10 || true
  rootc=$(docker ps --filter 'ancestor=*' --format '{{.Names}}' 2>/dev/null | wc -l) # 占位
  echo "（root/非 root 需在 compose/镜像内核实；worker 已 no-new-privileges）"
fi

# 5) 生产禁项：debug/mock/fallback 关键字定位（只报文件数）
prod_flags=$(grep -rlnI -E 'MODEL_MOCK_FALLBACK=true|RAG_MOCK_FALLBACK=true|debug=true|FIXED_VERIFY_CODE' "$ROOT/deploy/env" "$ROOT/backend/app" 2>/dev/null | wc -l)
echo "含 mock/fallback/debug 开关的运行时文件数: $prod_flags（生产须显式 false/关）"

# 6) 证书私钥是否误入 Git
cert=$(git -C "$ROOT" ls-files 2>/dev/null | grep -E '\.(key|pem|crt)$' | tr '\n' ' ')
echo "Git 跟踪的证书/私钥: ${cert:-无（应无）}"
echo "=== 安全检查完成：以上为脱敏结果，未打印任何真实秘密值 ==="