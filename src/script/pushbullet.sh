#!/bin/sh
set -eu

: "${ANIDOWN_PUSHBULLET_ACCESS_TOKEN:?set ANIDOWN_PUSHBULLET_ACCESS_TOKEN via a secret provider}"

curl -u "$ANIDOWN_PUSHBULLET_ACCESS_TOKEN:" "https://api.pushbullet.com/v2/pushes" \
  -d type=note \
  -d title="Sonarr - Anime Downloader" \
  -d body="${1:-}" \
  --silent --show-error --output /dev/null
