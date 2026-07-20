FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \
        ca-certificates libfbclient2 python3 python3-venv \
    && python3 -m venv /opt/venv \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 fire2api \
    && useradd --system --uid 10001 --gid fire2api --home-dir /app fire2api

WORKDIR /app
COPY requirements-runtime.txt ./
RUN python -m pip install --requirement requirements-runtime.txt

COPY --chown=fire2api:fire2api app ./app
COPY --chown=fire2api:fire2api migrations ./migrations
COPY --chown=fire2api:fire2api alembic.ini main.py pyproject.toml ./
RUN mkdir -p /app/data && chown fire2api:fire2api /app/data \
    && test -z "$(find /app/data -mindepth 1 -print -quit)"

USER 10001:10001
EXPOSE 8000
VOLUME ["/app/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["python", "main.py"]
