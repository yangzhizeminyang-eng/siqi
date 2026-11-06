"""OPT-13 harness：Worker 启动入口（celery -A scripts.opt13_worker_boot:celery_app worker ...）。

职责：
1. 复用生产 celery_app（app.workers.celery_app），不改任何生产配置；
2. 仅当 `OPT13_VISIBILITY_TIMEOUT` > 0 时覆盖 `broker_transport_options.visibility_timeout`：
   acks_late 消息在持有 Worker 崩溃后按该窗口被 broker 恢复重投（S3/S4 的真实重投驱动）；
3. 导入慢执行探针（scripts.opt13_probe），按环境变量决定是否启用；
4. 导入恢复路径取证/互斥锁加固模块（scripts.opt13_trace_restore）：以无 Lua 的 CAS 释放
   替换 kombu unacked_mutex 释放（规避 fakeredis 的 NoScriptError 残锁缺陷），并按环境变量
   输出恢复扫描取证日志。
"""

from __future__ import annotations

import os

from app.workers.celery_app import celery_app

_vis = int(os.getenv("OPT13_VISIBILITY_TIMEOUT", "0") or 0)
if _vis > 0:
    opts = dict(celery_app.conf.broker_transport_options or {})
    opts["visibility_timeout"] = _vis
    celery_app.conf.broker_transport_options = opts

import scripts.opt13_trace_restore  # noqa: E402,F401 侧效应模块：恢复扫描取证 + unacked_mutex 释放加固
import scripts.opt13_probe  # noqa: E402,F401 侧效应模块：按环境变量挂载慢执行探针
