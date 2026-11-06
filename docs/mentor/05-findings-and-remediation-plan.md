# 15 个发现的修复方案（可执行）

> 来源：`docs/mentor/02-guided-tour-log.md` §5.1 / §9 / §11.5 / §12.6 / §14 / §15 / §16
> 生成：2026-10-08 ｜ 原则：**每条都可追溯到行号或实测输出**，未验证的一律标注。
> **本文只给方案，未改任何代码。** 需要你确认后才动手。

---

## 1. 总览（按优先级排序）

| ID | 问题 | 严重度 | 生产在线 | 改动量 | 需你决策 |
| --- | --- | --- | --- | --- | --- |
| **F1** | 未认证可读运维指标端点 | **中高** | ✅ 已确认 | 低 | ✅ 三选一 |
| **F12** | 安全头被重复添加 + 验收脚本只查存在 | **中** | ✅ 已确认 | 低 | 否 |
| **F13** | `backup.sh` 把 DB 口令放进进程命令行 | 中 | ✅ 脚本本身 | 低 | 否 |
| **F2** | Runtime 路由无权限门禁（文档称有 403）| **高** | 结构同源，应同样成立 | 中 | ✅ 定权限 |
| **F15** | 测试执行两套实现漂移 → 回放结论相反 | **中高** | — | 中 | 否 |
| **F8** | `runtime.execute` 治理注册不可执行 | 中高 | 待确认 | 中 | ✅ 二选一 |
| **F14** | 失败任务被误标 `context_missing` | 中 | 待确认 | **极低** | 否 |
| **F3** | `/ops/status` 的 `dependencies` 恒为空 | 低 | 待确认 | 极低（但需统一形状）| 否 |
| **F4** | Runtime 前端守卫用 `rag:search` | 低 | — | 极低 | ✅ 随 F2 |
| F5 | 角色矩阵文档与代码不一致 | 低（文档）| — | 低 | 否 |
| F6 | `rag/reports` 泄露服务器绝对路径 | 低 | 待确认 | 极低 | 否 |
| F7 | 报告投影 `generated_at`/`index_id` 为 null | 低 | — | 低 | 否 |
| F9 | `AppShell` 测试夹具路由漂移 | 极低（测试噪声）| — | 低 | 否 |
| F10 | 「任何场景禁止 downgrade」表述过度 | 极低（文档）| — | 极低 | 否 |
| F11 | `known-flaky` 只写在文档，代码无标记 | 低（测试维护）| — | 极低 | 否 |

**修复顺序建议**：`F12 → F13 → F14 → F3 → F4+F2 → F15 → F8 → F1`，其余文档/测试项可批量做。
理由：先做「生产在线 + 改动极小 + 不需决策」的（F12/F13），再做「一行级正确性」（F14/F3），
然后是需要一起设计的权限模型（F2/F4），最后是需要你决策的（F1/F8）。

---

## 2. P0：生产在线且改动小

### F12 · 安全头重复 + 验收脚本盲区

**现象**（生产实测响应头）：
```text
X-Frame-Options: DENY,DENY
X-Content-Type-Options: nosniff,nosniff
Referrer-Policy: strict-origin-when-cross-origin,strict-origin-when-cross-origin   （列表合法，无害）
Strict-Transport-Security: max-age=31536000                                        （单值，正常）
```

**根因**：两层 nginx 都 `add_header` 同一批头：
- 宿主机 TLS 层 `deploy/nginx.enterprise.tls.conf:32-33`
- 容器内前端 `deploy/nginx.enterprise.conf:11-12`

**改法（推荐：静态头归容器，TLS 专属头归宿主机）**：
1. **删掉** `nginx.enterprise.tls.conf:32-33` 的 `X-Content-Type-Options` / `X-Frame-Options`
   （以及如果有 `Referrer-Policy` 一并删），**只保留 HSTS**
   —— 因为容器不知道自己被 TLS 终结，HSTS 只能由宿主机加；
   而三个静态头放到容器层，**在 app-only / 本地 compose（无宿主机 nginx）下也生效**；
2. 在 `nginx.enterprise.tls.conf` 顶部加注释写明这条分工规则，防止以后又有人加回去。

**验收脚本必须一起改**（否则同类问题还会漏）——`deploy/scripts/verify-https.sh:37-41`
目前只查头名。替换为值校验：

