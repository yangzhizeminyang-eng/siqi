"""测试执行服务：真实 Docker Runner seam + 演示模拟 + 危险命令拦截。

MVP 执行口径与 diff_service 对称：
- ``TEST_RUNNER_MODE``（env）默认 ``mock``：走 ``mocked_runner`` 模拟执行，
  结果明确标注「演示运行」，不伪装真实容器执行；失败/重试/拦截路径由测试或
  驱动脚本通过替换 ``execute_test`` 注入。
- ``TEST_RUNNER_MODE=docker``：走 ``DockerRunner`` 真实执行——在独立临时目录
  构造隔离工作区（绝不直接在宿主项目目录执行），docker run 挂载运行命令，
  容器退出后清理临时目录；命令超时 120s（env ``TEST_RUNNER_TIMEOUT``）；
  Docker/镜像不可用时返回不可用原因，由调用方记为 skipped，绝不伪装成功。

危险命令拦截独立于仓库命令白名单，作为第二道防线：即使配置了高危 test_command
也被拦截并返回中文原因（blocked_reason）。只依赖任务/产物对象属性（duck typing），
不 import main，避免循环依赖。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.main import DiffArtifact, TaskRecord

DOCKER_IMAGE = os.getenv("TEST_RUNNER_IMAGE", "python:3.12-slim")
RUN_TIMEOUT_SECONDS = float(os.getenv("TEST_RUNNER_TIMEOUT", "600") or "600")
DEFAULT_RUNNER_NETWORK = "none"
DEFAULT_RUNNER_CPUS = "0.50"
DEFAULT_RUNNER_MEMORY = "384m"
DEFAULT_RUNNER_PIDS_LIMIT = "128"
DEFAULT_RUNNER_INSTALL_MODE = "auto"
RUNNER_INSTALL_MODES = {"auto", "image", "never"}

OUTPUT_MAX_CHARS = 20_000
OUTPUT_KEEP_CHARS = 8_000
MOCKED_RUNNER_LABEL = "（演示运行：模拟执行结果，未真实运行 Docker 容器）"

# 危险命令黑名单：(正则, 拦截原因)。命中任一即拒绝执行。
DANGEROUS_COMMAND_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(^|[\s;&|])rm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|-rf)\s+(/|/\*|/?[a-zA-Z]:[\\/])", re.IGNORECASE), "禁止删除根目录/宿主磁盘根路径（rm -rf / 类命令）"),
    (re.compile(r"(^|[\s;&|])rm\s+-rf\s+(\*|\.|\.\s|/workspace/\*)", re.IGNORECASE), "禁止删除工作区全部内容（rm -rf * 类命令）"),
    (re.compile(r"(^|[\s;&|])rm\s+-rf\s+[^;|&\s]*(boot|etc|usr|var|windows|winnt|system32|Program\s?Files)[\\/]?([\s;|&]|$)", re.IGNORECASE), "禁止删除宿主关键系统目录"),
    (re.compile(r"(^|[\s;&|])del\s+/[sqf]", re.IGNORECASE), "禁止 del /s 静默递归删除"),
    (re.compile(r"(^|[\s;&|])(format|mkfs(\.\w+)?)\b", re.IGNORECASE), "禁止格式化磁盘/创建文件系统（format/mkfs）"),
    (re.compile(r"(^|[\s;&|])(shutdown|reboot|poweroff|halt)\b", re.IGNORECASE), "禁止关机/重启/停机类系统命令"),
    (re.compile(r"(curl|wget)\b[^;&]*\|\s*(sh|bash|zsh)\b", re.IGNORECASE), "禁止 curl/wget 下载后直接管道给 shell 执行"),
    (re.compile(r"(iwr|invoke-webrequest)\b[^;|&]*\b(iex|invoke-expression)\b", re.IGNORECASE), "禁止 iwr 下载后直接 Invoke-Expression 执行"),
    (re.compile(r"\b(invoke-expression|iex)\b", re.IGNORECASE), "禁止 PowerShell Invoke-Expression 动态执行"),
    (re.compile(r"(\bc:\\\\|/\s*--?bind|-v\s+[a-zA-Z]:[\\/]|-v\s+/)", re.IGNORECASE), "禁止挂载/写入宿主磁盘根路径"),
]


def _runner_data_dir() -> Path:
    """返回 Runner 与仓库镜像共用的数据目录。"""
    return Path(os.getenv("DATA_DIR", "/var/lib/agent/data")).expanduser().resolve()


def _runner_workspace_root() -> Path:
    """创建可被 Docker 宿主机看到的测试工作区根目录。"""
    root = _runner_data_dir() / "runner-workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _host_mount_path(path: str | Path) -> str:
    """把后端容器内的数据卷路径转换为 Docker daemon 的宿主机路径。

    Docker CLI 运行在后端容器内但 daemon 在宿主机上。直接把容器路径交给
    ``docker run -v`` 时，daemon 会在宿主机创建同名空目录，导致测试容器看不到
    仓库源码。团队部署通过 TEST_RUNNER_HOST_DATA_DIR 提供 named volume 的宿主机
    挂载点；本地环境未配置时保持原路径行为。
    """
    host_root = os.getenv("TEST_RUNNER_HOST_DATA_DIR", "").strip()
    if not host_root:
        return str(path)
    raw_path = str(path)
    data_dir = os.getenv("DATA_DIR", "/var/lib/agent/data").strip() or "/var/lib/agent/data"
    if raw_path.startswith("/") and data_dir.startswith("/") and host_root.startswith("/"):
        try:
            relative = PurePosixPath(raw_path).relative_to(PurePosixPath(data_dir))
        except ValueError:
            return raw_path
        return str(PurePosixPath(host_root) / relative)
    try:
        relative = Path(path).expanduser().resolve().relative_to(_runner_data_dir())
    except ValueError:
        return str(path)
    return str((Path(host_root).expanduser().resolve() / relative).resolve())


def _make_workspace_runner_accessible(workspace: Path) -> None:
    """让受限 Runner 的 user namespace 可以进入隔离 worktree。

    git worktree 默认可能按宿主用户 umask 创建 0700 目录。Runner 使用独立
    user namespace 后，即使容器内显示为 root，也不能穿过这个目录，因此需要
    对隔离工作区开放目录遍历和文件读写权限。工作区会在测试结束后删除。
    """
    for dirpath, dirnames, filenames in os.walk(workspace):
        for name in [dirpath, *[os.path.join(dirpath, item) for item in dirnames]]:
            try:
                os.chmod(name, os.stat(name).st_mode | 0o755)
            except OSError:
                pass
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                os.chmod(path, os.stat(path).st_mode | 0o666)
            except OSError:
                pass


def check_dangerous_command(command: str) -> str | None:
    """危险命令拦截：命中返回中文原因，未命中返回 None。"""
    if not command or not command.strip():
        return "测试命令为空，无法执行"
    for pattern, reason in DANGEROUS_COMMAND_RULES:
        if pattern.search(command):
            return reason
    return None


def run_whitelisted(command: str, *, workspace: str | None = None) -> dict:
    """Run one real allowlisted command in the Docker runner.

    Enterprise Agent tools use this narrow wrapper so the model cannot bypass the
    dangerous-command gate or invoke the Docker runner directly.
    """
    blocked = check_dangerous_command(command)
    if blocked:
        return {
            "runner_mode": "blocked",
            "exit_code": None,
            "stdout": "",
            "stderr": blocked,
            "error": blocked,
        }
    first = (command or "").strip().split(maxsplit=1)[0].lower()
    allowlist = {
        item.strip().lower()
        for item in os.getenv("TEST_COMMAND_ALLOWLIST", "pytest,python,python3,echo").split(",")
        if item.strip()
    }
    if first not in allowlist:
        reason = f"命令不在测试白名单: {first or '<empty>'}"
        return {
            "runner_mode": "blocked",
            "exit_code": None,
            "stdout": "",
            "stderr": reason,
            "error": reason,
        }
    result, error = DockerRunner().run(command, workspace=workspace or _runner_workspace_root())
    if error:
        return {
            "runner_mode": "unavailable",
            "exit_code": None,
            "stdout": "",
            "stderr": error,
            "error": error,
        }
    result["runner_mode"] = "docker"
    result["error"] = None
    return result


def clip_output(text: str) -> str:
    """stdout/stderr 长度截断：超限时保留头尾，中间以截断标记代替。"""
    if text is None:
        return ""
    if len(text) <= OUTPUT_MAX_CHARS:
        return text
    head = text[:OUTPUT_KEEP_CHARS]
    tail = text[-OUTPUT_KEEP_CHARS:]
    return f"{head}\n…[输出过长，已截断 {len(text) - OUTPUT_KEEP_CHARS * 2} 字符]…\n{tail}"


def _mock_stdout(command: str, attempt: int) -> str:
    return "\n".join(
        [
            f"$ pytest 演示运行（runner_mode=mocked_runner，attempt={attempt}）",
            f"$ {command}",
            "…",
            "12 项测试通过，0 项失败（演示运行：模拟执行结果，未真实运行 Docker 容器）。",
        ]
    )


class DockerRunner:
    """真实 Docker 执行：隔离临时工作区 + 超时 + 退出后清理。"""

    def __init__(self, image: str = DOCKER_IMAGE, timeout: float = RUN_TIMEOUT_SECONDS) -> None:
        self.image = image
        self.timeout = timeout

    def unavailable_reason(self) -> str | None:
        """Docker 不可用时返回中文原因；可用返回 None。"""
        if not shutil.which("docker"):
            return "Docker CLI 未安装，无法执行容器测试"
        try:
            probe = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "Docker daemon 探测超时，无法执行容器测试"
        if probe.returncode != 0:
            return "Docker daemon 未运行或不可用（docker info 失败），无法执行容器测试"
        return None

    def run(self, command: str, files: list[dict] | None = None, workspace: str | None = None) -> tuple[dict | None, str | None]:
        """在独立临时目录执行命令并返回 (结果 dict, 错误)。

        结果 dict：{exit_code, stdout, stderr, duration}；错误非 None 表示环境不可用，
        调用方应记录 skipped 而非伪装成功。容器退出后临时目录必然被清理。

        workspace 提供时挂载完整工作树（含仓库源码与 agent 改动），不再用 diff files
        组装示意目录；此时工作树生命周期由调用方负责。
        """
        reason = self.unavailable_reason()
        if reason:
            return None, reason
        workdir: str | None = None
        started = time.monotonic()
        try:
            if workspace:
                mount = str(workspace)
            else:
                workdir = tempfile.mkdtemp(prefix="agent-test-run-", dir=str(_runner_workspace_root()))
                for file in files or []:
                    path = getattr(file, "file_path", None) or (file.get("file_path") if isinstance(file, dict) else None)
                    after = getattr(file, "after", None)
                    if after is None and isinstance(file, dict):
                        after = file.get("after")
                    after = list(after or [])
                    if not path or path in {"unknown", ".", "/"}:
                        continue
                    # 仅把 Diff 变更后的示意内容写入隔离工作区（无真实仓库时用于可核对演示）
                    file_path = os.path.join(workdir, path.replace("\\", "/"))
                    os.makedirs(os.path.dirname(file_path) or workdir, exist_ok=True)
                    with open(file_path, "w", encoding="utf-8") as handle:
                        handle.write("\n".join(after))
                mount = workdir
            mount = _host_mount_path(mount)
            install_mode = os.getenv("TEST_RUNNER_INSTALL_MODE", DEFAULT_RUNNER_INSTALL_MODE).strip().lower()
            if install_mode == "image":
                install_mode = "never"
            if install_mode not in RUNNER_INSTALL_MODES:
                return None, f"TEST_RUNNER_INSTALL_MODE 配置无效：{install_mode}（可选 auto/image/never）"

            runner_network = os.getenv("TEST_RUNNER_NETWORK", DEFAULT_RUNNER_NETWORK).strip() or DEFAULT_RUNNER_NETWORK
            install_cmd = ""
            python_path_prefix = ""
            if workspace:
                reqs = collect_requirements(workspace)
                workspace_path = Path(workspace)
                if (workspace_path / "backend" / "app").is_dir():
                    python_path_prefix = "export PYTHONPATH=/workspace/backend${PYTHONPATH:+:$PYTHONPATH} && "
                if reqs and install_mode == "auto":
                    if runner_network == "none":
                        return None, (
                            "测试工作区发现 requirements*.txt，但 TEST_RUNNER_NETWORK=none "
                            "禁止在线安装依赖；请使用预构建测试镜像并设置 "
                            "TEST_RUNNER_INSTALL_MODE=image，或显式允许受控网络"
                        )
                    install_cmd = (
                        " && ".join(
                            f"python -m pip install --target /tmp/runner-packages -r /workspace/{os.path.relpath(r, workspace).replace(os.sep, '/')} -q --disable-pip-version-check --timeout 180 --retries 5 --prefer-binary"
                            for r in reqs
                        )
                        + " && "
                    )
                    python_path_prefix = "export PYTHONPATH=/tmp/runner-packages${PYTHONPATH:+:$PYTHONPATH} && "
                elif reqs and install_mode == "never":
                    if not python_path_prefix:
                        python_path_prefix = "export PYTHONPATH=${PYTHONPATH:-} && "
            container_name = f"agent-test-{os.urandom(4).hex()}"
            runner_cpus = os.getenv("TEST_RUNNER_CPUS", DEFAULT_RUNNER_CPUS).strip() or DEFAULT_RUNNER_CPUS
            runner_memory = os.getenv("TEST_RUNNER_MEMORY", DEFAULT_RUNNER_MEMORY).strip() or DEFAULT_RUNNER_MEMORY
            runner_pids = os.getenv("TEST_RUNNER_PIDS_LIMIT", DEFAULT_RUNNER_PIDS_LIMIT).strip() or DEFAULT_RUNNER_PIDS_LIMIT
            container_command = f"cd /workspace && {python_path_prefix}{install_cmd}{command}"
            completed = subprocess.run(
                [
                    "docker", "run", "--rm", "--name", container_name,
                    "--network", runner_network,
                    "--cpus", runner_cpus,
                    "--memory", runner_memory,
                    "--pids-limit", runner_pids,
                    "--read-only",
                    "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
                    "--tmpfs", "/tmp/runner-packages:rw,nosuid,nodev,size=256m",
                    "--tmpfs", "/root/.cache:rw,nosuid,nodev,size=128m",
                    "-v", f"{mount}:/workspace:rw",
                    "-w", "/workspace",
                    self.image, "sh", "-lc", container_command,
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            return {
                "exit_code": completed.returncode,
                "stdout": clip_output(completed.stdout),
                "stderr": clip_output(completed.stderr),
                "duration": round(time.monotonic() - started, 2),
            }, None
        except subprocess.TimeoutExpired:
            # docker CLI 被 kill 时容器仍会驻留(--rm 不触发)，显式清掉避免孤儿容器
            try:
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, timeout=15)
            except Exception:
                pass
            return {
                "exit_code": 124,
                "stdout": "",
                "stderr": f"测试命令执行超时（>{int(self.timeout)}s），容器已被终止",
                "duration": round(time.monotonic() - started, 2),
            }, None
        except Exception as error:  # noqa: BLE001 - 环境异常统一转为不可用原因
            return None, f"Docker 执行异常：{type(error).__name__}: {error}"
        finally:
            if workdir:
                shutil.rmtree(workdir, ignore_errors=True)


def collect_requirements(workspace: str) -> list[str]:
    """扫描工作树中的依赖声明文件（requirements*.txt），供容器内自动安装。

    只认 Python requirements 文本（含 backend/app/src 常见子目录），不扫描 node_modules /
    package.json 等前端依赖。返回宿主侧绝对路径列表；无需安装时返回空。
    """
    ws = Path(workspace).resolve()
    found: set[str] = set()
    for sub in ("", "backend", "app", "src"):
        base = ws / sub
        if not base.is_dir():
            continue
        for name in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt"):
            p = (base / name).resolve()
            if p.is_file():
                found.add(str(p))
        for p in base.glob("requirements*.txt"):
            if p.is_file():
                found.add(str(p.resolve()))
    return sorted(found)


def build_test_workspace(task, artifact) -> str | None:
    """构造 `基线 commit + agent 改动` 的完整工作树（含仓库源码），供 Docker 挂载跑真实单测。

    复用 repo_mirror：mirror -> checkout_commit(base_commit_sha) -> apply_diff_files(叠加 agent 改动)。
    返回工作树路径（生命周期由调用方清理）；缺基线/镜像不可用时返回 None。
    """
    from app.services import repo_mirror

    repository_id = getattr(task, "repository_id", None)
    url = getattr(task, "repository_url", None)
    base_commit = getattr(task, "base_commit_sha", None)
    if not repository_id or not base_commit:
        return None
    try:
        mirror = repo_mirror.mirror_dir(repository_id)
        if not (mirror / ".git").exists():
            mirror = repo_mirror.ensure_mirror(repository_id, url or "", token=os.getenv("GITEE_TOKEN") or None)
        tmp = tempfile.mkdtemp(prefix="agent-test-ws-", dir=str(_runner_workspace_root()))
        worktree = repo_mirror.checkout_commit(mirror, base_commit, Path(tmp))
        if getattr(artifact, "files", None):
            repo_mirror.apply_diff_files(worktree, artifact.files)
        _make_workspace_runner_accessible(worktree)
        return str(worktree)
    except Exception as exc:
        raise RuntimeError(f"构建测试工作区失败：{exc}") from exc


def execute_test(task: TaskRecord, artifact: DiffArtifact, command: str, attempt: int) -> dict:
    """执行测试的对外入口。返回 dict：
      runner_mode: mocked_runner / docker / unavailable
      exit_code / stdout / stderr / duration / error
    error 非 None 表示环境不可用（调用方记为 skipped，不伪装成功）。
    真实 Docker 模式下：先构造含仓库源码与 agent 改动的完整工作树，挂载进容器跑命令。
    """
    mode = os.getenv("TEST_RUNNER_MODE", "mock").strip().lower() or "mock"
    execution_step = getattr(artifact, "execution_step", "") or "测试执行"
    if mode != "docker":
        return {
            "runner_mode": "mocked_runner",
            "exit_code": 0,
            "stdout": _mock_stdout(command, attempt),
            "stderr": "",
            "duration": 0.12,
            "error": None,
            "execution_step": execution_step,
        }
    workspace = None
    try:
        workspace = build_test_workspace(task, artifact)
    except Exception as exc:
        return {
            "runner_mode": "unavailable",
            "exit_code": None,
            "stdout": "",
            "stderr": f"测试工作区构建失败：{exc}",
            "duration": 0.0,
            "error": f"测试工作区构建失败：{exc}",
            "execution_step": execution_step,
        }
    runner = DockerRunner()
    if workspace:
        try:
            result, error = runner.run(command, workspace=workspace)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
    else:
        # 无法构建完整工作树（如极简测试 task）：回退到 Diff files 沙盒，语义同旧版本
        result, error = runner.run(command, list(artifact.files))
    if error:
        return {
            "runner_mode": "unavailable",
            "exit_code": None,
            "stdout": "",
            "stderr": error,
            "duration": 0.0,
            "error": error,
            "execution_step": execution_step,
        }
    result["runner_mode"] = "docker"
    result["error"] = None
    result["execution_step"] = execution_step
    return result
