"""仓库代码 git-clone 化镜像：以本地 git clone/fetch/worktree 替代 Gitee API 逐文件/逐 blob 读取。

背景：真实索引曾因 Gitee 免费级代码读取限流（`git/branches`→403 Rate Limit Exceeded）失败。
本模块把 SYNCING 阶段的代码快照改为：首次 `git clone`，后续 `git fetch`，再 `git worktree add` 到指定
commit sha 后走本地工作区。token 仅用于拼 `https://oauth2:<token>@…` 远程并从不出现在日志/错误。

注意：本模块不 import `app.main`（避免服务↔main 循环依赖），owner/repo 由 URL 本地解析。
"""
from __future__ import annotations

import dataclasses
import os
import re
import subprocess
import urllib.parse
from pathlib import Path

from app.core.runtime_config import load_runtime_settings
from app.retrieval import parsing_config

_GITEE_URL = re.compile(r"^(?:https://gitee\.com/|git@gitee\.com:|ssh://git@gitee\.com/)([\w.\-]+)/([\w.\-]+?)(?:\.git)?/?$")


class RepoMirrorError(RuntimeError):
    """git 镜像失败；message 已脱敏，不含 token/remote。"""


@dataclasses.dataclass
class MirrorFile:
    path: str
    content: str


def parse_gitee_repository(url: str) -> tuple[str, str]:
    m = _GITEE_URL.match(url.strip())
    if not m:
        raise RepoMirrorError("无法解析 Gitee 仓库 URL（owner/repo）")
    return m.group(1), m.group(2)


def _is_http_remote(url: str) -> bool:
    u = url.strip().lower()
    return u.startswith("http://") or u.startswith("https://") or u.startswith("ssh://") or u.startswith("git@")


def build_authenticated_remote(url: str, token: str | None) -> str:
    """构造可 clone/push 的 https 远程（token 内嵌，仅用于 subprocess，不落日志）。

    非 http/ssh 远程（本地路径 / file://）原样返回，便于本地仓库测试。
    """
    if not _is_http_remote(url):
        return url
    if not token:
        return _plain_remote(url)
    owner, repo = parse_gitee_repository(url)
    return f"https://oauth2:{token}@gitee.com/{owner}/{repo}.git"


def _plain_remote(url: str) -> str:
    url = url.strip()
    if url.endswith(".git"):
        return url
    return url.rstrip("/") + ".git"


def mask_remote(url: str) -> str:
    """把远程里的 token（userinfo）隐掉用于日志/审计；token 不可回显。"""
    if not _is_http_remote(url):
        return f"<local:{os.path.basename(url.rstrip('/'))}>"
    return re.sub(r"://[^@/]*@", "://oauth2:***@", url, count=1)


def repo_mirror_root(repository_id: str) -> Path:
    settings = load_runtime_settings()
    root = settings.data_path / "repos" / repository_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def mirror_dir(repository_id: str, base_dir: Path | str | None = None) -> Path:
    root = Path(base_dir) if base_dir else repo_mirror_root(repository_id)
    mirror = (root / "mirror").resolve()  # git 用绝对路径，避免相对 cwd 解析错位
    mirror.mkdir(parents=True, exist_ok=True)
    return mirror


def _git(mirror_or_root: Path, *args: str, cwd: Path | None = None) -> str:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd or mirror_or_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=1200,
        )
    except FileNotFoundError:
        raise RepoMirrorError("未找到 git 可执行文件，无法执行仓库镜像") from None
    except subprocess.TimeoutExpired:
        raise RepoMirrorError("git 操作超时") from None
    if proc.returncode != 0:
        raw = (proc.stderr or proc.stdout or "").strip()
        # git 失败时常回显带 token 的 remote URL（https://oauth2:<token>@…），必须掩码后才能进异常/日志
        msg = re.sub(r"//oauth2:[^@/]+@", "//oauth2:***@", raw)
        raise RepoMirrorError(f"git 失败({args[0]}): {msg[:300]}")
    return proc.stdout


def ensure_mirror(
    repository_id: str,
    url: str,
    token: str | None = None,
    *,
    base_dir: Path | None = None,
) -> Path:
    """返回 mirror 路径；首次 clone，之后 fetch origin 全分支并拉取 HEAD 引用。"""
    mirror = mirror_dir(repository_id, base_dir)
    if not (mirror / ".git").exists():
        remote = build_authenticated_remote(url, token)
        _git(Path(mirror.parent), "clone", "--no-checkout", remote, str(mirror))
    else:
        _git(mirror, "remote", "set-url", "origin", build_authenticated_remote(url, token))
    _git(mirror, "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*", "+HEAD:refs/remotes/origin/HEAD")
    return mirror


