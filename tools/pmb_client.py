"""pmb_client.py — reach the local PMB memory daemon without a stale session.

WHY THIS EXISTS
---------------
The PMB daemon (`.pmb-venv`, FastMCP 4.0.10) idle-exits after
`daemon.idle_exit_min` (120 min) and is restarted on demand, and it migrates
ports when the old one is taken (observed 8765 -> 8766 -> 8768). Two failure
modes fall out of that, and both were live in this repo:

  1. A HARDCODED endpoint goes stale the moment the port moves.
     `tools/prepare_guard.py` pinned `http://127.0.0.1:8765/mcp`; the daemon was
     on 8768, so the guard could not mint a receipt.

  2. A CLIENT THAT CACHES ITS SESSION breaks across a restart. The
     `mcp-session-id` is issued per daemon INSTANCE, so an id cached before the
     daemon idle-exited no longer exists afterwards and every call answers:

         {"code": -32600, "message": "Session not found"}

     Auth still succeeds because the bearer token is deliberately REUSED across
     restarts (`daemon.write_daemon_token`); only the session is dead. That is
     why the symptom looks like an auth problem and is not one.

So this client:

  * discovers host/port/path from `$PMB_HOME/servers.json` — the daemon's own
    registry, written by `daemon.py` and pruned by `pmb_daemon_launcher.py` —
    never a constant;
  * performs a FRESH `initialize` on every invocation and never persists a
    session id, so it cannot hold a stale one;
  * reads the bearer token from `$PMB_HOME/daemon.token`.

It must run under the interpreter that has `mcp` and `httpx2` — the
`.pmb-venv` one, e.g.:

    C:/Users/clint/.pmb-venv/Scripts/python.exe tools/pmb_client.py overview azazael

CLI
---
    python tools/pmb_client.py prepare "what I am about to do"
    python tools/pmb_client.py recall "query" [--top-k 5]
    python tools/pmb_client.py overview <project>
    python tools/pmb_client.py call <tool> '<json-args>'
    python tools/pmb_client.py status          # endpoint + liveness only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def pmb_home() -> Path:
    return Path(os.environ.get("PMB_HOME") or (Path.home() / ".pmb"))


def discover_endpoint() -> str:
    """The live daemon's MCP URL, from its own registry.

    Falls back to the conventional port only if the registry is unreadable, so
    the common case never depends on a constant.
    """
    registry = pmb_home() / "servers.json"
    try:
        entries = json.loads(registry.read_text(encoding="utf-8"))
    except Exception:
        entries = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") not in (None, "daemon"):
            continue
        if entry.get("pid_alive") is False:
            continue
        host = entry.get("host") or "127.0.0.1"
        port = entry.get("port")
        path = entry.get("path") or "/mcp"
        if port:
            return "http://%s:%s%s" % (host, port, path)
    return "http://127.0.0.1:8765/mcp"


def read_token() -> str:
    return (pmb_home() / "daemon.token").read_text(encoding="utf-8").strip()


def call_tool(name: str, arguments: dict) -> object:
    """Call one PMB tool over a FRESH streamable-http session.

    The session is created and torn down inside this call, so no stale
    `mcp-session-id` can ever be reused between invocations.
    """
    import asyncio

    import httpx2
    from mcp import ClientSession
    #: The MCP SDK renamed this symbol between releases: `streamablehttp_client`
    #: (older) vs `streamable_http_client` (newer). Measured 2026-10-30 —
    #: py315t ships the former, .pmb-venv the latter. Importing one fixed name
    #: made this CLI die with ImportError on the other interpreter, and the
    #: supervisor read that death as empty stdout: a fallback that fails
    #: silently instead of falling back.
    try:
        from mcp.client.streamable_http import streamable_http_client
    except ImportError:
        from mcp.client.streamable_http import (
            streamablehttp_client as streamable_http_client,
        )

    url, token = discover_endpoint(), read_token()

    async def go():
        import inspect
        auth = {"Authorization": "Bearer %s" % token}
        client = httpx2.AsyncClient(headers=auth, timeout=120.0)
        #: Same rename carries a signature change: the newer client takes a
        #: prebuilt `http_client`, the older one takes `headers`/`timeout` and
        #: rejects the keyword outright (`TypeError: got an unexpected keyword
        #: argument 'http_client'`, measured on py315t 2026-10-30). Pick the
        #: form the installed SDK actually accepts instead of pinning one.
        try:
            params = inspect.signature(streamable_http_client).parameters
        except (TypeError, ValueError):
            params = {}
        if "http_client" in params:
            ctx = streamable_http_client(url, http_client=client)
        else:
            ctx = streamable_http_client(url, headers=auth, timeout=120.0)
        async with ctx as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                text = "".join(getattr(item, "text", "")
                               for item in result.content)
                try:
                    return json.loads(text)
                except (ValueError, TypeError):
                    return text

    return asyncio.run(go())


def _print_summary(payload: object) -> None:
    if not isinstance(payload, dict):
        print(payload)
        return
    for key in ("lessons", "decisions", "open_goals", "recent_activity",
                "results", "project_context"):
        value = payload.get(key)
        if not value:
            continue
        print("--%s--" % key)
        # `project_context` is a DICT (not a list) in prepare()'s payload, and
        # the first version sliced it unconditionally -> KeyError: slice(...).
        # A summary printer that crashes on a valid payload is the same class
        # of defect as a guard that reports OK while wrong.
        if isinstance(value, dict):
            for k, v in list(value.items())[:12]:
                print("  %s: %s" % (k, str(v)[:120]))
            continue
        if not isinstance(value, (list, tuple)):
            print("  -", str(value)[:140])
            continue
        for item in value[:8]:
            text = item.get("content", item) if isinstance(item, dict) else item
            print("  -", str(text)[:140])


def main() -> int:
    # Wide-char payloads (goal titles with section signs, arrows, etc.) kill
    # print() on cp1252 consoles: UnicodeEncodeError -> rc=1 with empty
    # stdout, which reads as an unreadable daemon. Replace, never die; UTF-8
    # because every consumer here (supervisor, 3.15 subprocess defaults)
    # decodes as UTF-8. The supervisor leg already worked around this with
    # PYTHONIOENCODING (its "#706 class" comment); this fixes it at source.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        _ = exc  # pythonw has no stdout to fix; keep the default codec
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("message")

    p_recall = sub.add_parser("recall")
    p_recall.add_argument("query")
    p_recall.add_argument("--top-k", type=int, default=5)

    p_overview = sub.add_parser("overview")
    p_overview.add_argument("name")

    p_call = sub.add_parser("call")
    p_call.add_argument("tool")
    p_call.add_argument("args", nargs="?", default="{}")
    #: Machine-readable payload. The summary printer is for humans and drops
    #: every numeric field, so a caller that needs to filter by score (the
    #: supervisor's relevance floor) has nothing to read. Printing the parsed
    #: payload is the difference between a floorable and an unfloorable
    #: fallback.
    p_call.add_argument("--json", action="store_true",
                        help="print the raw JSON payload, not a summary")

    sub.add_parser("status")

    args = parser.parse_args()

    if args.cmd == "status":
        url = discover_endpoint()
        try:
            payload = call_tool("project_overview", {"name": "azazael"})
            print("endpoint: %s" % url)
            print("reachable: True (%d key(s))"
                  % (len(payload) if isinstance(payload, dict) else 1))
        except Exception as exc:  # noqa: BLE001 — status must never raise
            print("endpoint: %s" % url)
            print("reachable: False (%s: %s)" % (type(exc).__name__, exc))
            return 1
        return 0

    if args.cmd == "prepare":
        payload = call_tool("prepare", {"message": args.message})
    elif args.cmd == "recall":
        payload = call_tool("recall", {"query": args.query,
                                       "top_k": args.top_k})
    elif args.cmd == "overview":
        payload = call_tool("project_overview", {"name": args.name})
    else:
        payload = call_tool(args.tool, json.loads(args.args))

    if getattr(args, "json", False) and isinstance(payload, (dict, list)):
        print(json.dumps(payload, default=str))
    else:
        _print_summary(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
