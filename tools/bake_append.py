#!/usr/bin/env python3
"""bake_append.py: the only sanctioned writer for bake harvest lanes.

Doctrine (user mandate 2026-11-06): every error must evolve the harness.
Concretely: rows failing schema are REJECTED HERE, at write time, instead
of dying as BAD_KIND drops at merge time (which is how 3 forge rows were
nearly lost on 2026-11-06: kind "fact" is not in the merge tool's KINDS).

Usage:
  python tools/bake_append.py --lane state/xxx.jsonl --kind lesson \\
      --id my-row --title "T" --body "text >= 40 chars..."
  python tools/bake_append.py --lane state/xxx.jsonl --kind lesson \\
      --id my-row --title "T" --body-file /tmp/body.txt
Exit 0 appended (prints new line count); exit 2 rejected (reason on stdout).
"""
import argparse
import json
import os
import sys
KINDS = {"error_fix", "lesson", "mandate", "sop_rule", "violation",
         "anti_pattern", "landing"}
#: Evidence-carrying kinds: a fix or a lesson with no provable artifact is a
#: recollection. Warn (not reject: field crews must never lose a row to a
#: missing digest) -- improvement #2, 2026-11-06.
EVIDENCE_KINDS = {"error_fix", "lesson"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", required=True)
    ap.add_argument("--kind", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--source", default="freebuff-harness")
    ap.add_argument("--artifact", help="landing only: what was landed")
    ap.add_argument("--sha", help="landing only: artifact digest")
    ap.add_argument("--verify", help="landing only: command that proved it")
    ap.add_argument("--evidence-cmd", help="command behind a fix/lesson")
    ap.add_argument("--exit-code", help="that command's exit status")
    ap.add_argument("--digest", help="output digest (sha/short quote)")
    ap.add_argument("--superseded-by",
                    help="item_id of the newer row retiring a row being "
                         "re-marked (improvement #7)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--body")
    g.add_argument("--body-file")
    a = ap.parse_args()
    if a.kind not in KINDS:
        print("REJECTED BAD_KIND %r (valid: %s)"
              % (a.kind, sorted(KINDS)))
        return 2
    body = a.body
    if a.body_file:
        with open(a.body_file, encoding="utf-8") as f:
            body = f.read()
    if len(body or "") < 40:
        print("REJECTED BODY-TOO-SHORT (%d chars, need >= 40)"
              % len(body or ""))
        return 2
    if a.kind == "landing" and not (a.artifact and a.sha and a.verify):
        print("REJECTED LANDING-INCOMPLETE (needs --artifact --sha --verify)")
        return 2
    row = {"source": a.source, "item_id": a.id,
           "title": a.title, "body": body, "kind": a.kind}
    for k in ("artifact", "sha", "verify", "evidence_cmd", "exit_code",
              "digest", "superseded_by"):
        v = getattr(a, k)
        if v:
            row[k] = v
    if a.kind in EVIDENCE_KINDS and not a.evidence_cmd:
        print("NOTE no --evidence-cmd: row is a recollection until proven")
    line = json.dumps(row)
    json.loads(line)  # must be one clean JSON line
    lane = a.lane
    if not os.path.exists(lane):
        print("REJECTED NO-SUCH-LANE %s" % lane)
        return 2
    with open(lane, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    n = sum(1 for _ in open(lane, encoding="utf-8"))
    print("appended %s as line %d" % (a.id, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
