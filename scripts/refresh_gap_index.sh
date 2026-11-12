#!/usr/bin/env bash
# refresh_gap_index.sh — one command: keep the BCC gap index machine-checkable.
#
# Usage:
#   scripts/refresh_gap_index.sh              # extract + write + anchors --strict
#   scripts/refresh_gap_index.sh --check      # verify only (freshness + anchors), no writes
#   scripts/refresh_gap_index.sh --extract    # refresh bcc_gap_index.json only
#
# What it does:
#   1. `build_gap_index.py extract`  — re-extract the canonical 62-gap JSON from
#      INVESTIGATION_GAPS.html into bcc_gap_index.json (the committed artifact the
#      pre-commit gate byte-compares against, so it must never drift).
#   2. `build_gap_index.py write`    — regenerate the in-doc index table from the
#      embedded JSON when stale (semantic freshness check: JSON equality + row-set
#      coverage — a healthy doc is never rewritten).
#   3. `build_gap_index.py anchors --strict` — every hub-doc anchor cite resolves to
#      an indexed gap; exit 1 on any dangling link.
#
# Companion to graphify_refresh_all.sh: run both after any gap-table or hub-doc edit
# to keep every machine-readable artifact in lockstep. The pre-commit hook
# (verify_gaps.sh) runs the same checks on every commit.
#
# Exit codes: 0 all green, 1 any check failed (writes may have been applied).

set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"          # repo root that owns INVESTIGATION_GAPS.html
TOOL="$ROOT/azazael/scripts/build_gap_index.py"
DOC="$ROOT/INVESTIGATION_GAPS.html"
JSON="$ROOT/azazael/scripts/bcc_gap_index.json"
MODE="${1:-full}"

[ -f "$TOOL" ] || { echo "❌ build_gap_index.py not found at $TOOL"; exit 1; }
[ -f "$DOC" ] || { echo "❌ $DOC not found — run from the corpus root (cwd: $ROOT)"; exit 1; }

PY=python
command -v "$PY" >/dev/null 2>&1 || PY=py

case "$MODE" in
  --check)
    echo "== check: index freshness + hub anchors (no writes) =="
    "$PY" "$TOOL" write "$DOC" --check || exit 1
    "$PY" "$TOOL" anchors "$DOC" --corpus "$ROOT" || exit 1
    ;;
  --extract)
    echo "== extract: refresh bcc_gap_index.json from the doc =="
    "$PY" "$TOOL" extract "$DOC" --out "$JSON" || exit 1
    ;;
  *)
    echo "== extract: refresh bcc_gap_index.json =="
    "$PY" "$TOOL" extract "$DOC" --out "$JSON" || exit 1
    echo "== write: regenerate the in-doc index table if stale =="
    "$PY" "$TOOL" write "$DOC" || exit 1
    echo "== anchors: every hub-doc cite must resolve =="
    "$PY" "$TOOL" anchors "$DOC" --corpus "$ROOT" || exit 1
    ;;
esac

echo "✅ gap index consistent ($(wc -l < "$JSON" 2>/dev/null) JSON lines)"
exit 0
