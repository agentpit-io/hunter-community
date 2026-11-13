#!/usr/bin/env bash
# 文件路径：scripts/release/verify_three_repos.sh
# 用途：核验三大代码仓库（双开源仓 100% 同 SHA + 独立业务仓 huntercode 功能与版本标签同步）
set -euo pipefail

RAW_VER="${1:-}"
if [ -z "${RAW_VER}" ]; then
  if [ -f "VERSION" ]; then
    RAW_VER=$(tr -d '[:space:]' < VERSION)
  else
    RAW_VER="v3.0.2"
  fi
fi

if [[ "${RAW_VER}" == v* ]]; then
  EXPECTED_TAG="${RAW_VER}"
else
  EXPECTED_TAG="v${RAW_VER}"
fi

echo "=== 1. 获取三大仓库远端分支与标签 SHA (目标版本: ${EXPECTED_TAG}) ==="
SHA_COMMUNITY=$(git ls-remote https://github.com/agentpit-io/hunter-community.git refs/heads/main | awk '{print $1}')
SHA_ATOMCODE_GH=$(git ls-remote https://github.com/agentpit-io/huntercode-atomcode.git refs/heads/main | awk '{print $1}')
SHA_ATOMCODE=$(git ls-remote git@gitcode.com:agentpit-io/huntercode-atomcode.git refs/heads/main | awk '{print $1}')
SHA_HUNTERCODE=$(git ls-remote https://github.com/agentpit-io/huntercode.git refs/heads/dev | awk '{print $1}')
if [ -z "${SHA_HUNTERCODE}" ]; then
  SHA_HUNTERCODE=$(git ls-remote https://github.com/agentpit-io/huntercode.git refs/heads/main | awk '{print $1}')
fi

# 兼容 lightweight tag 与 annotated tag（优先取 ^{} 解引用后的 commit SHA，若无则取 tag SHA）
get_remote_tag_sha() {
  local remote_url="$1"
  local tag_name="$2"
  local deref
  deref=$(git ls-remote "${remote_url}" "refs/tags/${tag_name}^{}" | awk '{print $1}')
  if [ -n "${deref}" ]; then
    echo "${deref}"
  else
    git ls-remote "${remote_url}" "refs/tags/${tag_name}" | awk '{print $1}'
  fi
}

TAG_COMMUNITY=$(get_remote_tag_sha "https://github.com/agentpit-io/hunter-community.git" "${EXPECTED_TAG}")
TAG_ATOMCODE_GH=$(get_remote_tag_sha "https://github.com/agentpit-io/huntercode-atomcode.git" "${EXPECTED_TAG}")
TAG_ATOMCODE=$(get_remote_tag_sha "git@gitcode.com:agentpit-io/huntercode-atomcode.git" "${EXPECTED_TAG}")
TAG_HUNTERCODE=$(get_remote_tag_sha "https://github.com/agentpit-io/huntercode.git" "${EXPECTED_TAG}")

echo "hunter-community (GitHub) main SHA       : ${SHA_COMMUNITY}"
echo "huntercode-atomcode (GitHub) main SHA    : ${SHA_ATOMCODE_GH}"
echo "huntercode-atomcode (GitCode) main SHA   : ${SHA_ATOMCODE}"
echo "huntercode (GitHub 独立业务仓 dev) SHA   : ${SHA_HUNTERCODE}"
echo "hunter-community Tag ${EXPECTED_TAG}             : ${TAG_COMMUNITY}"
echo "huntercode-atomcode (GitHub) Tag ${EXPECTED_TAG} : ${TAG_ATOMCODE_GH}"
echo "huntercode-atomcode (GitCode) Tag ${EXPECTED_TAG}: ${TAG_ATOMCODE}"
echo "huntercode (独立业务仓) Tag ${EXPECTED_TAG}      : ${TAG_HUNTERCODE}"

# 判据 1：hunter-community 与 huntercode-atomcode (GitCode & GitHub) 主干必须 100% 代码完全相同（同 Commit SHA）
if [ -z "${SHA_COMMUNITY}" ] || [ "${SHA_COMMUNITY}" != "${SHA_ATOMCODE}" ] || [ "${SHA_COMMUNITY}" != "${SHA_ATOMCODE_GH}" ]; then
  echo "❌ FAIL: hunter-community 与 huntercode-atomcode (GitCode/GitHub) 的 main 分支 SHA 不一致！"
  exit 1
fi
echo "✅ PASS 1: GitHub hunter-community 与 GitCode/GitHub huntercode-atomcode 主干 100% 代码完全相同（同 SHA: ${SHA_COMMUNITY}）"

# 判据 2：hunter-community 与 huntercode-atomcode 的版本 Tag 必须完全一致
if [ -z "${TAG_COMMUNITY}" ] || [ "${TAG_COMMUNITY}" != "${TAG_ATOMCODE}" ] || [ "${TAG_COMMUNITY}" != "${TAG_ATOMCODE_GH}" ]; then
  echo "❌ FAIL: hunter-community 与 huntercode-atomcode 的标签 ${EXPECTED_TAG} 不一致！"
  exit 1
fi
echo "✅ PASS 2: 双开源仓版本标签 ${EXPECTED_TAG} 100% 完全对齐（SHA: ${TAG_COMMUNITY}）"

# 判据 3：独立业务仓 github.com/agentpit-io/huntercode 严禁被直接镜像覆盖（SHA 应保持其独立演进历史）
if [ -z "${SHA_HUNTERCODE}" ] || [ "${SHA_HUNTERCODE}" = "${SHA_COMMUNITY}" ]; then
  echo "❌ FAIL: 警告！huntercode 独立业务仓的分支 SHA 为空或与 hunter-community 相同，疑似误用了直接镜像覆盖！"
  exit 1
fi
echo "✅ PASS 3: github.com/agentpit-io/huntercode 保持独立仓库与独立业务历史（未被错误镜像覆盖，dev SHA: ${SHA_HUNTERCODE}）"

# 判据 4：独立业务仓 github.com/agentpit-io/huntercode 已同步对应功能代码并打上相同版本号 Tag
if [ -z "${TAG_HUNTERCODE}" ]; then
  echo "❌ FAIL: github.com/agentpit-io/huntercode 尚未同步版本标签 ${EXPECTED_TAG}！"
  exit 1
fi
if [ "${TAG_HUNTERCODE}" != "${SHA_HUNTERCODE}" ]; then
  echo "⚠️ 提示: huntercode Tag ${EXPECTED_TAG} (${TAG_HUNTERCODE}) 指向其独立业务仓提交，校验其不为社区仓 SHA..."
  if [ "${TAG_HUNTERCODE}" = "${SHA_COMMUNITY}" ]; then
    echo "❌ FAIL: huntercode 的标签 ${EXPECTED_TAG} 指向了社区仓 Commit (${TAG_HUNTERCODE})，属于跨仓孤立标签而非 huntercode 自身分支提交！"
    exit 1
  fi
fi
echo "✅ PASS 4: github.com/agentpit-io/huntercode 独立仓已同步功能代码与版本 ${EXPECTED_TAG}（Tag SHA: ${TAG_HUNTERCODE}）"
