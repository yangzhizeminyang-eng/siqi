"""OPT-13 多 Worker 恢复与长稳验证 harness（默认隔离环境；真实进程/真实 broker 协议）。

不做假验证：拉起真实 fake-Redis（fakeredis TCP，RESP 协议 + Lua）、真实 SQLite DB、
真实 Celery Worker 子进程（生产 celery_app，仅注入 harness 环境变量），用 taskkill 真实杀进程，
断言全部来自 DB 账本（enterprise_runtime_tasks / enterprise_agent_runs）与 Worker 原始日志。

环境启动方差（2026-10-16 实测：热启动 1.5-6s，冷/负载下 30-90s，双峰分布）：
所有进程启动等待、子进程超时、探针窗口均按上界加固；子进程超时被收敛为 rc=-998
（场景级失败检查），不升级为全局 fatal。

场景：
S1  smoke×6 恰好一次（双 Worker 分布消费，按 message= 标记计数）。
S2  同任务双投递：A 带长探针（150s，须覆盖 B 启动上界）保证「锁被持有 + step RUNNING」
    窗口可观测；重复消息由 seed-then-dup 同一子进程在观测到 RUNNING 后立即发送（消除
    跨子进程冷启动竞态）→ B 消费第二条 → 锁忙有界重试 → A 完成后接管空跑 →
    单链执行、completed_steps 无重复、各步 succeeded run 恰好 1、无 err。
S3  崩溃恢复：Worker A 带慢执行探针（90s）跑到 requirement 步 RUNNING + AgentRun running
    → taskkill /F /T 真实杀 A → 等可见性超时过后起 B（kombu 恢复扫描的确定性触发点是
    新 Worker 启动首扫；空闲轮询尾部为辅）→ 回收重做（attempt=2，崩溃 run 落 failed）→
    WAITING_APPROVAL；kill→收敛 < 300s（含 visibility 等待 40s + B 启动方差）。杀 A 后
    与起 B 前各做一次 broker 取证（unacked_index 存在途条目、unacked_mutex 无残锁）。
S4  有界长稳（确定性受害者）：先只留 B（150s 慢执行探针，须覆盖 A 启动重叠）消费 T4-1，
    观测到 RUNNING+hold 后才启动 A 并 taskkill B（真实故障注入）→ 可见性超时后重启
    B-again 承接重投恢复，其余 5 个任务并行补投收敛 → 全部 WAITING_APPROVAL、账本无
    悬挂 running、恢复后 smoke 恰好一次；kill→全量收敛 < 300s（总墙钟含启动方差，仅报告）。

Worker 引导层经 scripts.opt13_trace_restore 加固 kombu unacked_mutex 释放（无 Lua CAS，
规避 fakeredis 的 NoScriptError 残锁缺陷），并输出 [opt13-broker] 恢复取证日志
（仅供 harness 与证据引用；生产代码零改动）。

子进程模式（harness 主进程不 import 任何 app 模块）：
  --mode init-db / seed / seed-then-dup / send-execute / smoke

用法：.venv/Scripts/python.exe -m scripts.opt13_multiworker_verify [--scenarios s1,s2,s3,s4]
退出码：0=全部检查通过；2=存在失败检查（如实报告，不伪造成功）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import redis

BACKEND_ROOT = Path(__file__).resolve().parents[1]
EXP_ROOT = BACKEND_ROOT / ".data" / "opt13-exp"
RUN_ROOT = EXP_ROOT / "run"
LOG_DIR = RUN_ROOT / "logs"
DB_PATH = RUN_ROOT / "app.db"
PY = str(BACKEND_ROOT / ".venv" / "Scripts" / "python.exe")

DISPATCH_TTL = 15
STEP_LEASE_TTL = 10
VISIBILITY_TIMEOUT = 30
PROBE_SLEEP = 90            # S3 慢执行探针：RUNNING 观测后 kill 必落在 hold 窗口内
S2_PROBE_SLEEP = 150        # S2 持锁窗口：须覆盖 B 启动上界（负载下实测 15-60s）使重复消息必落在持锁期内
S4_VICTIM_PROBE_SLEEP = 150  # S4 受害者窗口：须覆盖「A 启动重叠（负载下 15-60s）+ kill 余量」
RECOVERY_BUDGET_S = 300     # S3 kill→WAITING_APPROVAL 预算（含 visibility 等待 40s + B 启动方差上界）
S4_BUDGET_S = 300           # S4 kill→全量收敛预算（总墙钟含环境启动方差，仅报告不设界）
TENANT = "tenant_default"
V2_FIRST_STEPS = ["requirement_agent", "repository_research_agent", "planner_agent"]


# ---------------- 子进程环境（隔离配方，与 OPT-03/OPT-05 同口径） ----------------


def build_env(port: int) -> dict[str, str]:
    env = dict(os.environ)
    env["ENV_FILE"] = str(EXP_ROOT / ".env-harness-nonexistent")  # 屏蔽本机 backend/.env
    env["APP_ENV"] = "local"
    env["USE_SQLALCHEMY"] = "true"
    env["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
    env["USE_CELERY"] = "true"
    env["CELERY_BROKER_URL"] = f"redis://127.0.0.1:{port}/0"
    env["CELERY_RESULT_BACKEND"] = f"redis://127.0.0.1:{port}/1"
    env["REDIS_URL"] = f"redis://127.0.0.1:{port}/0"  # 分布式锁走真实 RedisLockBackend
    env["DATA_DIR"] = str(RUN_ROOT / "data")
    env["USE_MILVUS"] = "false"
    env["AGENT_PROVIDER"] = "deterministic"
    env["MODEL_MOCK_FALLBACK"] = "true"
    env["PYTHONIOENCODING"] = "utf-8"
    env["ENTERPRISE_AGENT_DISPATCH_TTL_SECONDS"] = str(DISPATCH_TTL)
    env["ENTERPRISE_STEP_LEASE_TTL_SECONDS"] = str(STEP_LEASE_TTL)
    env["OPT13_VISIBILITY_TIMEOUT"] = str(VISIBILITY_TIMEOUT)
    env["OPT13_PROBE_AGENTS"] = "requirement_agent"
    env["OPT13_TRACE_RESTORE"] = "1"  # [opt13-broker] 恢复取证日志（worker_boot 侧效应模块）
    env.pop("CELERY_QUEUE", None)
    env.pop("OPT13_PROBE_SLEEP_SECONDS", None)  # 慢执行仅按 Worker 角色显式注入
    return env


# ---------------- 进程/端口/日志工具 ----------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_proc(cmd: list[str], env: dict[str, str], log_path: Path) -> dict:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_path, "ab")
    proc = subprocess.Popen(cmd, cwd=str(BACKEND_ROOT), env=env,
                            stdout=fh, stderr=subprocess.STDOUT)
    return {"cmd": cmd, "proc": proc, "fh": fh, "log": log_path}


def kill_tree(proc: subprocess.Popen, timeout: float = 20.0) -> int | None:
    if proc.poll() is None:
        # taskkill 输出在中文 Windows 为 GBK：一律按字节丢弃，不做文本解码
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    try:
        proc.wait(timeout=timeout)
    except Exception:  # noqa: BLE001
        pass
    return proc.poll()


def read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def wait_log_text(path: Path, needle: str, timeout: float) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = read_log(path)
        if needle in text:
            return text
        time.sleep(0.3)
    return None


def wait_new_log(path: Path, baseline: int, needle: str, timeout: float) -> str | None:
    """只匹配日志第 baseline 行之后的新增内容。

    同一 Worker 名重启会 append 到同一日志文件，旧会话的 ready/hold 行会让
    wait_log_text 瞬时误判「已就绪/已进入探针」，这里用行数基线切片规避。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = read_log(path).splitlines()
        new_text = "\n".join(lines[baseline:])
        if needle in new_text:
            return new_text
        time.sleep(0.3)
    return None


