# The generations of the tree

Generated, never composed: each "begat" below is an import edge found by
reading `src/` with `ast`. Re-run it and the numbers must be the same.

```bash
python tools/bible_check.py --genealogy --write
```

26 houses, 587 edges between them.

And **CORE** begat **META**, **ORCHESTRATION**, **SCRIPTS**, and of the rest there were 3.
And **CODEX** begat **CORE**, **ORCHESTRATION**, **META**, and of the rest there were 1.
And **eve_unleashed** begat **CORE**, **META** and **UTILITIES**.
And **AOA** begat **CORE** and **eve_unleashed**.
And **HYDRA** begat **CORE** and **ORCHESTRATION**.
And **ORCHESTRATION** begat **CORE** and **CODEX**.
And **SHARDS** begat **CORE** and **INTERPRETERS**.
And **TOOLS** begat **CORE** and **AZAZAEL_TOOLKIT**.
And **UTILITIES** begat **CORE** and **eve_unleashed**.
And **tests** begat **CORE** and **ML**.
And **AZAZAEL_TOOLKIT** begat **CORE**.
And **CLI** begat **CORE**.
And **DAEMONS** begat **CORE**.
And **HYPER_EDGE** begat **CORE**.
And **INTERPRETERS** begat **CORE**.
And **META** begat **CORE**.
And **ML** begat **CORE**.
And **PSYCHE** begat **CORE**.
And **SCRIPTS** begat **CORE**.
And **ARC** begat no other house.
And **RENDERING** begat no other house.
And **RUST_VIDEO_WASM** begat no other house.
And **SHORTCUTS** begat no other house.
And **grantforge** begat no other house.
And **grantforge_native** begat no other house.
And **spitfire** begat no other house.

| house | begat | edges out |
|---|---|---|
| AOA | CORE, eve_unleashed | 51 |
| ARC | -- | 0 |
| AZAZAEL_TOOLKIT | CORE | 9 |
| CLI | CORE | 2 |
| CODEX | CORE, ORCHESTRATION, META, UTILITIES | 115 |
| CORE | META, ORCHESTRATION, SCRIPTS, UTILITIES, ML, PSYCHE | 11 |
| DAEMONS | CORE | 4 |
| HYDRA | CORE, ORCHESTRATION | 15 |
| HYPER_EDGE | CORE | 2 |
| INTERPRETERS | CORE | 11 |
| META | CORE | 32 |
| ML | CORE | 9 |
| ORCHESTRATION | CORE, CODEX | 113 |
| PSYCHE | CORE | 9 |
| RENDERING | -- | 0 |
| RUST_VIDEO_WASM | -- | 0 |
| SCRIPTS | CORE | 56 |
| SHARDS | CORE, INTERPRETERS | 13 |
| SHORTCUTS | -- | 0 |
| TOOLS | CORE, AZAZAEL_TOOLKIT | 10 |
| UTILITIES | CORE, eve_unleashed | 20 |
| eve_unleashed | CORE, META, UTILITIES | 64 |
| grantforge | -- | 0 |
| grantforge_native | -- | 0 |
| spitfire | -- | 0 |
| tests | CORE, ML | 41 |
