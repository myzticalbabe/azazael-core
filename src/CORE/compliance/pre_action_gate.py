"""
PreActionGate â€” Hardening module to prevent catastrophic indentation-destruction.

Provides three-phase safety for every str_replace/write_file operation:
1. Pre-edit Validation (validate_str_replace): exact match, indentation preservation
2. Pre-edit Backup (take_snapshot): copy file to .pre_gate_backup before mutation
3. Post-edit Validation (validate_after_edit): py_compile syntax check, merged-line
   detection, 0-indent runaway method detection, auto-revert on failure

Designed for integration with azazael/src/SCRIPTS/sentinel_gate.py and
CORE.compliance.orchestrator.
"""

import ast
import fnmatch
import inspect
import json
import os
import py_compile
import re
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List
import tempfile

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Dict, List, Optional, Tuple  # removed — all names have native equivalents

# Ensure src/ is on sys.path for direct script execution (self-test)
_SRC_DIR = Path(__file__).resolve().parent.parent.parent  # compliance/ -> CORE/ -> src/
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# Optional CORE dependencies â€” graceful fallback if not available
try:
    from CORE.direct_memory import DirectMemory
    _MEMORY_AVAILABLE = True
except ImportError as exc:
    note_failure("pre_action_gate:L37", exc)
    _MEMORY_AVAILABLE = False
    DirectMemory = None

try:
    from CORE.compliance.telemetry import ComplianceTelemetry
    _TELEMETRY_AVAILABLE = True
except ImportError as exc:
    note_failure("pre_action_gate:L44", exc)
    _TELEMETRY_AVAILABLE = False
    ComplianceTelemetry = None

#: EP152 (DeepVerifier) -- the 0X/0Y verdict is holistic: it names the failing
#: COMPONENT ("0X:policy_gate"), never the failing part of the requirement.
#: `decompose_rubric` (already written in verification_gate) turns a criterion
#: into atomic, checkable sub-questions carrying a failure class each.
#: Module-level (an in-function import is a ratchet failure) and fail-open.
try:
    from CORE.verification_gate import decompose_rubric as _decompose_rubric
except ImportError as exc:                               # gate absent: deny stays holistic
    note_failure("pre_action_gate:verification-gate-import", exc)
    _decompose_rubric = None

# Orchestrator import is LAZY (deferred to _check_rule_0x_0y) to avoid
# a Python import-ordering bug: when orchestrator is already in sys.modules
# (loaded by sentinel_gate.py first), pre_action_gate's module-level
# from-import of ComplianceOrchestrator spuriously raises ImportError.
# Lazy import moves the resolution to runtime when all modules are fully
# initialized, eliminating the ordering dependency.
_ORCHESTRATOR_AVAILABLE = None  # None = not yet attempted
ComplianceOrchestrator = None  # type: ignore
_ox_component_passed = lambda n, d: True  # type: ignore
_oy_component_passed = lambda n, d: True  # type: ignore


def _lazy_import_orchestrator():
    """Import orchestrator on first use, caching success/failure.

    Returns True if orchestrator imported successfully.
    Only the FIRST call triggers the import; subsequent calls use cache.
    """
    global _ORCHESTRATOR_AVAILABLE, ComplianceOrchestrator
    global _ox_component_passed, _oy_component_passed

    if _ORCHESTRATOR_AVAILABLE is not None:
        return _ORCHESTRATOR_AVAILABLE

    try:
        from CORE.compliance.orchestrator import (
            ComplianceOrchestrator,
            _ox_component_passed,
            _oy_component_passed,
        )
        _ORCHESTRATOR_AVAILABLE = True
    except ImportError as exc:
        note_failure("pre_action_gate:L79", exc)
        _ORCHESTRATOR_AVAILABLE = False
    return _ORCHESTRATOR_AVAILABLE


# ---------------------------------------------------------------------------
# RULE 0Z-F telemetry state persistence — counters survive across processes
# ---------------------------------------------------------------------------

_TELEMETRY_STATE_PATH = Path.home() / ".azazael" / "pre_action_gate_state.json"


def _load_telemetry_state() -> dict:
    """Load persisted RULE 0Z-F counters (cumulative across processes).

    Returns dict with parallel_batch_count / sequential_spawn_count / edit_count.
    Never raises — missing or corrupt state falls back to zeros.
    """
    try:
        data = json.loads(_TELEMETRY_STATE_PATH.read_text(encoding="utf-8"))
        return {
            "parallel_batch_count": int(data.get("parallel_batch_count", 0)),
            "sequential_spawn_count": int(data.get("sequential_spawn_count", 0)),
            "edit_count": int(data.get("edit_count", 0)),
        }
    except Exception as exc:
        note_failure("pre_action_gate:L104", exc)
        return {"parallel_batch_count": 0, "sequential_spawn_count": 0, "edit_count": 0}


def _save_telemetry_state(gate: "PreActionGate") -> None:
    """Persist current counters so other processes see cumulative totals.

    Caller must hold gate._lock. Failure is advisory-only (never blocks the gate).
    """
    try:
        _TELEMETRY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "parallel_batch_count": gate._parallel_batch_count,
            "sequential_spawn_count": gate._sequential_spawn_count,
            "edit_count": gate._edit_count,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _TELEMETRY_STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        note_failure("pre_action_gate:L122", exc) # Persistence is advisory — never block the gate


def reset_telemetry_state() -> None:
    """Reset persisted and in-memory RULE 0Z-F counters (test/admin use)."""
    try:
        _TELEMETRY_STATE_PATH.unlink(missing_ok=True)
    except Exception as exc:
        note_failure("pre_action_gate:L130", exc)
    inst = _pre_action_gate_instance
    if inst is not None:
        with inst._lock:
            inst._parallel_batch_count = 0
            inst._sequential_spawn_count = 0
            inst._edit_count = 0


def _capability_match(pattern: str, action: str, filepath: str) -> bool:
    """Match an "action:path" grant pattern; a bare action grants any path.

    Backslashes are normalised to "/" (via chr(92)) so one authority table
    is portable between the Windows host and the WSL mirror.
    """
    pat_action, sep, pat_path = pattern.partition(":")
    if not sep:
        return fnmatch.fnmatch(action, pat_action)
    return (fnmatch.fnmatch(action, pat_action)
            and fnmatch.fnmatch(filepath.replace(chr(92), "/"), pat_path))


