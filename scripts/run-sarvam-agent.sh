#!/usr/bin/env bash
# Runs a second, independently-named agent worker wired to Sarvam's own
# LLM (sarvam-105b - "sarvam-m" is deprecated per their API), for
# side-by-side comparison against the DeepSeek worker - see
# scripts/run-deepseek-agent.sh. Uses SARVAM_API_KEY directly (not
# LiveKit's inference gateway - Sarvam isn't on it), via
# agent/worker.py's _build_llm() auto-detection on the "sarvam-" prefix.
#
# `dev` mode connects to your LiveKit Cloud project and registers this
# agent_name so it's dispatchable from the Agent Console / Agents
# Playground - it does NOT touch the live Telnyx telephony trunk/dispatch
# rule, which points at the AGENT_NAME set in .env, unaffected by this.
set -euo pipefail
cd "$(dirname "$0")/.."

export AGENT_NAME="real-estate-group-sarvam"
export LLM_MODEL="sarvam-105b"

echo "Starting agent '$AGENT_NAME' with LLM_MODEL=$LLM_MODEL ..."
uv run python -m agent.worker dev
