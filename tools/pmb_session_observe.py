#!/usr/bin/env python
"""pmb_session_observe.py -- close PMB's outcome loop for a HOSTLESS agent.

WHY THIS EXISTS
---------------
PMB's Earned-Memory loop (`pmb.health.earned_memory.lesson_impact`) joins
surfaced lessons to the OUTCOME of the turn they were active in. It reads that
join from the `agent_actions` table (`pmb.core.ambient_log`), which is written
only by the PostToolUse `track-action` hook that PMB installs for
**claude-code**. This host is **Codebuff**, which PMB's capability registry
(`pmb.cli.hooks._AGENT_CAP`) does not know, so no hook is installed, no
`agent_actions` row is ever written, and `lesson_impact` permanently reports:

    n_outcome_turns: 0, signal_sufficiency: "insufficient", lessons: []

The same table is also the input to `followcheck` (adherence), so BOTH halves
of the learning loop are dead here -- and PMB's designed fallback
(`pmb ambient-watch`) cannot help, because it observes git file changes only
and therefore can never produce a test/build OUTCOME signal.

THE FIX
-------
Observe the outcomes this host DOES produce, at a trigger that is guaranteed to
fire regardless of agent behaviour: the test run and the commit. This module
appends the same `agent_actions` rows `track-action` would have, using PMB's
own writer (`pmb.core.ambient_log.insert_agent_action`), so `classify_actions`
sees a real outcome-bearing turn:

    pytest exit 0  -> tool="Bash" target="pytest" status="ok"    -> tests_passed
    pytest exit N  -> tool="Bash" target="pytest" status="1"     -> tests_failed
    git commit ok  -> tool="Bash" target="git commit" status="ok"

`insert_agent_action` also stamps `surface_ids` with the recent unconfirmed
lesson surfaces, which is exactly the edge the join needs.

It runs under the `.pmb-venv` interpreter (the one with `pmb`), e.g.:

    C:/Users/clint/.pmb-venv/Scripts/python.exe tools/pmb_session_observe.py --pytest-exit 0

Wired from `conftest.py::pytest_sessionfinish` (every test run) and from
`.githooks/pre-commit` (every commit). Both are best-effort: a failure here must
never fail a test run or block a commit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# tools/ is not a package; make `pmb` importable only if this is run from the
# venv (it will be). No third-party import happens at module import time.


def _workspace():
    """(db_path, workspace_id) for the workspace containing this project.

    `detect_workspace` forwards `cwd` straight into `Path / "..."`, so it must
    be a Path, not a str -- passing str raises
    `TypeError: unsupported operand type(s) for /: 'str' and 'str'`.
    """
    from pmb.core.workspace import detect_workspace
    ws = detect_workspace(cwd=Path(__file__).resolve().parent.parent)
    return ws.db_path, ws.id


def _record(tool: str, target: str, status: str, session_id: str | None = None):
    from pmb.core.ambient_log import insert_agent_action
    db_path, ws_id = _workspace()
    row = insert_agent_action(
        db_path, ws_id, tool=tool, target=target, status=status,
        session_id=session_id,
    )
    return row


def observe_pytest(exitstatus: int, session_id: str | None = None) -> int | None:
    """Record one test-run outcome row. Exit 0 -> ok; anything else -> failed."""
    status = "ok" if int(exitstatus) == 0 else str(int(exitstatus))
    return _record("Bash", "pytest", status, session_id)


def observe_commit(ok: bool = True, session_id: str | None = None) -> int | None:
    return _record("Bash", "git commit", "ok" if ok else "1", session_id)


def observe_command(command: str, status: str = "ok",
                    session_id: str | None = None) -> int | None:
    """Record an arbitrary significant command (used by the transcript mode)."""
    from pmb.core.ambient_log import is_significant_action
    if not is_significant_action("Bash", command=command):
        return None
    return _record("Bash", command[:400], status, session_id)


def run_followcheck(window: int = 30) -> dict:
    """Run PMB's adherence pass so surfaced lessons get a followed/ignored arm."""
    from pmb.core.engine import Engine
    from pmb.hooks.followcheck import run_followcheck as _fc
    eng = Engine()
    res = _fc(eng, window_minutes=float(window), apply=True)
    return res.to_dict() if hasattr(res, "to_dict") else {"result": str(res)}


def _parse_transcript(path: str) -> list[tuple[str, str]]:
    """Best-effort (command, status) extraction from a Codebuff transcript.

    Codebuff's transcript schema is not guaranteed, so this walks any nested
    JSON looking for objects that carry a shell command and an exit code, and
    degrades to [] on anything unexpected.
    """
    out: list[tuple[str, str]] = []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return out

    def walk(node):
        if isinstance(node, dict):
            cmd = (node.get("command") or node.get("cmd")
                   or node.get("shell_command"))
            if isinstance(cmd, str) and cmd.strip():
                code = node.get("exit_code", node.get("exitCode"))
                if code is None and isinstance(node.get("result"), dict):
                    code = node["result"].get("exit_code")
                status = "ok" if code in (0, None) else str(code)
                out.append((cmd.strip(), status))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pytest-exit", type=int, default=None)
    ap.add_argument("--commit-ok", action="store_true")
    ap.add_argument("--commit-fail", action="store_true")
    ap.add_argument("--transcript", default=None)
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--followcheck", action="store_true")
    ap.add_argument("--followcheck-window", type=int, default=30)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    wrote = 0
    try:
        if args.pytest_exit is not None:
            if observe_pytest(args.pytest_exit, args.session_id) is not None:
                wrote += 1
        if args.commit_ok:
            if observe_commit(True, args.session_id) is not None:
                wrote += 1
        if args.commit_fail:
            if observe_commit(False, args.session_id) is not None:
                wrote += 1
        if args.transcript:
            for cmd, status in _parse_transcript(args.transcript):
                if observe_command(cmd, status, args.session_id) is not None:
                    wrote += 1
    except Exception as exc:  # noqa: BLE001 -- never break the caller
        if not args.quiet:
            sys.stderr.write("pmb_session_observe: record failed: %r\n" % (exc,))
        return 0

    fc = None
    if args.followcheck:
        try:
            fc = run_followcheck(args.followcheck_window)
        except Exception as exc:  # noqa: BLE001
            fc = {"error": repr(exc)}

    if not args.quiet:
        print(json.dumps({"recorded": wrote, "followcheck": fc}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
