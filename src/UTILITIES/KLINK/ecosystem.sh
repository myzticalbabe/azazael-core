#!/bin/bash
# ============================================================================
# AZAZAEL ECOSYSTEM COMMAND - Linux/Debian/WSL
# Usage: ./ecosystem.sh [command]
# ============================================================================

export AZAZAEL_ROOT="${AZAZAEL_ROOT:-$HOME/Desktop/Azazael}"

case "${1:-}" in
    --help|-h|"")
        echo "AZAZAEL ECOSYSTEM - Unified Command Interface"
        echo ""
        echo "Usage: ecosystem.sh [command]"
        echo ""
        echo "Commands:"
        echo "  status      - Show ecosystem status"
        echo "  worms       - Run all WORMZ"
        echo "  metaworms   - Run all METAWORMS"
        echo "  hydra       - Run full HYDRA cycle"
        echo "  terence     - TERENCE error resolver"
        echo "  horus       - HORUS output formatter"
        echo "  tarbaby     - Binary builder"
        echo "  poindexter  - Mathematical indexer"
        echo "  persephone  - Cross-platform utilities"
        echo "  optimize    - Run optimizer"
        echo "  all         - Full ecosystem"
        echo "  --help      - Show this help"
        ;;
    status)
        python3 "$AZAZAEL_ROOT/AZAZAEL_INTEGRATION.py" --status
        ;;
    worms)
        python3 "$AZAZAEL_ROOT/WORMZ/CONCURRENCY.py" --scan
        ;;
    metaworms)
        python3 "$AZAZAEL_ROOT/METAWORMS/HYDROMETA.py" --all
        ;;
    hydra)
        python3 "$AZAZAEL_ROOT/HYDRA/HYDRA.py" --all
        ;;
    terence)
        python3 "$AZAZAEL_ROOT/TERENCE/TERENCE.py" --scan
        ;;
    horus)
        python3 "$AZAZAEL_ROOT/HORUS/HORUS.py"
        ;;
    tarbaby)
        python3 "$AZAZAEL_ROOT/TARBABY/TARBABY.py" --test
        ;;
    poindexter)
        python3 "$AZAZAEL_ROOT/POINDEXTER/POINDEXTER.py"
        ;;
    persephone)
        python3 "$AZAZAEL_ROOT/PERSEPHONE/persephone.py"
        ;;
    optimize)
        python3 "$AZAZAEL_ROOT/ECOSYSTEM_OPTIMIZER.py" -o
        ;;
    all)
        python3 "$AZAZAEL_ROOT/AZAZAEL_INTEGRATION.py"
        ;;
    *)
        echo "Unknown command: $1"
        echo "Use --help for usage"
        exit 1
        ;;
esac
