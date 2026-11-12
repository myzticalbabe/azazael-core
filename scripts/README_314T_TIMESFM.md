# TimesFM Setup (3.15t production)

> **3.14 RETIRED 2026-10-19** — this stack was originally built on Python 3.14.7
> free-threading (`venv-314t`). That venv is retired and archived; production is
> now the free-threaded 3.15t venv (`C:/Azazael/.venvs/py315t`). The recipe below
> targets 3.15t. `.venvs/py314m` is the only sanctioned 3.14 runtime, for a
> package that has no 3.15 build. Results already recorded from the 3.14t run are
> kept below and labelled as such — they were not re-measured on 3.15.

Free-threaded setup for the **TimesFM 3 second-opinion anomaly layer** on the BCC
financial series.

## Environment

| Item | Value |
|---|---|
| Interpreter | `C:/Azazael/.venvs/py315t/Scripts/python.exe` (3.15.0rc3, `_is_gil_enabled() == False`) |
| Location | `C:/Azazael/.venvs/py315t` |
| torch | install the free-threaded wheel for 3.15t (`cp315t`); the 3.14t run used `2.14.0+cpu` |
| timesfm | `3.0.1` (ships `timesfm3` / `TimesFM3Forecaster`) |
| Model weights | `google/timesfm-3.0-pytorch` (~200 M params, one-time HuggingFace download ~166 s) |
| Supporting | `numpy 2.4.6`, `pandas 3.0.3`, `scipy 1.18.0`, `statsmodels 0.15.0` |

## Install (reproducible)

```bash
# 1. Use the production free-threaded venv (already exists on this machine)
#    C:/Azazael/.venvs/py315t   (3.15.0rc3, GIL off)

# 2. Install torch — use the free-threaded wheel (NOT the GIL build)
C:/Azazael/.venvs/py315t/Scripts/python.exe -m pip install torch

# 3. Install TimesFM 3
C:/Azazael/.venvs/py315t/Scripts/python.exe -m pip install timesfm

# 4. Verify free-threading + imports
C:/Azazael/.venvs/py315t/Scripts/python.exe -c "import sys, torch; print(sys._is_gil_enabled(), torch.__version__)"
C:/Azazael/.venvs/py315t/Scripts/python.exe -c "from timesfm3 import TimesFM3Forecaster; print('timesfm OK')"
```

> **Note:** free-threaded wheels are shipped with a `t` ABI tag — Pip resolves them
> automatically on a 3.15t interpreter. Do **not** install the standard GIL torch
> build into this venv; it will import but loses free-threading benefits. If a
> package has no 3.15 wheel at all, install it into `.venvs/py314m` (GIL, 3.14.5),
> which is the sanctioned fallback — never rebuild a 3.14t venv.

## Usage

```bash
# Default: all three metrics (revenue, expenses, officer_comp)
C:/Azazael/.venvs/py315t/Scripts/python.exe azazael/scripts/timesfm_pilot_314t.py

# Single metric with explicit series path
C:/Azazael/.venvs/py315t/Scripts/python.exe azazael/scripts/timesfm_pilot_314t.py --metrics revenue --series bcc_990_series.json
```

First run downloads `google/timesfm-3.0-pytorch` (~166 s); afterwards ~5 s load
+ ~0.1 s per forecast on 8 threads *(timing measured on the retired 3.14t venv)*.

## Output contract

Each metric prints: `TF err` (forecast-vs-actual residual %), `z` (rolling z-score,
window w=5), and an `agreement` verdict (`DIVERGENT` when the two layers disagree).
See `BCC_FINANCIAL_ANOMALY.html` (renamed from FINANCIAL_ANOMALY_LAYER) for the Sep-4, 2026 interpretation: TimesFM flags
FY2025 revenue −15.5 % vs model where rolling z misses it (−0.33); comp z=+2.79 flags
where TimesFM is +9.0 %. The deficit (−$796,924) is only visible as the *combination*.

## Honest caveats (carried into BCC_FINANCIAL_ANOMALY.html)

- N=15 ≪ TimesFM's ≥256-point sweet spot — **pilot, not court-defensible**; z-scores remain primary.
- Single probe point (FY2025); expanding needs the pre-e-file FY2001–2010 PDF era.
- Forecast = continuation, not fact.

## Related

- `azazael/scripts/fetch_990_series.py` — builds `bcc_990_series.json` (FY2011–2025, 15 filings)
  + fully documented IRS source chain (IRS TEOS CSV index → ProPublica API v2 → `/full_text/` CDP endpoint).
- `bcc_990_series.json` + `timesfm_pilot_314t.py` live beside `BCC_FINANCIAL_ANOMALY.html` in the investigation corpus root.