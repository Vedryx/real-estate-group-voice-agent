#!/usr/bin/env bash
# Creates the LiveKit-side SIP inbound trunk + dispatch rule from the JSON
# configs in this directory. Run after:
#   1. Editing inbound-trunk.json and dispatch-rule.json with real values.
#   2. Completing the Telnyx-side setup in README.md (cannot be scripted -
#      requires the Telnyx portal).
#   3. `lk cloud auth` (or exporting LIVEKIT_URL/LIVEKIT_API_KEY/
#      LIVEKIT_API_SECRET) so the `lk` CLI can reach your LiveKit project.
#
# Requires the LiveKit CLI: https://github.com/livekit/livekit-cli
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v lk >/dev/null 2>&1; then
  echo "error: 'lk' CLI not found. Install: https://github.com/livekit/livekit-cli" >&2
  exit 1
fi

echo "Creating inbound SIP trunk..."
lk sip inbound create inbound-trunk.json

echo
echo "Copy the printed SipTrunkID into dispatch-rule.json's trunkIds, then run:"
echo "  lk sip dispatch create dispatch-rule.json"
