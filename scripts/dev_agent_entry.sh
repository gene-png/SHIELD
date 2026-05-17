#!/usr/bin/env bash
# =====================================================================
# Entry point for the SHIELD dev-agent container.
#
# Usage (from the host):
#   docker compose run --rm dev-agent "your prompt here"
#
# Forwards the prompt to the headless Claude Code CLI with
# --dangerously-skip-permissions ENABLED. This is intentional: the
# container is the sandbox. Permissions are unsafe to skip ON A HOST
# MACHINE; they are appropriate here because nothing outside /workspace
# is reachable.
# =====================================================================
set -euo pipefail

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ERROR: ANTHROPIC_API_KEY is not set. Put it in your .env file." >&2
  exit 2
fi

PROMPT="${*:-}"
if [[ -z "$PROMPT" ]]; then
  echo "Usage: docker compose run --rm dev-agent \"<task prompt>\"" >&2
  echo "Or:    docker compose run --rm --entrypoint bash dev-agent     (interactive shell)" >&2
  exit 2
fi

MODEL="${ANTHROPIC_MODEL_AGENT:-claude-sonnet-4-6}"
LOG_DIR="/workspace/reports/agent-runs"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/$(date -u +%Y%m%dT%H%M%SZ).log"

echo "================================================================"
echo " SHIELD dev-agent"
echo " model:  $MODEL"
echo " cwd:    $(pwd)"
echo " log:    $LOG_FILE"
echo " prompt: $PROMPT"
echo "================================================================"

# --dangerously-skip-permissions is SAFE here because we are inside the
# sandboxed container with no host filesystem access.
exec claude \
  --model "$MODEL" \
  --dangerously-skip-permissions \
  -p "$PROMPT" \
  2>&1 | tee "$LOG_FILE"
