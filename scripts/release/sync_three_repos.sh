#!/usr/bin/env bash
# 文件路径：scripts/release/sync_three_repos.sh
# 用途：执行两级三仓代码与版本同步铁律
#   第一级（双开源仓 100% 完全相同、同 Commit SHA）：
#     - github.com/agentpit-io/hunter-community (main & Tag)
#     - gitcode.com/agentpit-io/huntercode-atomcode (SSH: git@gitcode.com:agentpit-io/huntercode-atomcode.git, main & Tag)
#     - github.com/agentpit-io/huntercode-atomcode (main & Tag)
#     - 同步更新本地副本 /mnt/mixplode/hunter-dev/stock-ai/hunter-community
#   第二级（独立业务仓 huntercode 定向同步功能代码与版本，绝不 force/mirror 覆盖其独立业务代码）：
#     - 本地克隆：/mnt/mixplode/hunter-dev/huntercode-repo (默认分支 dev)
#     - 定向同步新增/升级的 MCP 工具 (mcp/)、金融 SKILL (skills-source/)、共享模块 (providers/services) 与规范文档 (docs-hunter/)
#     - 更新版本号并打上相同版本 Tag 推送至 github.com/agentpit-io/huntercode
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HCA_REPO="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMMUNITY_COPY="/mnt/mixplode/hunter-dev/stock-ai/hunter-community"
HUNTERCODE_REPO="/mnt/mixplode/hunter-dev/huntercode-repo"

RAW_VER="${1:-}"
if [ -z "${RAW_VER}" ]; then
  if [ -f "${HCA_REPO}/VERSION" ]; then
    RAW_VER=$(tr -d '[:space:]' < "${HCA_REPO}/VERSION")
  else
    echo "❌ 错误: 请传入版本号（如 v3.0.2）或确保 VERSION 文件存在"
    exit 1
  fi
fi

if [[ "${RAW_VER}" == v* ]]; then
  TAG_NAME="${RAW_VER}"
  VER_NUM="${RAW_VER#v}"
else
  TAG_NAME="v${RAW_VER}"
  VER_NUM="${RAW_VER}"
fi

echo "=================================================================="
echo "🚀 开始执行三仓同步流水线 · 目标版本: ${TAG_NAME} (VERSION=${VER_NUM})"
echo "=================================================================="

cd "${HCA_REPO}"

# 1. 检查已跟踪文件是否干净（忽略未跟踪的本地环境备份文件）
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "❌ 错误: ${HCA_REPO} 存在未提交的已跟踪文件改动，请先 git commit 后再运行同步脚本！"
  git status -s
  exit 1
fi

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "${CURRENT_BRANCH}" != "main" ]; then
  echo "❌ 错误: 当前分支为 ${CURRENT_BRANCH}，必须在 main 分支执行发布同步！"
  exit 1
fi

HEAD_SHA=$(git rev-parse HEAD)
echo "📌 主工作仓 (${HCA_REPO}) main HEAD SHA: ${HEAD_SHA}"

# 2. 打或更新本地版本 Tag（指向当前 main HEAD）
git tag -f "${TAG_NAME}" "${HEAD_SHA}"
echo "🏷️  已在主工作仓创建/对齐标签: ${TAG_NAME} -> ${HEAD_SHA}"

# 3. 第一级：推送至双开源仓（GitHub hunter-community、GitHub huntercode-atomcode、GitCode huntercode-atomcode）
echo "------------------------------------------------------------------"
echo "📦 [第一级] 同步双开源仓 (100% 同 Commit SHA: ${HEAD_SHA})"
echo "------------------------------------------------------------------"

echo "-> 推送至 origin (github.com/agentpit-io/huntercode-atomcode)..."
git push origin main
git push origin "refs/tags/${TAG_NAME}" --force

echo "-> 推送至 community (github.com/agentpit-io/hunter-community)..."
git push community main
git push community "refs/tags/${TAG_NAME}" --force

echo "-> 推送至 gitcode (git@gitcode.com:agentpit-io/huntercode-atomcode.git · 强制 SSH)..."
git push gitcode main
git push gitcode "refs/tags/${TAG_NAME}" --force

# 4. 同步更新本地副本 /mnt/mixplode/hunter-dev/stock-ai/hunter-community
if [ -d "${COMMUNITY_COPY}/.git" ]; then
  echo "-> 同步本地工作区副本 ${COMMUNITY_COPY}..."
  git -C "${COMMUNITY_COPY}" fetch origin main --tags --force
  git -C "${COMMUNITY_COPY}" checkout main
  git -C "${COMMUNITY_COPY}" reset --hard "${HEAD_SHA}"
fi

# 5. 第二级：向独立业务仓 /mnt/mixplode/hunter-dev/huntercode-repo 定向同步功能模块与版本
echo "------------------------------------------------------------------"
echo "🛠️  [第二级] 向独立业务仓 huntercode (${HUNTERCODE_REPO}) 定向同步功能模块与版本"
echo "------------------------------------------------------------------"

if [ ! -d "${HUNTERCODE_REPO}/.git" ]; then
  echo "❌ 错误: 未找到独立业务仓本地克隆 ${HUNTERCODE_REPO}"
  exit 1
fi

git -C "${HUNTERCODE_REPO}" checkout dev
git -C "${HUNTERCODE_REPO}" pull --ff-only origin dev || true

