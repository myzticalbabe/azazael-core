#!/usr/bin/env bash
# Azazael Archive Monitor — Watches the G: archive for changes and reports.
# Usage:  ./azazael-monitor.sh [interval_seconds]

INTERVAL="${1:-300}"  # Default: check every 5 minutes
ARCHIVE="/mnt/windows/g/AZAZAEL"
STATE_FILE="/tmp/azazael_archive_state.txt"
LOG_FILE="$HOME/.azazael_archive_monitor.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

if [ ! -d "$ARCHIVE" ]; then
    log "❌ Archive not found at $ARCHIVE. Is G: mounted?"
    exit 1
fi

log "📡 Starting archive monitor (interval: ${INTERVAL}s)"
log "   Archive: $ARCHIVE"
log "   Log:     $LOG_FILE"

# Initial scan
find "$ARCHIVE" -type f -not -path '*__pycache__*' | sort > "$STATE_FILE"
log "   Initial state recorded: $(wc -l < "$STATE_FILE") files"

while true; do
    sleep "$INTERVAL"
    
    NEW_FILE=$(mktemp)
    find "$ARCHIVE" -type f -not -path '*__pycache__*' | sort > "$NEW_FILE"
    
    ADDED=$(comm -13 "$STATE_FILE" "$NEW_FILE")
    REMOVED=$(comm -23 "$STATE_FILE" "$NEW_FILE")
    
    if [ -n "$ADDED" ]; then
        echo "$ADDED" | while IFS= read -r f; do
            log "➕ Added: ${f#$ARCHIVE/}"
        done
    fi
    
    if [ -n "$REMOVED" ]; then
        echo "$REMOVED" | while IFS= read -r f; do
            log "➖ Removed: ${f#$ARCHIVE/}"
        done
    fi
    
    mv "$NEW_FILE" "$STATE_FILE"
done
