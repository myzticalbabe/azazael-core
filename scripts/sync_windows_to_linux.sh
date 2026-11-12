#!/bin/bash
# sync_windows_to_linux.sh — One-way sync from Windows 11 → WSL2 Linux
#
# Source: /mnt/c/Azazael/azazael  (Windows canonical environment)
# Target: /root/azazael               (WSL2 Linux build environment)
#
# Windows is the canonical source of truth. This script ensures Linux
# stays in sync by copying new/modified files to avoid content drift.
#
# Usage:
#   ./scripts/sync_windows_to_linux.sh             Perform sync
#   ./scripts/sync_windows_to_linux.sh --status    Show file counts & drift
#   ./scripts/sync_windows_to_linux.sh --verify    Check sync integrity
#   ./scripts/sync_windows_to_linux.sh --dry-run   Preview changes
#   ./scripts/sync_windows_to_linux.sh --help      Show this help
#
# Created: June 15, 2026 — Cross-environment drift prevention

set -euo pipefail

# ── Color Codes ──────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()   { echo -e "${YELLOW}[!!]${NC} $1"; }
err()    { echo -e "${RED}[FAIL]${NC} $1"; }
header() { echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }

# ── Paths ────────────────────────────────────────────────────────────
WINDOWS_ROOT="/mnt/c/Azazael/azazael"
LINUX_ROOT="/root/azazael"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# ── Exclusion Patterns ──────────────────────────────────────────────
# Reasons for each exclusion:
#   nuitka_build_backup/  → 516 build artifacts, never needed on Linux
#   packages/             → 261 build output files (pip wheels, eggs)
#   *.mp4, *.wmv          → Large video media files (~1GB total)
#   *.bak                 → Editor backup files, not source
#   archived/             → Archived/compressed directories
#   archived_unified/     → Linux-only archive consolidation
#   workspace/            → Environment-specific SQLite databases
#   output/               → Generated output files, not source
#   .git/                 → Version control metadata (separate repos)
#   .venv*/               → Virtual environments (environment-specific)
#   __pycache__/, *.pyc   → Python bytecode cache
#   .mypy_cache/ etc.    → Tool/cache directories (environment-specific)
#   .vscode/              → IDE settings (environment-specific)
#   .github/, .githooks/  → CI/CD configuration (Windows-only)

EXCLUDE_PATTERNS=(
    "--exclude=nuitka_build_backup/"
    "--exclude=packages/"
    "--exclude=*.mp4"
    "--exclude=*.wmv"
    "--exclude=*.bak"
    "--exclude=archived/"
    "--exclude=archived_unified/"
    "--exclude=workspace/"
    "--exclude=output/"
    "--exclude=.git/"
    "--exclude=.venv*/"
    "--exclude=.mypy_cache/"
    "--exclude=.pytest_cache/"
    "--exclude=.inference_cache/"
    "--exclude=.debug/"
    "--exclude=.audit/"
    "--exclude=.agent/"
    "--exclude=__pycache__/"
    "--exclude=*.pyc"
    "--exclude=.vscode/"
    "--exclude=.github/"
    "--exclude=.githooks/"
    "--exclude=.coverage*"
    "--exclude=htmlcov/"
    "--exclude=*.egg-info/"
)

# ── Prerequisites ───────────────────────────────────────────────────
check_prereqs() {
    local fail=0
    if [ ! -d "$WINDOWS_ROOT" ]; then
        err "Windows project directory not found: $WINDOWS_ROOT"
        warn "Is the Windows filesystem mounted at /mnt/c?"
        fail=1
    fi
    if [ ! -d "$LINUX_ROOT" ]; then
        err "Linux project directory not found: $LINUX_ROOT"
        fail=1
    fi
    if ! command -v rsync &>/dev/null; then
        err "rsync is not installed. Install it: sudo apt install rsync"
        fail=1
    fi
    return $fail
}

