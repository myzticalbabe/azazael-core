#!/usr/bin/env python
# coding: utf-8
"""The pmb daemon's bearer token, resolved AT RUNTIME rather than baked.

WHY THIS EXISTS
The token in `~/.pmb/daemon.token` is written by pmb's
`mcp/daemon.py::write_daemon_token()`. It is deliberately REUSED across daemon
restarts so that a client config holding `Authorization: Bearer <token>` keeps
working -- but that reuse is a property of the TOKEN FILE, not of the client's
copy of it. `write_daemon_token(rotate=True)` mints a fresh secret, and so does
deleting `~/.pmb/daemon.token` (the daemon mints a new one on its next start).

Measured 2026-11-11: `~/.config/opencode/opencode.json` and
`~/.config/kilo/kilo.jsonc` both held `zof5qUiXCYSg...` while the live token on
disk was `wQ3iLShZcqMu...`. The daemon was healthy the whole time -- 30 tools,
62 ms -- and `opencode mcp list` reported `pmb needs authentication`. The
client's baked copy was dead; the server was fine. The startup gate read
PASS=9 throughout, because its `lane-config` check only asks whether the pmb
URL routes through :8766, never whether the baked credential still
authenticates.

THE SHAPE OF THE FIX
A token that a server can rotate must never be COPIED into a client's config,
because a copy is a promise the server did not make. So the token is READ from
disk at the moment it is needed:

  * the floor proxy (:8766) reads it per request and injects it upstream, so a
    rotation is picked up without restarting the proxy and without touching a
    single lane config;
  * a lane whose config holds a STALE token still works, because the proxy
    replaces the client's header rather than forwarding it verbatim.

What this does NOT do is weaken the boundary: the proxy still refuses to
forward a request it cannot authenticate, and it still requires the client to
present the live token. The difference is WHERE the truth comes from -- the
server's own file, read now -- rather than a copy that may be months old.

WHY A SEPARATE MODULE
The proxy is started by a Startup VBS with a working directory outside the
repository, so `from tools.defaults_profile import read_default` FAILS there
(measured: ModuleNotFoundError) and the proxy silently falls back to its
environment-variable defaults. Anything the proxy needs to resolve must
therefore work with NO repo root on sys.path, which is exactly the constraint
this module is written under: stdlib only, no imports from `tools.`.

CONTRACT
  live_token()      -> the current token, or "" when it cannot be read
  token_matches(given) -> constant-time-ish comparison of a presented header
  upstream_headers(given) -> the headers to forward, with the live token
                        substituted for whatever the client presented
"""
from __future__ import annotations

import hmac
import os
import sys
from pathlib import Path

#: Failures this module absorbed, newest last.
#:
#: A resolver that swallows an error and returns a value that looks like an
#: answer is how "the token file is missing" becomes "the token is empty"
#: becomes "the daemon is refusing us" three steps away from the cause. So
#: every absorbed failure is recorded here AND written to stderr, where the
#: proxy's operator will actually see it.
FAILURES: list[str] = []


def record_failure(message: str) -> None:
    """Record an absorbed failure without changing the caller's result."""
    FAILURES.append(str(message))
    try:
        print("[pmb-token] %s" % message, file=sys.stderr, flush=True)
    except Exception:
        # A failure to REPORT is still a failure to report; recording it in
        # FAILURES is the part that must not depend on the console.
        FAILURES.append("could not write the failure to stderr")

#: Where pmb keeps the daemon token. `PMB_HOME` overrides it, because pmb
#: itself honours PMB_HOME and a token from one home is not a token for
#: another. The default matches pmb's own `mcp/daemon.py::token_path()`.
DEFAULT_TOKEN_PATH = Path.home() / ".pmb" / "daemon.token"


def token_path() -> Path:
    """The file the live daemon token lives in, honouring PMB_HOME."""
    home = os.environ.get("PMB_HOME")
    base = Path(home) if home else Path.home() / ".pmb"
    return base / "daemon.token"


def live_token(path: Path | None = None) -> str:
    """The daemon's CURRENT token, or "" when it cannot be read.

    Read on EVERY call, never cached: the whole point is that a rotation is
    invisible to the caller. The file is 43 bytes and the read is a stat plus a
    read on a path the proxy touches once per request, so the cost is noise
    against a 150 ms recall.

    An unreadable token is reported as "" rather than raised: the caller has a
    better answer than an exception (it can forward the client's own header and
    let the daemon answer 401 with its own reason), and a proxy that dies
    because a token file is missing is a proxy that takes down every lane.

    The failure is RECORDED as well as reported: a resolver that returns "" for
    a missing file and for an unreadable one, with nothing written down, is
    indistinguishable from a resolver that is not being called at all -- which
    is precisely the state this module exists to end.
    """
    target = path if path is not None else token_path()
    try:
        return target.read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as exc:
        record_failure("live_token(%s): %s" % (target, exc))
        return ""


def token_matches(presented: str | None, expected: str | None = None) -> bool:
    """Is `presented` the live token?

    `presented` is the raw value of an Authorization header (or the bearer
    value alone -- both forms are accepted, because the header and the value
    are the same secret and clients differ on which they send).

    The comparison is `hmac.compare_digest`, not `==`: a length-independent,
    constant-time compare, so a caller cannot learn the token a character at a
    time by timing the answer. It matters less for a loopback-only secret than
    for a network one, but it costs nothing to do it properly.
    """
    want = expected if expected is not None else live_token()
    if not want:
        return False
    got = (presented or "").strip()
    if got.lower().startswith("bearer "):
        got = got[7:].strip()
    if not got:
        return False
    return hmac.compare_digest(got, want)


def upstream_headers(headers: dict, path: Path | None = None) -> dict:
    """The headers to forward upstream, with the LIVE token substituted.

    `headers` is the client's header dict. Whatever the client sent as
    Authorization is REPLACED by the token on disk, not merely defaulted to:
    a client whose config holds a stale copy must keep working, and a copy is
    exactly what may be stale.

    When the token cannot be read at all, the client's own header is passed
    through unchanged -- the daemon then answers 401 with its own reason
    instead of the proxy answering 401 with an invented one. That keeps the
    failure legible: "your token is wrong" and "I could not find any token" are
    different problems and must not look the same.
    """
    out = dict(headers)
    token = live_token(path)
    if token:
        out["Authorization"] = "Bearer " + token
    return out


if __name__ == "__main__":       # a human-readable read of the current state
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    path = token_path()
    token = live_token(path)
    if token:
        print("token file : %s" % path)
        print("token      : %s... (%d chars)" % (token[:8], len(token)))
    else:
        print("token file : %s" % path)
        print("token      : UNREADABLE (daemon.token missing or empty)")
        raise SystemExit(1)
