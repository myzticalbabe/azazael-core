#!/usr/bin/env python3
"""Berks Counseling Center live monitor (first-party public sites only).

Runs BerksMonitorWorm.check_all(), appends JSONL to G:, feeds per-site
up/down into the spine ledger (downtime becomes training signal).
Schedule (user-level): every 30 min.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Resolve src/ portably from this file's location (no hardcoded user path).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Silence the registry chatter by NAME (never `logging.disable`): a blanket
# switch would also swallow this process's operator warnings.
from CORE.log_hygiene import quiet_noisy_loggers  # noqa: E402

quiet_noisy_loggers()

LOG = "G:/source-of-truth/azazael_models/bcc_status.jsonl"


def main() -> int:
    from CORE.WORMS.expansion.berks import BerksMonitorWorm
    from CORE import spine_registry as sp
    rep = BerksMonitorWorm().check_all()
    line = json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                       "up": rep["up"], "of": rep["of"], "results": rep["results"]})
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        print(f"log write failed: {e}")
    # These writes are the script's stated purpose ("per-site up/down into the
    # spine ledger; downtime becomes training signal"). Swallowing a failure
    # here meant the training signal could stop accumulating while every run
    # still exited successfully. Count them and say so.
    spine_written = 0
    spine_failed = []
    for r in rep["results"]:
        try:
            sp.record_outcome("monitor", f"berks:{r['host']}", bool(r["up"]), 0.0)
            spine_written += 1
        except Exception as e:  # noqa: BLE001
            spine_failed.append(f"{r['host']}: {type(e).__name__}")
    try:
        sp.save_spine_state()
    except Exception as e:  # noqa: BLE001
        spine_failed.append(f"save_spine_state: {type(e).__name__}")
    if spine_failed:
        print(f"WARN spine ledger not updated for {len(spine_failed)} of "
              f"{len(rep['results'])} outcomes ({'; '.join(spine_failed[:3])})"
              f" -- downtime is NOT becoming training signal")
    for r in rep["results"]:
        state = "UP" if r["up"] else "DOWN"
        print(f"{state} {r['host']} http={r.get('http_status')} "
              f"{r.get('http_ms', '?')}ms tls_days={r.get('tls_days_left')}")
    return 0 if rep["up"] == rep["of"] else 1


if __name__ == "__main__":
    sys.exit(main())
