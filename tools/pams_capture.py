#!/usr/bin/env python
# coding: utf-8
"""pams_capture.py - session-boundary capture into PAMS conversations.db.

WHY (the defect being fixed): the PAMS conversation pipeline has been dead
since 2026-05-27 - supervisor `_pams_honest` reports "pams conversation
capture dead (0 messages)" while memories and tool_calls write hourly. The
May-era writer (conversations row user_id='system', model_id='Codebuff')
was the Codebuff client itself; when other clients replaced it, nothing
drove `ConversationDatabase.store_message` any more. A stdio MCP server
cannot observe chat, so the durable wiring point is the MANDATED
session-boundary protocol scripts (AGENTS.md MEMORY PROTOCOL), which reach
this module as one subprocess call on the pinned 3.15t interpreter (the
interpreter with persistent_ai_memory installed; the delegating scripts
still run under any interpreter).

Contract:
  open TASK      - stores a SESSION START message (role=user) under a fresh
                   per-agent session id and writes a linkage file so the
                   matching `close` attaches the end summary to the same
                   session.
  close SUMMARY  - stores the summary (role=assistant, source=session_end)
                   under the linkage session, then removes the file.
  status         - prints the linkage file contents (or that there is none).

Honesty rules:
  * user_id / model_id are required by the server ("Do not use defaults").
    --user-id defaults to the real OS user; --model-id defaults to
    $AZAZAEL_MODEL_ID. Without a model id the call SKIPS with a named
    reason and exits 0 - capture is advisory and must never block.
  * AZAZAEL_PAMS_CAPTURE=0 or "off" disables capture; the skip is named,
    never silent.
  * Writes go through ConversationDatabase (the MCP server's own class), so
    the rows are legible to the running server without a restart.
  * The linkage file lives under ~/.pams/state (outside the repository)
    with a per-agent name ($AZAZAEL_AGENT, else "default"), so parallel
    agents on one checkout cannot clobber each other.
  * sqlite3 here runs the default (delete) journal with busy_timeout=0, so
    a live MCP server connection can hold the lock. A bounded retry on
    OperationalError("database is locked") is recorded as a result;
    exhaustion is an explicit error, never a silent swallow.
"""
import argparse
import asyncio
import datetime
import getpass
import json
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path

_RECONFIGURE_ERROR = ""
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError) as exc:
        # cp1252/odd consoles; callers decode subprocess output with
        # errors="replace", so an unavailable reconfigure is recorded state.
        _RECONFIGURE_ERROR = "%s: %s" % (type(exc).__name__, exc)

LINKAGE_DIR = Path.home() / ".pams" / "state"
DEFAULT_DB = Path.home() / ".pams" / "memory_data" / "conversations.db"
_BUSY_ATTEMPTS = 8
_IDENTITY_ERROR = ""


def _capture_enabled() -> "tuple[bool, str]":
    """AZAZAEL_PAMS_CAPTURE=0/off/false disables capture, with a named skip."""
    flag = (os.environ.get("AZAZAEL_PAMS_CAPTURE") or "").strip().lower()
    if flag in ("0", "off", "false"):
        return False, "AZAZAEL_PAMS_CAPTURE=%s" % flag
    return True, ""


def _default_user() -> str:
    """The real OS user, or empty if it cannot be derived (then the caller skips)."""
    global _IDENTITY_ERROR
    try:
        return getpass.getuser() or ""
    except Exception as exc:  # noqa: BLE001 - identity lookup failure is named state
        _IDENTITY_ERROR = "%s: %s" % (type(exc).__name__, exc)
        return ""


def _resolve_ids(_last: "list[tuple]", user_id: str, model_id: str) -> "tuple[str, str] | None":
    """Identity pairs in the order explicit > linkage > environment.

    Without a model id, records the named skip reason into _last and returns
    None: the server refuses defaults, so an id-less call would be exactly
    the 'MISSING REQUIRED PARAMETERS' silent failure the fix forbids.
    """
    user = user_id or _default_user()
    model = model_id or os.environ.get("AZAZAEL_MODEL_ID") or ""
    if not user:
        _last.append(("skip", "no user id derivable (%s; pass --user-id)"
                      % (_IDENTITY_ERROR or "identity empty")))
        return None
    if not model:
        _last.append(("skip", "no model id: pass --model-id or set $AZAZAEL_MODEL_ID"
                              " (server refuses defaults)"))
        return None
    return user, model


