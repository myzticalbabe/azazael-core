"""Re-extract the SOTA technique corpus into consistent machine-readable form.

Why this exists
---------------
An audit of `state/sota_batches/batch06/` on 2026-10-09 found the derived
artifacts disagree:

    TECHNIQUES.md                 168 entries
    class_part*/class_fill_part*  168 rows
    techniques_manifest.json      168 rows
    technique_index.json          141 rows   <-- short by 27
    technique_codebase_map.json   141 rows   <-- short by 27, totals block stale

The extraction front-end (`TECHNIQUES.md` + the classification tables) is the
source of truth here: it is what a human read the raw `technique/<yt_id>.txt`
notes into. The JSON artifacts are DERIVED, so drift in them is a derived-
artifact defect, not new information -- the same class of defect recorded in
`azazael-stale-artifact-self-audit`.

This tool never invents analysis. It re-derives the mechanical fields
(ep / name / arxiv / mechanism / mixer) from the markdown, reports exactly
which rows the derived artifacts are missing, and refuses to guess the
analytical fields (layer / target / coverage / gap) -- those are listed for
re-mapping instead.

Modes
-----
    --check   diagnose only; write nothing (default)
    --write   regenerate technique_index.json (+ per-batch indexes for 01-05)

Usage:
    python tools/sota_batch06_reextract.py --check
    python tools/sota_batch06_reextract.py --write
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# --- config -----------------------------------------------------------------

HERE = Path(__file__).resolve()
REPO = HERE.parents[1]                      # C:/Azazael/azazael
CANDIDATES = (REPO.parent / "state" / "sota_batches",   # C:/Azazael/state/...
              REPO / "state" / "sota_batches")          # fallback

BATCHES = ("batch01", "batch02", "batch03", "batch04", "batch05", "batch06")
BATCH06 = "batch06"

# "## EP169 — Agentic AI Attack/Defense Survey (2603.11088)"
H_ENTRY = re.compile(r"^##\s+EP(\d+)\s*[-\u2014\u2013]\s*(.*?)\s*$")
# trailing "(2603.11088)" or "(2608.00754v1)"
H_ARXIV = re.compile(r"\((\d{4}\.\d{4,5})(v\d+)?\)\s*$")
# "- Mechanism: ..." / "- Mixer steal: ..." / "- Numbers: ..."
FIELD = re.compile(r"^-\s*([A-Za-z][A-Za-z /-]*?):\s*(.*)$")


def find_state() -> Path:
    for c in CANDIDATES:
        if c.is_dir():
            return c
    sys.exit("no state/sota_batches found under %s" % REPO.parent)


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def parse_techniques(md: str) -> list[dict]:
    """Parse TECHNIQUES.md into entries. Continuation lines fold into the
    preceding field, so wrapped mechanisms survive."""
    entries: list[dict] = []
    cur: dict | None = None
    field: str | None = None

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = H_ENTRY.match(line)
        if m:
            ep, rest = m.group(1), m.group(2)
            arx = ""
            am = H_ARXIV.search(rest)
            if am:
                arx = am.group(1) + (am.group(2) or "")
                rest = rest[: am.start()].strip()
            cur = {"ep": int(ep), "name": rest, "arxiv": arx,
                   "mechanism": "", "mixer": "", "extra": {}}
            entries.append(cur)
            field = None
            continue
        if cur is None:
            continue
        fm = FIELD.match(line.strip())
        if fm:
            key = fm.group(1).strip().lower()
            val = fm.group(2).strip()
            if key == "mechanism":
                field = "mechanism"
                cur["mechanism"] = val
            elif key == "mixer steal" or key == "mixer":
                field = "mixer"
                cur["mixer"] = val
            else:
                field = None
                cur["extra"][key] = val
            continue
        if field:  # wrapped continuation of the current field
            cur[field] = (cur[field] + " " + line.strip()).strip()
    return entries


def load_json(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(read_text(p))
    except json.JSONDecodeError as e:
        print("  !! unparseable JSON: %s (%s)" % (p.name, e))
        return None


def eps_of(rows) -> list[int]:
    """Accept ep as int, 'EP11' or '11'. Never raise on a malformed row."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        v = r.get("ep")
        if isinstance(v, int):
            out.append(v)
        elif isinstance(v, str):
            t = v.strip().lstrip("EPep").lstrip("0")
            if t.isdigit():
                out.append(int(t))
    return out


