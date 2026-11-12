# AOA — Armorie-of-Anubis Umbrella Orchestrator (restored, runnable)

**Status: RUNNABLE** — verified 2026-09-01 from this location on the repo's Python 3.14.7
(`arms list` = 8/8 arms discovered; `test_cross_arm.py` PASSED — session lifecycle
INIT → DISCOVERY → INITIALIZE 8/8 → READY 9/9 → SHUTDOWN, cross-arm events routed 14).

## What this is

AOA is a stdlib-only orchestrator that coordinates the Azazael ecosystem: the
77-worm system (azazael arm), CODEX Studio video engine (codex arm), Libra legal
automation (libra arm), EveUnleashed (eve arm), PAMS memory (pams arm), performance
monitor, agent selector, and LlamaServer. **Zero third-party imports** — every module
is pure Python stdlib, so it runs in any environment (verified on 3.14.7).

## Provenance (why this copy exists / where it came from)

| Item | Value |
|---|---|
| **Source** | `C:\Users\clint\Desktop\AOA` — **CANONICAL, newest generation** (Aug 2026, BCC-era superset) |
| **Copied to repo** | 2026-09-01 (31 files, 236.8 KB; robocopy 0 failed / 0 mismatched) |
| **Generation lineage** | gen 1 `G:\AZAZAEL\AOA` ≈ gen 2 `G:\AZAZAEL_SOURCE_OF_TRUTH\AOA` (24/24 .py byte-identical) → **Desktop = superset** (AOA: 15 identical / 11 differ / 26 new-only vs gen 1 — incl. the BCC-era `libra_arm.py` 11,281 B and test suite) |
| **Historical reference** | `archive/restored/AOA/` holds the **gen 1** copy (pre-BCC) — kept for history, NOT runnable canon |
| **G: originals** | Untouched (copy, not move — standing directive) |

`src/CODEX/CODEX_Studio.py` and `src/META/` (Z-modules) already live in this repo;
AOA's `codex_arm.py` and `cli/aoa_cli.py` hard-code `Path.home()/azazael/src/...`
paths, so this copy resolves its primary integration targets against the repo's own
tree. Desktop-external targets (EveUnleashed, Libra, LlamaServer) are coordinated by
absolute `Path.home()/Desktop/...` subprocess paths and work from anywhere.

## Exclusions from the copy (still on Desktop)

- `*.mp4` disclosure renders (~101 MB) — display artifacts, not code
- `frames/`, `proof_frames/` — disclosure render intermediates
- `__pycache__/` — regenerable

## Sync convention

**Desktop is canonical.** To refresh this copy after Desktop changes:
```
robocopy "C:\Users\clint\Desktop\AOA" "C:\Users\clint\azazael\src\AOA" /E /XD __pycache__ frames proof_frames /XF *.mp4
```

## Running

```
cd src/AOA
python cli/aoa_cli.py --help      # init | arms | run | session | workflow
python cli/aoa_cli.py arms list
python test_cross_arm.py          # standalone integration script (not a pytest test)
```Reference: `.agents/skills/aoa-architecture.md` (documents the architecture; note its misdocumented claims per `AOA_LIBRA_GAP_CATALOG.md` §1 — three causes: never-existed `build_html.py` + Libra `index_terms`/`search_dict`; misattributed Eve `select_agent` (on AgentSelector) + Llama `start_server`/`stop_server` (in Desktop/LlamaServer scripts); stale "101 worms" → 77).