def log_since(worker: dict) -> str:
    """Worker 当前会话（log_lines0 基线之后）的日志切片。"""
    lines = read_log(worker["log"]).splitlines()
    return "\n".join(lines[worker.get("log_lines0", 0):])


def wait_until(fn, timeout: float, interval: float = 0.5):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:  # noqa: BLE001 轮询期瞬时错误（DB 忙/半写）不致命
            last = exc
        time.sleep(interval)
    return None


def _timeout_result(exc: subprocess.TimeoutExpired, mode_args: list[str],
                    timeout: float) -> subprocess.CompletedProcess:
    out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
    err = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
    return subprocess.CompletedProcess(
        args=exc.cmd, returncode=-998, stdout=out,
        stderr=err + f"\n[run_child] TimeoutExpired after {timeout}s: {mode_args}")


def run_child(mode_args: list[str], env: dict[str, str], timeout: float) -> subprocess.CompletedProcess:
    """子进程调用；超时不让 TimeoutExpired 逃逸（否则升级为全局 fatal 并跳过后续场景）。

    环境冷启动方差大（热 1.5-6s / 冷或负载下 30-90s），调用方超时须按上界给足；
    真的超时则返回 rc=-998，由场景层作为失败检查如实记录。
    """
    try:
        return subprocess.run([PY, "-m", "scripts.opt13_multiworker_verify", *mode_args],
                              cwd=str(BACKEND_ROOT), env=env, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        return _timeout_result(exc, mode_args, timeout)


def run_children_parallel(mode_args_list: list[list[str]], env: dict[str, str],
                          timeout: float) -> list[subprocess.CompletedProcess]:
    """并行版 run_child：全部先启动，再按共享截止时间逐个回收。

    串行 seed 会线性累加冷启动墙钟（每个 30-90s）；并行后总等待 ≈ 共享超时上界。
    单个子进程超时 → kill 并记 rc=-998，不影响其余子进程。
    """
    procs = [subprocess.Popen([PY, "-m", "scripts.opt13_multiworker_verify", *m],
                              cwd=str(BACKEND_ROOT), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8",
                              errors="replace")
             for m in mode_args_list]
    deadline = time.monotonic() + timeout
    results: list[subprocess.CompletedProcess] = []
    for proc, mode_args in zip(procs, mode_args_list):
        remaining = max(1.0, deadline - time.monotonic())
        try:
            out, err = proc.communicate(timeout=remaining)
            results.append(subprocess.CompletedProcess(
                args=proc.args, returncode=proc.returncode, stdout=out, stderr=err))
        except subprocess.TimeoutExpired as exc:
            kill_tree(proc)
            exc.stdout, exc.stderr = proc.communicate()
            results.append(_timeout_result(exc, mode_args, timeout))
    return results


# ---------------- DB 账本查询（只读） ----------------


def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def task_snapshot(tid: str) -> dict | None:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM enterprise_runtime_tasks WHERE id=?", (tid,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["steps"] = json.loads(data["steps"] or "[]")
        data["completed_steps"] = json.loads(data["completed_steps"] or "[]")
        return data


def runs_snapshot(tid: str) -> list[dict]:
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT run_id, task_id, agent_type, step_id, attempt, repair_round, status, "
            "error_reason, idempotency_key FROM enterprise_agent_runs WHERE task_id=? "
            "ORDER BY agent_type, attempt", (tid,)).fetchall()
        return [dict(r) for r in rows]


