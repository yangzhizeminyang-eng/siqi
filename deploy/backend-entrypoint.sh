#!/bin/sh
set -eu

app_env="${APP_ENV:-local}"
case "$app_env" in
  team|production|prod|enterprise)
    DATABASE_URL="${DATABASE_URL:-}"
    case "$DATABASE_URL" in
      mysql+pymysql://*) ;;
      *) echo "DATABASE_URL must use mysql+pymysql:// in team/production/enterprise" >&2; exit 64 ;;
    esac

    if [ "${USE_SQLALCHEMY:-true}" != "true" ]; then
      echo "USE_SQLALCHEMY=true is required in team/production/enterprise" >&2
      exit 64
    fi

    if [ -z "${AUTH_JWT_SECRET:-}" ] || [ "${AUTH_JWT_SECRET}" = "replace-with-a-unique-random-secret-at-least-32-bytes" ] || [ "${AUTH_JWT_SECRET}" = "replace-with-a-long-random-secret-at-least-32-bytes" ] || [ "${#AUTH_JWT_SECRET}" -lt 32 ]; then
      echo "AUTH_JWT_SECRET must be a unique secret with at least 32 characters (team/production/enterprise)" >&2
      exit 64
    fi

    if [ -z "${AUTH_RECOVERY_CODE:-}" ] || [ "${AUTH_RECOVERY_CODE}" = "replace-with-a-long-random-recovery-code" ] || [ "${#AUTH_RECOVERY_CODE}" -lt 16 ]; then
      echo "AUTH_RECOVERY_CODE must be configured with at least 16 characters (team/production/enterprise)" >&2
      exit 64
    fi

    if [ "${USE_CELERY:-true}" != "true" ] || [ -z "${CELERY_BROKER_URL:-}" ] || [ -z "${CELERY_RESULT_BACKEND:-}" ] || [ -z "${REDIS_URL:-}" ]; then
      echo "team/production/enterprise requires USE_CELERY=true, CELERY_BROKER_URL, CELERY_RESULT_BACKEND and REDIS_URL" >&2
      exit 64
    fi

    if [ "${USE_MILVUS:-true}" = "true" ] && [ -z "${MILVUS_URI:-}" ]; then
      echo "MILVUS_URI must point to a reachable Milvus service in team/production/enterprise" >&2
      exit 64
    fi

    if [ "$app_env" = "enterprise" ] && [ "${USE_MILVUS:-true}" = "true" ]; then
      if [ -z "${ZHIPU_API_KEY:-}" ]; then
        echo "enterprise Milvus verification requires ZHIPU_API_KEY for the real embedding model" >&2
        exit 64
      fi
      if [ "${EMBEDDING_PROVIDER:-zhipu}" != "zhipu" ] || [ "${ZHIPU_MODEL:-embedding-3}" != "embedding-3" ]; then
        echo "enterprise RAG requires EMBEDDING_PROVIDER=zhipu and ZHIPU_MODEL=embedding-3" >&2
        exit 64
      fi
      case "${EMBEDDING_DIMENSIONS:-auto}" in
        auto) ;;
        ''|*[!0-9]*)
          echo "EMBEDDING_DIMENSIONS must be auto or a positive integer in enterprise" >&2
          exit 64
          ;;
        0)
          echo "EMBEDDING_DIMENSIONS must be auto or a positive integer in enterprise" >&2
          exit 64
          ;;
      esac
      if [ "${MILVUS_COLLECTION_PREFIX:-}" != "enterprise" ]; then
        echo "enterprise Milvus requires MILVUS_COLLECTION_PREFIX=enterprise" >&2
        exit 64
      fi
    fi

    if [ -z "${FRONTEND_CORS_ORIGINS:-}" ]; then
      echo "FRONTEND_CORS_ORIGINS must be configured in team/production/enterprise" >&2
      exit 64
    fi
    ;;
esac

exec "$@"
