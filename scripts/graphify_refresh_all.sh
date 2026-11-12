#!/usr/bin/env bash
# graphify_refresh_all.sh — one command: apply the node-limit patch, then
# refresh every graphify atlas repo to its current on-disk state.
#
# Usage:
#   scripts/graphify_refresh_all.sh              # all repos
#   scripts/graphify_refresh_all.sh azazael aoa_g  # subset by repo name
#   scripts/graphify_refresh_all.sh --dry-run    # print plan, do nothing
#   scripts/graphify_refresh_all.sh --no-desktop # skip Desktop-root deploys
#   scripts/graphify_refresh_all.sh --no-verify  # skip the CDP render check
#
# What it does per repo:
#   1. Applies graphify_node_limit_patch.py (idempotent) so the >5000-node
#      HTML-viz recursion bug stays fixed even after `uv tool upgrade`.
#   2. Refreshes the graph:
#        - azazael: `graphify update`  (incremental, no LLM, keeps semantic
#          cache — fast after the first full run)
#        - vermillion_lightfury / aoa_g / aoa_source: `graphify extract
#          --code-only --out <atlas>` (writes to C:, never to the full G: drive)
#   3. Re-clusters, regenerates graph.html + GRAPH_REPORT.md + GRAPH_TREE.html.
#   4. Syncs every artifact into the Desktop atlas folder (including the
#      graph.nd*.js / graph.ed*.js data chunks graph.html references — without
#      them the atlas viewer renders STALE chunks, see the sync_out comment).
#   5. Rebuilds the Cosmos viewers for the same repos (cosmos_build_atlas.sh),
#      including the Desktop-root copies and single-file standalone HTMLs.
#   6. Re-creates the Desktop shortcuts: the atlas folder shortcut
#      (Graphify_Codebase_Atlas.lnk), the per-repo viewer shortcuts
#      (AZAZAEL_VIEWER / AOA_G_VIEWER / AOA_SOURCE_VIEWER / VERMILLION_VIEWER),
#      and the project launcher shortcuts (EveUnleashed_Selector, LlamaServer,
#      PAMS, PerformanceMonitor, Ultimate_Agent_Selector, WSL_CrossExplorer,
#      Azazael_MetaCluster, Tigger_CLI, G_EveUnleashed, AOA_Selector), so they
#      always point at the current files even if paths change.
#   7. CDP-renders each refreshed atlas viewer (verify_graphs_cdp.py) to prove
#      the HTML loads, vis-network builds, and the rendered node count is sane.
#      Failures are warnings, not fatal — inspect the FAIL lines before using
#      the viewers. Skip with --no-verify.
#
# Env defaults (User-scope global vars normally provide these; the script
# falls back to sane values if they are missing, e.g. on a fresh shell):
#   GRAPHIFY_VIZ_NODE_LIMIT  (default 1000000)
#   GRAPHIFY_MAX_GRAPH_BYTES (default 10GB)
#   GRAPHIFY_MAX_WORKERS     (default 16)
#   GRAPHIFY_ATLAS           (default ~/Desktop/Graphify_Codebase_Atlas_2026-08-27)
#   GRAPHIFY_BIN             (default scripts/graphify_bin.sh — capability-resolved)
#   GRAPHIFY_VERIFY          (default ~/verify_graphs_cdp.py)
set -euo pipefail

