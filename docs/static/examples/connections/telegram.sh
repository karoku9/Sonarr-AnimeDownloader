#!/bin/sh
set -eu

: "${ANIDOWN_TELEGRAM_BOT_TOKEN:?set ANIDOWN_TELEGRAM_BOT_TOKEN}"
: "${ANIDOWN_TELEGRAM_CHAT_ID:?set ANIDOWN_TELEGRAM_CHAT_ID}"

curl -X POST \
  -H 'Content-Type: application/json' \
  -d "{\"chat_id\":\"$ANIDOWN_TELEGRAM_CHAT_ID\",\"text\":\"$1\"}" \
  "https://api.telegram.org/bot$ANIDOWN_TELEGRAM_BOT_TOKEN/sendMessage" \
  --silent --show-error --output /dev/null
