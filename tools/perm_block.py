"""perm_block.py -- the permission ledger: make invisible approval dialogs visible.

A lane died 2026-11-07 behind an approval prompt no other lane could see.
The fix is a convention with a helper: any lane hitting a permission block
posts the EXACT command text to the board, where any other lane (or the user)
can adjudicate it -- safe to approve, or not, with the reason on record.

    python tools/perm_block.py post kilo "bun run verify --filter 3.16t"
    python tools/perm_block.py list              # unacked permission blocks

Rows use action=perm-block on lane.<lane>.tasks. `list` shows blocks with no
claim row yet (unacked). Read-only except `post`.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys

BOARD = r"C:\Users\clint\_board.py"
STORE = r"C:\Users\clint\.azazael\telemetry_store.db"


def _env() -> dict:
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    return env


def post(lane: str, command: str) -> int:
    # _board post signature: post <lane> <action> <detail> [--from X].
    detail = ("PERM-BLOCK: this exact command waits on approval in the "
              f"{lane} client. Adjudicate: approve it, run it yourself, "
              f"or refuse with reason. Command: {command}")
    p = subprocess.run([sys.executable, BOARD, "post", lane, "perm-block",
                        detail, "--from", "freebuff"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, env=_env())
    print((p.stdout or p.stderr).strip()[:200])
    return 0


def list_blocks() -> int:
    if not os.path.exists(STORE):
        print(f"store missing: {STORE}")
        return 2
    db = sqlite3.connect(STORE, timeout=30)
    try:
        claimed = {r[0] for r in db.execute(
            "SELECT board_id FROM lane_task_state")}
        rows = db.execute(
            "SELECT id, topic, substr(content,1,160), timestamp"
            " FROM memorybus_queue WHERE topic LIKE '%perm-block%'"
            " ORDER BY id DESC LIMIT 10").fetchall()
    finally:
        db.close()
    shown = 0
    for bid, topic, content, ts in rows:
        if bid in claimed:
            continue
        print(f"{bid} UNACKED {topic} @ {ts}\n    {content}")
        shown += 1
    if not shown:
        print("no unacked permission blocks")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Permission-ledger helper.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("post", help="post an approval-blocked command")
    p.add_argument("lane")
    p.add_argument("command")
    sub.add_parser("list", help="show unacked permission blocks")
    args = ap.parse_args()
    if args.cmd == "post":
        return post(args.lane, args.command)
    return list_blocks()


if __name__ == "__main__":
    sys.exit(main())