# --- config ------------------------------------------------------------------
ATLAS="${GRAPHIFY_ATLAS:-$HOME/Desktop/Graphify_Codebase_Atlas_2026-08-27}"
DESKTOP_ROOT="${GRAPHIFY_DESKTOP_ROOT:-$HOME/Desktop}"
PROJECTS_DIR="${GRAPHIFY_PROJECTS_DIR:-$HOME/Desktop/Project Launchers}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python}"
PY="${PY_LAUNCHER:-/mnt/c/Users/clint/AppData/Local/Programs/Python/Python315/python.exe}"
# Resolved by capability through scripts/graphify_bin.sh, not by hard-coded
# path: the old default (~/.local/bin/graphify.exe) is a uv trampoline that
# stopped resolving on 2026-10-09 -- "uv trampoline failed to canonicalize
# script path" -- which killed this refresh at repo 1 while a working graphify
# 0.9.72 sat unused in Python314's site-packages. See ADR-019 and
# tools/graphify_interpreter.py.
GRAPHIFY="${GRAPHIFY_BIN:-$SCRIPT_DIR/graphify_bin.sh}"
PATCH="$SCRIPT_DIR/graphify_node_limit_patch.py"
COSMOS="$SCRIPT_DIR/cosmos_build_atlas.sh"
VERIFY="${GRAPHIFY_VERIFY:-$(cygpath -m "$HOME/verify_graphs_cdp.py" 2>/dev/null || echo "$HOME/verify_graphs_cdp.py")}"

# helper: bash path -> Windows path (verify_graphs_cdp.py needs C:/... form)
win_path() { # <bash path>
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$1" 2>/dev/null || echo "$1"
  else
    echo "$1" | sed 's|^/\([a-z]\)/|\1:/|I'
  fi
}

