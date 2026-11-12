#!/usr/bin/env bash
# Fit Azazael to this Windows box, without changing anything it cannot verify.
#
# Run from the tree root, in Git Bash:   bash scripts/box/bootstrap_windows.sh
#
# What it does, in order: record the starting profile, check the two
# interpreters, install the dev dependency the suite needs, run the suite, then
# re-measure. It never edits the tree, never deletes a venv, and never installs
# an accelerator wheel -- that is a separate, deliberate step (docs/platforms/
# windows.md), because a wheel install that silently lands the CPU build is the
# failure this script exists to keep separate from "does the code run".
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"

py_free="${PY315T:-C:/Azazael/.venvs/py315t/Scripts/python.exe}"
profile="box_profile.json"

say() { printf '\n== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

say "1/5 record the starting profile"
[ -f "$py_free" ] || echo "note: $py_free not found; falling back to 'python' on PATH"
python_bin="$py_free"; [ -x "$py_free" ] || python_bin="python"
"$python_bin" tools/box_profile.py --write "$profile"
"$python_bin" tools/box_profile.py || true

say "2/5 interpreters"
python --version 2>&1 || true
"$python_bin" --version
"$python_bin" -c "import sys; print('free-threaded:', not sys._is_gil_enabled() if hasattr(sys,'_is_gil_enabled') else 'n/a')"

say "3/5 test dependency"
"$python_bin" -m pip install --upgrade --quiet pytest || fail "pytest install failed"

say "4/5 the suite"
"$python_bin" -m pytest tests/ -q || fail "suite failed -- read the failure before changing anything"

say "5/5 re-measure, and name the drift"
"$python_bin" tools/box_profile.py --check "$profile" || true

cat <<'NEXT'

Done. Next, deliberately and separately:

  Accelerator   docs/platforms/windows.md   (XPU only if you have an Intel Arc;
                NVIDIA goes to docs/platforms/cuda.md)
  Hooks         git config core.hooksPath .githooks   # per clone, not in the repo
  Renderer      Chrome must be installed before any capture script is meaningful

A suite that passes and a profile that says "preferred accelerator: cpu" is a
correct state on a box with no reachable accelerator wheel. It is not a fault
and it must not be reported as one.
NEXT
