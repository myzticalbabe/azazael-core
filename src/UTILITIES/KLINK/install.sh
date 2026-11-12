#!/bin/bash
# ============================================================================
# AZAZAEL INSTALL COMMAND
# Usage: ./install.sh [options]
# ============================================================================

export AZAZAEL_ROOT="${AZAZAEL_ROOT:-$HOME/Desktop/Azazael}"

echo "AZAZAEL Installation Script"
echo "==========================="
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found!"
    exit 1
fi
echo "[OK] Python 3 found: $(python3 --version)"

# Check Azazael directory
if [ ! -d "$AZAZAEL_ROOT" ]; then
    echo "ERROR: Azazael directory not found: $AZAZAEL_ROOT"
    exit 1
fi
echo "[OK] Azazael directory found: $AZAZAEL_ROOT"

# Run auto_install if exists
if [ -f "$AZAZAEL_ROOT/ml_platforms/auto_install.py" ]; then
    echo ""
    echo "Running auto_install..."
    python3 "$AZAZAEL_ROOT/ml_platforms/auto_install.py"
fi

# Create symlinks in /usr/local/bin for global access
if [ -d "/usr/local/bin" ]; then
    echo ""
    echo "Creating symlinks in /usr/local/bin..."
    for script in azazael.sh wormz.sh hydra.sh ecosystem.sh terence.sh optimizer.sh; do
        if [ -f "$AZAZAEL_ROOT/../KLINK/$script" ]; then
            ln -sf "$AZAZAEL_ROOT/../KLINK/$script" "/usr/local/bin/${script%.sh}"
            echo "[OK] ${script%.sh} -> /usr/local/bin"
        fi
    done
fi

echo ""
echo "Installation complete!"
echo ""
echo "Usage: Run ./KLINK/azazael.sh --help for commands"
