# coding: utf-8
"""Scheduled Ex Machina health probe: alert when the tier degrades.

Run every N minutes by the "Ex Machina Health" scheduled task (see
`exmachina_health_check.bat`). Every run writes `state/exmachina_health.json`,
so the tier's health is itself an observable artifact. Only on a TRANSITION
to degraded does it append to `logs/exmachina_health_alerts.log` and raise a
brief, auto-dismissing message box -- a fault that persists for hours must not
raise a box every fifteen minutes, or the alert is trained away.

Exit code: 0 healthy, 1 degraded (so a task or CI can gate on it).
"""
import argparse
import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import exmachina_live_monitor as mon  # noqa: E402

_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
STATE_JSON = os.path.join(_ROOT, "state", "exmachina_health.json")
ALERT_LOG = os.path.join(_ROOT, "logs", "exmachina_health_alerts.log")


def _popup(title, text, seconds=20):
    """Best-effort timed alert. Never raises, never hangs the scheduled run.

    MessageBoxTimeoutW auto-dismisses; MessageboxW would block the task until
    someone clicks OK, which a background probe must never do.
    """
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxTimeoutW(0, text, title, 0x30, 0,
                                                int(seconds) * 1000)
        return True
    except Exception:
        return False


def run(quiet=False, state_json=STATE_JSON, alert_log=ALERT_LOG):
    """One probe. Returns (ok, message). Writes state; alerts on transition."""
    ok, msg = mon.health_check()
    now = datetime.now().isoformat(timespec="seconds")

    prev_ok = None
    try:
        with open(state_json, encoding="utf-8") as f:
            prev_ok = (json.load(f) or {}).get("ok")
    except Exception:
        pass                                   # no previous state is not a fault

    try:
        os.makedirs(os.path.dirname(state_json), exist_ok=True)
        with open(state_json, "w", encoding="utf-8") as f:
            json.dump({"at": now, "ok": ok, "message": msg}, f, indent=2)
    except Exception:
        pass

    if not ok and prev_ok is not False:        # transition into degraded
        try:
            os.makedirs(os.path.dirname(alert_log), exist_ok=True)
            with open(alert_log, "a", encoding="utf-8") as f:
                f.write("[%s] DEGRADED %s\n" % (now, msg))
        except Exception:
            pass
        if not quiet:
            _popup("Ex Machina degraded", "%s\n\n%s" % (now, msg))
    return ok, msg


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ex Machina tier health probe (scheduled)")
    ap.add_argument("--quiet", action="store_true",
                    help="log only; do not raise a message box on a new degradation")
    args = ap.parse_args(argv)
    ok, msg = run(quiet=args.quiet)
    print(("OK   " if ok else "FAIL ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
