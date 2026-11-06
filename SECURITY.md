# Security Policy

## Supported Versions

当前仓库以主分支和活跃开发分支为安全修复目标。历史演示分支、临时验证分支和本地运行产物不承诺维护。

## Reporting a Vulnerability

如果你发现安全问题，请不要在公开 Issue 中粘贴密钥、Token、Cookie、数据库连接串或可直接利用的攻击细节。

建议报告内容包含：

- 影响的模块或接口；
- 复现步骤；
- 预期影响；
- 是否涉及凭据泄漏、越权、命令执行、审批绕过或数据越租户访问；
- 已验证的版本或提交号。

维护者会优先处理以下问题：

- 审批绕过；
- 越租户或越角色访问；
- Token、Cookie、API Key 泄漏；
- 测试 Runner 或 Agent 工具越权执行；
- RAG 检索返回未授权代码或敏感配置；
- 审计、回放、ReleaseEvidence 被伪造或篡改。

## Secret Handling

- 真实密钥只允许通过环境变量、部署密钥管理或本地 git-ignored `.env` 注入。
- 不提交 `backend/.env`、Cookie、日志、证书、私钥、测试账号外的真实密码。
- 文档和测试中的 token/password 只能使用明显的占位值或测试值。

