# kronos-mcp · 远程传输形态(常驻服务 · 多客户端连)
#
# 只想在 Claude Desktop / Cursor 里用的话**不需要这个镜像** ——
# 那边走 stdio,`uvx kronos-mcp` 就够了。这个镜像是给「跑成一个服务」的场景。
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY kronos_mcp/ ./kronos_mcp/

ENV KRONOS_MCP_TRANSPORT=streamable-http     KRONOS_MCP_HOST=0.0.0.0     KRONOS_MCP_PORT=8932

# KRONOS_API_KEY 必须在 run 的时候传进来 —— 不要 build 进镜像
EXPOSE 8932
CMD ["python", "-m", "kronos_mcp.server"]