def checkout_commit(mirror: Path, commit_sha: str, worktree_dir: Path) -> Path:
    """在独立 worktree 检出指定 commit；返回 worktree 路径（已含检出的代码）。"""
    mirror = mirror.resolve()
    worktree_dir = worktree_dir.resolve()
    if not (mirror / ".git").exists():
        raise RepoMirrorError("mirror 未建立，无法 checkout")
    worktree_dir.mkdir(parents=True, exist_ok=True)
    if (worktree_dir / ".git").exists():
        try:
            _git(mirror, "worktree", "remove", "--force", str(worktree_dir))
        except RepoMirrorError:
            pass
    else:
        _git(mirror, "worktree", "prune")
    # 先尝试直接 checkout 指定 commit；未知 sha 则显式 fetch 一次该 sha
    try:
        _git(mirror, "worktree", "add", "--detach", str(worktree_dir), commit_sha)
    except RepoMirrorError:
        _git(mirror, "fetch", "origin", commit_sha)
        _git(mirror, "worktree", "add", "--detach", str(worktree_dir), commit_sha)
    return worktree_dir


def resolve_commit_sha(mirror: Path, branch: str) -> str:
    """本地解析分支 HEAD commit（避免一次 Gitee branches API 调用）。"""
    out = _git(mirror, "rev-parse", f"refs/remotes/origin/{branch}")
    return out.strip()


def walk_snapshot(
    worktree: Path,
    *,
    blocked_paths: list[str],
    max_files: int,
    file_size_limit: int = parsing_config.INDEX_FILE_SIZE_LIMIT,
    line_limit: int = parsing_config.INDEX_FILE_LINE_LIMIT,
) -> list[MirrorFile]:
    """遍历 worktree 产出代码文件；应用 blocked_paths / 二进制 / 文件大小行数上限；受 max_files 封顶。"""
    files: list[MirrorFile] = []
    cap = max(max_files, 1)
    for dirpath, dirnames, filenames in os.walk(worktree):
        # 跳过版本库内部与构建目录
        kept = [
            d for d in dirnames
            if d not in {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".idea", ".data"}
        ]
        dirnames[:] = kept
        for name in filenames:
            if len(files) >= cap:
                return files
            rel = os.path.relpath(os.path.join(dirpath, name), worktree)
            rel = rel.replace("\\", "/")
            if rel in {".git", ".gitattributes", ".gitignore"} or rel.startswith(".git/"):
                continue
            if _path_blocked(rel, blocked_paths):
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > file_size_limit:
                    continue
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue
            if content.count("\n") > line_limit:
                continue
            files.append(MirrorFile(path=rel, content=content))
    return files


_BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".pdf", ".zip", ".gz",
    ".tar", ".7z", ".rar", ".exe", ".dll", ".so", ".dylib", ".woff", ".woff2", ".ttf",
    ".pyc", ".o", ".a", ".class", ".jar", ".lock", ".min.js", ".min.css",
}


def _path_blocked(path: str, blocked_paths: list[str]) -> bool:
    base = os.path.basename(path).lower()
    if path.startswith(".env") or path.endswith(".env"):
        return True
    if base.endswith(tuple(_BINARY_EXT)):
        return True
    p = path.lstrip("/")
    for pat in blocked_paths or []:
        pat = pat.strip().rstrip("/")
        if pat and (p == pat or p.startswith(pat + "/")):
            return True
    return False


def clone_snapshot(
    repository_id: str,
    url: str,
    commit_sha: str,
    *,
    token: str | None = None,
    blocked_paths: list[str],
    max_files: int,
    base_dir: Path | None = None,
) -> list[MirrorFile]:
    """一站式：ensure_mirror + checkout_commit(指定 sha) + walk_snapshot。返回代码文件列表。"""
    mirror = ensure_mirror(repository_id, url, token, base_dir=base_dir)
    root = Path(base_dir) if base_dir else repo_mirror_root(repository_id)
    wt = (root / "wt" / commit_sha[:16]).resolve()
    worktree = checkout_commit(mirror, commit_sha, wt)
    return walk_snapshot(worktree, blocked_paths=blocked_paths, max_files=max_files)


def apply_diff_files(worktree: Path, diff_files) -> None:
    """把 DiffArtifact 的逐文件产物写进 worktree（add/modify 写 after 内容；delete 删文件）。

    安全约束：diff 文件路径必须落在 worktree 内，越界(../ 或绝对路径)一律跳过，
    防止测试/模型产物把文件写穿到宿主任意路径。
    """
    base = worktree.resolve()
    for df in diff_files:
        rel = df.file_path.replace("\\", "/")
        target = worktree / rel
        try:
            # resolve() 真解析 "../" 与绝对路径，词法上 isn't enough（is_relative_to 不做 .. 展开）
            resolved = target.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(base):
            continue  # 越界路径：不做任何写入/删除
        change = getattr(df, "change_type", "modify")
        if change == "delete" or not getattr(df, "after", None):
            if target.exists():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        after = df.after
        target.write_text("\n".join(after), encoding="utf-8")


def commit_changes(worktree: Path, message: str, user: str = "enterprise-agent", email: str = "agent@example.com") -> None:
    _git(worktree, "add", "-A")
    try:
        _git(worktree, "-c", f"user.name={user}", "-c", f"user.email={email}", "commit", "-m", message)
    except RepoMirrorError:
        # 无变化可提交（幂等：已推送过 / 空 diff）
        return


def push_head_branch(worktree: Path, head_branch: str) -> None:
    """把当前 HEAD 推送到 origin 的 head 分支（origin 已是鉴权远程，token 仅在 .git/config，不落日志）。"""
    _git(worktree, "push", "origin", f"HEAD:refs/heads/{head_branch}")