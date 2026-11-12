# Android (Termux)

Status: **not measured by this project**, and the honest expectation is a
**degraded** box. This page exists so that "run it on your phone" produces a
truthful report instead of a bug hunt.

## What actually runs

The behavioural package is stdlib-only and does run:

| part | runs on Termux? | notes |
|---|---|---|
| `src/PSYCHE` (traits, affect, network, intuition, `rt_agent`, `rt_channel`) | **yes** | stdlib only; no numpy, no wheels |
| `tools/elicit.py`, `tools/psyche_fit_pams.py`, `tools/triage_eval.py` | **yes** | stdlib only, JSONL files in `$HOME` |
| `Project_Nurse_Ratchet` instruments and tests | **yes** | pure Python; `pytest` installs from pip |
| torch (XPU / CUDA / MPS) | **no** | no wheel for android-aarch64; `import torch` cannot succeed |
| Blender / Cycles | **no** | not ported |
| Puppeteer + Chrome capture | **no** | no Chrome; no headless shell in Termux |
| `ffmpeg` | **yes**, via `pkg install ffmpeg` | CPU only, software encoders |

So: the phone is a **valid box for the behavioural logic and its instruments**,
and an invalid box for the renderer and any accelerator work. Say which you are
asking of it.

## Install

```bash
pkg update && pkg upgrade
pkg install python git ffmpeg
python -m pip install --upgrade pip
python -m pip install pytest
git clone <your fork> azazael && cd azazael
python tools/box_profile.py
```

Expected profile on a phone (the shape, not a reading):

```
python   3.1x (CPython)  free-threaded=None
   xpu    unknown      torch is not installed, so this backend cannot be probed
   cuda   unknown      ...
   mps    unknown      ...
   npu    unknown      not detectable with stdlib
   cpu    available
preferred accelerator: cpu
```

Three `unknown` rows are the **correct** answer, not a failure. They mean "this
tool could not probe that backend", which on a phone is true and permanent. A
profile that claims `unavailable` for a library that was never installed is
describing itself, not the hardware — that collapse is what
`tools/box_profile.py` refuses to make.

## What to expect to fail, and what to do instead

| task | on the phone | instead |
|---|---|---|
| `pip install torch` | fails: no android-aarch64 wheel | keep the loop CPU-only, or move torch work to a desktop box |
| rendering a clip | fails: no Chrome, no Cycles | render on Windows/macOS/CUDA box; the renderer's inputs are text |
| `pytest` on the full suite | some tests spawn subprocesses and touch `sys.executable` | run the psyche and instrument subsets first: `python -m pytest tests/test_psyche_*.py -q` |
| long runs | Android kills background processes aggressively | keep sessions interactive; write to disk as you go (the elicitation store appends per session for this reason) |

## Storage and battery, which are the real constraints

- **Keep the store small.** `~/.psyche/elicitations.jsonl` is one line per
  session, so it is cheap — but a `pytest` run in a temp dir plus a node_modules
  tree is not. Do not run `npx`-based tooling on the phone.
- **Assume the process can be killed between commands.** The battery appends one
  complete record per session and hashes it at scoring time; there is no
  half-written state to corrupt mid-append beyond the final line, and a partial
  session is refused rather than scored.

## Verification

```bash
python tools/box_profile.py --write box_profile.json
python -m pytest tests/test_psyche_affect.py tests/test_psyche_network.py \
                tests/test_psyche_rt_agent.py tests/test_psyche_rt_channel.py -q
python tools/elicit.py items | head -20
python tools/psyche_fit_pams.py          # exit 2, as on every box: nothing elicited
```

If the four psyche test files pass and `elicit.py items` lists the battery, the
phone is a working box for the behavioural package. Everything else on it is a
different machine's job.
