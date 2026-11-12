# The reference box — Intel Core Ultra 9 285H / Arc 140T

This is the machine the tree was built on, so it is the only platform whose
numbers are measurements rather than expectations. Everything a reader copies
from the rest of the docs was validated here first.

## Measured 2026-11-01

```
$ python tools/box_profile.py
BOX PROFILE  --  Windows / AMD64
cpu      Intel64 Family 6 Model 197 Stepping 2, GenuineIntel
         16 logical processor(s), 15.4 GiB RAM
python   3.15.0rc3 (CPython)  free-threaded=True

   device status       detail                       meaning
   xpu    unavailable                               no (this is a measurement, not a fault)
   cuda   unavailable                               no (this is a measurement, not a fault)
   mps    unavailable                               no (this is a measurement, not a fault)
   npu    unknown      not detectable with stdlib    NOT MEASURED -- not the same as absent
   cpu    available    always present               usable

preferred accelerator: cpu
torch: 2.16.0.dev20261006+cpu
not on PATH: blender, chrome
```

## Two figures in circulation that this box does not support

Read this before quoting either.

1. **"torch XPU is live" is true of a *different interpreter* on this class of
   box, not of `C:/Azazael/.venvs/py315t`.** That venv holds a **CPU-only**
   torch build (`2.16.0.dev20261006+cpu`) and
   `torch.xpu.is_available()` is False in it. The XPU readings in earlier notes
   (torch 2.11.0.dev+xpu, `total_memory 7.80 GB`, Arc 140T) came from AI
   Playground's **bundled** ComfyUI venv — which is **not installed on this box
   today** (no `C:/Program Files/Intel/AI Playground`). So: the *hardware* is an
   Arc 140T; the *production interpreter* is currently CPU-only.
2. **RAM reads 15.4 GiB here, not 32 GB.** `docs/HARDWARE_ACCELERATION.txt`
   records 32 GB DDR5 for an Ubuntu build of this CPU; that is not this machine's
   measurement. Do not size a model or a buffer from the 32 GB figure on this
   box.

Both are the same failure the tree keeps paying for: a figure measured somewhere
got filed as a property of everything. `tools/box_profile.py` exists to make
that require effort.

## The XPU route, if you want it back

Intel's Arc iGPU is driven by an XPU torch build, not by CUDA and not by the
default `pip install torch` wheel. Two supported ways, both verified elsewhere
and **neither installed here today**:

```bash
# A. Intel's own bundle (AI Playground ships ComfyUI + a matching venv)
#    Path used by this tree's notes:
#    .../AI Playground/resources/ComfyUI/.venv/Scripts/python.exe
#    Verify before trusting it:
#      python -c "import torch; print(torch.__version__, torch.xpu.is_available())"

# B. XPU wheels into the tree's own venv
python -m pip install --upgrade torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/xpu
python -c "import torch; print(torch.xpu.is_available(), torch.xpu.get_device_properties(0).total_memory/2**30)"
```

Then re-measure. `preferred_accelerator` must read `xpu`; if it still reads
`cpu`, the install did not take and the wheel was probably the CPU one.

**VRAM.** Size with `torch.xpu.get_device_properties(...).total_memory` (7.80 GB
on this class of box), never with WMI `AdapterRAM`, which returns a static 4.00
GiB aperture and is wrong twice over — static, and not what is allocatable.

## What is specific to this box

| what | value here | why it does not travel |
|---|---|---|
| Python | CPython **3.15.0rc3 free-threaded** at `C:/Azazael/.venvs/py315t` | 3.15t is an RC; wheels for it are scarce and some packages have no free-threaded build. `python` on PATH is 3.14.7 **with** the GIL — a "hang" measured under 3.14 is not evidence about production |
| Interpreter rule | run the suite with `C:/Azazael/.venvs/py315t/Scripts/python.exe` | see above; the two are not interchangeable |
| Accelerator | CPU today (Arc 140T present, XPU wheels absent) | an NVIDIA box substitutes `cuda` |
| Blender | 4.5 LTS at `C:/Users/clint/blender/blender-4.5.10-windows-x64/blender.exe`, Cycles on `ONEAPI` | `METAL` on Apple, `CUDA` on NVIDIA; quote the **device** with any render time (CPU fallback is a ~13× swing) |
| Renderer | Puppeteer browser capture, never PIL | unchanged across platforms; Chrome required |

## Checklist for a fresh Ultra 9 / Arc box

1. `python tools/box_profile.py --write box_profile.json` — record the starting
   state. It should read `preferred_accelerator: cpu` before you install
   anything; if it reads `xpu` on a bare box, be suspicious of what is already
   on the machine.
2. Install the XPU wheel (route A or B above).
3. `python tools/box_profile.py --check box_profile.json` — drift is now
   *expected* and should name `xpu: unavailable → available`.
4. `python -m pytest tests/ -q` under the **3.15t** interpreter.
5. If you intend to render, install Blender 4.5 LTS and confirm the Cycles
   device string is `ONEAPI` — a silent CPU fallback is the usual cause of a
   render that "takes 13× longer than documented".
