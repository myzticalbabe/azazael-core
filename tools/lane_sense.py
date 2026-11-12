"""lane_sense.py — cross-lane visibility: sense every lane without claiming.

Why this exists (2026-11-09): three silent states were found by hand-reading
the store — kilo dark since Nov 7 with a repair brief sitting unread in its
queue, and an opencode row delivered-but-never-retired floating on an expired
lease. No lane could see this about another lane without raw sqlite. This tool
is the sense layer: read-only, no claims, no writes, no protocol changes.

Usage:
    python tools/lane_sense.py            # all lanes, human text
    python tools/lane_sense.py --json     # machine-readable
    python tools/lane_sense.py --lane kilo

Reads (all non-destructive):
    task_stats(lane)                      # counts incl. claimed_expired/churned
    poll_tasks(lane, include_claimed=True)  # open rows + notes, no lease taken
    poll_status(lane, limit=3)            # pulse = newest status timestamp

Signals:
    pulse_age   — hours since lane's last status row (dark-lane detector)
    stale-open  — a status row says done/complete/retired for a task_id whose
                  state row is still open (delivered but never retired)
    churned     — attempts>=3 and still open (board's own defect count)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from CORE.LaneMessageBoard import LaneMessageBoard  # noqa: E402

LANES = ("freebuff", "kilo", "opencode")
DONE_WORDS = re.compile(r"\b(done|complete|completed|retired|landed|finished)\b", re.I)


def _parse_ts(ts: str):
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        print(f"[lane_sense] unparseable timestamp: {str(ts)[:60]}",
              file=sys.stderr)
        return None


def sense_lane(board: LaneMessageBoard, lane: str) -> dict:
    stats = board.task_stats(lane)
    try:
        tasks = board.poll_tasks(lane, limit=50, include_claimed=True)
    except Exception as exc:  # sense must never die on one lane
        tasks = []
        stats["sense_error"] = str(exc)[:120]
    # limit=5 lies: poll_status sorts oldest-first, so 5 rows = 5 ancient
    # rows. Pull deep, take the max timestamp. (Blindness found 2026-11-09:
    # kilo had worked that morning; a 5-row window still said 44h dark.)
    try:
        statuses = board.poll_status(lane, limit=200)
    except Exception as exc:
        statuses = []
        stats["sense_error"] = f"poll_status: {exc}"[:120]
    # Ground truth: the state table. Status rows are voluntary; transitions
    # are not — every claim/done/block lands here with claimed/retired stamps.
    try:
        transitions = board.recent_transitions(lane, limit=20)
    except Exception as exc:
        transitions = []
        stats["sense_error"] = f"transitions: {exc}"[:120]

    now = datetime.now(timezone.utc)
    # NOTE (2026-11-09): poll_status sorts oldest-first despite its docstring
    # saying newest-first, so pulse is the MAX timestamp, never the first row.
    activity_ts = None
    for tr in transitions:
        if not isinstance(tr, dict):
            continue
        for key in ("retired_at", "claimed_at"):
            if tr.get(key):
                ts = _parse_ts(str(tr.get(key)))
                if ts and (activity_ts is None or ts > activity_ts):
                    activity_ts = ts
    done_today, blocked_today = [], []
    for tr in transitions:
        if not isinstance(tr, dict):
            continue
        stamp = str(tr.get("retired_at") or tr.get("claimed_at") or "")
        if stamp[:10] != now.date().isoformat():
            continue
        entry = f"#{tr.get('board_id')} {tr.get('task_id')} :: {str(tr.get('note') or '')[:110]}"
        if tr.get("state") == "done":
            done_today.append(entry)
        elif tr.get("state") == "blocked":
            blocked_today.append(entry)

    pulse_ts = activity_ts
    for s in statuses:
        if not isinstance(s, dict):
            continue
        for key in ("timestamp", "created_at", "posted_at"):
            if s.get(key):
                ts = _parse_ts(str(s.get(key)))
                if ts and (pulse_ts is None or ts > pulse_ts):
                    pulse_ts = ts
    pulse_age_h = round((now - pulse_ts).total_seconds() / 3600, 1) if pulse_ts else None

    # newest inbound task post vs pulse: a push newer than the lane's last
    # status means the lane has not looked at its queue since the push landed.
    inbound_ts = None
    for t in tasks:
        d0 = t.to_dict() if hasattr(t, "to_dict") else dict(t)
        ts0 = _parse_ts(str(d0.get("created_at", "")))
        if ts0 and (inbound_ts is None or ts0 > inbound_ts):
            inbound_ts = ts0

    open_rows = []
    for t in tasks:
        d = t.to_dict() if hasattr(t, "to_dict") else dict(t)
        open_rows.append(
            {
                "task_id": d.get("task_id"),
                "attempts": d.get("attempts", 0),
                "posted": d.get("created_at"),
                "detail": str(d.get("detail", ""))[:160],
            }
        )

    # delivered-but-unretired: a status row claims done-ness naming an open task
    status_text = " ".join(
        str(s.get("detail", "")) for s in statuses if isinstance(s, dict)
    )
    stale_open = []
    if DONE_WORDS.search(status_text):
        for row in open_rows:
            tid = str(row.get("task_id") or "")
            if tid and tid in status_text:
                stale_open.append(tid)

    return {
        "lane": lane,
        "pulse_age_h": pulse_age_h,
        "done_today": done_today,
        "blocked_today": blocked_today,
        "status_rows_seen": len(statuses),
        "available": stats.get("available", 0),
        "claimed_live": stats.get("claimed_live", 0),
        "claimed_expired": stats.get("claimed_expired", 0),
        "blocked": stats.get("blocked", 0),
        "churned": stats.get("churned", 0),
        "open_rows": open_rows,
        "stale_open": stale_open,
        "newest_inbound": inbound_ts.isoformat() if inbound_ts else None,
        "unacked": bool(pulse_ts and inbound_ts and inbound_ts > pulse_ts),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    lanes = (args.lane,) if args.lane else LANES
    board = LaneMessageBoard(lane="freebuff")
    report = {lane: sense_lane(board, lane) for lane in lanes}

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    for lane, r in report.items():
        pulse = f"{r['pulse_age_h']}h ago" if r["pulse_age_h"] is not None else "NEVER"
        print(f"--- {lane}: pulse {pulse} | avail {r['available']} "
              f"| live {r['claimed_live']} | expired {r['claimed_expired']} "
              f"| blocked {r['blocked']} | churned {r['churned']}")
        for entry in r["done_today"]:
            print(f"    DONE-TODAY: {entry}")
        for entry in r["blocked_today"]:
            print(f"    BLOCKED-TODAY: {entry}")
        for row in r["open_rows"]:
            print(f"    open {row['task_id']} "
                  f"(attempts={row['attempts']}) :: {row['detail'][:100]}")
        for tid in r["stale_open"]:
            print(f"    STALE-OPEN: status claims done but row open: {tid}")
        if r["pulse_age_h"] is not None and r["pulse_age_h"] > 24 and (
            r["available"] or r["claimed_expired"]
        ):
            print(f"    DARK-LANE: no pulse in {r['pulse_age_h']}h with work waiting")
        if r["unacked"]:
            print(f"    UNACKED: newest inbound {r['newest_inbound']} is newer than "
                  f"lane pulse — queue has unread pushes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
