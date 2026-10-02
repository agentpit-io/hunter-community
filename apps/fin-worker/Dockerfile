# Hunter Community · fin-worker · Runtime Bridge + Temporal Workers
#
# 与 apps/paper 同栈（Python），但**独立镜像、独立进程**：它不连账本库，
# 只经 paper 的 HTTP 改账本（01方案 §7.4 / §11.3）。依赖里没有 psycopg2 ——
# 「不直连账本」是一条能被 grep 出来的事实，不是一句注释。
#
# ⚠️ 构建上下文是 `apps/fin-worker/`（不是仓库根）。
# 构建： docker build -f apps/fin-worker/Dockerfile -t hunter-community-fin-worker apps/fin-worker

# ─── builder ────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt ./
RUN pip install -r requirements.txt

# ─── runtime ────────────────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app /app/app
COPY boot.sh /app/boot.sh
RUN chmod +x /app/boot.sh

EXPOSE 8300

# /healthz 免密钥（容器内健康检查拿不到 HUNTER_INTERNAL_KEY）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8300/healthz || exit 1

CMD ["/app/boot.sh"]
