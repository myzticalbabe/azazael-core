#!/usr/bin/env python
# coding: utf-8
"""Session start: pull memory context into this session (one command).

Usage: python tools/session_start.py ["what I am about to do"] [--strict]

Prints PMB context (via the .pmb-venv client) and Engram context (via the
engram binary), so the single pushed session-start instruction delivers
both memory stores instead of PMB alone. It then prints ONE summary line
stating how many characters each store actually delivered.
Runs under ANY interpreter. Exit 0 by default -- context is best-effort and a
session must never be blocked by a memory daemon. `--strict` is the opt-in
gate: it exits 1 when any store delivered nothing, when the MCP gate reports
FAIL/BLOCKED, or when the supervisor load guard reports a BROKEN client config
(its exit=2). A guard exit=1 deliberately does NOT gate -- it means "client not
yet restarted", a remedy a running session cannot perform.

Why this file no longer just "continues without context" -- measured 2026-10-14
-----------------------------------------------------------------------------
It reported `PMB unreachable` and `engram unreachable` and exited 0 while BOTH
stores had in fact answered. stdout is cp1252 on this host; PMB's payload
carried a raw byte that decoded to U+FFFD and engram's carried U+2192, so the
`print()` raised UnicodeEncodeError INSIDE the per-store `except Exception`,
which then relabelled an OUTPUT failure as a CONNECTION failure. No context was
delivered and rc was 0 -- a false green on the mandated step 1 of the memory
protocol (recorded as engram #706).

So two things that used to be fused are now kept apart:

  * the payload is written through `_emit()`, which degrades a character the
    console cannot encode instead of losing the store's whole payload;
  * each store returns the number of characters it delivered, and `main()`
    prints one summary line naming every store's status. A store that
    delivered nothing is reported as such, with its reason -- never as a pass.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tools.defaults_profile import read_default

# Force UTF-8 and degrade unencodable characters: the default Windows console is
# cp1252 and cannot encode either store's payload. Estate convention -- the same
# block sits in tools/verify_hashes.py and scripts/env_drift_alarm.py.
_RECONFIGURE_ERROR = None
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError) as exc:
        # Not fatal: `_emit()` sanitizes against whatever encoding survives.
        # Recorded rather than discarded so the degraded case stays visible.
        _RECONFIGURE_ERROR = "%s: %s" % (type(exc).__name__, exc)

HERE = Path(__file__).resolve().parent
PMB_PY = Path.home() / ".pmb-venv" / "Scripts" / "python.exe"
CLIENT = HERE / "pmb_client.py"
ENGRAM_PROJECT = os.environ.get("ENGRAM_PROJECT", "azazael")
ENGRAM_BIN = os.environ.get("ENGRAM_BIN") or shutil.which("engram") or str(
    Path.home() / "go" / "bin" / "engram.exe"
)
PAMS_PY315T = Path("C:/Azazael/.venvs/py315t/Scripts/python.exe")

# Read runtime config from defaults
RUNTIME_INTERPRETER = read_default("runtime.interpreter", "py316t")
RUNTIME_VENV_PATH = read_default("runtime.venv_path", "C:/Azazael/.venvs/py316t")
RUNTIME_PYTHON_EXE = read_default("runtime.python_exe", "C:/Azazael/.venvs/py316t/Scripts/python.exe")
RUNTIME_GIL_SIDECAR = read_default("runtime.gil_sidecar", True)
RUNTIME_FREETHREADED_REQUIRED = read_default("runtime.freethreaded_required", True)


def _emit(text: str) -> int:
    """Write `text`, degrading characters the console cannot encode.

    Returns the number of characters delivered. Degrading one character is
    strictly better than losing a store's entire payload, and returning the
    count is what lets `main()` report delivered-vs-failed instead of guessing.

    The fallback re-encodes through the console's OWN codec: a UTF-8 round-trip
    would preserve the unprintable character and fail again (the trap
    tools/technique_evidence.py documents). Nothing here is allowed to raise,
    because an output failure raised here would be caught by a store's call
    handler and reported as that store being unreachable -- the defect below.
    """
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    safe = text
    try:
        text.encode(enc)
    except UnicodeEncodeError:
        safe = text.encode(enc, "replace").decode(enc, "replace")
    except (LookupError, ValueError):
        safe = text.encode("utf-8", "replace").decode("utf-8", "replace")
    print(safe)
    return len(safe)


def pmb_context(topic: str) -> "tuple[int, str]":
    """Deliver the PMB context. Returns (chars_delivered, status)."""
    if not PMB_PY.exists() or not CLIENT.exists():
        unavailable = (0, "client unavailable (%s)" % CLIENT)
        return unavailable
    try:
        r = subprocess.run(
            [str(PMB_PY), str(CLIENT), "prepare", topic],
            timeout=90, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(HERE.parent))
    except Exception as exc:  # noqa: BLE001 -- any call failure is a result here
        failure = (0, "call failed (%s: %s)" % (type(exc).__name__, exc))
        return failure
    body = (r.stdout or "").strip()
    if not body:
        empty = (0, "no context returned (rc=%s)" % r.returncode)
        return empty
    # Emitted OUTSIDE the call's try: an output failure is not an unreachable
    # store, and only this separation keeps the two from being confused again.
    return _emit(body[-3000:]), "ok"


def engram_context() -> "tuple[int, str]":
    """Deliver the engram context. Returns (chars_delivered, status)."""
    if not (Path(ENGRAM_BIN).exists() or shutil.which(ENGRAM_BIN)):
        missing = (0, "binary not found (%s)" % ENGRAM_BIN)
        return missing
    try:
        r = subprocess.run(
            [ENGRAM_BIN, "context", ENGRAM_PROJECT],
            timeout=60, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(HERE.parent))
    except Exception as exc:  # noqa: BLE001 -- any call failure is a result here
        failure = (0, "call failed (%s: %s)" % (type(exc).__name__, exc))
        return failure
    out = (r.stdout or "").strip()
    if not out:
        empty = (0, "no context returned (rc=%s)" % r.returncode)
        return empty
    # Head, not tail: engram lists newest-first, so the tail is the
    # oldest/least-relevant material. Cap only if it grows large.
    if len(out) > 12000:
        text = out[:12000] + "\n... [engram context truncated]"
    else:
        text = out
    # Emitted OUTSIDE the call's try, for the same reason as pmb_context().
    return _emit(text), "ok"


def mcp_guard() -> str:
    """Ensure the engram MCP config is loadable, and report the restart verdict.

    Runs at session start so the config is guaranteed HEALTHY before the next
    client start - MCP servers resolve at process start, so nothing done here
    can affect the current session. Full detail (including every error) is
    appended to azazael/state/engram_mcp_restart_log.jsonl.

    Returns a short status string for the summary line; never returns None, so
    "the guard did not run" cannot look like "the guard ran and passed".
    """
    guard = HERE / "verify_engram_mcp.py"
    if not guard.exists():
        return "guard-missing"
    try:
        r = subprocess.run(
            [sys.executable, str(guard)],
            timeout=180, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(HERE.parent))
    except Exception as exc:  # noqa: BLE001 -- report, do not lose the session
        status = "unrunnable (%s)" % type(exc).__name__
        _emit("session_start: MCP guard could not run (%s: %s)"
              % (type(exc).__name__, exc))
        return status

    out = (r.stdout or "").strip()
    warn = [l.strip() for l in out.splitlines() if l.strip().startswith("!!")]
    tail = out.split("RESULT")[-1] if "RESULT" in out else out[-600:]
    _emit("engram MCP guard: exit=%s (0=loaded, 1=needs client restart, 2=errors)"
          % r.returncode)
    for w in warn:
        _emit("  " + w)
    for l in tail.splitlines():
        s = l.strip()
        if s and "====" not in s:
            _emit("  " + s)
    return "exit=%s" % r.returncode


def pams_capture_start(topic: str) -> "tuple[int, str]":
    """Phase-3 (#380) session-start capture into PAMS conversations.db.

    The conversation pipeline has been dead since 2026-05-27 (supervisor:
    "pams conversation capture dead (0 messages)"); the writer was the
    Codebuff client, so nothing records since that client left. Delegating
    to tools/pams_capture.py on the pinned 3.15t interpreter keeps this
    script runnable under ANY interpreter while the capture uses
    persistent_ai_memory (3.15t-installed). Advisory: capture must never
    block or fail a protocol step, so an unavailable capturer or a
    skip-for-reason result is reported, never raised.

    Returns (chars_delivered, status) for the summary line. Output is
    emitted OUTSIDE the call's try, per the #706 separation (an output
    failure is not a capture failure).
    """
    cli = HERE / "pams_capture.py"
    if not (PAMS_PY315T.exists() and cli.exists()):
        return (0, "unavailable (%s)" % (
            "3.15t interpreter missing" if not PAMS_PY315T.exists()
            else "capturer module missing"))
    try:
        r = subprocess.run(
            [str(PAMS_PY315T), str(cli), "open", topic],
            timeout=60, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(HERE.parent))
    except Exception as exc:  # noqa: BLE001 - any call failure is a result here
        return (0, "call failed (%s: %s)" % (type(exc).__name__, exc))
    out = (r.stdout or "").strip()
    if not out:
        return (0, "no response (rc=%s)" % r.returncode)
    # Emitted OUTSIDE the call's try, for the same reason as pmb_context().
    if ">stored " in out:
        return _emit(out[-500:]), "ok"
    # disabled / skipped / error carry their named reason verbatim in the
    # summary line; only a successful store counts as delivered context.
    note = out.replace("\n", " ").replace("pams capture open>", "").strip()
    return (0, note[:80] or ("rc=%s" % r.returncode))


MCP_GATE = Path("C:/Azazael/azazael/tools/mcp_startup_gate.py")


def mcp_gate(fix: bool = True) -> str:
    """Run the standing MCP gate. Returns a one-word status for the summary line.

    This is the FIRST thing a session does, before any task work, because the
    failures it catches are silent: a stale stdio child serving old code, a
    duplicate floor proxy splitting traffic across two thresholds, or a lane
    wired straight at the daemon so its recall has no floor at all. None of
    those raise an error anywhere -- they just make answers wrong.

    `--fix` repairs what is safe to repair (daemon, proxy, duplicates). It never
    touches pams or supervisor: those are the client's stdio children, and
    killing one breaks the live session. A stale child is reported as BLOCKED
    with the only remedy that works, restarting the client.
    """
    if not MCP_GATE.exists():
        _emit("gate: MISSING %s" % MCP_GATE)
        return "gate=missing"
    cmd = [sys.executable, str(MCP_GATE), "--quiet"]
    if fix:
        cmd.append("--fix")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=240)
    except Exception as exc:  # never block startup on the gate itself
        _emit("gate: could not run (%s: %s)" % (type(exc).__name__, exc))
        return "gate=error"
    for line in (proc.stdout or "").strip().splitlines():
        _emit(line)
    for line in (proc.stderr or "").strip().splitlines():
        _emit("gate-stderr: " + line)
    tail = [l for l in (proc.stdout or "").splitlines() if l.startswith("gate:")]
    return (tail[-1] if tail else "gate=no-summary")


def supervisor_status() -> str:
    """Unified verdict at session open (advisory here; enforcement is downstream).

    Runs the supervisor on the pinned 3.15t interpreter (it needs FastMCP).
    Emits the trailing VERDICT line plus the per-leg lines, returns a short
    status for the summary. Never returns None; never raises.
    """
    py315t = Path("C:/Azazael/.venvs/py315t/Scripts/python.exe")
    script = HERE / "supervisor_mcp.py"
    if not py315t.exists() or not script.exists():
        _emit("supervisor: unavailable (interpreter or script missing)")
        return "unavailable"
    try:
        r = subprocess.run(
            [str(py315t), str(script), "--verdict"],
            timeout=120, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(HERE.parent))
    except Exception as exc:  # noqa: BLE001 -- report, do not lose the session
        _emit("supervisor: unrunnable (%s)" % type(exc).__name__)
        return "unrunnable"
    out = (r.stdout or "").strip()
    if not out:
        _emit("supervisor: empty response (rc=%s)" % r.returncode)
        return "empty"
    for line in out.splitlines()[-9:]:
        _emit(line)
    verdict = [l for l in out.splitlines() if l.startswith("VERDICT:")]
    return verdict[-1].split(":", 1)[1].strip().split(" - ")[0] if verdict else "unparsed"


def supervisor_load() -> str:
    """Report whether the supervisor will drive THIS session (restart guard).

    Config-without-load is the recurring failure: the entry sits in mcp.json
    while the client never restarted, so no supervisor tool exists in this
    session. Runs verify_supervisor_load.py and emits its lines; returns the
    worst state. Never returns None; never raises.
    """
    guard = HERE / "verify_supervisor_load.py"
    if not guard.exists():
        _emit("supervisor load: guard missing")
        return "guard-missing"
    try:
        r = subprocess.run(
            [sys.executable, str(guard)],
            timeout=180, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(HERE.parent))
    except Exception as exc:  # noqa: BLE001 -- report, do not lose the session
        _emit("supervisor load: unrunnable (%s)" % type(exc).__name__)
        return "unrunnable"
    for line in (r.stdout or "").strip().splitlines():
        _emit(line)
    return "exit=%s" % r.returncode


def _load_config_broken(load_status: str) -> bool:
    """True only when the load guard reported a BROKEN config (its exit 2).

    The guard's exit contract splits two states that must never be fused:

      exit=1  config healthy, load not yet observed -- almost always "the
              client has not been restarted". The remedy is a client restart,
              which a RUNNING session cannot perform, so gating on it would
              stop work for a reason the session cannot clear.
      exit=2  a client config is missing the supervisor entry or is
              unreadable. That is hard misconfiguration -- the same class the
              MCP gate's FAIL/BLOCKED already fails --strict on.

    Anything else ("exit=0", "unrunnable", "guard-missing") is not evidence
    of a broken config, so it must not gate either.
    """
    return load_status.strip() == "exit=2"


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in args
    args = [a for a in args if a != "--strict"]
    topic = args[0] if args else "general Azazael work"

    if _RECONFIGURE_ERROR is not None:
        _emit("session_start: stdout left un-reconfigured (%s); payloads now "
              "degrade to the console codec instead of failing" % _RECONFIGURE_ERROR)

    _emit("=== MCP STARTUP GATE (standing order: before any task work) ===")
    gate_status = mcp_gate(fix="--no-fix" not in args)
    _emit("")
    if "FAIL=" in gate_status or "BLOCKED=" in gate_status:
        _emit("session_start: the MCP stack is NOT clean -- see the gate lines "
              "above and fix them before trusting any recall or tool answer")
    _emit("=== PMB ===")
    pmb_chars, pmb_status = pmb_context(topic)
    _emit("")
    _emit("=== ENGRAM (%s) ===" % ENGRAM_PROJECT)
    eng_chars, eng_status = engram_context()
    _emit("")
    _emit("=== ENGRAM MCP (restart guard) ===")
    guard_status = mcp_guard()
    _emit("")
    _emit("=== SUPERVISOR (unified verdict) ===")
    sup_status = supervisor_status()
    _emit("")
    _emit("=== SUPERVISOR LOAD (post-restart drive) ===")
    load_status = supervisor_load()
    _emit("")
    _emit("=== PAMS CAPTURE (session start, #380) ===")
    pams_chars, pams_status = pams_capture_start(topic)

    # The whole point of the summary line: a reader can tell "delivered
    # everything" from "delivered nothing" without parsing the payload.
    _emit("session_start: delivered PMB=%dch (%s) engram=%dch (%s) mcp_guard=%s supervisor=%s load=%s pams_capture=%s %s"
          % (pmb_chars, pmb_status, eng_chars, eng_status, guard_status,
             sup_status, load_status, pams_status, gate_status))

    absent = [name for name, chars in (("PMB", pmb_chars), ("engram", eng_chars))
              if chars == 0]
    if absent:
        _emit("session_start: NO CONTEXT from %s -- do not treat this session as "
              "memory-loaded" % ", ".join(absent))
    if strict and absent:
        return 1
    if strict and ("FAIL=" in gate_status or "BLOCKED=" in gate_status):
        return 1
    if strict and _load_config_broken(load_status):
        _emit("session_start: STRICT -- the supervisor load guard reports a "
              "broken client config (%s): a client is missing the supervisor "
              "entry or its config is unreadable. Fix that config, then "
              "restart the client." % load_status)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
