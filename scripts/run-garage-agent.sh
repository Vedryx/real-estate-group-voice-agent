#!/usr/bin/env bash
# Runs the inbound auto-garage vertical (car service + car rental) under its
# own agent_name, for side-by-side demo use with run-canopy-agent.sh /
# run-clinic-agent.sh / run-salon-agent.sh — run whichever vertical you want
# to demo, or several at once under different agent_names.
#
# `dev` mode connects to your LiveKit Cloud project and registers this
# agent_name so it's dispatchable from the Agent Console / Agents
# Playground - it does NOT touch the live Telnyx telephony trunk/dispatch
# rule (that points at whatever AGENT_NAME is set to in .env, unaffected
# by this - keep the two in sync if you ever change .env's AGENT_NAME).
set -euo pipefail
cd "$(dirname "$0")/.."

export VERTICAL="garage"
export AGENT_NAME="the-garage-agent"

echo "Starting vertical '$VERTICAL' as agent '$AGENT_NAME' ..."
uv run python -m agent.worker dev
