# azazael-core — portable extraction

Installable, interpreter-agnostic repackaging of the Azazael tree: `src/`,
`tests/`, `scripts/`, `tools/`, the files tests actually import (`docs/scripts`,
`docs/bible`, `docs/platforms`, `docs/METTHEW`, `Project_LUCY`,
`azazael_operator_task.py`, root `conftest.py`), and the persistent-memory
(PAMS) subsystem as `src/persistent_ai_memory`.

## Provenance (measured 2026-11-11)

| input | identity |
|---|---|
| source tree | `C:/Azazael/azazael`, branch `azazael-intel-ultra-9-evo`, HEAD `254548f9` (2026-11-11), 3029 commits |
| memory subsystem | `C:/Azazael/pams-source`, HEAD `3361af4` (2026-10-29) **plus uncommitted worktree edits** (`ai_memory_core.py`, `mcp_server.py`) — shipped as found |
| operator instrument | `C:/Azazael/azazael_operator_task.py` (lives next to the source tree, not inside it) |
| reference wheel | `dist/azazael-1.0.0-py3-none-any.whl` (built 2026-11-09 in the source tree) |
| dependency builds | `C:/Azazael/.build/wheelhouse-316t/` — locally built `cp316t` wheels (numpy, scipy, torch-nightly deps, …). **Not vendored**: Windows-only binaries have no place in a portable repo. Consume via `--find-links` on 3.16t hosts. |

Deliberately excluded: `node_modules/`, `*.exe`, `*.log`, `__pycache__`,
`graphify-out/`, `docs/_shard01_frames/` (194M), `*.mp4`, `state/`,
`memory_data/`, `logs/`, `*.egg-info`, and the mangled-path artifacts
(`C:\…`, `D:`). Nothing under `tests/` references any of it (verified by
collection parity, below).

## The six portability fixes (all in this repo)

| # | was | now | proof |
|---|---|---|---|
| 1 | three `requires-python` values (`>=3.16` pyproject, `>=3.15` uv.lock, `>=3.14` Ubuntu install) | one: `pyproject.toml` says `>=3.13` | `pip install -e .` on stock 3.14.7 GIL |
| 2 | one declared dep (`psutil`); suite needs dozens | `[project.optional-dependencies] test` (pytest, xdist, timeout, hypothesis, dill, pydantic) + `memory` (aiohttp, watchdog) | fresh venv + `pip install -e ".[test]"` succeeds on 3.14/3.15t/3.16t |
| 3 | no `requirements-3.16t.txt` (only interpreter without a lockfile) | `requirements-3.16t.txt` — 84 pins frozen from the production venv | file present, generated 2026-11-11 |
| 4 | `CORE/python_env.py` hardcoded `C:/Azazael/…` with no override | `AZAZAEL_PRIMARY_PYTHON` / `AZAZAEL_GIL_PYTHON` / `AZAZAEL_FALLBACK_PYTHON` win when set; literals kept as fallback | override + default + missing-override cases all printed live |
| 5 | `AZAZAEL_ROOT` honored by `get_project_root()` but undocumented | documented here; resolves to the checkout, never a stale home path | `get_project_root()` → `C:\azazael-core` on a machine that never had one |
| 6 | free-threading treated as a requirement | requirement is stock 3.13+; GIL state is a runtime property | full reference file green on a GIL build (below) |

## Install

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"        # everywhere
.venv\Scripts\python -m pip install -e ".[test,memory]" # + PAMS subsystem
```

On free-threaded 3.15t/3.16t, add `--find-links <host wheelhouse>` for the
`heavy` ML surface (torch nightly, scipy, sklearn), which has no portable pin
by design. One more command turns collection fully green on stock Python:
`pip install numpy` (pure PyPI wheel on 3.13/3.14).

## Measured test evidence (2026-11-11, fresh venvs, new metadata only)

| venv | install `.[test]` | imports | `test_hyper_edge.py` | full collect |
|---|---|---|---|---|
| stock 3.14.7 GIL | ok | `CORE`, `persistent_ai_memory` ok | **51 passed** | 7017 collected, 57 errors (all `numpy`); **7469 collected, 0 errors after `pip install numpy`** |
| 3.15.0rc3 free-threaded | ok | ok, `gil False` | **51 passed** | 7017 collected, 57 errors (all `numpy`) |
| 3.16.0a1 free-threaded | ok | ok, `gil False` | **51 passed** | 7017 collected, 57 errors (all `numpy`) |

- Collection parity with the source tree on the same clean venv: old
  `6918/59` vs new `6918/59` before the `pydantic` extra, `7017/57` after —
  strict superset, zero regressions attributable to the extraction.
- Runtime spot-check (4 files, stock 3.14): **143 passed, 2 skipped,
  3 failed** — the 3 failures reproduce identically on the source tree
  (`test_grantforge_engines.py`, pre-existing, unrelated to packaging).
- Full-suite single run exceeds a 590 s budget (consistent with the source
  tree's own documented behavior; its runner is per-file). Not re-run blindly.
- Wheel rebuild from this repo on stock 3.14:
  `dist/azazael_core-1.0.0-py3-none-any.whl` (4.2 MB). Not committed.

## License — unresolved

No license file or grant was found in the source tree, and none is added
here. **A public repo without a license grants no reuse rights by default.**
Do not treat this as open source until the owner declares terms.

## What this repo is not

The author-machine infrastructure does not travel: `_board.py`, the
pmb `:8765`/`:8766` proxy, the PAMS supervisor MCP children, the VBS logon
starter, the 1042 archived package-skill descriptions, and the
`C:/Users/clint` home-repo context. `tools/` gates still name the old paths;
run them on the source tree, not here.
