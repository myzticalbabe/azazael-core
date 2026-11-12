"""Eve Unleashed -- live event log.

Testing, evolving and dreaming stream here while they run: one JSON
object per line, plus one human line on stdout per event. A replay, a
``tail -f``, or the next session reads the same file -- the stream is
the record, not a recollection of it.

Path: EVE_LIVE_LOG, else state/eve_live.jsonl under the repo cwd.
"""

import json
import os
import threading
import time

try:
    from CORE.failure_ledger import note_failure
except Exception:
    def note_failure(where, exc=None):
        pass

_LOCK = threading.Lock()


def default_path():
    """Where events go unless a caller names a path."""
    return os.environ.get("EVE_LIVE_LOG",
                           os.path.join("state", "eve_live.jsonl"))


def emit(phase, event, path=None, session=None, **fields):
    """Append one event and print one live line. Returns the record."""
    path = path or default_path()
    rec = {"ts": time.time(), "phase": phase, "event": event}
    if session is not None:
        rec["session"] = session
    rec.update(fields)
    line = json.dumps(rec, default=str)
    parent = os.path.dirname(path)
    with _LOCK:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    flat = " ".join("%s=%s" % (k, v) for k, v in fields.items())
    print("LIVE [%s] %s %s" % (phase, event, flat), flush=True)
    return rec


def read_events(path=None, limit=None):
    """Read records oldest-first. Malformed lines are skipped and counted."""
    path = path or default_path()
    recs, bad = [], 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    bad += 1
    except FileNotFoundError as e:
        note_failure("live_log:read:missing", e)
        return {"records": [], "bad": 0}
    if limit is not None:
        recs = recs[-limit:]
    return {"records": recs, "bad": bad}


def tail(path=None, n=20):
    """Last n records, oldest-first."""
    return read_events(path, limit=n)["records"]
