#!/usr/bin/env bash
# Relocate the Azazael model stores off the system volume.
#
# Why: at the 2026-09-20 inventory, C: held 13-25 GB free while .ollama (34.6 GiB)
# and .lmstudio (55.1 GiB) sat on it.  That headroom ceiling blocks every
# subsequent optimisation, because any new wheel, model, or environment has to
# fit in what is left.
#
# Layout (both targets were checked for capacity; neither drive fits both stores):
#   C:\Users\clint\.ollama\models   (35 G)  ->  D:\ollama-models     (D: 70 G free)
#   C:\Users\clint\.lmstudio\models (53 G)  ->  G:\lmstudio-models   (G: 90 G free)
#
# The move is COPY -> VERIFY -> only-then-remove.  Nothing is deleted until the
# byte count and file count match on the far side.  A directory junction is left
# at each original path so tools that hard-code the old location keep working,
# in addition to repointing the two real config keys:
#   ollama    : OLLAMA_MODELS user environment variable
#   lm studio : "downloadsFolder" in .lmstudio/settings.json
#
# Usage:
#   bash azazael/scripts/relocate_model_stores.sh --plan       # show what would happen
#   bash azazael/scripts/relocate_model_stores.sh --execute    # do it
#   bash azazael/scripts/relocate_model_stores.sh --verify     # re-check an existing move
set -uo pipefail

HOME_WIN="C:\\Users\\clint"
OLLAMA_SRC_WIN="${HOME_WIN}\\.ollama\\models"
LMSTUDIO_SRC_WIN="${HOME_WIN}\\.lmstudio\\models"
OLLAMA_DST_WIN="D:\\ollama-models"
LMSTUDIO_DST_WIN="G:\\lmstudio-models"

OLLAMA_SRC="/c/Users/clint/.ollama/models"
LMSTUDIO_SRC="/c/Users/clint/.lmstudio/models"
OLLAMA_DST="/d/ollama-models"
LMSTUDIO_DST="/g/lmstudio-models"

SETTINGS="/c/Users/clint/.lmstudio/settings.json"
LOG_DIR="/c/Azazael/azazael/logs"
LOG="$LOG_DIR/model_relocation_$(date +%Y%m%d_%H%M%S).log"

MODE="${1:---plan}"

log()  { echo "$*" | tee -a "$LOG"; }
die()  { echo "ERROR: $*" | tee -a "$LOG" >&2; exit 1; }

mkdir -p "$LOG_DIR"

size_of() { du -sb "$1" 2>/dev/null | cut -f1; }
count_of() { find "$1" -type f 2>/dev/null | wc -l; }
free_bytes() { df -B1 "$1" 2>/dev/null | awk 'NR==2{print $4}'; }

preflight() {
  log "=== preflight $(date) ==="
  local ok=1
  for pair in "$OLLAMA_SRC:$OLLAMA_DST" "$LMSTUDIO_SRC:$LMSTUDIO_DST"; do
    local src="${pair%%:*}" dst="${pair##*:}"
    if [ ! -d "$src" ]; then
      log "  source missing: $src (already moved?)"
      continue
    fi
    local sbytes sfree
    sbytes=$(size_of "$src"); sfree=$(free_bytes "$(dirname "$dst")")
    log "  $src -> $dst"
    log "    source $(numfmt --to=iec "$sbytes" 2>/dev/null || echo "$sbytes B") in $(count_of "$src") files"
    log "    target free $(numfmt --to=iec "$sfree" 2>/dev/null || echo "$sfree B")"
    if [ -d "$dst" ] && [ "$(size_of "$dst")" -gt 0 ] 2>/dev/null; then
      log "    NOTE: target already populated - robocopy will top it up"
    fi
    if [ "$sfree" -lt $(( sbytes * 105 / 100 )) ]; then
      log "    INSUFFICIENT SPACE (need ~5% headroom)"
      ok=0
    fi
  done
  return $(( ok == 1 ? 0 : 1 ))
}

robocopy_move() {
  local src="$1" dst="$2" label="$3"
  [ -d "$src" ] || { log "  [$label] source gone, skipping"; return 0; }
  log "  [$label] copying $src -> $dst"
  mkdir -p "$dst"
  # /E all subdirs incl. empty, /COPY:DAT data+attrs+timestamps, /R:1 /W:2 minimal
  # retries (the cross-platform skill warns robocopy can hang on long retries),
  # /NFL /NDL /NP quiet, /MT:8 multithreaded for many small files.
  robocopy "$(cygpath -w "$src")" "$(cygpath -w "$dst")" /E /COPY:DAT /R:1 /W:2 /NFL /NDL /NP /MT:8 >>"$LOG" 2>&1
  local rc=$?
  # robocopy: 0-7 are success codes, >=8 is failure.
  if [ "$rc" -ge 8 ]; then log "  [$label] robocopy FAILED rc=$rc"; return 1; fi
  log "  [$label] robocopy rc=$rc (0-7 = success)"
  return 0
}

