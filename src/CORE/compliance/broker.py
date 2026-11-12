"""
RULE 0X-2: Runtime Broker Interception

Intercepts Codebuff tool calls (write_file, str_replace, basher) and enforces
the PRE-ACTION GATE programmatically. Ensures that the External Compliance
Auditor (ECA) is not halted, the agent has explicitly asserted the gate via
gate_asserted(), and the ComplianceStateMachine is in CODE_EDIT state.

Architecture:
  - gate_asserted(): Bridge between text gate output and programmatic enforcement.
    The agent MUST call this when it prints the PRE-ACTION GATE in response text.
  - enforce_gate(): Decorator for write_file/str_replace/basher that checks gate
    passage, ECA halt status, and state machine state before allowing execution.
  - advance_to_code_edit(): Auto-advances state machine through required states
    (GATE_ASSERT -> GEMINI_SPAWN -> CODE_EDIT) for tasks that skip Gemini.

Usage:
    from CORE.compliance.broker import gate_asserted, enforce_gate, gate_passed

    # Agent prints PRE-ACTION GATE, then calls:
    gate_asserted("AP-1: user asked for X. GATE DECISION: PROCEED.")

    # Tool functions are decorated:
    @enforce_gate("write_file")
    def write_file(path, content):
        ...

    # Check if gate has been passed:
    if not gate_passed():
        raise GateViolationError("PRE-ACTION GATE not passed")

Thread-safe: All state mutations use RLock for 3.14t free-threading compatibility.
"""

import functools
import json
import sys
import threading
import time
from pathlib import Path
# from typing import Optional  # removed — all names have native equivalents

from CORE.compliance.state_machine import (
    ComplianceStateMachine,
    ComplianceState,
    StateMachineError,
)
from CORE.compliance_auditor import get_eca
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Optional GateBash import for basher command interception
try:
    _scripts_path = Path(__file__).resolve().parent.parent.parent / "SCRIPTS"
    if str(_scripts_path.parent) not in sys.path:
        sys.path.insert(0, str(_scripts_path.parent))
    from SCRIPTS.gate_bash import GateBash
    _GATE_BASH_AVAILABLE = True
    _GATE_BASH_ERR = ""
except ImportError as _gb_e:
    note_failure("broker:L58", _gb_e)
    _GATE_BASH_AVAILABLE = False
    _GATE_BASH_ERR = str(_gb_e)
    GateBash = None  # type: ignore

# Cached GateBash instance — created once per session to avoid
# spinning up a fresh ComplianceOrchestrator on every basher call.
_gate_bash_instance = None

# Optional wrapt for signature-preserving decoration
try:
    import wrapt
    HAS_WRAPT = True
except ImportError as exc:
    note_failure("broker:L71", exc)
    HAS_WRAPT = False


# ---------------------------------------------------------------------------
# Gate Violation Exception
# ---------------------------------------------------------------------------

class GateViolationError(Exception):
    """Raised when a tool is called before passing the PRE-ACTION GATE,
    while the ECA is halted, or when the state machine is in an invalid state.

    This exception makes compliance enforcement IMPOSSIBLE TO BYPASS.
    Unlike returning False (which the agent can ignore), raising an
    exception forces the calling code to either handle it or crash.
    """


# ---------------------------------------------------------------------------
# Module-Level Gate State (Thread-Safe)
# ---------------------------------------------------------------------------

_gate_lock = threading.RLock()
_gate_is_asserted: bool = False
_gate_assertion_text: str = ""
_gate_assertion_count: int = 0
_global_sm = ComplianceStateMachine()

# Track which tool functions have been decorated for reporting
_decorated_tools: dict = {}


# ---------------------------------------------------------------------------
# State Machine Access
# ---------------------------------------------------------------------------

def get_state_machine() -> ComplianceStateMachine:
    """Access the global broker state machine singleton.

    The state machine enforces the strict workflow sequence:
    IDLE -> GATE_ASSERT -> GEMINI_SPAWN -> CODE_EDIT -> CODE_REVIEW -> SYNC -> IDLE

    Returns:
        The global ComplianceStateMachine instance
    """
    return _global_sm


