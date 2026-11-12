#!/usr/bin/env python
# coding: utf-8
"""pmb floor proxy -- relevance floor in front of the pmb daemon's MCP endpoint.

WHY THIS EXISTS
`pmb` returns its nearest rows even when nothing matches, so a nonsense query
comes back wearing the shape of an answer. `confidence` does NOT separate the
two (0.539 nonsense against 0.72-0.89 real), so the floor reads `score`.

TWO TIERS, because one threshold cannot do this job
Measured on the live daemon 2026-10-30, twice: first on 4 noise queries, then
again after the contaminated test records were purged (n=15 noise, 8 real):

  noise BEST score  0.836  0.722(p90)  0.646  0.646  0.640  ...  mean 0.645
  real  BEST score  1.313  1.298  1.280  1.251  1.245  0.994  0.949  ... min 0.949
  real  ROW scores  0.600(min)  0.640(p10)  0.850(p25)  0.937(median)

The best row separates the two populations with a measured GAP of 0.113; the
individual rows do not, because a genuine query's tail reaches down to 0.600.
So one per-row threshold is wrong in both directions, and the two tiers are
placed on the gap, not on a guess:

  PMB_RECALL_MIN_BEST  0.89  midpoint of the measured gap (0.836 .. 0.949).
                             Under it, nothing matched: drop the set and say so.
  PMB_RECALL_FLOOR     0.70  trims a genuine query's weak tail. This only ever
                             runs after tier 1 passed, so it never has to hold
                             noise out -- it decides how much low-confidence
                             text reaches the model.

The package cannot enforce this: pmb-ai 1.2.3 is simultaneously the installed
and the latest published version, and its only score gate
(`auto_recall.recall_min_score`, 0.30) lives in the HOOK path, which Kilo never
runs because it has no hooks. Patching site-packages would work until the next
upgrade. So the floor lives here.

WHY A REVERSE PROXY AND NOT AN MCP SERVER
A stdio MCP server would have to re-expose 30 upstream tools and would be
pinned to one `mcp` SDK API -- and that API is not stable: mcp 2.2.0 has no
`Server.list_tools()` decorator at all, while other interpreters here ship the
1.x surface. The floor is a PAYLOAD concern, so it belongs at the payload
layer: forward every JSON-RPC frame untouched, and rewrite only the ones whose
tool result carries scored rows. No SDK, no tool re-registration, nothing that
can drift.

CONTRACT
* Every request/response passes through byte-for-byte except scored results.
* A scored result below the floor is dropped and the drop is NAMED in a
  `floor` field, so a filtered answer cannot be mistaken for an empty store.
* Every filtered result is also added to cumulative kept/dropped counters,
  served by GET /healthz -- a drop is invisible to the model by construction,
  so the counters are the one place the silence is measured.
* Unscored results (writes, stats, goals) are never touched.
* Daemon still down after PMB_UPSTREAM_WAIT seconds -> 502 with the reason,
  never an empty success. A daemon that is merely still starting is waited
  for, because a 502 during a cold start is indistinguishable to the client
  from a dead stack.

ENV
  PMB_RECALL_FLOOR      row floor, default 0.70; 0 disables filtering.
  PMB_RECALL_MIN_BEST   query floor, default 0.89.
  PMB_FLOOR_PORT        default 8766.
  PMB_DAEMON_URL        default http://127.0.0.1:8765/mcp.

CALIBRATION
After purging the records that quoted the nonsense phrases, the top noise score
fell from 0.836 to a mean of 0.645 and the weakest real query sits at 0.949.
Re-run the sweep whenever the corpus changes substantially -- thresholds are a
property of the store, not of the software.

RUN
  C:/Users/clint/.pmb-venv/Scripts/python.exe C:/Azazael/azazael/tools/pmb_floor_proxy.py

Normally nothing runs this by hand. pams_server_launcher keeps it warm, and at
logon `Startup\\pmb-floor-proxy.vbs` starts it hidden, so :8766 exists before
any MCP client connects -- opencode's remote client gives an unreachable
endpoint three attempts in 1.65 s and then marks the server failed for the
whole session (measured 2026-10-31).
Kilo's `pmb` entry then points at http://127.0.0.1:8766/mcp with the same
bearer token it used for the daemon.
"""
import json
import os
import sys
import threading
import time
import urllib.error
from datetime import datetime
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The token resolver is a SIBLING of this file, not a package member: the
# proxy is started by a Startup VBS whose working directory is outside the
# repository, so `from tools.x import y` FAILS there (measured:
# ModuleNotFoundError) and the proxy silently falls back to its environment
# defaults instead of reading config/defaults.json. Loading the sibling by
# PATH keeps this file working with no repo root on sys.path, which is the
# condition it actually runs under.
#
# This file is loaded two ways -- as a SCRIPT (sys.path[0] is this directory)
# and by FILE PATH in the test suite (which resolves neither `pmb_token` nor
# `tools.pmb_token`) -- so the directory is added to sys.path explicitly and
# the sibling is imported from its own name. One path, no guessing.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
try:
    from pmb_token import upstream_headers as _upstream_headers
    from pmb_token import token_matches as _token_matches
