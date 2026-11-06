# 团队版 API 增量文档

> 迁移说明：团队版 API 新口径已整理到 `team/03-team-api-design.md`。本文保留为旧入口，避免历史引用失效。

## 1. 文档定位

本文档只记录团队版相对 MVP 新增或变化的接口。MVP 已有接口仍以 `08-api-design.md` 为准。

## 2. 成员接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/team/members` | 获取团队成员列表 |
| POST | `/api/team/members/invite` | 邀请成员 |
| PATCH | `/api/team/members/{member_id}/role` | 修改成员角色 |
| PATCH | `/api/team/members/{member_id}/status` | 启用或停用成员 |

## 3. 任务协作接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| PATCH | `/api/tasks/{task_id}/assignee` | 设置任务负责人 |
| PATCH | `/api/tasks/{task_id}/approvers` | 设置审批人 |
| POST | `/api/tasks/{task_id}/watchers` | 添加关注人 |
| DELETE | `/api/tasks/{task_id}/watchers/{user_id}` | 移除关注人 |

## 4. 评论接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/tasks/{task_id}/comments` | 查看任务评论 |
| POST | `/api/tasks/{task_id}/comments` | 新增评论 |
| PATCH | `/api/tasks/{task_id}/comments/{comment_id}` | 修改评论 |
| DELETE | `/api/tasks/{task_id}/comments/{comment_id}` | 删除评论 |

## 5. 通知接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/notifications` | 查看站内通知 |
| PATCH | `/api/notifications/{notification_id}/read` | 标记已读 |
| POST | `/api/tasks/{task_id}/approval/remind` | 催办审批 |

## 6. 接口约束

- 所有团队版接口都必须带项目权限校验。
- 成员、审批人、关注人必须属于当前团队。
- 催办接口需要限制频率，避免重复打扰。
- 评论内容需要做长度限制和敏感信息过滤。

## 7. 验收标准

- 成员接口可完成邀请、改角色、停用闭环。
- 审批人变更后，待审批列表实时反映。
- 通知能区分未读、已读和已处理。
- 所有写操作写入审计事件。
