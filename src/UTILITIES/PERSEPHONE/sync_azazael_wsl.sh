#!/bin/bash
# =============================================================================
# Azazael <-> PERSEPHONE WSL Sync Script
#
# Sync files between Azazael and PERSEPHONE for cross-platform development
#
# Usage:
#   ./sync_azazael_wsl.sh           # Interactive menu
#   ./sync_azazael_wsl.sh to-pers   # Azazael -> PERSEPHONE
#   ./sync_azazael_wsl.sh from-pers # PERSEPHONE -> Azazael
#   ./sync_azazael_wsl.sh both      # Bidirectional
#   ./sync_azazael_wsl.sh dry       # Compare only
# =============================================================================

set -e

# Configuration
AZAZAEL_ROOT="/mnt/c/Users/clint/Desktop/Azazael"
PERSEPHONE_ROOT="/mnt/c/Users/clint/Desktop/PERSEPHONE"
SYNC_LOG="${HOME}/.persephone/sync_log.txt"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

ensure_dirs() {
    mkdir -p "$(dirname "$SYNC_LOG")"
}

sync_to_pers() {
    log_info "Syncing Azazael -> PERSEPHONE..."
    log_info "Source: $AZAZAEL_ROOT"
    log_info "Target: $PERSEPHONE_ROOT"
    echo

    rsync -avz --progress \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        --exclude='*.log' \
        --exclude='node_modules' \
        --exclude='.git' \
        --exclude='*.egg-info' \
        "$AZAZAEL_ROOT/" "$PERSEPHONE_ROOT/"

    log_success "Sync complete"
}

sync_from_pers() {
    log_info "Syncing PERSEPHONE -> Azazael..."
    log_info "Source: $PERSEPHONE_ROOT"
    log_info "Target: $AZAZAEL_ROOT"
    echo

    rsync -avz --progress \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        --exclude='*.log' \
        --exclude='node_modules' \
        --exclude='.git' \
        --exclude='*.egg-info' \
        "$PERSEPHONE_ROOT/" "$AZAZAEL_ROOT/"

    log_success "Sync complete"
}

sync_both() {
    log_info "Bidirectional sync..."
    echo

    log_info "Step 1: Azazael -> PERSEPHONE"
    rsync -avz \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        --exclude='*.log' \
        --exclude='node_modules' \
        "$AZAZAEL_ROOT/" "$PERSEPHONE_ROOT/"

    log_info "Step 2: PERSEPHONE -> Azazael"
    rsync -avz \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        --exclude='*.log' \
        --exclude='node_modules' \
        "$PERSEPHONE_ROOT/" "$AZAZAEL_ROOT/"

    log_success "Bidirectional sync complete"
}

dry_run() {
    log_info "Dry run - comparing directories..."
    echo

    echo "Azazael -> PERSEPHONE comparison:"
    rsync -avz --dry-run \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        --exclude='*.log' \
        --exclude='node_modules' \
        "$AZAZAEL_ROOT/" "$PERSEPHONE_ROOT/" | head -20

    echo
    echo "PERSEPHONE -> Azazael comparison:"
    rsync -avz --dry-run \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        --exclude='*.log' \
        --exclude='node_modules' \
        "$PERSEPHONE_ROOT/" "$AZAZAEL_ROOT/" | head -20

    log_info "Dry run complete - no files were copied"
}

show_status() {
    echo
    echo "================================================================================"
    echo "Azazael <-> PERSEPHONE Sync Status"
    echo "================================================================================"
    echo
    echo "Azazael:  $AZAZAEL_ROOT"
    echo "PERSEPHONE: $PERSEPHONE_ROOT"
    echo
    echo "Azazael files:"
    find "$AZAZAEL_ROOT" -name "*.py" 2>/dev/null | wc -l | xargs echo "  Python files:"
    find "$AZAZAEL_ROOT" -type f 2>/dev/null | wc -l | xargs echo "  Total files:"
    echo
    echo "PERSEPHONE files:"
    find "$PERSEPHONE_ROOT" -name "*.py" 2>/dev/null | wc -l | xargs echo "  Python files:"
    find "$PERSEPHONE_ROOT" -type f 2>/dev/null | wc -l | xargs echo "  Total files:"
    echo
}

interactive_menu() {
    echo
    echo "================================================================================"
    echo "Azazael <-> PERSEPHONE Sync"
    echo "================================================================================"
    echo
    echo "Options:"
    echo "  [1] Sync Azazael -> PERSEPHONE"
    echo "  [2] Sync PERSEPHONE -> Azazael"
    echo "  [3] Bidirectional Sync"
    echo "  [4] Compare Only (Dry Run)"
    echo "  [5] Show Status"
    echo "  [6] Exit"
    echo
    read -p "Select option [1-6]: " CHOICE

    case $CHOICE in
        1) sync_to_pers ;;
        2) sync_from_pers ;;
        3) sync_both ;;
        4) dry_run ;;
        5) show_status ;;
        6) echo "Exiting..." ;;
        *) echo "Invalid choice" ;;
    esac
}

# Main
ensure_dirs

if [ $# -gt 0 ]; then
    case $1 in
        to-pers) sync_to_pers ;;
        from-pers) sync_from_pers ;;
        both) sync_both ;;
        dry) dry_run ;;
        status) show_status ;;
        *) echo "Usage: $0 [to-pers|from-pers|both|dry|status]" ;;
    esac
else
    interactive_menu
fi
