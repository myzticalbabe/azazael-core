"""merge_bake_lane.py — validate + dedupe a lane file into the bake loop input.

Lane files (e.g. state/bake_deepseek_712.jsonl from Engram #714) arrive
from concurrent workers. This tool is the coordinator-side merge gate:
  1. validates every line (JSON + required keys source/item_id/title/body/kind
     + kind in schema + body >= 40 chars). Invalid rows go to the report,
     never to the merged set (a late hallucinated row dies here, per #714).
  2. dedupes row titles against bake_corpus.jsonl + bake_harvest_*.jsonl
     (normalized) and intra-file. Dupes go to the report with reasons.
  3. writes state/bake_merged.jsonl = corpus + harvest + accepted lane rows,
     the single file the demo-expansion step reads.

Usage: python tools/merge_bake_lane.py --lane state/bake_deepseek_712.jsonl [--apply]
  default = dry run (counts only). --apply writes state/bake_merged.jsonl
  + state/bake_merge_report.txt. --lane is repeatable.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_MERGED = ROOT / "state" / "bake_merged.jsonl"
OUT_REPORT = ROOT / "state" / "bake_merge_report.txt"
REQUIRED = ("source", "item_id", "title", "body", "kind")
#: A `landing` row is an accomplishment record: the artifact it landed plus
#: the proof. The three verify fields are mandatory for this kind -- a
#: landing without its sha and verify command is a claim, not evidence
#: (harness improvement #1, 2026-11-06; the forge rows that used to hide
#: under kind "fact" now live here with their proof attached).
LANDING_REQUIRED = ("artifact", "sha", "verify")
#: Optional fields carried through the merge untouched. `evidence_cmd` /
#: `exit_code` / `digest` answer "what command proved this" (#2);
#: `superseded_by` names the newer row that retires this one (#7: history
#: stays in the merged set, but consumers can tell settled rows apart).
OPT_FIELDS = ("artifact", "sha", "verify", "evidence_cmd", "exit_code",
              "digest", "superseded_by", "supersedes")

#: Absolute paths of the lane file(s) under validation. Populated by
#: merge_lanes() before base_rows() runs so input rows are never mistaken for
#: already-baked base rows. Module-global (not a parameter) because
#: base_rows(report) arity is load-bearing (see its docstring).
_EXCLUDE_FILES = set()  # lane file NAMES (see below: Path equality is
                             # unreliable across C:/ vs c:/ spellings)
KINDS = {"error_fix", "lesson", "mandate", "sop_rule", "violation",
         "anti_pattern", "landing"}


def norm_title(t):
    return re.sub(r"\s+", " ", str(t).strip().lower())


def load_jsonl(fp):
    rows = []
    for line in Path(fp).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def base_rows(report=None):
    """Load the base set, refusing rows a title-keyed consumer would read as "".

    The base set (corpus + harvest) is NOT lane-validated -- it is written by
    harvesters rather than under the lane contract -- so a harvest file that
    drifts shape used to land in bake_merged.jsonl unexamined. That happened on
    2026-10-16: state/bake_harvest_web9/web10 arrived as {angle, arxiv, note,
    technique, verdict}, so every `title` lookup on those 19 rows silently
    returned "". A row is now admitted only if it is a dict carrying a
    non-empty title, and exclusions are appended to `report` (never dropped
    quietly) so the next drift is loud instead of invisible. Returns
    (rows, report) is deliberately NOT the contract -- `report` is mutated in
    place so the 4-tuple `merge_lanes()` already returns keeps its arity.
    """
    rows = []
    files = [ROOT / "state" / "bake_corpus.jsonl"]
    files += sorted((ROOT / "state").glob("bake_harvest_*.jsonl"))
    for fp in files:
        if not fp.exists():
            continue
        if fp.name in _EXCLUDE_FILES:
            continue  # the lane under validation is input, not base:
            # without this every row self-matches as "dup" and no lane
            # can ever be accepted (found 2026-10-17: 8/8 novel rows
            # dropped as dup).
        try:
            loaded = load_jsonl(fp)
        except (OSError, ValueError) as e:
            if report is not None:
                report.append((fp.name, "base-unreadable", str(e)[:80]))
            continue
        for i, r in enumerate(loaded):
            if not isinstance(r, dict) or not str(r.get("title", "")).strip():
                if report is not None:
                    report.append(("%s:%d" % (fp.name, i + 1), "base-no-title",
                                   str(sorted(r))[:60]
                                   if isinstance(r, dict) else type(r).__name__))
                continue
            rows.append(r)
    return rows


def merge_lanes(lanes):
    dropped = []
    _EXCLUDE_FILES.clear()
    for _lane in lanes:
        _EXCLUDE_FILES.add(Path(_lane).name)
    base = base_rows(dropped)
    seen = set(norm_title(r.get("title", "")) for r in base)
    merged = list(base)
    accepted = 0
    for lane in lanes:
        try:
            rows = load_jsonl(lane)
        except OSError as e:
            dropped.append((str(lane), "UNREADABLE", str(e)[:80]))
            continue
        except ValueError as e:
            dropped.append((str(lane), "BAD_JSON", str(e)[:80]))
            continue
        lane_seen = set()
        for i, r in enumerate(rows):
            where = "%s:%d" % (Path(lane).name, i + 1)
            if not isinstance(r, dict) or any(k not in r for k in REQUIRED):
                dropped.append((where, "BAD_SCHEMA", "missing keys"))
                continue
            if r.get("kind") not in KINDS:
                dropped.append((where, "BAD_KIND", str(r.get("kind"))[:40]))
                continue
            if r.get("kind") == "landing" and \
                    any(not str(r.get(k, "")).strip() for k in LANDING_REQUIRED):
                dropped.append((where, "landing-incomplete",
                                "needs artifact+sha+verify"))
                continue
            if len(str(r.get("body", ""))) < 40:
                dropped.append((where, "too-short", str(r.get("title", ""))[:60]))
                continue
            key = norm_title(r.get("title", ""))
            if not key:
                dropped.append((where, "empty-title", ""))
                continue
            if key in seen or key in lane_seen:
                dropped.append((where, "dup", str(r.get("title", ""))[:60]))
                continue
            lane_seen.add(key)
            seen.add(key)
            # Fields persist FULL-SIZE. Cutting here produced silently
            # truncated merged rows -- no marker, no drop row (D1 site,
            # truncation sweep 2026-10-22). Point-count validation above
            # (body >= 40) is the size floor; a size is not a reason to cut.
            out = {"source": str(r["source"]),
                   "item_id": str(r["item_id"]),
                   "title": str(r["title"]), "body": str(r["body"]),
                   "kind": r["kind"]}
            for k in OPT_FIELDS:
                if r.get(k) not in (None, ""):
                    out[k] = r[k]
            merged.append(out)
            accepted += 1
    return merged, dropped, accepted, len(base)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", action="append", default=[])
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    # The drop report echoes harvested titles verbatim, and a cp1252 console
    # raised UnicodeEncodeError part-way through it on
    # state/bake_harvest_wslkilo (2026-10-16). That did two harms at once: it
    # truncated the report -- 14 of 112 drops printed, so anything past the
    # crash point was invisible -- and it exited 1 on a healthy dry run, which
    # is a fabricated failure where exit status is the oracle. Re-encode
    # lossily instead of raising. Convention follows lane_acceptance.py, which
    # captures with encoding="utf-8", errors="replace".
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    lanes = args.lane or [str(ROOT / "state" / "bake_deepseek_712.jsonl")]

    merged, dropped, accepted, base_n = merge_lanes(lanes)
    retired = [(r["item_id"], r.get("superseded_by", "")) for r in merged
               if r.get("superseded_by")]
    print("BASE=%d ACCEPTED=%d MERGED=%d DROPPED=%d SUPERSEDED=%d"
          % (base_n, accepted, len(merged), len(dropped), len(retired)))
    for old, new in retired[:10]:
        print("  retired %s -> %s" % (old, new))
    why = {}
    for _, _, w in dropped:
        why[w] = why.get(w, 0) + 1
    print("DROP_REASONS=%s" % json.dumps(why))
    for f, i, w in dropped[:25]:
        print("  drop %s %s (%s)" % (f, i, w))
    if args.apply:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE.sentinel_enforcer import permit_here
        with permit_here("bake merge output"):
            with open(OUT_MERGED, "w", encoding="utf-8") as fh:
                for r in merged:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            with open(OUT_REPORT, "w", encoding="utf-8") as fh:
                for f, i, w in dropped:
                    fh.write("%s\t%s\t%s\n" % (f, i, w))
        print("WROTE %s + %s" % (OUT_MERGED.name, OUT_REPORT.name))


if __name__ == "__main__":
    main()
