#!/bin/sh
# 智能炒股 · 给账本库的运行期角色 fin_paper_rw 设置密码（一次性 provision）
#
# 为什么需要这一步：`0023_fin_core.sql` 建角色时**故意不写密码**（密码不进仓库），
# 而 `paper` 容器要通过 TCP + 口令认证连库。表权限、序列权限迁移里都给了，
# 只差这一条口令。
#
# 用法：
#     # 1) 在 .env 里设一个随机密码（别用示例值）
#     #    FIN_PAPER_PASSWORD=$(openssl rand -hex 24)
#     # 2) 跑这个脚本（在仓库根、容器在跑）
#     bash scripts/fin_provision_paper_role.sh
#     # 3) 起账本服务
#     docker compose --profile fin up -d paper
#
# 幂等：重复运行只是把密码改成同一个值。
# 安全：用 psql 变量 + `:'pw'` 做字符串字面量，密码里有引号也不会拼坏 SQL。
set -e

cd "$(dirname "$0")/.."

# 从 .env 读（没有就要求显式导出）。**只取 FIN_PAPER_PASSWORD 一行**，不 . 整个文件
# （.env 里的值可能带空格 / 特殊字符，source 会出事）。
if [ -z "${FIN_PAPER_PASSWORD:-}" ] && [ -r .env ]; then
    FIN_PAPER_PASSWORD="$(sed -n 's/^FIN_PAPER_PASSWORD=//p' .env | head -n 1)"
fi

if [ -z "${FIN_PAPER_PASSWORD:-}" ]; then
    echo "错误：FIN_PAPER_PASSWORD 未设置。" >&2
    echo "  在 .env 里加一行（随机值）：" >&2
    echo "    FIN_PAPER_PASSWORD=$(openssl rand -hex 24)" >&2
    exit 1
fi

PG_USER="$(sed -n 's/^POSTGRES_USER=//p' .env 2>/dev/null | head -n 1)"
PG_DB="$(sed -n 's/^POSTGRES_DB=//p' .env 2>/dev/null | head -n 1)"
PG_USER="${PG_USER:-hunter}"
PG_DB="${PG_DB:-hunter}"

echo "[fin] 给 fin_paper_rw 设置密码（库=${PG_DB} · 用户=${PG_USER}）"
# ⚠️ psql 的 `-v` 变量在 `-c` 里**不做替换**（M4 实测：`-c "... :'pw'"` 报
#    `syntax error at or near ":"`，一次性 provision 直接失败）。要走 stdin。
#    这里用 heredoc 把 SQL 喂给 psql，`:'pw'` 才会被替换成带引号的字面量。
docker compose exec -T postgres \
    psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 -v pw="$FIN_PAPER_PASSWORD" <<'SQL'
ALTER ROLE fin_paper_rw WITH LOGIN PASSWORD :'pw';
SQL

echo "[fin] 完成。起服务： docker compose --profile fin up -d paper"
