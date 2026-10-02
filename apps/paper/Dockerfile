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
# tzdata · **必须装**：风控第 1 条按市场时区判交易时段（`app/market_time.py` 用
#   `zoneinfo.ZoneInfo("America/New_York")`）。python:slim 不带 tzdata ——
#   `risk/session.py` 原来硬编码 `CST=+8` 正是因为镜像没有它（N2 起改走真时区）。
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app /app/app
# ⚠️ `boot.sh` 必须一起 COPY 进来：`CMD` 指向 `/app/boot.sh`，不拷就是
#    「镜像构建成功、一跑就 exec: /app/boot.sh: no such file or directory」。
#    开发用的是 dev compose 的 bind mount，只有本地跑过才知道 ——
#    M3 用真实镜像起容器时踩到，这里补上（构建期挡不住，镜像里没这个文件）。
COPY boot.sh /app/boot.sh
RUN chmod +x /app/boot.sh

EXPOSE 8000

# /healthz 是唯一免密钥端点（容器内健康检查拿不到 HUNTER_INTERNAL_KEY）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# 入口换成 boot.sh：自检（模式 / 口令 / 库权限）→ 起 uvicorn。
# 自检不过就起不来 —— 缺表 / 连不上的实例「看着健康、一点就 500」比直接起不来难排查。
CMD ["/app/boot.sh"]
