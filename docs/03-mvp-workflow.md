# MVP 流程与 Mermaid 图

## 1. 产品闭环流程

流程默认由系统自动推进。用户只在以下节点介入：高风险审批、需要补充业务信息、授权过期、索引或检索失败、命令被安全策略拦截、并发冲突。低风险且通过安全策略检查的任务可以按项目策略自动通过审批，并继续生成 Diff、执行测试和生成 PR 描述。

```mermaid
flowchart TD
    END((结束))
    A[Gitee Issue] --> B[创建 Agent 任务]
    B --> C[拉取 Issue 内容]
    C --> D{仓库级索引是否可复用}
    D -- 可复用 --> G[检索相关代码上下文]
    D -- 缺失或过期 --> E[同步并扫描 Gitee 仓库]
    E --> F[LlamaIndex 解析与代码切片]
    F --> F1[Embedding 写入 Milvus]
    F1 --> G[检索相关代码上下文]
    G --> H[AI 分析需求 / Bug]
    H --> I[生成修改计划]
    I --> J{是否需要人工介入}
    J -- 低风险且策略允许 --> J1[系统自动审批<br/>写入审批与审计]
    J -- 高风险 / 上下文不足 / 命令拦截 --> J2[进入人工待处理]
    J2 --> J3{人工审批}
    J3 -- 拒绝 --> K[记录拒绝原因并终止当前版本]
    J3 -- 要求重写 --> I
    J3 -- 通过 --> L[Agent 生成 Diff]
    J1 --> L
    L --> M[Docker 隔离运行测试]
    M --> N[生成 PR 描述]
    N --> END
    K --> END
```

前端联调口径：

- 工作台默认展示人工待处理项和自动化收益，不要求用户确认每个自动节点。
- 任务详情页只保留一个主动作，由后端 `next_action` 决定。
- 审批中心默认优先展示高风险与阻塞任务，低风险任务进入可批量或自动通过区域。
- 失败任务必须返回 `failed_node`、`failed_reason`、`retry_count` 和 `next_action=retry` 或 `update_token`。

## 2. Agent 执行状态机

```mermaid
stateDiagram-v2
    state "已创建" as CREATED
    state "Issue 已拉取" as ISSUE_FETCHED
    state "代码上下文已索引" as CONTEXT_INDEXED
    state "AI 分析完成" as ANALYZED
    state "修改计划已生成" as PLAN_GENERATED
    state "等待人工审批" as WAITING_APPROVAL
    state "审批通过" as APPROVED
    state "审批拒绝" as REJECTED
    state "Diff 已生成" as DIFF_GENERATED
    state "测试完成" as TESTED
    state "PR 描述已生成" as PR_DESC_GENERATED
    state "任务完成" as COMPLETED
    state "任务失败" as FAILED
    state "用户主动取消" as CANCELLED

    [*] --> CREATED
    CREATED --> ISSUE_FETCHED: 拉取 Issue
    ISSUE_FETCHED --> CONTEXT_INDEXED: 同步 / 索引仓库
    CONTEXT_INDEXED --> ANALYZED: AI 分析
    ANALYZED --> PLAN_GENERATED: 生成计划
    PLAN_GENERATED --> WAITING_APPROVAL: 提交审批
    WAITING_APPROVAL --> APPROVED: 审批通过
    WAITING_APPROVAL --> REJECTED: 审批拒绝
    WAITING_APPROVAL --> PLAN_GENERATED: 要求重写
    APPROVED --> DIFF_GENERATED: 生成 Diff
    DIFF_GENERATED --> TESTED: 执行测试
    TESTED --> PR_DESC_GENERATED: 生成 PR 描述
    PR_DESC_GENERATED --> COMPLETED
    ISSUE_FETCHED --> FAILED
    CONTEXT_INDEXED --> FAILED
    ANALYZED --> FAILED
    PLAN_GENERATED --> FAILED
    WAITING_APPROVAL --> FAILED
    APPROVED --> FAILED
    DIFF_GENERATED --> FAILED
    TESTED --> FAILED
    PR_DESC_GENERATED --> FAILED
    FAILED --> PLAN_GENERATED: 重新分析或重试
    FAILED --> ISSUE_FETCHED: 重新拉取 Issue
    FAILED --> CONTEXT_INDEXED: 重新索引仓库
    FAILED --> ANALYZED: 重新分析
    FAILED --> DIFF_GENERATED: 重新生成 Diff
    FAILED --> TESTED: 重新执行测试
    FAILED --> PR_DESC_GENERATED: 重新生成 PR 描述
    FAILED --> [*]: 超过重试上限
    REJECTED --> [*]
    CREATED --> CANCELLED: 用户取消
    ISSUE_FETCHED --> CANCELLED: 用户取消
    CONTEXT_INDEXED --> CANCELLED: 用户取消
    ANALYZED --> CANCELLED: 用户取消
    PLAN_GENERATED --> CANCELLED: 用户取消
    WAITING_APPROVAL --> CANCELLED: 用户取消
    APPROVED --> CANCELLED: 用户取消
    DIFF_GENERATED --> CANCELLED: 用户取消
    TESTED --> CANCELLED: 用户取消
    PR_DESC_GENERATED --> CANCELLED: 用户取消
    FAILED --> CANCELLED: 用户取消
    CANCELLED --> [*]
    COMPLETED --> [*]
```

