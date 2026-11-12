# Fitting Azazael to your box

This tree was built and tuned on one machine. Everything below exists because
that is a problem for everyone else, and because "it works on mine" is not an
installation instruction.

The rule for this directory: **a document here may not claim a backend that
`tools/box_profile.py` does not report on the box the document describes.** A
platform guide that names a device you do not have is a guide that sends you
chasing a failure that is not yours.

## Step 0 — measure, do not assume

```bash
python tools/box_profile.py                 # human table
python tools/box_profile.py --write box_profile.json
```

Three states matter, and the third one is the one people collapse by accident:

| state | meaning | what to do |
|---|---|---|
| `available` | library present, device reported | use it |
| `unavailable` | library present, **no** device reported | driver, or a wheel built without that backend |
| `unknown` | **the library is not installed**, so nothing could be probed | install it, then re-measure — this is not "no GPU" |

`box_profile.json` is the input to every later step. Write it down before you
change anything: a profile taken before and after an install is the only
evidence that the install did something.

## Step 1 — pick your column

| your box | read this | the short version |
|---|---|---|
| Intel Core Ultra 9 285H / Arc 140T (the reference build) | [`intel-ultra-9-evo.md`](intel-ultra-9-evo.md) | XPU via Intel's torch build; no CUDA; Cycles on `ONEAPI` |
| Windows, any GPU | [`windows.md`](windows.md) | `py315t` free-threaded venv, `MSVC` for the C++ side, no WSL assumptions |
| macOS (Apple silicon or Intel) | [`macos.md`](macos.md) | MPS, never XPU; Cycles on `METAL`; no CUDA at all |
| Android (Termux) | [`android.md`](android.md) | no torch, no ffmpeg pipeline as documented; degraded mode is the honest mode |
| NVIDIA GPU, any OS | [`cuda.md`](cuda.md) | `cu12x` wheels, `CUDA_PATH`, the oneAPI recipe does not apply |

If your box is a mix (Windows + NVIDIA, macOS + external GPU), read the OS page
first, then the CUDA page for the accelerator section only. The OS page owns
paths and shell; the accelerator page owns wheels and device strings.

## Step 2 — apply the difference, not the whole tree

Each platform page has a **Delta table**: the constant, the reference value, and
what it should be on that platform. Change those and nothing else. A full
re-tune is how a working build becomes an unverified one.

The deltas that recur:

| constant | reference (Intel) | Apple | NVIDIA | Android |
|---|---|---|---|---|
| torch backend | `xpu` | `mps` | `cuda` | none |
| Cycles device | `ONEAPI` | `METAL` | `CUDA` | n/a (no Cycles) |
| VRAM figure | shared, ~7.8 GiB allocatable | unified = RAM | card VRAM | n/a |
| free-threaded Python | yes (3.15t) | check; 3.13t+ | check | no |
| Blender | 4.5 LTS | 4.5 LTS | 4.5 LTS | n/a |

## Step 3 — verify with the same instruments the reference build used

A platform change is verified the same way a code change is: run the thing and
read the number.

```bash
python tools/box_profile.py --check box_profile.json   # did anything actually change?
C:/Azazael/.venvs/py315t/Scripts/python.exe -m pytest tests/ -q   # the suite, 3.15t
python tools/psyche_fit_pams.py                        # refuses: no constructs yet
python tools/elicit.py items                           # the battery loads
python tools/platform_claims.py --check                # do these pages still tell the truth?
```

`box_profile.py --check` compares a stored profile against the live machine and
exits non-zero on drift, so the question "did my install take effect" has an
answer that is not a feeling.

Run the suite with the **free-threaded** interpreter, not whatever `python`
resolves to: on the reference box `python` is 3.14.7 with the GIL, and a hang or
a segfault measured there is not evidence about production. See
[`windows.md`](windows.md) for the two-interpreter table.

### The pages are checked, not trusted

`docs/platforms/claims.json` holds every claim in this directory that names a
value, a path or a status, together with the command that settles it. The
checker re-runs them and names the ones that have gone stale:

```bash
python tools/platform_claims.py              # CONFIRMED / STALE / UNVERIFIABLE
python tools/platform_claims.py --json        # machine-readable
```

That is the difference between a platform guide and a recollection: a page may
not claim a backend or a command that the checker cannot reproduce here.

## Step 4 — write down what is still unknown

Every profile has `unknown` rows. They are the honest part. Do not delete them
from a committed profile to make the table look complete: a document that says
"CUDA: unavailable" on a machine where torch was never installed is describing
itself, not the hardware.

## What is NOT portable, on purpose

Two things are deliberately not abstracted, and you should expect to change
them rather than configure them:

- **Measured numbers.** A recall figure, a latency, a bridge coefficient — all
  measured on this box with these wheels. A different accelerator will produce
  different timings, and the platform pages say so rather than pretending the
  figure travels.
- **The forensic gate's deny-list.** `Project_ELIZA`'s allowlist encodes what
  must never be published about named people. It is not a portability knob. If
  you fork it for your own case, the list is the first thing to re-read, not the
  first thing to relax.
