#!/usr/bin/env bash
# moctar-listen.sh : Raccourci global système (Super+Espace) pour M.O.C.T.A.R sur Dell Precision M4800
# Déclenche l'écoute vocale instantanée même avec Chrome fermé ou minimisé.

set -e

PIPE="/tmp/moctar_voice.pipe"
API_URL="http://127.0.0.1:5050/api/voice/trigger"

if [ -p "$PIPE" ]; then
    echo "wake" > "$PIPE" 2>/dev/null || true
else
    curl -s -X POST "$API_URL" -H "Content-Type: application/json" -d '{"source": "os_shortcut"}' >/dev/null 2>&1 || true
fi

