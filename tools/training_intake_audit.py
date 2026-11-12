#!/usr/bin/env python3
"""training_intake_audit.py — is each harvested algorithm's input wired into a trainer?

WHY THIS EXISTS
Engram #770 closes the gap recorded in #368 ("Harvested benefit unapplied: 1/33
in prod, zero trainer intake") and item H-3 of `state/ACTION_REGISTRY_2026-10-10.md`
("P10 trajectory->training edge: write run/exercise trajectories as rows
DataPipelineWorm consumes (source/event_type trainers already read). Why: zero
trainer<-technique edge on any predicate today").

The CLAIM under test is "1/33 applied, zero trainer intake". #635 already falsified
a sibling claim (the "32/33 exercise-only" premise) by re-measuring it, so this tool
re-measures rather than inheriting the number: it asks, per technique, whether ANY
trainer edge exists now, and names the predicate that proves it.

THE FOUR TRAINERS (read, not assumed — #368 and #770 both name these four):
  RLTrainLoop   src/CORE/WORMS/expansion/learning_rl.py
  SB3Agent      src/CORE/sb3_agent.py
  GRPOTrainer   src/CORE/WORMS/expansion/learning_grpo.py
  NeuralPipeline src/CORE/NeuralPipeline.py
All four reach their data through `DataPipelineWorm.extract_from_memory` /
`LocalMemoryStore.query_memories` and the worm registry, so the predicates below
are evaluated on the trainer modules AND on that shared extraction path.

THE PREDICATES (an edge must be exhibited, never inferred from prose):
  P1 IMPORT    the trainer imports the technique's host module
  P2 CALL      the technique's host symbol is referenced in the trainer source
  P3 ROWS      the technique's host emits a row `source=`/`event_type=` that a
               trainer filters on -- this is the H-3 edge shape
  P4 PARAM     a trainer constructor/train signature carries a parameter whose
               name is the technique's own vocabulary (EP350's `step_decay`,
               EP351's `dense_reward_weight` are the known-true cases)

P1-P4 are PER-TECHNIQUE, and they stay the per-EP verdict. #770's H-3 fix is
GENERAL, so it needs its own reading or this instrument would keep reporting
"zero trainer intake" for a tree that no longer has that property: `intake_edge()`
below measures the shared channel -- the `technique-link` stream translated by
`CORE.training_intake.build_training_records` and driven by the `INTAKE` track
-- and reports it as one headline fact rather than smearing it across 33 rows
that have not each been individually wired.

A row with no predicate is an ACCEPT-GAP, and the gap text names the missing
precondition. Absence of a predicate is reported as absence -- never as zero
evidence of absence, and never as a pass.

Usage:
    python tools/training_intake_audit.py                 # table + JSON to state/
    python tools/training_intake_audit.py --md PATH       # write the report here
    python tools/training_intake_audit.py --json PATH
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"

#: The audited set, transcribed from `state/SOTA_STEAL_AUDIT.md` section 3
#: ("VERDICT: 33 distinct algorithms -- 27 ADD-new, 6 EXTEND-existing"). This is
#: the audit's own artifact, and it is transcribed rather than re-derived so a
#: disagreement shows up as a missing row instead of a silently smaller set.
ADD_NEW = ("EP408", "EP412", "EP413", "EP414", "EP417", "EP420", "EP425", "EP427",
           "EP429", "EP435", "EP437", "EP438", "EP440", "EP441", "EP447", "EP448",
           "EP452", "EP455", "EP458", "EP461", "EP463", "EP464", "EP465", "EP471",
           "EP472", "EP474", "EP478")
EXTEND = ("EP171", "EP431", "EP442", "EP446", "EP453", "EP423")
AUDITED = ADD_NEW + EXTEND

#: The four trainers #770 names, plus the extraction path all four share.
TRAINERS = {
    "RLTrainLoop": "src/CORE/WORMS/expansion/learning_rl.py",
    "SB3Agent": "src/CORE/sb3_agent.py",
    "GRPOTrainer": "src/CORE/WORMS/expansion/learning_grpo.py",
    "NeuralPipeline": "src/CORE/NeuralPipeline.py",
}
EXTRACTION_PATH = {
    "DataPipelineWorm": "src/CORE/WORMS/expansion/learning.py",
    "SchemaAwareFeatureExtractor": "src/CORE/WORMS/expansion/learning_features.py",
}

#: Host sources, in priority order. The batch06 map covers batch06 only, so it
#: answers nothing about the audited 33 (measured: 0 of 33 present) -- the right
#: source is `SOTA_FULL_REVIEW_2026-10-09.md` section 1, whose table covers all 33
#: and whose `wiring action` column names the concrete host per EP (e.g. EP412 ->
#: `learning_grpo.py`). That file is the one `ACTION_REGISTRY_2026-10-10.md` cites
#: for the "32/33 exercise-only" claim this audit re-measures.
MAP_CANDIDATES = (REPO.parent / "state" / "sota_batches" / "batch06"
                  / "technique_codebase_map.json",
                  REPO / "state" / "sota_batches" / "batch06"
                  / "technique_codebase_map.json")
REVIEW_CANDIDATES = (REPO / "state" / "SOTA_FULL_REVIEW_2026-10-09.md",
                     REPO.parent / "state" / "SOTA_FULL_REVIEW_2026-10-09.md")

#: `| EP412 | CN101 ... | L6+L5 | mechanism | target | ABSENT | `learning_grpo.py` -- ... |`
REVIEW_ROW_RE = re.compile(r"^\|\s*(EP\d{3})\s*\|", re.M)
HOST_FILE_RE = re.compile(r"`([A-Za-z0-9_/.-]+\.py)`")

DEFAULT_MD = REPO / "state" / "TRAINING_INTAKE_AUDIT.md"
DEFAULT_JSON = REPO / "state" / "training_intake_audit.json"

#: `source=`/`event_type=` literals as written in code.
ROW_KIND_RE = re.compile(r"""(?:source|event_type)\s*=\s*["']([a-zA-Z0-9_.\-]+)["']""")

#: `CORE/module.py :: symbol` / `CORE.module: symbol` -- both spellings appear.
TARGET_RE = re.compile(r"^(?P<path>[A-Za-z0-9_./\\-]+\.py)\s*(?:::|\s)\s*(?P<sym>[A-Za-z_][A-Za-z0-9_.]*)?")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print("training_intake_audit: cannot read %s: %s" % (path, exc),
              file=sys.stderr)
        return ""


def _load_map() -> Dict[str, Dict[str, Any]]:
    """EP -> map row, from the first map that exists. Read-only, fail-open."""
    for cand in MAP_CANDIDATES:
        if not cand.is_file():
            continue
        try:
            payload = json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("training_intake_audit: %s unreadable (%s)" % (cand, exc),
                  file=sys.stderr)
            continue
        rows = payload.get("rows") or []
        out = {}
        for row in rows:
            ep = str(row.get("ep") or "")
            if ep:
                out[ep] = row
        return out
    return {}


def _load_review() -> Dict[str, Dict[str, Any]]:
    """EP -> {name, target, verdict, wiring} from SOTA_FULL_REVIEW s1's table.

    Cells are `| EP | name | layer | mechanism | target | verdict | wiring action |`.
    The `wiring action` cell is where the audit's intended HOST is written, which is
    the column this tool measures against -- the `target` cell names a subsystem
    ("worm retry/fitness budgets"), not a file.
    """
    for cand in REVIEW_CANDIDATES:
        if not cand.is_file():
            continue
        out: Dict[str, Dict[str, Any]] = {}
        for line in cand.read_text(encoding="utf-8", errors="replace").splitlines():
            if not REVIEW_ROW_RE.match(line):
                continue
            # Backticks are KEPT: the wiring-action cell marks its host file with
            # them (`learning_grpo.py`), and stripping the cell destroys the only
            # delimiter that says which token in the sentence is a path. Cells are
            # read from the END because a mechanism cell may itself contain a pipe.
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 7:
                continue
            out[cells[0].strip("` ")] = {
                "name": cells[1].strip("` "),
                "target": cells[-3].strip("` "),
                "coverage": cells[-2].strip("` "),
                "wiring": cells[-1],
            }
        if out:
            return out
    return {}


def _hosts_of(action: str) -> List[str]:
    """Source files named in a `wiring action` cell, resolved under src/ first."""
    out: List[str] = []
    for name in HOST_FILE_RE.findall(action or ""):
        rel = name.lstrip("./").replace("\\", "/")
        if rel.startswith("src/"):
            out.append(rel)
            continue
        matches = sorted(SRC.rglob(rel))
        if matches:
            out.append(str(matches[0].relative_to(REPO)).replace("\\", "/"))
    return out


def _targets(target: str) -> List[str]:
    """Source files named by a map `target` cell (`no-host:` declares none)."""
    if not target or target.startswith("no-host"):
        return []
    out: List[str] = []
    for chunk in re.split(r"[;,]", target):
        match = TARGET_RE.match(chunk.strip().strip("`"))
        if match:
            out.append(match.group("path").replace("\\", "/").lstrip("./"))
    return out


def _symbols(target: str) -> List[str]:
    """Symbol names named by a map `target` cell."""
    if not target or target.startswith("no-host"):
        return []
    out: List[str] = []
    for chunk in re.split(r"[;,]", target):
        match = TARGET_RE.match(chunk.strip().strip("`"))
        if match and match.group("sym"):
            out.append(match.group("sym").split(".")[-1])
    return out


def _module_of(rel: str) -> str:
    """`src/CORE/x.py` -> `CORE.x` (the importable dotted name)."""
    name = rel
    if name.startswith("src/"):
        name = name[len("src/"):]
    if name.endswith(".py"):
        name = name[:-3]
    return name.replace("/", ".")


def _measures(rel_targets: List[str], symbols: List[str]) -> Dict[str, Any]:
    """Evaluate the four predicates against the trainers + extraction path."""
    trainer_rel = list(TRAINERS.values())
    text = {p: _read(REPO / p) for p in trainer_rel}
    shared_rel = list(EXTRACTION_PATH.values())
    shared_text = {p: _read(REPO / p) for p in shared_rel}

    hits: Dict[str, List[str]] = {"P1": [], "P2": [], "P3": [], "P4": []}
    for rel in trainer_rel:
        body = text[rel]
        if not body:
            continue
        for mod in {_module_of(t) for t in rel_targets}:
            if re.search(r"^\s*(?:from\s+%s\s+import|import\s+%s\b)"
                         % (re.escape(mod), re.escape(mod)), body, re.M):
                hits["P1"].append("%s imports %s" % (rel, mod))
        for sym in symbols:
            for m in re.finditer(r"\b%s\b" % re.escape(sym), body):
                line = body[:m.start()].count("\n") + 1
                hits["P2"].append("%s:%d %s" % (rel, line, sym))
                break

    # P3: rows emitted by the technique's host that a trainer filters on.
    emitted: Dict[str, List[str]] = {}
    for rel in rel_targets:
        body = _read(REPO / rel)
        for kind in set(ROW_KIND_RE.findall(body)):
            emitted.setdefault(kind, []).append("%s emits %s" % (rel, kind))
    for kind, where in emitted.items():
        for rel, body in list(text.items()) + list(shared_text.items()):
            if re.search(r"""["']%s["']""" % re.escape(kind), body):
                hits["P3"].append("%s filters on %s (%s)"
                                  % (rel, kind, where[0].split()[0]))

    # P4: a trainer parameter carrying the technique's own vocabulary. Only the
    # technique's symbol names are used, so this cannot be satisfied by prose.
    for rel in trainer_rel:
        body = text[rel]
        for sym in symbols:
            if re.search(r"def\s+\w+\([^)]*\b%s\b" % re.escape(sym), body, re.S):
                hits["P4"].append("%s takes %s as a parameter" % (rel, sym))
    return hits


#: The general technique-outcome intake edge (Engram #770 / H-3). Each entry is
#: (file, needle, what the needle proves). All three must hold: an adapter that
#: translates the rows, a registry entry that makes it a track, and a runner that
#: hands the result to a real trainer.
INTAKE_EDGE = (
    ("src/CORE/training_intake.py", "def build_training_records",
     "adapter translates technique-link rows into trainer records"),
    ("scripts/track_registry.py", "\"INTAKE\"",
     "INTAKE is a registry track with a reward event"),
    ("scripts/run_training_orchestrator.py", "def run_intake_cycle",
     "the track's runner feeds RLTrainLoop through its records= path"),
)


def intake_edge() -> Dict[str, Any]:
    """Is the shared technique->trainer intake channel present? Read-only."""
    evidence: List[str] = []
    missing: List[str] = []
    for rel, needle, why in INTAKE_EDGE:
        body = _read(REPO / rel)
        if needle in body:
            evidence.append("%s: %s" % (rel, why))
        else:
            missing.append("%s (missing %r)" % (rel, needle))
    return {"present": not missing, "evidence": evidence, "missing": missing}


def _classify(row: Dict[str, Any], hits: Dict[str, List[str]]) -> str:
    if hits["P2"] or hits["P3"] or hits["P4"]:
        return "APPLIED"
    if hits["P1"]:
        return "PARTIAL"
    if str(row.get("target") or "").startswith("no-host"):
        return "ACCEPT-GAP-NO-HOST"
    return "ACCEPT-GAP"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--md", default=str(DEFAULT_MD),
                    help="where to write the markdown table")
    ap.add_argument("--json", default=str(DEFAULT_JSON),
                    help="where to write the machine-readable table")
    args = ap.parse_args()

    emap = _load_map()
    review = _load_review()
    if not emap and not review:
        print("no host source found under any of: %s | %s"
              % (", ".join(str(c) for c in MAP_CANDIDATES),
                 ", ".join(str(c) for c in REVIEW_CANDIDATES)), file=sys.stderr)
        return 2
    if not review:
        print("SOTA_FULL_REVIEW_2026-10-09.md not found: the audited 33 have no "
              "host map (the batch06 map covers batch06 only)", file=sys.stderr)
        return 2

    results: List[Dict[str, Any]] = []
    for ep in AUDITED:
        row = {**emap.get(ep, {}), **review.get(ep, {})}
        action = str(row.get("wiring") or "")
        hosts = _hosts_of(action)
        syms = _symbols(str(row.get("target") or ""))
        hits = _measures(hosts, syms)
        host_missing = [h for h in hosts if not (REPO / h).is_file()]
        results.append({
            "ep": ep,
            "name": row.get("name") or "",
            "klass": "EXTEND" if ep in EXTEND else "ADD",
            "target": str(row.get("target") or "(absent from the map)"),
            "action": action,
            "hosts": hosts,
            "hosts_missing": host_missing,
            "coverage": row.get("coverage") or "",
            "verdict": _classify(row, hits),
            "hits": {k: v[:4] for k, v in hits.items() if v},
            "gap": row.get("gap") or "",
        })

    counts: Dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        # The named missing precondition, per row: what a future slice would
        # have to add for this technique to reach a trainer itself.
        if r["verdict"].startswith("ACCEPT-GAP") and not r["hosts"]:
            r["precondition"] = ("%s has no host file in the wiring action; the "
                                 "mechanism's host must land first (target: %s)"
                                 % (r["ep"], r["target"][:60]))
        elif r["verdict"].startswith("ACCEPT-GAP"):
            r["precondition"] = ("a trainer call site inside %s (its host), or an "
                                 "intake row naming this EP's technique"
                                 % ", ".join(r["hosts"][:2]))
        elif r["verdict"] == "PARTIAL":
            r["precondition"] = ("a call to the host's symbol from a trainer, not "
                                 "an import of the module")
        else:
            r["precondition"] = ""
    missing = [r["ep"] for r in results if r["target"].startswith("(absent")]
    edge = intake_edge()

    print("audited set: %d (%d ADD-new + %d EXTEND, per SOTA_STEAL_AUDIT.md s3)"
          % (len(AUDITED), len(ADD_NEW), len(EXTEND)))
    print("not present in technique_codebase_map.json: %d %s"
          % (len(missing), missing if missing else ""))
    print("general technique->trainer intake edge: %s"
          % ("PRESENT" if edge["present"] else "ABSENT"))
    for line in (edge["evidence"] if edge["present"] else edge["missing"]):
        print("  %s" % line)
    for verdict in ("APPLIED", "PARTIAL", "ACCEPT-GAP", "ACCEPT-GAP-NO-HOST"):
        if counts.get(verdict):
            print("  %-18s %d" % (verdict, counts[verdict]))
    applied = [r["ep"] for r in results if r["verdict"] == "APPLIED"]
    print("trainer edge proven for: %s" % (", ".join(applied) if applied else "none"))

    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps({
        "audited": len(AUDITED), "source": "state/SOTA_STEAL_AUDIT.md s3",
        "trainers": TRAINERS, "extraction_path": EXTRACTION_PATH,
        "counts": counts, "intake_edge": edge, "rows": results,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# TRAINING INTAKE AUDIT — do the harvested algorithms reach a trainer?",
        "",
        "Takes each audited technique's host from the `wiring action` column of",
        "`state/SOTA_FULL_REVIEW_2026-10-09.md` section 1 (the batch06 map is also",
        "read but covers batch06 only: 0 of these 33), and measures four per-technique",
        "edge predicates against the four trainers #770 names (RLTrainLoop, SB3Agent,",
        "GRPOTrainer, NeuralPipeline) plus the extraction path all four share, then the",
        "shared-channel edge below. Predicate definitions are in the module docstring",
        "of `tools/training_intake_audit.py`.",
        "",
        "Audited set: the 33 from `state/SOTA_STEAL_AUDIT.md` s3 (27 ADD-new + 6 EXTEND).",
        "",
        "## The general intake edge (Engram #770 / H-3)",
        "",
        "P1-P4 are per-technique and stay the per-EP verdict. The #770 fix is a",
        "SHARED channel, reported here as one fact so this report cannot keep",
        "claiming \"zero trainer intake\" for a tree that has one:",
        "",
        "**%s**" % ("PRESENT" if edge["present"] else "ABSENT"),
        "",
    ]
    for line in (edge["evidence"] if edge["present"] else edge["missing"]):
        lines.append("- %s" % line)
    lines += [
        "",
        "| verdict | count |",
        "|---|---|",
    ]
    for verdict in ("APPLIED", "PARTIAL", "ACCEPT-GAP", "ACCEPT-GAP-NO-HOST"):
        lines.append("| %s | %d |" % (verdict, counts.get(verdict, 0)))
    lines += ["", "| EP | technique | named host (wiring action) | verdict | predicate evidence |",
              "|---|---|---|---|---|"]
    for r in results:
        ev = "; ".join("%s: %s" % (k, v[0]) for k, v in sorted(r["hits"].items()))
        if not ev:
            if r["hosts_missing"]:
                ev = "host named in the wiring action does not exist: %s" \
                     % ", ".join(r["hosts_missing"])
            elif r["hosts"]:
                ev = ("host exists (%s) but no trainer reaches it; subsystem target: %s"
                      % (", ".join(r["hosts"][:2]), r["target"][:80]))
            else:
                ev = ("no host file named in the wiring action; subsystem target: %s"
                      % r["target"][:80])
        lines.append("| `%s` | %s | `%s` | %s | %s |"
                     % (r["ep"], (r["name"] or "-")[:44],
                        (",".join(r["hosts"]) or r["target"])[:58],
                        r["verdict"], ev.replace("|", "/")[:220]))
    lines += ["",
              "`APPLIED` = a predicate is exhibited. `PARTIAL` = the trainer imports the",
              "host but never reaches a technique symbol, which is the surface-probe shape",
              "EP435 taught us to stop calling wired. `ACCEPT-GAP` = no edge, with the",
              "missing precondition named in `state/training_intake_audit.json`;",
              "`ACCEPT-GAP-NO-HOST` = the map itself declares no host for the mechanism.",
              "",
              "A per-EP `ACCEPT-GAP` is not contradicted by the general edge above: the",
              "shared channel carries whatever MEASURED hook outcomes exist, and what",
              "remains per EP is that technique's own host reaching a trainer.", ""]
    Path(args.md).write_text("\n".join(lines), encoding="utf-8")
    print("wrote %s" % args.md)
    print("wrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
