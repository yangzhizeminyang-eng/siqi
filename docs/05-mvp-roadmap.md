# MVP 里程碑计划

## 阶段 0：项目骨架

目标：搭建可运行的前后端与基础服务。

交付物：

- Vue 3 + Vite + TypeScript 前端工程
- FastAPI 后端工程
- Docker Compose：MySQL、Redis、Milvus、后端、前端、Nginx
- 基础 README 和环境变量模板

## 阶段 1：任务与 Gitee Issue

目标：从 Gitee Issue 创建 Agent 任务。

交付物：

- 项目、仓库、任务数据模型
- Gitee Issue 拉取服务
- 创建任务 API
- 任务列表和任务详情基础页面
- 任务事件日志

## 阶段 2：代码索引与上下文检索

目标：完成仓库同步、代码预处理和 RAG 基础准备，为阶段三的 LlamaIndex + Milvus 知识库检索提供干净、可追溯的输入。

交付物：

- 仓库同步逻辑
- 文件过滤和敏感文件排除
- 代码文件解析准备与基础切片规则
- 索引任务状态记录
- 仓库 Commit 版本记录
- Milvus Collection 命名和索引版本规划

## 阶段 3：LlamaIndex + Milvus RAG 实现

目标：建立代码知识库，让 Agent 能基于 Issue 检索相关代码上下文。

交付物：

- LlamaIndex 数据接入和代码节点构建
- Embedding 模型接入
- Milvus Collection 创建
- 代码向量批量写入
- Issue 语义检索
- 文件路径、行号和 Commit SHA 元数据返回
- RAG 检索结果页面
- 索引失败重试和基础重建能力
- MySQL 与 Milvus 的元数据关联

验收：

- 指定仓库可以完成一次索引
- Issue 可以召回相关代码片段
- 检索结果可以追溯到具体文件和行号
- 项目、仓库和索引版本之间相互隔离

## 阶段 4：AI 分析与修改计划

目标：Agent 输出结构化分析和修改计划。

交付物：

- langgraph-api + LangChain + DeepAgents MVP 流程
- Issue 分析节点
- LlamaIndex RAG 检索节点
- 修改计划生成节点
- 计划版本存储
- 计划展示页面

## 阶段 5：人工审批

目标：建立可控执行门禁。

交付物：

- 审批 API
- 审批中心页面
- 审批通过 / 拒绝 / 要求重写
- 审批事件审计
- Agent 等待审批状态恢复

## 阶段 6：Diff、测试与 PR 描述

目标：跑通研发结果产物。

交付物：

- Diff 生成与存储
- Diff 展示页面
- Docker 隔离测试执行
- 测试结果记录
- PR 描述生成

## 阶段 7：任务回放与演示闭环

目标：让用户能看清 Agent 做了什么。

交付物：

- 任务时间线
- 节点输入输出摘要
- 审批、Diff、测试、PR 描述回放
- 演示数据与演示脚本

## 建议开发顺序

1. 先做任务主流程和状态流转，不急着接复杂 AI。
2. 再接 Gitee Issue 和仓库读取。
3. 完成代码预处理，明确索引版本和文件边界。
4. 实现 LlamaIndex + Milvus RAG。
5. 补 langgraph-api / LangChain / DeepAgents 节点，将 RAG 上下文注入分析流程。
6. 接入人工审批门禁。
7. 最后做 Diff、测试和任务回放体验。

## 第一版 Demo 成功画面

用户打开平台，输入 Gitee 仓库和 Issue ID，系统自动分析问题、找到相关代码、生成修改计划。审批人点击通过后，Agent 生成 Diff、运行测试并产出 PR 描述。整个过程可以在任务回放页按时间线查看。
