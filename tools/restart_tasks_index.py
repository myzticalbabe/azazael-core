#!/usr/bin/env python
"""Build the EV-id -> board-row index for a restart task list.

The problem this solves was measured, not assumed: `RESTART_TASKS_2026-10-31.md` carried
15 EV entries and 23 board row numbers, and the task text appeared in both places. Two
copies of a task list drift, and a reader cannot tell which copy is live.

So: the board carries the bodies (`memorybus_queue.content.detail`), this file carries one
line per EV with the rows that belong to it and their live status, and the markdown list
keeps only what the board cannot hold -- the measurement narrative, the refutations, the
"do not trim on this" corrections.

Read-only against the store: opened with `mode=ro`. A missing row is reported as DANGLING
rather than skipped, because an index with a dangling id is worse than the duplication it
replaces -- that was the row's own warning and it is why the check exists.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

STORE = Path.home() / ".azazael" / "telemetry_store.db"
DEFAULT_SRC = Path(r"C:\Azazael\azazael\RESTART_TASKS_2026-10-31.md")
DEFAULT_OUT = Path(r"C:\Azazael\azazael\RESTART_TASKS_INDEX.md")

EV_HEAD = re.compile(r"^#{2,3}\s+(EV-\d+[a-z]?)\s*[—-]\s*(.+?)\s*$")
ROW_ID = re.compile(r"\b(4[0-9]{4})\b")


def read_rows(ids: list[int]) -> dict[int, dict]:
    if not STORE.exists():
        sys.exit(f"board store not found: {STORE}")
    con = sqlite3.connect(f"file:{STORE.as_posix()}?mode=ro", uri=True)
    try:
        q = ",".join("?" * len(ids))
        out: dict[int, dict] = {}
        # The lifecycle lives in `lane_task_state`; the row's own JSON `status`
        # field is frozen at post time. Measured 2026-11-01: this index reported
        # 29 rows as `pending` while kilo had retired eleven of them the same
        # afternoon -- a stale readout of exactly the kind this tree keeps
        # finding. Join the state table and let it win; keep the JSON value as
        # `posted_status` so a disagreement is visible instead of hidden.
        for rid, topic, content, state, retired_at in con.execute(
            f"SELECT q.id, q.topic, q.content, s.state, s.retired_at "
            f"FROM memorybus_queue q LEFT JOIN lane_task_state s ON s.board_id = q.id "
            f"WHERE q.id IN ({q})", ids
        ):
            try:
                d = json.loads(content)
            except Exception:
                d = {}
            out[int(rid)] = {
                "topic": topic,
                "task_id": d.get("task_id") or "",
                "status": state or d.get("status") or "(no state row)",
                "posted_status": d.get("status") or "",
                "retired_at": retired_at or "",
                "detail": (d.get("detail") or "").replace("\n", " ").strip(),
            }
        return out
    finally:
        con.close()


def rows_by_ev_prefix() -> dict[str, list[int]]:
    """Attach rows whose task_id names an EV, e.g. `EV-008-AP5-HISTORICAL` -> EV-008.

    Needed because a row can be cited in the list as a *range* (`46818–46825`) in a
    preamble section rather than inside the EV section it belongs to, so a body-only scan
    reported EV-008 as having no board row while 46825 is exactly that task. The task_id
    is the stronger signal and it is written by the poster.
    """
    if not STORE.exists():
        return {}
    con = sqlite3.connect(f"file:{STORE.as_posix()}?mode=ro", uri=True)
    out: dict[str, list[int]] = {}
    try:
        for rid, content in con.execute(
            "SELECT id, content FROM memorybus_queue WHERE content LIKE '%EV-0%' "
            "ORDER BY id DESC LIMIT 400"
        ):
            try:
                tid = str(json.loads(content).get("task_id") or "")
            except Exception as exc:
                note_failure("tools/restart_tasks_index.py", exc)
                continue
            m = re.match(r"(EV-\d+[a-z]?)\b", tid)
            if m:
                out.setdefault(m.group(1), []).append(int(rid))
    finally:
        con.close()
    return out


def parse(src: Path) -> list[dict]:
    lines = src.read_text(encoding="utf-8").splitlines()
    heads: list[tuple[int, str, str]] = []
    for i, ln in enumerate(lines):
        m = EV_HEAD.match(ln)
        if m:
            heads.append((i, m.group(1), m.group(2)))
    evs: list[dict] = []
    for idx, (start, ev, title) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        body = "\n".join(lines[start:end])
        ids = sorted({int(x) for x in ROW_ID.findall(body)})
        evs.append({"ev": ev, "title": title, "line": start + 1,
                    "end_line": end, "rows": ids, "body": body})
    return evs


def main() -> int:
    src = DEFAULT_SRC
    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
    if not src.exists():
        sys.exit(f"no such source: {src}")

    evs = parse(src)
    by_prefix = rows_by_ev_prefix()
    for e in evs:
        extra = [i for i in by_prefix.get(e["ev"], []) if i not in e["rows"]]
        if extra:
            e["rows"] = sorted(set(e["rows"]) | set(extra))
            e["from_task_id"] = extra
    all_ids = sorted({i for e in evs for i in e["rows"]})
    rows = read_rows(all_ids) if all_ids else {}
    dangling = [i for i in all_ids if i not in rows]

    out: list[str] = []
    add = out.append
    add("# RESTART_TASKS index — EV id → board row\n")
    add(f"Generated from `{src.name}` ({len(evs)} EV entries, {len(all_ids)} distinct board "
        f"row numbers) against `{STORE.name}`. Regenerate with "
        "`python tools/restart_tasks_index.py`; do not hand-edit, the statuses move.\n")
    add("**The board owns the task text.** Each row below points at "
        "`memorybus_queue.content.detail`, which is the authoritative body. The EV "
        "sections in the source file keep only what a queue row cannot carry: the "
        "measurement, the refutation, and the correction.\n")
    if dangling:
        add(f"> **{len(dangling)} DANGLING row id(s): {dangling}** — cited in the list but "
            "absent from the store. Fix the citation before trusting that entry.\n")
    dupes = {}
    for rid, r in rows.items():
        if r["task_id"]:
            dupes.setdefault(r["task_id"], []).append(rid)
    dup_task_ids = {k: v for k, v in dupes.items() if len(v) > 1}
    if dup_task_ids:
        add(f"> **Duplicate task_ids across rows:** "
            + "; ".join(f"`{k}` → {v}" for k, v in sorted(dup_task_ids.items()))
            + ". Same text filed twice; retire one, do not work both.\n")

    add("| EV | subject | rows | lane(s) | status(es) |")
    add("|---|---|---|---|---|")
    _ = ""  # statuses below come from lane_task_state, not the posted JSON
    for e in evs:
        if e["rows"]:
            lanes = ", ".join(sorted({rows[i]["topic"].split(".")[1] for i in e["rows"]
                                      if i in rows}))
            stats = ", ".join(sorted({rows[i]["status"] or "?" for i in e["rows"] if i in rows}))
            cells = ", ".join(f"[{i}]({rows[i]['task_id'] or i})" for i in e["rows"])
        else:
            lanes, stats, cells = "—", "no rows", "no board row (narrative only)"
        add(f"| **{e['ev']}** | {e['title'][:90]} | {cells} | {lanes} | {stats} |")
    add("")

    open_rows = [(i, r) for i, r in sorted(rows.items()) if r["status"] not in
                 ("done", "blocked", "archived")]
    add(f"## Live rows ({len(open_rows)} of {len(rows)})\n")
    add("Rows whose status is not `done`/`blocked`/`archived`. These are the ones the source "
        "list is still describing as outstanding, and they are the reason its prose still "
        "reads as a to-do list.\n")
    add("| row | lane | task_id | body starts |")
    add("|---|---|---|---|")
    for i, r in open_rows:
        add(f"| {i} | {r['topic'].split('.')[1]} | `{r['task_id'] or '—'}` | "
            f"{r['detail'][:110]} |")
    add("")

    add("## What is deliberately NOT in this index\n")
    add("- The EV narrative (what was measured, what was refuted, what the numbers were). "
        "It stays in the source file, because a queue row is a work order, not a record of "
        "a measurement.")
    add(f"- Retired rows: {len(rows) - len(open_rows)} of the {len(rows)} cited ids are "
        "retired. A row retired later changes its status column on the next regeneration; "
        "nothing else needs editing.")

    DEFAULT_OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {DEFAULT_OUT}")
    print(f"evs={len(evs)} ids={len(all_ids)} found={len(rows)} dangling={dangling} "
          f"open={len(open_rows)} dup_task_ids={sorted(dup_task_ids)}")
    return 0



try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

if __name__ == "__main__":
    raise SystemExit(main())