# ── Status: Show file counts and drift ──────────────────────────────
show_status() {
    header "ENVIRONMENT STATUS"

    local win_files win_size lin_files lin_size

    # Windows file counts
    win_files=$(find "$WINDOWS_ROOT" -type f \
        -not -path '*/.git/*' \
        -not -path '*/__pycache__/*' \
        -not -name '*.pyc' \
        -not -path '*/.venv*/' \
        -not -path '*/nuitka_build_backup/*' \
        -not -path '*/packages/*' \
        2>/dev/null | wc -l)

    win_size=$(du -sh "$WINDOWS_ROOT" 2>/dev/null | awk '{print $1}')

    # Linux file counts
    lin_files=$(find "$LINUX_ROOT" -type f \
        -not -path '*/.git/*' \
        -not -path '*/__pycache__/*' \
        -not -name '*.pyc' \
        -not -path '*/.venv*/' \
        2>/dev/null | wc -l)

    lin_size=$(du -sh "$LINUX_ROOT" 2>/dev/null | awk '{print $1}')

    echo -e "  ${CYAN}Windows (canonical):${NC}"
    echo -e "    Path:   $WINDOWS_ROOT"
    echo -e "    Files:  $win_files"
    echo -e "    Size:   $win_size"
    echo
    echo -e "  ${CYAN}Linux (build):${NC}"
    echo -e "    Path:   $LINUX_ROOT"
    echo -e "    Files:  $lin_files"
    echo -e "    Size:   $lin_size"
    echo

    # Compute drift
    local drift=$((win_files - lin_files))
    if [ $drift -gt 0 ]; then
        warn "Drift: $drift files missing on Linux"
    elif [ $drift -eq 0 ]; then
        log "File counts match — no drift"
    else
        warn "Linux has $(( -drift )) files NOT on Windows"
    fi
}

# ── Sync: Copy missing/new files from Windows to Linux ──────────────
do_sync() {
    local dry_run_flag=""
    if [ "${1:-}" = "--dry-run" ]; then
        dry_run_flag="--dry-run"
        header "DRY RUN — Previewing sync"
    else
        header "SYNCING Windows → Linux"
    fi

    if [ ! -d "$WINDOWS_ROOT" ]; then
        err "Windows source not available at $WINDOWS_ROOT"
        exit 1
    fi

    echo -e "  Source:   $WINDOWS_ROOT"
    echo -e "  Target:   $LINUX_ROOT"
    if [ -n "$dry_run_flag" ]; then
        echo -e "  Mode:     DRY RUN (no files copied)"
    else
        echo -e "  Mode:     LIVE (--ignore-existing)"
    fi
    echo -e "  Started:  $TIMESTAMP"
    echo

    # rsync flags:
    #   -a                Archive mode (preserve permissions, timestamps, etc.)
    #   --info=progress2  Show overall progress summary
    #   --itemize-changes Show which files changed (e.g., "<f+++++++++ new")
    #   --ignore-existing Never overwrite existing Linux files
    #   --exclude         Skip build artifacts, caches, etc.
    rsync -a \
        --info=progress2 \
        --itemize-changes \
        --ignore-existing \
        $([ -n "$dry_run_flag" ] && echo -n "--dry-run") \
        "${EXCLUDE_PATTERNS[@]}" \
        "$WINDOWS_ROOT/" \
        "$LINUX_ROOT/"

    local rc=$?
    echo

    # Check rsync exit code
    #   0  = success (nothing to transfer or all files transferred)
    #   1  = syntax or usage error
    #   2  = protocol incompatibility
    #   3  = partial transfer (some files skipped due to errors)
    #   23 = partial transfer due to I/O errors
    if [ $rc -ne 0 ]; then
        warn "rsync completed with exit code $rc — some files may not have synced"
        return $rc
    fi

    if [ -z "$dry_run_flag" ]; then
        log "Sync completed at $(date '+%H:%M:%S')"
    else
        log "Dry run complete — no files were modified"
    fi

    return 0
}

