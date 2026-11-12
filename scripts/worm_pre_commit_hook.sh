#!/bin/bash
# ============================================================================
# Azazael Pre-Commit Hook — Worm Registry + Compliance + Syntax Checks
# ============================================================================
# Installed by: sentinel_enforcer.install_git_hooks()
# RULES ENFORCED: Worm registry integrity, compliance scanning, syntax validation
#
# To bypass in emergency: git commit --no-verify
# ============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SRC_DIR="$REPO_ROOT/src"
PYTHON="$( [ -x /c/Azazael/.venvs/py315t/Scripts/python.exe ] && echo /c/Azazael/.venvs/py315t/Scripts/python.exe || which python3.15t 2>/dev/null || which python3 2>/dev/null || which python 2>/dev/null)"

echo "=== Azazael Pre-Commit Hook ==="
echo ""

# ─── Check 1: Python syntax validation on all staged .py files ───
echo "[1/5] Syntax check on staged Python files..."
STAGED_PY=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)
SYNTAX_FAIL=0
if [ -n "$STAGED_PY" ]; then
    for f in $STAGED_PY; do
        if [ -f "$REPO_ROOT/$f" ]; then
            if ! $PYTHON -m py_compile "$REPO_ROOT/$f" 2>/dev/null; then
                echo "  FAIL: Syntax error in $f"
                SYNTAX_FAIL=1
            fi
        fi
    done
    if [ $SYNTAX_FAIL -eq 0 ]; then
        echo "  OK: All staged .py files pass syntax check"
    fi
else
    echo "  No staged .py files — skipping"
fi
echo ""

# ─── Check 2: Worm registry validation ───
echo "[2/5] Worm registry validation..."
REGISTRY_OK=0
$PYTHON -c "
import sys
sys.path.insert(0, '$SRC_DIR')
from CORE.worm_registry import discover_worms, validate_registry
discover_worms()
report = validate_registry()
if report['status'] == 'critical':
    print(f\"  FAIL: Registry status={report['status']} — {len(report['issues'])} issue(s)\")
    for issue in report['issues'][:5]:
        print(f\"    [{issue['severity']}] {issue['worm']}: {issue['issue']}\")
    sys.exit(1)
elif report['status'] == 'warn':
    print(f\"  WARN: {report['total_worms']} worms, {report['categories']} categories — {len(report['issues'])} warning(s)\")
else:
    print(f\"  OK: {report['total_worms']} worms, {report['categories']} categories — all healthy\")
" 2>&1 || REGISTRY_OK=$?
echo ""

# ─── Check 3: Compliance scanner (if available) ───
echo "[3/5] Compliance scanner..."
SCANNER_OK=0
if [ -f "$SRC_DIR/CORE/compliance/scanner.py" ]; then
    $PYTHON "$SRC_DIR/CORE/compliance/scanner.py" --report --ci --dir "$SRC_DIR" --quiet 2>&1 || SCANNER_OK=$?
    if [ $SCANNER_OK -eq 0 ]; then
        echo "  OK: Compliance scan passed"
    else
        echo "  FAIL: Compliance scan found issues"
    fi
else
    echo "  Scanner not available — skipping"
fi
echo ""

# ─── Check 4: Environment sync (if sync_all.py exists) ───
echo "[4/5] Environment sync check..."
SYNC_OK=0
if [ -f "$REPO_ROOT/scripts/sync_all.py" ]; then
    $PYTHON "$REPO_ROOT/scripts/sync_all.py" --verify 2>&1 || SYNC_OK=$?
    if [ $SYNC_OK -eq 0 ]; then
        echo "  OK: Environment is in sync"
    else
        echo "  FAIL: Environment is out of sync. Run: \$PYTHON \$SYNC_SCRIPT --sync-all"
    fi
else
    echo "  sync_all.py not found — skipping"
fi
echo ""

# ─── Check 5: Catch-all regression guard ───
echo "[5/5] Catch-all guard..."
CATCHALL_OK=0
CATCHALL_FILES=$(git -C "$REPO_ROOT" diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)
if [ -n "$CATCHALL_FILES" ]; then
    if ( cd "$REPO_ROOT" && "$PYTHON" scripts/check_no_catch_alls.py --files $CATCHALL_FILES ); then
        echo "  OK: No bare/catch-all handlers in staged files"
    else
        CATCHALL_OK=1
        echo "  FAIL: Staged file(s) introduce a bare except (see output above). Add an explicit '# allow-broad' marker only if intentional."
    fi
else
    echo "  No staged Python files — skipping"
fi
echo ""

# ─── Summary ───
echo "=========================================="
FAILS=$((SYNTAX_FAIL + REGISTRY_OK + SCANNER_OK + SYNC_OK + CATCHALL_OK))
if [ $FAILS -eq 0 ]; then
    echo "  ALL CHECKS PASSED"
    echo "=========================================="
    exit 0
else
    echo "  $FAILS CHECK(S) FAILED — commit blocked"
    echo "  (Use 'git commit --no-verify' to bypass in emergency)"
    echo "=========================================="
    exit 1
fi
