"""Repository configuration inspection.

This module derives repository setup suggestions from real git metadata and
well-known project files. It never stores tokens and always masks credentials in
returned errors.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from app.services.repo_mirror import RepoMirrorError, build_authenticated_remote, parse_gitee_repository


DEFAULT_BRANCH = "main"
DEFAULT_COMMAND_WHITELIST = ["pytest", "npm test", "pnpm test"]
DEFAULT_BLOCKED_PATHS = [".env", "infra/production/", "secrets/"]


def inspect_repository_config(url: str, token: str | None = None) -> dict[str, Any]:
    normalized_url = url.strip()
    _, repo = parse_gitee_repository(normalized_url)
    result = _base_result(normalized_url, repo)
    remote = build_authenticated_remote(normalized_url, (token or "").strip() or None)

    branch = _detect_default_branch(remote, result)
    result["suggested_payload"]["default_branch"] = branch

    with tempfile.TemporaryDirectory(prefix="agent-repo-inspect-") as temp:
        checkout = Path(temp) / "checkout"
        if _clone_for_inspection(remote, checkout, branch, result):
            _inspect_project_files(checkout, result)

    return result


def _base_result(url: str, repo: str) -> dict[str, Any]:
    return {
        "suggested_payload": {
            "name": repo,
            "url": url,
            "default_branch": DEFAULT_BRANCH,
            "test_command": "",
            "command_whitelist": DEFAULT_COMMAND_WHITELIST,
            "approval_owner_id": "",
            "approval_mode": "single",
            "risk_threshold": "medium",
            "max_files": 12,
            "max_retries": 2,
            "blocked_paths": DEFAULT_BLOCKED_PATHS,
            "auto_create_task_after_sync": False,
        },
        "sources": {
            "name": "仓库地址 owner/repo",
            "url": "管理员输入",
            "default_branch": "组织默认模板",
            "command_whitelist": "组织默认安全模板",
            "approval_mode": "组织默认审批模板",
            "risk_threshold": "组织默认审批模板",
            "max_files": "组织默认安全模板",
            "max_retries": "组织默认安全模板",
            "blocked_paths": "组织默认安全模板",
            "auto_create_task_after_sync": "组织默认自动化策略",
        },
        "confidence": {
            "name": "high",
            "url": "high",
            "default_branch": "low",
            "command_whitelist": "medium",
            "approval_mode": "medium",
            "risk_threshold": "medium",
            "max_files": "medium",
            "max_retries": "medium",
            "blocked_paths": "medium",
            "auto_create_task_after_sync": "medium",
        },
        "detected_stack": [],
        "warnings": [],
    }


def _detect_default_branch(remote: str, result: dict[str, Any]) -> str:
    try:
        output = _git(["ls-remote", "--symref", remote, "HEAD"], cwd=None)
    except RepoMirrorError as error:
        result["warnings"].append(f"无法读取远程默认分支，暂用 {DEFAULT_BRANCH}：{error}")
        return DEFAULT_BRANCH

    for line in output.splitlines():
        match = re.match(r"ref:\s+refs/heads/(.+?)\s+HEAD", line.strip())
        if match:
            branch = match.group(1).strip()
            result["sources"]["default_branch"] = "git ls-remote HEAD"
            result["confidence"]["default_branch"] = "high"
            return branch

    for candidate in ("main", "master", "develop"):
        if f"refs/heads/{candidate}" in output:
            result["sources"]["default_branch"] = f"远程分支列表匹配 {candidate}"
            result["confidence"]["default_branch"] = "medium"
            return candidate

    result["warnings"].append(f"远程未声明 HEAD 默认分支，暂用 {DEFAULT_BRANCH}")
    return DEFAULT_BRANCH


def _clone_for_inspection(remote: str, checkout: Path, branch: str, result: dict[str, Any]) -> bool:
    args = ["clone", "--depth", "1", "--single-branch"]
    if branch:
        args.extend(["--branch", branch])
    args.extend([remote, str(checkout)])
    try:
        _git(args, cwd=checkout.parent)
        return True
    except RepoMirrorError as error:
        result["warnings"].append(f"无法读取仓库文件，已只生成基础配置：{error}")
        return False


def _inspect_project_files(root: Path, result: dict[str, Any]) -> None:
    detectors = (
        _inspect_node,
        _inspect_python,
        _inspect_java,
        _inspect_go,
        _inspect_rust,
    )
    for detector in detectors:
        detected = detector(root, result)
        if detected:
            command = result["suggested_payload"].get("test_command", "")
            result["suggested_payload"]["command_whitelist"] = _whitelist_for_command(command)
            result["sources"]["command_whitelist"] = "根据识别到的测试命令生成"
            result["confidence"]["command_whitelist"] = "high"
            return
    result["warnings"].append("未识别到明确测试命令，请在高级配置里手动确认。")


def _inspect_node(root: Path, result: dict[str, Any]) -> bool:
    package_json = _find_file(root, "package.json")
    if not package_json:
        return False
    result["detected_stack"].append("Node.js")
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result["warnings"].append("发现 package.json，但内容无法解析。")
        return False
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return False

    manager = _node_package_manager(package_json.parent)
    command = ""
    source = ""
    test_script = str(scripts.get("test") or "").strip()
    if test_script and "no test specified" not in test_script.lower():
        command = f"{manager} test"
        source = f"{_relative_source(root, package_json)} > scripts.test"
    else:
        for key in ("test:unit", "test:ci", "vitest", "jest"):
            if str(scripts.get(key) or "").strip():
                command = f"{manager} run {key}"
                source = f"{_relative_source(root, package_json)} > scripts.{key}"
                break
    if not command:
        return False
    _set_test_command(result, command, source, "high")
    return True


def _inspect_python(root: Path, result: dict[str, Any]) -> bool:
    markers = [_find_file(root, "pyproject.toml"), _find_file(root, "pytest.ini"), _find_file(root, "setup.cfg")]
    marker = next((item for item in markers if item), None)
    requirements = _find_file(root, "requirements.txt")
    tests_dir = _find_dir(root, "tests")
    has_pytest = _python_declares_pytest(marker) or _python_declares_pytest(requirements)
    if not marker and not requirements and not tests_dir:
        return False
    if not tests_dir and not has_pytest:
        return False
    result["detected_stack"].append("Python")
    command = "pytest tests/ -q" if tests_dir else "pytest -q"
    confidence = "high" if has_pytest else "medium"
    source = _relative_source(root, marker or requirements) if marker or requirements else "tests/ 目录"
    _set_test_command(result, command, source, confidence)
    return True


def _inspect_java(root: Path, result: dict[str, Any]) -> bool:
    pom = _find_file(root, "pom.xml")
    gradle = _find_file(root, "build.gradle") or _find_file(root, "build.gradle.kts")
    if pom:
        result["detected_stack"].append("Java/Maven")
        _set_test_command(result, "mvn test", _relative_source(root, pom), "high")
        return True
    if gradle:
        result["detected_stack"].append("Java/Gradle")
        wrapper = gradle.parent / ("gradlew.bat" if os.name == "nt" else "gradlew")
        command = "./gradlew test" if wrapper.exists() else "gradle test"
        _set_test_command(result, command, _relative_source(root, gradle), "high")
        return True
    return False


def _inspect_go(root: Path, result: dict[str, Any]) -> bool:
    go_mod = _find_file(root, "go.mod")
    if not go_mod:
        return False
    result["detected_stack"].append("Go")
    _set_test_command(result, "go test ./...", _relative_source(root, go_mod), "high")
    return True


def _inspect_rust(root: Path, result: dict[str, Any]) -> bool:
    cargo = _find_file(root, "Cargo.toml")
    if not cargo:
        return False
    result["detected_stack"].append("Rust")
    _set_test_command(result, "cargo test", _relative_source(root, cargo), "high")
    return True


def _set_test_command(result: dict[str, Any], command: str, source: str, confidence: str) -> None:
    result["suggested_payload"]["test_command"] = command
    result["sources"]["test_command"] = source
    result["confidence"]["test_command"] = confidence


def _node_package_manager(directory: Path) -> str:
    if (directory / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (directory / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _python_declares_pytest(marker: Path | None) -> bool:
    if not marker:
        return False
    try:
        text = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if marker.name == "pyproject.toml":
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return "pytest" in text.lower()
        return "pytest" in json.dumps(data).lower()
    return "pytest" in text.lower()


def _whitelist_for_command(command: str) -> list[str]:
    command = command.strip()
    if not command:
        return DEFAULT_COMMAND_WHITELIST
    if command.startswith("pytest"):
        return ["pytest"]
    if command.startswith("npm test"):
        return ["npm test"]
    if command.startswith("pnpm test"):
        return ["pnpm test"]
    if command.startswith("yarn test"):
        return ["yarn test"]
    if command.startswith("npm run "):
        return [command]
    if command.startswith("pnpm run "):
        return [command]
    if command.startswith("yarn "):
        return [command]
    if command.startswith("mvn test"):
        return ["mvn test"]
    if command.startswith("gradle test"):
        return ["gradle test"]
    if command.startswith("./gradlew test"):
        return ["./gradlew test"]
    if command.startswith("go test"):
        return ["go test"]
    if command.startswith("cargo test"):
        return ["cargo test"]
    return [command]


def _find_file(root: Path, name: str, *, max_depth: int = 3) -> Path | None:
    for path in _walk_candidates(root, max_depth=max_depth):
        if path.is_file() and path.name == name:
            return path
    return None


def _find_dir(root: Path, name: str, *, max_depth: int = 3) -> Path | None:
    for path in _walk_candidates(root, max_depth=max_depth):
        if path.is_dir() and path.name == name:
            return path
    return None


def _walk_candidates(root: Path, *, max_depth: int) -> list[Path]:
    skip = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".idea", ".data"}
    candidates: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)
        depth = len(current_path.relative_to(root).parts)
        dirnames[:] = [item for item in dirnames if item not in skip and depth < max_depth]
        for dirname in dirnames:
            candidates.append(current_path / dirname)
        for filename in filenames:
            candidates.append(current_path / filename)
    return candidates


def _relative_source(root: Path, path: Path | None) -> str:
    if not path:
        return "仓库结构"
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return path.name


def _git(args: list[str], cwd: Path | None) -> str:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=180,
        )
    except FileNotFoundError:
        raise RepoMirrorError("未找到 git 可执行文件") from None
    except subprocess.TimeoutExpired:
        raise RepoMirrorError("git 探测超时") from None
    if proc.returncode != 0:
        raw = (proc.stderr or proc.stdout or "").strip()
        masked = _mask_secrets(raw)
        raise RepoMirrorError(masked[:240] or f"git {args[0]} 执行失败")
    return proc.stdout


def _mask_secrets(value: str) -> str:
    return re.sub(r"//oauth2:[^@/]+@", "//oauth2:***@", value)