def running_step(snapshot: dict | None, agent_type: str | None = None) -> dict | None:
    if not snapshot:
        return None
    for step in snapshot["steps"]:
        if step.get("status") == "running" and (agent_type is None or step.get("agent_type") == agent_type):
            return step
    return None


def run_ledger_check(tid: str, agent_type: str, *, expect_crash_redo: bool,
                     strict_redo: bool = True) -> dict:
    runs = runs_snapshot(tid)
    mine = [r for r in runs if r["agent_type"] == agent_type]
    succeeded = [r for r in mine if r["status"] == "succeeded"]
    failed = [r for r in mine if r["status"] == "failed"]
    running = [r for r in mine if r["status"] == "running"]
    detail = {
        "agent_type": agent_type,
        "succeeded": len(succeeded),
        "failed": len(failed),
        "running_dangling": len(running),
        "attempts": [r["attempt"] for r in mine],
        "runs": mine,
    }
    if expect_crash_redo and strict_redo:
        # S3 强断言：probe 保证崩溃发生在 AgentRun 已写入 running 之后
        ok = (len(succeeded) == 1 and succeeded[0]["attempt"] == 2
              and len(failed) == 1 and failed[0]["attempt"] == 1
              and "reclaim" in (failed[0]["error_reason"] or "")
              and not running)
    elif expect_crash_redo:
        # S4 宽松断言：杀进程落点可能在「已置 RUNNING 但 run 尚未写入」的毫秒窗口，
        # 此时重做为 attempt=2 且无 failed run——两种形态均合法，只需无悬挂。
        ok = (len(succeeded) == 1 and not running and (
            (len(failed) == 1 and failed[0]["attempt"] == 1
             and succeeded[0]["attempt"] == 2
             and "reclaim" in (failed[0]["error_reason"] or ""))
            or (len(failed) == 0 and succeeded[0]["attempt"] == 2)))
    else:
        ok = (len(succeeded) == 1 and succeeded[0]["attempt"] == 1
              and not failed and not running)
    detail["ok"] = ok
    return detail


def ts_from_log(text: str, needle: str) -> float | None:
    """从 celery 日志文本取第一个匹配行的本地时间戳（epoch 秒）。"""
    m = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})[^\]]*\] [^\n]*" + re.escape(needle),
                  text)
    if not m:
        return None
    dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=timezone.utc)
    return dt.timestamp() - time.timezone  # 日志为本地时间


def count_occurrences(text: str, needle: str) -> int:
    return text.count(needle)


def count_received_enterprise_since(worker: dict) -> int:
    """celery 5.x 日志格式为 `Task enterprise.agent_execute[<id>] received`（旧版 `Received task:` 已废弃）。

    按会话基线切片：同一 Worker 名重启 append 同一文件，整文件计数会把历史会话的接收
    也算进来（S3 中 B 是全新文件不受影响，但 S4 的 b-again 复用名会误报）。
    """
    return sum(1 for line in log_since(worker).splitlines()
               if "enterprise.agent_execute[" in line and "] received" in line)


def broker_probe(port: int) -> dict:
    """直连 fake-Redis 读取恢复相关键状态（只读；异常如实记录，不伪造通过）。

    unacked_index 存在途条目 = 被杀 Worker 未确认的在途消息；
    unacked_mutex 无键 = 恢复扫描不会被残锁抑制（kombu 用该 mutex 串行化 restore_visible）。
    """
    try:
        client = redis.Redis(host="127.0.0.1", port=port, db=0, socket_timeout=5,
                             decode_responses=True)
        keys = sorted(client.keys("*"))
        unacked_index = client.zrange("unacked_index", 0, -1, withscores=True)
        unacked = client.hgetall("unacked")
        mutex_ttl = client.ttl("unacked_mutex")
        info = {
            "keys": keys,
            "unacked_index_entries": [[m, s] for m, s in unacked_index],
            "unacked_hlen": len(unacked),
            "unacked_mutex": {"exists": mutex_ttl >= 0, "ttl_seconds": mutex_ttl},
        }
        other = {}
        for key in keys:
            if key in ("unacked_index", "unacked", "unacked_mutex"):
                continue
            ktype = client.type(key)
            if ktype == "list":
                other[key] = {"type": "list", "llen": client.llen(key)}
            else:
                other[key] = {"type": ktype}
        info["other_keys"] = other
        client.close()
        return info
    except Exception as exc:  # noqa: BLE001 取证失败如实记录
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------- Worker 生命周期 ----------------