def dupes(eps: list[int]) -> dict[int, int]:
    seen: dict[int, int] = {}
    for e in eps:
        seen[e] = seen.get(e, 0) + 1
    return {k: v for k, v in seen.items() if v > 1}


def check(state: Path) -> int:
    b6 = state / BATCH06
    tech_md = b6 / "TECHNIQUES.md"
    print("state root: %s" % state)
    if not tech_md.exists():
        print("!! %s missing" % tech_md)
        return 2

    entries = parse_techniques(read_text(tech_md))
    md_eps = [e["ep"] for e in entries]
    print("\n== source of truth ==")
    print("  TECHNIQUES.md        %4d entries" % len(entries))
    md_dupes = dupes(md_eps)
    print("  duplicate EP codes   %4d  %s" % (len(md_dupes), sorted(md_dupes)))
    print("  distinct EP codes    %4d" % len(set(md_eps)))
    no_mech = [e["ep"] for e in entries if not e["mechanism"]]
    no_mix = [e["ep"] for e in entries if not e["mixer"]]
    print("  missing mechanism    %4d  %s" % (len(no_mech), no_mech[:12]))
    print("  missing mixer        %4d  %s" % (len(no_mix), no_mix[:12]))

    # classification tables. Counted over ROWS, not over a dict: the corpus
    # legitimately carries 168 rows for 141 distinct papers (27 EP codes have
    # a second entry), so collapsing to a dict would under-report.
    cls: dict[int, tuple[str, str]] = {}
    cls_rows = 0
    tally: dict[str, int] = {}
    for p in sorted(b6.glob("class*part*.md")):
        for line in read_text(p).splitlines():
            if not line.startswith("| EP"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3 or not cells[0].startswith("EP"):
                continue
            t = cells[0][2:].lstrip("0")
            if not t.isdigit():
                continue
            cls_rows += 1
            tally[cells[2]] = tally.get(cells[2], 0) + 1
            cls.setdefault(int(t), (cells[2], cells[3] if len(cells) > 3 else ""))
    print("\n== classification tables ==")
    print("  rows                 %4d" % cls_rows)
    for k in sorted(tally):
        print("  %-20s %4d" % (k, tally[k]))
    print("  distinct EP codes    %4d" % len(cls))

    # derived artifacts
    idx = load_json(b6 / "technique_index.json")
    man = load_json(b6 / "techniques_manifest.json")
    mapj = load_json(b6 / "technique_codebase_map.json")
    print("\n== derived artifacts ==")
    for label, rows in (("technique_index.json", idx),
                        ("techniques_manifest.json", man),
                        ("technique_codebase_map.json", mapj.get("rows") if isinstance(mapj, dict) else None)):
        e = eps_of(rows)
        d = dupes(e)
        print("  %-28s %4d rows  %4d distinct  %d dup codes"
              % (label, len(rows or []), len(set(e)), len(d)))

    if isinstance(mapj, dict):
        rows = mapj.get("rows") or []
        cov: dict[str, int] = {}
        for r in rows:
            cov[str(r.get("coverage"))] = cov.get(str(r.get("coverage")), 0) + 1
        print("\n== map coverage (recounted from rows) ==")
        for k in sorted(cov):
            print("  %-10s %4d" % (k, cov[k]))
        print("  declared    %s" % json.dumps(mapj.get("totals")))
        declared = sum(mapj.get("totals", {}).values()) if isinstance(mapj.get("totals"), dict) else 0
        if declared != len(rows):
            print("  !! STALE totals: declares %d, file holds %d" % (declared, len(rows)))

    # the drift that matters
    md_set = set(md_eps)
    print("\n== drift (derived vs TECHNIQUES.md) ==")
    if idx is not None:
        miss = sorted(md_set - set(eps_of(idx)))
        print("  index missing %d: %s" % (len(miss), miss))
    if man is not None:
        miss = sorted(md_set - set(eps_of(man)))
        print("  manifest missing %d: %s" % (len(miss), miss))
    if isinstance(mapj, dict):
        miss = sorted(md_set - set(eps_of(mapj.get("rows"))))
        print("  map missing %d: %s" % (len(miss), miss))

    # ep type divergence
    if idx and man:
        ti = {r.get("ep") for r in idx if isinstance(r, dict)}
        tm = {r.get("ep") for r in man if isinstance(r, dict)}
        ti_str = sum(1 for v in ti if isinstance(v, str))
        tm_int = sum(1 for v in tm if isinstance(v, int))
        print("\n== join key ==")
        print("  index    ep types: %d str / %d int" % (ti_str, len(ti) - ti_str))
        print("  manifest ep types: %d int / %d str" % (tm_int, len(tm) - tm_int))
        if ti_str and tm_int:
            print("  !! joining index.ep to manifest.ep fails without normalisation")

    # damaged rows
    if isinstance(mapj, dict):
        bad = [r.get("ep") for r in mapj.get("rows") or []
               if len(str(r.get("gap", ""))) < 25]
        print("\n== damaged map rows (gap under 25 chars) ==")
        print("  %s" % bad)
        for r in mapj.get("rows") or []:
            if r.get("ep") in bad:
                print("  %s -> gap=%r" % (r.get("ep"), r.get("gap")))

    print("\n== per-batch machine-readability ==")
    for b in BATCHES:
        d = state / b
        if not d.is_dir():
            print("  %-8s MISSING" % b)
            continue
        t = d / "TECHNIQUES.md"
        n = len(parse_techniques(read_text(t))) if t.exists() else 0
        js = sorted(p.name for p in d.glob("*.json"))
        print("  %-8s TECHNIQUES=%3d  json=%s" % (b, n, js or "none"))
    return 0


def write_indexes(state: Path, force: bool = False) -> int:
    """Emit technique_index.json for every batch that lacks one.

    Deliberately does NOT overwrite an existing index unless --force: batch06's
    index is deduplicated to 141 rows (one per paper) while its manifest carri es
    168 rows (27 EP codes have a second entry). Regenerating it from TECHNIQUES.md
    would re-introduce those 27 duplicate rows into the index -- a regression, not
    a repair. Batches 01-05 have no index at all, so they are simply added.
    """
    written = []
    skipped = []
    for b in BATCHES:
        d = state / b
        t = d / "TECHNIQUES.md"
        if not t.exists():
            print("skip %s (no TECHNIQUES.md)" % b)
            continue
        out = d / "technique_index.json"
        if out.exists() and not force:
            skipped.append((out, len(load_json(out) or [])))
            print("keep  %s (exists, %d rows; use --force to regenerate)"
                  % (out, len(load_json(out) or [])))
            continue
        entries = parse_techniques(read_text(t))
        rows = [{
            "ep": "EP%d" % e["ep"],           # keep the historical string form
            "ep_num": e["ep"],                # additive, for reliable joins
            "name": e["name"],
            "arxiv": e["arxiv"],
            "mechanism": e["mechanism"],
            "mixer": e["mixer"],
        } for e in entries]
        out.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        written.append((out, len(rows)))
        print("wrote %s (%d rows)" % (out, len(rows)))
    print("\n%d written, %d kept" % (len(written), len(skipped)))
    return 0 if written else 1


# --- review coverage ---------------------------------------------------------

REVIEW_DEFAULT = REPO / "state" / "SOTA_FULL_REVIEW_2026-10-09.md"

# the verdict vocabulary of the review tables
VERDICTS = ("PRESENT", "APPLIED", "PARTIAL", "ABSENT", "EXTEND-EXISTING",
            "NO-HOST", "SURVEY")
ROW = re.compile(r"^\|\s*EP(\d+)\b")


def corpus_eps(state: Path) -> dict[int, str]:
    """The north star: distinct EP per harvested paper, across every batch.

    batch06 holds 168 ENTRIES but 141 distinct EP codes (27 codes carry a
    second entry); batches 01-05 hold one entry each. So the defensible
    denominator is the DISTINCT code count, not the entry count.
    """
    out: dict[int, str] = {}
    for b in BATCHES:
        t = state / b / "TECHNIQUES.md"
        if t.exists():
            for e in parse_techniques(read_text(t)):
                out.setdefault(e["ep"], b)
    return out


def verify_review(state: Path, path: Path) -> int:
    """Check a review document against the corpus it claims to review.

    A review that is one row short looks exactly like a complete one when read.
    That is the defect this check exists to catch: it compares the EP set of the
    review's verdict-bearing table rows against the distinct-EP corpus set and
    fails on any gap. Exit 0 only when the sets match.
    """
    if not path.exists():
        print("no review at %s" % path)
        return 2

    corpus = corpus_eps(state)
    md = read_text(path)
    anywhere = {int(m) for m in re.findall(r"EP(\d+)", md)}

    rows: dict[int, list[str]] = {}
    for line in md.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        ep = int(m.group(1))
        cells = [c.strip().strip("*").strip()
                 for c in line.strip().strip("|").split("|")]
        rows.setdefault(ep, []).extend(c for c in cells if c in VERDICTS)

    covered = sorted(set(corpus) & set(rows))
    missing = sorted(set(corpus) - set(rows))
    extra = sorted(set(rows) - set(corpus))

    print("== review coverage: %s" % path.name)
    print("  corpus        %3d distinct EP across %d batches"
          % (len(corpus), len({b for b in corpus.values()})))
    print("  table rows    %3d EP with a verdict" % len(rows))
    print("  covered       %3d" % len(covered))
    print("  prose only    %3d %s"
          % (len(anywhere - set(rows)),
             sorted(anywhere - set(rows)) or ""))
    print("  MISSING       %3d %s" % (len(missing), missing or ""))
    print("  NOT IN CORPUS %3d %s" % (len(extra), extra or ""))

    hist: dict[str, int] = {}
    for ep in covered:
        v = rows[ep][0] if rows[ep] else "(none)"
        hist[v] = hist.get(v, 0) + 1
    print("\n== verdict histogram (one per paper) ==")
    for k in sorted(hist):
        print("  %-16s %4d" % (k, hist[k]))
    print("  %-16s %4d" % ("TOTAL", sum(hist.values())))
    unlabelled = [ep for ep in covered if not rows[ep]]
    if unlabelled:
        print("  !! no verdict token in row: %s" % unlabelled)

    ok = not missing and not extra and not unlabelled
    print("\n%s" % ("PASS - review covers the corpus exactly" if ok
                     else "FAIL - review does not match the corpus"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="diagnose only (default)")
    g.add_argument("--write", action="store_true", help="add missing indexes")
    g.add_argument("--force", action="store_true",
                   help="with --write, overwrite existing indexes too")
    g.add_argument("--verify-review", metavar="PATH", nargs="?",
                   const=str(REVIEW_DEFAULT),
                   help="check a review doc's EP set against the corpus")
    args = ap.parse_args()
    state = find_state()

    if args.verify_review is not None:
        return verify_review(state, Path(args.verify_review))

    rc = check(state)
    if args.write:
        print("\n--write: emitting missing derived indexes")
        rc = write_indexes(state, force=args.force) or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
