#!/usr/bin/env bash
# cosmos_build_atlas.sh — convert every atlas repo's graph.json to Cosmos
# viewer data and deploy the HTML viewer next to it.
#
# Usage:
#   scripts/cosmos_build_atlas.sh                # all repos
#   scripts/cosmos_build_atlas.sh azazael aoa_g  # subset by repo name
#
# Requires: node/npm (for bundling viewer), python3. The Cosmos bundle is
# built once from scripts/cosmos-viewer/ and reused for every repo.
#
# Each repo's viewer is also deployed as a self-contained copy to the Desktop
# root (Desktop/AZAZAEL_COSMOS_VIEWER for azazael,
# Desktop/AZAZAEL_COSMOS_VIEWER_<repo> for others) so it's visible without
# digging into the atlas. Small repos (< 200MB of data) additionally get a
# single-file fully-inlined Desktop/AZAZAEL_COSMOS_VIEWER_<repo>.html that
# works standalone with zero sibling files. Disable with --no-desktop.
set -euo pipefail

ATLAS="${GRAPHIFY_ATLAS:-$HOME/Desktop/Graphify_Codebase_Atlas_2026-08-27}"

# helper: bash path -> Windows path. python3 on this box is a NATIVE Windows
# interpreter, so a MSYS-style /c/... path is not openable by it -- doing so
# raised FileNotFoundError in the deploy step and aborted the build after the
# cosmos data had already been written (2026-10-09). graphify_refresh_all.sh
# carries the same helper for the same reason; keep the two in step.
win_path() { # <bash path>
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$1" 2>/dev/null || echo "$1"
  else
    echo "$1" | sed 's|^/\([a-z]\)/|\1:/|I'
  fi
}
DESKTOP_ROOT="${GRAPHIFY_DESKTOP_ROOT:-$HOME/Desktop}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPORT="$SCRIPT_DIR/cosmos_export.py"
INLINE="$SCRIPT_DIR/cosmos_inline_viewer.py"
SINGLEFILE="$SCRIPT_DIR/cosmos_singlefile.py"
VIEWER_SRC="$SCRIPT_DIR/cosmos-viewer"
VIEWER_BUNDLE="$VIEWER_SRC/dist/viewer.bundle.js"

REPOS=()
DEPLOY_DESKTOP=1
for arg in "$@"; do
  if [ "$arg" = "--no-desktop" ]; then DEPLOY_DESKTOP=0; else REPOS+=("$arg"); fi
