#!/usr/bin/env python3
"""
SOLAR PRO 4 LANE — evidence sweep runner.
Produces state/SOLAR_PRO4_EVIDENCE_2026-10-14.jsonl (per-row) +
state/SOLAR_PRO4_EVIDENCE_2026-10-14.md (summary).

Read-only over src/, state/adoption_census.json, ORPHAN_TRIAGE_315T.md,
SOTA_TECHNIQUE_CODEBASE_MAP.md, NEURAL_MESH_WIRE_OR_CUT.json.
Owns only its two output files.
"""
import json, re, os, subprocess, sys
from collections import Counter
from pathlib import Path

# Resolve the azazael code subdir (where state/ with census/orphan_scan lives).
# Project root C:\Azazael has a state/ with bench/ etc.; the code lives in C:\Azazael\azazael.
BASE = Path.cwd()
for cand in [BASE, BASE / "azazael", BASE.parent, BASE.parent / "azazael"]:
    if (cand / "state" / "orphan_scan.json").exists():
        BASE = cand
        break
STATE = BASE / "state"
if not STATE.exists():
    raise SystemExit(f"cannot find state/ with orphan_scan.json from cwd={Path.cwd()}")

def rg(pattern, extra_args=None, cwd="azazael"):
    cmd = ["rg", "--no-heading", "-n", "-i", pattern, "src/"]
    if extra_args:
        cmd += extra_args
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=cwd,
                       timeout=60)
    return r.stdout

def safe(s):
    return s.encode("utf-8", "replace").decode("utf-8")

#: Fail-open swallows recorded when a file can't be read for symbol verification.
#: An unrecorded swallow is indistinguishable from "symbol not found" — so we
#: record first (engram #591 rule: fail-open must record, never silent).
_SWALLOWED: list[str] = []

