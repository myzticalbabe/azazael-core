# @echo off
# Azazael Unified - Git Bash Setup Script
# Run this in Git Bash or CMD to configure the bash environment
# 
# Usage:
#   1. Open Git Bash as Administrator
#   2. Run: bash setup_bash.sh
#   3. Restart Git Bash

echo "========================================"
echo "  Azazael Unified - Git Bash Setup"
echo "========================================"
echo ""

# Check if running in Git Bash or WSL
if [[ "$OSTYPE" == "msys" ]]; then
    echo "[INFO] Running in Git Bash (msys)"
    BASHRC="$HOME/.bashrc"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "[INFO] Running in WSL"
    BASHRC="$HOME/.bashrc"
else
    echo "[WARN] Unknown shell: $OSTYPE"
    BASHRC="$HOME/.bashrc"
fi

echo "[INFO] Using bashrc: $BASHRC"
echo ""

# Create .bashrc.d directory if it doesn't exist
BASHRC_D="$HOME/.bashrc.d"
if [ ! -d "$BASHRC_D" ]; then
    echo "[CREATE] Creating $BASHRC_D"
    mkdir -p "$BASHRC_D"
fi

# Create Windows helpers file
cat > "$BASHRC_D/windows-helpers.sh" << 'WINDOWS_HELPERS'
#!/bin/bash
# Windows File Helpers for Git Bash
# Fixes permission issues with Windows files

# Get actual Windows path from msys path
to_winpath() {
    cygpath -w "$1" 2>/dev/null || echo "$1"
}

# Delete Windows files (bypasses Git Bash permission restrictions)
rm-win() {
    if [ -z "$1" ]; then
        echo "Usage: rm-win <file_or_folder>"
        return 1
    fi
    winpath=$(cygpath -w "$1" 2>/dev/null)
    if [ -z "$winpath" ]; then
        winpath="$1"
    fi
    powershell.exe -Command "Remove-Item -Force -Recurse '$winpath'" 2>/dev/null
    echo "Deleted: $1"
}

# Rename/Move Windows files
mv-win() {
    if [ -z "$1" ] || [ -z "$2" ]; then
        echo "Usage: mv-win <old_name> <new_name>"
        return 1
    fi
    winpath1=$(cygpath -w "$1" 2>/dev/null)
    winpath2=$(cygpath -w "$2" 2>/dev/null)
    if [ -z "$winpath1" ]; then
        winpath1="$1"
    fi
    if [ -z "$winpath2" ]; then
        winpath2="$2"
    fi
    powershell.exe -Command "Rename-Item -Path '$winpath1' -NewName '$winpath2'" 2>/dev/null
    echo "Renamed: $1 -> $2"
}

# Create directory on Windows
mkdr-win() {
    if [ -z "$1" ]; then
        echo "Usage: mkdr-win <directory>"
        return 1
    fi
    winpath=$(cygpath -w "$1" 2>/dev/null)
    if [ -z "$winpath" ]; then
        winpath="$1"
    fi
    powershell.exe -Command "New-Item -ItemType Directory -Force '$winpath'" 2>/dev/null
    echo "Created: $1"
}

# Touch file on Windows
touch-win() {
    if [ -z "$1" ]; then
        echo "Usage: touch-win <file>"
        return 1
    fi
    winpath=$(cygpath -w "$1" 2>/dev/null)
    if [ -z "$winpath" ]; then
        winpath="$1"
    fi
    powershell.exe -Command "[System.IO.File]::OpenWrite('$winpath').Close()" 2>/dev/null
    echo "Touched: $1"
}

# Edit file in VS Code from Git Bash
code-win() {
    if [ -z "$1" ]; then
        echo "Usage: code-win <file>"
        return 1
    fi
    winpath=$(cygpath -w "$1" 2>/dev/null)
    if [ -z "$winpath" ]; then
        winpath="$1"
    fi
    powershell.exe -Command "code '$winpath'" 2>/dev/null
}

# Get file owner on Windows
owner-win() {
    if [ -z "$1" ]; then
        echo "Usage: owner-win <file>"
        return 1
    fi
    winpath=$(cygpath -w "$1" 2>/dev/null)
    if [ -z "$winpath" ]; then
        winpath="$1"
    fi
    owner=$(powershell.exe -Command "(Get-Acl '$winpath').Owner" 2>/dev/null)
    echo "$owner"
}

# Take ownership of file
takeown-win() {
    if [ -z "$1" ]; then
        echo "Usage: takeown-win <file>"
        return 1
    fi
    winpath=$(cygpath -w "$1" 2>/dev/null)
    if [ -z "$winpath" ]; then
        winpath="$1"
    fi
    powershell.exe -Command "takeown /f '$winpath' /a" 2>/dev/null
    echo "Took ownership: $1"
}

# Shortcuts for common commands
alias rm='rm-win'
alias mv='mv-win'
alias mkdir='mkdr-win'
alias del='rm-win'
alias ren='mv-win'

# Azazael shortcuts
alias azconfig='code "$HOME/.bashrc.d/windows-helpers.sh"'
alias azreload='source "$HOME/.bashrc"'
alias azpaths='cat "$HOME/.bashrc.d/windows-helpers.sh" | grep -A2 "^alias"'

# Export functions for subshells (if supported)
export -f rm-win 2>/dev/null
export -f mv-win 2>/dev/null
export -f mkdr-win 2>/dev/null
export -f touch-win 2>/dev/null
export -f code-win 2>/dev/null
export -f owner-win 2>/dev/null
export -f takeown-win 2>/dev/null
WINDOWS_HELPERS

echo "[CREATE] Created: $BASHRC_D/windows-helpers.sh"

# Add to .bashrc if not already present
BASHRC_CONTENT=$(cat "$BASHRC" 2>/dev/null)
if [[ ! "$BASHRC_CONTENT" == *"windows-helpers.sh"* ]]; then
    echo "" >> "$BASHRC"
    echo "# Load Windows helpers for Git Bash" >> "$BASHRC"
    echo "if [ -f \"\$HOME/.bashrc.d/windows-helpers.sh\" ]; then" >> "$BASHRC"
    echo "    source \"\$HOME/.bashrc.d/windows-helpers.sh\"" >> "$BASHRC"
    echo "fi" >> "$BASHRC"
    echo "[UPDATE] Added to $BASHRC"
else
    echo "[SKIP] Windows helpers already in $BASHRC"
fi

echo ""
echo "========================================"
echo "  Setup Complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo "  1. Restart Git Bash"
echo "  2. Test with: rm-win /c/Users/clint/Desktop/test.txt"
echo ""
echo "Useful aliases:"
echo "  rm <file>     - Delete file (Windows)"
echo "  mv <old> <new> - Rename file (Windows)"
echo "  mkdir <dir>   - Create directory (Windows)"
echo "  code <file>   - Open in VS Code"
echo "  azreload      - Reload bashrc"
echo "  azpaths       - Show Azazael aliases"
echo ""
