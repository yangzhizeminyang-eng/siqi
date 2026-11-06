"""E4 Runtime 的真实 PR 与发布证据适配层。

E4 使用 RuntimeTask/ArtifactStore，不复用团队版 TaskRecord 的 PR 编排。
这里复用现有 repo_mirror 与 gitee_pr_service，增加：

* scratch 仓库 + 显式开关门禁；
* 从隔离工作区读取真实 git diff，应用到远端镜像并推送分支；
* Gitee PR 创建幂等记录；
* 基于测试、审查、diff hash 和 PR 结果生成可核验的本地发布证据。

本模块不会把 token 写入返回值、日志或证据文件。没有真实输入时返回
BLOCKED/抛出脱敏异常，不把草稿当成成功。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# gitee_pr_service 保留引用：既有测试按 release_service.gitee_pr_service 注入 PR 桩。
# OPT-15 起 PR 创建经 GitHost Provider（内部委托同一模块属性，monkeypatch 补丁链不变）。
from app.services import gitee_pr_service, repo_mirror  # noqa: F401
from app.services.git_host import get_git_host_provider


class EnterpriseReleaseError(RuntimeError):
    """E4 PR/发布证据失败，message 不包含凭据。"""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(value: Any, limit: int = 500) -> str:
    return str(value or "")[:limit]


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._/-]+", "-", str(value or "").strip())
    return cleaned.strip("./-")[:80] or "runtime"


def _state_dir() -> Path:
    raw = (
        os.environ.get("E4_RELEASE_STATE_DIR", "").strip()
        or os.environ.get("RELEASE_EVIDENCE_DIR", "").strip()
    )
    if raw:
        root = Path(raw).expanduser().resolve()
    else:
        from app.core.runtime_config import load_runtime_settings

        root = (load_runtime_settings().data_path / "enterprise-e4-release").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _json_path(task_id: str, suffix: str) -> Path:
    return _state_dir() / f"{_safe_name(task_id)}.{suffix}.json"


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def _repository_for_task(task) -> Any:
    from app import main

    repository = main.REPOSITORIES.get(getattr(task, "repository_id", ""))
    if repository is not None:
        return repository
    try:
        from app.db.reads import get_repository

        repository = get_repository(getattr(task, "repository_id", ""))
        if repository is not None:
            return repository
    except Exception:  # noqa: BLE001
        pass
    raise EnterpriseReleaseError("E4 任务绑定的仓库配置不存在")


def _repository_token(repository) -> str:
    from app import main

    token = str(main.REPOSITORY_TOKENS.get(repository.id, "") or "").strip()
    if token and not token.startswith("demo-token-"):
        return token
    token = os.environ.get("GITEE_TOKEN", "").strip()
    if token:
        return token
    raise EnterpriseReleaseError("Gitee 访问令牌未配置")


def _scratch_allowed(repository) -> bool:
    if not _truthy(os.environ.get("ALLOW_SCRATCH_REAL_CHANGE")):
        return False
    ids = {item.strip() for item in os.environ.get("E2E_REAL_CHANGE_REPO_IDS", "").split(",") if item.strip()}
    urls = {item.strip().rstrip("/") for item in os.environ.get("E2E_SCRATCH_REPO_URLS", "").split(",") if item.strip()}
    rid = str(getattr(repository, "id", "") or "")
    url = str(getattr(repository, "url", "") or "").rstrip("/")
    return (rid in ids) or any(url.startswith(item) or item.startswith(url) for item in urls)


def _workspace_diff() -> tuple[str, str]:
    root = os.environ.get("AGENT_WORKSPACE_ROOT", "").strip()
    if not root or not Path(root).is_dir():
        raise EnterpriseReleaseError("AGENT_WORKSPACE_ROOT 未配置或不可读")
    workspace = Path(root).resolve()
    subprocess.run(
        ["git", "-C", str(workspace), "add", "-N", "--", "."],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    proc = subprocess.run(
        ["git", "-C", str(workspace), "diff", "--no-ext-diff", "--binary", "--"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise EnterpriseReleaseError("隔离工作区 git diff 执行失败")
    diff = proc.stdout or ""
    if not diff.strip():
        raise EnterpriseReleaseError("隔离工作区没有可提交的真实 diff")
    return diff, hashlib.sha256(diff.encode("utf-8")).hexdigest()


def _commit_sha(worktree: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise EnterpriseReleaseError("无法读取远端工作树 commit SHA")
    return proc.stdout.strip()


def _prepare_and_push_runtime_branch(task, repository, *, title: str, body: str) -> dict:
    del body
    if not _scratch_allowed(repository):
        raise EnterpriseReleaseError(
            "E4 真实 PR 仅允许显式 scratch 仓库（ALLOW_SCRATCH_REAL_CHANGE + 白名单）"
        )
    token = _repository_token(repository)
    diff, diff_hash = _workspace_diff()
    mirror = repo_mirror.ensure_mirror(repository.id, repository.url, token=token)
    base_branch = (
        os.environ.get("E4_PR_BASE_BRANCH", "").strip()
        or getattr(repository, "default_branch", None)
        or "main"
    )
    base_sha = os.environ.get("E4_PR_BASE_COMMIT", "").strip() or repo_mirror.resolve_commit_sha(
        mirror, base_branch
    )
    branch = os.environ.get("E4_PR_HEAD_BRANCH", "").strip() or (
        f"agent/e4/{_safe_name(getattr(task, 'id', 'runtime'))}"
    )
    wt = repo_mirror.repo_mirror_root(repository.id) / "wt_pr" / _safe_name(branch)
    worktree = repo_mirror.checkout_commit(mirror, base_sha, wt)
    applied = subprocess.run(
        ["git", "-C", str(worktree), "apply", "--binary", "--ignore-whitespace", "-"],
        input=diff,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if applied.returncode != 0:
        raise EnterpriseReleaseError("真实 diff 无法应用到远端基线工作树")
    repo_mirror.commit_changes(
        worktree,
        f"feat(e4): {_clip(title, 120)}",
        user="enterprise-agent",
        email="enterprise-agent@localhost",
    )
    commit_sha = _commit_sha(worktree)
    repo_mirror.push_head_branch(worktree, branch)
    return {
        "head_branch": branch,
        "base_branch": base_branch,
        "base_commit_sha": base_sha,
        "commit_sha": commit_sha,
        "diff_hash": diff_hash,
    }


def create_runtime_pr(task, *, title: str, body: str) -> dict:
    """为 E4 Runtime 创建真实 PR；同一 task 幂等。

    OPT-15：PR 创建经 GitHost Provider 注册表（显式名走 E4_PR_PROVIDER，未设则读
    GIT_HOST_PROVIDER；非 gitee fail-closed 拒绝）。Gitee provider 内部仍委托
    gitee_pr_service（既有 monkeypatch 桩与异常语义不变）。
    """
    existing = _read_json(_json_path(task.id, "pr"))
    if existing and existing.get("html_url"):
        return existing
    host = get_git_host_provider(os.environ.get("E4_PR_PROVIDER") or None)
    repository = _repository_for_task(task)
    token = _repository_token(repository)
    pushed = _prepare_and_push_runtime_branch(task, repository, title=title, body=body)
    pr = host.create_pull_request(
        repository.url,
        token,
        head_branch=pushed["head_branch"],
        base_branch=pushed.get("base_branch") or getattr(repository, "default_branch", None) or "main",
        title=title,
        body=body,
    )
    result = {
        "provider": host.name,
        "id": pr.id,
        "number": pr.number,
        "html_url": pr.html_url,
        "state": pr.state or "OPEN",
        **pushed,
        "task_id": str(task.id),
        "idempotency_key": f"e4-pr:{task.id}:{pushed['diff_hash']}",
        "created_at": _now(),
    }
    if not result["html_url"]:
        raise EnterpriseReleaseError("Gitee PR 创建响应缺少 URL")
    _write_json(_json_path(task.id, "pr"), result)
    return result


def write_release_evidence(
    *,
    task_id: str,
    run_id: str,
    diff_ref: str,
    diff_hash: str,
    review: dict,
    test_result: dict,
    pr: dict,
) -> dict:
    """汇总真实测试、审查、PR 结果，生成可复核的发布证据。"""
    missing: list[str] = []
    if not diff_ref or not diff_hash:
        missing.append("diff")
    if not review or review.get("approved") is not True:
        missing.append("review.approved")
    if not test_result or test_result.get("runner_mode") != "docker":
        missing.append("test.runner_mode=docker")
    if not test_result or test_result.get("exit_code") != 0:
        missing.append("test.exit_code=0")
    if not pr or not pr.get("html_url"):
        missing.append("pr.html_url")
    if missing:
        return {
            "status": "BLOCKED",
            "blocked_reasons": missing,
            "task_id": task_id,
            "run_id": run_id,
            "created_at": _now(),
        }
    payload = {
        "status": "VERIFIED",
        "task_id": task_id,
        "run_id": run_id,
        "diff": {"ref": diff_ref, "content_hash": diff_hash},
        "review": {
            "approved": True,
            "security_risk_count": len(review.get("security_risks") or []),
        },
        "test": {
            "test_run_id": test_result.get("test_run_id", ""),
            "runner_mode": test_result.get("runner_mode"),
            "exit_code": test_result.get("exit_code"),
            "stdout": _clip(test_result.get("stdout"), 1200),
            "stderr": _clip(test_result.get("stderr"), 1200),
        },
        "pr": {
            "provider": pr.get("provider", "gitee"),
            "number": pr.get("number", ""),
            "html_url": pr.get("html_url", ""),
            "commit_sha": pr.get("commit_sha", ""),
            "head_branch": pr.get("head_branch", ""),
            "base_branch": pr.get("base_branch", ""),
        },
        "created_at": _now(),
        "evidence_source": "local_file",
    }
    # content_hash 只覆盖可复核输入（排除 created_at 等易变字段）：同一 diff/测试/审查/PR
    # 组合重复收集必须得到同一证据指纹，避免"重复调用生成新证据"（OPT-06 幂等回归）。
    canonical = json.dumps(
        {key: value for key, value in payload.items() if key != "created_at"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    payload["content_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    _write_json(_json_path(task_id, "release-evidence"), payload)
    return payload


def probe_pr_backend() -> tuple[bool, str]:
    """预检直连 Gitee PR 后端，不创建 PR。"""
    provider = os.environ.get("E4_PR_PROVIDER", "gitee").strip().lower() or "gitee"
    if provider != "gitee":
        return False, f"不支持的 E4_PR_PROVIDER: {provider}"
    if not _truthy(os.environ.get("ALLOW_SCRATCH_REAL_CHANGE")):
        return False, "未开启 ALLOW_SCRATCH_REAL_CHANGE"
    repository_id = os.environ.get("E4_PR_REPOSITORY_ID", "").strip()
    if not repository_id:
        return False, "E4_PR_REPOSITORY_ID 未配置"
    try:
        from types import SimpleNamespace

        repository = _repository_for_task(SimpleNamespace(repository_id=repository_id))
        if not _scratch_allowed(repository):
            return False, "目标仓库不在 scratch 白名单"
        token = _repository_token(repository)
        owner, repo = repo_mirror.parse_gitee_repository(repository.url)
        url = f"https://gitee.com/api/v5/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "enterprise-agent-platform"},
            method="GET",
        )
        if token:
            url = url + "?" + urllib.parse.urlencode({"access_token": token})
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "enterprise-agent-platform"},
                method="GET",
            )
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status not in (200, 201):
                return False, f"Gitee 仓库探测 HTTP {response.status}"
        return True, f"gitee reachable: {owner}/{repo}"
    except urllib.error.HTTPError as exc:
        return False, f"Gitee 仓库探测 HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Gitee 仓库探测失败: {type(exc).__name__}"


def probe_release_evidence_backend() -> tuple[bool, str]:
    """预检本地发布证据源：真实创建、读取、清理一个探针文件。"""
    try:
        root = _state_dir()
        probe = root / ".e4-release-evidence-probe"
        probe.write_text("probe", encoding="utf-8")
        ok = probe.read_text(encoding="utf-8") == "probe"
        probe.unlink(missing_ok=True)
        return (True, str(root)) if ok else (False, "发布证据源读回不一致")
    except Exception as exc:  # noqa: BLE001
        return False, f"发布证据源不可写: {type(exc).__name__}"
