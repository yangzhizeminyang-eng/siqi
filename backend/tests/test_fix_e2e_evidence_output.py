"""FIX-E2E 证据输出路径契约：显式参数 + 不覆盖历史归档 + 并发原子独占。"""

from __future__ import annotations

import json
import multiprocessing
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _process_writer(path: str, start, queue) -> None:
    from scripts import fix_e2e_acceptance as e2e

    start.wait(timeout=15)
    try:
        e2e._write_evidence({"pid": os.getpid()}, path)
        queue.put("ok")
    except FileExistsError:
        queue.put("exists")


def test_evidence_out_argument_is_explicit_and_default_is_new_file():
    from scripts import fix_e2e_acceptance as e2e

    parser = e2e._build_parser()
    target = Path("custom-evidence.json")
    args = parser.parse_args(["--evidence-out", str(target)])
    assert Path(args.evidence_out) == target

    default_args = parser.parse_args([])
    assert default_args.evidence_out is None
    default_path = e2e._default_evidence_path()
    assert default_path.name != "fix_e2e_evidence.json", \
        "默认输出必须是带 run ID 的新文件，不得静默覆盖历史证据"
    assert default_path.name.startswith("fix_e2e_evidence_")


def test_write_evidence_writes_new_file_and_never_overwrites_existing(tmp_path):
    from scripts import fix_e2e_acceptance as e2e

    historical = tmp_path / "fix_e2e_evidence.json"
    historical.write_text('{"historical": true}', encoding="utf-8")
    new_path = tmp_path / "fix_e2e_evidence_run_20261018.json"

    written = e2e._write_evidence({"all_passed": True, "checks": {"x": True}}, new_path)
    assert Path(written) == new_path
    assert new_path.is_file()
    assert historical.read_text(encoding="utf-8") == '{"historical": true}', \
        "历史证据不得被覆盖"

    try:
        e2e._write_evidence({"all_passed": False}, new_path)
    except FileExistsError:
        pass
    else:
        raise AssertionError("已有证据文件必须拒绝覆盖")


def test_write_evidence_does_not_trust_exists_check(monkeypatch, tmp_path):
    """即使 exists() 被绕过（模拟竞态窗口），第二次写入仍必须失败。"""
    from scripts import fix_e2e_acceptance as e2e

    target = tmp_path / "race_evidence.json"
    monkeypatch.setattr(Path, "exists", lambda self: False)

    e2e._write_evidence({"version": 1}, target)
    try:
        e2e._write_evidence({"version": 2}, target)
    except FileExistsError:
        pass
    else:
        raise AssertionError("不能依赖 exists() 预检查；必须原子独占创建")

    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}


def test_write_evidence_is_exclusive_under_concurrent_writers(tmp_path):
    """两个线程同时写同一路径：恰好一个成功，另一个 FileExistsError。"""
    from scripts import fix_e2e_acceptance as e2e

    payload = {"data": "x" * 1_000_000}
    for trial in range(5):
        target = tmp_path / f"concurrent_evidence_{trial}.json"
        barrier = threading.Barrier(2)

        def writer(value: int) -> str:
            barrier.wait(timeout=10)
            try:
                e2e._write_evidence({"writer": value, **payload}, target)
                return "ok"
            except FileExistsError:
                return "exists"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(writer, (1, 2)))

        assert sorted(results) == ["exists", "ok"], results
        assert json.loads(target.read_text(encoding="utf-8"))["writer"] in (1, 2)


def test_write_evidence_is_exclusive_across_processes(tmp_path):
    """两个独立进程同一路径：恰好一个成功，另一个 FileExistsError。"""
    ctx = multiprocessing.get_context("spawn")
    target = tmp_path / "process_evidence.json"
    start = ctx.Event()
    queue = ctx.Queue()
    processes = [
        ctx.Process(target=_process_writer, args=(str(target), start, queue))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    results = [queue.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)

    assert sorted(results) == ["exists", "ok"], results
    assert all(process.exitcode == 0 for process in processes), \
        [(p.pid, p.exitcode) for p in processes]
    assert json.loads(target.read_text(encoding="utf-8"))["pid"] > 0
