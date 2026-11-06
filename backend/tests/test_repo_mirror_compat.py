"""H3 路径穿越防护的回归测试：diff 文件路径越界时必须跳过，不得写穿到 worktree 外。"""
import shutil
import tempfile
from pathlib import Path

from app.services import repo_mirror


class _DF:
    def __init__(self, path: str, after=None, change="modify"):
        self.file_path = path
        self.after = after if after is not None else []
        self.change_type = change


def test_apply_diff_files_skips_outside_worktree():
    base = Path(tempfile.mkdtemp(prefix="wt_compat_"))
    wt = base / "repo"
    wt.mkdir()
    (wt / "keep.txt").write_text("k", encoding="utf-8")
    try:
        # ../ 越界 + 正常文件：越界必须被跳过，正常文件照常写入
        repo_mirror.apply_diff_files(wt, [_DF("../escape.txt", ["x"]), _DF("in.txt", ["a"])])
        assert not (base / "escape.txt").exists(), "越界路径不应被写穿"
        assert (wt / "in.txt").exists()
        assert (wt / "in.txt").read_text(encoding="utf-8") == "a"
        # 绝对路径越界也跳过
        repo_mirror.apply_diff_files(wt, [_DF(str(base / "abs_escaped.txt"), ["x"])])
        assert not (base / "abs_escaped.txt").exists()
    finally:
        shutil.rmtree(base, ignore_errors=True)