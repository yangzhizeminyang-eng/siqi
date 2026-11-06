# Contributing

感谢你关注司契（Siqi）。

这个项目强调真实研发闭环：需求、检索、计划、审批、Diff、测试、PR 描述、回放与审计都需要可复现、可验证。

## 开发前

- 先阅读 [README.md](README.md) 和 [docs/README.md](docs/README.md)。
- 新功能先确认范围、角色权限、状态流转和验收标准。
- 触及核心逻辑时优先补测试，尤其是审批、Runtime、RAG、测试执行和权限边界。
- 不提交本地密钥、Cookie、运行日志、缓存、真实 `.env` 或临时验证工作区。

## 本地启动

```powershell
.\start-dev.ps1
```

团队版本地联调：

```powershell
.\start-team.ps1 -DryRun
.\start-team.ps1
```

## 测试

后端：

```powershell
cd backend
pytest
```

前端：

```powershell
cd frontend
npm test
npm run build
```

提交前建议至少执行：

```powershell
git diff --check
```

## 提交规范

建议使用简洁的 Conventional Commit 风格：

- `feat:` 新功能
- `fix:` 缺陷修复
- `test:` 测试补充或调整
- `docs:` 文档更新
- `refactor:` 不改变行为的结构调整
- `chore:` 工程化、清理、配置

示例：

```text
feat: add enterprise runtime approval gate regression
docs: archive e4 release evidence
chore: remove generated runtime artifacts
```

## Pull Request 要求

- 描述变更目标、影响范围和验收方式。
- 列出实际执行过的测试命令和结果。
- 涉及接口、配置、部署或流程变化时同步更新文档。
- 涉及页面变化时提供真实运行截图。
- 不把未完成事项写成已完成，未验证内容必须明确标注。

