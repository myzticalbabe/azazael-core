#!/usr/bin/env python
"""pmb_e2e_check.py -- end-to-end verification of the PMB integration.

Exercises every surface the Azazael workflow depends on and reports PASS/FAIL
per capability, so a regression is visible rather than inferred. Run with the
`.pmb-venv` interpreter:

    C:/Users/clint/.pmb-venv/Scripts/python.exe tools/pmb_e2e_check.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pmb_client  # noqa: E402
import sys as _sys

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
    RESULTS.append((name, ok, detail))
    print("%s  %-34s %s" % ("PASS" if ok else "FAIL", name, detail))


def main() -> int:
    print("=" * 78)
    print("PMB END-TO-END CHECK")
    print("=" * 78)

    # 1. transport -----------------------------------------------------------
    check("endpoint reachable", lambda: (
        (lambda r: (True, "%d keys" % len(r)) if isinstance(r, dict) else (False, str(r)[:60]))(
            pmb_client.call_tool("project_overview", {"name": "azazael"}))))

    # 2. tool surface --------------------------------------------------------
    def tool_count():
        import asyncio
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        url, token = pmb_client.discover_endpoint(), pmb_client.read_token()

        async def go():
            c = httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}, timeout=30.0)
            async with streamable_http_client(url, http_client=c) as st:
                async with ClientSession(st[0], st[1]) as s:
                    await s.initialize()
                    return [t.name for t in (await s.list_tools()).tools]

        names = asyncio.run(go())
        need = {"prepare", "recall", "record_batch", "record_fact", "record_goal",
                "record_activity", "find_lessons", "mark_lesson_followed",
                "lesson_impact", "session_brief", "stats", "chain_current"}
        missing = need - set(names)
        return (not missing, "%d tools; missing=%s" % (len(names), sorted(missing) or "none"))
    check("tool surface complete", tool_count)

    # 3. read-first bundle ---------------------------------------------------
    check("prepare() returns a bundle", lambda: (
        (lambda r: (isinstance(r, dict) and bool(r), "keys=%s" % sorted(r)[:6]))(
            pmb_client.call_tool("prepare", {"message": "e2e check"}))))

    # 4. recall --------------------------------------------------------------
    check("recall() finds this work", lambda: (
        (lambda r: (bool(r), "%d hit(s)" % len(r) if isinstance(r, list) else str(r)[:60]))(
            pmb_client.call_tool("recall", {"query": "PMB outcome loop hardwired", "top_k": 3}))))

    # 5. write path ----------------------------------------------------------
    def write_path():
        r = pmb_client.call_tool("record_batch", {"items": [
            {"type": "activity", "kind": "tool_call",
             "summary": "pmb_e2e_check.py ran"}]})
        return (bool(r.get("ok")), "outbox=%s" % r.get("outbox_id"))
    check("record_batch() writes", write_path)

    # 6. session_brief -------------------------------------------------------
    check("session_brief() works", lambda: (
        (lambda r: (r is not None, "ok" if r is not None else "none"))(
            pmb_client.call_tool("session_brief", {}))))

    # 7. stats ---------------------------------------------------------------
    check("stats() works", lambda: (
        (lambda r: ("events" in r, "events=%d" % r["events"]["total"]))(
            pmb_client.call_tool("stats", {}))))

    # 8. THE FIX: outcome signal --------------------------------------------
    def outcome_signal():
        r = pmb_client.call_tool("lesson_impact", {"window_days": 90})
        n = r.get("n_outcome_turns", 0)
        return (n > 0, "n_outcome_turns=%s sufficiency=%s" % (n, r.get("signal_sufficiency")))
    check("lesson_impact has outcome signal", outcome_signal)

    # 9. adherence pass ------------------------------------------------------
    def followcheck():
        """The adherence pass runs AND reports its four counters. On an
        mcp-only host there is no `followcheck` MCP tool, so drive the engine
        API directly (the same call the Stop hook would make)."""
        from pathlib import Path as _P
        _sys.path.insert(0, str(_P(__file__).resolve().parent))
        from pmb.core.engine import Engine
        from pmb.hooks.followcheck import run_followcheck
        res = run_followcheck(Engine(), window_minutes=30.0, apply=False)
        d = res.to_dict() if hasattr(res, "to_dict") else dict(res)
        need = {"checked", "marked_followed", "not_applicable"}
        missing = need - set(d)
        return (not missing,
                "checked=%s marked=%s n/a=%s" % (
                    d.get("checked"), d.get("marked_followed"),
                    d.get("not_applicable")))
    check("followcheck reports counters", followcheck)

    # 9b. the causal arm's input: surface_ids stamped on actions -------------
    def surface_ids_present():
        """The join needs `surface_ids` on agent_actions rows. Without it the
        followed/ignored arm can never populate, so the loop stays associational
        forever. Assert the COLUMN exists (values may legitimately be empty
        when no lesson was surfaced in the window)."""
        import sqlite3
        dbs = list((Path.home() / ".pmb" / "workspaces").glob("*/events.sqlite"))
        if not dbs:
            return False, "no events.sqlite"
        c = sqlite3.connect(str(dbs[0]), timeout=30)
        cols = {r[1] for r in c.execute("PRAGMA table_info(agent_actions)").fetchall()}
        stamped = c.execute(
            "SELECT COUNT(*) FROM agent_actions WHERE surface_ids IS NOT NULL "
            "AND surface_ids != ''").fetchone()[0]
        return ("surface_ids" in cols, "column=%s stamped_rows=%d" % ("surface_ids" in cols, stamped))
    check("agent_actions has surface_ids", surface_ids_present)

    # 10. agent_actions table exists (the loop's input) ---------------------
    def table_exists():
        db = Path.home() / ".pmb" / "workspaces"
        dbs = list(db.glob("*/events.sqlite"))
        if not dbs:
            return False, "no events.sqlite found"
        c = sqlite3.connect(str(dbs[0]), timeout=30)
        tabs = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        n = c.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0] \
            if "agent_actions" in tabs else -1
        return ("agent_actions" in tabs and n > 0, "rows=%d" % n)
    check("agent_actions populated", table_exists)

    # 11. wiring present in the repo ----------------------------------------
    def wiring():
        root = Path(__file__).resolve().parent.parent
        conftest = (root / "conftest.py").read_text(encoding="utf-8", errors="replace")
        hook = (root / ".githooks" / "pre-commit").read_text(encoding="utf-8", errors="replace")
        obs = (root / "tools" / "pmb_session_observe.py").exists()
        has_cf = "pytest_sessionfinish" in conftest and "pmb_session_observe" in conftest
        has_hk = "pmb_session_observe" in hook
        return (obs and has_cf and has_hk,
                "observer=%s conftest=%s hook=%s" % (obs, has_cf, has_hk))
    check("outcome observer wired", wiring)

    print("=" * 78)
    n_pass = sum(1 for _, ok, _ in RESULTS if ok)
    print("%d/%d checks passed" % (n_pass, len(RESULTS)))
    return 0 if n_pass == len(RESULTS) else 1


def _has_tool(name: str) -> bool:
    try:
        return name in _TOOL_NAMES
    except NameError:
        return False


_TOOL_NAMES: set[str] = set()

if __name__ == "__main__":
    raise SystemExit(main())
