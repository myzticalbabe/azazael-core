#!/usr/bin/env python
# coding: utf-8
"""Session end: improve pass + record prompt (one command).

Usage: python tools/session_end.py
Prints the azazael-improve checklist with this session's evidence sources
and the exact record command. The agent executes the pass; this script
makes it one step instead of a remembered discipline.
Exit 0 always.
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _pams_record(content: str, role: str, session_id: str,
                 user_id: str, model_id: str) -> None:
    """Phase-3 memory-unity feeder (#380): record one session message into
    PAMS conversations.db via the same ConversationDatabase class the MCP
    server uses (schema-consistent, no server restart). Advisory: prints the
    store result, never raises — session end must exit 0."""
    try:
        import asyncio as _asyncio
        import os as _os
        from persistent_ai_memory.ai_memory_core import (
            ConversationDatabase as _ConversationDatabase,
        )
        data_dir = _os.environ.get("AI_MEMORY_DATA_DIR") or str(
            Path.home() / ".pams" / "memory_data")
        db_path = str(Path(data_dir) / "conversations.db")
        db = _ConversationDatabase(db_path=db_path)
        res = _asyncio.run(db.store_message(
            content=content, role=role, session_id=session_id or None,
            user_id=user_id, model_id=model_id, source="session_end"))
        print(f"   pams conversations record: {res.get('status')}"
              f" ({res.get('message_id') or res.get('error')})")
    except Exception as exc:
        print(f"   pams conversations record SKIPPED ({exc})")


PY316T = Path("C:/Azazael/.venvs/py316t/Scripts/python.exe")


def _pams_close_delegated(record: str, role: str, session_id: str,
                          user_id: str, model_id: str) -> bool:
    """Delegate the close to tools/pams_capture.py on the pinned 3.15t
    interpreter (the interpreter with persistent_ai_memory). True when the
    delegate ran to completion - INCLUDING a named skip - so the caller
    never double-records. Prints one record line naming the outcome;
    never raises."""
    cli = HERE / "pams_capture.py"
    if not (PY315T.exists() and cli.exists()):
        return False
    argv = [str(PY315T), str(cli), "close", record, "--record-role", role]
    if session_id:
        argv += ["--session-id", session_id]
    if user_id:
        argv += ["--user-id", user_id]
    if model_id:
        argv += ["--model-id", model_id]
    try:
        r = subprocess.run(argv, timeout=60, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           cwd=str(HERE.parent))
    except Exception as exc:  # noqa: BLE001 - report, then legacy fallback
        print("   pams conversations record: delegate call failed (%s: %s)"
              % (type(exc).__name__, exc))
        return False
    out = (r.stdout or "").strip().replace("\n", " ")
    out = out.replace("pams capture close: ", "")
    print("   pams conversations record: %s"
          % (out or ("empty response (rc=%s)" % r.returncode)))
    return True


def _zerom_review(root: Path) -> None:
    """RULE 0Z-M defect-class capture review (advisory; the hard block is
    pre-commit: staged_scanner_gate + anti_pattern_gate --check). Names the
    exact worktree files needing a coverage decision so the agent cannot
    claim 'nothing to capture'."""
    print("5. RULE 0Z-M capture review (every fixed class -> gate rule or ledger rejection):")
    ledger = root / "state" / "heuristic_ledger.jsonl"
    try:
        lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
        import json as _json
        bad = sum(1 for ln in lines if not _json.loads(ln))
        print(f"   ledger: {len(lines)} entries, {bad} invalid lines ({ledger.name})")
    except FileNotFoundError:
        print("   ledger: MISSING state/heuristic_ledger.jsonl -- create it before finishing")
    except Exception as exc:
        print(f"   ledger: UNREADABLE ({exc}) -- fix before finishing")
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "--", "src", "tools", "scripts", "tests"],
            timeout=30, capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=str(root))
        touched = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        if touched:
            print(f"   worktree code files ({len(touched)}) needing a coverage decision:")
            for ln in touched[:20]:
                print(f"     {ln}")
            if len(touched) > 20:
                print(f"     ... +{len(touched) - 20} more")
            print("   For each fix above: gate rule added + baseline, or ledger rejection. "
                  "Undecided = INCOMPLETE per 0Z-M.")
        else:
            print("   worktree clean: nothing to capture.")
    except Exception as exc:
        print(f"   worktree scan failed ({exc})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", default="",
                    help="Session summary text to store in PAMS conversations")
    ap.add_argument("--record-role", default="assistant")
    ap.add_argument("--session-id", default="")
    ap.add_argument("--user-id", default="")
    ap.add_argument("--model-id", default="")
    args = ap.parse_args(argv)
    print("=== SESSION END: improve pass ===")
    if args.record:
        delegate_ran = _pams_close_delegated(
            args.record, args.record_role, args.session_id,
            args.user_id, args.model_id)
        if not delegate_ran:
            if not (args.user_id and args.model_id):
                print("   pams conversations record SKIPPED: --user-id and"
                      " --model-id are required (server refuses defaults)")
            else:
                _pams_record(args.record, args.record_role, args.session_id,
                             args.user_id, args.model_id)
    print("1. Evidence: state/converge_315.json, PMB outbox, session tool outputs.")
    print("2. Write [IMP-N] items (Observation file:line, Suggestion, Grounded).")
    print("3. Fix trivial now; file the rest as a PMB goal.")
    print("4. Record outcomes now (facts, lessons, goal updates):")
    print()
    print("   C:/Users/clint/.pmb-venv/Scripts/python.exe tools/pmb_capture.py \\")
    print('       --lesson "..." --fact "..." --project azazael')
    print()
    _zerom_review(HERE.parent)
    print()
    try:
        r = subprocess.run(
            [sys.executable, "-c",
             "import json; m=json.load(open('state/converge_315.json')); "
             "print('converge:', m.get('iteration'), 'live:', m.get('total_live'))"],
            timeout=30, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(HERE.parent))
        print((r.stdout or "").strip() or "(no converge manifest yet)")
    except Exception:
        print("(no converge manifest yet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
