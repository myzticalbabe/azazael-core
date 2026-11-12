# Prompts that fit a box

These are the prompts to hand an agent (or yourself) when the machine is not the
reference build. They are written to produce a *measured* result, because the
failure this tree keeps hitting is a plausible statement nobody checked.

Rules that every prompt below assumes:

1. **Measure first, change second.** `python tools/box_profile.py` before and
   after. A change with no `--check` drift output did not happen.
2. **Three states, never two.** `available` / `unavailable` / `unknown`.
   `unknown` means the library is missing, not that the hardware is absent.
3. **No invented numbers.** VRAM comes from `torch.<backend>.get_device_properties`,
   never from WMI `AdapterRAM` and never from a vendor's marketing page.
4. **Change the delta, not the tree.** Each platform page has a Delta table;
   edit those constants and leave the rest alone.

---

## 1. Measure and report (safe, no changes)

```
You are fitting this tree (Azazael) to a new box. Do not install, modify or
delete anything in this pass.

Run `python tools/box_profile.py --json` and report back:
- os.system / machine, cpu model, logical count, memory_gb
- python.version, implementation, free_threaded
- for each accelerator: backend, status, and the name/memory IF present
- preferred_accelerator

Then state, in one line each, which rows are `unknown` and what library would
have to be installed before that row could be measured. Do not convert a row
from `unknown` to `unavailable` for any reason.

Finally: which of docs/platforms/{intel-ultra-9-evo,windows,macos,android,cuda}.md
does this profile match most closely, and which Delta-table rows already differ?
```

Why it is worded this way: the last question is what stops a reader applying
the Intel page to a Mac, and the `unknown` rule is the one people quietly break.

---

## 2. Install and verify (state-changing, bounded)

```
Install the accelerator path for this box, then prove it took effect.

Constraints:
- one accelerator only. If a wheel for another backend is already installed in
  this venv, tell me before removing it; do not install a second one alongside.
- use the tree's own venv (Windows: C:/Azazael/.venvs/py315t). Do not touch the
  system interpreter.
- after installing, run `python tools/box_profile.py --check box_profile.json`
  (take the "before" profile first if it does not exist) and paste the drift.

Report ONLY what the tool printed. Do not write "CUDA/XPU is now enabled" unless
preferred_accelerator changed in the drift output. If the drift is empty, say
the install is not observable and stop.
```

Why: "installed successfully" and "reachable from this interpreter" are
different claims, and this tree has shipped the first while the second was false.

---

## 3. Port one module to a new backend

```
Port <module> to <backend> on this box, without changing its public behaviour.

Rules:
- the backend probe must be one function, not an if-chain spread through the module.
  The ranking already exists as data: `tools/box_profile.py --json` reports
  `accelerators[]` and `preferred_accelerator`, ordered xpu → cuda → mps → cpu.
  Consume that, or port its ranking into one helper — there is no
  `src/CORE/accelerate` resolver in this tree today, so do not ask an agent to
  "use" one it will not find.
- device-specific constants (dtype, memory cap, thread count) go in one table
  with the platform named per entry, so the next port edits a row, not a code path.
- add one test that fails on the reference box and passes on the target, and
  one test that passes on both (the negative control). A port with only the
  first test cannot be distinguished from a broken original.
- do not report a speedup without a measured before/after on the same input.
```

---

## 4. Diagnose "it does not work on my box"

```
Something in this tree fails on my box. Diagnose from the outside in, and do not
change code until a measurement names the cause.

The evidence I need before any fix:
1. `python tools/box_profile.py` output (full).
2. The exact command that failed and its complete stderr.
3. Which interpreter ran it (path), and whether it is the free-threaded one.
4. Whether the failure reproduces with the package imported from the repo root
   only (no installed copy): `PYTHONPATH=src python -c "import ..."`.

Then classify the failure as one of:
- missing/incompatible wheel      (box_profile shows `unknown`)
- wheel present, backend absent   (box_profile shows `unavailable`)
- path resolution                 (works from the repo, fails installed)
- version skew                   (3.14 with GIL vs 3.15t; two torch builds)
and say which line of evidence supports the classification.
```

Why: the four classes have four different fixes, and guessing between them is
how a working build gets "repaired" into a broken one.

---

## 5. Confirm a platform document is still true

```
Verify docs/platforms/<page>.md against this machine.

For every claim in the document that names a value or a status, find the line in
the document, then either (a) reproduce it with the command the document gives,
or (b) mark it UNVERIFIED with the reason (not measured / hardware absent).

Output two lists: CONFIRMED (claim → command → output line) and UNVERIFIED
(claim → why). Do not edit the document yet; the second list is the edit plan.
```

---

## The anti-prompts

Prompts that produce confident nonsense here, and what to say instead:

| instead of | say |
|---|---|
| "set up the GPU" | "install the XPU/NVIDIA/MPS wheel for this venv, then show the `--check` drift" |
| "make it faster" | "measure this input before and after; report both, and the device name with the timing" |
| "my VRAM is 4 GB" | "read `total_memory` from the device properties; WMI `AdapterRAM` is a static aperture" |
| "the model should run on my phone" | "which parts of this tree are stdlib-only, and which require torch/Chrome/Cycles?" |
| "it works on my machine" | "here is `box_profile.json` and the `--check` drift" |
