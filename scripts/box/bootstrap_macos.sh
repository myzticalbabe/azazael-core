#!/usr/bin/env bash
# Fit Azazael to a Mac (Apple silicon or Intel), recording what is measured and
# what is only expected.
#
# Run from the tree root:   bash scripts/box/bootstrap_macos.sh
#
# This script is written for a platform the project has NOT measured on. So it
# spends its checks on the two things most likely to be quietly wrong: whether
# the interpreter is real arm64 (not an x86_64 build under Rosetta) and whether
# MPS is actually reachable. Everything else it reports rather than fixes.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"

venv="${VENV:-$root/.venvs/py3}"
profile="box_profile.json"

say() { printf '\n== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

say "1/6 interpreter architecture"
machine="$(python3 -c 'import platform; print(platform.machine())')"
echo "python3 reports: $machine"
if [ "$(uname -m)" = "arm64" ] && [ "$machine" != "arm64" ]; then
  fail "this shell is arm64 but python3 is $machine (Rosetta). MPS will not be reachable; install an arm64 Python."
fi

say "2/6 venv"
if [ ! -x "$venv/bin/python" ]; then
  python3 -m venv "$venv" || fail "venv creation failed (xcode-select --install?)"
fi
py="$venv/bin/python"
"$py" -m pip install --upgrade --quiet pip

say "3/6 starting profile"
"$py" tools/box_profile.py --write "$profile"

say "4/6 GIL state (informational, never a blocker)"
"$py" -c "import sys; print('free-threaded:', not sys._is_gil_enabled() if hasattr(sys,'_is_gil_enabled') else 'n/a')"

say "5/6 suite"
"$py" -m pip install --upgrade --quiet pytest
"$py" -m pytest tests/ -q || fail "suite failed -- read the failure before changing anything"

say "6/6 re-measure"
"$py" tools/box_profile.py
"$py" tools/box_profile.py --check "$profile" || true

cat <<'NEXT'

Done. Now read docs/platforms/macos.md before doing anything else, and note:

  MPS           If preferred_accelerator is not "mps" on Apple silicon, the
                profile row says why: "unknown" = torch missing, "unavailable" =
                present without a device (usually a Rosetta or old wheel).
  Cycles        -d METAL, not ONEAPI. The Intel recipe does not apply here.
  VRAM          MPS exposes no per-device figure. Unified memory = system RAM,
                and that is what to quote -- not a made-up VRAM number.
  No measurement in the docs transfers to this box. Re-measure before quoting.

No accelerator wheel is installed by this script, on purpose. A wheel install is
a separate decision with its own verification step.
NEXT
