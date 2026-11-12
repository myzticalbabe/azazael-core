#!/usr/bin/env python3
"""Azazael environment drift alarm.

Answers one question on a schedule: *has the estate diverged from the state we
last accepted?* It is deliberately louder than the tools it wraps:

  SIGNAL                  TRIPS WHEN
  ----------------------  -----------------------------------------------------
  1 baseline-contract     any contract package is skewed or missing (via
                          check_runtime_baseline.py)
  2 sync-integrity        sync_all.py --status reports OUT OF SYNC, or any of
                          expansion/tests/scripts FAIL
  3 environment-shape     a pillar's distribution count moved beyond tolerance
                          since the last --bless
  4 disk-headroom         a drive used by the estate drops below its floor
  5 adaptation-stack      the stack no longer imports on the free-threaded
                          runtime (the I1 regression guard)

Exit codes:
    0  no drift
    1  drift detected
    2  the alarm could not gather every signal (treat as failure by default)

Usage:
    python azazael/scripts/env_drift_alarm.py                 # check
    python azazael/scripts/env_drift_alarm.py --bless         # accept current state
    python azazael/scripts/env_drift_alarm.py --no-wsl        # skip WSL probes
    python azazael/scripts/env_drift_alarm.py --json out.json --report out.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# The estate's own convention (PYTHONIOENCODING=utf-8) cannot be relied on for a
# scheduled task, and a Windows cp1252 console will crash on any non-ASCII byte
# that arrives from a subprocess. Force UTF-8 and degrade unencodable chars.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass  # scanner:ignore -- console reconfigure is best-effort; a stream that refuses UTF-8 still runs the alarm

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

try:
    import check_runtime_baseline as crb  # type: ignore
except Exception as exc:  # pragma: no cover
    print(f"FATAL: cannot import check_runtime_baseline: {exc}", file=sys.stderr)
    raise SystemExit(2)

STATE = REPO / "state" / "env_drift_state.json"

# Disk floors in GB. C: held 13 GB at the 2026-09-20 inventory, which is why
# headroom is a first-class signal rather than a footnote.
DISK_FLOORS_GB = {"C:": 40.0, "G:": 20.0, "D:": 10.0}
DISK_WARN_GB = {"C:": 80.0, "G:": 40.0, "D:": 20.0}

# How much a pillar's distribution count may move without being called drift.
DIST_TOLERANCE = 15

# Free-threaded runtime + its adaptation stack (the I1 regression guard).
# 3.14t was retired 2026-10-19; the adaptation stack now imports on the 3.15t
# production venv. The retired venv-314t path would only prove the archive works.
ADAPT_PYTHON = r"C:\Azazael\.venvs\py315t\Scripts\python.exe"
ADAPT_MODULES = ["torch", "transformers", "gymnasium", "stable_baselines3",
                 "peft", "trl", "datasets", "accelerate"]

STATUS_LEVELS = {"ok": 0, "warn": 1, "accepted": 1, "fail": 2}

# Conditions that have been deliberately accepted, with the decision recorded.
# An alarm that fires forever on a known, chosen condition stops being read -
# which is the exact failure mode (a red check nobody runs) this script exists
# to prevent. Accepted conditions are still PRINTED, never hidden.
DEFAULT_ACCEPTED: dict[str, dict] = {
    "disk-headroom": {
        "since": "2026-09-20",
        "reason": "C: headroom deliberately not reclaimed. The model-store "
                  "relocation (Ollama 25G -> D:, LM Studio 53G -> G:) was planned "
                  "and declined 2026-09-20; C: stays near-full by decision. "
                  "D: 70G and G: 90G remain free if this is revisited. "
                  "Procedure is staged in scripts/relocate_model_stores.sh --plan.",
        "review_when": "C: free falls below 10 GB, or a build needs the space",
    },
}


def _ascii(text: str) -> str:
    """Keep console output encodable on a cp1252 Windows terminal."""
    return text.encode("ascii", "replace").decode("ascii")


def run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except Exception as exc:  # pragma: no cover
        return 125, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# Signal 1 — runtime baseline contract
# --------------------------------------------------------------------------- #
def signal_baseline(no_wsl: bool) -> dict:
    cmd = [sys.executable, str(SCRIPTS / "check_runtime_baseline.py"), "--quiet"]
    if no_wsl:
        cmd += ["--envs", "win-main,win-315,win-314m,win-313tools,win-interp"]
    rc, out = run(cmd)
    verdict_re = re.search(r"VERDICT:\s*(.+)", out)
    detail = verdict_re.group(1).strip() if verdict_re else out.strip()[:200]
    if rc == 0:
        return {"name": "baseline-contract", "status": "ok", "detail": "all environments match the contract"}
    if rc == 1:
        per_env = _baseline_json()
        total = sum(len(r.get("contract_violations", {})) + len(r.get("contract_gaps", []))
                    for r in per_env.values())
        bad_envs = [e for e, r in per_env.items()
                    if r.get("contract_violations") or r.get("contract_gaps")]
        count = total or (re.search(r"\d+", detail) or [None])[0] or "?"
        where = f" across {', '.join(bad_envs)}" if bad_envs else ""
        return {"name": "baseline-contract", "status": "fail",
                "detail": f"{count} contract violation(s){where}"}
    return {"name": "baseline-contract", "status": "fail",
            "detail": f"probe incomplete (exit {rc}) - {_ascii(detail)}"}


def _baseline_json() -> dict:
    tmp = REPO / "reports" / "env_inventory" / "_alarm_baseline.json"
    rc, _ = run([sys.executable, str(SCRIPTS / "check_runtime_baseline.py"),
                 "--quiet", "--json", str(tmp)])
    try:
        return json.loads(tmp.read_text(encoding="utf-8")).get("environments", {})
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Signal 2 — sync integrity
# --------------------------------------------------------------------------- #
def signal_sync() -> dict:
    script = SCRIPTS / "sync_all.py"
    if not script.exists():
        return {"name": "sync-integrity", "status": "fail",
                "detail": f"sync_all.py missing at {script}"}
    rc, out = run([sys.executable, str(script), "--status"])
    if rc != 0 and not out:
        return {"name": "sync-integrity", "status": "fail",
                "detail": f"sync_all.py --status exited {rc}"}
    m = re.search(r"Integrity:\s*(.+)", out)
    integrity = m.group(1).strip() if m else "UNKNOWN"
    # Current sync_all.py --status prints a parity table whose section lines look
    # like:  "    expansion  vs WSL2   65/69   DRIFT".  Older builds printed
    # "<section>: FAIL".  Accept either, and name the failing sections - an
    # integrity verdict without them is what made this check unreadable before.
    fails = re.findall(r"^\s+(\w+)\s+vs\s+\S.*?\b(?:DRIFT|UNREACHABLE)\s*$", out, re.M)
    if not fails:
        fails = re.findall(r"^\s+(\w+):\s*(?:FAIL|out of sync)", out, re.M)
    if "OUT OF SYNC" in integrity.upper() or fails:
        named = f" | failing: {', '.join(fails)}" if fails else ""
        return {"name": "sync-integrity", "status": "fail",
                "detail": f"{integrity}{named}"}
    if "UNKNOWN" in integrity.upper():
        return {"name": "sync-integrity", "status": "fail",
                "detail": "could not parse sync_all.py output - the check did not run"}
    return {"name": "sync-integrity", "status": "ok", "detail": integrity}


# --------------------------------------------------------------------------- #
# Signal 3 — environment shape (dist counts vs last blessed snapshot)
# --------------------------------------------------------------------------- #
def collect_shape(no_wsl: bool) -> dict:
    shape: dict[str, int] = {}
    for name, spec in crb.ENV_SPECS.items():
        if no_wsl and spec["kind"] == "wsl":
            continue
        pkgs, err = crb.probe(name, spec)
        shape[name] = len(pkgs) if pkgs else -1
    return shape


def signal_shape(no_wsl: bool, current: dict, previous: dict) -> dict:
    if not previous:
        return {"name": "environment-shape", "status": "warn",
                "detail": "no blessed snapshot yet - run --bless to record one"}
    changes, unreachable = [], []
    for env, count in current.items():
        if count < 0:
            unreachable.append(env)
            continue
        old = previous.get(env)
        if old is None or old < 0:
            changes.append(f"{env}: new/unknown ({count})")
        elif abs(count - old) > DIST_TOLERANCE:
            changes.append(f"{env}: {old} -> {count} ({count - old:+d})")
    if unreachable:
        return {"name": "environment-shape", "status": "fail",
                "detail": f"unreachable: {', '.join(unreachable)}"}
    if changes:
        return {"name": "environment-shape", "status": "fail",
                "detail": "; ".join(changes)}
    return {"name": "environment-shape", "status": "ok",
            "detail": f"{len(current)} environment(s) within +/-{DIST_TOLERANCE} dists"}


# --------------------------------------------------------------------------- #
# Signal 4 — disk headroom
# --------------------------------------------------------------------------- #
def signal_disk() -> dict:
    problems, warns, notes = [], [], []
    for drive, floor in DISK_FLOORS_GB.items():
        path = drive  # shutil.disk_usage accepts "C:\\" style
        try:
            free_gb = shutil.disk_usage(path).free / (1024 ** 3)
        except OSError:
            notes.append(f"{drive} unavailable")
            continue
        tag = f"{drive} {free_gb:.0f}G free (floor {floor:.0f}G)"
        if free_gb < floor:
            problems.append(tag)
        elif free_gb < DISK_WARN_GB.get(drive, floor * 2):
            warns.append(tag)
    if problems:
        return {"name": "disk-headroom", "status": "fail", "detail": "; ".join(problems + warns)}
    if warns:
        return {"name": "disk-headroom", "status": "warn", "detail": "; ".join(warns)}
    return {"name": "disk-headroom", "status": "ok", "detail": f"checked {len(DISK_FLOORS_GB)} drives"}


# --------------------------------------------------------------------------- #
# Signal 5 — adaptation stack on the free-threaded runtime
# --------------------------------------------------------------------------- #
def signal_adaptation() -> dict:
    if not Path(ADAPT_PYTHON).exists():
        return {"name": "adaptation-stack", "status": "fail",
                "detail": f"free-threaded interpreter missing: {ADAPT_PYTHON}"}
    code = (
        "import importlib,sys\n"
        f"mods={ADAPT_MODULES!r}\n"
        "bad=[]\n"
        "for m in mods:\n"
        "    try: importlib.import_module(m)\n"
        "    except Exception: bad.append(m)\n"
        "print('GIL_ENABLED=' + str(getattr(sys,'_is_gil_enabled',lambda:True)()))\n"
        "print('MISSING=' + ','.join(bad))\n"
    )
    rc, out = run([ADAPT_PYTHON, "-c", code], timeout=300)
    if rc != 0:
        return {"name": "adaptation-stack", "status": "fail",
                "detail": f"interpreter exited {rc}: {' '.join(out.split())[:160]}"}
    gil = re.search(r"GIL_ENABLED=(\w+)", out)
    missing = re.search(r"MISSING=(.*)", out)
    missing_list = [m for m in (missing.group(1).strip().split(",") if missing else []) if m]
    if gil and gil.group(1) == "True":
        return {"name": "adaptation-stack", "status": "fail",
                "detail": "target runtime has the GIL re-enabled - free-threading lost"}
    if missing_list:
        return {"name": "adaptation-stack", "status": "fail",
                "detail": f"cannot import on 3.15t: {', '.join(missing_list)}"}
    return {"name": "adaptation-stack", "status": "ok",
            "detail": f"{len(ADAPT_MODULES)}/{len(ADAPT_MODULES)} import free-threaded"}


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bless", action="store_true",
                    help="record the current environment shape as accepted")
    ap.add_argument("--no-wsl", action="store_true", help="skip WSL probes")
    ap.add_argument("--json", type=Path, help="write machine-readable result")
    ap.add_argument("--report", type=Path, help="write a markdown report")
    ap.add_argument("--soft", action="store_true",
                    help="always exit 0 (report only; for observation runs)")
    ap.add_argument("--accept", metavar="SIGNAL",
                    help="record a signal's failure as deliberately accepted")
    ap.add_argument("--reason", default="", help="why it is accepted (required with --accept)")
    ap.add_argument("--unaccept", metavar="SIGNAL", help="remove an accepted condition")
    ap.add_argument("--list-accepted", action="store_true")
    args = ap.parse_args()

    now = dt.datetime.now().astimezone()
    stored: dict = {}
    if STATE.exists():
        try:
            stored = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            stored = {}
    previous = stored.get("shape", {})
    accepted: dict = stored.get("accepted", dict(DEFAULT_ACCEPTED))

    def save_state(**extra) -> None:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"blessed_at": stored.get("blessed_at"), "shape": previous,
                   "accepted": accepted}
        payload.update(extra)
        STATE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.list_accepted:
        if not accepted:
            print("no accepted conditions")
            return 0
        for sig, meta in sorted(accepted.items()):
            print(f"{sig}")
            print(f"  since:  {meta.get('since', '?')}")
            print(f"  reason: {meta.get('reason', '?')}")
            print(f"  review: {meta.get('review_when', '?')}")
        return 0

    if args.accept:
        if not args.reason:
            print("ERROR: --accept requires --reason", file=sys.stderr)
            return 2
        accepted[args.accept] = {"since": now.date().isoformat(), "reason": args.reason}
        previous = stored.get("shape", previous)
        save_state()
        print(f"accepted '{args.accept}' -> {STATE}")
        return 0

    if args.unaccept:
        accepted.pop(args.unaccept, None)
        save_state()
        print(f"removed acceptance for '{args.unaccept}'")
        return 0

    if args.bless:
        shape = collect_shape(args.no_wsl)
        previous = shape
        save_state(blessed_at=now.isoformat())
        print(f"blessed {len(shape)} environment(s) -> {STATE}")
        for env, n in sorted(shape.items()):
            print(f"  {env:14s} {n if n >= 0 else 'UNREACHABLE'}")
        return 0

    signals = [
        signal_baseline(args.no_wsl),
    ]
    current_shape = collect_shape(args.no_wsl)
    signals.append(signal_shape(args.no_wsl, current_shape, previous))
    signals.append(signal_sync())
    signals.append(signal_disk())
    signals.append(signal_adaptation())

    # Downgrade failures that were deliberately accepted, but never hide them.
    for s in signals:
        meta = accepted.get(s["name"])
        if s["status"] == "fail" and meta:
            s["status"] = "accepted"
            s["accepted_since"] = meta.get("since")
            s["accepted_reason"] = meta.get("reason")
            s["detail"] = f"{s['detail']}  [ACCEPTED since {meta.get('since', '?')}]"

    worst = max(STATUS_LEVELS.get(s["status"], 2) for s in signals)
    code = 0 if (worst == 0 or args.soft) else (1 if worst == 1 else 1)

    print("=" * 78)
    print(f"AZAZAEL ENVIRONMENT DRIFT ALARM   {now:%Y-%m-%d %H:%M:%S %Z}")
    print("=" * 78)
    for s in signals:
        tag = {"ok": "  ok  ", "warn": " WARN ", "accepted": "ACCEPT",
               "fail": " FAIL "}.get(s["status"], "  ??  ")
        print(f"[{tag}] {s['name']:20s} {s['detail']}")
    print()
    accepted_now = [s["name"] for s in signals if s["status"] == "accepted"]
    if accepted_now:
        print(f"accepted by decision (not drift): {', '.join(accepted_now)}")
        print("  review with: env_drift_alarm.py --list-accepted")
        print()
    print(f"VERDICT: {'NO DRIFT' if worst == 0 else 'DRIFT DETECTED' if worst == 2 else 'WARNING'}")
    if worst == 2 and not args.soft:
        print("a scheduled run that exits non-zero is the alarm firing — investigate,")
        print("do not re-bless until the divergence is explained.")

    if worst == 2:
        verdict = "drift"
    elif accepted_now:
        verdict = f"ok-{len(accepted_now)}-accepted"
    else:
        verdict = "no-drift" if worst == 0 else "warning"
    result = {"timestamp": now.isoformat(), "verdict": verdict,
              "accepted": sorted(accepted_now), "shape": current_shape,
              "signals": signals}

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"json:   {args.json}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# Drift alarm - {now:%Y-%m-%d %H:%M:%S}", "",
                 f"**Verdict: {result['verdict'].upper()}**", "",
                 "| signal | status | detail |", "|---|---|---|"]
        lines += [f"| `{s['name']}` | {s['status'].upper()} | {s['detail']} |" for s in signals]
        lines += ["", "## Environment shape", "", "| environment | distributions |", "|---|---:|"]
        lines += [f"| {e} | {n if n >= 0 else 'UNREACHABLE'} |" for e, n in sorted(current_shape.items())]
        args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"report: {args.report}")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