# 5.1 同步 MCP 工具脚本至 huntercode-repo/mcp/
mkdir -p "${HUNTERCODE_REPO}/mcp"
if [ -d "${HCA_REPO}/tools" ]; then
  for mcp_file in "${HCA_REPO}"/tools/*_mcp.py "${HCA_REPO}"/tools/hca_size_guard.py; do
    if [ -f "${mcp_file}" ]; then
      cp -f "${mcp_file}" "${HUNTERCODE_REPO}/mcp/"
    fi
  done
fi
if [ -d "${HCA_REPO}/distro/mcp-servers" ]; then
  mkdir -p "${HUNTERCODE_REPO}/mcp/broker-servers"
  cp -rf "${HCA_REPO}/distro/mcp-servers/"* "${HUNTERCODE_REPO}/mcp/broker-servers/" 2>/dev/null || true
fi
if [ -f "${HCA_REPO}/distro/mcp-tools.json" ]; then
  cp -f "${HCA_REPO}/distro/mcp-tools.json" "${HUNTERCODE_REPO}/mcp/mcp-tools.json"
fi

# 5.2 同步金融 SKILL 至 huntercode-repo/skills-source/
mkdir -p "${HUNTERCODE_REPO}/skills-source"
if [ -d "${HCA_REPO}/skills" ]; then
  for skill_dir in "${HCA_REPO}/skills"/*; do
    if [ -d "${skill_dir}" ]; then
      skill_name=$(basename "${skill_dir}")
      rm -rf "${HUNTERCODE_REPO}/skills-source/${skill_name}"
      cp -rf "${skill_dir}" "${HUNTERCODE_REPO}/skills-source/${skill_name}"
    fi
  done
fi
if [ -d "${HCA_REPO}/distro/workspace-template/skills" ]; then
  for skill_dir in "${HCA_REPO}/distro/workspace-template/skills"/*; do
    if [ -d "${skill_dir}" ]; then
      skill_name=$(basename "${skill_dir}")
      rm -rf "${HUNTERCODE_REPO}/skills-source/${skill_name}"
      cp -rf "${skill_dir}" "${HUNTERCODE_REPO}/skills-source/${skill_name}"
    fi
  done
fi

if [ -f "${HCA_REPO}/apps/api/skills/market-event-analyst.skill" ]; then
  cp -f "${HCA_REPO}/apps/api/skills/market-event-analyst.skill" "${HUNTERCODE_REPO}/skills-source/market-event-analyst.skill"
fi

# 5.3 同步券商接口适配层、计算引擎与共享规范文档至 huntercode-repo/docs-hunter/ 及 shared-broker/
mkdir -p "${HUNTERCODE_REPO}/docs-hunter/broker-specs"
if [ -f "${HCA_REPO}/docs/开源SKILL复用与许可证清单.md" ]; then
  cp -f "${HCA_REPO}/docs/开源SKILL复用与许可证清单.md" "${HUNTERCODE_REPO}/docs-hunter/broker-specs/"
fi

mkdir -p "${HUNTERCODE_REPO}/packages/hunter-broker/providers"
if [ -d "${HCA_REPO}/apps/api/app/providers/broker" ]; then
  cp -rf "${HCA_REPO}/apps/api/app/providers/broker/"* "${HUNTERCODE_REPO}/packages/hunter-broker/providers/"
  rm -rf "${HUNTERCODE_REPO}/packages/hunter-broker/providers/__pycache__"
fi

for svc_name in indicators fundamentals etf_dca workflows; do
  if [ -d "${HCA_REPO}/apps/api/app/services/${svc_name}" ]; then
    mkdir -p "${HUNTERCODE_REPO}/packages/hunter-broker/services/${svc_name}"
    cp -rf "${HCA_REPO}/apps/api/app/services/${svc_name}/"* "${HUNTERCODE_REPO}/packages/hunter-broker/services/${svc_name}/"
    rm -rf "${HUNTERCODE_REPO}/packages/hunter-broker/services/${svc_name}/__pycache__"
  fi
done

if [ -f "${HCA_REPO}/apps/api/app/routers/daily_workflows.py" ]; then
  mkdir -p "${HUNTERCODE_REPO}/packages/hunter-broker/routers"
  cp -f "${HCA_REPO}/apps/api/app/routers/daily_workflows.py" "${HUNTERCODE_REPO}/packages/hunter-broker/routers/"
fi

# 5.4 更新 huntercode-repo 版本号文件
echo "${VER_NUM}" > "${HUNTERCODE_REPO}/VERSION"

# 5.5 在 huntercode-repo 提交同步改动并打 Tag 推送至 origin (github.com/agentpit-io/huntercode)
git -C "${HUNTERCODE_REPO}" add -A
if ! git -C "${HUNTERCODE_REPO}" diff --cached --quiet; then
  git -C "${HUNTERCODE_REPO}" commit -m "chore(sync): 同步券商接口适配层、MCP、金融 SKILL 与规范文档至 ${TAG_NAME}

对应社区开源仓 Commit: ${HEAD_SHA}

Co-Authored-By: Claude Code <noreply@anthropic.com>"
else
  echo "ℹ️  huntercode-repo 文件无新增差异，保持当前 dev HEAD"
fi

HC_HEAD_SHA=$(git -C "${HUNTERCODE_REPO}" rev-parse HEAD)
git -C "${HUNTERCODE_REPO}" tag -f "${TAG_NAME}" "${HC_HEAD_SHA}"
echo "-> 推送独立业务仓 huntercode (dev 分支与标签 ${TAG_NAME} -> ${HC_HEAD_SHA})..."
git -C "${HUNTERCODE_REPO}" push origin dev
git -C "${HUNTERCODE_REPO}" push origin "refs/tags/${TAG_NAME}" --force

# 6. 调用核验脚本验证三仓同步结果
echo "------------------------------------------------------------------"
echo "🔍 [第三步] 运行三仓同步核验脚本 verify_three_repos.sh ${TAG_NAME}"
echo "------------------------------------------------------------------"
bash "${SCRIPT_DIR}/verify_three_repos.sh" "${TAG_NAME}"
