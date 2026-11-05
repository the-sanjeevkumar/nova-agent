#!/usr/bin/env bash
# Launch the DeepSeek Harness (dsh) against Nova's local model endpoint.
# Usage: ./scripts/run_deepseek_harness.sh [extra dsh args...]
set -euo pipefail

# Ensure Nova's local OpenAI-compatible proxy is running, or point at your
# local model server (Ollama/vLLM/etc.).
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:11434/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-nova-local}"
export OPENAI_MODEL="${OPENAI_MODEL:-deepseek-coder}"

echo "Starting DeepSeek Harness against ${OPENAI_BASE_URL} (model: ${OPENAI_MODEL})"
exec npx @deepseek-ai/dsh web "$@"