# AKShare MCP —— 你自己跑的 AKShare 能力(`_24` §8.2④)
#
# 构建:  docker build -t akshare-mcp .
# 运行:  docker run -d -p 8931:8931 --name akshare-mcp akshare-mcp
#
# ⚠️ 不要 -p 0.0.0.0 暴露到公网:这个服务没有鉴权,
#    而 AKShare 会向第三方站点发请求 —— 等于把你的机器借出去。
FROM python:3.11-slim

# akshare 的依赖里有要编译的(lxml 等),slim 镜像缺 gcc。
# 装完就删,别把编译链留在最终镜像里
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y gcc g++ && apt-get autoremove -y

COPY server.py .

ENV AKSHARE_MCP_HOST=0.0.0.0 \
    AKSHARE_MCP_PORT=8931 \
    AKSHARE_MAX_ROWS=200

EXPOSE 8931
CMD ["python", "server.py"]