def start_worker(name: str, port: int, probe_sleep: float) -> dict:
    env = build_env(port)
    env["OPT13_PROBE_SLEEP_SECONDS"] = str(probe_sleep)
    cmd = [PY, "-m", "celery", "-A", "scripts.opt13_worker_boot:celery_app",
           "worker", "--pool=solo", "-n", f"{name}@127.0.0.1", "-l", "INFO",
           "-Q", "celery", "--prefetch-multiplier=1"]
    log_path = LOG_DIR / f"worker_{name}.log"
    baseline = len(read_log(log_path).splitlines())  # append 模式：旧会话内容不参与新会话断言
    proc = start_proc(cmd, env, log_path)
    proc["log_lines0"] = baseline
    marker = f"{name}@127.0.0.1 ready"
    # 就绪等待按冷启动上界（负载下实测 30-90s）放宽到 210s：启动慢于场景容忍会如实报错，不误判
    if wait_new_log(log_path, baseline, marker, timeout=210) is None:
        raise RuntimeError(f"worker {name} 未在 210s 内就绪: {log_path}")
    proc["name"] = name
    return proc


# ---------------- 场景实现 ----------------


def scenario_s1_smoke(port: int, env: dict[str, str], workers: list[dict]) -> dict:
    messages = [f"s1-{i:03d}" for i in range(6)]
    t0 = time.time()
    child = run_child(["--mode", "smoke", *messages], env, timeout=240)
    results = json.loads(child.stdout.strip().splitlines()[-1]) if child.returncode == 0 else {}
    time.sleep(1.0)
    combined = "".join(read_log(w["log"]) for w in workers)
    consumed = {m: count_occurrences(combined, f"message={m}") for m in messages}
    per_worker = {w["name"]: {m: count_occurrences(read_log(w["log"]), f"message={m}") for m in messages}
                  for w in workers}
    checks = {
        "smoke_results_ok": child.returncode == 0 and all(
            results.get(m, {}).get("echo") is True for m in messages),
        "consumed_exactly_once": all(n == 1 for n in consumed.values()),
        "no_result_failures": child.returncode == 0,
    }
    return {
        "scenario": "s1_smoke_exactly_once",
        "wall_seconds": round(time.time() - t0, 2),
        "consumed_counts": consumed,
        "per_worker_counts": per_worker,
        "worker_distribution": {w["name"]: sum(v.values()) for w, v in
                                ((w, per_worker[w["name"]]) for w in workers)},
        "checks": {**checks, "all_passed": all(checks.values())},
    }


def scenario_s2_double_dispatch(port: int, env: dict[str, str], worker_a: dict, start_b, tid: str) -> dict:
    """确定性锁竞争窗口：A 带 150s 探针持有派发锁；重复消息由同一子进程在观测到 RUNNING 后立即发送。

    时序：子进程 seed（A 消费）→ 子进程轮询 DB 观测 RUNNING → 子进程立即发送第二条消息
    → 启动 B（此刻唯一空闲消费者，消费第二条）→ B 锁忙有界重试 → A 完成并释放 →
    B 重试接管对已完成任务空跑（幂等）。

    旧时序缺陷（2026-10-16 全量实测）：seed 与 send-execute 是两个独立子进程，发送侧
    冷启动可达 ~35s，重复消息在 A 探针窗口（旧 25s）结束后才到达，被已空闲的 A 自己
    消费（B 零接收、busy_retry=0）。改由同一子进程观测 RUNNING 后立即发送 + 探针
    150s（须覆盖 B 启动上界，负载下实测 15-60s）。
    """
    t0 = time.time()
    child = run_child(["--mode", "seed-then-dup", "--task-id", tid, "--title", "OPT13-S2 双投递"],
                      env, timeout=240)
    payload: dict = {}
    if child.returncode == 0 and child.stdout.strip():
        try:
            payload = json.loads(child.stdout.strip().splitlines()[-1])
        except ValueError:
            payload = {}
    if child.returncode != 0 or payload.get("dup_sent") is not True:
        return {"scenario": "s2_double_dispatch",
                "error": (child.stderr or child.stdout)[-2000:], "all_passed": False}
    # 子进程已确认 RUNNING 且重复消息已发出；这里快速复核（此刻 A 仍在 150s 探针 hold 内）
    seen = wait_until(lambda: running_step(task_snapshot(tid)), timeout=30)
    holder_ok = bool(seen) and str(seen.get("worker_id", "")).startswith(worker_a["name"])
    worker_b = start_b()  # 唯一空闲消费者：消费重复消息；A 探针未返回 → 必落在持锁期内
    snap = wait_until(lambda: (s := task_snapshot(tid)) and s["status"] == "waiting_approval" and s,
                      timeout=180)
    err_hits = {name: count_occurrences(
        read_log(w["log"]), f"[enterprise.agent_execute] task={tid} err=")
        for name, w in (("opt13a", worker_a), ("opt13b", worker_b))}
    busy_retry = count_occurrences(log_since(worker_b), "retry: Retry in")
    ledger = [run_ledger_check(tid, at, expect_crash_redo=False) for at in V2_FIRST_STEPS]
    running_left = [r for r in runs_snapshot(tid) if r["status"] == "running"]
    checks = {
        "running_seen_before_dup": bool(seen) and payload.get("running_worker_id", "").startswith(
            worker_a["name"]),
        "running_held_by_a": holder_ok,
        "duplicate_sent": True,
        "b_busy_retry_observed": busy_retry >= 1,
        "final_waiting_approval": bool(snap),
        "completed_steps_ok": bool(snap) and snap["completed_steps"] == V2_FIRST_STEPS,
        "per_step_single_succeeded_run": all(item["ok"] for item in ledger),
        "no_dangling_runs": not running_left,
        "no_dispatch_err": all(n == 0 for n in err_hits.values()),
    }
    return {
        "scenario": "s2_double_dispatch",
        "wall_seconds": round(time.time() - t0, 2),
        "task_id": tid,
        "running_step_at_dup": seen,
        "b_busy_retry_count": busy_retry,
        "final_status": (snap or {}).get("status"),
        "completed_steps": (snap or {}).get("completed_steps"),
        "ledger": ledger,
        "dispatch_err_hits": err_hits,
        "checks": {**checks, "all_passed": all(checks.values())},
    }


