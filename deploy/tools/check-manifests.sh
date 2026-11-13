#!/usr/bin/env bash
# 发版自检:匿名(不带任何凭据)取 GHCR 上四个镜像某个标签的 manifest,列出它覆盖了哪些架构。
#
# 为什么要有:docker-publish.yml 是「分平台构建 → push by digest → 合成 manifest list」,
# 合成那一步失败时**单架构的镜像照样能拉**,只有 arm64 用户会发现拉不到 —— 而我们手上
# 没有 arm64 机器。匿名取一次 manifest 是唯一不需要 arm64 机器也能验的那一层。
#
#   bash deploy/tools/check-manifests.sh 3.0.0
#
# 六行都应该是 `HTTP 200  架构: linux/amd64, linux/arm64`。
TAG="${1:?用法: check-manifests.sh <标签>}"
for img in api web daemon llm-shim paper fin-worker; do
  repo="agentpit-io/hunter-community-$img"
  tok=$(curl -s "https://ghcr.io/token?scope=repository:$repo:pull&service=ghcr.io" \
        | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
  out=$(curl -s -o /tmp/_mf.json -w '%{http_code}' -H "Authorization: Bearer $tok" \
        -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' \
        "https://ghcr.io/v2/$repo/manifests/$TAG")
  archs=$(python3 - <<'PY' 2>/dev/null
import json
d = json.load(open('/tmp/_mf.json'))
ms = d.get('manifests') or []
# attestation manifest 的 platform 是 unknown/unknown,不算架构
a = sorted({f"{m['platform']['os']}/{m['platform']['architecture']}" for m in ms
            if m.get('platform', {}).get('architecture') != 'unknown'})
print(', '.join(a) if a else ('单架构:' + str(d.get('architecture', '?'))))
PY
)
  if [ "$out" != "200" ]; then archs="(这个标签还没发布)"; fi
  printf '%-10s %-8s HTTP %s  架构: %s\n' "$img" "$TAG" "$out" "${archs:-解析失败}"
done
