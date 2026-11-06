"""运行时基础配置：数据库、队列、向量库与本地工作目录。

这层只负责把环境变量收拢成统一入口，不参与业务判断。
启动时若存在 `.env`（默认 <backend>/.env，或用 ENV_FILE 指定），会把其中键加载进 os.environ（仅当未设置时），
便于把 LLM / Embedding / DB 等凭据放本地 git-ignored 文件，而不提交进代码/示例。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file() -> None:
    """零依赖 dotenv：把 `.env` 中未设置的键写入 os.environ（不覆盖已有环境变量）。"""
    env_file = os.getenv("ENV_FILE")
    if env_file:
        path = Path(env_file).expanduser()
    else:
        path = Path(__file__).resolve().parents[2] / ".env"  # <backend>/.env
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().strip("\"'")
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


_load_env_file()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float, minimum: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _auth_mode() -> str:
    """OPT-14 身份边界：当前仅支持 local（内建账号体系）。

    OIDC / MFA 仅完成边界声明（docs/project-development/126），未实现前
    配置非 local 值必须 fail-closed 拒绝启动，绝不静默降级到本地认证。
    """
    value = (_env("AUTH_MODE", "local") or "local").lower()
    if value != "local":
        raise RuntimeError(
            f"AUTH_MODE={value} 未实现（OIDC/MFA 规划中）：fail-closed 拒绝启动，"
            "绝不静默降级到 local 认证。见 docs/project-development/126。"
        )
    return value


def _git_host_provider() -> str:
    """OPT-15 代码托管适配边界：当前仅支持 gitee（GitHub/GitLab 规划中）。

    未实现前配置非 gitee 值必须 fail-closed 拒绝启动：绝不静默回退 gitee，
    避免把 Issue/PR 写到非预期的托管平台。见 docs/project-development/127。
    """
    value = (_env("GIT_HOST_PROVIDER", "gitee") or "gitee").lower()
    if value != "gitee":
        raise RuntimeError(
            f"GIT_HOST_PROVIDER={value} 未实现（GitHub/GitLab 适配规划中）：fail-closed 拒绝启动，"
            "绝不静默回退 gitee 托管。见 docs/project-development/127。"
        )
    return value


# 必须强制 MySQL / 严格运行期的环境（团队版、生产、企业版）
# 完整强校验（JWT/恢复码/Celery/CORS/Milvus 等）在部署侧 backend-entrypoint.sh 统一强制；
# 本层与 team/production 保持同等口径：强制 MySQL + celery_required。
_PROD_ENVS = frozenset({"team", "production", "prod", "enterprise"})


@dataclass(frozen=True)
class RuntimeSettings:
    database_url: str
    celery_broker_url: str
    celery_result_backend: str
    milvus_uri: str
    milvus_token: str
    data_dir: str
    app_env: str
    use_celery: bool
    use_milvus: bool
    use_sqlalchemy: bool
    use_git_clone_index: bool
    rag_mock_fallback: bool
    rag_retrieval_mode: str
    rag_query_expansion: bool
    rag_rerank_enabled: bool
    rag_rerank_max_candidates: int
    # 企业版 Hybrid 检索各阶段候选数（集中在后端配置层，前端不写死）
    rag_vector_top_k: int
    rag_bm25_top_k: int
    rag_final_top_k: int
    # E3-02 Query Rewrite / 多路 Expansion（安全默认：开、LLM 关、模型不可用确定性回退）
    rag_query_rewrite_enabled: bool
    rag_query_max_variants: int
    rag_query_llm: bool
    rag_query_variant_decay: float
    # E3-03 Rerank（安全默认：关；provider 默认 heuristic；模型不可用/超时稳定回退）
    rag_rerank_provider: str
    rag_rerank_top_n: int
    rag_rerank_timeout_seconds: int
    # E3-07 精确符号定义优先：query token 与 chunk.symbol_name 完全一致时对 bm25/RRF 分数加权，
    # 让“函数定义块”排到“仅引用该符号的调用块”之前，提升代码检索 precision/MRR（不越权、不回退过滤）。
    rag_symbol_boost: float
    # OPT-10 Hybrid 融合权重（RRF 两路候选权重；默认 1.0/1.0 = 与历史公式逐位一致；
    # 0.0 = 禁用该路（不查询、不产生候选）；负值钳制到 0）
    rag_hybrid_vector_weight: float
    rag_hybrid_bm25_weight: float
    # E3-09 Cross-Encoder 精排（安全默认：关 / 空模型 / cpu；模型不可用/超时稳定回退 heuristic，不冒充 CE）
    rag_cross_encoder_enabled: bool
    rag_cross_encoder_model: str
    rag_cross_encoder_device: str
    rag_cross_encoder_top_n: int
    rag_cross_encoder_batch_size: int
    rag_cross_encoder_timeout_seconds: int
    rag_cross_encoder_max_candidates: int
    rag_cross_encoder_fallback: str
    # DB 连接池：压测（多 worker + HTTP 并发建任务）下默认 QueuePool size=5/overflow=10 会顶满报 timeout。
    # 这些值可由环境变量覆盖，默认值面向 2~4 worker 的团队版部署。
    db_pool_size: int
    db_max_overflow: int
    db_pool_recycle: int
    db_pool_timeout: int
    db_pool_pre_ping: bool
    # OPT-14 审计保留（安全默认：0 = 永久保留；低于 min_days 需显式放行；单次删除有上限）
    audit_retention_days: int
    audit_retention_min_days: int
    audit_retention_max_delete: int
    # OPT-14 身份边界：仅支持 local；非 local（OIDC/MFA 未实现）→ fail-closed 拒绝启动
    auth_mode: str
    # OPT-15 代码托管适配：仅支持 gitee；非 gitee（GitHub/GitLab 未实现）→ fail-closed 拒绝启动
    git_host_provider: str

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir or ".data").expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def milvus_path(self) -> str:
        return self.milvus_uri or str(self.data_path / "milvus" / "code_index.db")

    def celery_required(self) -> bool:
        """team / production / enterprise 必须是真实 Celery+Redis 主链路，不允许静默 eager。"""
        return self.app_env in _PROD_ENVS


def load_runtime_settings() -> RuntimeSettings:
    data_dir = _env("DATA_DIR", ".data")
    app_env = _env("APP_ENV", "local").lower()
    use_sqlalchemy = _env_bool("USE_SQLALCHEMY", True)
    # 默认库口径严格按运行态分离：
    # - local / test：允许默认 SQLite（开发/测试降级）；
    # - team / production / prod / enterprise：必须显式指向 MySQL，绝不静默回退 sqlite。
    prod = app_env in _PROD_ENVS
    database_url = _env("DATABASE_URL")
    if prod and use_sqlalchemy and (not database_url or database_url.startswith("sqlite")):
        raise RuntimeError(
            f"APP_ENV={app_env or 'local'}：生产/团队版/企业版必须配置 DATABASE_URL 指向 MySQL "
            f"(如 mysql+pymysql://user:pass@host:3306/db?charset=utf8mb4)，不允许默认/回退 SQLite。"
        )
    if not database_url:
        database_url = f"sqlite:///{(Path(data_dir) / 'app.db').as_posix()}" if not prod else database_url
    return RuntimeSettings(
        database_url=database_url,
        celery_broker_url=_env("CELERY_BROKER_URL"),
        celery_result_backend=_env("CELERY_RESULT_BACKEND"),
        milvus_uri=_env("MILVUS_URI"),
        milvus_token=_env("MILVUS_TOKEN"),
        data_dir=data_dir,
        app_env=app_env,
        use_celery=_env_bool("USE_CELERY", False),
        use_milvus=_env_bool("USE_MILVUS", True),
        use_sqlalchemy=use_sqlalchemy,
        use_git_clone_index=_env_bool("USE_GIT_CLONE_INDEX", True),
        rag_mock_fallback=_env_bool("RAG_MOCK_FALLBACK", True),
        rag_retrieval_mode=_env("RAG_RETRIEVAL_MODE", "vector").lower(),
        rag_query_expansion=_env_bool("RAG_QUERY_EXPANSION", False),
        rag_rerank_max_candidates=max(1, min(_env_int("RAG_RERANK_MAX_CANDIDATES", 20), 50)),
        rag_vector_top_k=max(1, _env_int("RAG_VECTOR_TOP_K", 20)),
        rag_bm25_top_k=max(1, _env_int("RAG_BM25_TOP_K", 10)),
        rag_final_top_k=max(1, min(_env_int("RAG_FINAL_TOP_K", 8), 50)),
        rag_query_rewrite_enabled=_env_bool("RAG_QUERY_REWRITE_ENABLED", True),
        rag_query_max_variants=max(1, min(_env_int("RAG_QUERY_MAX_VARIANTS", 3), 8)),
        rag_query_llm=_env_bool("RAG_QUERY_LLM", False),
        rag_query_variant_decay=float(_env("RAG_QUERY_VARIANT_DECAY", "0.85")),
        rag_rerank_enabled=_env_bool("RAG_RERANK_ENABLED", False),
        rag_rerank_provider=(_env("RAG_RERANK_PROVIDER", "heuristic") or "heuristic").lower(),
        rag_rerank_top_n=max(1, min(_env_int("RAG_RERANK_TOP_N", 8), 50)),
        rag_rerank_timeout_seconds=max(1, _env_int("RAG_RERANK_TIMEOUT_SECONDS", 5)),
        # E3-07 精确符号定义 boost；安全默认开启（属 rank 排序偏好，非安全开关），置 0.0 关闭
        rag_symbol_boost=max(0.0, float(_env("RAG_SYMBOL_BOOST", "1.0"))),
        # OPT-10 Hybrid RRF 权重（默认 1.0/1.0 保持历史行为；置 0.0 即禁用该路，供消融对比）
        rag_hybrid_vector_weight=_env_float("RAG_HYBRID_VECTOR_WEIGHT", 1.0, 0.0),
        rag_hybrid_bm25_weight=_env_float("RAG_HYBRID_BM25_WEIGHT", 1.0, 0.0),
        # E3-09 Cross-Encoder（安全默认全关/空模型；本地私有运行时优先；不可用→heuristic fallback）
        rag_cross_encoder_enabled=_env_bool("RAG_CROSS_ENCODER_ENABLED", False),
        rag_cross_encoder_model=(_env("RAG_CROSS_ENCODER_MODEL", "") or "").strip(),
        rag_cross_encoder_device=(_env("RAG_CROSS_ENCODER_DEVICE", "cpu") or "cpu").strip(),
        rag_cross_encoder_top_n=max(1, min(_env_int("RAG_CROSS_ENCODER_TOP_N", 16), 50)),
        rag_cross_encoder_batch_size=max(1, _env_int("RAG_CROSS_ENCODER_BATCH_SIZE", 8)),
        rag_cross_encoder_timeout_seconds=max(1, _env_int("RAG_CROSS_ENCODER_TIMEOUT_SECONDS", 5)),
        rag_cross_encoder_max_candidates=max(1, min(_env_int("RAG_CROSS_ENCODER_MAX_CANDIDATES", 32), 64)),
        rag_cross_encoder_fallback=(_env("RAG_CROSS_ENCODER_FALLBACK", "heuristic") or "heuristic").lower(),
        db_pool_size=_env_int("DB_POOL_SIZE", 10),
        db_max_overflow=_env_int("DB_MAX_OVERFLOW", 20),
        db_pool_recycle=_env_int("DB_POOL_RECYCLE", 1800),
        db_pool_timeout=_env_int("DB_POOL_TIMEOUT", 30),
        db_pool_pre_ping=_env_bool("DB_POOL_PRE_PING", True),
        # OPT-14 审计保留：0=永久保留（默认）；min_days 是防误删下限（默认 30）；max_delete 单次上限
        audit_retention_days=max(0, _env_int("AUDIT_RETENTION_DAYS", 0)),
        audit_retention_min_days=max(1, _env_int("AUDIT_RETENTION_MIN_DAYS", 30)),
        audit_retention_max_delete=max(1, _env_int("AUDIT_RETENTION_MAX_DELETE", 10000)),
        auth_mode=_auth_mode(),
        git_host_provider=_git_host_provider(),
    )
