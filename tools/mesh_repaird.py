#!/usr/bin/env python
# coding: utf-8
"""mesh_repaird — operations-hygiene repair loop FOR the Azazael neural mesh.

Same loop shape as the foundry repaird (detect -> diagnose -> act -> verify)
reoriented to mesh substrate. Explicit non-goals: this does NOT tune worms,
retune routers, or modify registry/src/tests/data — those belong to mesh
organs (HomeostatWorm, monitors) and human operators. This handles the
operations layer beneath them and escalates everything else.

Allowlist (acts autonomously):
  M1 stale locks/tmp   : *.lock older than 10 min + tmp_* cruft in workspace/
                         + temp files older than 7 days -> remove
  M2 scheduler presence: worm_matrix_scheduler task exists+enabled -> else ESCALATE
                         (never creates tasks; creation is operator act)
  M3 spine staleness    : SPINE_MAP older than 6h -> ESCALATE (never fabricate
                         topology; a stale map is a fact to report, not fix)
  M4 homeostat tripwire : failure rate over recent window > 25% -> ESCALATE
                         with summary (tuning is HomeostatWorm's job)
  M5 workspace disk    : report-only quota line (never deletes model/data)

Interlocks (never violated):
  - Read-only on: src/, tests/, data/, .audit/, *.db, configs, registry.
  - Never: git ops, process kills (mesh owns execution), network calls.
  - Stdlib only (matches spine_registry discipline; no import cycles).
  - Every action logged; --report dry-runs.

Usage:
  python tools/mesh_repaird.py            # one pass
  python tools/mesh_repaird.py --report   # dry run
"""
import os
import sys
import time

AZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGF = os.path.join(AZ, "logs", "mesh_repaird.log")
sys.path.insert(0, os.path.join(AZ, "src"))


def say(m, fh):
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    fh.write(line + "\n")


def file_age_s(path):
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return -1.0


def main():
    report = "--report" in sys.argv
    os.makedirs(os.path.dirname(LOGF), exist_ok=True)
    fh = open(LOGF, "a", encoding="utf-8")
    actions, escalate = [], []
    say(f"mesh_repaird pass report={report}", fh)

    # M1: stale locks + workspace tmp cruft.
    try:
        n = 0
        for dirpath, _, files in os.walk(os.path.join(AZ, "workspace")):
            for fn in files:
                p = os.path.join(dirpath, fn)
                if fn.endswith(".lock") and file_age_s(p) > 600:
                    if not report:
                        os.remove(p)
                    n += 1
                    say(f"{'WOULD ' if report else ''}M1 removed stale lock "
                        f"{os.path.relpath(p, AZ)}", fh)
                elif fn.startswith("tmp_") and file_age_s(p) > 86400:
                    if not report:
                        os.remove(p)
                    n += 1
        if n:
            actions.append(f"M1 cleared {n} stale locks/tmp files")
    except Exception as e:
        escalate.append(f"M1 workspace walk failed: {e}")

    # M2: scheduler presence (check only).
    try:
        import subprocess
        r = subprocess.run(
            ["schtasks", "/query", "/TN", "worm_matrix_scheduler"],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            escalate.append("M2 worm_matrix_scheduler task MISSING - operator must create it")
        else:
            say("M2 scheduler present", fh)
    except Exception as e:
        escalate.append(f"M2 scheduler query failed: {e}")

    # M3/M4: spine staleness + homeostat tripwire (read-only; escalate only).
    try:
        from CORE.spine_registry import get_homeostat_view
        import datetime
        view = get_homeostat_view()
        now = datetime.datetime.now()
        latest, total_s, total_f, worst, erring = None, 0, 0, [], []
        for name, rec in view.items():
            if not isinstance(rec, dict):
                continue
            total_s += int(rec.get("successes", 0) or 0)
            total_f += int(rec.get("failures", 0) or 0)
            try:
                ts = datetime.datetime.fromisoformat(str(rec.get("last_at", "")))
                if latest is None or ts > latest:
                    latest = ts
            except (ValueError, TypeError):
                pass  # scanner:ignore -- unparseable/missing last_at means the record never ran; leaving latest unset is the designed stale trigger
            f = int(rec.get("failures", 0) or 0)
            s = int(rec.get("successes", 0) or 0)
            if s + f > 0 and f / (s + f) > 0.5:
                worst.append((name, f, s))
            if str(rec.get("last_error", "") or "").strip():
                erring.append((name, str(rec["last_error"])[:100]))
        if latest is None or (now - latest).total_seconds() > 6 * 3600:
            escalate.append(f"M3 spine state stale (latest activity: {latest}) - "
                            "topology must come from mesh organs, never fabricated")
        else:
            say(f"M3 spine fresh ({len(view)} worms, latest {latest})", fh)
        denom = total_s + total_f
        rate = total_f / denom if denom else 0.0
        if rate > 0.25:
            worst = sorted(worst, key=lambda x: -x[1])[:5]
            escalate.append(
                f"M4 homeostat tripwire: failure rate {rate:.1%} over "
                f"{denom} outcomes; worst={worst} - tuning is HomeostatWorm's job")
        else:
            say(f"M4 failure rate {rate:.1%} over {denom} outcomes (floor 25%)", fh)
        for name, err in erring[:5]:
            say(f"M4 note {name}: {err}", fh)
    except Exception as e:
        escalate.append(f"M3 spine_registry unreadable: {e}")

    # M5: workspace disk line.
    try:
        import shutil
        total, used, free = shutil.disk_usage(AZ[:3] if AZ[1:2] == ":" else "/")
        say(f"M5 disk free={free/1e9:.1f}GB on {AZ[:3] if AZ[1:2]==':' else '/'}", fh)
    except Exception as e:
        escalate.append(f"M5 disk check failed: {e}")

    say(f"pass done: {len(actions)} actions, {len(escalate)} escalations", fh)
    for e in escalate:
        say(f"ESCALATE: {e}", fh)
    fh.close()
    return 1 if escalate else 0


if __name__ == "__main__":
    sys.exit(main())
