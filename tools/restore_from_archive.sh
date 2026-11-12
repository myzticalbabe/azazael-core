#!/usr/bin/env bash
# Restore from G: Archive
# Usage:  bash restore_from_archive.sh [target_dir]

TARGET="${1:-$HOME/Desktop/azazael-project-restored}"
ARCHIVE="/mnt/windows/g/AZAZAEL/azazael-project-e_backup"

echo "========================================"
echo "  RESTORE FROM G: ARCHIVE"
echo "========================================"
echo ""
echo "  Source:      $ARCHIVE"
echo "  Target:      $TARGET"
echo ""

if [ ! -d "$ARCHIVE" ]; then
    echo "❌ Archive not found at $ARCHIVE"
    echo "   Is the G: drive mounted?"
    exit 1
fi

if [ -d "$TARGET" ]; then
    echo "⚠️  Target directory already exists."
    read -p "  Overwrite? (y/N): " CONFIRM
    if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
        echo "Cancelled."
        exit 0
    fi
fi

echo "  Restoring files..."
rsync -avh --delete \
    --exclude=__pycache__ \
    --exclude=.appdata \
    --exclude=*.pyc \
    "$ARCHIVE/" "$TARGET/"

COUNT=$(find "$TARGET" -type f -not -path '*__pycache__*' | wc -l)
echo ""
echo "✅ Restore complete: $COUNT files restored to $TARGET"
echo ""
