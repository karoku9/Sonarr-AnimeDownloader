#!/bin/sh
set -eu

: "${ANIDOWN_PUSHOVER_APP_TOKEN:?set ANIDOWN_PUSHOVER_APP_TOKEN via a secret provider}"
: "${ANIDOWN_PUSHOVER_USER_KEY:?set ANIDOWN_PUSHOVER_USER_KEY via a secret provider}"

curl \
  --form-string "token=$ANIDOWN_PUSHOVER_APP_TOKEN" \
  --form-string "user=$ANIDOWN_PUSHOVER_USER_KEY" \
  --form-string "message=${1:-}" \
  --silent --show-error --output /dev/null \
  "https://api.pushover.net/1/messages.json"
