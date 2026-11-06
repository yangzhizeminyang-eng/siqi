"""Diff 生成服务：真实代码执行 seam + 演示兜底产物构造。

MVP 未接入真实执行环境（Docker Runner），本服务固定走「显式演示兜底」：

- ``attempt_real_diff`` 是真实执行 seam：默认返回 ``(None, 不可用原因)``，接入 Runner 后
  在此实现并返回真实文件补丁列表；真实执行失败时把原因放进 error 返回，由调用方决定是否兜底。
- ``build_demo_diff`` 构造固定结构的演示 patch 草稿（含 新增/修改/删除 三类文件）、
  测试摘要与 PR 描述草案，内容全部标注「演示兜底」，不伪装真实执行结果。
- 真实失败原因由调用方原样落库（``real_failure_reason``），不会被兜底成功覆盖。

只依赖任务/计划对象的属性（duck typing），不 import main，避免循环依赖。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.main import ModificationPlan, TaskRecord

REAL_EXECUTION_UNAVAILABLE_REASON = (
    "MVP 未接入真实代码执行环境（Docker Runner），无法在工作区执行改动并产出真实补丁"
)

# 演示兜底样例内容：固定结构、行数可核对，patch 与 added/deleted 行数保持一致
_MODIFY_BEFORE = [
    "def apply_change(change, order):",
    "    # TODO: 按修改计划实现变更逻辑",
    "    return order",
]
_MODIFY_AFTER = [
    "def apply_change(change, order):",
    "    # 演示兜底样例：按修改计划补齐实现与校验",
    "    validated = validate_change_order(order)",
    "    apply_change_events(validated)",
    "    return validated",
]
_ADD_AFTER = [
    "import pytest",
    "",
    "def test_regression_scenario_covered():",
    "    \"\"\"演示兜底：覆盖计划声明的回归场景。\"\"\"",
    "    result = run_change_scenario()",
    "    assert result.is_ok",
]
_DELETE_BEFORE = [
    "def legacy_retry_workaround():",
    "    # 旧逻辑：网络重试时重复处理已完成事件",
    "    return retry_all_events()",
]
_FALLBACK_MODIFY_PATH = "src/handler.py"
_FALLBACK_ADD_PATH = "tests/test_task_regression.py"
_FALLBACK_DELETE_PATH = "legacy/retry_workaround.py"


def attempt_real_diff(task: TaskRecord, plan: ModificationPlan) -> tuple[list[dict] | None, str | None]:
    """真实执行 seam：返回 (文件补丁列表, 错误)。

    默认不可用；当启用 scratch 真实变更（env ALLOW_SCRATCH_REAL_CHANGE=true 且 repo 在白名单
    E2E_REAL_CHANGE_REPO_IDS）时，在 git-clone worktree 里产出**真实变更**（source=e2e_scratch_real_change）：
    确定性小改 README.md/src/app.py 并写回工作区，产出真实 before/after 统一 diff。非 scratch 一律不可用。
    """
    if not _scratch_real_allowed(task):
        return None, REAL_EXECUTION_UNAVAILABLE_REASON
    try:
        return _scratch_real_change(task, plan), None
    except Exception as error:  # noqa: BLE001 - 真实变更失败返回原因，交调用方（不伪装）
        return None, f"真实变更失败（scratch e2e）：{type(error).__name__}: {error}"


def _scratch_real_allowed(task: TaskRecord) -> bool:
    """仅专用 scratch 测试仓 + 显式开关启用真实变更；生产仓默认禁止。

    仓白名单：repository_id 命中 E2E_REAL_CHANGE_REPO_IDS，或 repository_url 命中 E2E_SCRATCH_REPO_URLS；
    两者均未配置 → 不启用（保守，防误改生产仓）。
    """
    flag = os.getenv("ALLOW_SCRATCH_REAL_CHANGE", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return False
    ids = [x.strip() for x in os.getenv("E2E_REAL_CHANGE_REPO_IDS", "").split(",") if x.strip()]
    urls = [x.strip() for x in os.getenv("E2E_SCRATCH_REPO_URLS", "").split(",") if x.strip()]
    rid = getattr(task, "repository_id", None)
    url = getattr(task, "repository_url", "") or ""
    if ids and rid in ids:
        return True
    if urls and any(u and (u in url or url.startswith(u) or url.endswith(u)) for u in urls):
        return True
    return False


def _scratch_real_change(task: TaskRecord, plan: ModificationPlan) -> list[dict]:
    """在 git-clone worktree 里产出确定性真实变更并写回工作区，返回 DiffFile 同构 dict（真实 before/after）。"""
    from pathlib import Path

    import datetime as _dt

    from app.services import repo_mirror

    token = os.getenv("GITEE_TOKEN") or None
    mirror = repo_mirror.ensure_mirror(task.repository_id, task.repository_url, token=token)
    base_sha = getattr(task, "base_commit_sha", None) or repo_mirror.resolve_commit_sha(
        mirror, getattr(task, "base_branch", "") or "master")
    root = repo_mirror.repo_mirror_root(task.repository_id)
    wt = repo_mirror.checkout_commit(mirror, base_sha, root / "wt_real" / (getattr(task, "work_branch", "") or "change")[:40])
    path = _pick_change_path(wt)
    target = wt / path
    exists = target.exists()
    before = target.read_text(encoding="utf-8").split("\n") if exists else []
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    mark = [
        "",
        "## AI Agent 真实变更（e2e_scratch_real_change，专用 scratch 测试仓，非生产策略）",
        f"- Task：{getattr(task, 'id', '?')}｜Issue：{getattr(task, 'issue_id', '?')}",
        f"- 计划目标：{_clip(getattr(plan, 'goal', '') or '', 120)}",
        f"- 生成时间：{now}｜来源：里程碑 A 真实 E2E",
    ]
    after = [*before, *mark]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(after), encoding="utf-8")  # 真实写回工作区
    return [_file_record(path, "modify" if exists else "add", before, after)]


def _pick_change_path(wt) -> str:
    if (wt / "README.md").exists():
        return "README.md"
    if (wt / "src" / "app.py").exists():
        return "src/app.py"
    return "E2E_REAL_CHANGE.md"


def execute_diff(task: TaskRecord, plan: ModificationPlan) -> dict:
    """生成 Diff 的对外入口：先尝试真实执行，失败/不可用时走显式演示兜底。

    返回 dict（供 main 落库 DiffArtifact）：
      generation_status: SUCCEEDED / FALLBACK / FAILED
      execution_mode: REAL / DEMO_FALLBACK
      is_demo_fallback: bool
      execution_step / fallback_reason / real_failure_reason
      files: list[dict]（DiffFile 同构，含 patch 与 before/after 行）
      test_summary / pr_description_draft: str | None
    真实执行不可用与真实执行失败都会被记录：不可用只写 fallback_reason；
    真实失败同时保留 real_failure_reason（兜底产物不覆盖该原因）。
    """
    execution_step = _execution_step(plan)
    real_files, real_error = attempt_real_diff(task, plan)
    if real_files is not None:
        return {
            "execution_step": execution_step,
            "generation_status": "SUCCEEDED",
            "execution_mode": "REAL",
            "is_demo_fallback": False,
            "fallback_reason": None,
            "real_failure_reason": None,
            "real_change_source": "e2e_scratch_real_change" if _scratch_real_allowed(task) else "real",
            "files": real_files,
            "test_summary": None,
            "pr_description_draft": None,
        }

    demo = build_demo_diff(task, plan)
    if demo is None:
        return {
            "execution_step": execution_step,
            "generation_status": "FAILED",
            "execution_mode": "REAL",
            "is_demo_fallback": False,
            "fallback_reason": "演示兜底产物构造失败，无法生成可审阅的模拟 Diff",
            "real_failure_reason": real_error,
            "files": [],
            "test_summary": None,
            "pr_description_draft": None,
        }

    is_unavailable = real_error == REAL_EXECUTION_UNAVAILABLE_REASON
    demo.update(
        {
            "execution_step": execution_step,
            "generation_status": "FALLBACK",
            "execution_mode": "DEMO_FALLBACK",
            "is_demo_fallback": True,
            "fallback_reason": real_error,
            # 真实执行失败原因单独保留；未接入执行环境（不可用）不算真实失败
            "real_failure_reason": None if is_unavailable else real_error,
        }
    )
    return demo


def build_demo_diff(task: TaskRecord, plan: ModificationPlan) -> dict | None:
    """构造固定结构的演示 Diff 产物；内部异常时返回 None（视为兜底失败）。"""
    try:
        files = _demo_files(task, plan)
        summary_lines = [
            "测试摘要（演示兜底，未真实执行）：",
            f"- 覆盖场景：1 项计划回归冒烟断言（样例），0 项失败；",
            f"- 计划建议：{_clip('；'.join(plan.test_suggestions or []), 160) or '无（按仓库测试命令执行）'}",
            f"- 真实命令：{_clip(task.test_command or '', 120) or '未配置'}，需接入执行环境后运行核对",
        ]
        pr_lines = [
            "PR 描述草案（演示兜底，由演示产物生成，不可直接提交）：",
            f"标题：{_clip(task.title, 80)}",
            f"变更文件：{', '.join(f['file_path'] for f in files)}",
            "改动说明：按审批通过的修改计划生成代码改动与回归覆盖，真实合入前需人工走查并执行测试。",
            "测试情况：演示摘要（未真实执行），见测试摘要字段。",
            "风险提示：本草案由演示兜底生成，仅供流程演示，不替代真实执行与人工审阅。",
        ]
        return {
            "execution_step": "",
            "files": files,
            "test_summary": "\n".join(summary_lines),
            "pr_description_draft": "\n".join(pr_lines),
        }
    except Exception:  # noqa: BLE001 - 演示构造失败走 FAILED，不向调用方抛异常
        return None


def _execution_step(plan: ModificationPlan) -> str:
    if plan.steps:
        last = plan.steps[-1]
        return f"步骤 {last.order}：{last.title}"
    return "代码实现与自检"


def _demo_files(task: TaskRecord, plan: ModificationPlan) -> list[dict]:
    """三类演示文件（修改/新增/删除各至少一个），路径优先取自计划文件范围。"""
    scope_modify = _first_scope_path(plan, {"modify"})
    scope_add = _first_scope_path(plan, {"add"})
    scope_delete = _first_scope_path(plan, {"delete"})
    fallback_add = _FALLBACK_ADD_PATH.replace("task_regression", _sanitize(task.issue_id or "task"))

    modify_path = scope_modify or _FALLBACK_MODIFY_PATH
    add_path = scope_add or fallback_add
    delete_path = scope_delete or _FALLBACK_DELETE_PATH
    used_paths = {modify_path, add_path, delete_path}
    if len(used_paths) < 3:
        # 计划文件范围存在同路径冲突时退化为演示样例路径，保证三类文件互不重复
        add_path = scope_add if scope_add not in used_paths else fallback_add
        delete_path = scope_delete if scope_delete not in used_paths else _FALLBACK_DELETE_PATH

    goal_hint = f"# 计划目标：{_clip(plan.goal, 70)}（演示兜底样例）"
    modify_before = list(_MODIFY_BEFORE)
    modify_after = [goal_hint, *list(_MODIFY_AFTER)]

    return [
        _file_record(modify_path, "modify", modify_before, modify_after),
        _file_record(add_path, "add", [], list(_ADD_AFTER)),
        _file_record(delete_path, "delete", list(_DELETE_BEFORE), []),
    ]


def _first_scope_path(plan: ModificationPlan, change_types: set[str]) -> str | None:
    for item in plan.file_scope:
        if item.change_type in change_types and item.file_path and item.file_path != "unknown":
            return item.file_path
    return None


def _file_record(file_path: str, change_type: str, before: list[str], after: list[str]) -> dict:
    return {
        "file_path": file_path,
        "change_type": change_type,
        "patch": _build_patch(file_path, before, after),
        "added_lines": len(after),
        "deleted_lines": len(before),
        "before": before,
        "after": after,
    }


def _build_patch(file_path: str, before: list[str], after: list[str]) -> str:
    """由 before/after 行构造统一 diff 文本（行数与 added/deleted 计数严格一致，可直接复制）。"""
    header = [
        f"--- a/{file_path}",
        f"+++ b/{file_path}",
        f"@@ -1,{len(before) or 1} +1,{len(after) or 1} @@",
    ]
    if not before and after:
        body = [f"+{line}" for line in after]
    elif before and not after:
        body = [f"-{line}" for line in before]
    elif not before and not after:
        body = []
    else:
        body = []
        for line in before:
            body.append(f"-{line}")
        for line in after:
            body.append(f"+{line}")
    return "\n".join([*header, *body])


def _sanitize(value: str) -> str:
    import re as _re

    normalized = _re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return normalized.strip("_").lower()[:40] or "task"


def _clip(value: str, limit: int) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else f"{value[: limit - 1]}…"
