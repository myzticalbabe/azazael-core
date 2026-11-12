#!/usr/bin/env python
"""pmb_observe_transcript.py -- record real command outcomes from a transcript.

The test and commit triggers (conftest.py, .githooks/pre-commit) cover the two
outcomes that always fire, but a session also runs ad-hoc commands whose exit
status is a genuine outcome signal (a build, a lint, a migration). Those live in
the host's transcript, and this wrapper feeds them to PMB.

WHY A WRAPPER
-------------
The outcome row must be written by the `.pmb-venv` interpreter (the one with
`pmb`), but a transcript is produced by whatever agent ran the session. So the
parsing is pure and testable, and the recording is delegated to
`tools/pmb_session_observe.py --transcript`.

It is deliberately conservative: only commands PMB already considers SIGNIFICANT
(`pmb.core.ambient_log.is_significant_action`) are recorded, so a transcript full
of `ls`/`cat` adds nothing. Unknown transcript shapes produce zero rows rather
than a guess.

Usage
-----
    C:/Users/clint/.pmb-venv/Scripts/python.exe tools/pmb_observe_transcript.py \
        --transcript <path.json> [--followcheck]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _extract_commands(node, out: list[tuple[str, str]]) -> None:
    """Recursively collect (command, status) from any JSON shape.

    Looks for objects carrying a shell command under a few known key names and
    an exit code under a few more; anything unrecognised is skipped.
    """
    if isinstance(node, dict):
        cmd = None
        for key in ("command", "cmd", "shell_command", "bash_command"):
            v = node.get(key)
            if isinstance(v, str) and v.strip():
                cmd = v.strip()
                break
        if cmd is not None:
            code = None
            for key in ("exit_code", "exitCode", "status_code"):
                if key in node:
                    code = node[key]
                    break
            if code is None and isinstance(node.get("result"), dict):
                code = node["result"].get("exit_code")
            if code is None and isinstance(node.get("tool_response"), dict):
                code = node["tool_response"].get("exit_code")
            status = "ok" if code in (0, None) else str(code)
            out.append((cmd, status))
        for v in node.values():
            _extract_commands(v, out)
    elif isinstance(node, list):
        for v in node:
            _extract_commands(v, out)


def load_commands(path: str) -> list[tuple[str, str]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    out: list[tuple[str, str]] = []
    _extract_commands(data, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--followcheck", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    pairs = load_commands(args.transcript)
    try:
        from pmb.core.ambient_log import is_significant_action
    except Exception as exc:  # noqa: BLE001 -- run under the .pmb-venv python
        if not args.quiet:
            sys.stderr.write("pmb not importable: %r\n" % (exc,))
        return 1

    # Delegate recording to the single writer so there is one code path.
    import pmb_session_observe as obs

    recorded = 0
    for cmd, status in pairs:
        if not is_significant_action("Bash", command=cmd):
            continue
        try:
            if obs.observe_command(cmd, status) is not None:
                recorded += 1
        except Exception:  # noqa: BLE001
            continue

    result = {"parsed": len(pairs), "recorded": recorded}
    if args.followcheck:
        try:
            result["followcheck"] = obs.run_followcheck(30)
        except Exception as exc:  # noqa: BLE001
            result["followcheck"] = {"error": repr(exc)}
    if not args.quiet:
        print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
