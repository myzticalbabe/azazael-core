#!/usr/bin/env bash
# Install and VERIFY an NVIDIA/CUDA accelerator path for this tree.
#
# Run from the tree root:   bash scripts/box/bootstrap_cuda.sh [cu124]
#
# The wheel tag matters: a default `pip install torch` very often lands a CPU
# build, which leaves the library present and the backend compiled out. That is
# the specific failure this script is shaped around -- it installs into the
# tree's own venv, then insists on drift output proving preferred_accelerator
# changed. If nothing changed it says so and exits non-zero, because "installed
# successfully" is not the claim anyone needs.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"

tag="${1:-cu124}"
profile="box_profile.json"
py="${PY:-C:/Azazael/.venvs/py315t/Scripts/python.exe}"
[ -x "$py" ] || py="$(command -v python || command -v python3)" \
  || { echo "FAIL: no python found" >&2; exit 1; }

say() { printf '\n== %s\n' "$*"; }

say "1/5 before"
[ -f "$profile" ] || "$py" tools/box_profile.py --write "$profile"
"$py" - <<'PY'
import importlib.util
print("torch installed:", importlib.util.find_spec("torch") is not None)
PY

say "2/5 install the cu12x wheel into THIS venv"
"$py" -m pip install --upgrade torch torchvision torchaudio \
  --index-url "https://download.pytorch.org/whl/$tag"

say "3/5 what the wheel actually is"
"$py" -c "import torch; print('torch', torch.__version__, '| compiled cuda:', torch.version.cuda, '| available:', torch.cuda.is_available())"

say "4/5 drift -- the only acceptable evidence that this worked"
if ! "$py" tools/box_profile.py --check "$profile"; then
  cat >&2 <<'MSG'

Drift was reported. Read which row moved:
  cuda: unavailable -> available   the install took effect. Good.
  cuda: unknown     -> available   it was never installed before; also fine.
  no drift at all                  the install is NOT observable. Almost always
                                   a CPU wheel. Check torch.version.cuda above.
MSG
else
  echo "no drift: the backend state did not change. Treat the install as failed."
  exit 1
fi

say "5/5 one number, with its device named"
"$py" - <<'PY'
import torch
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    print("device:", p.name, "| total_memory GB:", round(p.total_memory / 2**30, 2))
    x = torch.randn(1024, 1024, device="cuda")
    y = (x @ x).sum().item()
    print("matmul ok:", float(y) == float(y))
else:
    print("cuda reports no device; nothing to benchmark and no number to quote")
PY

cat <<'NEXT'

Done. Next:
  Cycles   Blender with -d CUDA (OptiX on RTX); the ONEAPI recipe in the Intel
           docs does not apply to this card.
  llama.cpp  CMake with -DGGML_CUDA=ON; docs/CUDA_INTEGRATION_PROTOCOL.md covers
           the sidecar chain, not this page.
  Numbers  Any timing you now measure is a CUDA timing. The docs' latency
           figures were measured on an Intel/CPU box and do not transfer.
NEXT
