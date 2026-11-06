"""git-clone 化索引测试：clone/fetch 成功路径、token 脱敏、blocked_paths/caps、主索引不再依赖 Gitee API 逐文件读取、显式 fallback 标记。

不依赖外部网络：用本地临时 git 仓作为 origin；主索引测试把 DATA_DIR 指到临时目录。
"""
from __future__ import annotations

import base64
import pathlib
import subprocess

import pytest

from app import main

from app.services import repo_mirror


def sh(args, cwd):
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)


@pytest.fixture()
def origin(tmp_path: pathlib.Path) -> pathlib.Path:
    """构造一个含 app.py(正常) / secret.env(应屏蔽) / legacy_file.py(blocked) 的本地 git 仓。"""
    repo = tmp_path / "origin"
    repo.mkdir()
    sh(["git", "init", "-q", "-b", "master"], repo)
    (repo / "app.py").write_text("def hello():\n    return 1\n", encoding="utf8")
    (repo / "README.md").write_text("# hi\n", encoding="utf8")
    (repo / "secret.env").write_text("TOKEN=abc\n", encoding="utf8")
    (repo / "legacy_file.py").write_text("x=1\n", encoding="utf8")
    (repo / "blocked").mkdir()
    (repo / "blocked" / "inner.py").write_text("y=2\n", encoding="utf8")
    sh(["git", "-C", str(repo), "add", "-A"], repo)
    sh(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c1"], repo)
    return repo


def head_of(repo: pathlib.Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def make_repo_record(tmp_path: pathlib.Path, repo_id: str, url: str, blocked: list[str] | None = None):
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    return main.RepositoryConfigRecord(
        id=repo_id, project_id="p", name=pathlib.Path(url).name, url=str(url),
        default_branch="master", test_command="echo ok", command_whitelist=["echo ok"],
        approval_mode="single", risk_threshold="medium", max_files=50, max_retries=2,
        blocked_paths=blocked or [], created_at=now, updated_at=now,
    )


def test_clone_and_fetch_success(origin, tmp_path):
    head = head_of(origin)
    cache = tmp_path / "cache"
    files = repo_mirror.clone_snapshot("repo_a", str(origin), head, token=None,
                                       blocked_paths=["legacy_file.py", "blocked/"], max_files=50, base_dir=cache)
    paths = [f.content and f.path for f in files]
    assert "app.py" in paths and "README.md" in paths, paths
    assert all(p != "secret.env" for p in paths), ".env 应被屏蔽"
    assert all(p != "legacy_file.py" and not p.startswith("blocked/") for p in paths), "blocked_paths 应生效"
    assert (cache / "mirror" / ".git").exists(), "mirror 应已建立"
    # 第二次调用：复用已有 mirror（fetch），结果稳定
    files2 = repo_mirror.clone_snapshot("repo_a", str(origin), head, token=None,
                                        blocked_paths=["legacy_file.py", "blocked/"], max_files=50, base_dir=cache)
    assert {f.path for f in files} == {f.path for f in files2}


def test_masking_and_no_token_leak_on_failure(tmp_path):
    rem = repo_mirror.build_authenticated_remote("https://gitee.com/o/r.git", "ZL-TOPSECRET-9")
    assert rem == "https://oauth2:ZL-TOPSECRET-9@gitee.com/o/r.git"
    masked = repo_mirror.mask_remote(rem)
    assert masked == "https://oauth2:***@gitee.com/o/r.git"
    assert "ZL-TOPSECRET-9" not in masked
    # 真实 clone 失败（含远端回显）也不得泄露 token
    with pytest.raises(repo_mirror.RepoMirrorError) as ei:
        repo_mirror.ensure_mirror("repo_x", "https://gitee.com/not-exist-repo-xyz/r.git",
                                  token="ZL-TOPSECRET-9", base_dir=tmp_path / "c")
    assert "ZL-TOPSECRET-9" not in str(ei.value)
    assert "oauth2:***@" in str(ei.value) or "osha2:ZL" not in str(ei.value)


def test_walk_applies_size_and_line_caps(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "a.py").write_text("x\n" * 10, encoding="utf8")
    (wt / "big.py").write_text("y\n" * 5000, encoding="utf8")
    files = repo_mirror.walk_snapshot(wt, blocked_paths=[], max_files=50,
                                      file_size_limit=200_000, line_limit=2000)
    assert [f.path for f in files] == ["a.py"]
    files2 = repo_mirror.walk_snapshot(wt, blocked_paths=[], max_files=1,
                                       file_size_limit=200_000, line_limit=2000)
    assert len(files2) == 1


def test_main_sync_uses_git_clone_avoids_gitee_api(origin, tmp_path, monkeypatch):
    """git clone 主链：gitee_api_get 不应被调用（不再被 API 限流阻断），source=git_clone。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("USE_GIT_CLONE_INDEX", "true")
    repo = make_repo_record(tmp_path, "repo_b", str(origin), blocked=["legacy_file.py", "blocked/"])

    calls = {"n": 0}
    monkeypatch.setattr(main, "gitee_api_get", lambda *a, **k: calls.update(n=calls["n"] + 1) or {})
    head = head_of(origin)
    files = main.sync_repository_snapshot(repo, head)
    assert calls["n"] == 0, "git-clone 主链不应调用 Gitee API"
    paths = [f.path for f in files]
    assert "app.py" in paths
    assert main._last_snapshot_source == "git_clone"


def test_main_sync_fallback_marks_gitee_api_fallback(tmp_path, monkeypatch):
    """git 克隆失败 → 显式回退 Gitee API 并标记 source=gitee_api_fallback（不静默）。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("USE_GIT_CLONE_INDEX", "true")
    repo = make_repo_record(tmp_path, "repo_c", "https://gitee.com/o/r.git")

    def boom(*a, **k):
        raise repo_mirror.RepoMirrorError("git 失败(clone): remote masked")

    monkeypatch.setattr(repo_mirror, "clone_snapshot", boom)

    def fake_api(path, token, *a, **k):
        if "/git/blobs/" in path:
            return {"content": base64.b64encode(b"def cb():\n    return 2\n").decode()}
        return {"tree": [{"type": "blob", "path": "callback.py", "sha": "abc123"}]}

    monkeypatch.setattr(main, "gitee_api_get", fake_api)
    files = main.sync_repository_snapshot(repo, "deadbeef")
    assert files, "fallback 应产出代码文件"
    assert files[0].path == "callback.py"
    assert main._last_snapshot_source == "gitee_api_fallback"


def test_index_record_restorable_roundtrip(tmp_path, monkeypatch):
    """索引记录（COMPLETED/source）经 DB 往返可恢复：git-clone 产出天然可用。"""
    import tempfile

    db = tmp_path / "restore.db"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app.db.session import create_schema
    from app.db.repositories import upsert_repository_index, load_repository_indexes

    create_schema()
    index = main.RepositoryIndexRecord(
        id="idx_rest", repository_id="r", project_id="p", base_branch="master", commit_sha="c" * 40,
        status="COMPLETED", mode="full", total_files=2, processed_files=2, chunk_count=3,
        requested_by="u", requested_by_name="u", requested_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc), created_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc), updated_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc), finished_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc),
    )
    upsert_repository_index(index)
    loaded = {x["id"]: x for x in load_repository_indexes()}
    assert loaded["idx_rest"]["status"] == "COMPLETED"
    assert loaded["idx_rest"]["commit_sha"] == "c" * 40