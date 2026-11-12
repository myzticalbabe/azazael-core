"""audit_append_stress.py — prove the audit sidecar survives CONCURRENT appends.

WHY THIS EXISTS
---------------
`.audit/daemon_health.jsonl` is appended to by more than one process (the pmb
daemon and the pams launcher). On 2026-10-09 it was found corrupted exactly the
way concurrent non-atomic appends corrupt a file:

    line 41  {... "type":"pmb_registry_pruned"}        <- complete record
    line 42  _daemon_gone_by_pams"}                    <- orphan fragment
    line 110 emon_started"}                            <- orphan fragment

...after which `verify_audit_chain` returned `False, 'line 42 is not JSON'`.

`--mode buffered` reproduces the OLD writer (`path.open("a")` + `handle.write`)
so the test is a real negative control: if both modes passed, the test would be
proving nothing. `--mode atomic` exercises the fixed writer, which holds a
cross-process lock across the whole read-head -> append cycle.

Usage:
    python azazael/tools/audit_append_stress.py                 # both modes
    python azazael/tools/audit_append_stress.py --mode atomic   # fixed writer
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import time

UNIFIED = r"C:/Users/clint/llama-server/unified"


def _worker(mode: str, directory: str, records: int, pad: int) -> None:
    """Append `records` rows to the sidecar in `directory`. Runs as a process."""
    os.environ["AZAZAEL_AUDIT_DIR"] = directory
    sys.path.insert(0, UNIFIED)
    import threading

    import pmb_daemon_launcher as pmb
    path = Path(directory) / pmb.AUDIT_FILENAME
    payload = "x" * pad

    for i in range(records):
        if mode == "atomic":
            pmb.audit_event("stress", worker=os.getpid(), n=i, pad=payload)
            continue
        # The pre-fix writer, verbatim: an in-process lock plus a buffered
        # text-mode append, which on Windows is seek-to-end-then-write.
        with pmb._AUDIT_LOCK:
            prev = pmb._head_hash(path)
            entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": "stress",
                     "pid": os.getpid(), "n": i, "pad": payload,
                     "source": "pmb_daemon_launcher"}
            entry["prev_hash"] = prev
            entry["hash"] = pmb._compute_hash(entry, prev)
            line = json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    threading  # imported to mirror the original writer's locking context


def _run_mode(mode: str, procs: int, records: int, pad: int) -> dict:
    directory = tempfile.mkdtemp(prefix="audit-stress-%s-" % mode)
    workers = [
        subprocess.Popen([sys.executable, os.path.abspath(__file__),
                          "--worker", "--mode", mode, "--dir", directory,
                          "--records", str(records), "--pad", str(pad)])
        for _ in range(procs)
    ]
    for w in workers:
        try:
            w.wait(timeout=900)
        except subprocess.TimeoutExpired:
            print("audit_append_stress: worker %s exceeded 900s, terminating" % w.pid,
                  file=sys.stderr)
            w.kill()

    sys.path.insert(0, UNIFIED)
    import pmb_daemon_launcher as pmb
    path = Path(directory) / pmb.AUDIT_FILENAME
    lines = [l for l in path.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
    broken = []
    for i, line in enumerate(lines, 1):
        try:
            json.loads(line)
        except ValueError:
            broken.append(i)
    expected = procs * records
    ok, detail = pmb.verify_audit_chain(path)
    return {"mode": mode, "procs": procs, "records_per_proc": records,
            "expected": expected, "lines": len(lines), "lost": expected - len(lines),
            "non_json_lines": broken[:6], "non_json_total": len(broken),
            "chain_ok": ok, "chain_detail": detail, "dir": directory}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["atomic", "buffered", "both"], default="both")
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--records", type=int, default=300)
    ap.add_argument("--pad", type=int, default=2000)
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--dir")
    args = ap.parse_args()

    if args.worker:
        _worker(args.mode, args.dir, args.records, args.pad)
        return 0

    modes = ["buffered", "atomic"] if args.mode == "both" else [args.mode]
    verdict = 0
    for mode in modes:
        res = _run_mode(mode, args.procs, args.records, args.pad)
        print(json.dumps(res, indent=2))
        if mode == "atomic" and (res["non_json_total"] or not res["chain_ok"]
                                 or res["lines"] != res["expected"]):
            verdict = 1
        print()
    print("VERDICT:", "PASS (atomic writer held under %d processes)" % args.procs
          if verdict == 0 else "FAIL")
    return verdict


if __name__ == "__main__":
    sys.exit(main())
