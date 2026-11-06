# 团队版业务流程

## 1. 主流程

```mermaid
flowchart TD
    A["开发者在平台创建需求/缺陷<br/>frontend: CreateTaskView"] --> B{"选择入口"}
    B -->|已有 Gitee Issue| C["拉取 Issue 快照<br/>backend: fetch_gitee_issue_snapshot"]
    B -->|平台新建| D["创建平台草稿<br/>team_requests"]
    D --> E["同步创建 Gitee Issue<br/>POST /api/team/issues/sync"]
    E -->|成功| C
    E -->|失败| F["保留草稿 + 同步失败原因<br/>manual_required=auth_expired/gitee_sync_failed"]
    C --> G["创建 Agent Task<br/>tasks"]
    G --> H["自动拉仓库与 Commit<br/>repositories + branch head"]
    H --> I{"索引是否可复用"}
    I -->|可复用| J["RAG 检索代码上下文"]
    I -->|过期/缺失| K["自动重建索引"]
    K --> J
    J --> L{"上下文是否足够"}
    L -->|不足| M["人工补充信息<br/>manual_required=context_missing"]
    M --> N["重新进入分析"]
    L -->|足够| N["AI 分析"]
    N --> O["生成修改计划"]
    O --> P{"风险分级"}
    P -->|低风险且无异常| Q["自动推进<br/>记录 AUTO_APPROVED"]
    P -->|高风险/敏感/异常| R["审批中心待处理"]
    R --> S{"审批结果"}
    S -->|通过| T["生成 Diff"]
    S -->|要求重写| O
    S -->|拒绝| U["任务失败，重试新轮次"]
    Q --> T
    T --> V["运行测试"]
    V --> W["生成 PR 描述"]
    W --> X["生成回放摘要"]
    X --> Y["沉淀任务复用索引"]
```

## 2. 入口规则

| 入口 | 团队版处理 |
| --- | --- |
| 已有 Gitee Issue ID | 沿用 MVP，平台拉取 Issue 后创建任务 |
| 平台内新建需求/缺陷 | 先保存平台草稿，再同步创建 Gitee Issue |
| 空 Issue | 允许创建任务，但进入人工补充信息 |
| 重复 Issue | 有未结束任务时跳转已有任务；已结束可新建版本 |

## 3. 审批分级规则

```mermaid
flowchart TD
    A["修改计划生成"] --> B{"风险/异常判断"}
    B -->|低风险 + 无敏感文件 + 命令安全 + 上下文充足| C["系统自动推进"]
    B -->|中高风险| D["进入审批中心"]
    B -->|敏感文件命中| D
    B -->|危险命令拦截| D
    B -->|上下文不足| E["人工补充信息"]
    B -->|模型输出异常| D
    C --> F["写入自动决策事件"]
    D --> G["指定审批人处理"]
    E --> H["开发者/负责人补充"]
```

团队版允许低风险任务自动推进，但必须满足：

- 计划风险为 low。
- 不命中敏感文件。
- 测试命令在白名单内。
- RAG 上下文 fresh。
- 没有阻塞问题。
- 系统写入自动决策事件，说明自动推进原因。

## 4. 异常与恢复

| 场景 | 处理方式 |
| --- | --- |
| Gitee Token 过期 | 任务进入 `manual_required=auth_expired`，提示更新授权后从失败节点重试 |
| 已有 Issue 拉取失败 | 任务进入 `manual_required=gitee_fetch_failed`，提示 Issue 不存在、无权限或网络异常 |
| 平台 Issue 创建同步失败 | 平台草稿保留，标记 `manual_required=gitee_sync_failed`，允许重新同步 |
| 索引过期 | 自动重建索引，任务进入等待索引 |
| Embedding 失败 | 索引进入失败态，可重试索引 |
| 上下文不足 | 任务暂停，要求补充 Issue 信息或手动指定文件 |
| 测试失败 | Diff 不被覆盖，允许有限重试 |
| 并发审批 | 第一条有效审批生效，后续返回冲突 |
| 并发创建 | 以 `repository_id + issue_id + 未结束状态` 做幂等 |

## 5. 回放沉淀

任务完成后自动生成复用摘要：

- Issue 摘要。
- 命中的代码上下文。
- 修改计划最终版本。
- Diff 文件范围和摘要。
- 测试命令与结果。
- PR 描述。
- 审批与风险调整记录。

复用摘要进入历史任务索引，下次检索时作为辅助上下文展示。
