#!/usr/bin/env bash
# Fit the BEHAVIOURAL parts of Azazael to an Android phone (Termux).
#
# Run in Termux:   bash scripts/box/bootstrap_android.sh
#
# This is a degraded box and the script says so instead of pretending otherwise.
# Torch has no android-aarch64 wheel, Chrome is absent and Blender is not ported,
# so the accelerator, renderer and capture paths are genuinely unavailable here
# -- while the psyche package, the elicitation battery and the instruments are
# stdlib-only and do run.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"

profile="box_profile.json"
py="${PYTHON:-python}"

say() { printf '\n== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

command -v pkg >/dev/null || fail "this is not Termux (no 'pkg'); for a normal Linux box use the OS page instead"

say "1/5 packages"
pkg install -y python git >/dev/null || fail "pkg install failed"
"$py" --version

say "2/5 test dependency"
"$py" -m pip install --upgrade --quiet pip pytest || fail "pip install failed"

say "3/5 starting profile (expect three 'unknown' accelerator rows)"
"$py" tools/box_profile.py --write "$profile"

say "4/5 what actually runs here"
"$py" -m pytest tests/test_psyche_affect.py tests/test_psyche_network.py \
                  tests/test_psyche_rt_agent.py tests/test_psyche_rt_channel.py \
                  tests/test_psyche_traits.py -q \
  || fail "the behavioural subset failed -- that is a real failure, not a platform limitation"
"$py" tools/elicit.py items | head -12

say "5/5 the instrument that must refuse"
set +e
"$py" tools/psyche_fit_pams.py >/dev/null 2>&1
rc=$?
set -e
if [ "$rc" -eq 2 ]; then
  echo "psyche_fit_pams: exit 2 -- correct, no constructs have been elicited anywhere yet"
else
  echo "psyche_fit_pams: exit $rc -- 2 was expected; investigate before trusting the fit"
fi

cat <<'NEXT'

Done. What this box can and cannot do (docs/platforms/android.md):

  CAN      the behavioural package, the construct battery, the instruments,
           and the JSONL store at ~/.psyche/elicitations.jsonl
  CANNOT   torch (no android-aarch64 wheel), Blender/Cycles, Chrome/Puppeteer
           capture, any accelerator work

Three 'unknown' rows in the profile are the correct reading on a phone: the
libraries are not installed, so nothing could be probed. That is not the same
statement as "this phone has no GPU", and the profile keeps them apart.
NEXT
