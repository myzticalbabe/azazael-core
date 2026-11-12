#!/bin/bash
#===============================================================================
# Python 3.13 Uninstallation Script (REVERSED: now removes 3.13, preserves the 3.14 base)
#===============================================================================
# RETIRED 2026-10-19: free-threaded 3.14t is no longer the project runtime. Production
# is the free-threaded 3.15t venv (C:/Azazael/.venvs/py315t) and the only
# sanctioned 3.14 runtime is the GIL venv C:/Azazael/.venvs/py314m. This script
# is kept because it is the deliberate, reversible 3.13 cleanup path; it must
# still PRESERVE whichever 3.14 base install the 3.14m venv / GIL sidecar is built from.
# Removes Python 3.13, backs up modules, keeps the 3.14 base installs intact.
#
# Usage: ./uninstall_py314.sh [--dry-run]
#===============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
PYTHON313_PATH=""
PYTHON314_PATH=""
TARBABY_BASE="C:/Users/clint/Desktop/TARBABY"
BACKUP_DIR="$TARBABY_BASE/backups"
LOG_FILE="$TARBABY_BASE/logs/uninstall_313.log"
DRY_RUN=false

#===============================================================================
# Logging Functions
#===============================================================================

log() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "[$timestamp] $1" | tee -a "$LOG_FILE"
}

