#!/bin/bash
# ============================================================================
# AZAZAEL OPTIMIZER COMMAND
# Usage: ./optimizer.sh [command]
# ============================================================================

export AZAZAEL_ROOT="${AZAZAEL_ROOT:-$HOME/Desktop/Azazael}"

case "${1:-}" in
    --help|-h|"")
        echo "ECOSYSTEM OPTIMIZER"
        echo ""
        echo "Usage: optimizer.sh [command]"
        echo ""
        echo "Commands:"
        echo "  run         - Run full optimization"
        echo "  analyze     - Analyze batch files"
        echo "  status      - Show optimizer status"
        echo "  --help      - Show this help"
        ;;
    run)
        python3 "$AZAZAEL_ROOT/ECOSYSTEM_OPTIMIZER.py" -o
        ;;
    analyze)
        python3 "$AZAZAEL_ROOT/BAT_OPTIMIZATION_ANALYSIS.py"
        ;;
    status)
        python3 "$AZAZAEL_ROOT/ECOSYSTEM_OPTIMIZER.py" --status
        ;;
    *)
        python3 "$AZAZAEL_ROOT/ECOSYSTEM_OPTIMIZER.py" -o
        ;;
esac