def scenario_s3_crash_recovery(port: int, env: dict[str, str], tid: str,
                               worker_a: dict, start_b) -> dict:
    t0 = time.time()
    timeline: list[dict] = []

    def mark(event: str, **kw) -> None:
        timeline.append({"event": event, "at": datetime.now(timezone.utc).isoformat(), **kw})

    seed = run_child(["--mode", "seed", "--task-id", tid, "--title", "OPT13-S3 崩溃恢复"], env, timeout=240)
    if seed.returncode != 0:
        return {"scenario": "s3_crash_recovery", "error": seed.stderr[-2000:], "all_passed": False}
    mark("seeded")

    def _running_on_a():
        snap = task_snapshot(tid)
        step = running_step(snap, V2_FIRST_STEPS[0])
        if step and str(step.get("worker_id", "")).startswith(worker_a["name"]):
            return step
        return None

    step = wait_until(_running_on_a, timeout=150)
    if step is None:
        return {"scenario": "s3_crash_recovery", "error": "未观察到 requirement 步骤在 A 上 RUNNING",
                "all_passed": False}
    mark("step_running_on_a", worker_id=step.get("worker_id"), agent_attempt=step.get("agent_attempt"))

    # 等探针「hold」落日志再杀：确保 kill 确定落在 AgentRun 已写入 running 的执行期中部
    # （按会话基线只匹配新增行：A 的日志文件在 S2 阶段已被复用，旧 hold 行会瞬时误判）
    probe_seen = wait_new_log(worker_a["log"], worker_a["log_lines0"], "[opt13-probe] hold",
                              timeout=30) is not None
    kill_at = time.time()
    code = kill_tree(worker_a["proc"])
    broker_after_kill = broker_probe(port)
    mark("worker_a_killed", exit_code=code, probe_seen=probe_seen, broker=broker_after_kill)

    # kombu 可见性恢复扫描由轮询周期驱动：空闲 Worker 阻塞在 BRPOP 时不会周期扫描；
    # 新 Worker 启动时的首扫（_vrestore_count==1）才会重投「已超过 visibility_timeout
    # 的未确认消息」。因此先等过可见性超时（+余量），再启动 B，使重投成为确定性事件
    # （等待期同时让步骤租约先行过期，回收与重做顺序确定）。
    # 注意：restore_visible 用 redis unacked_mutex 串行化，历史上曾被 NoScriptError
    # 残锁（TTL 300s）整体抑制（见模块 docstring）；本 harness 已在 worker_boot 加固
    # 释放。起 B 前的 broker 取证将断言：无残锁 + unacked_index 存在途条目。
    wait_after = VISIBILITY_TIMEOUT + 10
    while time.time() - kill_at < wait_after:
        time.sleep(1)
    mark("visibility_elapsed", waited_seconds=round(time.time() - kill_at, 1))
    broker_before_recovery = broker_probe(port)
    mark("broker_before_recovery_worker", broker=broker_before_recovery)
    worker_b = start_b()
    mark("worker_b_started")

    snap = wait_until(lambda: (s := task_snapshot(tid)) and s["status"] == "waiting_approval" and s,
                      timeout=RECOVERY_BUDGET_S)
    recovered_at = time.time()
    recovery_seconds = round(recovered_at - kill_at, 2)
    mark("recovered", recovery_seconds=recovery_seconds, status=(snap or {}).get("status"))

    redelivered = count_received_enterprise_since(worker_b)
    ledger = [run_ledger_check(tid, at, expect_crash_redo=(at == V2_FIRST_STEPS[0]))
              for at in V2_FIRST_STEPS]
    running_left = [r for r in runs_snapshot(tid) if r["status"] == "running"]
    final_step0 = (snap or {}).get("steps", [{}])[0] if snap else {}
    err_hits = {w["name"]: count_occurrences(read_log(w["log"]),
                                             f"[enterprise.agent_execute] task={tid} err=")
                for w in [worker_a, worker_b]}
    checks = {
        "step_running_seen_on_a": True,
        "killed_mid_probe_window": probe_seen,
        "worker_a_dead": worker_a["proc"].poll() is not None,
        "in_flight_unacked_present": "error" not in broker_after_kill
        and len(broker_after_kill.get("unacked_index_entries", [])) >= 1,
        "no_stale_mutex_before_recovery": "error" not in broker_before_recovery
        and not broker_before_recovery.get("unacked_mutex", {}).get("exists", True),
        "redelivered_to_b": redelivered >= 1,
        "recovered_in_budget": bool(snap) and recovery_seconds <= RECOVERY_BUDGET_S,
        "final_waiting_approval": bool(snap),
        "completed_steps_ok": bool(snap) and snap["completed_steps"] == V2_FIRST_STEPS,
        "crash_run_failed_and_redo_attempt2": ledger[0]["ok"],
        "later_steps_single_clean_run": all(item["ok"] for item in ledger[1:]),
        "step0_redone_by_b": str(final_step0.get("worker_id", "")).startswith(worker_b["name"]),
        "no_dangling_runs": not running_left,
        "no_dispatch_err": all(n == 0 for n in err_hits.values()),
    }
    return {
        "scenario": "s3_crash_recovery",
        "wall_seconds": round(time.time() - t0, 2),
        "task_id": tid,
        "recovery_seconds": recovery_seconds,
        "timeline": timeline,
        "ledger": ledger,
        "redelivered_received_count": redelivered,
        "dispatch_err_hits": err_hits,
        "broker_after_kill": broker_after_kill,
        "broker_before_recovery": broker_before_recovery,
        "checks": {**checks, "all_passed": all(checks.values())},
    }


