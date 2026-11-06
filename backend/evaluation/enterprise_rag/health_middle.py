"""E3-06-CLOUD-HEALTH-MIDDLE：真实 health-middle 仓库评测与本地质量基线。

- 从真实 Gitee 仓库 health-middle（clone 到本地）解析真实代码 → 建真实 COMPLETED 索引（内存态，
  供 enterprise_rag_search 检索）→ 生成 >=50 条真实评测集（source_provenance，不含代码正文/token）→
  bm25 / hybrid 本地评测（validation_status=local_only）。
- 本机无真实 Milvus：vector 模式与 validation_status=verified 一律 blocked，绝不把 fallback 标成 verified。
- 真实凭据/Token 不从本模块产出；GITEE_TOKEN 只在 git clone 时经环境变量使用，绝不落盘/落日志/入库。
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app import main
from app.enterprise_rag.evaluation import EvaluationCase, evaluate_dataset, load_cases
from app.core import model_config, runtime_config
from app.retrieval import llamaindex_backend as rag_backend
from app.services import embedding_service

REPO_URL = "https://gitee.com/yang-zhizemin/health-middle.git"
REPO_NAME = "health-middle"
REPO_ID = "repo_health_middle"
PROJECT_ID = "proj_health_middle"
# 2026-09 真实 HEAD（git ls-remote 取得）
REAL_COMMIT = "73e7e5497addc74f67fe49a875d7afdd8d8c190b"
INDEX_ID = "idx_health_middle_73e7e54"

EVAL_DIR = Path(__file__).resolve().parent
EVAL_SET = EVAL_DIR / "e3_06_health_middle.jsonl"
REPORTS_DIR = EVAL_DIR / "reports"

# 顶层 def/class 块切分
_TOP_BLOCK = re.compile(r"^(?P<indent> *)def |^(?P<kind>class )", re.MULTILINE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def iter_python_files(repo_root: Path):
    for path in sorted((repo_root / "backend" / "app").rglob("*.py")):
        yield path


def _split_top_blocks(text: str) -> list[tuple[str, str, int, int]]:
    """按顶层 def/class 切分，返回 [(symbol, block, start_line, end_line)]。"""
    lines = text.splitlines()
    starts: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        m = _TOP_BLOCK.match(line)
        if m and not line[: m.start()].strip():  # 缩进为 0 的顶层定义
            name = line[m.end():].split("(")[0].split(":")[0].strip()
            starts.append((idx, name))
    blocks: list[tuple[str, str, int, int]] = []
    for i, (start, name) in enumerate(starts):
        end = (starts[i + 1][0] - 1) if i + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start - 1:end])
        blocks.append((name, body, start, end))
    return blocks


def resolve_repo_commit(repo_root: Path) -> str:
    """从真实工作树读取当前 commit，避免用旧常量伪造索引版本。"""
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    commit = proc.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError(f"无法解析 health-middle commit: {commit!r}")
    return commit


def index_id_for_commit(commit_sha: str) -> str:
    return f"idx_health_middle_{commit_sha[:7]}"


def parse_repo_chunks(repo_root: Path, *, commit_sha: str, index_id: str, collection_name: str):
    """把真实后端 Python 文件切分为真实 CodeChunkRecord（file_path/symbol/content/commit）。"""
    chunks: list[main.CodeChunkRecord] = []
    for path in iter_python_files(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks = _split_top_blocks(text)
        if not blocks:
            blocks = [("__module__", text, 1, max(1, len(text.splitlines())))]
        for idx, (symbol, block, start, end) in enumerate(blocks):
            chunk_id = f"hm_{commit_sha[:8]}_{rel.replace('/', '_')}_{idx}"
            chunks.append(main.CodeChunkRecord(
                chunk_id=chunk_id, repository_id=REPO_ID, index_id=index_id,
                file_path=rel, chunk_index=idx, content=block[:4000],
                language="python", symbol_name=symbol or None, start_line=start, end_line=end,
                commit_sha=commit_sha, milvus_collection=collection_name, milvus_pk=chunk_id,
                checksum=f"hmc{idx}",
            ))
    return chunks


def build_real_index(
    repo_root: Path,
    *,
    tenant_id: str = "tenant_default",
    persist_vector: bool | None = None,
    require_milvus: bool = False,
):
    """建立真实 health-middle 索引；USE_MILVUS=true 时同步写入独立企业 collection。"""
    repo_root = Path(repo_root).resolve()
    commit_sha = resolve_repo_commit(repo_root)
    index_id = index_id_for_commit(commit_sha)
    collection_name = main.index_collection_name(index_id)
    settings = runtime_config.load_runtime_settings()
    persist_vector = settings.use_milvus if persist_vector is None else persist_vector
    if require_milvus:
        persist_vector = True
    if persist_vector:
        if not settings.use_milvus:
            raise RuntimeError("真实 Milvus 索引要求 USE_MILVUS=true")
        actual_dim = embedding_service.probe_embedding_dimension()
        configured_dim = model_config.load_embedding_settings().dimensions
        if configured_dim is not None and actual_dim != configured_dim:
            raise RuntimeError("真实 embedding 维度与配置不一致，拒绝创建 Milvus collection")
    main.PROJECTS[PROJECT_ID] = main.ProjectRecord(
        id=PROJECT_ID, tenant_id=tenant_id, name=REPO_NAME, description="health-middle",
        created_at=_now(), updated_at=_now())
    main.REPOSITORIES[REPO_ID] = main.RepositoryConfigRecord(
        id=REPO_ID, tenant_id=tenant_id, project_id=PROJECT_ID, name=REPO_NAME,
        url=REPO_URL, default_branch="main", test_command="pytest tests/ -q",
        command_whitelist=["pytest"], approval_mode="single", risk_threshold="medium",
        max_files=200, max_retries=1, blocked_paths=[], created_at=_now(), updated_at=_now())
    index = main.RepositoryIndexRecord(
        id=index_id, tenant_id=tenant_id, repository_id=REPO_ID, project_id=PROJECT_ID,
        base_branch="main", commit_sha=commit_sha, status="COMPLETED",
        requested_by="e3-06-cloud", requested_by_name="e3-06-cloud", requested_at=_now(),
        created_at=_now(), updated_at=_now(), milvus_collection=collection_name)
    main.REPOSITORY_INDEXES[index.id] = index
    main.LATEST_INDEX_BY_REPO[REPO_ID] = index.id
    chunks = parse_repo_chunks(
        repo_root,
        commit_sha=commit_sha,
        index_id=index_id,
        collection_name=collection_name,
    )
    for chunk in chunks:
        main.CODE_CHUNKS[chunk.chunk_id] = chunk
    main.CODE_INDEX_SNAPSHOTS[index.id] = chunks
    if runtime_config.load_runtime_settings().use_sqlalchemy:
        from app.db.repositories import (
            upsert_code_chunks,
            upsert_project,
            upsert_repository,
            upsert_repository_index,
        )

        upsert_project(main.PROJECTS[PROJECT_ID])
        upsert_repository(main.REPOSITORIES[REPO_ID])
        upsert_repository_index(index)
        upsert_code_chunks(chunks)
    if persist_vector:
        vector_index = rag_backend.build_index_from_chunks(
            chunks,
            repository_id=REPO_ID,
            index_id=index_id,
            collection_name=collection_name,
            overwrite=True,
        )
        if vector_index is None:
            raise RuntimeError("LlamaIndex/Milvus 依赖不可用，真实向量索引未创建")
    return chunks


def _provenance(source_file: str, source_symbol: str | None, source_type: str = "code") -> dict:
    return {"source_file": source_file, "source_symbol": source_symbol or "",
            "source_type": source_type, "commit_sha": REAL_COMMIT}


_PROVENANCE_EMPTY = {"source_file": "", "source_symbol": "", "source_type": "none",
                     "commit_sha": REAL_COMMIT}


def generate_eval_cases(chunks) -> list[EvaluationCase]:
    """从真实 chunk 结构生成 >=50 条真实评测样本（含边界/无答案/source_type/stale/403/422）。"""
    seen_files = sorted({c.file_path for c in chunks})
    seen_symbols = sorted({(c.file_path, c.symbol_name) for c in chunks if c.symbol_name})
    cases: list[EvaluationCase] = []
    cid = 1

    def add(query, status, tags, difficulty, rationale, *, expected_paths=None, expected_ids=None,
            commit=REAL_COMMIT, ref=None, source_types=None, src=None):
        nonlocal cid
        case = EvaluationCase(
            case_id=f"hm{cid:03d}", query=query, project_id=PROJECT_ID, repository_id=REPO_ID,
            commit_sha=commit, ref=ref, expected_file_paths=expected_paths or [],
            expected_chunk_ids=expected_ids or [], source_types=source_types,
            tags=tags, difficulty=difficulty, rationale=rationale, expected_status=status,
            source_provenance=_provenance(*(src or ("", None))) if src else _PROVENANCE_EMPTY,
        )
        cases.append(case)
        cid += 1
        return case.source_provenance

    # —— 真实函数/类名（每个真实文件取 1~2 个符号）——
    used: set[str] = set()
    for path, sym in seen_symbols:
        if cid > 52:
            break
        if sym in used:
            continue
        used.add(sym)
        prov = _provenance(path, sym, "code")
        add(sym, 200, ["code", "symbol"], "easy" if len(sym) < 24 else "medium",
            f"真实函数/类名 {sym}，应命中真实文件 {path}", expected_paths=[path], src=(path, sym))

    # —— 真实 API 路径 ——
    add("/api/auth/login", 200, ["code", "api"], "medium",
        "真实登录端点应命中 auth 模块源码", expected_paths=["backend/app/api/auth.py"],
        src=("backend/app/api/auth.py", "router.post('/login')"))
    add("/api/chat/send", 200, ["code", "api"], "medium",
        "真实聊天发送端点应命中 chat 模块源码", expected_paths=["backend/app/api/chat.py"],
        src=("backend/app/api/chat.py", "router.post('/send')"))
    add("/api/ocr/upload 体检报告识别", 200, ["code", "api", "natural_language"], "hard",
        "OCR 上传端点与识别服务应命中 ocr 模块", expected_paths=["backend/app/api/ocr.py"],
        src=("backend/app/api/ocr.py", "router.post('/upload')"))

    # —— 自然语言研发问题（基于真实领域）——
    add("如何流式生成健康聊天回复 stream_health_chat_reply", 200, ["natural_language", "symbol"], "easy",
        "流式健康对话实现应命中 llm_service", expected_paths=["backend/app/services/llm_service.py"],
        src=("backend/app/services/llm_service.py", "stream_health_chat_reply"))
    add("体检报告 OCR 识别走哪个服务 recognize_report", 200, ["natural_language", "symbol"], "medium",
        "OCR 识别入口应命中 ocr_service", expected_paths=["backend/app/services/ocr_service.py"],
        src=("backend/app/services/ocr_service.py", "recognize_report"))

    # —— 跨文件调用 ——
    add("登录与密码哈希 hash_password 在哪个文件", 200, ["cross_file", "symbol"], "medium",
        "认证服务与密码工具跨文件关联", expected_paths=["backend/app/services/security.py"]
        if any("security" in p for p in seen_files) else ["backend/app/services/passwords.py"],
        src=("backend/app/services/passwords.py", "hash_password"))
    add("chat 会话与 metrics 报告联动", 200, ["cross_file"], "hard",
        "聊天与指标报告跨模块", expected_paths=["backend/app/api/chat.py", "backend/app/api/metrics.py"])

    # —— 配置项 ——
    cfg = next((p for p in seen_files if p.endswith("config.py")), "backend/app/config.py")
    add("应用配置与数据库连接 config", 200, ["code", "config"], "medium",
        "配置模块应命中 config.py", expected_paths=[cfg], src=(cfg, "config"))
    add("短信发送服务 sms_service", 200, ["code", "symbol"], "easy",
        "短信服务真实符号", expected_paths=["backend/app/services/sms_service.py"],
        src=("backend/app/services/sms_service.py", "sms_service"))

    # —— fresh / stale / source_type / 无答案 / 422 / 403 ——
    add("hash_password 密码哈希", 200, ["fresh"], "easy", "fresh 同 commit",
        expected_paths=["backend/app/services/passwords.py"])
    add("hash_password 密码哈希", 200, ["stale"], "easy", "stale 不同 commit",
        expected_paths=["backend/app/services/passwords.py"], commit="0" * 40)
    add("hash_password", 200, ["source_type"], "medium", "source_types=code 命中",
        expected_paths=["backend/app/services/passwords.py"], source_types=["code"])
    add("hash_password", 200, ["source_type"], "medium", "source_types=document 无文档切片 -> 空结果",
        expected_paths=["backend/app/services/passwords.py"], source_types=["document"])
    add("definitely_not_a_real_symbol_zzz_hm", 200, ["empty"], "easy", "无答案样本 -> 空结果")
    cases.append(EvaluationCase(
        case_id=f"hm{cid:03d}", query="缺版本边界", project_id=PROJECT_ID, repository_id=REPO_ID,
        tags=["boundary"], difficulty="easy", rationale="缺 commit/ref 应为 422",
        expected_status=422, source_provenance=_PROVENANCE_EMPTY))
    cid += 1
    return cases


def ensure_eval_set(repo_root: Path, *, force: bool = False) -> list[EvaluationCase]:
    if EVAL_SET.exists() and not force:
        return load_cases(str(EVAL_SET))
    chunks = build_real_index(repo_root)
    cases = generate_eval_cases(chunks)
    with EVAL_SET.open("w", encoding="utf-8") as handle:
        for case in cases:
            import json as _json
            handle.write(_json.dumps(case.model_dump(mode="json"), ensure_ascii=False) + "\n")
    return cases




def resolve_head(repo_url: str = REPO_URL) -> str:
    """运行时经 git ls-remote 解析仓库 HEAD（不写死旧 SHA）。失败返回空串（离线）。"""
    import subprocess
    try:
        proc = subprocess.run(["git", "ls-remote", repo_url, "HEAD"],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            head = proc.stdout.splitlines()[0].split("	")[0].strip() if proc.stdout.splitlines() else ""
            return head.strip()
    except Exception:  # noqa: BLE001 离线/网络失败
        return ""
    return ""



def write_cloud_evidence(
    report: dict,
    *,
    collection_name: str | None = None,
    query_success: bool = False,
) -> None:
    """写非敏感云端证据；只有真实 collection/index/查询均成立才允许 verified。"""
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    expected_dim = report.get("embedding_dimension")
    collection = collection_name or report.get("collection_name") or "unknown"
    milvus = rag_backend.inspect_milvus_collection(collection, expected_dim=expected_dim)
    milvus["query_success"] = bool(query_success and milvus.get("collection_exists"))
    verified = bool(
        report.get("validation_status") == "verified"
        and report.get("milvus_verified") is True
        and report.get("fallback_used") is False
        and milvus.get("milvus_server_available")
        and milvus.get("collection_exists")
        and milvus.get("index_exists")
        and milvus.get("dimension_match")
        and milvus.get("query_success")
    )
    evidence = {
        "checked_at": _dt.now(_tz.utc).isoformat(),
        "repository_url": "https://gitee.com/yang-zhizemin/health-middle",
        "project_id": PROJECT_ID,
        "repository_id": REPO_ID,
        "commit_sha": report.get("commit_sha", ""),
        "index_id": report.get("index_id", ""),
        "index_commit_sha": report.get("index_commit_sha", ""),
        "commit_index_match": report.get("commit_index_match", False),
        "embedding_model": model_config.load_embedding_settings().model,
        "embedding_model_configured": model_config.embedding_configured(),
        "embedding_dimension": expected_dim,
        "collection_name": collection,
        **milvus,
        "fallback_used": report.get("fallback_used", True),
        "validation_status": "verified" if verified else (
            "blocked" if report.get("validation_status") == "blocked" else "local_only"
        ),
        "evidence_command_names": [
            "git ls-remote Origin HEAD",
            "scripts.evaluate_enterprise_rag --require-milvus --all-modes",
        ],
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "e3_06_health_middle_cloud_evidence.json").write_text(
        _json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


def evaluate_health_middle(repo_root: Path, *, modes=("hybrid", "bm25"), top_k: int = 8,
                           write_report: bool = True) -> dict:
    from app.tenancy import context as tc
    from app.tenancy import store as ts

    build_real_index(repo_root)
    cases = ensure_eval_set(repo_root, force=False)
    ctx = tc.build_context(ts.active_membership_for_user("user_admin"))
    mode_comparison: dict = {}
    primary: dict | None = None
    for mode in modes:
        try:
            report = evaluate_dataset(ctx, cases, top_k=top_k, mode=mode)
        except TypeError:
            report = evaluate_dataset(ctx, cases, top_k=top_k)
        mode_comparison[mode] = report.get("summary", {})
        if primary is None:
            primary = report
    if primary is None:
        return {"validation_status": "blocked", "detail": "评测失败"}

    head_sha = resolve_head(REPO_URL)
    commit_sha = head_sha or REAL_COMMIT
    report = {**primary, "repository_url": "https://gitee.com/yang-zhizemin/health-middle",
              "project_id": PROJECT_ID, "repository_id": REPO_ID,
              "commit_sha": commit_sha, "index_id": INDEX_ID, "index_commit_sha": REAL_COMMIT,
              "commit_index_match": bool(head_sha) and head_sha == REAL_COMMIT,
              "head_resolved": bool(head_sha), "mode_comparison": mode_comparison,
              "vector_backend": "local_no_milvus", "milvus_verified": False,
              "fallback_used": True, "validation_status": "local_only",
              "reproduction_command": (
                  "python scripts/evaluate_enterprise_rag.py --dataset "
                  "evaluation/enterprise_rag/e3_06_health_middle.jsonl "
                  "--all-modes --top-k 8 --require-milvus "
                  "--output evaluation/enterprise_rag/reports/e3_06_health_middle_verified.json"),
              "evidence_refs": [
                  "pytest tests/test_enterprise_e3_06_health_middle_cloud.py",
                  "evaluation/enterprise_rag/e3_06_health_middle.jsonl",
                  f"{REPO_ID} @ commit {commit_sha} index {INDEX_ID} (local in-memory)",
              ],
              "generated_at": _now().isoformat()}
    write_cloud_evidence(report)
    if write_report:
        import json as _json
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / "e3_06_health_middle_local.json").write_text(
            _json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