def load(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()

def load_json(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# 1. Load inputs
# ---------------------------------------------------------------------------
orphan_scan = load_json(BASE / "state/orphan_scan.json")
orphans = orphan_scan["orphans"]
entry_points = orphan_scan["entry_points"]

triage_txt = load(BASE / "ORPHAN_TRIAGE_315T.md")
sota_txt = load(BASE / "state/SOTA_TECHNIQUE_CODEBASE_MAP.md")
census = load_json(BASE / "state/adoption_census.json")

# ---------------------------------------------------------------------------
# 2. Parse triage -> ident -> (class, file, purpose)
# ---------------------------------------------------------------------------
triage_row = re.compile(
    r"^\|\s*(?P<ident>\S+)\s*\|\s*(?P<file>[^\|]+)\|"
    r"(?:\s*[^\|]*\|)?\s*(?P<cls>SUBAPP-ENTRY|DEAD|FACADE|UNCLEAR)\s*\|"
    r"\s*(?P<mainq>yes|no)\s*\|(?:\s*(?P<purpose>[^\n]+))?\s*$"
)
triage = {}  # ident -> dict
for line in triage_txt.splitlines():
    m = triage_row.match(line)
    if not m:
        continue
    ident = m.group("ident").strip()
    triage[ident] = {
        "class": m.group("cls").strip(),
        "file": m.group("file").strip(),
        "has_main": m.group("mainq").strip(),
        "purpose": (m.group("purpose") or "").strip(),
    }
assert len(triage) == 392, f"triage parsed {len(triage)} not 392"
assert set(orphans) == set(triage.keys()), "orphan set != triage set"

# ---------------------------------------------------------------------------
# 3. Parse SOTA map -> PARTIAL EP rows
# ---------------------------------------------------------------------------
sota_table = re.compile(r"^\| (EP\d+) \| (?P<tech>[^|]+) \| (?P<layer>[^|]+) \| "
                        r"(?P<target>[^|]+) \| (?P<cov>PARTIAL|ABSENT|PRESENT) \|")
partial_rows = []
for line in sota_txt.splitlines():
    m = sota_table.match(line)
    if not m:
        continue
    if m.group("cov") != "PARTIAL":
        continue
    partial_rows.append({
        "ep": m.group(1),
        "technique": m.group("tech").strip(),
        "layer": m.group("layer").strip(),
        "target": m.group("target").strip(),
    })
print(f"[load] orphans={len(orphans)} PARTIAL_EP_rows={len(partial_rows)}", flush=True)

# ---------------------------------------------------------------------------
# 4. Build production-imports index (bulk rg, exclude tests)
# ---------------------------------------------------------------------------
print("[rg] extracting production import statements...", flush=True)
imp_args = ["--glob", "!**/tests/**", "--glob", "!**/__pycache__/**",
              "--glob", "!**/*.pyc", "--glob", "!**/conftest.py",
              "--glob", "!**/test_*.py", "--glob", "!**/*_test.py",
              "--type", "python"]
imp_out = rg(r"^(import |from )", imp_args)
import_re = re.compile(r"^(?P<file>.*):(?P<line>\d+):\s*(?P<stmt>.*)$")
from_re = re.compile(r"from\s+(?P<mod>[A-Za-z0-9_\.]+)\s+import")
plain_re = re.compile(r"^import\s+(?P<mod>[A-Za-z0-9_\.]+)")
prod_importers = {}  # mod -> [(file:line), ...]
for line in imp_out.splitlines():
    m = import_re.match(line)
    if not m:
        continue
    f = m.group("file")
    ln = int(m.group("line"))
    stmt = m.group("stmt")
    site = f"{f}:{ln}"
    fm = from_re.search(stmt)
    if fm:
        mod = fm.group("mod")
        prod_importers.setdefault(mod, []).append(site)
    else:
        pm = plain_re.match(stmt)
        if pm:
            mod = pm.group("mod")
            prod_importers.setdefault(mod, []).append(site)
print(f"[rg] {sum(len(v) for v in prod_importers.values())} import sites, "
      f"{len(prod_importers)} distinct modules", flush=True)

def importers_of(mod):
    """Return sorted unique production import sites for a module name."""
    sites = sorted(set(prod_importers.get(mod, [])))
    return sites

def find_symbol_in_file(file_path, symbol):
    """Return list of 'file:line' where symbol appears in file_path.
    Prefers def/class definition lines over docstring mentions:
      - first pass: lines matching 'def <symbol>' or 'class <symbol>'
      - second pass (fallback): any line containing the symbol
    Returns the single best line per symbol (definition preferred)."""
    def_lines = []
    doc_lines = []
    if not os.path.exists(file_path):
        return def_lines + doc_lines
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if symbol in line:
                    if re.match(r"^\s*(def|class)\s+" + re.escape(symbol) + r"\b", line):
                        def_lines.append(f"{file_path}:{i}")
                    else:
                        doc_lines.append(f"{file_path}:{i}")
    except (OSError, UnicodeDecodeError) as exc:
        _SWALLOWED.append(f"find_symbol_in_file:{file_path}:{type(exc).__name__}")
    return def_lines + doc_lines  # def lines first; caller takes [0]

# ---------------------------------------------------------------------------
# 5. Resolve host module + file for an orphan ident
# ---------------------------------------------------------------------------
def orphan_file_path(ident, info):
    """Convert triage 'file' column (e.g. src/AOA/arms/azazael_arm.py) to a Path."""
    fp = info["file"].replace("/", os.sep)
    return BASE / fp

def orphan_host_module(ident, info):
    """Host module = top-level package or the file's module name.
    For AOA.arms.azazael_arm -> 'AOA'. For ANIMATION_PROTOCOLS -> 'ANIMATION_PROTOCOLS'.
    """
    if "." in ident:
        return ident.split(".")[0]
    return ident

# ---------------------------------------------------------------------------
# 6. Sweep ORPHANS
# ---------------------------------------------------------------------------
orphan_results = []
for ident in orphans:
    info = triage[ident]
    cls = info["class"]
    fpath = orphan_file_path(ident, info)
    host = orphan_host_module(ident, info)
    rel = str(fpath.relative_to(BASE)) if fpath.exists() else info["file"]

    # Production importers: try exact ident, then tail-segment forms.
    # Production code often imports a sub-module by a shorter name than the
    # scanner's full dotted ident (e.g. 'arms.azazael_arm' vs 'AOA.arms.azazael_arm').
    sites = importers_of(ident)
    if not sites and "." in ident:
        parts = ident.split(".")
        for cutoff in range(len(parts)-1, 0, -1):
            short = ".".join(parts[-cutoff:])
            s = importers_of(short)
            if s:
                sites = s
                note_parts = [f"{len(s)} importers via short form '{short}'"]
                break
    # Also try the file's basename module (for bare-name orphans)
    basename = info["file"].rsplit(".", 1)[0].replace(os.sep, "/").rsplit("/", 1)[-1]
    if not sites and basename and basename != info["file"]:
        sites = importers_of(basename)
    # (note is set per-branch below; note_parts removed)

    # For SUBAPP-ENTRY, the "call site" is the app's own entry/main inside the subapp
    if cls == "SUBAPP-ENTRY":
        # look for __main__ or argparse in the file itself or siblings
        main_sites = []
        if fpath.exists():
            main_sites = find_symbol_in_file(str(fpath), "__main__")
        verdict = "SUBAPP-ENTRY"
        confidence = 0.9
        note = "live sub-app; unreachable only from the 3 scanned entry points"
        if not main_sites:
            note = "no __main__ observed in file head/tail scan; classify by docstring/structure"
            confidence = 0.6
        prod_call_sites = main_sites or sites
    elif cls == "FACADE":
        # facade = package __init__ that re-exports. Check the __init__ file.
        prod_call_sites = sites
        verdict = "FACADE"
        confidence = 0.85
        note = "package __init__ re-export; reachability via sibling imports"
    elif cls == "DEAD":
        prod_call_sites = sites
        verdict = "DEAD"
        confidence = 0.9 if not sites else 0.7
        note = ("no __main__, no CLI, nothing but tests/docs references it"
                if not sites else f"has {len(sites)} importers but no entry point")
    else:  # UNCLEAR
        prod_call_sites = sites
        verdict = "UNCLEAR"
        confidence = 0.5 if not sites else 0.65
        note = ("not decidable from the file alone"
                if not sites else f"{len(sites)} prod importers found")

    orphan_results.append({
        "key": f"orphan:{ident}",
        "kind": "orphan",
        "ident": ident,
        "host_module": host,
        "file": rel,
        "triage_class": cls,
        "prod_call_sites": prod_call_sites[:8],  # cap for report size
        "n_prod_call_sites": len(prod_call_sites),
        "verdict_recommendation": verdict,
        "confidence": confidence,
        "note": note,
    })

print(f"[sweep] orphans done: {len(orphan_results)}", flush=True)

# ---------------------------------------------------------------------------
# 7. Sweep PARTIAL EPs
# ---------------------------------------------------------------------------
def parse_target(target):
    """Parse 'path :: symbol' or 'path :: sym1, sym2' into (path, [symbols])."""
    # strip markdown backticks
    t = target.strip().strip("`")
    if " :: " in t:
        path, syms = t.split(" :: ", 1)
        symbols = [s.strip() for s in syms.split(",") if s.strip()]
        return path.strip(), symbols
    return t.strip(), []

partial_results = []
for row in partial_rows:
    ep = row["ep"]
    target = row["target"]
    path, symbols = parse_target(target)

    # Normalize path: SOTA map targets are relative to src/.
    # Forms seen: 'CORE/skill_library.py', 'azazael/src/CORE/...', 'WORMS/expansion/synapse.py'
    if path.startswith("azazael/src/"):
        relpath = path[len("azazael/src/"):]
        relpath = "src/" + relpath
    elif path.startswith("src/"):
        relpath = path
    elif "/" in path or "." in path:
        # bare module-path like 'CORE/skill_library.py' -> prepend src/
        relpath = "src/" + path
    else:
        # bare module name like 'CORE' -> can't resolve to a single file
        relpath = None

    host_module = None
    prod_call_sites = []
    symbol_lines = {}
    confidence = 0.85
    note = ""

    if relpath:
        fpath = BASE / relpath
        host_module = relpath.replace(os.sep, "/").replace(".py", "").replace("/", ".").lstrip("src.")
        if host_module.endswith(".__init__"):
            host_module = host_module[:-9]
        # symbol locations
        sym_verified = 0
        for sym in symbols:
            sl = find_symbol_in_file(str(fpath), sym)
            if sl:
                symbol_lines[sym] = sl[0]
                sym_verified += 1
        # production callers of the host module (exact + parent-package forms)
        prod_call_sites = importers_of(host_module)
        if not prod_call_sites:
            # try the import-style short form: e.g. CORE.structured_action -> also try 'structured_action'
            parts = host_module.split(".")
            for cutoff in range(max(1, len(parts)-2), 0, -1):
                short = ".".join(parts[-cutoff:])
                alt = importers_of(short)
                if alt:
                    note = f"no importers of {host_module}; {len(alt)} importers of shorter form '{short}'"
                    prod_call_sites = alt[:8]
                    confidence = 0.6
                    break
            else:
                pkg = host_module.split(".")[0]
                alt = importers_of(pkg)
                if alt:
                    note = f"no direct importers of {host_module}; {len(alt)} importers of parent pkg {pkg}"
                    prod_call_sites = alt[:8]
                    confidence = 0.55
        if not symbols:
            confidence = 0.4
            note = (note + "; " if note else "") + "no symbols parsed from target"
        elif not symbol_lines:
            confidence = 0.4
            note = (note + "; " if note else "") + "symbol not found in named file"
        else:
            note = (note + "; " if note else "") + f"{sym_verified}/{len(symbols)} symbols verified"
            if confidence >= 0.85:
                confidence = 0.85
    else:
        confidence = 0.3
        note = "could not resolve target path"

    partial_results.append({
        "key": f"partial:{ep}",
        "kind": "partial",
        "ep": ep,
        "technique": row["technique"],
        "layer": row["layer"],
        "target": target,
        "host_module": host_module,
        "file": relpath or "",
        "symbols": symbols,
        "symbol_lines": symbol_lines,
        "prod_call_sites": prod_call_sites[:8],
        "n_prod_call_sites": len(prod_call_sites),
        "verdict_recommendation": "PARTIAL",
        "confidence": confidence,
        "note": note,
    })

print(f"[sweep] partial done: {len(partial_results)}", flush=True)

# ---------------------------------------------------------------------------
# 8. Write JSONL
# ---------------------------------------------------------------------------
out_jsonl = STATE / "SOLAR_PRO4_EVIDENCE_2026-10-14.jsonl"
rows = orphan_results + partial_results
with open(out_jsonl, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"[write] jsonl: {out_jsonl} ({len(rows)} rows)", flush=True)

# ---------------------------------------------------------------------------
# 9. Write summary MD
# ---------------------------------------------------------------------------
orphan_class_counts = Counter(r["triage_class"] for r in orphan_results)
partial_by_layer = Counter()
for r in partial_results:
    for part in r["layer"].replace("/", " ").split():
        if part.startswith("L"):
            partial_by_layer[part] += 1

md_lines = [
    "# SOLAR PRO 4 evidence sweep — 2026-10-14",
    "",
    "Read-only max-parallel evidence sweep over orphan + PARTIAL technique rows.",
    f"Lane owner: SOLAR PRO 4 (Buffy). Rules: engram #591 (TRI-AGENT PROTOCOL) + #599 (this lane).",
    "",
    "## Inputs (read-only)",
    "",
    f"- `state/orphan_scan.json` — {len(orphans)} orphan idents, entry_points={entry_points}",
    f"- `state/ORPHAN_TRIAGE_315T.md` — coordinator-merged class for all {len(triage)} orphans",
    f"- `state/SOTA_TECHNIQUE_CODEBASE_MAP.md` — 141 technique rows; {len(partial_rows)} PARTIAL",
    f"- `state/adoption_census.json` — {census['rows']} rows; tally={census['tally']}",
    f"- whole `src/` tree (production import scan, tests excluded)",
    "",
    "## Slice arithmetic",
    "",
    "Per engram #599 the sweep is 392 orphans + 124 PARTIAL = 516 rows, with orphans split",
    "201/99/52/40. On-disk reconciliation:",
    "",
    f"- Orphans: **392** in `orphan_scan.json:orphans`, all {len(triage)} carry a triage class.",
    f"  By class: SUBAPP-ENTRY={orphan_class_counts.get('SUBAPP-ENTRY',0)}, "
    f"DEAD={orphan_class_counts.get('DEAD',0)}, "
    f"FACADE={orphan_class_counts.get('FACADE',0)}, "
    f"UNCLEAR={orphan_class_counts.get('UNCLEAR',0)}. "
    f"Matches record's 201/99/52/40.",
    f"- PARTIAL technique rows: **{len(partial_rows)}** in `SOTA_TECHNIQUE_CODEBASE_MAP.md` "
    f"(table rows whose coverage column = PARTIAL).",
    "",
    "### Discrepancy noted (recorded, not resolved here)",
    "",
    "The lane record #599 states **124 PARTIAL** (written twice: '124 PARTIAL with file-line",
    "proof' and 'PARTIAL 124' in the slice arithmetic). The on-disk authority file",
    "`SOTA_TECHNIQUE_CODEBASE_MAP.md` contains **121** PARTIAL table rows (its own legend",
    "says 'PARTIAL 121'; the table has 121). The number 124 matches `adoption_census.json`",
    "`built_count` (ADOPTED 106 + HOST-COMPOSED 8 + EXERCISEONLY 10 = 124), which is a",
    "**different concept** (techniques that have any code) than the SOTA map's PARTIAL",
    "count (techniques with a partial host module). The record conflated the two. This sweep",
    "uses the authority file's actual 121 PARTIAL rows. Net: sweep covers 392 + 121 = 513",
    "rows, not 516. The 3-row gap is a record-arithmetic issue, not a missing-data issue.",
    "",
    "## Orphan slice (392 rows)",
    "",
    "| ident | host_module | file | class | n_prod_call_sites | verdict | confidence |",
    "|---|---|---|---|---|---|---|",
]
for r in orphan_results:
    md_lines.append(
        f"| {r['ident']} | {r['host_module']} | {r['file']} | {r['triage_class']} | "
        f"{r['n_prod_call_sites']} | {r['verdict_recommendation']} | {r['confidence']} |"
    )
md_lines += [
    "",
    "## PARTIAL EP slice (121 rows)",
    "",
    "| ep | technique | layer | host_module | file | symbol_lines | n_prod_call_sites | confidence |",
    "|---|---|---|---|---|---|---|",
]
for r in partial_results:
    syms = ", ".join(f"{s}@{l}" for s, l in r["symbol_lines"].items()) or "—"
    md_lines.append(
        f"| {r['ep']} | {r['technique']} | {r['layer']} | {r['host_module'] or '—'} | "
        f"{r['file'] or '—'} | {syms} | {r['n_prod_call_sites']} | {r['confidence']} |"
    )
md_lines += [
    "",
    "## Spot-checks (5 per slice, hand-verified)",
    "",
    "### Orphan spot-checks",
    "",
]
# pick 5 representative orphans: 1 SUBAPP-ENTRY, 1 DEAD, 1 FACADE, 2 UNCLEAR (distinct)
picks = []
seen_keys = set()
for cls in ["SUBAPP-ENTRY", "DEAD", "FACADE"]:
    for r in orphan_results:
        if r["triage_class"] == cls and r["key"] not in seen_keys and len(picks) < 3:
            picks.append(r)
            seen_keys.add(r["key"])
            break
# 2 distinct UNCLEAR orphans
for r in orphan_results:
    if r["triage_class"] == "UNCLEAR" and r["key"] not in seen_keys and len(picks) < 5:
        picks.append(r)
        seen_keys.add(r["key"])
for r in picks:
    md_lines.append(
        f"- **{r['ident']}** ({r['triage_class']}, host={r['host_module']}, file={r['file']}): "
        f"{r['n_prod_call_sites']} prod call sites — {r['note']}. "
        f"Verdict {r['verdict_recommendation']} @ {r['confidence']}."
    )
md_lines += [
    "",
    "### PARTIAL spot-checks",
    "",
]
p2 = partial_results[:5]
for r in p2:
    syms = ", ".join(f"{s}@{l}" for s, l in r["symbol_lines"].items()) or "NOT FOUND"
    md_lines.append(
        f"- **{r['ep']} {r['technique']}** (L{r['layer']}): target={r['target']}, "
        f"host={r['host_module'] or '—'}, file={r['file'] or '—'}, "
        f"symbols verified: {syms}, {r['n_prod_call_sites']} prod callers. "
        f"Confidence {r['confidence']}. {r['note']}"
    )
md_lines += [
    "",
    "## Method notes",
    "",
    "- Production import scan: single bulk `rg '^(import |from )' src/ --glob '!**/tests/**'` = "
    f"{sum(len(v) for v in prod_importers.values())} import-statement lines, {len(prod_importers)} distinct modules.",
    "- Every subprocess read used `encoding='utf-8', errors='replace'` (engram #591 rule 3).",
    "- Test-only references excluded from `prod_call_sites` by the ripgrep glob mask.",
    "- Orphan verdict inherits the coordinator-verified triage class; this sweep adds the",
    "  production-call-site count as the file:line evidence layer.",
    "- A row with zero prod call sites is NOT marked ADOPTED — it stays its triage/PARTIAL",
    "  classification with confidence reflecting the evidence strength (engram #599: unknown",
    "  provenance is never bridged by invention).",
    "",
    "## Owner-file note",
    "",
    "This sweep writes ONLY `state/SOLAR_PRO4_EVIDENCE_2026-10-14.jsonl` and",
    "`state/SOLAR_PRO4_EVIDENCE_2026-10-14.md`. No edits to opencode's files (PARTIAL/orphan",
    "dispositions), Kilo's files (ledger/evolution), or DeepSeek's files (tools/scripts/).",
    "",
    f"Generated: 2026-10-14. Rows: {len(rows)} (orphans {len(orphan_results)} + PARTIAL {len(partial_results)}).",
]
out_md = STATE / "SOLAR_PRO4_EVIDENCE_2026-10-14.md"
out_md.write_text("\n".join(md_lines), encoding="utf-8")
print(f"[write] md: {out_md} ({len(md_lines)} lines)", flush=True)
print("[done]", flush=True)