## 3. 系统上下文图

```mermaid
flowchart LR
    U[企业研发用户] --> FE[Vue 3 前端]
    FE --> API[FastAPI 后端]
    API --> DB[(MySQL)]
    API --> Milvus[(Milvus)]
    API --> Redis[(Redis)]
    API --> Celery[Celery Worker]
    API --> Gitee[Gitee OpenAPI]
    Celery --> AgentRuntime[Agent Runtime]
    AgentRuntime --> LlamaIndex[LlamaIndex RAG]
    LlamaIndex --> Milvus
    LlamaIndex --> Embed[Embedding 模型]
    AgentRuntime --> LLM[DeepSeek LLM]
    AgentRuntime --> DB
    AgentRuntime --> Gitee
    Celery --> Docker[Docker 隔离执行环境]
    API --> Nginx[Nginx 反向代理]
```

## 4. 任务时序图

```mermaid
sequenceDiagram
    participant User as 用户
    participant FE as 前端
    participant API as FastAPI
    participant Gitee as Gitee OpenAPI
    participant Queue as Redis/Celery
    participant Agent as Agent Runtime
    participant RAG as LlamaIndex RAG
    participant Milvus as Milvus
    participant DB as MySQL
    participant Docker as Docker Runner

    User->>FE: 输入仓库与 Issue ID
    FE->>API: 创建 Agent 任务
    API->>Gitee: 拉取 Issue 信息
    Gitee-->>API: 返回 Issue 内容
    API->>DB: 保存任务与 Issue 快照
    API->>Queue: 投递分析任务
    Queue->>Agent: 启动 Agent 流程
    Agent->>DB: 读取 Issue 快照
    Agent->>Gitee: 拉取仓库代码
    Agent->>RAG: 请求代码解析与切片
    RAG->>Milvus: 写入代码向量
    Agent->>RAG: 检索相关代码上下文
    RAG->>Milvus: 执行相似度检索
    Milvus-->>RAG: 返回代码片段与元数据
    RAG->>DB: 保存索引版本与检索记录
    Agent->>DB: 保存 AI 分析与修改计划
    FE->>API: 审批计划
    API->>DB: 保存审批记录
    API->>Queue: 投递 Diff 生成任务
    Queue->>Agent: 继续执行
    Agent->>DB: 保存 Diff
    Agent->>Docker: 运行测试命令
    Docker-->>Agent: 返回测试结果
    Agent->>DB: 保存测试日志和 PR 描述
    User->>FE: 查看任务回放
    FE->>API: 获取时间线与审计日志
```

## 5. 审批流程图

```mermaid
flowchart TD
    A[AI 生成修改计划] --> B[进入待审批]
    B --> C[审批人查看计划]
    C --> D{审批动作}
    D -- 通过 --> E[记录审批通过]
    E --> F[允许生成 Diff]
    D -- 拒绝 --> G[记录拒绝原因并终止任务版本]
    D -- 要求重写 --> H[追加修改意见]
    H --> I[重新生成计划版本]
    I --> B
```

说明：拒绝终止当前任务版本（`REJECTED` 终态），继续推进需新建任务版本；“要求重写”只在 `WAITING_APPROVAL` 状态下可用，生成新计划版本后重新进入待审批。

## 6. 数据关系草图

```mermaid
erDiagram
    USER ||--o{ PROJECT : owns
    PROJECT ||--o{ REPOSITORY : contains
    PROJECT ||--o{ AGENT_TASK : has
    REPOSITORY ||--o{ CODE_CHUNK_METADATA : describes
    REPOSITORY ||--o{ REPOSITORY_INDEX : has
    AGENT_TASK ||--o{ TASK_EVENT : records
    AGENT_TASK ||--o{ APPROVAL : requires
    AGENT_TASK ||--o{ DIFF_FILE : generates
    AGENT_TASK ||--o{ TEST_RUN : runs
    AGENT_TASK ||--o{ ARTIFACT : produces

    USER {
        uuid id
        string name
        string email
        string role
    }

    PROJECT {
        uuid id
        string name
        string description
    }

    REPOSITORY {
        uuid id
        uuid project_id
        string gitee_url
        string default_branch
    }

    AGENT_TASK {
        uuid id
        uuid project_id
        uuid repository_id
        string issue_id
        string status
        string title
        datetime created_at
    }

    CODE_CHUNK_METADATA {
        uuid id
        uuid repository_id
        string chunk_id
        string file_path
        string commit_sha
        int start_line
        int end_line
        string language
    }

    REPOSITORY_INDEX {
        uuid id
        uuid repository_id
        string commit_sha
        string status
        int chunk_count
        datetime created_at
    }

    TASK_EVENT {
        uuid id
        uuid task_id
        string event_type
        json payload
        datetime created_at
    }

    APPROVAL {
        uuid id
        uuid task_id
        uuid approver_id
        string decision
        text comment
    }

    DIFF_FILE {
        uuid id
        uuid task_id
        string file_path
        text patch
    }

    TEST_RUN {
        uuid id
        uuid task_id
        string command
        int exit_code
        text stdout
        text stderr
    }

    ARTIFACT {
        uuid id
        uuid task_id
        string artifact_type
        json content
    }
```
