# 部署与真实落地清单（MVP + 团队版）

> 目的：确认平台功能可以闭环落地，并明确「默认降级/演示」的功能要开哪些开关、配哪些凭据才能变成真实执行。
> 适用：新部署团队/生产、或从本地演示切换真实外部执行。

---

## 一、功能闭环总览

主链路 **Issue → 检索 → 分析 → 计划 → 审批 → Diff → 测试 → PR 描述 → 回放** 已可真实跑通（已用真实任务验证：从平台需求/Issue 一路到测试通过 + PR 描述生成）。

按「落地状态」分三层：

| 层级 | 含义 | 涉及功能 |
|---|---|---|
| A 真实闭环 | 代码 + 数据 + 运行已验证 | 登录、项目/仓库、需求同步、代码索引(Milvus)、AI 分析、计划、审批、测试执行、PR 描述、回放审计、评论/通知/催办 |
| B 代码就位·凭据激活 | 代码完整，配齐密钥/开关后即真实 | LLM(deepseek)、向量(zhipu)、Milvus、Redis/Celery、MySQL、Gitee API |
| C 真实落地缺口 | 默认演示/留手工，需显式开启 | 真实仓库 Diff 执行、真实远端 PR 创建 |

---

## 二、外部依赖与凭据（B 层激活）

| 项 | 环境变量 | 默认 | 需真实时 |
|---|---|---|---|
| 业务库 | `DATABASE_URL` | team 强制 MySQL（否则启动报错） | 已配 `mysql+pymysql://...` |
| 异步任务 | `USE_CELERY` / `CELERY_BROKER_URL` | false / eager 降级 | 配 Redis + 启动 Worker |
| 向量库 | `USE_MILVUS` / `MILVUS_URI` | true | 起 Milvus(19530)，`RAG_MOCK_FALLBACK=false` |
| 文本模型 | `LLM_PROVIDER/LLM_API_KEY` | 无 Key → 规则兜底 | 填 deepseek Key |
| 向量模型 | `EMBEDDING_PROVIDER/ZHIPU_API_KEY` | 无 Key → token-hash | 填 zhipu embedding Key |
| 代码托管 | `GITEE_TOKEN` | — | 填有权限 Token（Issue/索引/PR） |
| 运维恢复码 | `AUTH_RECOVERY_CODE` | 留空有风险 | 生产必须配强随机值 |

---

## 三、测试执行配置（这是最容易踩坑的一环）

一定要按这套配，否则测试会 `pytest: not found` / `No module named 'app'` / 依赖超时：

1. **专用测试镜像**：构建并指向它
   ```
   docker build -f deploy/test-runner/Dockerfile -t platform-test-runner:base .
   export TEST_RUNNER_IMAGE=platform-test-runner:base
   export TEST_RUNNER_INSTALL_MODE=image
   export TEST_RUNNER_NETWORK=none
   ```
2. **测试跑真实**：`export TEST_RUNNER_MODE=docker`
3. **被测仓库必须有依赖声明**：仓库根/backend 建 `requirements.txt`（列顶层直接依赖），自动装配会按它装依赖
4. **测试命令用 python -m**（保证 sys.path 正确）：
   `cd backend && python -m pytest tests/ -q`
5. **超时留够**：`TEST_RUNNER_TIMEOUT`（装依赖+跑测试，建议 ≥600）
6. 依赖下载慢时，镜像已把 `PIP_INDEX_URL` 指到清华加速源；大依赖建议预装进镜像

> 团队版默认 `TEST_RUNNER_NETWORK=none` 与 `TEST_RUNNER_INSTALL_MODE=image` 配套使用。若必须在线安装依赖，需显式改为 `TEST_RUNNER_INSTALL_MODE=auto` 并允许受控网络。

> 已修复的坑：镜像无 pytest、仓库无 requirements、裸 `pytest` 缺 sys.path、大依赖在线超时——均已在代码/镜像/requirements 层面解决。

---

## 四、真实执行的开关（C 层 → 从演示切真实）

当前 **Diff 生成**与 **远端 PR 创建**默认是"演示兜底 / 留手工"。要用真实外部执行：

| 开关 | 作用 | 说明 |
|---|---|---|
| `ALLOW_SCRATCH_REAL_CHANGE=true` | 放开对真实仓库执行代码 Diff | Diff 从「演示兜底」切为「真实生成对仓库的改动」 |
| `E2E_SCRATCH_REPO_URLS` | 白名单允许真实变更的仓库地址 | 只有名单内仓库才允许真实 Diff/PR 写入 |
| 远端 PR | 在真实 Diff + 白名单命中 + Token 有写权限前提下可 push | 依赖上述开关与 `GITEE_TOKEN` 的 push 权限 |

> 未开这些时，系统**明确标注「演示/降级」**，不伪装成功；对外部/生产要真写仓库才开启。

> ✅ **已验证（2026-09-11）**：在 `health-middle-commercial`（授权 scratch 仓）上真实跑通：
> 真实 Diff `execution_mode=REAL`（对仓库做真实改动）→ commit → push work 分支 → 创建真实 PR
> **PR #1：https://gitee.com/yang-zhizemin/health-middle-commercial/pulls/1（state=open）**
> 相关代码改动：`diffs.real_change_source`持久化 + PR 门禁放宽为"必须是 REAL 真实 Diff"（demo 仍拒绝；ALLOW+白名单仍硬约束）。

---

## 五、多 Worker 并发安全（已根治）

多进程/多 Worker 同时推进同一任务会并发写库冲突。已用 **Redis 分布式锁（`agent_run:{task_id}`）** 保证同一任务只被投递一次 `run_agent`，worker 完成后释放续投。
- 只要配好 `REDIS_URL` 即生效；
- 这样可放心起多个 Worker / 多人并行。

---

## 六、落地启动顺序建议

1. 起外部依赖：MySQL、Redis、Milvus(19530)、构建测试镜像
2. 配齐 `.env` 凭据（数据库/LLM/向量/Gitee/恢复码）
3. `export TEST_RUNNER_MODE=docker` + `TEST_RUNNER_IMAGE=platform-test-runner:base`
4. 启动团队版后端 + Worker（`.\start-team.ps1`，Windows PowerShell 5.1 已做 stderr 兼容修复）
5. 配置被测仓库：校验/填测试命令、补 `requirements.txt`、白名单
6. 建一条任务走完整闭环验收（Issue→…→测试通过→PR 描述→回放）
7. 如需真实写代码/PR：按「四」开启 `ALLOW_SCRATCH_REAL_CHANGE` + 仓库白名单

---

## 七、验收判据

- 后端全量测试绿（当前基线 ≈496 passed）
- 前端 build（vue-tsc）绿 + 单测 ≈204 passed
- 一条真实任务能走完：分析→计划→审批→Diff→**测试通过(exit 0)**→PR 描述→回放