# ---------------------------------------------------------------------------
# Gate Assertion Bridge (Text -> Code Enforcement)
# ---------------------------------------------------------------------------

def gate_asserted(assertion_text: str) -> bool:
    """Bridge between text gate output and programmatic enforcement.

    The agent MUST call this function when it prints the PRE-ACTION GATE
    in its response text. This is the critical link between the honor-system
    text gate and the programmatic enforcement that RULE 0X requires.

    Transitions the state machine from IDLE to GATE_ASSERT (or from HALTED
    back to GATE_ASSERT for resume).

    Args:
        assertion_text: The full text of the PRE-ACTION GATE assertion block

    Returns:
        True if the gate was successfully asserted

    Raises:
        StateMachineError: If the transition is illegal
    """
    global _gate_is_asserted, _gate_assertion_text, _gate_assertion_count

    with _gate_lock:
        if _global_sm.state == ComplianceState.IDLE:
            _global_sm.transition_to(ComplianceState.GATE_ASSERT, reason="Gate asserted by agent")
        elif _global_sm.state == ComplianceState.HALTED:
            _global_sm.transition_to(ComplianceState.GATE_ASSERT, reason="Resuming from halt — gate re-asserted")

        _global_sm.assert_gate(assertion_text)
        _gate_is_asserted = True
        _gate_assertion_text = assertion_text
        _gate_assertion_count += 1

    return True


# ---------------------------------------------------------------------------
# State Machine Advancement
# ---------------------------------------------------------------------------

def advance_to_code_edit() -> bool:
    """Advances the state machine through required intermediate states.

    Transitions: GATE_ASSERT -> GEMINI_SPAWN -> CODE_EDIT

    This is called automatically by the @enforce_gate decorator when
    a tool function is invoked and the state machine is in GATE_ASSERT
    or GEMINI_SPAWN state. For tasks where Gemini is not required,
    this auto-advances through GEMINI_SPAWN to reach CODE_EDIT.

    Returns:
        True if advancement succeeded

    Raises:
        GateViolationError: If advancement fails due to illegal transition
    """
    with _gate_lock:
        try:
            if _global_sm.state == ComplianceState.GATE_ASSERT:
                _global_sm.transition_to(ComplianceState.GEMINI_SPAWN,
                                         reason="Auto-advance: gate passed, proceeding to spawn")
                _global_sm.transition_to(ComplianceState.CODE_EDIT,
                                         reason="Auto-advance: spawn complete, entering code edit")
            elif _global_sm.state == ComplianceState.GEMINI_SPAWN:
                _global_sm.transition_to(ComplianceState.CODE_EDIT,
                                         reason="Auto-advance: spawn complete, entering code edit")
            return True
        except StateMachineError as e:
            raise GateViolationError(
                f"Failed to advance state machine from '{_global_sm.state.name}' "
                f"to CODE_EDIT: {e}"
            ) from e


def advance_to_review() -> bool:
    """Advance state machine from CODE_EDIT to CODE_REVIEW.

    Call this after completing code changes and before spawning
    the code reviewer.

    Returns:
        True if advancement succeeded
    """
    with _gate_lock:
        try:
            if _global_sm.state == ComplianceState.CODE_EDIT:
                _global_sm.transition_to(ComplianceState.CODE_REVIEW,
                                         reason="Code changes complete, entering review")
            return True
        except StateMachineError as e:
            raise GateViolationError(f"Failed to advance to CODE_REVIEW: {e}") from e


def advance_to_idle() -> bool:
    """Advance state machine through REVIEW and SYNC back to IDLE.

    Call this when a task is complete to reset for the next task cycle.

    Returns:
        True if advancement succeeded
    """
    with _gate_lock:
        try:
            if _global_sm.state == ComplianceState.CODE_REVIEW:
                _global_sm.transition_to(ComplianceState.SYNC,
                                         reason="Review complete, syncing")
                _global_sm.transition_to(ComplianceState.IDLE,
                                         reason="Sync complete, returning to idle")
            elif _global_sm.state == ComplianceState.SYNC:
                _global_sm.transition_to(ComplianceState.IDLE,
                                         reason="Sync complete, returning to idle")
            return True
        except StateMachineError as e:
            raise GateViolationError(f"Failed to advance to IDLE: {e}") from e