```bash
expect_header() {           # $1=头名  $2=期望精确值
  local raw value
  raw="$(echo "$HDRS" | grep -i "^$1:" | head -1)"
  [ -n "$raw" ] || fail "缺少安全头: $1"
  value="$(echo "$raw" | cut -d: -f2- | tr -d ' \r\n')"
  [ "$value" = "$2" ] || fail "安全头 $1 值不合法: '$value'（期望 '$2'）"
  echo "安全头 $1: '$value' OK"
}
expect_header X-Frame-Options DENY
expect_header X-Content-Type-Options nosniff
echo "$HDRS" | grep -qiE '^Strict-Transport-Security: *max-age=[0-9]+' \
  || fail "HSTS 缺失或格式不合法"
```

**回归**：`bash deploy/scripts/verify-https.sh agentforge.ltd` 必须 PASS，
且 `curl -sD - -o /dev/null https://agentforge.ltd/ | grep -i x-frame` 输出**不含逗号**。

**部署注意**：改宿主机 nginx 后需 `nginx -t && nginx -s reload`；改容器 nginx 需重建 frontend 镜像。

---

### F13 · `backup.sh` 口令进进程命令行

**现象/根因**：`deploy/scripts/backup.sh:27` 用 `-p"$mpass"` → 口令出现在进程命令行，
同机可 `ps` 看到；`:27` 末尾 `2>/dev/null` 还吞掉了 mysqldump 的真实错误。

> 注意边界：脚本 `:3` 声明的「备份文件不含明文密钥」**仍然成立**，问题在**调用方式**。

**改法**：

```bash
CNF="$(mktemp)"; chmod 600 "$CNF"
trap 'rm -f "$CNF"' EXIT
printf '[client]\nuser=%s\npassword=%s\nhost=%s\nport=%s\n' \
  "$muser" "$mpass" "${rhost%%:*}" "${rhost#*:}" > "$CNF"

mysqldump --defaults-extra-file="$CNF" --single-transaction --hex-blob \
  --routines --triggers "$mdb" > "$DB_DUMP_DIR/agent_enterprise.sql"
```
同时**去掉 `2>/dev/null`**，改为 `2>>"$DB_DUMP_DIR/mysqldump.err"` 并在失败时
把该文件内容摘要打进错误信息，保留可诊断性。

**回归**：备份跑通（`SHA256SUMS` 生成）；执行期间另开终端 `ps aux | grep mysqldump`
**不应看到口令**；故意给错口令时错误信息能指出原因。

---

### F14 · 失败任务被误标「上下文缺失」

**现象**：任何 Agent 节点异常失败的任务，`manual_reason` 都是 `context_missing`。

**根因（两个事实叠加）**：
- `main.py:2571` `mark_task_failed(..., manual_reason: ManualReason = "context_missing")` —— 默认值本身就不该是具体原因；
- `workflow.py:330` `node_fail` 调用时**没传** `manual_reason` → 吃默认值。
  （对比 `workflow.py:275` 的 Diff 失败就显式传了 `manual_reason="diff_failed"`。）

**改法**：
1. `main.py:330-343` 的 `ManualReason` Literal **新增 `"agent_failed"`**；
2. `main.py:2571` 默认值改为 `"none"`；
3. `workflow.py:330` 显式传 `manual_reason="agent_failed"`；
4. 检查其余调用点是否依赖旧默认值：
   `grep -n "mark_task_failed(" backend/app/*.py backend/app/**/*.py`，逐个确认。

**回归**：新增测试——让某节点抛异常，断言
`task.status == "FAILED"` 且 `task.manual_reason == "agent_failed"`（**不是** `context_missing`）。

---

## 3. P1：行为正确性（需要设计，不只是改一行）

### F3 · `/ops/status` 的 `dependencies` 恒为空

**根因（两处都错）**：`enterprise_ops/routes.py:75-76`
```python
resp = main.api_health() if hasattr(main, "api_health") else None   # ← 该属性不存在（:4809 叫 health）
deps = dict((resp or {}).get("dependencies", {})) if isinstance(resp, dict) else {}
                                                                     # ← 且 dependencies 在 data 里，不在信封上
```
**铁证**：`enterprise_ops/metrics.py:37` 的注释写着「**不再依赖不存在的 `main.api_health`**」
——同一 bug 在 metrics.py 已修，routes.py 漏改。

**改法（关键：不要只让它非空，要统一形状）**：
现在同一份信息有**两种形状**：
- `/api/health` → `{"db": {"status": "ok", "latency_ms": 1.1}, "milvus": {...}}`
- `/ops/metrics` → `{"db": "1", "redis": "1", ...}`（字符串布尔）

