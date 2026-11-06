"""pytest 公共夹具：每个测试前重置内存态，并提供已登录的 TestClient。

OPT-05 测试环境隔离（必须先于任何 app.* 导入执行）：
1. 屏蔽本地 backend/.env：把 ENV_FILE 指向永不存在的哨兵路径，本地凭据（LLM/Gitee Token）、
   生产地址（Milvus/MySQL）、Redis broker 不得进入测试进程；CI 与本地同口径、零机密。
   （celery_app 在导入时绑定 broker，若 .env 生效会把整套测试悄悄变成「依赖本机 Redis」。）
2. 清空会冻结导入期行为的外部服务变量，默认套件 = 内存态 + Celery eager + 无外网；
   真正需要外部服务的用例用 @pytest.mark.requires_redis 显式标记，服务缺失时由
   pytest_collection_modifyitems 归类为 skip（环境缺失，非代码失败）。
"""
from __future__ import annotations

import os
from pathlib import Path

_HERMETIC_ENV_FILE = Path(__file__).resolve().with_name(".env.hermetic-nonexistent")
os.environ["ENV_FILE"] = str(_HERMETIC_ENV_FILE)
# pymilvus.settings 在导入时无条件调用 load_dotenv()，会无视 ENV_FILE 重新读 backend/.env。
# 必须在导入 app 之前禁用它，否则本地 MySQL/Milvus/凭据会泄漏进测试进程（OPT-05 隔离缺口）。
try:
    import dotenv as _dotenv

    _dotenv.load_dotenv = lambda *args, **kwargs: False  # noqa: E731
except ImportError:  # pragma: no cover - 依赖缺失时保持原有行为
    pass
for _leaky_key in (
    "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "REDIS_URL",
    "DATABASE_URL", "MILVUS_URI", "MILVUS_TOKEN",
):
    os.environ.pop(_leaky_key, None)

import pytest
from fastapi.testclient import TestClient

from app import main
from app.core import distributed_lock
from service_probe import PROBE_TIMEOUT_SECONDS, redis_broker_url, redis_reachable, target_label


def pytest_collection_modifyitems(config, items):
    """requires_redis：服务不可达 → 显式 skip 并标注「环境缺失」（区别于代码失败）。"""
    marked = [item for item in items if item.get_closest_marker("requires_redis")]
    if not marked:
        return
    url = redis_broker_url()
    if redis_reachable(url):
        return
    reason = (
        f"requires_redis: broker 不可达（{target_label(url)}，TCP 探测超时 {PROBE_TIMEOUT_SECONDS}s）——"
        "环境缺失分类 → skip（非代码失败）；启动本地 Redis 或设置 TEST_REDIS_URL 后重跑"
    )
    skip_marker = pytest.mark.skip(reason=reason)
    for item in marked:
        item.add_marker(skip_marker)