def _store(db_path: str, **kwargs) -> "dict":
    """Store through ConversationDatabase with a bounded lock retry.

    Per-operation connections here have busy_timeout=0, so contention with
    the live MCP server raises instead of waiting; a lock error is retried
    backoff-wise and exhaustion surfaces as an explicit error. Non-lock
    errors propagate - a schema/SQL defect must not be mistaken for
    contention and must never be retried silently.
    """
    delay = 0.4
    last_lock = ""
    for _attempt in range(_BUSY_ATTEMPTS):
        try:
            from persistent_ai_memory.ai_memory_core import (
                ConversationDatabase as _ConversationDatabase,
            )
            db = _ConversationDatabase(db_path=db_path)
            return asyncio.run(db.store_message(**kwargs))
        except sqlite3.OperationalError as exc:
            if "lock" not in str(exc).lower():
                raise
            last_lock = str(exc)
            time.sleep(delay)
            delay = min(delay * 2, 3.0)
    return {"status": "error",
            "error": "conversations.db stayed locked through %d attempts: %s"
                     % (_BUSY_ATTEMPTS, last_lock)}


def _linkage_path(agent: str, state_dir: "str | None") -> Path:
    base = Path(state_dir) if state_dir else LINKAGE_DIR
    return base / ("pams_session_%s.json" % agent)


