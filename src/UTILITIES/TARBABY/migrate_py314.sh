#!/bin/bash
#===============================================================================
# TARBABY Migration Script
#===============================================================================
# Migrates Python modules for cross-version compatibility
#
# RETIRED 2026-10-19: 3.14t is no longer a project runtime. Production is the
# 3.15t venv (C:/Azazael/.venvs/py315t); the only sanctioned 3.14 runtime is
# the GIL venv C:/Azazael/.venvs/py314m. This migration helper is kept for the
# deliberate case of moving modules onto that fallback, not as a default step.
#
# Usage: ./migrate_py314.sh [source_path] [target_version]

set -e

# Configuration
TARBABY_BASE="C:/Users/clint/Desktop/TARBABY"
PYTHON_SOURCE=""
TARGET_VERSION="3.14"
LOG_FILE="$TARBABY_BASE/logs/migration.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "[$timestamp] $1" | tee -a "$LOG_FILE"
}

log_success() {
    log "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    log "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    log "${RED}[ERROR]${NC} $1"
}

#===============================================================================
# Main Migration Function
#===============================================================================

migrate_python314() {
    local source_path="$1"
    local target_version="${2:-3.11}"
    
    log "=============================================="
    log "TARBABY Python 3.14 Migration"
    log "=============================================="
    log "Source: $source_path"
    log "Target Version: $target_version"
    log "Target: $TARBABY_BASE"
    log "Note: Python 3.14t (free-threaded) is the primary target"
    
    # Create directories
    mkdir -p "$TARBABY_BASE/output"
    mkdir -p "$TARBABY_BASE/temp"
    mkdir -p "$TARBABY_BASE/metadata"
    mkdir -p "$TARBABY_BASE/logs"
    
    # Check source
    if [ ! -d "$source_path" ]; then
        log_error "Source path does not exist: $source_path"
        exit 1
    fi
    
    # Count files
    local py_files=$(find "$source_path" -name "*.py" 2>/dev/null | wc -l)
    log "Found $py_files Python files"
    
    local migrated=0
    local errors=0
    
    # Migrate each Python file
    while IFS= read -r -d '' file; do
        local rel_path="${file#$source_path/}"
        local target_file="$TARBABY_BASE/output/$rel_path"
        
        # Create target directory
        mkdir -p "$(dirname "$target_file")"
        
        # Copy file (no version-specific transforms needed for 3.14t target)
        if convert_python_syntax "$file" "$target_file" "$target_version"; then
            ((migrated++))
        else
            ((errors++))
            log_warn "Failed to migrate: $rel_path"
        fi
        
        # Progress indicator
        if [ $((migrated % 10)) -eq 0 ]; then
            log "Progress: $migrated files migrated..."
        fi
        
    done < <(find "$source_path" -name "*.py" -print0)
    
    # Create archive
    local archive_name="python314_migration_$(date +%Y%m%d_%H%M%S)"
    create_archive "$archive_name"
    
    # Create metadata
    create_metadata "$source_path" "$migrated" "$errors" "$target_version"
    
    log "=============================================="
    log "Migration Complete"
    log "=============================================="
    log "Files migrated: $migrated"
    log "Errors: $errors"
    log "Archive: $archive_name.tar.gz"
    log "Log: $LOG_FILE"
    
    return $errors
}

#===============================================================================
# Helper Functions
#===============================================================================

convert_python_syntax() {
    local input_file="$1"
    local output_file="$2"
    local target_version="$3"
    
    # Read file
    local content=$(cat "$input_file")
    
    # Copy file as-is (project targets Python 3.14t)
    cp "$input_file" "$output_file"
    return 0
}

create_archive() {
    local name="$1"
    local archive_dir="$TARBABY_BASE/output"
    
    cd "$archive_dir"
    tar -czf "${name}.tar.gz" output/ 2>/dev/null
    log "Archive created: ${name}.tar.gz"
}

create_metadata() {
    local source="$1"
    local migrated="$2"
    local errors="$3"
    local version="$4"
    
    local timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    
    cat > "$TARBABY_BASE/metadata/latest_migration.json" << EOF
{
    "timestamp": "$timestamp",
    "source": "$source",
    "target_version": "$version",
    "files_migrated": $migrated,
    "errors": $errors,
    "status": "complete"
}
EOF
}

show_help() {
    cat << EOF
TARBABY Migration Script
======================

Usage: $0 <source_path> [target_version]

Arguments:
    source_path      Path to Python modules
    target_version   Target Python version (default: 3.14)

Examples:
    $0 C:/Python314/Lib
    $0 C:/Python314/Scripts 3.14

Supported target versions:
    3.12, 3.13, 3.14

EOF
}

#===============================================================================
# Entry Point
#===============================================================================

main() {
    if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        show_help
        exit 0
    fi
    
    if [ -z "$1" ]; then
        log_error "Source path required"
        show_help
        exit 1
    fi
    
    migrate_python314 "$1" "${2:-3.14}"
}

main "$@"
