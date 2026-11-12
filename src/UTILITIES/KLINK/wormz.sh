#!/bin/bash
# ============================================================================
# AZAZAEL WORMZ COMMAND - Linux/Debian/WSL
# Usage: ./wormz.sh [command]
# ============================================================================

export AZAZAEL_ROOT="${AZAZAEL_ROOT:-$HOME/Desktop/Azazael}"

case "${1:-}" in
    --help|-h|"")
        echo "AZAZAEL WORMZ - System Worms"
        echo ""
        echo "Usage: wormz.sh [command]"
        echo ""
        echo "Commands:"
        echo "  systemz   - System information"
        echo "  processz  - Process management"
        echo "  diskz     - Disk analysis"
        echo "  networkz  - Network analysis"
        echo "  clockz    - Time synchronization"
        echo "  all       - Run all worms"
        echo "  --help    - Show this help"
        ;;
    systemz)
        python3 "$AZAZAEL_ROOT/WORMZ/SYSTEMZ.py" --status
        ;;
    processz)
        python3 "$AZAZAEL_ROOT/WORMZ/PROCESSZ.py" --status
        ;;
    diskz)
        python3 "$AZAZAEL_ROOT/WORMZ/DISKZ.py" --status
        ;;
    networkz)
        python3 "$AZAZAEL_ROOT/WORMZ/NETWORXZ.py" --status
        ;;
    clockz)
        python3 "$AZAZAEL_ROOT/WORMZ/CLOCKZ.py" --status
        ;;
    all)
        echo "Running all worms..."
        python3 "$AZAZAEL_ROOT/WORMZ/CONCURRENCY.py" --scan
        ;;
    *)
        echo "Unknown command: $1"
        echo "Use --help for usage"
        exit 1
        ;;
esac
