# truesource-mcp · 远程传输形态(常驻服务 · 多客户端连)
#
# 只想在 Claude Desktop / Cursor 里用的话**不需要这个镜像** ——
# 那边走 stdio,`uvx truesource-mcp` 就够了。这个镜像是给「跑成一个服务」的场景。
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY truesource_mcp/ ./truesource_mcp/

ENV TRUESOURCE_MCP_TRANSPORT=streamable-http     TRUESOURCE_MCP_HOST=0.0.0.0     TRUESOURCE_MCP_PORT=8933

# HUNTER_API_KEY 必须在 run 的时候传进来 —— 不要 build 进镜像
EXPOSE 8933
CMD ["python", "-m", "truesource_mcp.server"]
