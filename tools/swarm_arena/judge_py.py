#!/usr/bin/env python3
"""judge_py — swarm-arena shard (Python): cross-reference-density judge.

Reads pairs JSON from stdin: [{"pair":..,"x":..,"y":..}]
Writes verdicts JSON to stdout: [{"pair":..,"winner":"X|Y","sx":..,"sy":..}]

Signals (deliberately disjoint from judge_rs evidence-refs and judge_go
structure): RULE citations, task refs, PMB event ids, observation ids,
file paths, line anchors. Stdlib only, deterministic.
"""
import json
import re
import sys

RULE = re.compile(r"RULE\s+[0-9A-Z][0-9A-Z\-\/]*")
TASK = re.compile(r"#\d{3,4}\b")
PMB = re.compile(r"PMB event id\s+\d+", re.IGNORECASE)
OBS = re.compile(r"obs-[0-9a-f]{6,}")
PATH = re.compile(r"[A-Za-z0-9_\-./]+\.(py|md|json|jsonl|txt|toml|yaml)\b")
LINE = re.compile(r"[Ll]ines?\s+\d+")
MEASURED = re.compile(r"[Mm]easured|[Vv]erified|exit\s+0|green\s+\d+/\d+|\d+/\d+\s+(PASS|green)")


def score(t: str) -> float:
    s = 0.0
    s += min(len(RULE.findall(t)), 5) * 2.0
    s += min(len(TASK.findall(t)), 5) * 1.0
    s += min(len(PMB.findall(t)), 3) * 2.0
    s += min(len(OBS.findall(t)), 3) * 1.0
    s += min(len(PATH.findall(t)), 4) * 1.0
    s += 1.0 if LINE.search(t) else 0.0
    s += 1.0 if MEASURED.search(t) else 0.0
    return s


def main() -> None:
    pairs = json.load(sys.stdin)
    out = []
    for p in pairs:
        sx, sy = score(p["x"]), score(p["y"])
        if sy > sx or (sy == sx and len(p["y"]) > len(p["x"])):
            w = "Y"
        else:
            w = "X"
        out.append({"pair": p["pair"], "winner": w,
                    "sx": round(sx, 2), "sy": round(sy, 2)})
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
