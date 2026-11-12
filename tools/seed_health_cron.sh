#!/usr/bin/env bash
# Daily Azazael Health Check — Cron Script
# Add to crontab:  crontab -e
# Then add: 0 8 * * * /home/myzticalbabe/Desktop/seed_health_cron.sh

LOGFILE="/home/myzticalbabe/.azazael_health.log"
DATE=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$DATE] Starting daily health check..." >> "$LOGFILE"

# 1. Import test
cd /home/myzticalbabe/Desktop
KERAS_BACKEND=torch python3 test_azazael_imports.py >> "$LOGFILE" 2>&1
echo "" >> "$LOGFILE"

# 2. Azazael module health
KERAS_BACKEND=torch python3 health_check_azazael.py >> "$LOGFILE" 2>&1
echo "" >> "$LOGFILE"

# 3. Disk usage
echo "=== DISK USAGE ===" >> "$LOGFILE"
df -h / >> "$LOGFILE" 2>&1
echo "" >> "$LOGFILE"

# 4. Memory
echo "=== MEMORY ===" >> "$LOGFILE"
free -h >> "$LOGFILE" 2>&1
echo "" >> "$LOGFILE"

# 5. pip check
echo "=== PIP CHECK ===" >> "$LOGFILE"
pip3 check >> "$LOGFILE" 2>&1
echo "" >> "$LOGFILE"

echo "[$DATE] Health check complete." >> "$LOGFILE"
echo "---" >> "$LOGFILE"

# Keep only last 1000 lines
tail -n 1000 "$LOGFILE" > "${LOGFILE}.tmp" && mv "${LOGFILE}.tmp" "$LOGFILE"