# ── Verify: Check sync integrity ────────────────────────────────────
verify_sync() {
    header "VERIFYING SYNC INTEGRITY"
    echo -e "  Comparing key files between Windows and Linux...\n"

    local check_count=0
    local mismatch_count=0
    local missing_count=0

    # Check a representative set of files
    local check_files=(
        "pyproject.toml"
        "MEMORIES.md"
        "MASTER_TASKS.md"
        "MEMORY_PROTOCOL.md"
        "src/session_memory.py"
        "src/persistent_memory.py"
        "src/CORE/NODE.py"
        "src/CORE/STRUCTURE.py"
    )

    for rel_path in "${check_files[@]}"; do
        local win_file="$WINDOWS_ROOT/$rel_path"
        local lin_file="$LINUX_ROOT/$rel_path"
        check_count=$((check_count + 1))

        if [ ! -f "$win_file" ]; then
            warn "Windows: $rel_path — NOT FOUND (skipped)"
            continue
        fi

        if [ ! -f "$lin_file" ]; then
            err "Linux: $rel_path — MISSING"
            missing_count=$((missing_count + 1))
            continue
        fi

        # Compare checksums
        local win_hash lin_hash
        win_hash=$(md5sum "$win_file" 2>/dev/null | awk '{print $1}')
        lin_hash=$(md5sum "$lin_file" 2>/dev/null | awk '{print $1}')

        if [ "$win_hash" != "$lin_hash" ]; then
            err "$rel_path — CONTENT MISMATCH"
            mismatch_count=$((mismatch_count + 1))
        else
            log "$rel_path — OK"
        fi
    done

    echo
    if [ $mismatch_count -eq 0 ] && [ $missing_count -eq 0 ]; then
        log "All $check_count files in sync"
    else
        warn "$mismatch_count content mismatches, $missing_count missing files out of $check_count checked"
        return 1
    fi
}

# ── Help ─────────────────────────────────────────────────────────────
show_help() {
    cat <<'EOF'
╔══════════════════════════════════════════════════════════════════════╗
║  sync_windows_to_linux.sh — One-way Windows → WSL2 sync           ║
╚══════════════════════════════════════════════════════════════════════╝

Windows is the canonical source of truth. This script ensures the
WSL2 Linux environment stays in sync with the Windows project by
copying new/modified files using rsync --ignore-existing.

USAGE:
    ./scripts/sync_windows_to_linux.sh             Perform sync
    ./scripts/sync_windows_to_linux.sh --status    Show file counts & drift
    ./scripts/sync_windows_to_linux.sh --verify    Check sync integrity
    ./scripts/sync_windows_to_linux.sh --dry-run   Preview changes
    ./scripts/sync_windows_to_linux.sh --help      Show this help

EXCLUSIONS:
    Build artifacts:   nuitka_build_backup/, packages/
    Media files:       *.mp4, *.wmv (~1GB)
    Caches:            __pycache__/, .mypy_cache/, .pytest_cache/, etc.
    Environment:       .venv*/, .vscode/, .git/, workspace/
    Archives:          archived/, archived_unified/, output/

SYNC BEHAVIOR:
    Uses rsync --ignore-existing so existing Linux files are NEVER
    overwritten. Only NEW files (present on Windows, missing on Linux)
    are copied. This prevents losing Linux-specific changes.

RECOMMENDED USAGE:
    # Quick check if sync is needed
    ./scripts/sync_windows_to_linux.sh --status

    # Preview what would be synced
    ./scripts/sync_windows_to_linux.sh --dry-run

    # Perform the sync
    ./scripts/sync_windows_to_linux.sh

    # Verify integrity after sync
    ./scripts/sync_windows_to_linux.sh --verify
EOF
}

# ── Main ─────────────────────────────────────────────────────────────
main() {
    case "${1:-}" in
        --status)
            check_prereqs || exit 1
            show_status
            ;;
        --verify)
            check_prereqs || exit 1
            verify_sync
            ;;
        --dry-run)
            check_prereqs || exit 1
            do_sync "--dry-run"
            ;;
        --help|-h)
            show_help
            ;;
        "")
            check_prereqs || exit 1
            do_sync
            verify_sync
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