except ImportError:
    _upstream_headers = None
    _token_matches = None

if _upstream_headers is None:
    _TOKEN_SIBLING_MISSING = True

    def _upstream_headers(headers, path=None):
        """Fallback when the sibling module cannot be loaded.

        Pass the client's own header through unchanged rather than inventing
        a token: the daemon then answers 401 with its own reason, and a
        missing sibling is visible in the daemon's answer instead of being
        masked by the proxy's own guess. The failure is NAMED on stderr --
        ONCE, not per request, because a per-request warning on a hot path
        buries the message it is trying to deliver -- and because a silent
        fallback here is exactly how this proxy ends up running with the wrong
        thresholds and nobody noticing.
        """
        global _TOKEN_SIBLING_MISSING
        if _TOKEN_SIBLING_MISSING:
            _TOKEN_SIBLING_MISSING = False
            print("[floor-proxy] pmb_token sibling not importable; "
                  "forwarding the client's Authorization header unchanged",
                  file=sys.stderr, flush=True)
        return dict(headers)

if _token_matches is None:
    def _token_matches(presented, expected=None):
        """Fallback when the sibling module cannot be loaded.

        Without the resolver there is nothing to compare the client's token
        against, so strict mode cannot be honoured. Say so rather than
        pretending: the caller turns this into a refusal, and a refusal that
        names its cause is a far better failure than a silent pass.
        """
        print("[floor-proxy] pmb_token sibling not importable; strict auth "
              "cannot be verified", file=sys.stderr, flush=True)
        return False

# Configuration from config/defaults.json via defaults_profile.
#
# The repo root is put on sys.path FIRST, because this file is started by a
# Startup VBS whose working directory is outside the repository: measured
# 2026-11-11, `from tools.defaults_profile import read_default` FAILED there
# (ModuleNotFoundError) and the proxy silently fell back to its
# environment-variable defaults -- so editing config/defaults.json did not
# change the thresholds the live proxy was actually applying. A settings
# surface that the running process cannot see is not a settings surface.
#
# The fallback is still here (a host without the repo must still serve), but
# it now says so out loud: a silent fallback is how a proxy ends up running
# with thresholds nobody chose.
try:
    _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    from tools.defaults_profile import read_default
    FLOOR = float(read_default("memory.pmb_recall_floor", 0.70))
    MIN_BEST = float(read_default("memory.pmb_recall_min_best", 0.89))
    PORT = int(read_default("mcp_stack.floor_port", 8766))
    UPSTREAM = "http://127.0.0.1:%d/mcp" % int(read_default("mcp_stack.daemon_port", 8765))
    UPSTREAM_WAIT = float(read_default("mcp_stack.floor_upstream_wait_s", 30))
    ORIGIN = read_default("mcp_stack.floor_origin", "logon")
    CONFIG_SOURCE = "config/defaults.json"
except Exception as exc:
    # Fallback to environment variables for backward compatibility
    FLOOR = float(os.environ.get("PMB_RECALL_FLOOR", "0.70"))
    MIN_BEST = float(os.environ.get("PMB_RECALL_MIN_BEST", "0.89"))
    PORT = int(os.environ.get("PMB_FLOOR_PORT", "8766"))
    UPSTREAM = os.environ.get("PMB_DAEMON_URL", "http://127.0.0.1:8765/mcp")
    UPSTREAM_WAIT = float(os.environ.get("PMB_UPSTREAM_WAIT", "30"))
    ORIGIN = os.environ.get("PMB_FLOOR_ORIGIN", "manual")
    CONFIG_SOURCE = "environment fallback (%s: %s)" % (type(exc).__name__, exc)

