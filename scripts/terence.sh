#!/bin/bash
# ============================================================================
# AZAZAEL TERENCE COMMAND - Error Resolver
# Usage: ./terence.sh [command]
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export AZAZAEL_ROOT="${AZAZAEL_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
TERENCE_PY="$AZAZAEL_ROOT/src/UTILITIES/TERENCE/TERENCE.py"

case "${1:-}" in
    --help|-h|"")
        echo "TERENCE - Error Resolution System"
        echo ""
        echo "Usage: terence.sh [command]"
        echo ""
        echo "Commands:"
        echo "  scan        - Scan for errors"
        echo "  fix         - Auto-fix errors"
        echo "  report      - Generate error report"
        echo "  --help      - Show this help"
        ;;
    scan|fix|report)
        python3 "$TERENCE_PY" --${1}
        ;;
    *)
        python3 "$TERENCE_PY" --scan
        ;;
esac