def _read_linkage(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - corrupt linkage is named state
        # Carried forward: id/session fills fall through to absent, while
        # the failure stays visible to anyone inspecting the result chain.
        return {"corrupt_linkage": "%s: %s" % (type(exc).__name__, exc)}


def open_capture(task: str, agent: str = None, user_id: str = None,
                 model_id: str = None, db_path: str = None,
                 state_dir: str = None) -> dict:
    """Store the SESSION START message and write the linkage file.

    Returns {"status": "stored"|"skipped"|"disabled"|"error", ...}; the
    skipped/disabled statuses carry their named reasons. Never raises.
    """
    enabled, why = _capture_enabled()
    if not enabled:
        return {"status": "disabled", "reason": why}
    reasons: list = []
    ids = _resolve_ids(reasons, user_id, model_id)
    if ids is None:
        return {"status": "skipped", "reason": reasons[-1][1]}
    user_id, model_id = ids
    agent = agent or os.environ.get("AZAZAEL_AGENT") or "default"
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = "%s_%s_%s" % (agent, stamp, secrets.token_hex(2))
    try:
        res = _store(db_path or str(DEFAULT_DB),
                     content="SESSION START [%s]: %s" % (agent, task),
                     role="user",
                     session_id=session_id,
                     metadata={"application": "azazael",
                               "type": "session_start",
                               "agent": agent},
                     user_id=user_id, model_id=model_id,
                     source="session_start")
    except Exception as exc:  # noqa: BLE001 - report, never block the protocol
        return {"status": "error", "reason": "%s: %s" % (type(exc).__name__, exc)}
    if res.get("status") == "error" or not res.get("message_id"):
        return {"status": "error", "reason": res.get("error") or res}
    link_path = _linkage_path(agent, state_dir)
    try:
        link_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.write_text(json.dumps(
            {"session_id": session_id, "user_id": user_id,
             "model_id": model_id, "agent": agent, "task": task,
             "started": datetime.datetime.now().isoformat()},
            ensure_ascii=True), encoding="utf-8")
        linkage = str(link_path)
    except OSError as exc:
        linkage = "WRITE FAILED (%s: %s)" % (type(exc).__name__, exc)
    return {"status": "stored", "message_id": res["message_id"],
            "conversation_id": res.get("conversation_id"),
            "session_id": session_id, "agent": agent, "linkage": linkage}


def close_capture(summary: str, agent: str = None, user_id: str = None,
                  model_id: str = None, session_id: str = None,
                  record_role: str = "assistant", db_path: str = None,
                  state_dir: str = None) -> dict:
    """Store the summary under the linkage session, then remove the file.

    An explicit --session-id overrides the linkage session (the linkage file
    still fills any missing user/model id, but is only unlinked when the
    stored session matches it). A session without a linkage file and without
    --session-id is skipped with a named reason - guessing a session id would
    fragment the very state capture exists to unify.
    """
    enabled, why = _capture_enabled()
    if not enabled:
        return {"status": "disabled", "reason": why}
    agent = agent or os.environ.get("AZAZAEL_AGENT") or "default"
    link_path = _linkage_path(agent, state_dir)
    linkage = _read_linkage(link_path)
    reasons: list = []
    ids = _resolve_ids(reasons, user_id or linkage.get("user_id"),
                       model_id or linkage.get("model_id"))
    if ids is None:
        return {"status": "skipped", "reason": reasons[-1][1]}
    user_id, model_id = ids
    session_id = session_id or linkage.get("session_id")
    if not session_id:
        return {"status": "skipped",
                "reason": "no open linkage session for agent %s; pass --session-id"
                          % agent}
    try:
        res = _store(db_path or str(DEFAULT_DB),
                     content=summary, role=record_role,
                     session_id=session_id,
                     metadata={"application": "azazael",
                               "type": "session_end", "agent": agent},
                     user_id=user_id, model_id=model_id,
                     source="session_end")
    except Exception as exc:  # noqa: BLE001 - report, never block session end
        return {"status": "error", "reason": "%s: %s" % (type(exc).__name__, exc)}
    if res.get("status") == "error" or not res.get("message_id"):
        return {"status": "error", "reason": res.get("error") or res}
    warning = ""
    if session_id == linkage.get("session_id") and link_path.exists():
        try:
            link_path.unlink()
        except OSError as exc:
            warning = "linkage file not removed (%s: %s)" % (type(exc).__name__, exc)
    return {"status": "stored", "message_id": res["message_id"],
            "session_id": session_id, "agent": agent, "warning": warning}


def status_capture(agent: str = None, state_dir: str = None) -> dict:
    agent = agent or os.environ.get("AZAZAEL_AGENT") or "default"
    link_path = _linkage_path(agent, state_dir)
    if not link_path.exists():
        return {"status": "none", "agent": agent, "path": str(link_path)}
    return {"status": "open", "agent": agent,
            "linkage": _read_linkage(link_path)}


def _result_line(prefix: str, res: dict) -> str:
    """One stdout line per call: status plus every nameable reason."""
    if res["status"] in ("stored",):
        parts = (prefix + ">stored message_id=%s session_id=%s agent=%s"
                 % (res.get("message_id"), res.get("session_id"), res.get("agent", "")))
        extra = " linkage=%s" % res["linkage"] if res.get("linkage") else ""
        warn = " warning=%s" % res["warning"] if res.get("warning") else ""
        return parts + extra + warn
    detail = res.get("reason", "")
    if not detail and "linkage" in res:
        detail = json.dumps(res.get("linkage") or {}, ensure_ascii=True)
    return "%s>%s%s" % (prefix, res["status"], ((" (%s)" % detail) if detail else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="pams_capture CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_open = sub.add_parser("open", help="store the session start message")
    p_open.add_argument("task", nargs="+")
    p_open.add_argument("--agent"); p_open.add_argument("--user-id")
    p_open.add_argument("--model-id"); p_open.add_argument("--db-path")
    p_close = sub.add_parser("close", help="store the session end summary")
    p_close.add_argument("summary", nargs="+")
    p_close.add_argument("--agent"); p_close.add_argument("--user-id")
    p_close.add_argument("--model-id"); p_close.add_argument("--session-id")
    p_close.add_argument("--record-role", default="assistant")
    p_close.add_argument("--db-path")
    p_stat = sub.add_parser("status", help="show the linkage file")
    p_stat.add_argument("--agent")
    args = ap.parse_args(argv)
    if args.cmd == "open":
        res = open_capture(" ".join(args.task), agent=args.agent,
                           user_id=args.user_id, model_id=args.model_id,
                           db_path=args.db_path)
        line = _result_line("pams capture open", res)
    elif args.cmd == "close":
        res = close_capture(" ".join(args.summary), agent=args.agent,
                            user_id=args.user_id, model_id=args.model_id,
                            session_id=args.session_id,
                            record_role=args.record_role,
                            db_path=args.db_path)
        line = _result_line("pams capture close", res)
    else:
        res = status_capture(agent=args.agent)
        line = _result_line("pams capture status", res)
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