#: Whether a client must present the token at all.
#:
#: "inject" -- the proxy resolves the live token and authenticates on the
#:   client's behalf. A client with a stale (or absent) token still works,
#:   which is the whole point: a token the server can rotate must never be
#:   COPIED into a client. The daemon's token is a shared secret for THIS
#:   machine, and the proxy is the only thing that talks to the daemon, so
#:   holding it here is the natural place.
#:
#: "strict" -- the client must present the live token itself; a stale or
#:   missing one is refused with 401. Use this when the proxy is reachable by
#:   something that should NOT be able to use the memory store.
#:
#: The default is "inject" because the failure this fixes is silent and
#: total (a lane's memory tools vanish for the whole session) while the
#: threat it opens is a loopback-only port that already had the token in
#: every lane config on the box. It is a KNOB rather than a constant so the
#: choice is visible and reversible rather than baked into the code.
try:
    from tools.defaults_profile import read_default as _read_default
    AUTH_MODE = str(_read_default("mcp_stack.floor_auth_mode", "inject")).lower()
except Exception:
    AUTH_MODE = os.environ.get("PMB_FLOOR_AUTH_MODE", "inject").lower()
if AUTH_MODE not in ("inject", "strict"):
    print("[floor-proxy] unknown floor_auth_mode %r; falling back to 'inject'"
          % AUTH_MODE, file=sys.stderr, flush=True)
    AUTH_MODE = "inject"

STAMP = os.environ.get("PMB_FLOOR_STAMP") or os.path.join(
    os.path.expanduser("~"), ".pmb", "floor_proxy_runtime.json")


def stamp_runtime() -> None:
    """Record which starter owns this instance. Best effort: never blocks serving."""
    try:
        parent = os.path.dirname(STAMP)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {
            "origin": ORIGIN,
            "pid": os.getpid(),
            "port": PORT,
            "upstream": UPSTREAM,
            "started": datetime.now().astimezone().isoformat(timespec="seconds"),
            "script": os.path.abspath(__file__),
        }
        tmp = STAMP + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, STAMP)
    except Exception as exc:                       # a stamp is not a precondition
        print("[floor-proxy] provenance stamp not written: %s" % exc,
              file=sys.stderr, flush=True)

#: Payload keys that can carry scored rows.
SCORED_KEYS = ("results", "lessons", "decisions", "recent_activity")


# --------------------------------------------------------------------------- #
# Cumulative counters -- what the floor has done since this instance started
# --------------------------------------------------------------------------- #

#: A dropped row is invisible by construction: the model never sees it, so
#: nothing downstream can report it. These counters are the one place the
#: silence is measured. /healthz answers with them (kept_total, dropped_total,
#: query_events), so an operator can see whether the floor is trimming or
#: strangling without reading stderr.
_COUNTERS = {"kept_total": 0, "dropped_total": 0, "query_events": 0}
_COUNTERS_LOCK = threading.Lock()
_COUNT_EVENT_START = time.monotonic()


def _reset_counters() -> None:
    """Zero the counters. Test hook; the proxy itself never resets them."""
    global _COUNT_EVENT_START
    with _COUNTERS_LOCK:
        _COUNTERS["kept_total"] = 0
        _COUNTERS["dropped_total"] = 0
        _COUNTERS["query_events"] = 0
        _COUNT_EVENT_START = time.monotonic()


def _counters() -> dict:
    with _COUNTERS_LOCK:
        return {
            "kept_total": _COUNTERS["kept_total"],
            "dropped_total": _COUNTERS["dropped_total"],
            "query_events": _COUNTERS["query_events"],
            "seconds_running": round(time.monotonic() - _COUNT_EVENT_START, 1),
        }


def record_floor_event(floor_dict):
    """Add one filtered result's kept/dropped counts to the totals."""
    if not isinstance(floor_dict, dict):
        return
    kept = floor_dict.get("kept")
    dropped = floor_dict.get("dropped")
    if not isinstance(kept, int) or not isinstance(dropped, int):
        return
    with _COUNTERS_LOCK:
        _COUNTERS["kept_total"] += kept
        _COUNTERS["dropped_total"] += dropped
        _COUNTERS["query_events"] += 1


