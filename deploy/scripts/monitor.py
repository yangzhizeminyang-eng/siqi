#!/usr/bin/env python3
"""E6-01 P4 轻量监控+告警（真实运行）：轮询 /api/enterprise/ops/metrics 与健康端点，
按阈值判定并写带时间戳的 ALERT/RECOVERY 到日志（可选 webhook 通知）。不含密钥。"""
import os, sys, time, json, datetime, urllib.request, subprocess, collections

BASE = os.environ.get("MONITOR_BASE", "http://127.0.0.1:8081")      # 前端(经其访问 backend)
API  = os.environ.get("MONITOR_API", f"{BASE}/api/enterprise/ops/metrics")
HEALTH = os.environ.get("MONITOR_HEALTH", f"{BASE}/health")
HEALTH_API = os.environ.get("MONITOR_HEALTH_API", f"{BASE}/api/health")
LOG = os.environ.get("MONITOR_LOG", "/var/log/agent-monitor/alerts.log")
INTERVAL = int(os.environ.get("MONITOR_INTERVAL", "10"))
WEBHOOK = os.environ.get("MONITOR_WEBHOOK", "")                     # 可选通知渠道；空=仅日志
CERT = os.environ.get("E6_CERT", "/etc/nginx/ssl/agentforge.ltd_bundle.crt")
FIVEXX_RATE = float(os.environ.get("MONITOR_5XX_RATE", "0.05"))
DEP_DOWN_SEC = int(os.environ.get("MONITOR_DEP_DOWN_SEC", "30"))    # 依赖连续 down 阈值
CERT_WARN_DAYS = int(os.environ.get("MONITOR_CERT_DAYS", "30"))
DISK_WARN_PCT = int(os.environ.get("MONITOR_DISK_PCT", "20"))

os.makedirs(os.path.dirname(LOG), exist_ok=True)
def now(): return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def w(line):
    with open(LOG, "a", encoding="utf-8") as f: f.write(line+"\n")
    if WEBHOOK:
        try:
            req = urllib.request.Request(WEBHOOK, data=json.dumps({"text": line}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception: pass

def get(url, t=6):
    try:
        return urllib.request.urlopen(url, timeout=t).read().decode("utf-8", "ignore")
    except Exception as e:
        return f"ERR {type(e).__name__}"

def parse_deps(body):
    """从 /api/health JSON 解析依赖状态（metrics 缺 db/redis/milvus 时用此兜底）。"""
    import json as _j
    deps = {}
    try:
        d = _j.loads(body).get("data", {}).get("dependencies", {})
        for k in ("db", "redis", "milvus"):
            v = d.get(k) or {}
            # "skipped"（app-only 下 milvus/worker 有意未启用）按非故障处理，与 metrics.py st() 口径一致；
            # 否则 /api/health "skipped"→0 会压过 metrics "skipped"→1，导致 app-only 下 milvus ALERT 永久无 RECOVERY。
            deps[f"agent_dependency_{k}"] = "1" if v.get("status") in ("ok", "degraded", "skipped") else "0"
    except Exception:
        pass
    return deps

def disk_pct():
    try:
        out = subprocess.run(["df", "-P", "/"], capture_output=True, text=True).stdout
        return int(out.splitlines()[1].split()[4].rstrip("%"))
    except Exception: return -1

def cert_days():
    try:
        out = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in", CERT],
                             capture_output=True, text=True).stdout
        d = datetime.datetime.strptime(out.split("notAfter=")[1].strip(), "%b %d %H:%M:%S %Y %Z")
        return (d.replace(tzinfo=datetime.timezone.utc) - datetime.datetime.now(datetime.timezone.utc)).days
    except Exception: return -1

# 状态表：metric -> (活跃报警时长, 已告警)
state = collections.defaultdict(lambda: [0, False])
def check(metric, ok, sev="P2", detail=""):
    if ok:
        if state[metric][1]:
            w(f"[RECOVERY {now()}] {metric} sev={sev} detail={detail}")
            state[metric] = [0, False]
        else:
            state[metric][0] = 0
    else:
        state[metric][0] += INTERVAL
        if not state[metric][1] and state[metric][0] >= DEP_DOWN_SEC:
            w(f"[ALERT {now()}] {metric} sev={sev} duration={state[metric][0]}s detail={detail}")
            state[metric][1] = True

print(f"[monitor] start base={BASE} interval={INTERVAL}s log={LOG} webhook={'on' if WEBHOOK else 'off'}", flush=True)
while True:
    body = get(API)
    h = get(HEALTH)
    # 后端健康
    check("backend.health", "ok" in h or "200" in h, "P1", f"health={h[:40]}")
    # 依赖：优先 /api/health JSON（metrics 的 db/redis/milvus 行可能缺失，见 E6 §22 缺陷记录）
    deps = parse_deps(get(HEALTH_API))
    for line in body.splitlines():
        for key in ("agent_dependency_db", "agent_dependency_redis", "agent_dependency_milvus", "agent_dependency_worker", "agent_dependency_cert_days_left"):
            if line.startswith(key):
                deps.setdefault(key, line.split()[-1])
    for k in ("agent_dependency_db", "agent_dependency_redis", "agent_dependency_milvus"):
        v = deps.get(k)
        check(k, v == "1", "P1", f"value={v}")
    # worker：仅当显式声明存在 worker（MONITOR_CHECK_WORKER=1，如 multi-replica/drill）才告警，
    # 避免 app-only（有意无 worker）对 worker=0 误报。
    if os.environ.get("MONITOR_CHECK_WORKER") == "1":
        wv = deps.get("agent_dependency_worker")
        check("agent_dependency_worker", wv == "1", "P1", f"value={wv}")
    # 5xx 率
    try:
        r5 = float([l.split()[-1] for l in body.splitlines() if l.startswith("agent_http_5xx_rate")][0])
        check("http.5xx_rate", r5 <= FIVEXX_RATE, "P2", f"rate={r5}")
    except Exception: pass
    # 证书天数
    cd = cert_days()
    if cd >= 0:
        check("cert.days", cd >= CERT_WARN_DAYS, "P2", f"days={cd}")
    # 磁盘
    dp = disk_pct()
    if dp >= 0:
        check("disk.pct", dp >= DISK_WARN_PCT, "P2", f"pct={dp}")
    time.sleep(INTERVAL)
