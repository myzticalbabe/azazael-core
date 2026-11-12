#!/usr/bin/env bash
# Detached background launcher for graphify_refresh_all.sh.
# Usage: bash start_refresh_background.sh
nohup bash "$(dirname "$0")/graphify_refresh_all.sh" > /tmp/refresh_all.log 2>&1 &
echo "refresh started in background (log: /tmp/refresh_all.log)"
