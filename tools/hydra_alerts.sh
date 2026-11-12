#!/usr/bin/env bash
# HYDRA Desktop Alerts — Notifies on stage completion
# Requires: notify-send (part of libnotify-bin)

HYDRA_DIR="$HOME/Desktop/azazael-project-e"
LAST_STATUS=""

while true; do
    STATUS=$(cd "$HYDRA_DIR" && python3 HYDRA.py --status 2>/dev/null)
    
    if [ "$STATUS" != "$LAST_STATUS" ] && [ -n "$LAST_STATUS" ]; then
        notify-send "🛰️ HYDRA Status Changed" "$STATUS" -i dialog-information
    fi
    
    LAST_STATUS="$STATUS"
    sleep 60  # Check every 60 seconds
done