done
if [ ${#REPOS[@]} -eq 0 ]; then
  REPOS=(azazael aoa_g aoa_source vermillion_lightfury kali)
fi

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

# Deploy a self-contained viewer copy (html + bundle + data chunks) to the
# Desktop root so it renders standalone from file:// without the atlas path.
deploy_desktop_copy() {
  local repo="$1"
  local name="AZAZAEL_COSMOS_VIEWER"
  [ "$repo" != "azazael" ] && name="AZAZAEL_COSMOS_VIEWER_${repo}"
  local dst="$DESKTOP_ROOT/$name"
  rm -rf "$dst"
  mkdir -p "$dst"
  cp "$ATLAS/$repo/cosmos-viewer.html" "$dst/"
  cp "$ATLAS/$repo/viewer.bundle.js" "$dst/"
  cp -r "$ATLAS/$repo/cosmos" "$dst/"
  echo "[cosmos] desktop copy: $dst ($(ls "$dst/cosmos" | wc -l) chunks)"
}

# Build the bundle once if it's stale or missing.
if [ ! -f "$VIEWER_BUNDLE" ]; then
  log "building Cosmos viewer bundle"
  (cd "$VIEWER_SRC" && npx esbuild src/viewer.js --bundle --format=iife --minify \
      --outfile=dist/viewer.bundle.js >/dev/null)
fi

for repo in "${REPOS[@]}"; do
  graph="$ATLAS/$repo/graph.json"
  if [ ! -f "$graph" ]; then
    echo "[cosmos] SKIP $repo (no graph.json)"
    continue
  fi
  log "[$repo] converting graph.json -> cosmos/ data"
  python3 "$EXPORT" "$graph" "$ATLAS/$repo/cosmos" --label "$repo"
  log "[$repo] deploying viewer"
   cp "$VIEWER_SRC/dist/viewer.bundle.js" "$ATLAS/$repo/viewer.bundle.js"
   nodes=$(python3 -c "import json; print(len(json.load(open(r'$(win_path "$graph")', encoding='utf-8'))['nodes']))")
  # Generate the label-chunk <script> tags dynamically (small repos have 1 chunk,
  # azazael has 11) and substitute into the HTML template.
  nchunks=$(ls "$ATLAS/$repo/cosmos"/graph.labels.[0-9]*.js 2>/dev/null | wc -l)
  tags=""
  for ((i=0; i<nchunks; i++)); do
    tags+="<script src=\"cosmos/graph.labels.${i}.js\"></script>\n"
  done
  python3 - "$VIEWER_SRC/src/viewer.html" "$ATLAS/$repo/cosmos-viewer.html" "$repo" "$nodes" "$tags" <<'PYEOF' 2>&1 || echo "[cosmos] ERROR deploying viewer for $repo"
import sys
src, dst, repo, nodes, tags = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
html = open(src, encoding="utf-8").read()
html = html.replace("%LABEL%", repo).replace("%NODES%", nodes)
# replace the fixed label chunk block (from the first labels.0 to the concat file)
start = html.index('<script src="cosmos/graph.labels.0.js"></script>')
end = html.index('<script src="cosmos/graph.labels.js"></script>')
html = html[:start] + tags + html[end:]
open(dst, "w", encoding="utf-8").write(html)
PYEOF
  echo "[cosmos] $repo: $nodes nodes, $nchunks label chunks, cosmos-viewer.html deployed"
  if [ "$DEPLOY_DESKTOP" -eq 1 ]; then
    deploy_desktop_copy "$repo"
    # Single-file standalone HTML for repos whose data is small enough to inline.
    if [ "$repo" != "azazael" ]; then
      python3 "$INLINE" "$ATLAS/$repo" "$DESKTOP_ROOT/AZAZAEL_COSMOS_VIEWER_${repo}.html" 2>/dev/null \
        || python3 "$INLINE" "$DESKTOP_ROOT/AZAZAEL_COSMOS_VIEWER_${repo}" "$DESKTOP_ROOT/AZAZAEL_COSMOS_VIEWER_${repo}.html" \
        || echo "[cosmos] WARN could not inline $repo"
    else
      # azazael: compressed single-file (20MB vs 297MB chunked) with the
      # precomputed static layout — physics off, renders instantly. Built
      # from the atlas cosmos/ data and staged both in the atlas folder and
      # at the Desktop root.
      if [ -f "$VIEWER_BUNDLE" ]; then
        python3 "$SINGLEFILE" "$ATLAS/$repo/cosmos" \
            "$ATLAS/$repo/cosmos-viewer-single.html" \
            --bundle "$VIEWER_BUNDLE" --label "$repo" \
            || echo "[cosmos] WARN could not build single-file viewer for $repo"
        cp -f "$ATLAS/$repo/cosmos-viewer-single.html" \
              "$DESKTOP_ROOT/AZAZAEL_COSMOS_VIEWER_SINGLE.html" 2>/dev/null \
          || echo "[cosmos] WARN could not stage single-file on Desktop"
      else
        echo "[cosmos] WARN viewer bundle missing - skipping single-file for $repo"
      fi
    fi
  fi
done

if [ "$DEPLOY_DESKTOP" -eq 1 ]; then
  log "done. Open <atlas>/<repo>/cosmos-viewer.html or Desktop/AZAZAEL_COSMOS_VIEWER*/cosmos-viewer.html"
else
  log "done (--no-desktop). Open <atlas>/<repo>/cosmos-viewer.html"
fi
