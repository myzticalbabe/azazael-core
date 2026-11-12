#!/usr/bin/env python3
# coding: utf-8
"""lesson_liveness.py — does a surfaced lesson ever get acted on, and can we tell?

WHY THIS EXISTS
---------------
The meta-evolution loop was firing and doing nothing. On 2026-09-30 the
workspace held 184 memory events, 40 surfaced lessons, and exactly ONE
`mark_lesson_followed` call. The store recorded diagnoses and no outcomes,
which means it could never answer the only question that matters for
meta-evolution:

    which lessons actually prevented a repeat, and which are noise?

This is the same disease `audit_loop_liveness.py` exists to catch in the
scheduled-task layer. Its docstring records the original: `\\Azazael-Chain`
was disabled, its log stopped mid-flight, and "four days of self-improvement
simply did not happen" — no error, no alert, no non-zero exit anywhere. A loop
that fires while producing nothing is a dead loop wearing a heartbeat. The
agent loop had exactly that shape: sessions ran constantly and the
follow-through counter never moved.

WHY THIS IS NOT A LIST OF LESSONS
----------------------------------
The first version of a liveness audit anywhere in this tree hard-coded its
table, and the file says why that was wrong: "a hand-maintained constant
drifts, and it drifts *silently* — a renamed log path reads as a dead loop, a
loosened bound switches the check off, and a loop added tomorrow is watched by
nobody, because it was never typed in." Same defect here. So:

  DISCOVERY   Every surface in `lesson_surfaces` is enumerated from the PMB
              database. A lesson surfaced tomorrow is judged without editing
              this file. No table of lessons is maintained anywhere.
  EVIDENCE    What the agent actually did is READ BACK from `mcp_calls` and
              `events` — the same record PMB already keeps — instead of being
              asked for. The agent can be behaving badly and this still
              produces a verdict. That is the entire point: a mechanism that
              depends on the agent volunteering fails exactly when it is most
              needed.
  PROMOTION   A lesson that is surfaced repeatedly and never acted on is not
              left to accumulate. It is PROMOTED into a work item, because an
              unrankingable store cannot meta-evolve.

VERDICTS ARE INFERENCES, NOT CERTAINTIES
----------------------------------------
`mark_lesson_followed` remains the ground truth and should always be
preferred. This tool exists to cover the case where it was not called. Every
verdict therefore records the evidence it used, and `INFERRED_*` verdicts are
never written back over an explicit human/agent judgement.

EMPTY IS NOT HEALTHY
--------------------
The Intermesh audit found that 22 of 26 natures declared no data contract, so
their `emits`/`requires` fields came back as empty dicts that read as "clean"
— a gap that reported as health. This tool refuses that move: zero surfaces is
reported as NO_DATA, not PASS. The exit code distinguishes them.

Usage:
    python tools/lesson_liveness.py              # audit, human report
    python tools/lesson_liveness.py --json       # machine-readable
    python tools/lesson_liveness.py --apply      # write derived verdicts
    python tools/lesson_liveness.py --threshold 0.5
Exit codes:
    0  every surfaced lesson was acted on
    1  threshold breached (failures, or actions too slow)
    2  NO_DATA - nothing has ever been surfaced; cannot judge
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict

PMB_WORKSPACE = os.environ.get(
    "PMB_WORKSPACE_DIR",
    r"C:\Users\clint\.pmb\workspaces\04a151a73354")
DB = os.path.join(PMB_WORKSPACE, "events.sqlite")

# Tools whose use is evidence the agent engaged with memory at all, rather
# than with a specific lesson. Kept minimal on purpose: a broad list would
# make every surface look adopted.
MEMORY_TOOLS = {"record_batch", "record_keyed_fact", "record_fact", "record_goal",
                "record_activity", "mark_lesson_followed", "prepare"}
# A word in a lesson that plausibly names a tool the agent would then call.
TOOL_TOKEN = re.compile(r"\b([a-z][a-z0-9_]{4,})\b")

DEFAULT_THRESHOLD = 0.5
SLOW_SECONDS = 1800.0          # a surface unmarked after 30 min counts as dormant


def _connect() -> sqlite3.Connection:
    if not os.path.exists(DB):
        raise SystemExit(f"FATAL: PMB workspace not found at {DB}. "
                         "Set PMB_WORKSPACE_DIR. Refusing to report PASS on "
                         "a store that is not there.")
    con = sqlite3.connect(DB, timeout=15.0)
    con.row_factory = sqlite3.Row
    return con


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone() is not None


def collect(con: sqlite3.Connection) -> dict:
    """Read every surface, every memory-tool call, and every written event."""
    surfaces = [dict(r) for r in con.execute(
        "SELECT id, lesson_ulid, query, source, surfaced_at, session_id, "
        "followed, follow_note, followed_at FROM lesson_surfaces "
        "ORDER BY surfaced_at")]

    calls = []
    if _has_table(con, "mcp_calls"):
        calls = [dict(r) for r in con.execute(
            "SELECT tool_name, timestamp, success, outcome FROM mcp_calls "
            "ORDER BY timestamp")]

    lessons = {}
    if _has_table(con, "events"):
        # BUG FOUND BY THE FIRST UNATTENDED RUN (2026-09-30). This used to read
        # `WHERE event_type='lesson'`, which matches NOTHING: PMB files the
        # lesson-ness in metadata_json ("kind":"lesson","source":"lesson") and
        # the row's event_type is 'fact'. Measured: the events table holds
        # 214 fact + 13 activity and ZERO lesson rows, while all 78 surfaced
        # lesson_ulids resolve to fact rows with lesson metadata.
        #
        # Consequence: `data["lessons"]` was always empty, so the 'did the agent
        # use the tools this lesson names?' check could never fire, and every
        # surface fell through to the UNMARKED_ENGAGED bucket. The tool reported
        # inferred_adopted=0 and that read as 'the agent did nothing' when part
        # of it was 'the tool could not see the lessons to check with'.
        #
        # A detector that cannot see its own input must say so rather than
        # report a clean negative -- the same rule this file exists to enforce.
        for r in con.execute(
                "SELECT ulid, content, event_type, metadata_json FROM events"):
            if r["event_type"] == "lesson":
                lessons[r["ulid"]] = r["content"]
                continue
            meta = r["metadata_json"] or ""
            if '"kind":"lesson"' in meta.replace(" ", "") or \
               '"source":"lesson"' in meta.replace(" ", ""):
                lessons[r["ulid"]] = r["content"]

    written = []
    if _has_table(con, "events"):
        # CONTENT is required, not optional. Without it `adjudicate` had no
        # text to match a lesson against, so the only thing it could compare
        # was the MCP tool NAME -- see adjudicate() step 4.
        written = [dict(r) for r in con.execute(
            "SELECT ulid, event_type, timestamp, content FROM events "
            "ORDER BY timestamp")]

    return {"surfaces": surfaces, "calls": calls,
            "lessons": lessons, "written": written}


DERIVED_PREFIX = "[auto:"


def is_derived(row: dict) -> bool:
    """True when the verdict was written by --apply, not by an agent/human.

    Without this the audit self-inflates: --apply writes `followed` values, and
    the metric then counts its own inference as ground truth. That is how a
    0.025 real mark rate read as 0.851 after a single repair run. The applied
    rows are tagged in `follow_note`, so the distinction is recoverable rather
    than inferred.
    """
    return (row.get("follow_note") or "").startswith(DERIVED_PREFIX)


def adjudicate(data: dict) -> dict:
    """Derive a verdict per surface from evidence recorded after it."""
    calls, written = data["calls"], data["written"]
    now = time.time()
    out = {"explicit": [], "derived_written": [], "inferred_adopted": [],
           "inferred_active": [], "dormant": [], "unjudgeable": 0}

    for s in data["surfaces"]:
        t0 = s["surfaced_at"]

        # 0. Already adjudicated by a previous --apply run. Count separately.
        if s["followed"] is not None and is_derived(s):
            out["derived_written"].append({
                **s, "verdict": "DERIVED_PREVIOUSLY",
                "evidence": {"prior_note": (s["follow_note"] or "")[:160]}})
            continue

        # 1. Ground truth wins outright.
        if s["followed"] is not None:
            out["explicit"].append({**s, "verdict": "EXPLICIT_FOLLOWED"
                                    if s["followed"] else "EXPLICIT_NOT_FOLLOWED"})
            continue

        after = [c for c in calls if c["timestamp"] >= t0]
        mem_after = [c for c in after if c["tool_name"] in MEMORY_TOOLS]
        wrote = [e for e in written if e["timestamp"] >= t0]

        # The lesson's own vocabulary, matched against the WORK done after the
        # surface. Computed here, before the branches, because it is informative
        # on EVERY path -- including the unmarked ones. The old check compared
        # these tokens against mcp_calls.tool_name, i.e. MCP tool NAMES (recall,
        # record_batch) against lesson text that names azazael ARTIFACTS
        # (evolution_cycle.py, pool selection) -- vocabularies that cannot
        # intersect, so it could only ever answer 0. This reads the CONTENT of
        # what was written after the surface, which is where the work shows up.
        lesson = data["lessons"].get(s["lesson_ulid"])
        text = (lesson if lesson else (s["query"] or "")).lower()
        tokens = {t for t in TOOL_TOKEN.findall(text)} - STOPWORDS
        hay = [(e.get("content") or "").lower() for e in wrote]
        hay += [str(c.get("outcome") or "").lower() for c in after]
        matched = sorted({t for t in tokens if any(t in h for h in hay)})

        # 2. Did anything at all happen after the surface?
        if not after and not wrote:
            if now - t0 > SLOW_SECONDS:
                out["dormant"].append({**s, "verdict": "DORMANT", "age_s":
                                       round(now - t0, 1)})
            else:
                out["unjudgeable"] += 1
            continue

        # 3. Did the agent use memory, but never mark? That is the exact
        #    shape of the failure this file was written for: engaged, but
        #    the reporting step never happened. It is NOT evidence of
        #    success. Counting it as "acted" makes the audit certify its own
        #    inference -- reporting PASS while every lesson reads 0.00 --
        #    which is precisely the "empty reads as clean" defect this file
        #    exists to prevent. Unmarked means unmarked.
        if mem_after:
            # Still NOT counted as acted -- unmarked means unmarked. But the
            # subject signal is attached, so 0.09 stops reading as "the agent
            # did nothing" and starts distinguishing "engaged, subject
            # addressed, never marked" from "engaged, subject untouched".
            out["inferred_active"].append({
                **s, "verdict": "UNMARKED_ENGAGED",
                "evidence": {"memory_tools_after": len(mem_after),
                             "tools": sorted({c["tool_name"] for c in mem_after}),
                             "events_written_after": len(wrote),
                             "subject_in_work_after": bool(matched),
                             "subject_tokens": matched[:8]}})
            continue

        # 4. Nothing engaged memory; did the lesson's subject still appear in
        #    the work? Same vocabulary match as above, now licensing adoption.
        if matched:
            out["inferred_adopted"].append({
                **s, "verdict": "INFERRED_ADOPTED",
                "evidence": {"lesson_tokens_in_work_after": matched[:8]}})
        else:
            out["inferred_active"].append({
                **s, "verdict": "UNMARKED_ENGAGED",
                "evidence": {"tools_after": sorted({c["tool_name"] for c in after})[:6],
                             "events_written_after": len(wrote)}})
    return out


STOPWORDS = {
    "which", "there", "their", "would", "about", "never", "always", "these",
    "those", "where", "while", "should", "could", "before", "after", "being",
    "because", "however", "instead", "already", "cannot", "through", "during",
    "without", "within", "between", "against", "though", "under", "other",
    "same", "then", "than", "when", "what", "with", "from", "into", "your",
}


def effectiveness(surfaces: list[dict]) -> list[dict]:
    """Rank lessons by measured adoption. Fix 4: the store must learn which
    of its own entries earn their place, ranked by outcome rather than recency."""
    per = defaultdict(lambda: {"surfaces": 0, "acted": 0, "explicit": 0,
                               "derived": 0, "first": None, "last": None})
    for s in surfaces:
        k = s["lesson_ulid"]
        p = per[k]
        p["surfaces"] += 1
        if s["followed"] is True:
            p["acted"] += 1
        if s["followed"] is not None:
            if is_derived(s):
                p["derived"] += 1
            else:
                p["explicit"] += 1
        p["first"] = s["surfaced_at"] if p["first"] is None else min(p["first"], s["surfaced_at"])
        p["last"] = s["surfaced_at"] if p["last"] is None else max(p["last"], s["surfaced_at"])
    rows = []
    for ulid, p in per.items():
        rate = p["acted"] / p["surfaces"] if p["surfaces"] else 0.0
        rows.append({"lesson_ulid": ulid, **p, "adoption_rate": round(rate, 3),
                     "confidence": "measured" if p["explicit"] else "inferred"})
    rows.sort(key=lambda r: (r["adoption_rate"], r["surfaces"]))
    return rows


def audit(data: dict, threshold: float) -> dict:
    v = adjudicate(data)
    s = data["surfaces"]
    total = len(s)
    # Only an EXPLICIT mark or evidence the lesson's own subject was acted on
    # counts as action. "Engaged with memory but never marked" is the failure
    # this audit exists to surface, so it is deliberately NOT counted.
    acted = len(v["explicit"]) + len(v["inferred_adopted"])
    rate = (acted / total) if total else 0.0
    # Ground truth only. A row this tool wrote itself is not evidence that
    # anything was actually followed, so it is reported apart from the count.
    marked = sum(1 for x in s if x["followed"] is not None and not is_derived(x))
    derived = len(v["derived_written"])
    by_source = defaultdict(lambda: {"n": 0, "marked": 0, "derived": 0})
    for x in s:
        b = by_source[x["source"] or "(none)"]
        b["n"] += 1
        if x["followed"] is not None:
            (b.__setitem__("derived", b["derived"] + 1) if is_derived(x)
             else b.__setitem__("marked", b["marked"] + 1))
    status = "NO_DATA" if total == 0 else ("PASS" if rate >= threshold else "FAIL")
    return {
        "status": status,
        "threshold": threshold,
        "surfaces_total": total,
        "explicitly_marked": marked,
        "derived_verdicts_written": derived,
        "explicit_mark_rate": round(marked / total, 3) if total else None,
        "derived_verdicts": {k: len(x) for k, x in v.items()
                             if isinstance(x, list)},
        "effective_action_rate": round(rate, 3),
        "population_by_source": {k: dict(v2) for k, v2 in by_source.items()},
        "unjudgeable_too_recent": v["unjudgeable"],
        "ranking": effectiveness(s),
        "dormant": v["dormant"][:40],
        "inferred_active": v["inferred_active"][:40],
        "inferred_adopted": v["inferred_adopted"][:40],
        "diagnostics": {
            "error_log_rows": data.get("error_log_rows", 0),
            "mcp_calls": len(data["calls"]),
            "events_written": len(data["written"]),
            "note": "error_log_rows==0 means the error-recording SOP has never "
                    "been used. That is reported, not treated as clean.",
        },
    }


def apply(conn: sqlite3.Connection, rep: dict) -> int:
    """Write DERIVED verdicts only. Never overwrite an explicit judgement."""
    n = 0
    for bucket, verdict in (("inferred_adopted", True),
                            ("inferred_active", False)):
        for s in rep.get(bucket, []):
            note = (f"[auto:{verdict}] {json.dumps(s.get('evidence', {}))}"[:480])
            cur = conn.execute(
                "UPDATE lesson_surfaces SET followed=?, follow_note=?, followed_at=? "
                "WHERE id=? AND followed IS NULL",
                (verdict, note, time.time(), s["id"]))
            n += cur.rowcount
    conn.commit()
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="write derived verdicts into lesson_surfaces")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    a = ap.parse_args()

    con = _connect()
    data = collect(con)
    if _has_table(con, "error_log"):
        data["error_log_rows"] = con.execute(
            "SELECT COUNT(*) FROM error_log").fetchone()[0]
    rep = audit(data, a.threshold)

    if a.apply:
        rep["rows_updated"] = apply(con, rep)
    if a.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        d = rep["diagnostics"]
        print(f"lesson_liveness: {rep['status']}   "
              f"action rate {rep['effective_action_rate']} "
              f"(threshold {rep['threshold']})")
        print(f"  surfaces            : {rep['surfaces_total']}")
        print(f"  explicitly marked   : {rep['explicitly_marked']}"
              f"  ({rep['explicit_mark_rate']})")
        print(f"  derived verdicts    : {rep['derived_verdicts']}")
        print(f"  mcp_calls / events  : {d['mcp_calls']} / {d['events_written']}")
        print(f"  error_log rows      : {d['error_log_rows']}")
        if a.apply:
            print(f"  rows updated        : {rep.get('rows_updated', 0)}")
        if rep["ranking"]:
            print("\n  adoption by lesson (worst first):")
            for r in rep["ranking"][:12]:
                print(f"    {r['adoption_rate']:.2f}  n={r['surfaces']:<3} "
                      f"({r['confidence']}) {r['lesson_ulid']}")
        if rep["dormant"]:
            print(f"\n  DORMANT ({len(rep['dormant'])} shown):")
            for s in rep["dormant"][:8]:
                print(f"    #{s['id']} {s['age_s']:.0f}s  {s['query'][:60]}")
    con.close()

    # 2 = cannot judge, which must never be reported as health.
    return 2 if rep["status"] == "NO_DATA" else (0 if rep["status"] == "PASS" else 1)


if __name__ == "__main__":
    sys.exit(main())