# ---------------------------------------------------------------------------
# Gate Status Queries
# ---------------------------------------------------------------------------

def gate_passed() -> bool:
    """Check if the PRE-ACTION GATE has been programmatically passed.

    Returns:
        True if gate_asserted() has been called since last reset
    """
    with _gate_lock:
        return _gate_is_asserted


def get_gate_assertion_text() -> str:
    """Get the text of the last gate assertion."""
    with _gate_lock:
        return _gate_assertion_text


def get_gate_assertion_count() -> int:
    """Get the number of times gate_asserted() has been called."""
    with _gate_lock:
        return _gate_assertion_count


def can_edit() -> bool:
    """Check if the current state allows code edits.

    Returns:
        True if gate is passed AND state machine is in CODE_EDIT or can advance to it
    """
    with _gate_lock:
        if not _gate_is_asserted:
            return False
        if _global_sm.state in (ComplianceState.CODE_EDIT,):
            return True
        if _global_sm.state in (ComplianceState.GATE_ASSERT, ComplianceState.GEMINI_SPAWN):
            return True  # Can auto-advance
        return False


# ---------------------------------------------------------------------------
# Gate Reset (for testing and new task cycles)
# ---------------------------------------------------------------------------

def reset_gate() -> None:
    """Reset the gate state for testing or new task cycles.

    Clears the gate assertion flag and resets the state machine to IDLE.
    Does NOT reset the assertion count (cumulative).
    """
    global _gate_is_asserted, _gate_assertion_text

    with _gate_lock:
        _gate_is_asserted = False
        _gate_assertion_text = ""
        try:
            _global_sm.reset()
        except StateMachineError as exc:
            note_failure("broker:L297", exc)


# ---------------------------------------------------------------------------
# Worm Health Check (Matrix Integration)
# ---------------------------------------------------------------------------

# Thresholds for worm health gating
WORM_HEALTH_WARN_THRESHOLD: float = 90.0   # warn if pass rate drops below this
WORM_HEALTH_HALT_THRESHOLD: float = 40.0   # halt if pass rate drops below this
WORM_HEALTH_CACHE_TTL: float = 60.0        # seconds between matrix re-reads
_worm_health_cache: (dict | None) = None
_worm_health_cache_time: float = 0.0


def _resolve_matrix_path() -> Path:
    """Resolve the worm_execution_matrix.json path from broker's location."""
    # broker.py is at azazael/src/CORE/compliance/broker.py
    # Matrix JSON is at azazael/worm_execution_matrix.json
    broker_dir = Path(__file__).resolve().parent  # .../CORE/compliance
    azazael_dir = broker_dir.parent.parent.parent  # azazael/
    return azazael_dir / "worm_execution_matrix.json"


def check_worm_health() -> dict:
    """Check worm_execution_matrix.json for worm health status.

    Reads the most recent execution matrix and returns a health report.
    Wired into @enforce_gate as Check 1b to prevent code edits when
    the worm ecosystem is unhealthy.

    Results are cached for 60 seconds to avoid re-reading the JSON
    on every tool call. Thread-safe via _gate_lock.

    Returns:
        dict with: healthy (bool), status (ok|warn|critical|unknown),
        pass_rate, total, ok, failed, message, matrix_exists, halt_reason
    """
    global _worm_health_cache, _worm_health_cache_time

    now = time.time()
    with _gate_lock:
        if _worm_health_cache is not None and (now - _worm_health_cache_time) < WORM_HEALTH_CACHE_TTL:
            return dict(_worm_health_cache)  # Return copy to prevent mutation

    # Build result outside lock (file I/O, JSON parse)
    result = _build_worm_health()

    with _gate_lock:
        _worm_health_cache = dict(result)  # Store copy to prevent mutation
        _worm_health_cache_time = now

    return result