def scenario_s4_bounded_longrun(port: int, env: dict[str, str], workers: dict) -> dict:
    """有界长稳 + 确定性故障注入。

    旧设计缺陷：串行 seed 6 个任务（~60s）后才开始轮询「谁在 B 上 RUNNING」，
    B 的短探针窗口早已结束（killed_mid_step 恒 False 的竞态）。现改为：
    先只留带长探针（150s，须覆盖 A 启动重叠上界）的 B 消费 T4-1，观测 RUNNING+hold
    落日志后启动 A（其启动耗时负载下 15-60s 落在受害者窗口内），再 taskkill B —— kill
    确定落在「AgentRun 已写入 running 的执行期中部」；可见性超时后 B-again 启动首扫
    重投在途消息，其余 5 个任务并行补投，A/B-again 并行收敛。
    """
    t0 = time.time()
    tasks = [f"T4-{i}" for i in range(1, 7)]
    victim_task = tasks[0]

    # 清场：停掉 S3 遗留的 A/B，仅留「带长探针的 B」作为确定性受害者
    for name in ("b", "a"):
        if name in workers:
            kill_tree(workers[name]["proc"])
    workers.pop("b", None)
    workers.pop("a", None)
    b = start_worker("opt13b", port, probe_sleep=S4_VICTIM_PROBE_SLEEP)
    workers["b"] = b

    seed = run_child(["--mode", "seed", "--task-id", victim_task, "--title", "OPT13-S4 长稳 T4-1"],
                     env, timeout=240)
    if seed.returncode != 0:
        return {"scenario": "s4_bounded_longrun", "error": seed.stderr[-2000:], "all_passed": False}

    def _victim_running():
        step = running_step(task_snapshot(victim_task))
        if step and str(step.get("worker_id", "")).startswith(b["name"]):
            return step
        return None

    victim_step = wait_until(_victim_running, timeout=150)
    victim_running = victim_step is not None
    probe_seen = victim_running and wait_new_log(
        b["log"], b["log_lines0"], "[opt13-probe] hold", timeout=30) is not None

    # 起 A（无探针）承接其余任务的收敛；其启动耗时重叠在 B 的受害者窗口内
    a = start_worker("opt13a", port, probe_sleep=0)
    workers["a"] = a

    # kill 前再确认受害者仍在 RUNNING（若 A 启动超时、任务已推进则如实判 False，不虚构故障点）
    victim_still_running = _victim_running() is not None
    killed_mid_step = victim_running and probe_seen and victim_still_running
    kill_at = time.time()
    kill_tree(b["proc"])
    broker_after_kill = broker_probe(port)

    # 与 S3 同口径：等可见性超时过后再起 B2，让其启动首扫确定性重投 B 被杀时未确认的在途消息
    while time.time() - kill_at < VISIBILITY_TIMEOUT + 10:
        time.sleep(1)
    b2 = start_worker(f"{b['name']}-again", port, probe_sleep=0)
    workers["b-again"] = b2  # 登记进 workers，确保 main() finally 统一 kill + close（否则孤儿持日志句柄）

    # 其余 5 个任务在恢复期并行补投（串行 seed 会线性累加冷启动墙钟；A 与 B-again 并行消费）
    seed_cmds = [["--mode", "seed", "--task-id", tid, "--title", f"OPT13-S4 长稳 {tid}"]
                 for tid in tasks[1:]]
    for tid, child in zip(tasks[1:], run_children_parallel(seed_cmds, env, timeout=240)):
        if child.returncode != 0:
            return {"scenario": "s4_bounded_longrun",
                    "error": f"seed {tid} rc={child.returncode}: {(child.stderr or '')[-2000:]}",
                    "all_passed": False}

    final = wait_until(lambda: all(
        (s := task_snapshot(tid)) and s["status"] == "waiting_approval" for tid in tasks), timeout=600)
    converged_at = time.time()
    recovery_seconds = round(converged_at - kill_at, 2)
    wall = round(time.time() - t0, 2)

    per_task = {}
    for tid in tasks:
        snap = task_snapshot(tid) or {}
        ledger = []
        for at in V2_FIRST_STEPS:
            mine = [r for r in runs_snapshot(tid) if r["agent_type"] == at]
            crashed = [r for r in mine if r["status"] == "failed"]
            ledger.append(run_ledger_check(tid, at, expect_crash_redo=bool(crashed),
                                           strict_redo=False))
        per_task[tid] = {
            "status": snap.get("status"),
            "completed_steps": snap.get("completed_steps"),
            "ledger_ok": all(item["ok"] for item in ledger),
            "ledger": ledger,
        }
    all_runs = [r for tid in tasks for r in runs_snapshot(tid)]
    dangling = [r for r in all_runs if r["status"] == "running"]

    # 恢复后再发 smoke：消息链路仍健康且恰好一次
    smoke = run_child(["--mode", "smoke", "s4-post-001", "s4-post-002"], env, timeout=240)
    time.sleep(0.5)
    post_logs = read_log(a["log"]) + read_log(b2["log"])
    post_once = count_occurrences(post_logs, "message=s4-post-001") == 1 \
        and count_occurrences(post_logs, "message=s4-post-002") == 1
    checks = {
        "all_tasks_waiting_approval": bool(final),
        "killed_mid_step": killed_mid_step,
        "per_task_ledgers_ok": all(v["ledger_ok"] for v in per_task.values()),
        "no_dangling_runs": not dangling,
        "post_recovery_smoke_once": smoke.returncode == 0 and post_once,
        "wall_bound_ok": bool(final) and recovery_seconds <= S4_BUDGET_S,
    }
    return {
        "scenario": "s4_bounded_longrun",
        "wall_seconds": wall,
        "recovery_seconds": recovery_seconds,
        "total_wall_seconds": wall,
        "killed_mid_step": killed_mid_step,
        "victim_task": victim_task,
        "victim_running": victim_running,
        "probe_seen": probe_seen,
        "victim_still_running_at_kill": victim_still_running,
        "victim_step": victim_step,
        "kill_to_restart_wait_s": VISIBILITY_TIMEOUT + 10,
        "per_task": per_task,
        "post_smoke_ok": smoke.returncode == 0,
        "broker_after_kill": broker_after_kill,
        "checks": {**checks, "all_passed": all(checks.values())},
    }


