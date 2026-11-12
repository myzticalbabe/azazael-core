# coding: utf-8
"""graphify_refresh.py — bounded nightly refresh of the knowledge graph.

The graphify pair (graphify-out/graph.json + GRAPH_REPORT.md) cannot be
refreshed inside any gate with a deadline: the corpus graph is 829k+ nodes
and a full `graphify update` exceeds 600s (measured 2026-09-27). But leaving
it unrefreshed made it the permanent FAIL of artifact_freshness. This wrapper
gives the refresh its own scheduled slot with a generous internal budget and
a machine-readable result.

Safety: graphify's own writes are atomic (verified -- a killed run leaves no
partial graph.json), so a timeout here leaves the previous consistent pair in
place and the freshness tier treats the pair as advisory.

Run: chain job `graphify` (nightly:02:00). Output: logs/graphify_refresh.log
+ state/graphify_refresh.json.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "graphify_refresh.log"
OUT = ROOT / "state" / "graphify_refresh.json"
INTERNAL_TIMEOUT_S = int(os.environ.get("AZAZAEL_GRAPHIFY_TIMEOUT", "5000"))


def main() -> int:
    binary = os.environ.get("GRAPHIFY_BIN") or shutil.which("graphify")
    if not binary:
        print("graphify binary not found on PATH")
        return 1

    started = time.time()
    row = {
        "schema": "azazael.graphify_refresh.v1",
        "at": datetime.now(timezone.utc).isoformat(),
        "binary": binary,
        "returncode": None,
        "duration_s": None,
        "ok": False,
    }
    try:
        result = subprocess.run(
            [binary, "update", "."],
            cwd=str(ROOT), capture_output=True, text=True,
            timeout=INTERNAL_TIMEOUT_S, encoding="utf-8", errors="replace"
        )
        row["returncode"] = result.returncode
        tail = ((result.stdout or "") + (result.stderr or ""))[-2000:]
        row["output_tail"] = tail
        row["ok"] = result.returncode == 0
    except subprocess.TimeoutExpired:
        row["output_tail"] = "timed out after %ds (left no partial state)" % INTERNAL_TIMEOUT_S
    except Exception as exc:
        row["output_tail"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    row["duration_s"] = round(time.time() - started, 1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(OUT)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("%s ok=%s rc=%s %ss\n" % (row["at"], row["ok"],
                                           row["returncode"],
                                           row["duration_s"]))
    print("graphify refresh %s (%ss)" % ("ok" if row["ok"] else "FAILED",
                                         row["duration_s"]))
    return 0 if row["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())