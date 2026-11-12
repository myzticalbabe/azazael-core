#!/usr/bin/env python3
# coding: utf-8
"""terence_env_digest.py — nightly cross-pillar digest of TERENCE history.

`terence_invariants.py --environments` buckets recorded faults by
(class, environment). That view is only a TRACKING signal if it is written on a
cadence: environment drift across the four pillars (Windows / Ubuntu / Kali /
G:) is otherwise something you see only when you remember to look.

This job runs the view, persists it as structured JSON + markdown under
state/, and prints one summary line for the Chain log. It writes nothing into
any pillar tree — only the Windows pillar's own state/ directory.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORKSPACE = os.environ.get("PMB_WORKSPACE_DIR",
                           r"C:\Users\clint\.pmb\workspaces\04a151a73354")
ERROR_DB = os.path.join(WORKSPACE, "events.sqlite")
OUT_JSON = ROOT / "state" / "terence_env_digest.json"
OUT_MD = ROOT / "state" / "terence_env_digest.md"
TERENCE = HERE / "terence_invariants.py"


def _view_text() -> str:
    try:
        p = subprocess.run([sys.executable, str(TERENCE), "--environments"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           timeout=120, encoding="utf-8", errors="replace")
        return (p.stdout or "") + (p.stderr or "")
    except Exception as exc:
        return "view failed: %s" % exc


def _bucket() -> dict:
    """The same measurement the view makes, kept structured for the artifact."""
    rows = []
    try:
        con = sqlite3.connect(ERROR_DB, timeout=20)
        rows = con.execute(
            "SELECT component, note FROM error_log "
            "WHERE component LIKE 'terence:%'").fetchall()
        con.close()
    except Exception as exc:
        return {"error": str(exc)}
    pairs = Counter()
    untagged = 0
    for comp, note in rows:
        klass = comp.split("terence:", 1)[1] if comp.startswith("terence:") else comp
        m = re.search(r"env=(\w+)", note or "")
        env = m.group(1) if m else "(untagged:pre-2026-09-30)"
        pairs[(klass, env)] += 1
        untagged += 0 if m else 1
    by_env = Counter()
    for (klass, env), n in pairs.items():
        by_env[env] += n
    return {
        "rows": len(rows),
        "untagged": untagged,
        "by_environment": dict(by_env.most_common()),
        "by_class_environment": {"%s | %s" % k: v for k, v in sorted(pairs.items())},
    }


def main() -> int:
    buckets = _bucket()
    text = _view_text()
    rep = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema": "azazael.terence.env_digest.v1",
        "rule": "bucket by (class, environment): the same path means different "
                "things in different pillars",
        "measured": buckets,
        "view": text.strip().splitlines()[-12:],
    }
    try:
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(rep, indent=2), encoding="utf-8")
        lines = ["# TERENCE cross-pillar digest", "",
                 "generated: %s" % rep["generated_at"],
                 "rows: %s   untagged(legacy): %s"
                 % (buckets.get("rows"), buckets.get("untagged")), "",
                 "## totals by environment", ""]
        for env, n in (buckets.get("by_environment") or {}).items():
            lines.append("- %-26s %d" % (env, n))
        lines += ["", "## by (class, environment)", ""]
        for k, v in (buckets.get("by_class_environment") or {}).items():
            lines.append("- %-52s %d" % (k, v))
        OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        print("terence-env: write failed: %s" % exc)
        return 1
    envs = buckets.get("by_environment") or {}
    print("terence-env: %d rows across %d environment(s): %s"
          % (buckets.get("rows", 0), len(envs),
             ", ".join("%s=%d" % kv for kv in envs.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
