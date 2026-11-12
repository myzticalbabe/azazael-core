#!/bin/bash
# ============================================================================
# AZAZAEL HYDRA COMMAND - Linux/Debian/WSL
# Usage: ./hydra.sh [stage]
# ============================================================================

export AZAZAEL_ROOT="${AZAZAEL_ROOT:-$HOME/Desktop/Azazael}"

case "${1:-}" in
    --help|-h|"")
        echo "AZAZAEL HYDRA - ML Cluster Processor"
        echo ""
        echo "Usage: hydra.sh [stage]"
        echo ""
        echo "Stages:"
        echo "  all         - Run full 6-stage cycle"
        echo "  discovery   - Discovery stage"
        echo "  analysis    - Analysis stage"
        echo "  optimization - Optimization stage"
        echo "  validation  - Validation stage"
        echo "  deployment  - Deployment stage"
        echo "  feedback    - Feedback stage"
        echo "  --help      - Show this help"
        ;;
    all)
        python3 "$AZAZAEL_ROOT/HYDRA/HYDRA.py" --all
        ;;
    discovery|analysis|optimization|validation|deployment|feedback)
        python3 "$AZAZAEL_ROOT/HYDRA/HYDRA.py" --stage "$1"
        ;;
    *)
        echo "Unknown stage: $1"
        echo "Use --help for usage"
        exit 1
        ;;
esac