建议**抽一个共享采集函数**，例如 `app/core/dependency_health.py`：
```python
def collect_dependencies() -> dict[str, dict]:   # 统一返回 {name: {"status": ..., "detail": ...}}
```
然后 `/api/health`、`/ops/status`、`/ops/metrics` 三个出口都从它派生
（metrics 负责把 status 映射成 `"1"/"0"`）。
这样**以后加依赖只改一处**，也顺手解决了「认证接口信息比未认证接口少」的倒挂。

**回归**：
- `GET /api/enterprise/ops/status`（需 `org:manage`）返回的 `dependencies` **非空**且含 `db/milvus/redis`；
- 与 `GET /api/health` 的依赖结论**一致**（同一次调用内比对）；
- 断开 Milvus 后，两个接口都反映变化。

---

### F15 · 测试执行两套实现漂移

**现象（四处分歧，逐行核对）**：同一「测试环境不可用」情形——

| 维度 | A `execute_task_tests_worker`（`:3269`）| B `run_task_tests`（`:8593`）|
| --- | --- | --- |
| `task.status` | 无条件 `DIFF_GENERATED`（`:3348`）| `DIFF_GENERATED if TEST_RUNNING else 保持`（`:8689`）|
| `next_action` | `retry_test`（`:3352`）| `auto_run_test`（`:8693`）|
| 事件类型 | `TEST_RUN_UNAVAILABLE`（`:3354`）| `TEST_BLOCKED`（`:8695`）|
| 是否建 `execution_step` | **否**（`append_test_run_event` 无匹配分支，`:8588` 直落）| 是，`blocked`（`:8578`）|
| **回放摘要判定** | **`skipped`**（`_SKIPPED_TYPES`）| **`failed`**（`_FAILED_TYPES` 先判，`replay_service.py:91`）|

**改法**：
1. **抽共享核心**，两个入口只负责取参与返回格式：
   ```python
   def _finalize_test_run(task, record, outcome, *, entry: Literal["worker","http"]) -> dict:
       """测试收尾的唯一状态机实现：unavailable / passed / failed / retry 全在这里。"""
   ```
   `execute_task_tests_worker` 与 `run_task_tests` 都调它；
2. **统一事件类型**：环境不可用只用一个名字，并让它落进 `_SKIPPED_TYPES`
   （语义确实是「没执行」，不是「执行失败」）。
   若保留 `TEST_BLOCKED`（用于「命令被白名单拦截」），则**环境不可用必须换一个不在 `_FAILED_TYPES` 里的类型**；
3. **统一 `next_action`**：明确 `retry_test` 与 `auto_run_test` 的差别，
   并确认 `workflow.py:_router` 读哪一个（当前会有不一致信号）；
4. `append_test_run_event` 补上「环境不可用」分支，让它**也建步骤**（status=`blocked`），
   使 DEV-014 的「事件/步骤分离」契约在两条路径上都成立。

**回归（必须两条入口都测）**：
```python
@pytest.mark.parametrize("entry", ["worker", "http"])
def test_environment_unavailable_is_consistent(entry, ...):
    # 同一 outcome(error="docker unavailable")
    # 断言 task.status / next_action / event_type / 回放判定 四者在两入口下完全一致
```
这条测试**就是本次修复的验收标准**。

---

### F2 · Runtime 路由无权限门禁（+ F4 前端守卫错配）

**现象**：`enterprise_runtime/routes.py` 的 6 个路由（`:54/63/70/82/94/106`）**都没调 `require_permission`**，
只有租户隔离。实测 observer（企业角色 `auditor`，6 个权限里**无任何 Runtime 相关**）
调 `GET /api/enterprise/runtime/tasks` → **200**。而 E5 文档称「后端保留 403」。

**改法（需要你先定权限模型）**：
1. 在 `app/tenancy/permissions.py` 新增 `RUNTIME_VIEW = "runtime:view"`（和/或 `RUNTIME_OPERATE`）；
2. 授给谁？按 E7 试点 §5 矩阵反推：
   | 角色 | 矩阵里的 Runtime 列 | 建议授予 |
   | --- | --- | --- |
   | tenant_owner / tenant_admin | view | `runtime:view` |
   | developer / reviewer | view | `runtime:view` |
   | security_admin | view | `runtime:view` |
   | **auditor** | **重定向（不可见）** | **不授予** |