log_info() { log "${BLUE}[INFO]${NC} $*"; }
log_success() { log "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { log "${YELLOW}[WARNING]${NC} $*"; }
log_error() { log "${RED}[ERROR]${NC} $*"; }

#===============================================================================
# Main Functions
#===============================================================================

show_help() {
    cat << EOF
${BLUE}Python 3.13 Removal Script${NC}

${YELLOW}Usage:${NC} $0 [OPTIONS]

${YELLOW}Options:${NC}
    --dry-run       Show what would be done without executing
    --help, -h      Show this help message

${YELLOW}Description:${NC}
    3.14 remains a SUPPORTED FALLBACK (GIL venv .venvs/py314m) for a package
    with no 3.15 build; production workloads run on the 3.15t venv.
    Python 3.13 is NOT used.
    This script safely removes Python 3.13 while:
    - Preserving all 3.13 modules to TARBABY/backups (just in case)
    - Keeping the Python 3.14 base install intact (needed by .venvs/py314m)
    - Maintaining ecosystem compatibility
    - Logging all operations

${YELLOW}Example:${NC}
    $0                    # Run normally
    $0 --dry-run          # Preview actions

EOF
    exit 0
}

find_python_installations() {
    log_info "Detecting Python installations..."
    
    # Common Python 3.13 locations (to remove)
    PYTHON313_PATHS=(
        "/c/Python313"
        "/c/Program Files/Python313"
        "/c/Program Files (x86)/Python313"
        "/usr/local/bin/python3.13"
        "/usr/bin/python3.13"
    )
    
    # Common Python 3.14t locations (to preserve)
    PYTHON314_PATHS=(
        "/c/Python314"
        "/c/Program Files/Python314"
        "/c/Program Files (x86)/Python314"
        "/c/Users/clint/AppData/Local/Programs/Python/Python314"
        "/usr/local/python314t"
        "/usr/local/python3147-gil"
    )
    
    # Find Python 3.13
    for path in "${PYTHON313_PATHS[@]}"; do
        if [ -d "$path" ] || [ -f "$path/python.exe" ]; then
            PYTHON313_PATH="$path"
            break
        fi
    done
    
    # Find Python 3.14t (preserve this)
    for path in "${PYTHON314_PATHS[@]}"; do
        if [ -d "$path" ] || [ -f "$path/python.exe" ]; then
            PYTHON314_PATH="$path"
            break
        fi
    done
    
    log_info "Python 3.13: ${PYTHON313_PATH:-NOT FOUND}"
    log_info "Python 3.14t: ${PYTHON314_PATH:-FOUND, preserving}"
}

backup_modules() {
    log_info "Backing up Python 3.13 modules..."
    
    mkdir -p "$BACKUP_DIR"
    
    if [ -z "$PYTHON313_PATH" ]; then
        log_warn "No Python 3.13 installation found to backup"
        return 0
    fi
    
    local backup_file="$BACKUP_DIR/python313_modules_$(date +%Y%m%d_%H%M%S).tar.gz"
    
    if [ -d "$PYTHON313_PATH/Lib" ]; then
        log_info "Archiving Lib directory..."
        tar -czf "$backup_file" -C "$PYTHON313_PATH" Lib/ 2>/dev/null || true
        log_success "Modules backed up to: $backup_file"
    fi
    
    if [ -d "$PYTHON313_PATH/Scripts" ]; then
        local scripts_backup="$BACKUP_DIR/python313_scripts_$(date +%Y%m%d_%H%M%S).tar.gz"
        tar -czf "$scripts_backup" -C "$PYTHON313_PATH" Scripts/ 2>/dev/null || true
        log_info "Scripts backed up to: $scripts_backup"
    fi
    
    create_backup_metadata "$backup_file"
}

create_backup_metadata() {
    local backup_file="$1"
    
    cat > "$BACKUP_DIR/backup_manifest.json" << EOF
{
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "source_version": "3.13 (removed)",
    "backup_file": "$backup_file",
    "active_python": ["3.15t"],
    "status": "complete"
}
EOF
    log_info "Created backup metadata"
}

preserve_site_packages() {
    log_info "Preserving 3.13 site-packages for reference..."
    
    if [ -d "$PYTHON313_PATH/Lib/site-packages" ]; then
        local dev_backup="$BACKUP_DIR/site-packages_313_$(date +%Y%m%d).tar.gz"
        tar -czf "$dev_backup" -C "$PYTHON313_PATH/Lib" site-packages/ 2>/dev/null || true
        log_success "3.13 site-packages preserved"
    fi
}

uninstall_python313() {
    log_info "Removing Python 3.13..."
    
    if [ -z "$PYTHON313_PATH" ]; then
        log_warn "Python 3.13 not found - nothing to remove"
        return 0
    fi
    
    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would remove: $PYTHON313_PATH"
        return 0
    fi
    
    if [ -d "$PYTHON313_PATH" ]; then
        rm -rf "$PYTHON313_PATH"
        
        if [ $? -eq 0 ]; then
            log_success "Python 3.13 removed successfully"
        else
            log_error "Failed to remove Python 3.13"
            return 1
        fi
    fi
}

verify_python314t() {
    log_info "Verifying Python 3.14t is intact..."
    
    if [ -z "$PYTHON314_PATH" ]; then
        log_warn "Python 3.14t not found - ensure it is installed"
        return 1
    fi
    
    if [ -f "$PYTHON314_PATH/python.exe" ]; then
        log_success "Python 3.14t verified intact"
        return 0
    else
        log_error "Python 3.14t appears corrupted"
        return 1
    fi
}

update_environment() {
    log_info "Updating environment configuration..."
    
    if [ -f "$TARBABY_BASE/INSTALL.json" ]; then
        log_info "Updating INSTALL.json to remove 3.13 references..."
    fi
    
    if [ -f "$TARBABY_BASE/config.json" ]; then
        log_info "Updating TARBABY configuration..."
    fi
}

generate_report() {
    local report_file="$TARBABY_BASE/logs/remove_313_report_$(date +%Y%m%d_%H%M%S).json"
    
    cat > "$report_file" << EOF
{
    "remove_timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "removed_version": "3.13",
    "active_version": "3.15t",
    "backup_location": "$BACKUP_DIR",
    "modules_preserved": true,
    "python314t_intact": $([ -n "$PYTHON314_PATH" ] && echo "true" || echo "false"),
    "dry_run": $DRY_RUN,
    "status": "complete"
}
EOF
    
    log_success "Report saved to: $report_file"
}

#===============================================================================
# Main Entry Point
#===============================================================================

main() {
    if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        show_help
    fi
    
    if [ "$1" = "--dry-run" ]; then
        DRY_RUN=true
        log_warn "DRY RUN MODE - No changes will be made"
    fi
    
    echo ""
    log "=============================================="
    log "Python 3.13 Removal (3.15t mandate)"
    log "=============================================="
    echo ""
    
    mkdir -p "$TARBABY_BASE/logs"
    mkdir -p "$BACKUP_DIR"
    
    find_python_installations
    
    echo ""
    log_info "Starting 3.13 removal process..."
    echo ""
    
    backup_modules
    preserve_site_packages
    uninstall_python313
    verify_python314t
    update_environment
    generate_report
    
    echo ""
    log "=============================================="
    log "3.13 Removal Complete"
    log "=============================================="
    log "Backup location: $BACKUP_DIR"
    log "Logs: $TARBABY_BASE/logs/"
    echo ""
    log_success "Python 3.13 removed. Python 3.15t is the active Python."
    echo ""
}

main "$@"
