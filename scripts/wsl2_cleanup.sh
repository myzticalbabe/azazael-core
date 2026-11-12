#!/usr/bin/env bash
# =============================================================================
# AZAZAEL WSL2 Disk Cleanup Script
# =============================================================================
# Run:       ./azazael/scripts/wsl2_cleanup.sh
# Dry-run:   ./azazael/scripts/wsl2_cleanup.sh --dry-run
#
# Scheduling:
#   WSL2 cron does NOT auto-start. To schedule weekly:
#     1. sudo systemctl enable cron  # if using systemd
#     2. crontab -e
#     3. Add: 0 6 * * 1 /home/clint/azazael/scripts/wsl2_cleanup.sh
#   OR use Windows Task Scheduler to run:
#     wsl.exe -d Ubuntu-26.04 bash /home/clint/azazael/scripts/wsl2_cleanup.sh
# =============================================================================
set -euo pipefail

DRY_RUN=false
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
    echo "[AZAZAEL CLEANUP] DRY-RUN MODE — no files will be deleted"
fi

echo "[AZAZAEL CLEANUP] Starting WSL2 disk cleanup at $(date)"
echo ""

run() {
    if [ "$DRY_RUN" = true ]; then
        echo "  WOULD RUN: $*"
    else
        eval "$@" 2>/dev/null || true
    fi
}

# --- 1. Temp Directories ---
echo "[1] Cleaning /tmp/..."
run "sudo rm -rf /tmp/pip-* /tmp/tmp.* /tmp/Python-* /tmp/tf_* /tmp/tensorflow* /tmp/cudnn* /tmp/nvidia* /tmp/snap* /tmp/go* /tmp/vsc* /tmp/systemd* /tmp/kafka* /tmp/rocm* /tmp/amdgpu* 2>/dev/null || true"
echo "  Done"

# --- 2. pip Cache ---
echo "[2] Cleaning pip cache..."
run "rm -rf /home/clint/.cache/pip 2>/dev/null || true"
echo "  Done"

# --- 3. APT Cache ---
echo "[3] Cleaning apt cache..."
run "sudo apt-get clean -y 2>/dev/null || true"
run "sudo apt-get autoremove -y 2>/dev/null || true"
echo "  Done"

# --- 4. Build Artifacts (llama.cpp) ---
# Comment these out if you want to keep build caches
echo "[4] Cleaning llama.cpp build directories..."
run "rm -rf /home/clint/llama.cpp/build /home/clint/llama.cpp/build-cpu 2>/dev/null || true"
echo "  Done"

# --- 5. Old Linux Headers ---
echo "[5] Cleaning old kernel headers..."
run "sudo apt-get autoclean -y 2>/dev/null || true"
echo "  Done"

# --- 6. Journal Logs (keep last 7 days) ---
echo "[6] Trimming journal logs..."
run "sudo journalctl --vacuum-time=7d 2>/dev/null || true"
echo "  Done"

# --- 7. Docker (if installed) ---
if command -v docker &>/dev/null; then
    echo "[7] Cleaning Docker..."
    run "docker system prune -f --volumes 2>/dev/null || true"
    echo "  Done"
else
    echo "[7] Docker not installed, skipping"
fi

# --- Summary ---
echo ""
echo "[AZAZAEL CLEANUP] Complete at $(date)"
if [ "$DRY_RUN" = true ]; then
    echo "(dry-run — no changes made)"
fi
echo "Disk usage:"
df -h / 2>/dev/null | tail -1
