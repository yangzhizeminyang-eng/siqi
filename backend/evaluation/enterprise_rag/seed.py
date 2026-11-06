"""E3-05 基线评测种子：脱敏合成“支付/退款/订单/网关”demo 仓库（非真实商业代码），供可复现本地基线。

- 仅生成通用、可复现、无真实商业敏感的占位代码片段，用于驱动 enterprise_rag_search 的真实 BM25 检索。
- 种子内容不含 token / password / jwt / cookie / 恢复码；不依赖真实 Milvus（内存态）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import main

BASELINE_COMMIT = "e3baseline0000000000000000000000000000"
BASELINE_REPO = "repo_demo_payment"
BASELINE_PROJECT = "proj_demo_payment"
BASELINE_INDEX = "idx_demo_payment"
CROSS_REPO = "repo_demo_other"
CROSS_PROJECT = "proj_demo_other"
CROSS_TENANT = "tenant_b"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# (chunk_id, symbol, file_path, content)：通用占位代码，token 用于被 query 命中
CHUNKS: list[tuple[str, str, str, str]] = [
    ("p_refund_handle", "handle_refund", "src/payment/refund.py",
     "def handle_refund(refund_id):\n    if not validate(refund_id):\n        raise ERR_401\n    return refund(refund_id)"),
    ("p_refund_retry", "retry_refund", "src/payment/refund.py",
     "def retry_refund(refund_id):\n    return payment.callback.retry.publish(refund_id)"),
    ("p_callback", "handle_payment_callback", "src/payment/callback.py",
     "def handle_payment_callback(payload):\n    state = load_order(payload.order_id)\n    return transition(state, 'paid')"),
    ("p_callback_retry", "payment_callback_retry", "src/payment/retry.py",
     "def payment_callback_retry(msg):\n    return schedule_retry(backoff=30)"),
    ("api_orders_create", "create_order", "src/api/orders.py",
     "def create_order(body):\n    return http_post('/api/orders', body)"),
    ("api_orders_get", "get_order", "src/api/orders.py",
     "def get_order(order_id):\n    return http_get(f'/api/orders/{order_id}')"),
    ("cfg_timeout", "PAYMENT_TIMEOUT", "src/payment/config.py",
     "PAYMENT_TIMEOUT = 30\nRETRY_BACKOFF = 5"),
    ("gw_timeout", "gateway_timeout", "src/gateway/timeout.py",
     "def gateway_timeout(deadline):\n    return enforce(deadline, default=5)"),
    ("ledger", "post_ledger", "src/payment/ledger.py",
     "def post_ledger(txn):\n    return append(txn.amount, txn.currency)"),
    ("deposit", "resolve_deposit", "src/payment/deposit.py",
     "def resolve_deposit(deposit_id):\n    return mark_settled(deposit_id)"),
    ("auth_scope", "check_scope", "src/auth/scope.py",
     "def check_scope(principal, scope):\n    return principal.has(scope)"),
    ("webhook_sign", "sign_webhook", "src/webhook/signing.py",
     "def sign_webhook(body):\n    return hmac_sha256(body, key_ref)"),
    ("ops_health", "health_check", "src/ops/health.py",
     "def health_check():\n    return {'status': 'ok', 'checks': []}"),
    ("db_migration", "apply_migration", "src/db/migration.py",
     "def apply_migration(version):\n    return migrate(version)"),
]

# 跨租户仓库（tenant_b），供 403 边界样本
OTHER_CHUNKS: list[tuple[str, str, str, str]] = [
    ("other_c1", "other_private_symbol", "src/other/private.py",
     "def other_private_symbol():\n    return 'other-tenant-data'"),
]


def seed_repo(repo_id: str, project_id: str, tenant_id: str, index_id: str,
              commit: str, chunks: list[tuple[str, str, str, str]]) -> None:
    main.PROJECTS[project_id] = main.ProjectRecord(
        id=project_id, tenant_id=tenant_id, name=project_id, description="",
        created_at=_now(), updated_at=_now())
    main.REPOSITORIES[repo_id] = main.RepositoryConfigRecord(
        id=repo_id, tenant_id=tenant_id, project_id=project_id, name=repo_id,
        url=f"https://gitee.com/demo/{repo_id}.git", default_branch="main",
        test_command="pytest tests/ -q", command_whitelist=["pytest"],
        approval_mode="single", risk_threshold="medium", max_files=10, max_retries=1,
        blocked_paths=[], created_at=_now(), updated_at=_now())
    index = main.RepositoryIndexRecord(
        id=index_id, tenant_id=tenant_id, repository_id=repo_id, project_id=project_id,
        base_branch="main", commit_sha=commit, status="COMPLETED",
        requested_by="seed", requested_by_name="seed", requested_at=_now(),
        created_at=_now(), updated_at=_now())
    main.REPOSITORY_INDEXES[index.id] = index
    main.LATEST_INDEX_BY_REPO[repo_id] = index.id
    for chunk_id, symbol, file_path, content in chunks:
        main.CODE_CHUNKS[chunk_id] = main.CodeChunkRecord(
            chunk_id=chunk_id, repository_id=repo_id, index_id=index_id,
            file_path=file_path, chunk_index=0, content=content,
            language="python", symbol_name=symbol, start_line=1, end_line=20,
            commit_sha=commit, milvus_collection="", milvus_pk="", checksum="demo")
    main.CODE_INDEX_SNAPSHOTS[index_id] = [main.CODE_CHUNKS[c[0]] for c in chunks]
    # FIX-02：隔离种子也是「可检索索引」，必须登记 ready 生命周期指纹；
    # 否则 fail-closed 守卫会把隔离评测的向量模式全部拒绝。
    from app.enterprise_rag import index_lifecycle

    index_lifecycle.register_build(
        index_id=index_id, tenant_id=tenant_id, repository_id=repo_id,
        project_id=project_id, base_branch="main", commit_sha=commit,
        collection_name=index.milvus_collection or "",
    )
    index_lifecycle.mark_ready(index, chunk_count=len(chunks))


def seed_baseline() -> None:
    """种入基线（当前租户）demo 仓库 + 跨租户仓库（403 样本）。"""
    seed_repo(BASELINE_REPO, BASELINE_PROJECT, "tenant_default",
              BASELINE_INDEX, BASELINE_COMMIT, CHUNKS)
    seed_repo(CROSS_REPO, CROSS_PROJECT, CROSS_TENANT,
              "idx_demo_other", BASELINE_COMMIT, OTHER_CHUNKS)