3. 6 个路由分别加：
   - `GET /tasks`、`GET /tasks/{id}` → `require_tenant_permission(ctx, RUNTIME_VIEW)`
   - `retry` / `takeover` / `takeover/complete` / `POST /tasks` → `RUNTIME_OPERATE`（只给 owner/admin）
4. **F4 同步**：`frontend/src/router/index.ts:60` 与 `AppShell.vue:127` 的
   `permission: ['rag:search']` 改为 `['runtime:view']`。

**回归**：
- auditor 调 `/api/enterprise/runtime/tasks` → **403**（不是 200）；
- auditor 直输 `/enterprise/runtime` → 跳回工作台（现有行为保持）；
- tenant_owner / developer → 200；
- **补一条后端测试**（这正是 E5 缺的那条，只有前端测试所以漏了）。

> **这条是本次最该修的安全项**：现在「前端隐藏」是唯一屏障，直连 API 就能绕过。

---

### F8 · `runtime.execute` 治理注册不可执行

**现象（四点证据）**：
1. `task:execute` 全仓**只出现 1 次**（`governance/operations.py:80` 自己），**无角色拥有、无常量定义**；
2. Runtime 实际走 `approve()`（`approvals/service.py:402-450`），**不校验 `required_permission`**
   → 所以能跑通，但**该操作的业务权限约束实际未生效**；
3. 走 `execute()` 必然 403（`:562-563`）→ **任何人执行不了**；
4. `governance/executor.py:19-38` **无 `runtime.execute` 分支** → 落到兜底
   `{"status": "ok", "summary": "已执行"}`（`:38`）——**未实现的操作会报「已执行」**。

**改法（二选一，或两者都做）**：

**(a) 让权限真实存在**
- `app/tenancy/permissions.py` 定义 `TASK_EXECUTE = "task:execute"`；
- 授予 `tenant_owner` / `security_admin`（**不要广授**——这是执行代码变更的权限）；
- `approve()` 也补上 `required_permission` 校验，使两条路径判定一致。

**(b) 消除不实返回**（无论选不选 (a) 都该做）
```python
# governance/executor.py 在 execute_operation 里显式声明
if operation == "runtime.execute":
    return {"status": "ok",
            "summary": "runtime.execute 无独立副作用：实际执行由 Runtime 编排器完成（审批通过即回写）"}
```
并**去掉兜底分支的 `"已执行"`** 文案——改成
`raise ValueError(f"未注册执行器的治理操作: {operation}")`，
让「注册了但没实现」**显式失败**，而不是谎报成功。

**回归**：
- 审批通过 `runtime.execute` 后，Runtime 正常推进（保持现有能力）；
- 直接调 `/execute` 该审批时，**错误信息明确**（要么 403 说清缺哪个权限，要么 400 说清该操作无执行器）；
- 全仓不再有 `"已执行"` 这种无信息量的成功文案。

---

## 4. P2：文档 / 测试 / 低危（可批量）

### F1 · 未认证的运维指标端点（**需你三选一**）

**现状**：`enterprise_ops/routes.py:58 def ops_metrics():` **无 `user` 依赖**；
`nginx.enterprise.conf:21` 把整个 `/api/` 反代 → **生产公网无凭据可读**
（实测 `agent_http_requests_total = 221978`）。其余企业端点全部 401。

**性质**：信息暴露（无租户数据/密钥/代码），但可探测服务存活与四依赖健康、可轮询推断流量与错误率。
**它是有意设计**：E7 试点走查 §3.1 在登录前就调它——**但没有任何文档声明这条边界**。

| 方案 | 改法 | 代价 |
| --- | --- | --- |
| **A. 加鉴权** | `def ops_metrics(user: CurrentUser)` + `require_tenant_permission(ctx, ORG_MANAGE)` | 需改 E7 §3.1 走查脚本的预期；Prometheus 要带凭据 |
| **B. 独立内部端口**（**最贴生产惯例**） | metrics 挂到另一个仅内网监听的 app/端口，nginx **不**反代该路径 | 改动最大，需加 compose/nginx 配置；本地开发要适配 |
| **C. 保留公开但声明边界** | 文档写明「指标端点为公开健康探针」，并从 `metrics_text()` 里剔除依赖健康等敏感 gauge | 最省事，风险仍在（仍可轮询流量）|

