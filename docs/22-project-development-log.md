# 项目开发记录文档（旧入口）

> 新的主目录已迁移到 [docs/project-development/README.md](project-development/README.md)。
> 后续的项目开发记录请优先写到 `docs/project-development/` 下。

> **复核记录（2026-10-16）**：本存根自 2026-09 起停止更新；下文 §3 表格的 7 行「未开始」为立项时规划快照，勿据此判断进度。当前进度以 [PROJECT_PROGRESS.md](../PROJECT_PROGRESS.md) 与 [docs/project-development/00-project-development-log.md](project-development/00-project-development-log.md) 为准。

## 1. 文档用途

本文档用于记录项目每个功能模块从开发到验收的真实过程。以后每完成一个功能模块，都要在这里补充一条记录，避免只留下代码，看不到过程、问题和验证结果。

每条记录至少包含：

- 真实内容截图。
- 相关模块功能。
- 遇到的问题。
- 问题如何解决。
- 测试与验收结果。

## 2. 截图存放约定

截图统一放在：

```text
docs/assets/screenshots/
```

命名建议：

```text
YYYYMMDD-模块名-页面或功能.png
```

示例：

```text
20260901-task-list-page.png
20260901-create-task-error.png
20260901-rag-retrieval-result.png
```

## 3. 模块记录总表

> 本表为早期规划快照，编号已被 `docs/project-development/00-project-development-log.md` 的总表取代（当前主线编号下 DEV-007 = AI Issue 分析、DEV-008 = 代码 RAG 与索引、DEV-009 = 修改计划模块、DEV-010 = 审批模块、DEV-011 = Diff 生成与展示、DEV-012 = 测试执行模块、DEV-013 = PR 描述生成）。后续模块状态请以新总表为准。

| 编号 | 模块 | 状态 | 完成日期 | 关联文档 | 截图 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| DEV-001 | 项目骨架 | 未开始 |  | `12-development-plan.md` |  |  |
| DEV-002 | 任务与 Gitee Issue | 未开始 |  | `02-prd.md`、`08-api-design.md` |  |  |
| DEV-003 | LlamaIndex + Milvus RAG | 未开始 |  | `06-rag-design.md` |  |  |
| DEV-004 | AI 分析与修改计划 | 未开始 |  | `02-prd.md`、`03-mvp-workflow.md` |  |  |
| DEV-005 | 人工审批 | 未开始 |  | `02-prd.md`、`07-ui-high-fidelity.md` |  |  |
| DEV-006 | Diff、测试与 PR 描述 | 未开始 |  | `08-api-design.md`、`09-database-design.md` |  |  |
| DEV-007 | 任务回放与审计 | 未开始 |  | `10-ai-safety.md`、`11-test-and-demo.md` |  |  |

## 4. 单模块记录模板

复制下面模板，每完成一个模块就新增一节。

````markdown
## DEV-XXX 模块名称

### 1. 基本信息

| 项目 | 内容 |
| --- | --- |
| 模块名称 |  |
| 开发阶段 | MVP / 团队版 / 企业版 |
| 开始日期 |  |
| 完成日期 |  |
| 负责人 |  |
| 关联文档 |  |
| 关联接口 |  |
| 关联数据表 |  |

### 2. 真实内容截图

| 截图 | 说明 |
| --- | --- |
| `docs/assets/screenshots/xxx.png` |  |

### 3. 模块功能

- 

### 4. 实现流程

```mermaid
flowchart TD
    A[开始] --> B[核心步骤]
    B --> C[完成]
````

### 5. 遇到的问题

| 问题 | 原因 | 影响 |
| --- | --- | --- |
|  |  |  |

### 6. 解决方案

| 问题 | 解决方式 | 结果 |
| --- | --- | --- |
|  |  |  |

### 7. 测试与验收

| 验收项 | 操作 | 结果 |
| --- | --- | --- |
|  |  |  |

### 8. 复盘

- 做得好的地方：
- 需要加强的地方：
- 下次注意：
```

## 5. 记录规则

- 没有截图的模块不能标记为完成。
- 截图必须是真实运行页面、接口结果或控制台结果，不放概念图。
- 遇到的问题要写原因和解决方式，不只写“已修复”。
- 如果功能改了接口、数据库或 UI，要同步更新对应设计文档。
- 每次模块完成后，把模块记录总表状态改为“已完成”。
