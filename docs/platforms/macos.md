# macOS (Apple silicon, and Intel Macs)

Status: **not measured by this project.** No Apple hardware was available, so
every number here is a documented expectation, not a reading. The only figures
this tree trusts are the ones `tools/box_profile.py` prints on the machine in
front of you — run it before believing anything below.

## What changes, and what does not

The behavioural package is pure stdlib Python; it does not care what the
accelerator is. What changes is:

| constant | reference (Intel / Windows) | on macOS |
|---|---|---|
| torch backend | `xpu` (Intel Arc) | **`mps`** — there is no XPU on Apple silicon and no CUDA at all |
| Cycles device | `ONEAPI` | **`METAL`** (Apple silicon) or `CPU` (Intel Mac) |
| VRAM | shared aperture, ~7.8 GiB on the Arc | **unified**: the GPU allocates from system RAM. `box_profile.py` reports no per-device figure because MPS exposes none — quote RAM and call it unified |
| Blender | `blender.exe` 4.5 LTS | `/Applications/Blender.app/Contents/MacOS/Blender` |
| Chrome | `chrome` on PATH | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |
| free-threaded Python | 3.15.0rc3, GIL off | check yours: `python3 -c "import sys; print(sys._is_gil_enabled() if hasattr(sys,'_is_gil_enabled') else 'n/a')"`. Do not assume; a GIL-enabled build is fine, it is just not the tuned path |
| shell | Git Bash | `zsh` — the bash snippets here work, but `PATH` and app-bundle paths differ |

## Install

```bash
xcode-select --install                 # compiler toolchain for anything with a C extension
python3 --version                      # >= 3.10; a free-threaded 3.13t+ build if you can get one
python3 -m venv .venvs/py3
source .venvs/py3/bin/activate
python -m pip install --upgrade pip
python tools/box_profile.py            # confirm mps: available before you tune anything
```

An `mps: unavailable` on Apple silicon almost always means **the wheel is an
x86_64 build running under Rosetta**, or a torch too old to have MPS. Check
`python -c "import platform; print(platform.machine())"` — it must say `arm64`.

## MPS is not XPU, and the difference bites

- **No `torch.xpu`.** Code that calls `torch.xpu.*` unconditionally is Intel-only
  and will raise `AttributeError` here. The ranking this tree uses lives in
  `tools/box_profile.py` (`accelerators[]`, then `preferred_accelerator`, in the
  order `xpu → cuda → mps → cpu`); on a Mac it must land on `mps`, and
  `preferred_accelerator` tells you whether it did. There is no
  `src/CORE/accelerate` module in this tree — a page that sends you looking for
  one is the defect this directory's claims file exists to catch.
- **Some ops fall back silently.** MPS raises for unimplemented operations or
  falls back to CPU per-op; either way the wall-clock is the only honest report.
  A latency figure measured on this box does not transfer, and the docs say so
  rather than quoting one.
- **No `float64`.** MPS is float32-centric. A numeric that needs double precision
  has to be computed on CPU on this platform; that is a property of the device,
  not a bug to work around.

## Delta table

| constant | reference value | macOS value |
|---|---|---|
| preferred accelerator | `xpu` or `cpu` | `mps` |
| Cycles device argument | `-d ONEAPI` | `-d METAL` |
| device memory | `total_memory` on the device props | none exposed; unified with RAM |
| Chrome executable | `chrome` | full app-bundle path |
| `ffmpeg` | on PATH | `brew install ffmpeg`; there is no system copy |
| `sysctl` use | n/a | `sysctl -n hw.memsize`, `machdep.cpu.brand_string` — this is how `box_profile.py` reads your CPU and RAM |

## Verification

```bash
python tools/box_profile.py --write box_profile.json
python -m pytest tests/ -q
python tools/elicit.py items
python tools/psyche_fit_pams.py          # exit 2 is correct: no constructs elicited yet
```

If `preferred_accelerator` reads `cpu` on Apple silicon, that is a finding, not a
failure: it means MPS was not reachable from the interpreter you ran, and the
profile's `mps` row says whether the library was missing (`unknown`) or present
without a device (`unavailable`).
