"""glia quant selection (task #509) -- a CPU-only host must never get a GPU-only format.

The defect this pins down: `OligodendrocyteWorm.recommend` chose the quantisation
format with

    if has_gpu and mem_gb >= 24:   GPTQ-4bit
    elif mem_gb >= 16:             AWQ-4bit      <-- has_gpu ignored
    else:                          GGUF-Q4_K_M

AWQ-4bit and GPTQ-4bit are GPU-only formats. The second branch tested only
`mem_gb >= 16`, so a CPU-only host with 16-23 GB was handed AWQ-4bit and could
never reach the GGUF branch at all: `has_gpu=False` was ignored entirely above
16 GB. That is not hypothetical here -- this machine is CPU-only, so
`recommend(16.0, False)` returned a format it cannot run, and the CPU path was
reachable only below 16 GB.

The discriminating cases are exactly mem_gb = 16, 20 and 23 with has_gpu=False:
those returned AWQ-4bit before the fix. A test that only checked a low-memory CPU
host would have passed against the broken code, which is why the sweep is asserted
AT and ABOVE the 16 GB boundary rather than below it.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

#: Formats that require a GPU. A CPU-only host must never be handed one of these.
GPU_ONLY = ("GPTQ-4bit", "AWQ-4bit")

CPU_MEM_SWEEP = (4, 8, 15, 16, 20, 23, 24, 32, 64)


def test_cpu_only_host_is_never_offered_a_gpu_only_format():
    """The regression: above 16 GB a CPU host used to be offered AWQ-4bit."""
    from CORE.WORMS.expansion.glia import OligodendrocyteWorm

    worm = OligodendrocyteWorm()
    for mem_gb in CPU_MEM_SWEEP:
        chosen = worm.recommend(mem_gb=float(mem_gb), has_gpu=False)
        assert chosen["format"] not in GPU_ONLY, (
            "CPU-only host with %s GB was offered %s, a GPU-only format "
            "(16, 20 and 23 GB are the cases that used to fail)"
            % (mem_gb, chosen["format"]))
        assert chosen["format"] == "GGUF-Q4_K_M", (
            "the GGUF (CPU) branch must be the only one reachable without a GPU; "
            "got %s at %s GB" % (chosen["format"], mem_gb))


def test_gpu_host_selection_is_unchanged():
    """has_gpu=True keeps the previous tiering, so the fix is not a reshuffle."""
    from CORE.WORMS.expansion.glia import OligodendrocyteWorm

    worm = OligodendrocyteWorm()
    for mem_gb in (24, 32, 64):
        assert worm.recommend(float(mem_gb), True)["format"] == "GPTQ-4bit", mem_gb
    for mem_gb in (16, 20, 23):
        assert worm.recommend(float(mem_gb), True)["format"] == "AWQ-4bit", mem_gb
    assert worm.recommend(8.0, True)["format"] == "GGUF-Q4_K_M"


def test_recommendation_echoes_its_arguments_unchanged():
    """The non-format fields are untouched by the branch change."""
    from CORE.WORMS.expansion.glia import OligodendrocyteWorm

    worm = OligodendrocyteWorm()
    for mem_gb, has_gpu in ((16.0, False), (32.0, True), (8.0, False)):
        chosen = worm.recommend(mem_gb=mem_gb, has_gpu=has_gpu)
        assert chosen["success"] is True
        assert chosen["mem_gb"] == mem_gb, chosen
        assert chosen["gpu"] is has_gpu, chosen