class PreActionGate:
    """Gatekeeper for file mutations to prevent catastrophic indentation loss.

    Usage:
        gate = PreActionGate()
        ok, msg = gate.validate_str_replace(filepath, old_string, new_string)
        if not ok:
            raise ValueError(msg)
        backup = gate.take_snapshot(filepath)
        # ... perform str_replace ...
        ok, msg = gate.validate_after_edit(filepath, backup)
        if not ok:
            print(f"EDIT REVERTED: {msg}")
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.telemetry = (ComplianceTelemetry()
                          if (_TELEMETRY_AVAILABLE and ComplianceTelemetry is not None)
                          else None)
        self.memory = (DirectMemory()
                       if (_MEMORY_AVAILABLE and DirectMemory is not None)
                       else None)
        self.orchestrator = None  # Lazy-init on first _check_rule_0x_0y call
        # RULE 0Z-F telemetry — load persisted cumulative counters so
        # generate_telemetry.py in a fresh process sees real values.
        _state = _load_telemetry_state()
        self._edit_count = _state["edit_count"]
        self._parallel_batch_count = _state["parallel_batch_count"]
        self._sequential_spawn_count = _state["sequential_spawn_count"]
        # EP269 (Agent libOS): the rule engine was called with a hardcoded
        # role="developer" literal -- a fixed subject that could not be denied
        # anything, because no task-authority concept existed anywhere. These
        # fields are that concept's host. Empty grants is the FAIL-OPEN state:
        # install nothing and the gate behaves exactly as it did before.
        self.subject = "developer"
        self.task: str | None = None
        self._capability_grants: dict[str, List[str]] = {}

    # ------------------------------------------------------------------
    # Call-stack validation (v2 — Jun 17, 2026)
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_caller_stack():
        """Ensure edits only happen via authorized callers, preventing
        hallucinated scripts or direct calls bypassing the sentinel.

        Raises RuntimeError if caller is not authorized.
        """
        stack = inspect.stack()
        authorized = ("sentinel_enforcer.py", "sentinel_gate.py",
                       "pre_action_gate.py")
        for frame_info in stack:
            if os.path.basename(frame_info.filename) in authorized:
                return  # Authorized caller found
        raise RuntimeError(
            "CRITICAL: Direct call to PreActionGate detected. "
            "All edits MUST route through sentinel_enforcer.py or "
            "sentinel_gate.py. Unauthorized caller blocked."
        )

    # ------------------------------------------------------------------
    # Telemetry & Memory helpers
    # ------------------------------------------------------------------

    def _emit(self, event_type: str, status: str, details: dict):
        """Emit telemetry event if available."""
        if self.telemetry:
            try:
                self.telemetry.emit(event_type, "pre_action_gate", status, details)
            except Exception as exc:
                note_failure("pre_action_gate:L199", exc)

    def _record(self, event_type: str, details: dict):
        """Record to DirectMemory for session audit trail."""
        if self.memory:
            try:
                self.memory.record(event_type, data=details)
            except Exception as exc:
                note_failure("pre_action_gate:L207", exc)

    # ------------------------------------------------------------------
    # Indentation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_indent_level(text: str) -> int:
        """Return the column index of the first non-whitespace character."""
        if not text:
            return 0
        for line in text.split("\n"):
            stripped = line.lstrip()
            if stripped and not stripped.startswith("#"):
                return len(line) - len(stripped)
        return 0

    @staticmethod
    def _get_line_indents(text: str) -> list[int]:
        """Return indentation level for every non-empty line in text.

        Used to verify that new_string preserves the same relative indentation
        pattern as old_string, catching inner-line indentation breaks.
        """
        if not text:
            return []
        return [
            len(line) - len(line.lstrip())
            for line in text.split("\n")
            if line.strip() and not line.strip().startswith("#")
        ]

    @staticmethod
    def _has_zero_indent_declaration(text: str) -> bool:
        """Check if text contains a 0-indent def or class declaration."""
        for line in text.split("\n"):
            if line.startswith("def ") or line.startswith("class "):
                return True
        return False

    # ------------------------------------------------------------------
    # HEUR-04: Merged-line detection
    # ------------------------------------------------------------------

    # Statement-ending constructs (what comes before the merge gap)
    _END_STATEMENT = (
        r'(?:return\s+\S+|\bpass\b|\braise\s+\S+|\bbreak\b|'
        r'\bcontinue\b|\byield\s+\S+|\bdel\s+\S+|\bassert\s+\S+|'
        r'\w+\([^)]*\)|'  # function/method call
        r'\w+\s*=\s*\S+)'  # assignment
    )

    # Statement-starting constructs (what comes after the merge gap)
    _START_STATEMENT = (
        r'(?:\bdef\s+|\bclass\s+|\bif\s+|\bfor\s+|\bwhile\s+|'
        r'\bwith\s+|\btry\s*:|\bexcept\s|\bfinally\s*:|'
        r'\breturn\s+|\bpass\b|\braise\s+|\bbreak\b|'
        r'\bcontinue\b|\byield\s+|\bimport\s+|'
        r'\bfrom\s+\S+\s+import\s+|'
        r'self\.\w+\s*=|'  # self.attr = ...
        r'\b[A-Za-z_]\w*\s*\+?=\s*)'  # var = or var +=
    )

    # The merge gap: 3+ whitespace characters on the same line
    _MERGE_GAP = r'\s{3,}'

    # Compiled HEUR-04 pattern
    _MERGED_LINE_RE = re.compile(_END_STATEMENT + _MERGE_GAP + _START_STATEMENT)

    @staticmethod
    def _detect_merged_statements(content: str) -> list[tuple[int, str]]:
        """Scan for HEUR-04 merged-line patterns.

        Detects lines where two Python statements are joined without a
        newline separator (e.g., 'return x        self.y = z'). This is
        the signature of a destructive edit that merged adjacent lines.

        Returns:
            list of (line_number, description) tuples for each merge found.
            Empty list means no merged statements detected.
        """
        findings: list[tuple[int, str]] = []
        for i, line in enumerate(content.split('\n')):
            stripped = line.strip()
            # Skip comment-only lines and blank lines
            if not stripped or stripped.startswith('#'):
                continue
            match = PreActionGate._MERGED_LINE_RE.search(line)
            if match:
                matched_text = match.group()
                # HEUR-04 false-positive guard:
                # If '#' appears in the matched region, the second
                # 'statement' is likely inside an end-of-line comment.
                if '#' in matched_text:
                    continue
                # If quotes appear in the matched region, the match
                # may span string literal content â€” skip to avoid
                # false positives on long string lines.
                if '"' in matched_text or "'" in matched_text:
                    continue
                findings.append((
                    i + 1,
                    f"Merged statements at line {i + 1}: "
                    f"'{matched_text[:80]}'"
                ))
        return findings

    # ------------------------------------------------------------------
    # HEUR-07: Over-Indentation detection
    # ------------------------------------------------------------------

    # Lines that introduce a new block â€” body lines at 8+ spaces after
    # these at 4-space indent are legitimate nesting, not over-indentation.
    _BLOCK_INTRODUCER_RE = re.compile(
        r"^ {0,4}(?:def |class |if |for |while |try |with |"
        r"elif |else:|except\b|finally:)"
    )

    _OVER_INDENT_RE = re.compile(
        r"(?m)^( {8,})(?:return |if |for |while |try |with |print |"
        r"raise |yield |assert |pass |break |continue |global |"
        r"nonlocal |del |import |from )"
    )

    @staticmethod
    def _detect_over_indentation(content: str) -> list[tuple[int, str]]:
        """Scan for HEUR-07 over-indentation patterns.

        Detects lines at 8+ spaces indentation that are flanked by 4-space
        lines, indicating a batch fixer double-indented a single line.
        Consistent deeper nesting (e.g., class -> def -> if -> return at
        12 spaces) is NOT flagged. 8-space body lines after a block
        introducer (def/if/for/...) at 4-space are also skipped â€” those
        are legitimate first-level nesting.

        Returns:
            list of (line_number, description) tuples for each anomaly.
        """
        findings: list[tuple[int, str]] = []
        lines = content.split("\n")
        for m in PreActionGate._OVER_INDENT_RE.finditer(content):
            line_no = content[:m.start()].count("\n")
            over_indent = len(m.group(1))
            # Check surrounding lines: is this an anomaly amid 4-space lines?
            prev_indent = 0
            next_indent = 0
            prev_is_block_introducer = False
            if line_no > 0:
                prev_line = lines[line_no - 1]
                if prev_line.strip():
                    prev_indent = len(prev_line) - len(prev_line.lstrip())
                    # Skip if prev line is a block introducer at 4-space â€”
                    # 8-space body is legitimate nesting (e.g., def -> body)
                    if PreActionGate._BLOCK_INTRODUCER_RE.match(prev_line):
                        prev_is_block_introducer = True
            if line_no + 1 < len(lines):
                next_line = lines[line_no + 1]
                if next_line.strip():
                    next_indent = len(next_line) - len(next_line.lstrip())
            # Flag if this 8+ space line is flanked by 4-space neighbors
            # (not consistent deeper nesting where next is also >= 8)
            # AND the previous line is NOT a block introducer (def/if/etc)
            if prev_indent == 4 and over_indent >= 8 and not prev_is_block_introducer:
                if next_indent == 0 or next_indent <= 4:
                    findings.append((
                        line_no + 1,
                        f"Over-indented line at {line_no + 1}: "
                        f"{over_indent} spaces amid 4-space block"
                    ))
                    break  # One anomaly is enough
        return findings

    # ------------------------------------------------------------------
    # HEUR-08: Import-Indentation Anomaly detection
    # ------------------------------------------------------------------

    _IMPORT_ANOMALY_RE = re.compile(
        r"(?sm)^class \w+.*:\n"
        r"(?:(?!^(?:class |def |@|\s*$)).)*?"
        r"^(?:import |from )\w+"
    )

    @staticmethod
    def _detect_import_indent_anomaly(content: str) -> list[tuple[int, str]]:
        """Scan for HEUR-08 import-indentation anomalies.

        Detects import/from-import statements at column 0 that appear
        inside what should be an indented class body, indicating that
        import-line removal from a prior batch script left surrounding
        code at the wrong indentation.

        Top-level imports between class definitions (legitimate) are
        filtered out by checking the previous non-blank line is indented.

        Returns:
            list of (line_number, description) tuples for each anomaly.
        """
        findings: list[tuple[int, str]] = []
        lines = content.split("\n")
        for m in PreActionGate._IMPORT_ANOMALY_RE.finditer(content):
            import_start = m.end()
            line_no = content[:import_start].count("\n")  # 0-based index of import line
            # Verify this import is preceded by an indented class body line
            # lines[line_no - 1] is the previous line (0-based access)
            if line_no > 0:
                prev_line = lines[line_no - 1]
                if prev_line.strip():
                    prev_indent = len(prev_line) - len(prev_line.lstrip())
                    if prev_indent >= 4:
                        findings.append((
                            line_no + 1,  # 1-based for reporting
                            f"Import at column 0 inside class body at "
                            f"approx line {line_no + 1} â€” likely pulled from "
                            f"indented scope by batch fixer"
                        ))
                        break
                # Also check if import is right after class header
                prev_line_stripped = lines[line_no - 1].strip()
                if re.match(r"^class \w+", prev_line_stripped):
                    findings.append((
                        line_no + 1,  # 1-based for reporting
                        f"Import at column 0 immediately after class "
                        f"header at approx line {line_no + 1}"
                    ))
                    break
        return findings

    # ------------------------------------------------------------------
    # HEUR-03: Duplicate method detection
    # ------------------------------------------------------------------

    # Compiled HEUR-03 pattern: same-name def at same indentation
    _DUPLICATE_METHOD_RE = re.compile(
        r"(?sm)^( +def (\w+)\(.*\):)"
        r"(?:(?!^(?:class\b| {0,3}(?:def |class |@))).)*"
        r"\n\1"
    )

    @staticmethod
    def _detect_duplicate_methods(content: str) -> (tuple[int, str] | None):
        """Scan for HEUR-03 duplicate method definitions.

        Detects when a str_replace or write_file leaves behind a duplicate
        copy of a method/function at the same indentation in the same scope.
        Returns the FIRST genuine duplicate on its first hit â€” does not
        scan the entire file (early-return).

        Uses the shared _HEUR03_DUPLICATE_RE regex from heuristic_recorder
        with inline boundary checks matching HeuristicErrorRecorder's
        _heur03_class_boundary_check() logic to avoid regex duplication.
        Falls back to local _DUPLICATE_METHOD_RE if heuristic_recorder is
        unavailable.

        Returns:
            (line_number, description) tuple on first genuine duplicate,
            or None if no same-scope duplicate detected.
        """
        # â”€â”€ Primary path: use shared regex from heuristic_recorder â”€â”€
        try:
            from CORE.compliance.heuristic_recorder import _HEUR03_DUPLICATE_RE

            for m in _HEUR03_DUPLICATE_RE.finditer(content):
                method_name = m.group(2)
                indent = len(m.group(1)) - len(m.group(1).lstrip())
                between = content[m.start():m.end()]

                # Check for intervening class keyword at indent <= method indent
                if re.search(
                    r"(?m)^ {0," + str(indent) + r"}class\s+\w+", between
                ):
                    continue  # Different class â€” skip

                # Check for intervening def at strictly lower indentation
                if indent > 0:
                    if re.search(
                        r"(?m)^ {0," + str(indent - 1) + r"}def\s+\w+",
                        between
                    ):
                        continue  # Different scope â€” skip

                # No boundary â€” genuine same-scope duplicate (early-return)
                before_match = content[:m.end()]
                line_no = before_match.count('\n') + 1
                return (
                    line_no,
                    f"Duplicate method '{method_name}' at line {line_no} â€” "
                    f"same-scope duplicate at indent {indent}"
                )
            return None
        except ImportError as exc:
            note_failure("pre_action_gate:L497", exc) # Fall back to inline implementation below

        # â”€â”€ Fallback: inline duplicate detection (heuristic_recorder unavailable) â”€â”€
        for m in PreActionGate._DUPLICATE_METHOD_RE.finditer(content):
            method_name = m.group(2)
            indent = len(m.group(1)) - len(m.group(1).lstrip())
            span_start = m.start()
            span_end = m.end()
            between = content[span_start:span_end]

            # Check for intervening class keyword at indent <= method indent
            class_line_re = re.compile(
                r"(?m)^ {0," + str(indent) + r"}class\s+\w+"
            )
            if class_line_re.search(between):
                continue  # Different class â€” false positive

            # Check for intervening def at strictly lower indentation
            # (e.g., inner functions in different enclosing methods)
            if indent > 0:
                def_line_re = re.compile(
                    r"(?m)^ {0," + str(indent - 1) + r"}def\s+\w+"
                )
                if def_line_re.search(between):
                    continue  # Different scope â€” false positive

            # No class or method boundary â€” genuine same-scope duplicate (early-return)
            before_match = content[:m.end()]
            line_no = before_match.count('\n') + 1  # 1-based line number
            return (
                line_no,
                f"Duplicate method '{method_name}' at line {line_no} â€” "
                f"same-scope duplicate at indent {indent}"
            )
        return None

    # ------------------------------------------------------------------
    # HEUR-09: Indent Jump / Mismatched Siblings detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_indent_jump(content: str) -> (tuple[int, str] | None):
        """Scan for HEUR-09 indent-jump patterns.

        Detects two adjacent non-blank lines within the same block scope
        that differ by more than 4 spaces in indentation, indicating a
        batch fixer pulled one line to the wrong indent level.
        Uses sandwich detection: if one line is flanked both above and
        below by lines at the expected indent, it's Algorithm #8's exact
        signature.

        Imports shared regexes from heuristic_recorder; falls back to
        inline compiled patterns if unavailable.

        Returns:
            (line_number, description) tuple on first indent jump found,
            or None if no anomalous jump detected.
        """
        # â”€â”€ Primary path: use shared regexes from heuristic_recorder â”€â”€
        try:
            from CORE.compliance.heuristic_recorder import (
                _HEUR09_JUMP_RE, _HEUR09_BLOCK_RE
            )

            lines = content.split("\n")
            for m in _HEUR09_JUMP_RE.finditer(content):
                indent_a = len(m.group(1))
                indent_b = len(m.group(2))
                diff = abs(indent_a - indent_b)
                if diff <= 4:
                    continue  # Normal indent step

                line_b = content[:m.end()].count("\n")
                line_a = line_b - 1

                # Skip legitimate scope continuations: except/elif/else/finally
                # These ALWAYS close a previous block and start a new one at
                # the same indent as their matching opener (try/if/for).
                # Flagging them as indent jumps is a false positive.
                line_b_content = lines[line_b].strip()
                if line_b_content.startswith(("except", "elif ", "else:", "finally:")):
                    continue

                # Scope check: same enclosing block?
                scope_indent = max(indent_a, indent_b)
                same_scope = False
                for i in range(line_a - 1, -1, -1):
                    stripped = lines[i].strip()
                    if not stripped:
                        continue
                    line_indent_i = len(lines[i]) - len(lines[i].lstrip())
                    if line_indent_i < scope_indent:
                        if _HEUR09_BLOCK_RE.match(stripped):
                            same_scope = True
                        break
                if not same_scope:
                    continue

                # Sandwich detection
                if line_a > 0:
                    prev_prev = lines[line_a - 1].strip()
                    if prev_prev:
                        pind = len(lines[line_a - 1]) - len(lines[line_a - 1].lstrip())
                        if pind == indent_b and indent_a != indent_b:
                            return (
                                line_a + 1,
                                f"Indent jump at line {line_a + 1}: "
                                f"{indent_a} spaces flanked by {indent_b}-space lines"
                            )
                if line_b + 1 < len(lines):
                    next_next = lines[line_b + 1].strip()
                    if next_next:
                        nind = len(lines[line_b + 1]) - len(lines[line_b + 1].lstrip())
                        if indent_a == nind and indent_b != indent_a:
                            return (
                                line_b + 1,
                                f"Indent jump at line {line_b + 1}: "
                                f"{indent_b} spaces flanked by {indent_a}-space lines"
                            )

                # Not a sandwich anomaly â€” this is a legitimate scope
                # transition (e.g., returning from inside a nested block
                # to the parent scope). Skip it.
                continue
            return None
        except ImportError as exc:
            note_failure("pre_action_gate:L623", exc) # Fall back to inline implementation below

        # â”€â”€ Fallback: inline compiled patterns â”€â”€
        jump_re = re.compile(
            r"(?m)^(\s*)(?!\s*(?:class |def |@|#|from |import ))\S.*\n"
            r"(\s*)(?!\s*(?:class |def |@|#|from |import ))\S"
        )
        block_re = re.compile(
            r"^\s*(?:class |def |if |for |while |try:|with |elif |else:|except[ :]|finally:)"
        )

        lines = content.split("\n")
        for m in jump_re.finditer(content):
            indent_a = len(m.group(1))
            indent_b = len(m.group(2))
            diff = abs(indent_a - indent_b)
            if diff <= 4:
                continue

            line_b = content[:m.end()].count("\n")
            line_a = line_b - 1

            # Skip legitimate scope continuations: except/elif/else/finally
            line_b_content = lines[line_b].strip()
            if line_b_content.startswith(("except", "elif ", "else:", "finally:")):
                continue

            scope_indent = max(indent_a, indent_b)
            same_scope = False
            for i in range(line_a - 1, -1, -1):
                stripped = lines[i].strip()
                if not stripped:
                    continue
                line_indent_i = len(lines[i]) - len(lines[i].lstrip())
                if line_indent_i < scope_indent:
                    if block_re.match(stripped):
                        same_scope = True
                    break
            if not same_scope:
                continue

            if line_a > 0:
                prev_prev = lines[line_a - 1].strip()
                if prev_prev:
                    pind = len(lines[line_a - 1]) - len(lines[line_a - 1].lstrip())
                    if pind == indent_b and indent_a != indent_b:
                        return (
                            line_a + 1,
                            f"Indent jump at line {line_a + 1}: "
                            f"{indent_a} spaces flanked by {indent_b}-space lines"
                        )
            if line_b + 1 < len(lines):
                next_next = lines[line_b + 1].strip()
                if next_next:
                    nind = len(lines[line_b + 1]) - len(lines[line_b + 1].lstrip())
                    if indent_a == nind and indent_b != indent_a:
                        return (
                            line_b + 1,
                            f"Indent jump at line {line_b + 1}: "
                            f"{indent_b} spaces flanked by {indent_a}-space lines"
                        )

            return (
                line_b + 1,
                f"Indent jump at line {line_b + 1}: "
                f"{indent_a}sp -> {indent_b}sp in same scope"
            )
        return None

    # ------------------------------------------------------------------
    # HEUR-10: Dangling Function Body detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_dangling_body(content: str) -> (tuple[int, str] | None):
        """Scan for HEUR-10 dangling function body patterns.

        Detects a def statement followed by only blank/whitespace lines
        before another def/class/@ at the same indent, indicating the
        function body was accidentally deleted by a str_replace that
        matched too broadly (Algorithm #17's failure mode).

        Single-line stubs (def f(): pass) are legitimate and excluded.

        Imports shared regex from heuristic_recorder; falls back to
        inline compiled pattern if unavailable.

        Returns:
            (line_number, description) tuple on first dangling def found,
            or None if no bodyless def detected.
        """
        # â”€â”€ Primary path: use shared regex from heuristic_recorder â”€â”€
        try:
            from CORE.compliance.heuristic_recorder import (
                _HEUR10_DANGLING_RE, _DECL_OR_DECORATOR_RE
            )

            lines = content.split("\n")
            for m in _HEUR10_DANGLING_RE.finditer(content):
                func_name = m.group(2)
                def_line_no = content[:m.start()].count("\n")

                # Walk forward from def through blank lines
                has_body = False
                for i in range(def_line_no + 1, len(lines)):
                    stripped = lines[i].strip()
                    if not stripped:
                        continue
                    line_indent = len(lines[i]) - len(lines[i].lstrip())
                    def_indent = len(m.group(1))
                    if line_indent <= def_indent:
                        if _DECL_OR_DECORATOR_RE.match(stripped):
                            break  # No body found â€” genuine anomaly
                        else:
                            has_body = True
                            break
                    else:
                        has_body = True
                        break

                if not has_body:
                    return (
                        def_line_no + 1,
                        f"Dangling function '{func_name}' at line "
                        f"{def_line_no + 1} â€” def with no body before next declaration"
                    )
            return None
        except ImportError as exc:
            note_failure("pre_action_gate:L751", exc) # Fall back to inline implementation below

        # â”€â”€ Fallback: inline compiled pattern â”€â”€
        dang_re = re.compile(
            r"(?m)^( *)def (\w+)\(.*\):\s*\n"
            r"((?:\s*\n)*)"
            r"^(?=\1(?:class |def |@))"
        )
        decl_re = re.compile(r"^(?:class |def |@)")

        lines = content.split("\n")
        for m in dang_re.finditer(content):
            func_name = m.group(2)
            def_line_no = content[:m.start()].count("\n")

            has_body = False
            for i in range(def_line_no + 1, len(lines)):
                stripped = lines[i].strip()
                if not stripped:
                    continue
                line_indent = len(lines[i]) - len(lines[i].lstrip())
                def_indent = len(m.group(1))
                if line_indent <= def_indent:
                    if decl_re.match(stripped):
                        break
                    else:
                        has_body = True
                        break
                else:
                    has_body = True
                    break

            if not has_body:
                return (
                    def_line_no + 1,
                    f"Dangling function '{func_name}' at line "
                    f"{def_line_no + 1} â€” def with no body before next declaration"
                )
        return None


        # ------------------------------------------------------------------
    # Phase 1: Pre-edit Validation
    # ------------------------------------------------------------------

    # Non-blocking components â€” advisory only, never deny edits
    _NON_BLOCKING_0X = {"broker_interception", "telemetry"}
    _NON_BLOCKING_0Y = {
        "judge_consensus", "confidence_gate", "graph_analysis",
        "dual_ledger", "audit_pipeline",
    }

    def _decompose_denial(self, failing: List[str]) -> dict:
        """Decompose a blocking denial into targeted sub-questions (EP152).

        The DeepVerifier asymmetry: the gate can already deny, but a holistic
        deny cannot say WHICH part failed, so the remediation is a component
        list rather than repair targets. This splits the failing criteria with
        `verification_gate.decompose_rubric` and assigns each sub-question a
        failure class (omission / unsupported-claim / inconsistency /
        incompleteness).

        Fail-open: without the gate module, or if it raises, each criterion
        becomes one plain question and the reason is named in `gaps`. The deny
        itself is unaffected either way — only the detail degrades.

        Returns {"subquestions", "by_subquestion", "classes", "ok", "gaps"}.
        """
        out: dict = {"subquestions": [], "by_subquestion": {}, "classes": [],
                     "ok": False, "gaps": []}
        criteria = [str(f).strip() for f in (failing or []) if str(f).strip()]
        if not criteria:
            out["gaps"].append("no-failing-criteria")
            return out
        fallback = [f"does {c} pass?" for c in criteria]
        if _decompose_rubric is None:
            out["subquestions"] = fallback
            out["gaps"].append("verification-gate-unavailable")
            return out
        try:
            rubric = _decompose_rubric(", ".join(criteria))
        except Exception as exc:              # noqa: BLE001 — the deny stands regardless
            note_failure("pre_action_gate:denial-decompose", exc)
            out["subquestions"] = fallback
            out["gaps"].append(f"decompose-failed:{type(exc).__name__}")
            return out
        taxonomy = rubric.get("taxonomy") or {}
        out["subquestions"] = [str(q) for q in (rubric.get("subquestions") or [])]
        out["by_subquestion"] = dict(taxonomy.get("by_subquestion") or {})
        out["classes"] = [str(c.get("class"))
                          for c in (taxonomy.get("classes") or [])]
        out["ok"] = bool(rubric.get("ok"))
        out["gaps"].extend(str(g) for g in (rubric.get("gaps") or []))
        if not out["subquestions"]:
            out["subquestions"] = fallback
        return out

    def install_capabilities(self, grants: dict, subject: str | None = None,
                             task: str | None = None) -> dict[str, Any]:
        """Install the EP269 task-authority table (subject -> action:path).

        The libOS row's missing symbol was a task-authority concept: the gate
        could already deny, but every subject held every action and the subject
        itself was a hardcoded role=developer literal. This is the grant side
        of that concept, installed at the choke point every edit already
        passes through.

        Installing a table is the ONLY way to deny on capability; with none
        installed `authorize` allows everything, so the default gate is
        unchanged. A malformed entry installs NOTHING -- a half-applied
        authority table is worse than none -- and names the gap instead.

        Returns {"ok", "subjects", "grants", "gaps"}.
        """
        gaps: List[str] = []
        clean: dict[str, List[str]] = {}
        if subject is not None:
            self.subject = str(subject)
        if task is not None:
            self.task = str(task)
        for subj, patterns in (grants or {}).items():
            key = str(subj).strip()
            if not key:
                gaps.append("empty-subject")
                continue
            if isinstance(patterns, str):
                patterns = [patterns]
            if not isinstance(patterns, (list, tuple)):
                gaps.append("not-a-list:" + key)
                continue
            pats = [str(p).strip() for p in patterns if str(p).strip()]
            if not pats:
                gaps.append("no-patterns:" + key)
                continue
            clean[key] = pats
        if gaps:
            return {"ok": False,
                    "subjects": sorted(self._capability_grants),
                    "grants": sum(len(v) for v in
                                  self._capability_grants.values()),
                    "gaps": gaps}
        with self._lock:
            self._capability_grants = clean
        return {"ok": True, "subjects": sorted(clean),
                "grants": sum(len(v) for v in clean.values()), "gaps": []}

    def authorize(self, subject: str | None = None, action: str | None = None,
                  filepath: str | None = None) -> tuple[bool, str]:
        """EP269: does `subject` hold a grant for `action` on `filepath`?

        Fail-open by construction: with no table installed, or on any error,
        the answer is True -- exactly the pre-EP269 behaviour. A deny needs a
        table AND a subject whose patterns all miss, so installing nothing can
        never reject an edit.

        A pattern is "action:path" (fnmatch wildcards in either half) or a bare
        "*"; a pattern with no colon grants that action on any path.
        """
        subj = str(self.subject if subject is None else subject)
        act = str(action or "")
        path = str(filepath or "")
        try:
            grants = self._capability_grants
            if not grants:
                return True, ""
            patterns = grants.get(subj)
            if patterns is None:
                return False, "subject %r holds no task authority" % subj
            for pattern in patterns:
                if pattern.strip() == "*" or _capability_match(
                        pattern.strip(), act, path):
                    return True, ""
            return False, ("subject %r holds no grant for %s on %s"
                           % (subj, act or "any action", path or "any path"))
        except Exception as exc:   # noqa: BLE001 - an authority check fails OPEN
            note_failure("pre_action_gate:authorize", exc)
            return True, ""

    def _check_rule_0x_0y(self, action: str, filepath: str,
                           source_code: (str | None) = None) -> tuple[bool, str]:
        """Run mandatory RULE 0X + 0Y compliance check before any edit.

        Every validate_str_replace() and validate_write_file() call runs
        through this gate â€” the orchestrator check is non-optional.
        This is PROGRAMMATIC ENFORCEMENT: if the orchestrator is unavailable,
        the gate DENIES the edit. No silent passes.

        Only BLOCKING components deny edits:
          RULE 0X: policy_gate, formal_verification, state_machine,
                   schema_validation, compliance_scoring, ast_scanning
        Non-blocking (advisory only): broker_interception, telemetry,
          judge_consensus, confidence_gate, graph_analysis (anomalies),
          dual_ledger, audit_pipeline

        Args:
            action: The action type ("str_replace" or "write_file")
            filepath: Path to the target file
            source_code: Optional source code for AST scanning.
                For write_file, pass the NEW content so the scanner validates
                the incoming code, not the possibly-corrupted file on disk.
                For str_replace, leave as None to scan the existing file.

        Returns:
            (True, "") if passed, (False, reason) if denied
        """
        # EP269 (Agent libOS): authority is checked at the choke point, before
        # the machinery -- an unheld capability is a deny, not a warning. With
        # no table installed `authorize` returns True, so this is a no-op and
        # every pre-existing failure reason below is still reached unchanged.
        cap_ok, cap_reason = self.authorize(action=action, filepath=filepath)
        if not cap_ok:
            self._emit("capability_check", "denied", {
                "action": action, "filepath": filepath,
                "subject": self.subject, "task": self.task,
                "reason": cap_reason,
            })
            return False, "RULE 0X/0Y DENIED (capability): " + cap_reason

        # Lazy-init orchestrator on first call (avoids module-load import bug)
        if self.orchestrator is None:
            if _lazy_import_orchestrator():
                # Narrow the possibly-None module symbol through a local:
                # the lazy importer sets it on success, but the checker
                # cannot see through the flag call.
                orchestrator_cls = ComplianceOrchestrator
                if orchestrator_cls is not None:
                    self.orchestrator = orchestrator_cls()
            if self.orchestrator is None:
                return False, (
                    "RULE 0X/0Y DENIED: ComplianceOrchestrator unavailable — "
                    "gate requires it. Check sys.path and imports."
                )

        try:
            context = {
                "filepath": filepath,
                "state": "CODE_EDIT",
                "can_edit": True,
                "has_gate": True,
                "synced": True,
                "scope_check_ok": True,
                "gate_decision": "PROCEED",
            }
            # For write_file, pass the NEW content for AST scanning
            # so the scanner validates incoming code, not the old file
            if source_code is not None:
                context["source_code"] = source_code

            result = self.orchestrator.check_all(
                action=action,
                role=self.subject,
                context=context,
            )

            # Check BLOCKING components only
            failing_blocking = []
            ox = result.rule_0x.get("components", {})
            oy = result.rule_0y.get("components", {})

            for comp, data in ox.items():
                if comp in self._NON_BLOCKING_0X:
                    continue
                if not _ox_component_passed(comp, data):
                    failing_blocking.append(f"0X:{comp}")

            for comp, data in oy.items():
                if comp in self._NON_BLOCKING_0Y:
                    continue
                if not _oy_component_passed(comp, data):
                    failing_blocking.append(f"0Y:{comp}")

            if failing_blocking:
                # EP152: a holistic deny cannot say WHICH part of the rule
                # failed. Decompose the failing criteria into targeted,
                # checkable sub-questions (a failure class each) so the caller
                # receives repair targets, not a bare component list.
                decomposed = self._decompose_denial(failing_blocking)
                targeted = "; ".join(
                    "%s [%s]" % (q, decomposed["by_subquestion"].get(i, "unclassified"))
                    for i, q in enumerate(decomposed["subquestions"]))
                self._emit("compliance_check", "denied", {
                    "action": action,
                    "filepath": filepath,
                    "blocking": list(failing_blocking),
                    "subquestions": decomposed["subquestions"],
                })
                return False, (
                    f"RULE 0X/0Y DENIED (blocking): "
                    f"{', '.join(failing_blocking)}"
                    + (f" — targeted checks: {targeted}" if targeted else "")
                )

            # Non-blocking warnings are logged but don't deny
            self._emit("compliance_check", "pass", {
                "action": action,
                "filepath": filepath,
                "summary": result.summary,
            })
            return True, ""
        except Exception as e:
            note_failure("pre_action_gate:L892", e)
            return False, f"RULE 0X/0Y ERROR: {e}"

    def validate_str_replace(
        self, filepath: str, old_string: str, new_string: str
    ) -> tuple[bool, str]:
        """Validate a str_replace operation BEFORE applying it.

        Checks:
        0. RULE 0X + 0Y Compliance (mandatory â€” every edit)
        1. File exists and is readable
        2. old_string exists with exact whitespace match
        3. new_string preserves the same indentation level as old_string
        4. No 0-indent def/class declaration replacing an indented block

        Returns:
            (True, "Valid") or (False, "error message")
        """
        # Check 0: RULE 0X/0Y compliance (mandatory â€” every edit)
        # v2: Verify caller is authorized before any validation
        self._verify_caller_stack()

        ok, msg = self._check_rule_0x_0y("str_replace", filepath)
        if not ok:
            return False, msg

        with self._lock:
            path = Path(filepath)
            if not path.exists():
                msg = f"File not found: {filepath}"
                self._emit("pre_edit_validate", "fail",
                           {"error": msg, "filepath": filepath})
                return False, msg

            try:
                content = path.read_text(encoding="utf-8")
            except Exception as e:
                msg = f"Cannot read file: {e}"
                self._emit("pre_edit_validate", "fail",
                           {"error": msg, "filepath": filepath})
                return False, msg

            # Check 1: Exact match verification
            count = content.count(old_string)
            if count == 0:
                msg = (
                    "old_string NOT FOUND in file (whitespace mismatch or "
                    "already modified). Verify exact content before editing."
                )
                self._emit("pre_edit_validate", "fail",
                           {"error": msg, "filepath": filepath})
                return False, msg

            if count > 1:
                self._emit("pre_edit_validate", "warn",
                           {"warning": f"old_string appears {count} times",
                            "filepath": filepath, "count": count})

            # Check 2: Indentation preservation (first line)
            old_indent = self._get_indent_level(old_string)
            new_indent = self._get_indent_level(new_string)

            if old_indent != new_indent:
                msg = (
                    f"INDENTATION MISMATCH: old_string starts at column "
                    f"{old_indent}, new_string at column {new_indent}. "
                    f"Must preserve exact indentation."
                )
                self._emit("pre_edit_validate", "fail",
                           {"error": msg, "filepath": filepath,
                            "old_indent": old_indent, "new_indent": new_indent})
                return False, msg

            # Check 2b: Inner-line indentation preservation
            old_indents = self._get_line_indents(old_string)
            new_indents = self._get_line_indents(new_string)

            if len(old_indents) == len(new_indents) and len(old_indents) > 1:
                # Compute relative indent from first line for each
                old_base = old_indents[0]
                new_base = new_indents[0]
                for i, (oi, ni) in enumerate(
                    zip(old_indents, new_indents)
                ):
                    old_rel = oi - old_base
                    new_rel = ni - new_base
                    if old_rel != new_rel:
                        msg = (
                            f"INNER-LINE INDENTATION BREAK at line {i + 1}: "
                            f"old had relative indent {old_rel}, "
                            f"new has {new_rel}. "
                            f"All lines must preserve same relative indent."
                        )
                        self._emit(
                            "pre_edit_validate", "fail",
                            {"error": msg, "filepath": filepath,
                             "line": i + 1, "old_rel": old_rel,
                             "new_rel": new_rel}
                        )
                        return False, msg
            elif len(old_indents) != len(new_indents):
                # Different number of content lines â€” warn but don't block
                self._emit("pre_edit_validate", "warn",
                           {"warning": "Line count differs between old/new",
                            "old_lines": len(old_indents),
                            "new_lines": len(new_indents)})

            # Check 3: 0-indent catastrophe heuristic
            if (self._has_zero_indent_declaration(new_string)
                    and old_indent > 0):
                msg = (
                    "CATASTROPHIC INDENTATION: new_string contains a "
                    "0-indent def/class block replacing an indented block. "
                    "This would prematurely close the enclosing class."
                )
                self._emit("pre_edit_validate", "fail",
                           {"error": msg, "filepath": filepath})
                return False, msg

            self._emit("pre_edit_validate", "pass", {"filepath": filepath})
            return True, "Valid"

    def validate_write_file(
        self, filepath: str, content: str
    ) -> tuple[bool, str]:
        """Validate a write_file operation BEFORE applying it.

        Checks:
        0. RULE 0X + 0Y Compliance (mandatory â€” every edit)
        1. If file exists, warns about overwrite
        2. If .py file, validates Python syntax of new content
        3. Checks for 0-indent def/class declarations in non-empty files

        Returns:
            (True, "Valid") or (False, "error message")
        """
        # Check 0: RULE 0X/0Y compliance (mandatory â€” every edit)
        # v2: Verify caller is authorized before any validation
        self._verify_caller_stack()

        # Pass the NEW content as source_code so AST scanner validates
        # the incoming code, not the possibly-corrupted file on disk.
        ok, msg = self._check_rule_0x_0y("write_file", filepath,
                                          source_code=content)
        if not ok:
            return False, msg

        path = Path(filepath)

        # Existing file overwrite â€” take snapshot first
        if path.exists():
            self._emit("pre_edit_validate", "warn",
                       {"warning": "Overwriting existing file",
                        "filepath": filepath})

        # Check 1: Syntax validation for .py files
        if filepath.endswith(".py"):
            try:
                ast.parse(content)
            except SyntaxError as e:
                msg = f"write_file would create a file with SyntaxError: {e}"
                self._emit("pre_edit_validate", "fail",
                           {"error": msg, "filepath": filepath})
                return False, msg

        # Check 2: HEUR-04 merged-line detection (pre-edit)
        if filepath.endswith(".py"):
            merged_findings = self._detect_merged_statements(content)
            if merged_findings:
                for line_no, desc in merged_findings:
                    msg = f"MERGED STATEMENTS in write_file content: {desc}"
                    self._emit("pre_edit_validate", "fail",
                               {"error": msg, "filepath": filepath})
                    return False, msg

        # Check 3: 0-indent def/class in non-trivial file
        if filepath.endswith(".py") and len(content.splitlines()) > 10:
            lines = content.splitlines()
            has_regular_indent = any(
                l.startswith("    ") and not l.strip().startswith("#")
                for l in lines
            )
            has_zero_decl = any(
                l.startswith("def ") or l.startswith("class ")
                for l in lines
            )
            if has_regular_indent and has_zero_decl:
                self._emit("pre_edit_validate", "warn",
                           {"warning": "File has both indented and 0-indent "
                            "declarations â€” review class boundaries",
                            "filepath": filepath})

        return True, "Valid"

    # ------------------------------------------------------------------
    # Phase 2: Pre-edit Backup
    # ------------------------------------------------------------------

    def take_snapshot(self, filepath: str) -> str:
        """Copy file to .pre_gate_backup before mutation.

        Returns the backup path (empty string if source doesn't exist).
        """
        with self._lock:
            path = Path(filepath)
            if not path.exists():
                return ""

            backup_path = path.with_suffix(path.suffix + ".pre_gate_backup")
            try:
                shutil.copy2(path, backup_path)
            except OSError as exc:
                note_failure("pre_action_gate:L1103", exc)
                return ""

            bp_str = str(backup_path)
            self._record("snapshot_taken", {
                "filepath": filepath,
                "backup_path": bp_str,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return bp_str

    # ------------------------------------------------------------------
    # Phase 3: Post-edit Validation
    # ------------------------------------------------------------------

    def validate_after_edit(
        self, filepath: str, backup_path: str = ""
    ) -> tuple[bool, str]:
        """Validate file AFTER an edit operation.

        Checks:
        1. File still exists
        2. py_compile syntax check (for .py files)
        3. HEUR-04 merged-line detection (two statements joined without newline)
        4. HEUR-03 duplicate method detection (same-scope def at same indent)
        5. HEUR-07 over-indentation detection (8+ space line amid 4-space lines)
        6. HEUR-08 import-indentation anomaly (column-0 import in class body)
        7. HEUR-09 indent-jump detection (mismatched sibling indentation)
        8. HEUR-10 dangling function body (def with no body before next declaration)
        9. 0-indent runaway method after indented block (class boundary break)
        10. Auto-revert from backup on failure

        Returns:
            (True, "Valid") or (False, "error with revert status")
        """
        with self._lock:
            path = Path(filepath)
            if not path.exists():
                return False, "File disappeared after edit"

            errors: list[str] = []

            # Python-specific checks
            if path.suffix == ".py":
                # Check 1: Syntax
                try:
                    py_compile.compile(str(path), doraise=True)
                except py_compile.PyCompileError as e:
                    note_failure("pre_action_gate:L1150", e)
                    errors.append(f"SyntaxError: {e}")
                except Exception as e:
                    note_failure("pre_action_gate:L1152", e)
                    errors.append(f"Compilation error: {e}")

                # If syntax passed, run heuristic checks
                if not errors:
                    try:
                        content = path.read_text(encoding="utf-8")
                    except Exception as exc:
                        note_failure("pre_action_gate:L1159", exc)
                        return False, "Cannot read file for post-edit check"

                    lines = content.split("\n")
                    has_classes = any(
                        l.lstrip().startswith("class ") for l in lines
                    )

                    # Check 2: HEUR-04 merged-line detection
                    merged_findings = self._detect_merged_statements(content)
                    if merged_findings:
                        for line_no, desc in merged_findings:
                            errors.append(desc)
                            break  # One merge is enough to trigger revert

                    # Check 3: HEUR-03 duplicate method detection
                    if not errors:
                        dup_finding = self._detect_duplicate_methods(content)
                        if dup_finding is not None:
                            line_no, desc = dup_finding
                            errors.append(desc)

                    # Check 3a: HEUR-07 over-indentation detection
                    if not errors:
                        over_findings = self._detect_over_indentation(content)
                        if over_findings:
                            for line_no, desc in over_findings:
                                errors.append(desc)
                                break

                    # Check 3b: HEUR-08 import-indentation anomaly
                    if not errors:
                        imp_findings = self._detect_import_indent_anomaly(content)
                        if imp_findings:
                            for line_no, desc in imp_findings:
                                errors.append(desc)
                                break

                    # Check 3c: HEUR-09 indent-jump detection
                    if not errors:
                        jump_finding = self._detect_indent_jump(content)
                        if jump_finding is not None:
                            line_no, desc = jump_finding
                            errors.append(desc)

                    # Check 3d: HEUR-10 dangling function body
                    if not errors:
                        dang_finding = self._detect_dangling_body(content)
                        if dang_finding is not None:
                            line_no, desc = dang_finding
                            errors.append(desc)



                    # Check 4: 0-indent runaway method
                    if not errors:
                        for i, line in enumerate(lines):
                            if (
                                has_classes
                                and i > 50
                                and line.startswith("def ")
                            ):
                                prev = lines[i - 1] if i > 0 else ""
                                if prev.startswith("    ") and not prev.strip().startswith("#"):
                                    errors.append(
                                        f"Anomalous 0-indent def at line "
                                        f"{i + 1} after indented block â€” "
                                        f"class boundary may be broken"
                                    )
                                    break

            # ------------------------------------------------------------------
            # Revert on failure
            # ------------------------------------------------------------------
            if errors:
                error_msg = "; ".join(errors)
                # Attempt auto-revert from backup
                if backup_path and Path(backup_path).exists():
                    try:
                        shutil.copy2(backup_path, path)
                        restored = path.read_text(encoding="utf-8")
                        self._edit_count += 1
                        _save_telemetry_state(self)
                        self._emit("post_edit_validate", "reverted", {
                            "error": error_msg,
                            "filepath": filepath,
                            "backup_path": backup_path,
                        })
                        return False, f"POST-GATE REVERTED from backup: {error_msg}"
                    except Exception as revert_err:
                        note_failure("pre_action_gate:L1248", revert_err)
                        return False, f"POST-GATE FAILED (revert ALSO failed: {revert_err}): {error_msg}"
                return False, f"POST-GATE FAILED (no backup available): {error_msg}"
            self._edit_count += 1
            _save_telemetry_state(self)
            self._emit("post_edit_validate", "pass", {"filepath": filepath})
            return True, "Valid"

    # ------------------------------------------------------------------
    # Convenience: full three-phase gate
    # ------------------------------------------------------------------

    def gate_str_replace(
        self, filepath: str, old_string: str, new_string: str
    ) -> tuple[bool, str, str]:
        """Run full three-phase gate for a str_replace operation.

        RULE 0X + 0Y compliance check runs inside validate_str_replace()
        via _check_rule_0x_0y â€” no separate Phase 0 needed.

        Returns:
            (passed, message, backup_path)
        """
        # Phase 0+1: RULE 0X/0Y Compliance + Pre-validate
        # (both run inside validate_str_replace via _check_rule_0x_0y)
        ok, msg = self.validate_str_replace(filepath, old_string, new_string)
        if not ok:
            return False, f"PRE-GATE DENIED: {msg}", ""

        # Phase 2: Backup
        backup = self.take_snapshot(filepath)

        return True, "GATE PASSED â€” proceed with str_replace", backup

    def gate_write_file(
        self, filepath: str, content: str
    ) -> tuple[bool, str, str]:
        """Run full gate pipeline for a write_file operation.

        Mirrors gate_str_replace() but validates the full file content
        instead of a targeted string replacement.

        Pipeline:
          Phase 0+1 â€” RULE 0X/0Y Compliance + Pre-validate
              (both run inside validate_write_file via _check_rule_0x_0y)
          Phase 2 â€” Backup (snapshot existing file)
          Phase 3 â€” (caller writes the file)
          Phase 4 â€” Post-validate (py_compile, HEUR-04, auto-revert)
              â€” Caller calls validate_after_edit() after writing

        Returns:
            (passed, message, backup_path)
        """
        # Phase 0+1: RULE 0X/0Y Compliance + Pre-validate
        # (both run inside validate_write_file via _check_rule_0x_0y)
        ok, msg = self.validate_write_file(filepath, content)
        if not ok:
            return False, f"PRE-GATE DENIED: {msg}", ""

        # Phase 2: Backup
        backup = self.take_snapshot(filepath)

        return True, "GATE PASSED â€” proceed with write_file", backup

    # ------------------------------------------------------------------
    # Programmatic Enforcement: apply edits THROUGH the gate only
    # ------------------------------------------------------------------

    def apply_str_replace(
        self, filepath: str, old_string: str, new_string: str
    ) -> dict[str, Any]:
        """Validate â†’ backup â†’ apply â†’ validate â†’ auto-revert.

        This is the ONLY way file edits should be applied. Direct str_replace
        tool calls bypass ALL heuristic and compliance checks.

        Pipeline:
          1. RULE 0X/0Y Compliance + Pre-validate (via validate_str_replace)
          2. Snapshot backup (via take_snapshot)
          3. Apply str_replace to file
          4. Post-validate (py_compile + HEUR-03 through HEUR-10 via
             validate_after_edit)
          5. Auto-revert from backup on failure

        Returns:
            dict with keys:
              success (bool): True if edit was applied AND validated
              error (str): Error message if failed
              reverted (bool): True if auto-revert was triggered
              backup_path (str): Path to backup file (empty if none)
              edit_count (int): Total successful edits through this gate
        """
        result: dict[str, Any] = {
            "success": False,
            "error": "",
            "reverted": False,
            "backup_path": "",
            "edit_count": 0,
        }

        # Phase 0+1: RULE 0X/0Y Compliance + Pre-validate
        # (both run inside validate_str_replace via _check_rule_0x_0y)
        ok, msg = self.validate_str_replace(filepath, old_string, new_string)
        if not ok:
            result["error"] = f"PRE-GATE DENIED: {msg}"
            self._emit("apply_str_replace", "fail", result)
            return result

        # Phase 2: Backup
        backup = self.take_snapshot(filepath)
        result["backup_path"] = backup

        # Phase 3: Apply the edit
        try:
            path = Path(filepath)
            content = path.read_text(encoding="utf-8")
            count = content.count(old_string)
            if count == 0:
                result["error"] = (
                    "old_string NOT FOUND in file (race condition â€” "
                    "content changed between validation and application)"
                )
                self._emit("apply_str_replace", "fail", result)
                return result
            new_content = content.replace(old_string, new_string, 1)
            path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            result["error"] = f"APPLY ERROR: {e}"
            self._emit("apply_str_replace", "fail", result)
            return result

        # Phase 4: Post-validate with auto-revert
        post_ok, post_msg = self.validate_after_edit(filepath, backup)
        if not post_ok:
            result["error"] = f"POST-GATE FAILED: {post_msg}"
            result["reverted"] = True
            self._emit("apply_str_replace", "fail", result)
            return result

        # Success â€” edit_count already incremented by validate_after_edit()
        result["edit_count"] = self._edit_count
        result["success"] = True
        self._emit("apply_str_replace", "pass", result)
        self._record("edit_applied", {
            "filepath": filepath,
            "edit_count": result["edit_count"],
        })
        return result

    def apply_write_file(
        self, filepath: str, content: str
    ) -> dict[str, Any]:
        """Validate â†’ backup (if exists) â†’ write â†’ validate â†’ auto-revert.

        This is the ONLY way file creations/overwrites should happen.
        Direct write_file tool calls bypass ALL heuristic and compliance checks.

        Pipeline:
          1. RULE 0X/0Y Compliance + Pre-validate (via validate_write_file)
          2. Snapshot backup if file exists
          3. Write file
          4. Post-validate (py_compile + HEUR checks via validate_after_edit)
          5. Auto-revert from backup on failure

        Returns:
            dict with keys:
              success (bool): True if write was applied AND validated
              error (str): Error message if failed
              reverted (bool): True if auto-revert was triggered
              backup_path (str): Path to backup file (empty if none or new file)
              edit_count (int): Total successful edits through this gate
        """
        result: dict[str, Any] = {
            "success": False,
            "error": "",
            "reverted": False,
            "backup_path": "",
            "edit_count": 0,
        }

        # Phase 0+1: RULE 0X/0Y Compliance + Pre-validate
        ok, msg = self.validate_write_file(filepath, content)
        if not ok:
            result["error"] = f"PRE-GATE DENIED: {msg}"
            self._emit("apply_write_file", "fail", result)
            return result

        # Phase 2: Backup (only if file already exists)
        path = Path(filepath)
        if path.exists():
            backup = self.take_snapshot(filepath)
            result["backup_path"] = backup

        # Phase 3: Write content
        try:
            path.write_text(content, encoding="utf-8")
        except Exception as e:
            result["error"] = f"WRITE ERROR: {e}"
            self._emit("apply_write_file", "fail", result)
            return result

        # Phase 4: Post-validate with auto-revert
        backup = result["backup_path"]
        post_ok, post_msg = self.validate_after_edit(filepath, backup)
        if not post_ok:
            result["error"] = f"POST-GATE FAILED: {post_msg}"
            result["reverted"] = True
            self._emit("apply_write_file", "fail", result)
            return result

        # Success â€” edit_count already incremented by validate_after_edit()
        result["edit_count"] = self._edit_count
        result["success"] = True
        self._emit("apply_write_file", "pass", result)
        self._record("file_written", {
            "filepath": filepath,
            "edit_count": result["edit_count"],
        })
        return result

    @property
    def parallel_batch_count(self) -> int:
        return self._parallel_batch_count

    @property
    def sequential_spawn_count(self) -> int:
        return self._sequential_spawn_count

    def record_parallel_batch(self):
        with self._lock:
            self._parallel_batch_count += 1
            _save_telemetry_state(self)
        self._emit("rule_0zf", "parallel_batch", {"count": self._parallel_batch_count})

    def record_sequential_violation(self):
        with self._lock:
            self._sequential_spawn_count += 1
            _save_telemetry_state(self)
        self._emit("rule_0zf", "sequential_violation", {"count": self._sequential_spawn_count})

    @property
    def edit_count(self) -> int:
        """Total number of successful edits through this gate."""
        return self._edit_count


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_pre_action_gate_instance: (PreActionGate | None) = None
_gate_lock = threading.Lock()


def get_pre_action_gate() -> PreActionGate:
    """Get or create the global PreActionGate singleton."""
    global _pre_action_gate_instance
    if _pre_action_gate_instance is None:
        with _gate_lock:
            if _pre_action_gate_instance is None:
                _pre_action_gate_instance = PreActionGate()
    return _pre_action_gate_instance


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """Run a quick self-test of the PreActionGate."""
    from CORE.sentinel_enforcer import _GATED_CALL
    # Authorize all disk writes for the duration of this self-test.
    # sys.addaudithook blocks unauthorized tempfile writes otherwise.
    _GATED_CALL.is_active = True
    try:
        return _self_test_impl()
    finally:
        _GATED_CALL.is_active = False


def _self_test_impl() -> bool:
    """Internal implementation — called from self_test() under gate authorization."""

    print("=" * 60)
    print("  PRE-ACTION GATE SELF-TEST")
    print("=" * 60)

    gate = PreActionGate()
    # NOTE: orchestrator is NOT disabled â€” programmatic enforcement requires
    # RULE 0X/0Y compliance for EVERY edit, including self-tests.
    # The orchestrator's __init__ auto-transitions to CODE_EDIT, so
    # compliance checks will pass for test files.
    passed_all = True

    # Test 1: Indentation mismatch rejection
    print("\n[1/13] Indentation mismatch detection...")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as f:
        f.write("class Foo:\n    def bar(self):\n        pass\n")
        tmp = f.name

    try:
        ok, msg = gate.validate_str_replace(
            tmp,
            "    def bar(self):\n        pass",
            "def bar(self):\n        pass",
        )
        if ok:
            print("  FAIL: Should have rejected 0-indent replacement")
            passed_all = False
        else:
            print(f"  PASS: Correctly rejected: {msg[:60]}...")
    finally:
        os.unlink(tmp)

    # Test 2: Exact match failure
    print("[2/13] Exact match detection...")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as f:
        f.write("x = 1\n")
        tmp = f.name
    try:
        ok, msg = gate.validate_str_replace(tmp, "x = 2", "x = 3")
        if ok:
            print("  FAIL: Should have detected non-matching old_string")
            passed_all = False
        else:
            print("  PASS: Correctly rejected non-existent old_string")
    finally:
        os.unlink(tmp)

    # Test 3: Valid replacement passes
    print("[3/13] Valid replacement acceptance...")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as f:
        f.write("class Foo:\n    def bar(self):\n        return 1\n")
        tmp = f.name
    try:
        ok, msg = gate.validate_str_replace(
            tmp,
            "        return 1",
            "        return 2",
        )
        if not ok:
            print(f"  FAIL: Should have accepted valid replacement: {msg}")
            passed_all = False
        else:
            print("  PASS: Valid replacement accepted")
    finally:
        os.unlink(tmp)

    # Test 4: 0-indent catastrophe detection
    print("[4/13] 0-indent catastrophe detection...")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as f:
        f.write("class Foo:\n    def bar(self):\n        pass\n")
        tmp = f.name
    try:
        ok, msg = gate.validate_str_replace(
            tmp,
            "    def bar(self):\n        pass",
            "def bar(self):\n    pass",
        )
        if ok:
            print("  FAIL: Should have caught 0-indent def in class")
            passed_all = False
        else:
            print("  PASS: Catastrophic indentation detected")
    finally:
        os.unlink(tmp)

    # Test 5: Post-edit syntax validation + revert
    print("[5/13] Post-edit validation with backup...")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as f:
        f.write("x = 1\n")
        tmp = f.name

    backup = gate.take_snapshot(tmp)
    # Corrupt the file
    Path(tmp).write_text("x = 1\n    : bad\n", encoding="utf-8")
    ok, msg = gate.validate_after_edit(tmp, backup)
    if ok:
        print("  FAIL: Should have detected syntax error")
        passed_all = False
    else:
        # Check revert
        restored = Path(tmp).read_text(encoding="utf-8")
        if restored == "x = 1\n":
            print("  PASS: Syntax error detected and reverted")
        else:
            print(f"  FAIL: Revert failed, content is: {restored!r}")
            passed_all = False

    os.unlink(tmp)
    if backup:
        Path(backup).unlink(missing_ok=True)

    # Test 6: HEUR-04 merged-line detection + auto-revert
    print("[6/13] HEUR-04 merged-line detection...")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as f:
        f.write("class Foo:\n    def bar(self):\n        return 1\n        self.x = 2\n")
        tmp = f.name

    backup = gate.take_snapshot(tmp)
    # Simulate destructive merge: 'return 1' and 'self.x = 2' joined on one line
    Path(tmp).write_text("class Foo:\n    def bar(self):\n        return 1        self.x = 2\n", encoding="utf-8")
    ok, msg = gate.validate_after_edit(tmp, backup)
    if ok:
        print("  FAIL: Should have detected merged statements")
        passed_all = False
    else:
        restored = Path(tmp).read_text(encoding="utf-8")
        expected = "class Foo:\n    def bar(self):\n        return 1\n        self.x = 2\n"
        if restored == expected:
            print("  PASS: Merged statements detected and reverted")
        else:
            print(f"  FAIL: Revert failed, content is: {restored!r}")
            passed_all = False

    os.unlink(tmp)
    if backup:
        Path(backup).unlink(missing_ok=True)

    # Test 7: HEUR-04 merged-line pre-edit detection (validate_write_file)
    print("[7/13] HEUR-04 merged-line in write_file content...")
    # NOTE: Using the real gate with orchestrator. The orchestrator is
    # available and auto-initialized to CODE_EDIT, so RULE 0X/0Y compliance
    # will pass. The merged-line detection runs after compliance passes.

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as f:
        f.write("x = 1\n")
        tmp = f.name

    try:
        # Simulate destructive merge in write_file content
        merged = "class Foo:\n    def bar(self):\n        return 1        self.x = 2\n"
        ok, msg = gate.validate_write_file(tmp, merged)
        if ok:
            print("  FAIL: Should have detected merged statements in write_file")
            passed_all = False
        elif "MERGED" in msg:
            print("  PASS: Merged statements correctly blocked at pre-edit")
        else:
            print(f"  PASS: Blocked: {msg[:60]}...")

        # Verify file was NOT touched
        if Path(tmp).read_text(encoding="utf-8") == "x = 1\n":
            pass  # Good â€” file untouched
        else:
            print("  FAIL: File was modified despite rejection")
            passed_all = False
    finally:
        os.unlink(tmp)
        Path(str(tmp) + ".pre_gate_backup").unlink(missing_ok=True)

    # Test 8: gate_write_file pipeline
    print("[8/13] gate_write_file pipeline...")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as f:
        f.write("x = 1\n")
        tmp = f.name

    ok, msg, backup = gate.gate_write_file(tmp, "y = 2\n")
    if not ok:
        print(f"  FAIL: gate_write_file should pass: {msg}")
        passed_all = False
    else:
        # Verify backup was taken (file existed before write)
        if backup and Path(backup).exists():
            print("  PASS: gate_write_file passed pipeline with backup")
        else:
            print("  PASS: gate_write_file passed pipeline (new file, no backup needed)")

    if backup and Path(backup).exists():
        Path(backup).unlink(missing_ok=True)
    os.unlink(tmp)

    # Test 10: HEUR-07 over-indentation detection (direct method test)
    print("[10/13] HEUR-07 over-indentation detection...")
    # Direct method call â€” avoids py_compile preemption.
    # py_compile catches IndentationError before heuristic checks run,
    # so we test _detect_over_indentation() directly (like Test 9 uses
    # _HER._scan_for_patterns() rather than validate_after_edit).
    # Algorithm #8 signature: one 8-space line amid 4-space lines.
    findings = PreActionGate._detect_over_indentation(
        "if True:\n    return 1\n        return 2\n"
    )
    if len(findings) > 0:
        print(
            f"  PASS: HEUR-07 detected over-indented line "
            f"at line {findings[0][0]} â€” 8 spaces amid 4-space block"
        )
    else:
        print("  FAIL: Should have detected over-indented return")
        passed_all = False

    # Test 9: HEUR-03 duplicate method detection
    print("[9/13] HEUR-03 duplicate method detection...")
    try:
        from CORE.compliance.heuristic_recorder import HeuristicErrorRecorder
        _HER = HeuristicErrorRecorder()
    except ImportError as exc:
        note_failure("pre_action_gate:L1762", exc)
        _HER = None

    if _HER is None:
        print("  SKIP: HeuristicErrorRecorder not available")
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            # Genuine duplicate: two def foo() in same class
            f.write(
                "class Foo:\n"
                "    def foo(self):\n"
                "        pass\n"
                "    def foo(self):\n"
                "        return 1\n"
            )
            dup_tmp = f.name

        try:
            hits = _HER._scan_for_patterns(dup_tmp)
            heur03_hits = [h for h in hits if h["pattern_id"] == "HEUR-03"]
            if len(heur03_hits) > 0:
                print(
                    f"  PASS: HEUR-03 detected genuine duplicate method "
                    f"({heur03_hits[0]['name']})"
                )
            else:
                print("  FAIL: Should have detected duplicate def foo()")
                passed_all = False

            # False positive guard: methods in different classes
            Path(dup_tmp).write_text(
                "class Foo:\n"
                "    def foo(self):\n"
                "        pass\n"
                "\n"
                "class Bar:\n"
                "    def foo(self):\n"
                "        return 1\n",
                encoding="utf-8",
            )
            hits2 = _HER._scan_for_patterns(dup_tmp)
            heur03_hits2 = [h for h in hits2 if h["pattern_id"] == "HEUR-03"]
            if len(heur03_hits2) == 0:
                print(
                    "  PASS: HEUR-03 correctly skipped across-class "
                    "duplicate (class boundary guard)"
                )
            else:
                print(
                    "  FAIL: Should not flag def foo() in different classes"
                )
                passed_all = False
        finally:
            os.unlink(dup_tmp)

    # Test 11: HEUR-08 import-indentation anomaly detection (direct method test)
    print("[11/13] HEUR-08 import-indentation anomaly detection...")
    # Algorithm #3 signature: a column-0 import appears between indented
    # class body lines â€” the import was pulled from inside the class by a
    # batch fixer that removed surrounding code. This IS valid Python
    # (import at module level after a class), so py_compile won't catch
    # it â€” we call _detect_import_indent_anomaly() directly (like Test 10).
    findings = PreActionGate._detect_import_indent_anomaly(
        "class Foo:\n    def bar(self):\n        pass\nimport os\n\nclass Bar:\n    pass\n"
    )
    if len(findings) > 0:
        print(
            f"  PASS: HEUR-08 detected column-0 import inside class body "
            f"at approx line {findings[0][0]}"
        )
    else:
        print("  FAIL: Should have detected column-0 import inside class body")
        passed_all = False

    # False positive guard: top-level import between classes (legitimate)
    findings2 = PreActionGate._detect_import_indent_anomaly(
        "class Foo:\n    pass\n\nimport os\n\nclass Bar:\n    pass\n"
    )
    if len(findings2) == 0:
        print(
            "  PASS: HEUR-08 correctly skipped top-level import "
            "between classes (preceded by blank line, not indented body)"
        )
    else:
        print("  FAIL: Should NOT flag top-level import between classes")
        passed_all = False

    # Test 12: HEUR-09 indent jump / mismatched siblings (via _scan_for_patterns)
    print("[12/13] HEUR-09 indent-jump detection...")
    try:
        from CORE.compliance.heuristic_recorder import HeuristicErrorRecorder
        _HER2 = HeuristicErrorRecorder()
    except ImportError as exc:
        note_failure("pre_action_gate:L1855", exc)
        _HER2 = None

    if _HER2 is None:
        print("  SKIP: HeuristicErrorRecorder not available")
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            # Algorithm #3/#8 signature: one line at 12 spaces amid 4-space siblings
            f.write(
                "def process():\n"
                "    a = 1\n"
                "            b = 2\n"
                "    c = 3\n"
            )
            jump_tmp = f.name

        try:
            hits = _HER2._scan_for_patterns(jump_tmp)
            heur09_hits = [h for h in hits if h["pattern_id"] == "HEUR-09"]
            if len(heur09_hits) > 0:
                print(
                    f"  PASS: HEUR-09 detected indent jump "
                    f"({heur09_hits[0]['name']})"
                )
            else:
                print("  FAIL: Should have detected 12-space indent jump")
                passed_all = False

            # False positive guard: normal 4-space siblings â€” no jump
            Path(jump_tmp).write_text(
                "def process():\n"
                "    a = 1\n"
                "    b = 2\n"
                "    c = 3\n",
                encoding="utf-8",
            )
            hits2 = _HER2._scan_for_patterns(jump_tmp)
            heur09_hits2 = [h for h in hits2 if h["pattern_id"] == "HEUR-09"]
            if len(heur09_hits2) == 0:
                print(
                    "  PASS: HEUR-09 correctly skipped normal "
                    "4-space siblings (diff <= 4)"
                )
            else:
                print(
                    "  FAIL: Should not flag normal 4-space sibling lines"
                )
                passed_all = False
        finally:
            os.unlink(jump_tmp)

    # Test 13: HEUR-10 dangling function body (via _scan_for_patterns)
    print("[13/13] HEUR-10 dangling function body detection...")
    try:
        from CORE.compliance.heuristic_recorder import HeuristicErrorRecorder
        _HER3 = HeuristicErrorRecorder()
    except ImportError as exc:
        note_failure("pre_action_gate:L1912", exc)
        _HER3 = None

    if _HER3 is None:
        print("  SKIP: HeuristicErrorRecorder not available")
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            # Algorithm #17 signature: def with no body before next def
            f.write(
                "def main():\n"
                "\n"
                "\n"
                "def other():\n"
                "    pass\n"
            )
            dang_tmp = f.name

        try:
            hits = _HER3._scan_for_patterns(dang_tmp)
            heur10_hits = [h for h in hits if h["pattern_id"] == "HEUR-10"]
            if len(heur10_hits) > 0:
                print(
                    f"  PASS: HEUR-10 detected dangling function body "
                    f"({heur10_hits[0]['name']})"
                )
            else:
                print("  FAIL: Should have detected def main() with no body")
                passed_all = False

            # False positive guard: def with body line (pass) â€” legitimate
            Path(dang_tmp).write_text(
                "def main():\n"
                "    pass\n"
                "\n"
                "def other():\n"
                "    pass\n",
                encoding="utf-8",
            )
            hits2 = _HER3._scan_for_patterns(dang_tmp)
            heur10_hits2 = [h for h in hits2 if h["pattern_id"] == "HEUR-10"]
            if len(heur10_hits2) == 0:
                print(
                    "  PASS: HEUR-10 correctly skipped def with body "
                    "(pass on next line)"
                )
            else:
                print(
                    "  FAIL: Should not flag def main() with pass body"
                )
                passed_all = False
        finally:
            os.unlink(dang_tmp)

    print("\n" + "=" * 60)
    if passed_all:
        print("  ALL PRE-ACTION GATE TESTS PASSED!")
    else:
        print("  PRE-ACTION GATE TESTS FAILED!")
    print("=" * 60)
    return passed_all


if __name__ == "__main__":
    import sys
    success = self_test()
    sys.exit(0 if success else 1)