# ---------------- 子进程模式 ----------------


def child_main(argv) -> int:
    if argv.mode == "init-db":
        from app.db import migrate
        from app.db.session import create_schema

        create_schema()
        migrate.run_upgrade()
        print("[init-db] OK")
        return 0
    if argv.mode == "seed":
        from types import SimpleNamespace

        from app.enterprise_runtime import orchestrator as orch

        ctx = SimpleNamespace(tenant_id=TENANT, user_id="opt13-harness", user_name="harness",
                              role="tenant_owner")
        task = orch.start_runtime_task(ctx, argv.task_id, f"ik-{argv.task_id}",
                                       title=argv.title or argv.task_id, workflow_version=2,
                                       dispatch=True)
        print(json.dumps({"task_id": task.id, "status": task.status.value}))
        return 0
    if argv.mode == "seed-then-dup":
        # S2 专用：同一进程内 seed → 观测 RUNNING → 立即发重复消息（消除跨子进程冷启动竞态）
        from types import SimpleNamespace

        from app.enterprise_runtime import orchestrator as orch
        from app.workers.celery_app import celery_app

        ctx = SimpleNamespace(tenant_id=TENANT, user_id="opt13-harness", user_name="harness",
                              role="tenant_owner")
        task = orch.start_runtime_task(ctx, argv.task_id, f"ik-{argv.task_id}",
                                       title=argv.title or argv.task_id, workflow_version=2,
                                       dispatch=True)
        deadline = time.time() + 120
        running_worker_id = None
        while time.time() < deadline and running_worker_id is None:
            step = running_step(task_snapshot(argv.task_id), V2_FIRST_STEPS[0])
            if step:
                running_worker_id = step.get("worker_id")
            else:
                time.sleep(0.3)
        if running_worker_id is None:
            print(json.dumps({"task_id": task.id, "dup_sent": False,
                              "error": "未在 120s 内观测到 requirement 步 RUNNING"},
                             ensure_ascii=False))
            return 3
        celery_app.send_task("enterprise.agent_execute", kwargs={"task_id": argv.task_id})
        print(json.dumps({"task_id": task.id, "status": task.status.value,
                          "running_worker_id": running_worker_id, "dup_sent": True}))
        return 0
    if argv.mode == "send-execute":
        from app.workers.celery_app import celery_app

        for _ in range(argv.count):
            celery_app.send_task("enterprise.agent_execute", kwargs={"task_id": argv.task_id})
        print(json.dumps({"task_id": argv.task_id, "sent": argv.count}))
        return 0
    if argv.mode == "smoke":
        from app.workers.celery_app import celery_app

        results = {}
        for message in argv.messages:
            result = celery_app.send_task("backend.smoke", kwargs={"message": message})
            results[message] = result.get(timeout=150, interval=0.3)
        print(json.dumps(results, ensure_ascii=False))
        return 0
    raise SystemExit(f"unknown child mode: {argv.mode}")