# CDP-render each refreshed atlas viewer; failures warn (non-fatal) so a bad
# viewer never silently ships — but the refresh itself already succeeded.
verify_viewers() { # repos already filtered by WANT in the caller's loop order
  local viewers=() entry repo v
  [ -f "$VERIFY" ] || { warn "verify harness missing: $VERIFY (set GRAPHIFY_VERIFY) — skipping CDP render check"; return 0; }
  # union of update + extract repos, honoring WANT when given
  for entry in "${REPOS_UPDATE[@]}" "${REPOS_EXTRACT[@]}"; do
    repo="${entry%%|*}"
    if [ ${#WANT[@]} -gt 0 ] && [[ " ${WANT[*]} " != *" $repo "* ]]; then
      continue
    fi
    v="$ATLAS/$repo/graph.html"
    [ -f "$v" ] && viewers+=("$(win_path "$v")")
  done
  if [ ${#viewers[@]} -eq 0 ]; then
    warn "no atlas viewers found for the requested repos — skipping CDP render check"
    return 0
  fi
  log "CDP render check: ${#viewers[@]} viewer(s) via verify_graphs_cdp.py"
  if PYTHONIOENCODING=utf-8 "$PY" "$VERIFY" "${viewers[@]}"; then
    echo "  viewers: ALL CLEAN"
  else
    warn "viewer render check reported failures (see FAIL lines above)"
  fi
}

# export caps defensively (global User scope usually supplies these)
export GRAPHIFY_VIZ_NODE_LIMIT="${GRAPHIFY_VIZ_NODE_LIMIT:-1000000}"
export GRAPHIFY_MAX_GRAPH_BYTES="${GRAPHIFY_MAX_GRAPH_BYTES:-10GB}"
export GRAPHIFY_MAX_WORKERS="${GRAPHIFY_MAX_WORKERS:-16}"

# repo name -> "update <source>" or "extract <source>" + atlas subfolder
# NOTE (2026-10-10, closed 2026-10-21): azazael points at the production
# tree C:/Azazael/azazael (git repo, MCP graph source). The old second
# tree C:/Users/clint/azazael was archived 2026-10-21 to
# G:/azazael.archived-20261021 — same wrong-tree class as the old chain
# task (see T-1/T-2 notes in TODO_315T_BUILD.md).
REPOS_UPDATE=(
  "azazael|C:/Azazael/azazael"
)
REPOS_EXTRACT=(
  "vermillion_lightfury|C:/Users/clint/.kali_staging/vermillion_lightfury"
  "aoa_g|G:/AZAZAEL/AOA"
  "aoa_source|G:/AZAZAEL_SOURCE_OF_TRUTH/AOA"
  "kali|C:/Users/clint/Desktop/Graphify_Codebase_Atlas_2026-08-27/kali_src"
)

# --- helpers -----------------------------------------------------------------
log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }

refresh_shortcut() { # re-create Desktop .lnk pointing at the current atlas
  local lnk win_lnk win_atlas
  if ! command -v powershell >/dev/null 2>&1; then
    warn "powershell not found - skipping Desktop shortcut refresh"
    return 0
  fi
  win_atlas=$(cygpath -w "$ATLAS" 2>/dev/null || echo "$ATLAS")
  lnk="$DESKTOP_ROOT/Graphify_Codebase_Atlas.lnk"
  win_lnk=$(cygpath -w "$lnk" 2>/dev/null || echo "$lnk")
  powershell -NoProfile -Command \
    "\$ws = New-Object -ComObject WScript.Shell; \$s = \$ws.CreateShortcut('$win_lnk'); \$s.TargetPath = '$win_atlas'; \$s.Description = 'Graphify Codebase Atlas - all repos'; \$s.Save()" \
    >/dev/null 2>&1 && echo "shortcut -> $win_atlas" || warn "could not refresh Desktop shortcut"
}

make_lnk() { # <lnk path> <target> <description>
  local lnk="$1" target="$2" desc="$3" win_lnk win_target
  if ! command -v powershell >/dev/null 2>&1; then
    warn "powershell not found - skipping shortcut $lnk"
    return 1
  fi
  [ -f "$target" ] || { warn "target missing for $lnk: $target"; return 1; }
  win_lnk=$(cygpath -w "$lnk" 2>/dev/null || echo "$lnk")
  win_target=$(cygpath -w "$target" 2>/dev/null || echo "$target")
  powershell -NoProfile -Command \
    "\$ws = New-Object -ComObject WScript.Shell; \$s = \$ws.CreateShortcut('$win_lnk'); \$s.TargetPath = '$win_target'; \$s.Description = '$desc'; \$s.Save()" \
    >/dev/null 2>&1 && echo "shortcut $(basename "$lnk") -> $win_target" || warn "could not create shortcut $lnk"
}

# Per-repo viewer shortcuts: point at the single-file/standalone viewer on the
# Desktop root (or the atlas cosmos-viewer.html as fallback), so they always
# open the freshest build after a refresh.
refresh_viewer_shortcuts() {
  local repo lnk target
  # repo|lnk-name|desktop-root target (fallback: atlas/<repo>/cosmos-viewer.html)
  local entries=(
    "azazael|AZAZAEL_VIEWER|$DESKTOP_ROOT/AZAZAEL_COSMOS_VIEWER/cosmos-viewer.html"
    "aoa_g|AOA_G_VIEWER|$DESKTOP_ROOT/AZAZAEL_COSMOS_VIEWER_aoa_g.html"
    "aoa_source|AOA_SOURCE_VIEWER|$DESKTOP_ROOT/AZAZAEL_COSMOS_VIEWER_aoa_source.html"
    "vermillion_lightfury|VERMILLION_VIEWER|$DESKTOP_ROOT/AZAZAEL_COSMOS_VIEWER_vermillion_lightfury.html"
  )
  for entry in "${entries[@]}"; do
    repo="${entry%%|*}"; rest="${entry#*|}"
    lnk_name="${rest%%|*}"; target="${rest#*|}"
    # only touch repos in the requested subset
    if [ ${#WANT[@]} -gt 0 ] && [[ " ${WANT[*]} " != *" $repo "* ]]; then
      continue
    fi
    if [ ! -f "$target" ] || [ "$DEPLOY_DESKTOP" -eq 0 ]; then
      target="$ATLAS/$repo/cosmos-viewer.html"
    fi
    make_lnk "$DESKTOP_ROOT/$lnk_name.lnk" "$target" "$lnk_name - Cosmos viewer for $repo"
  done
}

# Project launcher shortcuts: python.exe + script, with working directory and
# a distinct icon per project. Mirrors the former make_project_shortcuts.ps1 so
# the 10 launchers are regenerated on every refresh instead of being a one-time
# artifact. They live in a single "Project Launchers" folder on the Desktop so
# they don't clutter the root; the folder is created if missing.
ICON_DIR="$SCRIPT_DIR/icons"
ICON_MAKER="$SCRIPT_DIR/make_launcher_icons.py"
PROJECT_SHORTCUTS=(
  "EveUnleashed_Selector|$DESKTOP_ROOT/EveUnleashed_Selector/run.py|$DESKTOP_ROOT/EveUnleashed_Selector"
  "LlamaServer|$DESKTOP_ROOT/LlamaServer/run.py|$DESKTOP_ROOT/LlamaServer"
  "PAMS|$DESKTOP_ROOT/PAMS/run.py|$DESKTOP_ROOT/PAMS"
  "PerformanceMonitor|$DESKTOP_ROOT/PerformanceMonitor/run.py|$DESKTOP_ROOT/PerformanceMonitor"
  "Ultimate_Agent_Selector|$DESKTOP_ROOT/Ultimate_Agent_Selector/run.py|$DESKTOP_ROOT/Ultimate_Agent_Selector"
  "WSL_CrossExplorer|$DESKTOP_ROOT/WSL_CrossExplorer/run.py|$DESKTOP_ROOT/WSL_CrossExplorer"
  "Azazael_MetaCluster|$HOME/azazael/METACLUSTER.py|$HOME/azazael"
  "Tigger_CLI|$HOME/tigger/cli.py|$HOME/tigger"
  "G_EveUnleashed|G:/AZAZAEL/EveUnleashed/run.py|G:/AZAZAEL/EveUnleashed"
  "AOA_Selector|$DESKTOP_ROOT/AOA/cli/aoa_cli.py|$DESKTOP_ROOT/AOA"
)

# regenerate the per-project .ico files (idempotent; writes only if changed)
ensure_icons() {
  [ -f "$ICON_MAKER" ] || { warn "icon generator missing: $ICON_MAKER"; return 1; }
  "$PYTHON" "$ICON_MAKER" >/dev/null 2>&1 \
    && echo "icons -> $ICON_DIR" \
    || warn "could not regenerate launcher icons"
}

make_py_lnk() { # <lnk> <script> <workdir> <icon> <description> - python launcher shortcut
  local lnk="$1" script="$2" workdir="$3" icon="$4" desc="$5" win_lnk win_script win_workdir win_py win_icon
  if ! command -v powershell >/dev/null 2>&1; then
    warn "powershell not found - skipping shortcut $lnk"
    return 1
  fi
  [ -f "$script" ] || { warn "script missing for $lnk: $script"; return 1; }
  win_lnk=$(cygpath -w "$lnk" 2>/dev/null || echo "$lnk")
  win_script=$(cygpath -w "$script" 2>/dev/null || echo "$script")
  win_workdir=$(cygpath -w "$workdir" 2>/dev/null || echo "$workdir")
  win_py=$(cygpath -w "$PY" 2>/dev/null || echo "$PY")
  win_icon=$(cygpath -w "$icon" 2>/dev/null || echo "$icon")
  [ -f "$icon" ] || win_icon=""
  powershell -NoProfile -Command \
    "\$ws = New-Object -ComObject WScript.Shell; \$s = \$ws.CreateShortcut('$win_lnk'); \$s.TargetPath = '$win_py'; \$s.Arguments = '\"\"$win_script\"\"'; \$s.WorkingDirectory = '$win_workdir'; \$s.Description = '$desc'; \$s.IconLocation = '\"$win_icon\",0'; \$s.Save()" \
    >/dev/null 2>&1 && echo "shortcut $(basename "$lnk") -> $win_script" || warn "could not create shortcut $lnk"
}

refresh_project_shortcuts() {
  local entry name script workdir icon
  ensure_icons
  mkdir -p "$PROJECTS_DIR"
  for entry in "${PROJECT_SHORTCUTS[@]}"; do
    name="${entry%%|*}"; rest="${entry#*|}"
    script="${rest%%|*}"; workdir="${rest#*|}"
    icon="$ICON_DIR/$name.ico"
    make_py_lnk "$PROJECTS_DIR/$name.lnk" "$script" "$workdir" "$icon" "Launcher: $script"
  done
}

sync_out() { # <repo> <graphify-out dir> -> copies artifacts up into atlas
  local repo="$1" outdir="$2" dst="$ATLAS/$repo"
  mkdir -p "$dst"
  for f in graph.json manifest.json graph.html GRAPH_REPORT.md \
           .graphify_labels.json .graphify_labels.json.sig .graphify_root; do
    if [ -f "$outdir/$f" ]; then
      cp -f "$outdir/$f" "$dst/$f"
    fi
  done
  # graph.html references graph.nd*.js (nodes) + graph.ed*.js (edges) via
  # <script src>; without copying them the atlas viewer renders STALE chunks
  # (2026-09-03: atlas showed 404,317 nodes vs the report's 410,128 until fixed)
  if compgen -G "$outdir/graph.nd*.js" >/dev/null || compgen -G "$outdir/graph.ed*.js" >/dev/null; then
    cp -f "$outdir"/graph.nd*.js "$outdir"/graph.ed*.js "$dst/"
  fi
}

refresh_update() { # <repo> <source>
  local repo="$1" source="$2" dst="$ATLAS/$repo"
  # source is a Windows path (e.g. C:/Azazael/azazael); outdir for bash
  # file ops (cp, [ -f ]) must be a WSL2 path — convert via cygpath -u.
  local outdir
  outdir="$(cygpath -u "$source/graphify-out" 2>/dev/null || echo "$source/graphify-out")"
  log "[$repo] update $source"
  "$GRAPHIFY" update "$source"
  # update runs the full pipeline; GRAPH_TREE.html is not part of it
  if [ -f "$outdir/graph.json" ]; then
    log "[$repo] regenerate GRAPH_TREE.html"
    "$GRAPHIFY" tree --graph "$(win_path "$outdir/graph.json")" \
        --output "$(win_path "$dst/GRAPH_TREE.html")" --label "$repo"
  fi
  sync_out "$repo" "$outdir"
}

refresh_extract() { # <repo> <source>
  local repo="$1" source="$2" dst="$ATLAS/$repo" outdir="$ATLAS/$repo/graphify-out"
  log "[$repo] extract --code-only $source -> $dst"
  "$GRAPHIFY" extract "$source" --code-only --out "$(win_path "$dst")"
  log "[$repo] cluster + report + graph.html"
  "$GRAPHIFY" cluster-only "$(win_path "$dst")"
  log "[$repo] regenerate GRAPH_TREE.html"
  "$GRAPHIFY" tree --graph "$(win_path "$outdir/graph.json")" \
      --output "$(win_path "$dst/GRAPH_TREE.html")" --label "$repo"
  sync_out "$repo" "$outdir"
}

# --- main --------------------------------------------------------------------
DRY_RUN=0
DEPLOY_DESKTOP=1
RUN_VERIFY=1
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --no-desktop) DEPLOY_DESKTOP=0 ;;
    --no-verify) RUN_VERIFY=0 ;;
    *) ARGS+=("$arg") ;;
  esac
done
set -- "${ARGS[@]}"
WANT=("$@")

log "graphify: $GRAPHIFY"
log "atlas:    $ATLAS"
log "caps:     VIZ=$GRAPHIFY_VIZ_NODE_LIMIT BYTES=$GRAPHIFY_MAX_GRAPH_BYTES WORKERS=$GRAPHIFY_MAX_WORKERS"

if [ "$DRY_RUN" -eq 1 ]; then
  log "DRY RUN — plan:"
  for entry in "${REPOS_UPDATE[@]}"; do
    repo="${entry%%|*}"; source="${entry#*|}"
    if [ ${#WANT[@]} -eq 0 ] || [[ " ${WANT[*]} " == *" $repo "* ]]; then
      echo "  update   $repo  <- $source"
    fi
  done
  for entry in "${REPOS_EXTRACT[@]}"; do
    repo="${entry%%|*}"; source="${entry#*|}"
    if [ ${#WANT[@]} -eq 0 ] || [[ " ${WANT[*]} " == *" $repo "* ]]; then
      echo "  extract  $repo  <- $source"
    fi
  done
  echo "  patch    $PATCH"
  echo "  cosmos   rebuild viewers for the same repos"
  echo "  shortcut refresh atlas + viewer + project launcher shortcuts"
  [ "$DEPLOY_DESKTOP" -eq 0 ] && echo "  cosmos   (--no-desktop: skip Desktop-root deploys)"
  if [ "$RUN_VERIFY" -eq 1 ]; then
    echo "  verify   CDP-render the refreshed atlas viewers ($VERIFY)"
  else
    echo "  verify   (--no-verify: skipped)"
  fi
  exit 0
fi

# 1. apply the node-limit fix (idempotent)
log "apply node-limit patch"
"$PYTHON" "$PATCH"

# 2. refresh
ran_any=0
for entry in "${REPOS_UPDATE[@]}"; do
  repo="${entry%%|*}"; source="${entry#*|}"
  if [ ${#WANT[@]} -eq 0 ] || [[ " ${WANT[*]} " == *" $repo "* ]]; then
    refresh_update "$repo" "$source"; ran_any=1
  fi
done
for entry in "${REPOS_EXTRACT[@]}"; do
  repo="${entry%%|*}"; source="${entry#*|}"
  if [ ${#WANT[@]} -eq 0 ] || [[ " ${WANT[*]} " == *" $repo "* ]]; then
    refresh_extract "$repo" "$source"; ran_any=1
  fi
done

if [ "$ran_any" -eq 0 ]; then
  warn "no matching repos. Valid: azazael vermillion_lightfury aoa_g aoa_source"
  exit 1
fi

# 3. rebuild Cosmos viewers for the same repo subset (skip if graphify-out
#    didn't produce a fresh graph.json for a repo — cosmos_build_atlas.sh
#    already skips repos without graph.json).
if [ -x "$COSMOS" ]; then
  cosmos_args=()
  [ ${#WANT[@]} -gt 0 ] && cosmos_args+=("${WANT[@]}")
  [ "$DEPLOY_DESKTOP" -eq 0 ] && cosmos_args+=("--no-desktop")
  log "rebuild Cosmos viewers"
  bash "$COSMOS" "${cosmos_args[@]}" || warn "cosmos viewer rebuild had issues (non-fatal)"
else
  warn "cosmos_build_atlas.sh not found next to this script — skipping viewer rebuild"
fi

# 4. keep the Desktop shortcuts pointed at the current files
refresh_shortcut
refresh_viewer_shortcuts
refresh_project_shortcuts

# 5. prove the refreshed viewers actually render (catch stale chunks / broken
#    HTML before the next session uses them). Non-fatal on failure.
if [ "$RUN_VERIFY" -eq 1 ]; then
  verify_viewers
else
  log "CDP render check skipped (--no-verify)"
fi

log "done. Atlas: $ATLAS"
echo "  - azazael:   graph.html (aggregated) + graph-full.html + GRAPH_TREE.html"
echo "  - others:    graph.html + GRAPH_TREE.html"
echo "  - cosmos:    cosmos-viewer.html + Desktop-root copies for the rebuilt repos"
