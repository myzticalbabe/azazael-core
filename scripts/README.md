# Graphify Codebase Atlas — Quick Reference

Everything below lives on your Desktop. Pick the file for the repo you want to
explore and double-click it. No server needed — all viewers run locally from
`file://` and never send your code anywhere.

---

## Which file to open, by repo

| Repo | Nodes | Single-file (double-click) | Full viewer folder |
|---|---|---|---|
| **azazael** | 404,433 | **`AZAZAEL_COSMOS_VIEWER_SINGLE.html`** (21 MB, one file) | `AZAZAEL_COSMOS_VIEWER\` → `cosmos-viewer.html` (GPU physics on) |
| **aoa_g** (G:\AZAZAEL\AOA) | 193 | `AZAZAEL_COSMOS_VIEWER_aoa_g.html` | `AZAZAEL_COSMOS_VIEWER_aoa_g\` |
| **aoa_source** (G:\AZAZAEL_SOURCE_OF_TRUTH\AOA) | 193 | `AZAZAEL_COSMOS_VIEWER_aoa_source.html` | `AZAZAEL_COSMOS_VIEWER_aoa_source\` |
| **vermillion_lightfury** | 700 | `AZAZAEL_COSMOS_VIEWER_vermillion_lightfury.html` | `AZAZAEL_COSMOS_VIEWER_vermillion_lightfury\` |

**Rule of thumb:** use the `.html` file (single-file, works from anywhere).
Use the folder (`cosmos-viewer.html`) only if you want the live GPU force
simulation for azazael — the single-file version uses a precomputed static
layout instead (20 MB vs 297 MB, renders instantly).

## Shortcuts (also on the Desktop)

- `AZAZAEL_VIEWER.lnk` → azazael GPU graph (folder viewer)
- `AOA_G_VIEWER.lnk` → aoa_g standalone viewer
- `AOA_SOURCE_VIEWER.lnk` → aoa_source standalone viewer
- `VERMILLION_VIEWER.lnk` → vermillion_lightfury standalone viewer
- `Graphify_Codebase_Atlas.lnk` → the full atlas folder (all repos, all artifacts)

## In the atlas folder — `Desktop\Graphify_Codebase_Atlas_2026-08-27\<repo>\`

Each repo folder contains the full artifact set:

| File | What it is |
|---|---|
| `cosmos-viewer.html` + `cosmos\` | GPU graph viewer (chunked data, physics on) |
| `graph.html` | Graphify's standard interactive view |
| `graph-full.html` | Full node-level detail view |
| `GRAPH_TREE.html` | Collapsible node-level D3 tree |
| `GRAPH_REPORT.md` | Community report |
| `graph.json` | The raw graph data |

## Using the Cosmos viewer

- **Drag** — pan · **Scroll** — zoom · **Click a node** — inspect it
- **Search box** (top) — type to find any node, click to zoom to it
- **Left pane tabs** — "Mind-map" (directory tree drill-down) or "Communities"
- Click a directory or community to highlight its nodes and zoom to them

## Rebuilding / refreshing

| Script (in `azazael\scripts\`) | What it does |
|---|---|
| `graphify_refresh_all.sh` | One command: patch + refresh all graphs + rebuild all viewers + refresh Desktop shortcut |
| `start_refresh_background.sh` | Launch the full refresh **detached in the background** (log → `/tmp/refresh_all.log`) so it survives a closed terminal |
| `refresh_atlas_and_open.bat` | The Desktop icon wrapper. Flags: `--admin` (elevate via UAC), `--no-open` (silent, for scheduled tasks) — combine as `--admin --no-open` |
| `cosmos_build_atlas.sh` | Rebuild just the Cosmos viewers (incl. single-file versions) from the atlas graphs |
| `graphify_node_limit_patch.py` | Re-apply the >5000-node fix after `uv tool upgrade graphifyy` |

Example:
```bash
bash ~/azazael/scripts/graphify_refresh_all.sh          # everything, in the foreground
bash ~/azazael/scripts/start_refresh_background.sh      # same, but detached (watch with: tail -f /tmp/refresh_all.log)
bash ~/azazael/scripts/cosmos_build_atlas.sh azazael    # just azazael viewers
```

**Scheduled task (admin, silent):**
```bat
schtasks /Create /TN "Azazael\Graphify Atlas Refresh" /TR "\"C:\Users\clint\azazael\scripts\refresh_atlas_and_open.bat\" --admin --no-open" /SC DAILY /ST 02:00 /RL HIGHEST /F
```

_Generated 2026-08-27._
