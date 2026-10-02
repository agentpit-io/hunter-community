# Hunter Community · Paper Service · 唯一账本 + 确定性风控
#
# 与 apps/api 同栈（FastAPI + psycopg2），但**独立镜像、独立进程**：
# 它是全仓唯一持有 `fin_paper_rw` 连接的服务（08 §二「服务落点」）。
#
# ⚠️ 构建上下文是 `apps/paper/`（不是仓库根）—— 本服务自包含，不依赖仓库里
#    skills/ / data/ / db/migrations 那些东西。默认 compose 文件里不写 build，
#    只用预构建镜像；开发用 docker-compose.dev.yml 才加 build。
#
# 构建： docker build -f apps/paper/Dockerfile -t hunter-community-paper apps/paper

# ─── builder ────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt ./
RUN pip install -r requirements.txt

# ─── runtime ────────────────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# curl · HEALTHCHECK（psycopg2-binary 自带 libpq，运行层不需要 libpq-dev）
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app /app/app

EXPOSE 8000

# /healthz 是唯一免密钥端点（容器内健康检查拿不到 HUNTER_INTERNAL_KEY）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# 入口换成 boot.sh：自检（模式 / 口令 / 库权限）→ 起 uvicorn。
# 自检不过就起不来 —— 缺表 / 连不上的实例「看着健康、一点就 500」比直接起不来难排查。
CMD ["/app/boot.sh"]