def health_payload() -> dict:
    """The document GET /healthz serves. Pure, so it is testable without a socket."""
    return {
        "status": "ok",
        "floors": {"status": "row_floor", "pmb_recall_floor": FLOOR,
                   "pmb_recall_min_best": MIN_BEST},
        "counters": _counters(),
        "origin": ORIGIN,
        "pid": os.getpid(),
        "upstream": UPSTREAM,
        "config_source": CONFIG_SOURCE,
        "started": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def apply_floor(payload):
    """Apply the two tiers. Returns (payload, note); note is "" when untouched.

    A list is only filtered when EVERY row carries a numeric `score`; a
    partly-scored list is left whole rather than half-trimmed, which would
    silently drop the unscored entries.
    """
    if not isinstance(payload, dict) or FLOOR <= 0:
        return payload, ""
    out = dict(payload)
    scored_keys = []
    total = 0
    best = 0.0
    for key in SCORED_KEYS:
        rows = out.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        if not all(isinstance(r, dict) and isinstance(r.get("score"), (int, float))
                   for r in rows):
            continue
        scored_keys.append(key)
        total += len(rows)
        for row in rows:
            best = max(best, float(row["score"]))
    if not scored_keys:
        return payload, ""

    # Tier 1: nothing in this result set is close enough to be an answer.
    if best < MIN_BEST:
        for key in scored_keys:
            out[key] = []
        note = ("no match: best score %.2f is under the query floor %.2f; "
                "%d nearest row(s) dropped" % (best, MIN_BEST, total))
        out["floor"] = {"applied": FLOOR, "min_best": MIN_BEST, "tier": "query",
                        "dropped": total, "kept": 0,
                        "best_score": round(best, 3), "note": note}
        if "n_results" in out:
            out["n_results"] = 0
        return out, note

    # Tier 2: some rows matched; drop the weak tail.
    dropped = kept = 0
    for key in scored_keys:
        survivors = [r for r in out[key] if float(r["score"]) >= FLOOR]
        dropped += len(out[key]) - len(survivors)
        kept += len(survivors)
        out[key] = survivors
    if not dropped:
        return payload, ""
    note = ("%d row(s) below relevance floor %.2f dropped (best %.2f)"
            % (dropped, FLOOR, best))
    out["floor"] = {"applied": FLOOR, "min_best": MIN_BEST, "tier": "row",
                    "dropped": dropped, "kept": kept,
                    "best_score": round(best, 3), "note": note}
    if "n_results" in out:
        out["n_results"] = kept
    return out, note



try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

def _floor_frame(frame):
    """Rewrite one JSON-RPC message in place if it carries scored rows."""
    result = frame.get("result")
    if not isinstance(result, dict):
        return frame
    content = result.get("content")
    if not isinstance(content, list):
        return frame
    changed = False
    new_content = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            new_content.append(item)
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.lstrip().startswith("{"):
            new_content.append(item)
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            new_content.append(item)
            continue
        filtered, note = apply_floor(payload)
        if note:
            record_floor_event(filtered.get("floor")
                               if isinstance(filtered, dict) else None)
            item = dict(item)
            item["text"] = json.dumps(filtered, ensure_ascii=False, default=str)
            changed = True
            print("[floor] %s" % note, file=sys.stderr, flush=True)
        new_content.append(item)
    if not changed:
        return frame
    frame = dict(frame)
    result = dict(result)
    result["content"] = new_content
    frame["result"] = result
    return frame


def transform_body(raw, content_type):
    """Apply the floor to a JSON or SSE body; return the bytes to send back."""
    if FLOOR <= 0:
        return raw
    is_sse = "text/event-stream" in (content_type or "")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    if not is_sse:
        try:
            frame = json.loads(text)
        except ValueError:
            return raw
        if not isinstance(frame, dict):
            return raw
        new = _floor_frame(frame)
        return raw if new is frame else json.dumps(new, ensure_ascii=False).encode("utf-8")
    lines_out, touched = [], False
    for line in text.splitlines():
        if line.startswith("data:"):
            payload_text = line[5:].strip()
            try:
                frame = json.loads(payload_text)
            except ValueError:
                lines_out.append(line)
                continue
            if isinstance(frame, dict):
                new = _floor_frame(frame)
                if new is not frame:
                    touched = True
                    line = "data: " + json.dumps(new, ensure_ascii=False)
        lines_out.append(line)
    if not touched:
        return raw
    return ("\n".join(lines_out) + "\n").encode("utf-8")


class FloorServer(ThreadingHTTPServer):
    """One owner per port, enforced.

    allow_reuse_address MUST stay False on Windows. There SO_REUSEADDR does not
    mean "reuse a socket in TIME_WAIT", it means "bind a port somebody else is
    already serving", so the default True lets a second proxy bind 8766 without
    an error: two instances, both answering, half the traffic floored by the
    other half's older threshold. Measured 2026-10-30 -- duplicates accumulated
    every time the keep-warm loop raced, and nothing ever failed.
    """
    allow_reuse_address = False
    daemon_threads = True


def _session_headers(headers):
    """Upstream response headers the MCP client MUST see.

    Streamable HTTP hands the client an `Mcp-Session-Id` on initialize and
    expects it back on every later request. This proxy echoed only
    Content-Type and Content-Length, so the daemon answered every follow-up
    call `400 Missing session ID` -- the floor was live but unusable through
    :8766. Every `mcp-*` header is forwarded rather than the one name today,
    so a protocol header added later does not reopen the same hole.
    """
    if headers is None:
        return []
    return [(k, v) for k, v in headers.items()
            if k.lower().startswith("mcp-")]


class FloorProxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep stdout clean for the client
        print("[floor-proxy] " + (fmt % args), file=sys.stderr, flush=True)

    def _refuse(self, reason):
        """Answer 401 with a NAMED reason, without touching the upstream.

        The daemon's own 401 says `{"error": "unauthorized"}` and nothing
        else, which cannot distinguish "your token is wrong" from "your token
        is a month old" from "there is no token at all". The proxy is the
        component that KNOWS which, so it is the component that says so.
        """
        body = json.dumps({"error": "unauthorized", "reason": reason},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        print("[floor-proxy] refused: %s" % reason, file=sys.stderr, flush=True)

    def _forward(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "content-length", "connection")}
        # The daemon's token is resolved FROM DISK HERE, per request, not
        # copied into any client's config. Measured 2026-11-11: opencode's and
        # kilo's configs held a token that had been rotated out from under
        # them, so the client was told `needs authentication` while the daemon
        # was healthy and serving 30 tools. A token the server can rotate must
        # never be COPIED into a client -- a copy is a promise the server never
        # made. See tools/pmb_token.py for the full contract.
        #
        # In "strict" mode the client's own credential is checked first, and a
        # missing or stale one is refused HERE with a named reason -- the
        # daemon would answer 401 too, but its answer says "unauthorized"
        # without saying WHICH of the two problems it was.
        if AUTH_MODE == "strict":
            presented = self.headers.get("Authorization")
            if not _token_matches(presented):
                self._refuse("unauthorized: the presented token is not the "
                             "live daemon token (rotate it, or set "
                             "mcp_stack.floor_auth_mode to 'inject')")
                return
        headers = _upstream_headers(headers)
        req = urllib.request.Request(UPSTREAM, data=body, headers=headers,
                                     method=method)
        extra = []
        deadline = time.monotonic() + UPSTREAM_WAIT
        waited = False
        while True:
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    raw = resp.read()
                    ctype = resp.headers.get("Content-Type", "application/json")
                    status = resp.status
                    extra = _session_headers(resp.headers)
                break
            except urllib.error.HTTPError as exc:
                raw = exc.read() or b""
                ctype = exc.headers.get("Content-Type", "application/json")
                status = exc.code
                extra = _session_headers(exc.headers)
                break
            except Exception as exc:  # daemon down: loud, never silent
                # Only a refused/unreachable connection is retried: the daemon
                # is still binding. A real HTTP error was handled above, so this
                # cannot hide a broken upstream.
                retryable = isinstance(exc, (urllib.error.URLError, OSError))
                if retryable and time.monotonic() < deadline:
                    if not waited:
                        print("[floor-proxy] waiting up to %.0fs for upstream %s"
                              % (UPSTREAM_WAIT, UPSTREAM),
                              file=sys.stderr, flush=True)
                        waited = True
                    time.sleep(0.25)
                    continue
                msg = ("pmb floor proxy: daemon unreachable at %s (%s: %s)"
                       % (UPSTREAM, type(exc).__name__, exc))
                print("[floor-proxy] " + msg, file=sys.stderr, flush=True)
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                data = msg.encode("utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        if method == "POST" or "json" in ctype or "event-stream" in ctype:
            raw = transform_body(raw, ctype)
        self.send_response(status)
        for name, value in extra:
            self.send_header(name, value)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def do_POST(self):
        self._forward("POST")

    def do_GET(self):
        if self.path == "/healthz":
            self._healthz()
            return
        self._forward("GET")

    def _healthz(self):
        """Serve counters + thresholds locally; never forwards to the daemon.

        A health probe must answer even when upstream is cold -- that is the
        state the probe exists to observe -- so this path is handled here and
        never touches UPSTREAM.
        """
        body = json.dumps(health_payload(), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        self._forward("DELETE")


def main() -> int:
    print("[floor-proxy] listening on 127.0.0.1:%d -> %s (row floor %.2f, "
          "query floor %.2f, started by %s)" % (PORT, UPSTREAM, FLOOR, MIN_BEST,
                                               ORIGIN),
          file=sys.stderr, flush=True)
    try:
        server = FloorServer(("127.0.0.1", PORT), FloorProxy)
    except OSError as exc:
        print("[floor-proxy] 127.0.0.1:%d is already owned (%s); this instance "
              "exits rather than double-binding" % (PORT, exc),
              file=sys.stderr, flush=True)
        return 0
    stamp_runtime()
    try:
        server.serve_forever()
    except KeyboardInterrupt as exc:
        note_failure("tools/pmb_floor_proxy.py", exc)
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