**我的建议**：**B 的简化版**——在 `nginx.enterprise.conf` 里对该路径单独 `deny all`，
同时保留容器内网可达（Prometheus 走内网或独立抓取端口）。
这样**公网读不到、内网抓得到**，且不用改应用代码。

**注意**：无论选哪个，都要**同步更新** `docs/enterprise/10-pilot/01-demo-data-and-walkthrough.md` §3.1，
否则交付包与实现不一致。

---

### F4 / F5 / F6 / F7 / F9 / F10 / F11

| ID | 改法 | 文件 |
| --- | --- | --- |
| **F4** | 随 F2 一起改（`['rag:search']` → `['runtime:view']`）| `frontend/src/router/index.ts:60`、`AppShell.vue:127` |
| **F5** | E7 §5 矩阵的 auditor 审批列「—」改为「**view 可读，不可 approve**」（代码授予了 `approval:view`）| `docs/enterprise/10-pilot/01-demo-data-and-walkthrough.md:74` |
| **F6** | 响应里删掉 `reports_dir`，或只返回目录名（`Path(dir).name`）| `backend/app/enterprise_rag/routes.py` |
| **F7** | 报告投影补 `generated_at`（取报告 `generated_at` 字段，缺失则退回文件 mtime）、`index_id`（从报告 `config_snapshot` 或文件名解析）；**确实取不到就显式返回 `null` 并在文档里写明**，不要留「看起来像没填」的状态 | 同上 |
| **F9** | 由真实路由表生成本地桩，或补齐 8 条缺失路由（`/requests`、`/settings`、`/account-recovery`、`/enterprise/{approvals,dashboard,rag,runtime,ops}`）| `frontend/src/components/__tests__/AppShell.spec.ts:36-45` |
| **F10** | `migrate.py:11` 改为「**生产路径前向-only / 禁止 downgrade；`run_upgrade_to` 仅供测试构造 legacy schema**」| `backend/app/db/migrate.py:11` |
| **F11** | 测试文件顶部加注释 + 在 `pyproject.toml`/`pytest.ini` 注册 `known_flaky` marker，指向 `docs/project-development/106` §132 | `backend/tests/test_dev015_concurrency.py` |

---

## 5. 从这 15 条提炼的工程规则（讲给别人听的部分）

这 15 条不是 15 个孤立的 bug，而是**五类系统性问题的样本**：

| 类型 | 涉及 | 规则 |
| --- | --- | --- |
| **重复逻辑漂移** | F15、F16(§16 的步骤缺失)、测试执行双实现 | **状态机只能写一份**；多入口只做「取参 + 返回格式」 |
| **默认值替调用方猜原因** | F14 | **默认值应当是「不声称任何具体原因」**，而不是猜一个 |
| **注册表与实现脱节** | F8 | 声明了约束就必须**可满足**；声明了操作就必须有**执行器或显式拒绝** |
| **验收脚本的盲区** | F12、F2（E5 只有前端测试）| **验收要查「值」不只查「存在」**；安全边界**必须前后端各测一次** |
| **诚实性缺口** | F1、F6、F7 | 降级要说降级（这条项目做得很好）→ **暴露面同样要说清楚** |

**最有价值的一条**：F12 和 F2 都栽在**同一件事**上——
**验证只覆盖了「东西存在」，没覆盖「东西正确」**。
`verify-https.sh` 查头名不查值；E5 只写前端测试不写后端 403 测试。
**这两个盲区放一起，就是这个项目当前最值得补的一课。**

---

## 6. 改完怎么验（统一回归清单）

```powershell
# 后端（内存态，不碰企业库）
cd backend; .\.venv\Scripts\python.exe -m pytest -q

# 前端
cd frontend; npm test

# 生产（只读）
bash deploy/scripts/verify-https.sh agentforge.ltd     # 改后必须值校验 PASS
curl -sD - -o /dev/null https://agentforge.ltd/ | grep -iE 'x-frame|x-content-type'   # 不得含逗号
```

**外加三条必须新写的测试**：
1. F15：同一 outcome 经 `worker` / `http` 两入口，四处结果一致（参数化）；
2. F2：auditor 调 `/api/enterprise/runtime/tasks` → **403**；
3. F14：节点异常失败 → `manual_reason == "agent_failed"`。

---

## 7. 我不会做的事

- **不在生产上点任何写操作**（重建索引 / 同步 Gitee / 启动 Runtime / 执行审批 / 改配置）；
- **不猜生产凭据**；
- **不在你确认前改代码**——本文档全部是方案，`git status` 应仍只有文档变更。
