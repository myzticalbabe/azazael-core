# NVIDIA / CUDA

Status: **not measured by this project.** The reference box has no NVIDIA card
(it has an Intel Arc iGPU), so nothing here is a reading. The routes below are
the documented ones, and `tools/box_profile.py` is what turns "documented" into
"measured on your machine".

This page covers the **accelerator only**. For shell, paths and interpreters,
read your OS page first ([`windows.md`](windows.md), [`macos.md`](macos.md)).

## The install that is usually wrong

```bash
python -m pip install torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu124
```

Two failure modes, both silent:

1. **A CPU wheel from PyPI.** `pip install torch` with no index URL installs a
   CPU build on many platforms. `box_profile.py` then reports
   `cuda: unavailable` and `torch_library.compiled_cuda: false` — the library is
   present, the backend is not compiled in.
2. **A driver older than the wheel.** `torch.cuda.is_available()` returns False.
   That is a *driver* reading, not an absence of hardware.

Verify before tuning:

```bash
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
python -c "import torch; p=torch.cuda.get_device_properties(0); print(p.name, round(p.total_memory/2**30,2))"
python tools/box_profile.py --write box_profile.json
```

The profile must show `cuda: available` with a name and a memory figure.
`preferred_accelerator` must read `cuda`, not `cpu`.

## Delta table

| constant | reference (Intel) | NVIDIA box |
|---|---|---|
| preferred accelerator | `xpu` / `cpu` | `cuda` |
| torch wheel index | `whl/xpu` or default | `whl/cu12x` matching your driver |
| VRAM source | `torch.xpu.get_device_properties(...).total_memory` | `torch.cuda.get_device_properties(...).total_memory` |
| VRAM honesty | shared aperture, never WMI `AdapterRAM` | card VRAM; still never WMI on Windows |
| Cycles device | `-d ONEAPI` | `-d CUDA` (verify Blender's GPU list; OptiX preferred for RTX) |
| llama.cpp build | `build-spark/` oneAPI recipe | CMake with `-DGGML_CUDA=ON`; the oneAPI recipe does not apply |
| `CUDA_PATH` | n/a | must point at the toolkit root for the C++ build |

`docs/CUDA_INTEGRATION_PROTOCOL.md` in the root docs is the longer recipe for
the `llama-server` sidecar chain; it assumes a CUDA toolkit and MSVC on Windows.
It is not a substitute for this page, and this page does not replace it.

## Mixed boxes are the common case

- **Windows + NVIDIA**: read `windows.md` (bash, hooks, venv names) then this
  page (wheels, device strings). Do not install the Intel XPU wheel "as well" —
  two torch builds in one venv is how `is_available()` starts returning the
  answer for the wrong backend.
- **Linux + NVIDIA**: same accelerator steps; substitute `apt`/`dnf` for the
  shell parts and make sure `/dev/nvidia*` exists before blaming the wheel.
- **Laptop with both iGPU and dGPU**: the wheel decides, not the machine. A
  `cu12x` wheel ignores the Intel iGPU; an XPU wheel ignores the NVIDIA card.
  Pick one and delete the other, or keep two venvs.

## What still will not work

CUDA buys you the accelerator path (torch device, Cycles device, llama.cpp
backend) and nothing else:

- **No behavioural number transfers.** Score recall, latency and bridge
  coefficients are properties of the data and the code, not the GPU. A faster
  card does not make a 0.18-recall router safer.
- **The renderer is unchanged.** Puppeteer capture is Chrome's rasteriser; a
  GPU may change timing, not output.
- **The forensic gate is unchanged.** `Project_ELIZA`'s allowlist is not a
  portability setting.

## Verification

```bash
python tools/box_profile.py --check box_profile.json    # expect: cuda unavailable -> available
python -m pytest tests/ -q
python tools/psyche_fit_pams.py
```

Drift output naming `cuda: unavailable → available` is the evidence that the
install took effect. Anything else — especially `compiled_cuda: false` — means
the wheel is the CPU build.