def _build_worm_health() -> dict:
    """Internal: parse worm_execution_matrix.json into health dict."""
    result = {
        "healthy": True,
        "status": "ok",
        "pass_rate": 100.0,
        "total": 0,
        "ok": 0,
        "failed": 0,
        "message": "Worm health: OK",
        "matrix_exists": False,
        "halt_reason": "",
    }

    matrix_path = _resolve_matrix_path()
    if not matrix_path.exists():
        result["status"] = "unknown"
        result["message"] = "Worm health: UNKNOWN (no matrix — run _worm_execution_matrix.py)"
        return result

    try:
        with open(matrix_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        summary = data.get("summary", {})
        total = summary.get("total_worms", 0)
        ok = summary.get("execute_ok", 0)
        failed = summary.get("failed", 0)
        pass_rate = summary.get("pass_rate_pct", 100.0)

        result["total"] = total
        result["ok"] = ok
        result["failed"] = failed
        result["pass_rate"] = pass_rate
        result["matrix_exists"] = True

        if pass_rate < WORM_HEALTH_HALT_THRESHOLD:
            result["healthy"] = False
            result["status"] = "critical"
            result["halt_reason"] = (
                f"Worm pass rate {pass_rate}% below halt threshold "
                f"{WORM_HEALTH_HALT_THRESHOLD}%"
            )
            result["message"] = (
                f"Worm health: CRITICAL — {ok}/{total} worms passed "
                f"({pass_rate}%). Run _worm_execution_matrix.py to diagnose."
            )
        elif pass_rate < WORM_HEALTH_WARN_THRESHOLD:
            result["healthy"] = False
            result["status"] = "warn"
            result["halt_reason"] = ""
            result["message"] = (
                f"Worm health: WARNING — {ok}/{total} worms passed "
                f"({pass_rate}%). Proceed with caution."
            )
        else:
            result["healthy"] = True
            result["status"] = "ok"
            result["halt_reason"] = ""
            result["message"] = f"Worm health: OK — {ok}/{total} worms passed ({pass_rate}%)"

    except (json.JSONDecodeError, OSError) as e:
        note_failure("broker:L413", e)
        result["status"] = "unknown"
        result["healthy"] = True
        result["halt_reason"] = ""
        result["message"] = f"Worm health: ERROR reading matrix — {e}"

    return result


# ---------------------------------------------------------------------------
# @enforce_gate Decorator (Tool Call Interception)
# ---------------------------------------------------------------------------

def enforce_gate(action_name: str = ""):
    """Decorator that enforces the compliance gate before tool execution.

    Wraps Codebuff tool functions (write_file, str_replace, basher) and
    checks three conditions before allowing execution:

    1. ECA HALT CHECK: Is the External Compliance Auditor halted?
       If so, raises GateViolationError — no tool calls allowed during halt.

    2. GATE ASSERTION CHECK: Has gate_asserted() been called?
       The agent must call gate_asserted() when it prints the PRE-ACTION GATE.
       Without this, the programmatic enforcement has no way to know the gate
       was printed.

    3. STATE MACHINE CHECK: Is the state machine in CODE_EDIT state?
       If in GATE_ASSERT or GEMINI_SPAWN, auto-advances to CODE_EDIT.
       If in any other state, raises GateViolationError.

    Uses wrapt for signature-preserving decoration when available.
    Falls back to functools.wraps for basic decoration.

    Args:
        action_name: Human-readable action name for error messages.
                     Defaults to the decorated function's __name__.

    Usage:
        @enforce_gate("write_file")
        def write_file(path, content):
            ...

        @enforce_gate("str_replace")
        def str_replace(path, replacements):
            ...

        @enforce_gate("basher")
        def basher(command):
            ...

    Raises:
        GateViolationError: If any of the three checks fail
    """
    def decorator(wrapped=None, wrapper=None, instance=None):
        actual_action = action_name or getattr(wrapped, "__name__", "unknown_action")

        def gate_wrapper(func, *args, **kwargs):
            # --- Check 1: ECA Halt ---
            eca = get_eca()
            if eca.is_halted():
                raise GateViolationError(
                    f"[COMPLIANCE BROKER] Action '{actual_action}' BLOCKED: "
                    f"External Compliance Auditor is HALTED. "
                    f"Reason: {eca.get_halt_reason()}"
                )

            # --- Check 1b: Worm Health ---
            health = check_worm_health()
            if health["status"] == "critical":
                raise GateViolationError(
                    f"[COMPLIANCE BROKER] Action '{actual_action}' BLOCKED: "
                    f"Worm ecosystem is UNHEALTHY. {health['halt_reason']}. "
                    f"Run: python azazael/_worm_execution_matrix.py"
                )
            elif health["status"] == "warn":
                print(
                    f"[COMPLIANCE BROKER] WARNING: {health['message']}",
                    file=sys.stderr, flush=True
                )

            # --- Check 2: Gate Assertion ---
            if not gate_passed():
                raise GateViolationError(
                    f"[COMPLIANCE BROKER] Action '{actual_action}' BLOCKED: "
                    f"PRE-ACTION GATE has not been passed. "
                    f"You MUST call gate_asserted() with the gate assertion text "
                    f"before invoking any code-modifying tool."
                )

            # --- Check 2b: Basher command gating (RULE 0Z-C) ---
            command = str(args[0]) if args else str(kwargs.get("command", ""))
            if _GATE_BASH_AVAILABLE and actual_action == "basher" and args:
                global _gate_bash_instance
                if _gate_bash_instance is None:
                    _gate_bash_instance = GateBash()  # pyright: ignore[reportOptionalCall]
                gb = _gate_bash_instance
                verdict = gb.classify(command)
                if verdict["destructive"]:
                    ok, msg = gb.gate(command)
                    if not ok:
                        raise GateViolationError(
                            f"[COMPLIANCE BROKER] Basher command '{actual_action}' BLOCKED: "
                            f"Destructive command denied by RULE 0Z-C gate: {msg}. "
                            f"Command: {command[:120]}"
                        )

            # --- Check 3: State Machine ---
            # Advance if needed (advance_to_code_edit has its own lock, so do it
            # OUTSIDE this with-block to avoid unnecessary nested RLock acquisition)
            sm_state = _global_sm.state
            if sm_state in (ComplianceState.GATE_ASSERT, ComplianceState.GEMINI_SPAWN):
                advance_to_code_edit()

            with _gate_lock:
                sm_state = _global_sm.state
                if sm_state != ComplianceState.CODE_EDIT:
                    raise GateViolationError(
                        f"[COMPLIANCE BROKER] Action '{actual_action}' BLOCKED: "
                        f"State machine is in '{sm_state.name}' state, "
                        f"expected 'CODE_EDIT'. Complete the workflow sequence: "
                        f"gate_asserted() -> advance_to_code_edit() -> tool call."
                    )

            # --- Execute ---
            return func(*args, **kwargs)

        if HAS_WRAPT and isinstance(wrapped, wrapt.ObjectProxy):
            @wrapt.decorator
            def wrapper_func(wrapped, instance, args, kwargs):  # pyright: ignore[reportRedeclaration]
                return gate_wrapper(wrapped, *args, **kwargs)
            result = wrapper_func(wrapped)  # pyright: ignore[reportCallIssue, reportArgumentType] -- wrapt ships no stubs; its decorator factory accepts the tool proxy at runtime
            _decorated_tools[actual_action] = wrapped
            return result
        elif wrapped is not None:
            @functools.wraps(wrapped)
            def wrapper_func(*args, **kwargs):
                return gate_wrapper(wrapped, *args, **kwargs)
            # Register as decorated tool
            _decorated_tools[actual_action] = wrapped
            return wrapper_func
        else:
            # Called with arguments: @enforce_gate("action_name")
            return decorator

    return decorator


# ---------------------------------------------------------------------------
# Decorated Tool Registry
# ---------------------------------------------------------------------------

def get_decorated_tools() -> dict:
    """Get the registry of tools decorated with @enforce_gate.

    Returns:
        dict mapping action_name -> original function
    """
    return dict(_decorated_tools)


# ---------------------------------------------------------------------------
# Compliance Summary
# ---------------------------------------------------------------------------

def get_compliance_summary() -> dict:
    """Get a summary of the current compliance state.

    Returns:
        dict with gate status, ECA status, state machine status
    """
    eca = get_eca()
    worm_health = check_worm_health()
    with _gate_lock:
        return {
            "gate_asserted": _gate_is_asserted,
            "gate_assertion_count": _gate_assertion_count,
            "state_machine_state": _global_sm.state.name,
            "state_machine_transitions": _global_sm.transition_count,
            "can_edit": can_edit(),
            "eca_halted": eca.is_halted(),
            "eca_halt_reason": eca.get_halt_reason() if eca.is_halted() else "",
            "decorated_tools": list(_decorated_tools),
            "wrapt_available": HAS_WRAPT,
            "worm_health": {
                "status": worm_health["status"],
                "pass_rate": worm_health["pass_rate"],
                "ok": worm_health["ok"],
                "failed": worm_health["failed"],
                "total": worm_health["total"],
                "message": worm_health["message"],
            },
        }


# ---------------------------------------------------------------------------
# Self-Test
# ---------------------------------------------------------------------------

def _test_broker():
    """Run self-test on the broker module."""
    print("=== Broker Self-Test ===")

    # Reset state
    reset_gate()
    assert not gate_passed(), "Gate should not be passed after reset"
    assert _global_sm.state == ComplianceState.IDLE, f"Expected IDLE, got {_global_sm.state.name}"
    print("  [OK] Reset works, state is IDLE")

    # Test gate_asserted
    gate_asserted("TEST: AP-1 user asked for X. PROCEED.")
    assert gate_passed(), "Gate should be passed after gate_asserted()"
    assert _global_sm.state == ComplianceState.GATE_ASSERT, f"Expected GATE_ASSERT, got {_global_sm.state.name}"
    assert _gate_assertion_count == 1
    print(f"  [OK] gate_asserted() works, state is {_global_sm.state.name}, count={_gate_assertion_count}")

    # Test advance_to_code_edit
    advance_to_code_edit()
    assert _global_sm.state == ComplianceState.CODE_EDIT, f"Expected CODE_EDIT, got {_global_sm.state.name}"
    print(f"  [OK] advance_to_code_edit() works, state is {_global_sm.state.name}")

    # Test can_edit
    assert can_edit(), "can_edit() should return True in CODE_EDIT state"
    print("  [OK] can_edit() returns True in CODE_EDIT")

    # Test @enforce_gate decorator
    @enforce_gate("test_tool")
    def test_tool(x):
        return x * 2

    # Should succeed (gate passed, state is CODE_EDIT)
    result = test_tool(21)
    assert result == 42, f"Expected 42, got {result}"
    print(f"  [OK] @enforce_gate decorator allows call in CODE_EDIT: test_tool(21) = {result}")

    # Test decorated tools registry
    decorated = get_decorated_tools()
    assert "test_tool" in decorated, f"Expected 'test_tool' in decorated tools: {decorated}"
    print(f"  [OK] Decorated tools: {list(decorated)}")

    # Test GateViolationError when gate not passed
    reset_gate()
    try:
        test_tool(1)
        assert False, "Should have raised GateViolationError"
    except GateViolationError as e:
        assert "not been passed" in str(e)
        print(f"  [OK] GateViolationError raised when gate not passed: {str(e)[:80]}...")

    # Test state machine summary
    summary = get_compliance_summary()
    assert "gate_asserted" in summary
    print(f"  [OK] Compliance summary: state={summary['state_machine_state']}, "
          f"can_edit={summary['can_edit']}, eca_halted={summary['eca_halted']}")

    # Test advance_to_review
    gate_asserted("TEST: Second gate for review test. PROCEED.")
    advance_to_code_edit()
    advance_to_review()
    assert _global_sm.state == ComplianceState.CODE_REVIEW, f"Expected CODE_REVIEW, got {_global_sm.state.name}"
    print(f"  [OK] advance_to_review() works, state is {_global_sm.state.name}")

    # Test advance_to_idle
    advance_to_idle()
    assert _global_sm.state == ComplianceState.IDLE, f"Expected IDLE, got {_global_sm.state.name}"
    print(f"  [OK] advance_to_idle() works, state is {_global_sm.state.name}")

    # Clean up
    reset_gate()
    print("\n  [BROKER] ALL SELF-TESTS PASSED")


if __name__ == "__main__":
    _test_broker()