# ---------------- 主流程 ----------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="OPT-13 多 Worker 恢复与长稳验证 harness")
    parser.add_argument("--mode", choices=["init-db", "seed", "seed-then-dup", "send-execute", "smoke"],
                        help="子进程模式（内部使用）")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("messages", nargs="*", help="smoke 模式的 message 列表")
    parser.add_argument("--scenarios", default="s1,s2,s3,s4")
    parser.add_argument("--report", default=str(EXP_ROOT / "opt13_multiworker_report.json"))
    parser.add_argument("--fresh", action="store_true", default=True,
                        help="重建运行目录（默认 true；保留历史请改 RUN_ROOT 常量）")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.mode:
        return child_main(args)

    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    if RUN_ROOT.exists():
        # Windows 句柄释放有延迟：若上一次运行的 worker 刚被杀，rmtree 可能短暂 WinError 32，做有界重试
        for attempt in range(5):
            try:
                shutil.rmtree(RUN_ROOT)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(1.5)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    port = free_port()
    env = build_env(port)
    report: dict = {
        "harness": "opt13_multiworker_verify",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "broker_port": port,
            "broker": "fakeredis TcpFakeServer（真实 RESP 协议 + Lua；非本机 redis-server）",
            "database": str(DB_PATH),
            "workers": ["opt13a@127.0.0.1", "opt13b@127.0.0.1"],
            "constants": {"dispatch_ttl_s": DISPATCH_TTL, "step_lease_ttl_s": STEP_LEASE_TTL,
                          "visibility_timeout_s": VISIBILITY_TIMEOUT,
                          "probe_sleep_s": {"s2_a": S2_PROBE_SLEEP, "s3_a": PROBE_SLEEP,
                                            "s4_victim_b": S4_VICTIM_PROBE_SLEEP}},
            "agent_provider": "deterministic（tests-only；不调真实模型）",
        },
        "validation_status": {
            "broker_type": "fakeredis_simulated（真实单机 Redis 服务未验证：validated=false）",
            "real_model_calls": False,
            "prod_multi_host_fault_drill": "not_collected：需生产 Redis/多机环境人工演练（本 harness 仅单机进程级）",
        },
        "scenarios": {},
    }
    fake = None
    workers: dict = {}
    try:
        fake = start_proc([PY, "-m", "scripts.opt13_fake_redis", "--port", str(port)],
                          env, LOG_DIR / "fake_redis.log")
        if wait_log_text(fake["log"], "READY", timeout=30) is None:
            raise RuntimeError("fake redis 未就绪")
        init = run_child(["--mode", "init-db"], env, timeout=300)
        if init.returncode != 0:
            raise RuntimeError(f"init-db 失败 (rc={init.returncode}): {init.stderr[-2000:]}")

        if "s1" in scenarios:
            workers["a"] = start_worker("opt13a", port, probe_sleep=0)
            workers["b"] = start_worker("opt13b", port, probe_sleep=0)
            report["scenarios"]["s1"] = scenario_s1_smoke(port, env, [workers["a"], workers["b"]])

        if "s2" in scenarios:
            # S1 的 worker 无探针（链路毫秒级完成，RUNNING/持锁窗口不可观测）；
            # 重启 A 带短探针制造确定性锁竞争窗口，B 在重复投递后才启动（由场景内触发）。
            for name in ("b", "a"):
                if name in workers:
                    kill_tree(workers[name]["proc"])
            workers["a"] = start_worker("opt13a", port, probe_sleep=S2_PROBE_SLEEP)

            def _start_b_s2():
                workers["b"] = start_worker("opt13b", port, probe_sleep=0)
                return workers["b"]

            report["scenarios"]["s2"] = scenario_s2_double_dispatch(
                port, env, workers["a"], _start_b_s2, "T2-double")

        if "s3" in scenarios:
            # 只留 A（带慢执行探针），B 由场景内按需启动（S4 场景会自行清场重建）
            for name in ("b", "a"):
                if name in workers:
                    kill_tree(workers[name]["proc"])
            workers["a"] = start_worker("opt13a", port, probe_sleep=PROBE_SLEEP)

            def _start_b():
                workers["b"] = start_worker("opt13b", port, probe_sleep=0)
                return workers["b"]

            report["scenarios"]["s3"] = scenario_s3_crash_recovery(
                port, env, "T3-crash", workers["a"], _start_b)

        if "s4" in scenarios:
            # Worker 生命周期全部由场景管理：清场 → 只留带长探针的 B 作为确定性受害者
            # → 恢复期并行收敛（见 scenario_s4_bounded_longrun docstring）
            report["scenarios"]["s4"] = scenario_s4_bounded_longrun(port, env, workers)

        all_passed = all(s.get("checks", {}).get("all_passed") is True
                         for s in report["scenarios"].values())
        report["checks_all_passed"] = all_passed
    except Exception as exc:  # noqa: BLE001 环境级失败如实记录
        report["fatal_error"] = f"{type(exc).__name__}: {exc}"
        report["checks_all_passed"] = False
    finally:
        for w in workers.values():
            try:
                kill_tree(w["proc"])
                w["fh"].close()
            except Exception:  # noqa: BLE001
                pass
        if fake:
            kill_tree(fake["proc"])
            fake["fh"].close()

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"OPT-13 harness: {'ALL PASSED' if report.get('checks_all_passed') else 'FAILED'}")
    for key, sc in report["scenarios"].items():
        checks = sc.get("checks", {})
        passed = sum(1 for v in checks.values() if v is True)
        print(f"  {key} {sc['scenario']}: {passed}/{len(checks)} checks"
              f"{'' if checks.get('all_passed') else '  <-- 有失败项'}")
    if report.get("fatal_error"):
        print(f"  fatal: {report['fatal_error']}")
    print(f"report={out.resolve()}")
    return 0 if report.get("checks_all_passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
