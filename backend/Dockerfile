FROM python:3.12-slim-bookworm

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r /app/backend/requirements.txt

COPY backend /app/backend
COPY deploy/backend-entrypoint.sh /usr/local/bin/backend-entrypoint
COPY deploy/worker-healthcheck.sh /usr/local/bin/worker-healthcheck.sh

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin agent \
    && mkdir -p /var/lib/agent/data \
    && chown -R agent:agent /app /var/lib/agent \
    && chmod 0555 /usr/local/bin/backend-entrypoint /usr/local/bin/worker-healthcheck.sh

WORKDIR /app/backend
USER agent

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/backend-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