@pytest.fixture(autouse=True)
def reset_in_memory_state():
    main.TASKS.clear()
    main.AUDIT_EVENTS.clear()
    main.TEAM_REQUESTS.clear()
    main.REPOSITORY_INDEXES.clear()
    main.LATEST_INDEX_BY_REPO.clear()
    main.CODE_INDEX_SNAPSHOTS.clear()
    main.CODE_CHUNKS.clear()
    main.RETRIEVAL_RECORDS.clear()
    main.CODE_VECTOR_STORE.clear()
    main.COMMENTS.clear()
    main.NOTIFICATIONS.clear()
    main.HISTORICAL_SUMMARIES.clear()
    main.HISTORICAL_INDEXES.clear()
    main.HISTORY_VECTOR_STORE.clear()
    main._AGENT_ENQUEUED.clear()
    # 重置分布式锁后端：清掉上个用例注入的 fake/自定义后端，恢复按环境变量自动选择
    distributed_lock._PROCESS_FALLBACK.reset()
    distributed_lock.set_lock_backend(None)
    # 企业版 E1-01：重置租户/组织/成员内存真源（各用例独立，隔离测试不受污染）
    from app.tenancy import store as tenancy_store

    tenancy_store.reset_store()
    # 企业版 E2-01：重置审批申请内存真源
    from app.approvals import store as approvals_store

    approvals_store.reset_store()
    # 企业版 E4-01：重置 Runtime 任务/租约/checkpoint 内存真源
    from app.enterprise_runtime import store as runtime_store
    from app.enterprise_runtime.checkpoint import CheckpointStore

    runtime_store.reset_store()
    CheckpointStore().reset()
    # 企业版 E4-02：重置 AgentRun / ArtifactStore 内存真源（各用例独立）
    from app.enterprise_runtime.artifacts import runs as agent_runs_store
    from app.enterprise_runtime.artifacts import store as artifacts_store

    artifacts_store.reset_store()
    agent_runs_store.reset_store()
    # 企业版 OPT-07：重置可观测性 span 内存真源（各用例独立）
    from app.enterprise_runtime import observability as observability_store

    observability_store.reset_store()
    # 企业版 OPT-08：重置在线评测 run 内存真源（各用例独立）
    from app.enterprise_rag import eval_store as rag_eval_store

    rag_eval_store.reset_store()
    # 企业版 OPT-09 / FIX-02：重置索引生命周期记录内存缓存（DB 真源由临时库隔离）
    from app.enterprise_rag import index_lifecycle as rag_index_lifecycle

    rag_index_lifecycle.reset_store()
    # FIX-04：每个用例恢复真实 UTC 时钟（需要冻结时钟的用例自行 set/reset）
    from app.core import clock as core_clock

    core_clock.reset_now_provider()
    for repository in main.REPOSITORIES.values():
        repository.risk_threshold = "medium"
    yield
    distributed_lock.set_lock_backend(None)


@pytest.fixture(autouse=True)
def disable_real_model_clients(monkeypatch):
    """默认屏蔽真实模型调用：无 Key 时整套测试走 mock/token-hash 兜底，避免命中本机环境变量。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    # 测试默认不连真实 Redis：需要验证分布式锁后端的用例自行注入 fake backend
    monkeypatch.delenv("REDIS_URL", raising=False)
    # OPT-05：默认套件不连本机 Celery broker（celery_app 已按隔离环境导入为 eager）；
    # 需要真实 broker 的用例走 @pytest.mark.requires_redis + TEST_REDIS_URL 显式 opt-in
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("CELERY_RESULT_BACKEND", raising=False)
    # P0：业务持久化走写入穿透缓存；测试默认关闭（不建库/不落盘/不重启恢复），
    # 保持既有用例在纯内存环境隔离运行。持久化用例自行开启并复位引擎缓存。
    monkeypatch.setenv("USE_SQLALCHEMY", "false")
    distributed_lock.set_lock_backend(None)
    monkeypatch.setenv("MODEL_MOCK_FALLBACK", "true")
    yield


@pytest.fixture()
def client():
    test_client = TestClient(main.app)
    login = test_client.post(
        "/api/auth/login",
        json={"account": "developer@company.com", "password": "Dev123!"},
    )
    assert login.status_code == 200
    return test_client


def _prepare_sqlite(tmp_path, monkeypatch, name: str) -> str:
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / name).as_posix()}")
    monkeypatch.setenv("PERSIST_STRICT", "false")
    from app.db import session as db_session
    from app.models.orm import Base

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    Base.metadata.create_all(db_session.get_engine())
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def _clear_sqlite_caches() -> None:
    from app.db import session as db_session

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()


@pytest.fixture()
def persistent_db(tmp_path, monkeypatch):
    """真实 SQLite + USE_SQLALCHEMY=true（业务与共享 store 都持久化）。"""
    monkeypatch.delenv("ENTERPRISE_SHARED_PERSISTENCE", raising=False)
    url = _prepare_sqlite(tmp_path, monkeypatch, "persistent.db")
    yield url
    _clear_sqlite_caches()


@pytest.fixture()
def shared_db(tmp_path, monkeypatch):
    """业务运行时保持内存态，仅评测 run / trace / 审计等共享状态落 SQLite。"""
    monkeypatch.setenv("ENTERPRISE_SHARED_PERSISTENCE", "true")
    url = _prepare_sqlite(tmp_path, monkeypatch, "shared.db")
    # 建表之后再关闭业务持久化：共享 store 显式走 DB，业务读路径保持内存态。
    monkeypatch.setenv("USE_SQLALCHEMY", "false")
    yield url
    _clear_sqlite_caches()
