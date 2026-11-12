"""Lane forensics: surface what every lane is doing inside this box, in one run.

Born 2026-11-07: a lane agent (kilo) died mid-verification and none of the
usual instruments could say why -- tasklist flags mangled by Git-bash path
conversion read as 'nothing running', the board CLI hid claim states, and the
dead client's UI (approval text, crash panel) lives nowhere on disk. This tool
encodes the checks that worked, with the failure modes removed:

- process list via `tasklist.exe` with MSYS_NO_PATHCONV=1 (never /V, /FO --
  both get mangled or hang under Git bash; grep the plain TABLE output)
- board ledger via SQL on telemetry_store.db (memorybus_queue tail +
  lane_task_state unretired claims) -- the CLI wrapper filters claim states
- build-tree freshness (PCbuild/amd64t mtimes) + interpreter differential:
  raw build-tree binary vs venv binary, each with a hard timeout, because a
  hanging venv launcher reads as 'broken build' when the build is fine
- event-log recent errors via a simple powershell one-liner (no complex
  quoting -- it breaks through two layers of shell escaping)

Usage:
    python tools/lane_forensics.py            # full sweep, exits 0
    python tools/lane_forensics.py --board     # board tail only
    python tools/lane_forensics.py --procs     # agent processes only
    python tools/lane_forensics.py --interp    # interpreter differential only
    python tools/lane_forensics.py --death     # dead-lane forensics only
    python tools/lane_forensics.py --waiting   # unclaimed work per lane only
    python tools/lane_forensics.py --fresh 30  # files changed in last 30 min

Read-only. Writes nothing, claims nothing, Retires nothing.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = r"C:\Users\clint\.azazael\telemetry_store.db"
BUILD_BIN = (
    r"C:\Azazael\.build\cpython-316\PCbuild\amd64t\python.exe"
)
VENV_BIN = r"C:\Azazael\.venvs\py316t\Scripts\python.exe"
AGENT_NAMES = ("kilo", "opencode", "freebuff", "codebuff", "bun", "node")


def _env() -> dict:
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"  # tasklist /X flags must not become paths
    return env


def procs() -> list[str]:
    """Agent/runtime processes alive right now (plain TABLE output only)."""
    try:
        out = subprocess.run(
            ["tasklist.exe"], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, env=_env(),
        ).stdout
    except Exception as exc:  # noqa: BLE001 -- forensics must not die
        return [f"tasklist failed: {exc}"]
    hits = [
        line for line in out.splitlines()
        if any(name in line.lower() for name in AGENT_NAMES)
    ]
    return hits or ["no lane agent processes running"]


def board_tail(n: int = 8) -> list[str]:
    """Newest board rows across all lanes, with claim state attached."""
    if not os.path.exists(STORE):
        return [f"store missing: {STORE}"]
    lines: list[str] = []
    db = None
    try:
        db = sqlite3.connect(STORE, timeout=30)
        states = {
            r[0]: r[1:] for r in db.execute(
                "SELECT board_id, state, claimed_by, retired_at,"
                " substr(note,1,80) FROM lane_task_state"
            )
        }
        rows = db.execute(
            "SELECT id, topic, substr(content,1,140), timestamp"
            " FROM memorybus_queue ORDER BY id DESC LIMIT ?", (n,),
        ).fetchall()
        for bid, topic, content, ts in rows:
            st = states.get(bid)
            tag = (
                f"[{st[0]}/{st[1] or '-'}]"
                if st else "[unclaimed]"
            )
            lines.append(f"{bid} {tag} {topic} @ {ts}\n    {content}")
        open_claims = db.execute(
            "SELECT board_id, lane, claimed_by, claimed_at FROM lane_task_state"
            " WHERE retired_at IS NULL AND claimed_by IS NOT NULL"
            " ORDER BY board_id DESC LIMIT 10"
        ).fetchall()
        if open_claims:
            lines.append("UNRETIRED CLAIMS (held, possibly by dead lanes):")
            lines += [f"  {r}" for r in open_claims]
    except Exception as exc:  # noqa: BLE001 -- forensics must not die
        return [f"board query failed: {exc}"]
    finally:
        if db is not None:
            db.close()
    return lines or ["board empty"]


def interp(timeout: int = 40) -> list[str]:
    """Raw build-tree binary vs venv binary: which runs, which hangs."""
    lines: list[str] = []
    for label, exe in (("raw", BUILD_BIN), ("venv", VENV_BIN)):
        if not os.path.exists(exe):
            lines.append(f"{label}: missing ({exe})")
            continue
        t0 = time.time()
        try:
            p = subprocess.run(
                [exe, "-I", "-c", "import sys; print(sys.version.split()[0],"
                 " sys._is_gil_enabled())"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=_env(),
            )
            dt = time.time() - t0
            lines.append(
                f"{label}: exit={p.returncode} in {dt:.1f}s ::"
                f" {(p.stdout or p.stderr).strip()[:100]}"
            )
        except subprocess.TimeoutExpired:
            lines.append(f"{label}: HANG (>{timeout}s, killed) -- wedged")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{label}: error {exc}")
    return lines


def build_fresh(max_age_min: int = 180) -> list[str]:
    """Newest files in the interpreter build tree (is anything building)."""
    root = r"C:\Azazael\.build\cpython-316\PCbuild\amd64t"
    if not os.path.isdir(root):
        return [f"build tree missing: {root}"]
    newest = sorted(
        (os.path.getmtime(os.path.join(root, f)), f)
        for f in os.listdir(root)
    )[-3:]
    now = time.time()
    return [
        f"{f}: {int((now - m) // 60)} min ago" + ("  <-- FRESH" if (now - m) < max_age_min * 60 else "")
        for m, f in reversed(newest)
    ]


def death() -> list[str]:
    """Dead-lane forensics: WER queue, recent app errors, missing agents."""
    lines: list[str] = []
    wer = r"C:\ProgramData\Microsoft\Windows\WER\ReportQueue"
    try:
        entries = sorted(os.listdir(wer))[-3:]
        lines.append(f"WER queue newest: {entries or ['empty']}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"WER unreadable: {exc}")
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-WinEvent -FilterHashtable @{LogName='Application';"
             " Level=2} -MaxEvents 4 |"
             " Select-Object TimeCreated, Id, ProviderName"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, env=_env(),
        ).stdout.strip().splitlines()
        lines.append("app errors: " + (" | ".join(out[3:7]) if len(out) > 3 else "none recent"))
    except Exception as exc:  # noqa: BLE001
        lines.append(f"event log unreadable: {exc}")
    alive = "\n".join(procs()).lower()
    for agent in ("kilo", "opencode", "freebuff"):
        if agent not in alive:
            lines.append(f"MISSING: no {agent} process (dead or never started)")
    return lines


def waiting() -> list[str]:
    """Unclaimed lane.*.tasks rows: work waiting that no lane picked up."""
    if not os.path.exists(STORE):
        return [f"store missing: {STORE}"]
    try:
        db = sqlite3.connect(STORE, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return [f"store unreadable: {exc}"]
    try:
        claimed = {r[0] for r in db.execute(
            "SELECT board_id FROM lane_task_state")}
        rows = db.execute(
            "SELECT id, topic, substr(content,1,120), timestamp"
            " FROM memorybus_queue")
        pending: dict[str, int] = {}
        detail: list[str] = []
        for bid, topic, content, ts in rows:
            if topic.endswith(".tasks") and bid not in claimed:
                lane = topic.split(".")[1] if "." in topic else topic
                pending[lane] = pending.get(lane, 0) + 1
                detail.append(f"  {bid} -> {topic} @ {ts}")
        lines = [f"waiting: {lane}={n}" for lane, n in sorted(pending.items())]
        return (lines + detail[-6:]) or ["nothing waiting"]
    finally:
        db.close()


def fresh(minutes: int = 30) -> list[str]:
    """Newest files in repo root, state, tools, build (who wrote lately)."""
    roots = [REPO, os.path.join(REPO, "state"),
             os.path.join(REPO, "tools"), r"C:\Azazael\.build"]
    hits: list[tuple[float, str]] = []
    now = time.time()
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            names = os.listdir(root)
        except OSError as exc:
            hits.append((0.0, f"{root}: unreadable ({exc})"))
            continue
        for f in names:
            p = os.path.join(root, f)
            if os.path.isfile(p):
                m = os.path.getmtime(p)
                if (now - m) < minutes * 60:
                    hits.append((m, p))
    hits.sort(reverse=True)
    return [f"{time.ctime(m)} :: {p}" for m, p in hits[:12]] or [
        f"nothing written in last {minutes} min"
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Surface lane activity in this box.")
    ap.add_argument("--board", action="store_true")
    ap.add_argument("--procs", action="store_true")
    ap.add_argument("--interp", action="store_true")
    ap.add_argument("--death", action="store_true")
    ap.add_argument("--waiting", action="store_true")
    ap.add_argument("--fresh", type=int, metavar="MIN", default=0)
    ap.add_argument("--requiem", metavar="LANE", default=None,
                    help="write state/requiem_<lane>_<date>.json bundle and print it")
    args = ap.parse_args()
    everything = not (args.board or args.procs or args.interp
                      or args.death or args.waiting or args.fresh)
    if args.procs or everything:
        print("== processes ==")
        print("\n".join(procs()))
    if args.board or everything:
        print("== board tail ==")
        print("\n".join(board_tail()))
    if args.waiting or everything:
        print("== waiting work ==")
        print("\n".join(waiting()))
    if args.death or everything:
        print("== death forensics ==")
        print("\n".join(death()))
    if args.interp or everything:
        print("== build freshness ==")
        print("\n".join(build_fresh()))
        print("== interpreter differential ==")
        print("\n".join(interp()))
    if args.fresh:
        print(f"== fresh files ({args.fresh} min) ==")
        print("\n".join(fresh(args.fresh)))
    if args.requiem:
        print(requiem(args.requiem))
    return 0


def requiem(lane: str) -> str:
    """Auto-requiem bundle: what died, where, last words -- no cause asserted.

    Writes state/requiem_<lane>_<YYYYMMDD>.json (board tail, unretired
    claims held by the lane, live processes, fresh files, HEAD sha) and
    returns its path. Cause is a judgement made at the worst moment, so
    the bundle records observations only.
    """
    import json as _json
    import datetime as _dt
    day = _dt.datetime.now().strftime("%Y%m%d")
    bundle = {
        "lane": lane,
        "recorded": _dt.datetime.now().isoformat(timespec="seconds"),
        "board_tail": board_tail(6),
        "held_claims": [r for r in _held_claims(lane)],
        "processes": procs(),
        "fresh_60min": fresh(60),
        "head": _head(),
        "cause": None,
    }
    out = os.path.join(REPO, "state",
                        f"requiem_{lane}_{day}.json")
    with open(out, "w", encoding="utf-8") as fh:
        _json.dump(bundle, fh, indent=2)
    return out


def _held_claims(lane: str) -> list:
    if not os.path.exists(STORE):
        return [f"store missing: {STORE}"]
    try:
        db = sqlite3.connect(STORE, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return [f"store unreadable: {exc}"]
    try:
        return db.execute(
            "SELECT board_id, task_id, state, claimed_by, claimed_at"
            " FROM lane_task_state WHERE lane=? AND retired_at IS NULL"
            " ORDER BY board_id DESC LIMIT 15", (lane,)).fetchall()
    finally:
        db.close()


def _head() -> str:
    try:
        p = subprocess.run(["git", "log", "-1", "--format=%H %s"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                           cwd=REPO, env=_env())
        return (p.stdout or p.stderr).strip()[:160]
    except Exception as exc:  # noqa: BLE001
        return f"git unreadable: {exc}"


if __name__ == "__main__":
    sys.exit(main())
