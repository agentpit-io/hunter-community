#!/usr/bin/env bash
# 跑 6 家 aihubmix 上游 · 每家 7 golden case
set -u
LOG=/tmp/run-6-vendors.log
ENV_FILE=$HUNTER_COMMUNITY/.env
RUNNER=docs/model-testing/scripts/run-golden-cases.py

MODELS=(
  "gpt-5.6-sol|aihubmix-gpt-5.6-sol"
  "claude-sonnet-5|aihubmix-claude-sonnet-5"
  "gemini-3.5-flash|aihubmix-gemini-3.5-flash"
  "qwen3.8-max|aihubmix-qwen3.8-max"
  "doubao-seed-2-1-pro|aihubmix-doubao-seed-2-1-pro"
  "minimax-m3|aihubmix-minimax-m3"
)

echo "[$(date +%H:%M:%S)] START · ${#MODELS[@]} models" | tee -a "$LOG"

for entry in "${MODELS[@]}"; do
  MODEL="${entry%%|*}"
  LABEL="${entry##*|}"
  echo "" | tee -a "$LOG"
  echo "===============================================================" | tee -a "$LOG"
  echo "[$(date +%H:%M:%S)] BEGIN · $MODEL · label=$LABEL" | tee -a "$LOG"
  echo "===============================================================" | tee -a "$LOG"

  # 1. sed .env
  sed -i '' "s|^LLM_DEFAULT_MODEL=.*|LLM_DEFAULT_MODEL=$MODEL|" "$ENV_FILE"
  grep '^LLM_DEFAULT_MODEL=' "$ENV_FILE" | tee -a "$LOG"

  # 2. docker recreate
  cd $HUNTER_COMMUNITY
  docker compose up -d --force-recreate opencode llm-shim >/dev/null 2>&1
  echo "[$(date +%H:%M:%S)] recreated · waiting healthy..." | tee -a "$LOG"

  # 3. wait healthy
  for i in $(seq 1 30); do
    STATUS=$(docker inspect hunter-community-opencode-1 --format '{{.State.Health.Status}}' 2>/dev/null)
    [ "$STATUS" = "healthy" ] && break
    sleep 2
  done
  echo "[$(date +%H:%M:%S)] opencode $STATUS" | tee -a "$LOG"

  # 4. verify provider config picked new model
  P=$(grep OPENCODE_PASS "$ENV_FILE" | cut -d= -f2)
  MODEL_LIST=$(curl -sS -u "opencode:$P" http://127.0.0.1:3921/config/providers 2>/dev/null | \
    python3 -c "
import sys, json
d = json.load(sys.stdin)
for p in d['providers']:
    if p['id'] == 'hunter-llm':
        print(list(p.get('models',{}).keys()))
        break
" 2>/dev/null)
  echo "[$(date +%H:%M:%S)] hunter-llm models: $MODEL_LIST" | tee -a "$LOG"

  # 5. run golden cases
  echo "[$(date +%H:%M:%S)] running golden cases..." | tee -a "$LOG"
  python3 "$RUNNER" --provider hunter-llm --model "$MODEL" --label "$LABEL" 2>&1 | tee -a "$LOG"
  echo "[$(date +%H:%M:%S)] DONE · $MODEL" | tee -a "$LOG"
done

echo "" | tee -a "$LOG"
echo "[$(date +%H:%M:%S)] ALL 6 MODELS DONE" | tee -a "$LOG"