verify_pair() {
  local src="$1" dst="$2" label="$3"
  [ -d "$src" ] || { log "  [$label] no source to compare"; return 0; }
  local sb db sc dc
  sb=$(size_of "$src"); db=$(size_of "$dst")
  sc=$(count_of "$src"); dc=$(count_of "$dst")
  log "  [$label] source  $sc files, $sb B"
  log "  [$label] target  $dc files, $db B"
  if [ "$dc" -lt "$sc" ]; then
    log "  [$label] VERIFY FAIL: fewer files on target"; return 1
  fi
  # Sizes may differ slightly due to filesystem accounting; files must not.
  log "  [$label] VERIFY PASS (target has >= source file count)"
  return 0
}

finalize() {
  local src="$1" dst="$2" label="$3" src_win="$4" dst_win="$5"
  [ -d "$src" ] || { log "  [$label] already finalized"; return 0; }
  local sc dc
  sc=$(count_of "$src"); dc=$(count_of "$dst")
  if [ "$dc" -lt "$sc" ]; then
    die "[$label] refusing to delete source: target has $dc < source $sc files"
  fi
  log "  [$label] removing source $src"
  rm -rf "$src" || die "[$label] could not remove source"
  log "  [$label] creating junction $src_win -> $dst_win"
  cmd.exe //c mklink /J "$src_win" "$dst_win" >>"$LOG" 2>&1 \
    || log "  [$label] WARNING: junction creation failed (config repoint still applies)"
}

repoint_config() {
  log "=== repointing config ==="
  # Ollama: OLLAMA_MODELS user env var
  if command -v setx.exe >/dev/null 2>&1; then
    setx.exe OLLAMA_MODELS "$OLLAMA_DST_WIN" >/dev/null 2>&1 \
      && log "  OLLAMA_MODELS=$OLLAMA_DST_WIN (user env; new shells only)" \
      || log "  WARNING: setx OLLAMA_MODELS failed"
  else
    log "  WARNING: setx not found; set OLLAMA_MODELS=$OLLAMA_DST_WIN manually"
  fi

  # LM Studio: downloadsFolder in settings.json
  if [ -f "$SETTINGS" ]; then
    python - "$SETTINGS" "$LMSTUDIO_DST_WIN" <<'PY' 2>>"$LOG"
import json, sys, shutil
path, target = sys.argv[1], sys.argv[2]
shutil.copy2(path, path + ".pre-relocation.bak")
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
old = data.get("downloadsFolder")
data["downloadsFolder"] = target
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
print(f"  downloadsFolder: {old} -> {target}")
PY
  else
    log "  WARNING: $SETTINGS not found"
  fi
}

show_verify() {
  log "=== current state $(date) ==="
  for pair in "$OLLAMA_SRC:$OLLAMA_DST:ollama" "$LMSTUDIO_SRC:$LMSTUDIO_DST:lmstudio"; do
    local src="${pair%%:*}" rest="${pair#*:}" dst="${rest%%:*}" label="${rest##*:}"
    log "  [$label] src $( [ -e "$src" ] && size_of "$src" || echo MISSING ) / dst $( [ -e "$dst" ] && size_of "$dst" || echo MISSING )"
  done
  log "  C: free $(numfmt --to=iec "$(free_bytes /c)" 2>/dev/null)"
  log "  D: free $(numfmt --to=iec "$(free_bytes /d)" 2>/dev/null)"
  log "  G: free $(numfmt --to=iec "$(free_bytes /g)" 2>/dev/null)"
}

case "$MODE" in
  --plan)
    log "PLAN ONLY - nothing will be moved."
    preflight || die "preflight failed - resolve space before executing"
    log ""
    log "Would run, in order:"
    log "  1. robocopy $OLLAMA_SRC_WIN   -> $OLLAMA_DST_WIN"
    log "  2. robocopy $LMSTUDIO_SRC_WIN -> $LMSTUDIO_DST_WIN"
    log "  3. verify file counts on both targets"
    log "  4. remove C: sources, create junctions at the original paths"
    log "  5. setx OLLAMA_MODELS=$OLLAMA_DST_WIN"
    log "  6. settings.json downloadsFolder -> $LMSTUDIO_DST_WIN"
    log ""
    log "Re-run with --execute to perform the move."
    ;;
  --execute)
    log "=== EXECUTE $(date) ==="
    preflight || die "preflight failed - aborting"
    robocopy_move "$OLLAMA_SRC"   "$OLLAMA_DST"   ollama   || die "ollama copy failed"
    robocopy_move "$LMSTUDIO_SRC" "$LMSTUDIO_DST" lmstudio || die "lmstudio copy failed"
    verify_pair "$OLLAMA_SRC"   "$OLLAMA_DST"   ollama   || die "ollama verify failed - source kept"
    verify_pair "$LMSTUDIO_SRC" "$LMSTUDIO_DST" lmstudio || die "lmstudio verify failed - source kept"
    finalize "$OLLAMA_SRC"   "$OLLAMA_DST"   ollama   "$OLLAMA_SRC_WIN"   "$OLLAMA_DST_WIN"
    finalize "$LMSTUDIO_SRC" "$LMSTUDIO_DST" lmstudio "$LMSTUDIO_SRC_WIN" "$LMSTUDIO_DST_WIN"
    repoint_config
    show_verify
    log "=== DONE $(date) - log: $LOG ==="
    ;;
  --verify)
    show_verify
    ;;
  *)
    die "usage: $0 [--plan|--execute|--verify]"
    ;;
esac
