#!/bin/sh
set -eu

: "${ANIDOWN_NTFY_BASE_URL:?set ANIDOWN_NTFY_BASE_URL}"
: "${ANIDOWN_NTFY_TOKEN:?set ANIDOWN_NTFY_TOKEN}"
: "${ANIDOWN_NTFY_TOPIC:?set ANIDOWN_NTFY_TOPIC}"

curl -X POST "$ANIDOWN_NTFY_BASE_URL/$ANIDOWN_NTFY_TOPIC" \
  -H "Authorization: Bearer $ANIDOWN_NTFY_TOKEN" \
  -H "Title: Sonarr - Anime Downloader" \
  -H "Priority: low" \
  -H "Markdown: yes" \
  -d "$1" \
  --silent --show-error --output /dev/null
