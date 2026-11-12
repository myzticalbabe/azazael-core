"""
heuristic_recorder.py — Heuristic Edit Error Tracking

Permanently catalogs and detects destructive AI text-replacement patterns
(indent breaks, lost methods, merged lines) to prevent systematic regressions.

Key features:
1. Pattern Registry — JSON catalog of known destructive edit patterns
2. Pre-edit Recording — logs every str_replace/write_file attempt to JSONL + DirectMemory
3. Post-edit Recording — py_compile syntax check + heuristic pattern detection
4. Escalation Protocol — detected errors written to ERROR_LOG.md + Telemetry + DirectMemory
5. Session Summary — groups errors by pattern type for reporting

Integration: called by PreActionGate before/after every edit.
Importable from CORE.compliance.heuristic_recorder.
"""

import json
import hashlib
import os
import re
import uuid
import py_compile
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

# Optional CORE imports — graceful fallback if unavailable
try:
    from CORE.direct_memory import DirectMemory
    _MEMORY_AVAILABLE = True
except ImportError as exc:
    note_failure("heuristic_recorder:L33", exc)
    _MEMORY_AVAILABLE = False
    DirectMemory = None

try:
    from CORE.compliance.telemetry import ComplianceTelemetry
    _TELEMETRY_AVAILABLE = True
except ImportError as exc:
    note_failure("heuristic_recorder:L40", exc)
    _TELEMETRY_AVAILABLE = False
    ComplianceTelemetry = None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _get_project_root() -> Path:
    """Find the azazael project root (parent of src/)."""
    # When running from src/CORE/compliance/, go up 3 levels
    here = Path(__file__).resolve().parent  # compliance/
    for _ in range(3):
        here = here.parent
    return here


_PROJECT_ROOT = _get_project_root()
_AUDIT_DIR = _PROJECT_ROOT / ".audit"
_LOG_FILE = _AUDIT_DIR / "heuristic_edits.jsonl"
_REGISTRY_FILE = _AUDIT_DIR / "heuristic_patterns.json"
_ERROR_LOG_PATH = _PROJECT_ROOT / "ERROR_LOG.md"


# Pattern version — bump to force cache-bust when DEFAULT_PATTERNS change
_PATTERN_VERSION = 6


def _compute_pattern_fingerprint(patterns: list[dict[str, Any]]) -> str:
    """Compute a stable SHA256 fingerprint of the pattern catalog.

    Used by the self-test to detect when DEFAULT_PATTERNS were modified
    but _PATTERN_VERSION wasn't bumped.
    """
    # Sort by pattern_id for stability, serialize deterministically
    sorted_patterns = sorted(patterns, key=lambda p: p.get("pattern_id", ""))
    canonical = json.dumps(sorted_patterns, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# Default destructive edit pattern catalog
# ---------------------------------------------------------------------------

DEFAULT_PATTERNS: list[dict[str, Any]] = [
    {
        "pattern_id": "HEUR-01",
        "name": "Indentation Break",
        "description": (
            "Block-level code indentation stripped or corrupted. "
            "A def/class/if/for/while/try/with block header is followed "
            "by an unindented or wrongly-indented body line."
        ),
        "detection_regex": (
            r"(?m)^(def |class |if |for |while |try |with )"
            r"[^:]+:\s*\n[^\s#]"
        ),
        "severity": "critical",
        "prevention_rule": (
            "Always verify strict Python block indentation before replacing. "
            "Include surrounding whitespace in oldString/newString."
        ),
    },
    {
        "pattern_id": "HEUR-02",
        "name": "Merged Lines / Lost Newlines",
        "description": (
            "Multiple separate logical lines merged into one broken "
            "syntax line due to missing newline separator. "
            "Excludes valid single-line def/class stubs "
            "(def name(...): return/pass/raise/yield)."
        ),
        "detection_regex": (
            r"(?m)^(?!\s*(?:def|class)\s+\w+.*:\s*(?:return|pass|raise|yield|print)\b.*$)"
            r"(?!\s*#)"
            r"(?!\s*(?:from|import)\s)"
            r".*(?:def |class |import ).*(?:def |class |return ).*$"
        ),
        "severity": "critical",
        "prevention_rule": (
            "Ensure \\n characters are strictly preserved between "
            "method/statement boundaries."
        ),
    },
    {
        "pattern_id": "HEUR-03",
        "name": "Duplicate Methods",
        "description": (
            "AI inserts the new method but fails to remove the old "
            "method footprint, creating two copies at different indentation. "
            "Constrained to same class scope — does not cross "
            "'class ' or same-indent 'def ' boundaries."
        ),
        "detection_regex": (
            r"(?sm)^( +def (\w+)\(.*\):)"
            r"(?:(?!^(?:class\b| {0,3}(?:def |class |@))).)*"
            r"\n\1"
        ),
        "severity": "warning",
        "prevention_rule": (
            "When replacing a method, explicitly include its entire old "
            "footprint in the oldString — from def to last indented line."
        ),
    },
    {
        "pattern_id": "HEUR-04",
        "name": "Deleted Function Definition",
        "description": (
            "A str_replace accidentally matched and removed a function "
            "signature (e.g., 'def main():' deleted when targeting "
            "surrounding code)."
        ),
        "detection_regex": (
            r"(?m)^if __name__ == .__main__.:\s*\n\s+(?!def )(\w+)\(\)"
        ),
        "severity": "warning",
        "prevention_rule": (
            "When editing code near def/class boundaries, verify the "
            "oldString does not inadvertently include the declaration."
        ),
    },
    {
        "pattern_id": "HEUR-05",
        "name": "Merged Import Statement",
        "description": (
            "An import statement was concatenated into a comment or "
            "adjacent line, breaking module resolution."
        ),
        "detection_regex": r"(?m)^#.*\bfrom \w+ import .*\bimport \w+",
        "severity": "warning",
        "prevention_rule": (
            "Keep import and comment lines in separate oldString/newString "
            "pairs. Never merge them in a single replacement."
        ),
    },
    {
        "pattern_id": "HEUR-06",
        "name": "0-Indent Method in Class",
        "description": (
            "A method definition at column 0 appears inside what should "
            "be an indented class body, breaking the class structure."
        ),
        "detection_regex": (
            r"(?m)^def \w+\(self.*\):\s*\n(?=\s{4,})"
        ),
        "severity": "critical",
        "prevention_rule": (
            "Verify that all methods inside a class are at 4-space indent. "
            "Use PreActionGate indentation checks."
        ),
    },
    {
        "pattern_id": "HEUR-07",
        "name": "Over-Indentation",
        "description": (
            "A statement inside a function/class body is indented to "
            "8+ spaces (double the expected 4-space indent), typically "
            "from iterative batch fix scripts over-correcting indentation. "
            "This was Algorithm #8's signature failure — 12,784 iterations, "
            "54 new syntax errors from over-indentation."
        ),
        "detection_regex": (
            r"(?m)^ {8,}(?:return |if |for |while |try |with |print |"
            r"raise |yield |assert |pass |break |continue |global |"
            r"nonlocal |del |import |from )"
        ),
        "severity": "warning",
        "prevention_rule": (
            "When fixing indentation, test on ONE file and verify with "
            "py_compile before batch-applying. Never iterate fix scripts — "
            "one failed algorithm means HALT, not write another fixer."
        ),
    },
    {
        "pattern_id": "HEUR-08",
        "name": "Import Indentation Anomaly",
        "description": (
            "An import or from-import statement appears at column 0 "
            "inside what should be an indented class body, indicating "
            "that import-line removal from a prior batch script left "
            "surrounding code at the wrong indentation level. "
            "This was Algorithm #3's signature damage — removed imports "
            "without adjusting subsequent line indentation, breaking ~92 files."
        ),
        "detection_regex": (
            r"(?sm)^class \w+.*:\n"
            r"(?:(?!^(?:class |def |@|\s*$)).)*?"
            r"^(?:import |from )\w+"
        ),
        "severity": "warning",
        "prevention_rule": (
            "When removing lines from class bodies, verify ALL subsequent "
            "lines retain correct indentation. Line removal is NEVER "
            "indentation-neutral — check the file with py_compile after."
        ),
    },
    {
        "pattern_id": "HEUR-09",
        "name": "Indentation Jump / Mismatched Siblings",
        "description": (
            "Two adjacent non-blank code lines within the same block "
            "scope differ by more than 4 spaces in indentation, "
            "indicating a batch fixer pulled one line to the wrong "
            "indent level. This was Algorithm #3's and #8's signature "
            "damage — import removal leaving subsequent lines at wrong "
            "indent, or over-correction creating sharp indent jumps."
        ),
        "detection_regex": (
            r"(?m)^(\s*)(?!\s*(?:class |def |@|#|from |import ))\S.*\n"
            r"(\s*)(?!\s*(?:class |def |@|#|from |import ))\S"
        ),
        "severity": "warning",
        "prevention_rule": (
            "When modifying indentation inside a block, verify ALL "
            "sibling lines stay at the same indent level. Never "
            "change one line's indentation without checking its neighbors."
        ),
    },
    {
        "pattern_id": "HEUR-10",
        "name": "Dangling Function Body",
        "description": (
            "A def statement followed by only blank/whitespace lines "
            "before another def or class at the same or lesser "
            "indentation, indicating the function body was accidentally "
            "deleted during a str_replace that matched too broadly. "
            "This was Algorithm #17's failure mode — _fix_healthz_v2.py "
            "replaced main() with AI-generated code, leaving a def with "
            "no body."
        ),
        "detection_regex": (
            r"(?m)^( *)def \w+\(.*\):\s*\n"
            r"(?:\s*\n)*"
            r"^(?=\1(?:class |def |@))"
        ),
        "severity": "critical",
        "prevention_rule": (
            "When editing code near def boundaries, ensure the oldString "
            "does not accidentally include the function body. Always "
            "verify the function still has at least one body line after edit."
        ),
    },
]


# ---------------------------------------------------------------------------
# Module-level compiled regexes — compiled once at import time instead of
# recompiled on every method call or per-file scan (~10 compilations saved).
# ---------------------------------------------------------------------------

_HEUR03_DUPLICATE_RE = re.compile(
    r"(?sm)^( +def (\w+)\(.*\):)"
    r"(?:(?!^(?:class\b| {0,3}(?:def |class |@))).)*"
    r"\n\1"
)

_HEUR04_MAIN_GUARD_RE = re.compile(
    r"(?m)^if __name__ == .__main__.:\s*\n\s+(?!def )(\w+)\(\)"
)

_HEUR07_OVER_INDENT_RE = re.compile(
    r"(?m)^( {8,})(?:return |if |for |while |try |with |print |"
    r"raise |yield |assert |pass |break |continue |global |"
    r"nonlocal |del |import |from )"
)

_HEUR08_IMPORT_ANOMALY_RE = re.compile(
    r"(?sm)^class \w+.*:\n"
    r"(?:(?!^(?:class |def |@|\s*$)).)*?"
    r"^(?:import |from )\w+"
)

_HEUR09_JUMP_RE = re.compile(
    r"(?m)^(\s*)(?!\s*(?:class |def |@|#|from |import ))\S.*\n"
    r"(\s*)(?!\s*(?:class |def |@|#|from |import ))\S"
)

_HEUR09_BLOCK_RE = re.compile(
    r"^\s*(?:class |def |if |for |while |try:|with |elif |else:|except[ :]|finally:)"
)

_HEUR10_DANGLING_RE = re.compile(
    r"(?m)^( *)def (\w+)\(.*\):\s*\n"
    r"((?:\s*\n)*)"
    r"^(?=\1(?:class |def |@))"
)

# Patterns that were previously compiled inline in method bodies —
# hoisted to module-level to eliminate repeated re.compile() calls.

_CLASS_HEADER_RE = re.compile(r"^class \w+")

_DECL_OR_DECORATOR_RE = re.compile(r"^(?:class |def |@)")


# ---------------------------------------------------------------------------
# Heuristic Error Recorder
# ---------------------------------------------------------------------------

class HeuristicErrorRecorder:
    """Records and detects destructive AI text-replacement patterns.

    Usage:
        recorder = HeuristicErrorRecorder()

        # Before edit
        attempt_id = recorder.record_attempt(
            "str_replace", filepath, old_string, new_string, "PROCEED")

        # ... perform edit ...

        # After edit
        outcome = recorder.record_outcome(attempt_id, filepath, success=True)

        # Session summary
        summary = recorder.get_session_errors()
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._session_errors: list[dict[str, Any]] = []
        self._attempts: dict[str, dict[str, Any]] = {}
        self._init_files()
        self._patterns = self._load_patterns()
        self.telemetry = (ComplianceTelemetry()
                          if (_TELEMETRY_AVAILABLE and ComplianceTelemetry is not None)
                          else None)
        self.memory = (DirectMemory()
                       if (_MEMORY_AVAILABLE and DirectMemory is not None)
                       else None)

    def _init_files(self):
        """Ensure audit files and directories exist.

        Writes DEFAULT_PATTERNS to the registry file (with _version stamp)
        if it doesn't exist or if the stored version is stale.
        """
        _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        write_new = True
        if _REGISTRY_FILE.exists():
            try:
                with open(_REGISTRY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("version") == _PATTERN_VERSION:
                    patterns = data.get("patterns", [])
                    if isinstance(patterns, list) and patterns:
                        write_new = False
            except Exception as exc:
                note_failure("heuristic_recorder:L383", exc)
        if write_new:
            payload = {
                "version": _PATTERN_VERSION,
                "patterns": DEFAULT_PATTERNS,
                "fingerprint": _compute_pattern_fingerprint(DEFAULT_PATTERNS),
            }
            with open(_REGISTRY_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

    def _load_patterns(self) -> list[dict[str, Any]]:
        """Load pattern registry, falling back to defaults.

        Checks a _version stamp in the registry file. If the stored
        version doesn't match _PATTERN_VERSION, the cached patterns
        are stale and defaults are used instead (file is regenerated).
        """
        try:
            with open(_REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "version" in data:
                    stored_version = data["version"]
                    patterns = data.get("patterns", [])
                else:
                    stored_version = 0
                    patterns = data  # Old format: plain list
                if stored_version == _PATTERN_VERSION and isinstance(patterns, list) and patterns:
                    return patterns
        except Exception as exc:
            note_failure("heuristic_recorder:L412", exc)
        return DEFAULT_PATTERNS

    # ------------------------------------------------------------------
    # Pre-edit Recording
    # ------------------------------------------------------------------

    def record_attempt(
        self,
        action_type: str,
        filepath: str,
        old_string: str,
        new_string: str,
        gate_decision: str,
    ) -> str:
        """Record an edit attempt BEFORE it is applied.

        Args:
            action_type: "str_replace" or "write_file"
            filepath: Target file path
            old_string: The old string (truncated to first 200 chars for log)
            new_string: The new string (truncated to first 200 chars for log)
            gate_decision: "PROCEED" or "HALT"

        Returns:
            attempt_id: UUID hex string for correlating with record_outcome
        """
        attempt_id = uuid.uuid4().hex
        ts = datetime.now(timezone.utc).isoformat()

        record = {
            "attempt_id": attempt_id,
            "timestamp": ts,
            "filepath": filepath,
            "action_type": action_type,
            "old_string_trunc": old_string[:200],
            "new_string_trunc": new_string[:200],
            "gate_decision": gate_decision,
        }

        # Store in-memory for correlation
        with self._lock:
            self._attempts[attempt_id] = record

        # Write to rotating JSONL log
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            note_failure("heuristic_recorder:L461", exc)

        # Write to DirectMemory
        if self.memory:
            try:
                self.memory.record(
                    "heuristic_edit_attempt", data=record
                )
            except Exception as exc:
                note_failure("heuristic_recorder:L470", exc)

        return attempt_id

    def record_write_file_attempt(
        self, filepath: str, content: str, gate_decision: str
    ) -> str:
        """Record a write_file attempt (no old_string)."""
        return self.record_attempt(
            "write_file", filepath, "", content, gate_decision
        )

    # ------------------------------------------------------------------
    # Post-edit Recording
    # ------------------------------------------------------------------

    def record_outcome(
        self,
        attempt_id: str,
        filepath: str,
        success: bool,
        error_msg: str = "",
    ) -> dict[str, Any]:
        """Record edit outcome AFTER the edit is applied.

        Performs:
        1. py_compile syntax check
        2. Heuristic pattern scan against REGISTRY_FILE patterns
        3. Escalation to ERROR_LOG.md on pattern detection

        Returns:
            Outcome dict with all checks
        """
        ts = datetime.now(timezone.utc).isoformat()

        # Syntax check
        py_ok, py_err = self._check_syntax(filepath)

        # Pattern scan
        detected = self._scan_for_patterns(filepath)

        outcome = {
            "attempt_id": attempt_id,
            "timestamp": ts,
            "filepath": filepath,
            "success": success,
            "error_msg": error_msg,
            "py_compile_ok": py_ok,
            "py_compile_err": py_err,
            "patterns_detected": [p["pattern_id"] for p in detected],
            "pattern_names": [p["name"] for p in detected],
        }

        # Track session errors
        is_error = not py_ok or bool(detected)
        if is_error:
            with self._lock:
                self._session_errors.append(outcome)

            # ESCALATION PROTOCOL
            error_details = py_err or "; ".join(
                f"{p['name']}: {p['description']}" for p in detected
            )
            self._escalate(filepath, detected, error_details)

        # Record to DirectMemory
        if self.memory:
            try:
                severity = "error" if is_error else "info"
                self.memory.record(
                    "heuristic_edit_outcome", data=outcome,
                    severity=severity
                )
            except Exception as exc:
                note_failure("heuristic_recorder:L544", exc)

        return outcome

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _check_syntax(self, filepath: str) -> tuple[bool, str]:
        """Run py_compile to detect syntax/indentation errors."""
        if not filepath.endswith(".py") or not os.path.exists(filepath):
            return True, ""

        try:
            py_compile.compile(filepath, doraise=True)
            return True, ""
        except py_compile.PyCompileError as e:
            note_failure("heuristic_recorder:L561", e)
            return False, str(e)
        except Exception as e:
            note_failure("heuristic_recorder:L563", e)
            return False, f"Unexpected compilation error: {e}"

    def _scan_for_patterns(
        self, filepath: str
    ) -> list[dict[str, Any]]:
        """Scan file against all registered heuristic patterns."""
        if not os.path.exists(filepath) or not filepath.endswith(".py"):
            return []

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as exc:
            note_failure("heuristic_recorder:L576", exc)
            return []

        hits = []
        for pattern in self._patterns:
            regex = pattern.get("detection_regex", "")
            if regex:
                try:
                    if re.search(regex, content):
                        # HEUR-03 post-validation: check for intervening class
                        # boundaries between duplicate method definitions.
                        # Two classes at the same indent level each legitimately
                        # defining __enter__ is not a bug.
                        if pattern.get("pattern_id") == "HEUR-03":
                            if not self._heur03_class_boundary_check(content):
                                continue
                        # HEUR-04 post-validation: verify the called function's
                        # def does not exist anywhere in the file (before or after
                        # the main guard).  Regex can only scan forward, so we
                        # do a two-pass check here.
                        if pattern.get("pattern_id") == "HEUR-04":
                            if not self._heur04_def_missing(content):
                                continue
                        # HEUR-07 post-validation: verify the over-indented
                        # line is an anomaly in an otherwise 4-space block,
                        # not consistent deeper nesting (e.g., class -> def -> if
                        # at 12 spaces is valid).
                        if pattern.get("pattern_id") == "HEUR-07":
                            if not self._heur07_over_indent_check(content):
                                continue
                        # HEUR-08 post-validation: verify the column-0 import
                        # is preceded by indented class body lines (indicating
                        # it was pulled from inside the class), not a blank
                        # line or dedented block (which suggests it's between
                        # class definitions — legitimate).
                        if pattern.get("pattern_id") == "HEUR-08":
                            if not self._heur08_import_indent_anomaly_check(content):
                                continue
                        # HEUR-09 post-validation: verify the indent jump
                        # is within the same block scope (no class/def
                        # boundary between the two lines).
                        if pattern.get("pattern_id") == "HEUR-09":
                            if not self._heur09_indent_jump_check(content):
                                continue
                        # HEUR-10 post-validation: verify there are
                        # genuinely zero body lines after the def header
                        # (not a single-line stub like `def f(): pass`).
                        if pattern.get("pattern_id") == "HEUR-10":
                            if not self._heur10_dangling_body_check(content):
                                continue
                        hits.append(pattern)
                except re.error as exc:
                    note_failure("heuristic_recorder:L627", exc) # Skip broken regex patterns
        return hits

    # ------------------------------------------------------------------
    # Pattern-specific post-validation
    # ------------------------------------------------------------------

    @staticmethod
    def _heur04_def_missing(content: str) -> bool:
        """Post-validate HEUR-04: check that the called function's def
        does NOT exist anywhere in the file (both before and after the
        if __name__ block — regex can only scan forward, so we do a
        full-file search here).

        Returns True if the called function's def is genuinely missing
        (possible deleted function), False if def exists in the file
        (standard main guard — false positive).
        """
        for m in _HEUR04_MAIN_GUARD_RE.finditer(content):
            func_name = m.group(1)
            # Check if def <funcname>( exists anywhere in the file
            def_pattern = r"^\s*def\s+" + re.escape(func_name) + r"\("
            if not re.search(def_pattern, content, re.MULTILINE):
                return True  # def does NOT exist — possible deletion
        return False  # All called functions have their defs present

    @staticmethod
    def _heur03_class_boundary_check(content: str) -> bool:
        """Post-validate HEUR-03: check that duplicate method definitions
        are NOT separated by an intervening class or method boundary.

        The HEUR-03 regex catches same-name defs at the same indentation,
        but can't distinguish:
        - Two different classes each legitimately defining the same dunder
          method (e.g., __enter__ in FreeThreadPool and WorkStealingPool)
        - Inner functions with the same name in different enclosing methods
          (e.g., _filter inside filter_by_severity and filter_by_source)

        Returns True if there IS a genuine same-scope duplicate (real bug),
        False if all duplicates are separated by class or method boundaries
        (false positive — different scopes).
        """
        for m in _HEUR03_DUPLICATE_RE.finditer(content):
            indent = len(m.group(1)) - len(m.group(1).lstrip())
            span_start = m.start()
            span_end = m.end()
            between = content[span_start:span_end]
            # Check for an intervening class keyword at indent <= method indent.
            # Uses re.search (not pre-compiled) because the indent width
            # varies per match — Python's internal regex cache handles this.
            if re.search(r"(?m)^ {0," + str(indent) + r"}class\s+\w+", between):
                continue  # Class boundary — different class, skip this pair
            # Check for an intervening def at strictly lower indentation
            # (e.g., inner function _filter inside two different methods
            # of the same class — the enclosing method def is at lower indent)
            if indent > 0:
                if re.search(r"(?m)^ {0," + str(indent - 1) + r"}def\s+\w+", between):
                    continue  # Method boundary — different scope, skip this pair
            # No class or method boundary found — genuine same-scope duplicate
            return True
        return False  # All duplicates separated by boundaries — false positive

    @staticmethod
    def _heur07_over_indent_check(content: str) -> bool:
        """Post-validate HEUR-07: verify the over-indented line is an
        anomaly surrounded by 4-space lines, not consistent deeper nesting.

        Legitimate triple-nesting (class -> def -> if -> return at 12 spaces)
        has all lines at consistent indentation steps. An over-indentation
        bug from a batch fixer has a single 8+ space line amid 4-space lines.

        Returns True if there IS a genuine over-indentation anomaly,
        False if all 8+ space lines are part of consistent deep nesting.
        """
        lines = content.split("\n")
        for m in _HEUR07_OVER_INDENT_RE.finditer(content):
            line_no = content[:m.start()].count("\n")
            over_indent = len(m.group(1))
            # Check if surrounding lines suggest this is an anomaly:
            # Look at the line immediately before and after.
            prev_indent = 0
            next_indent = 0
            if line_no > 0:
                prev_line = lines[line_no - 1]
                if prev_line.strip():
                    prev_indent = len(prev_line) - len(prev_line.lstrip())
            if line_no + 1 < len(lines):
                next_line = lines[line_no + 1]
                if next_line.strip():
                    next_indent = len(next_line) - len(next_line.lstrip())
            # If either neighbor is at 4-space indent and this line is at
            # 8+ spaces, that's a sharp jump suggesting a fixer double-indented.
            # But if the next line continues at >= 8 spaces, it's consistent
            # deeper nesting (e.g., class → def → if → return) — not a bug.
            if prev_indent == 4 and over_indent >= 8:
                if next_indent == 0 or next_indent <= 4:
                    return True  # Anomaly: 8+ space line sticks out
        return False  # All 8+ space lines appear to be consistent deep nesting

    @staticmethod
    def _heur08_import_indent_anomaly_check(content: str) -> bool:
        """Post-validate HEUR-08: verify the column-0 import appears
        within a class body (preceded by indented lines), not between
        two class definitions (legitimate top-level imports).

        Returns True if the import IS genuinely anomalous (column 0 inside
        what was likely a class body), False if it's a legitimate top-level
        import between classes.
        """
        lines = content.split("\n")
        for m in _HEUR08_IMPORT_ANOMALY_RE.finditer(content):
            # Find the import line number
            import_start = m.end()
            # Walk backwards from the import line to find the last non-blank
            # non-class-header line before the import.
            line_no = content[:import_start].count("\n")
            # Check the line immediately before the import
            if line_no > 0:
                prev_line = lines[line_no - 1]
                if prev_line.strip():
                    prev_indent = len(prev_line) - len(prev_line.lstrip())
                    # If the previous line is indented (4+ spaces), the
                    # column-0 import is likely pulled from inside a class body.
                    if prev_indent >= 4:
                        return True  # Genuine anomaly
            # Also check if the line before the import is the class header
            # itself (entire class body was wiped, leaving only the header).
            if line_no > 0:
                prev_line_stripped = lines[line_no - 1].strip()
                if _CLASS_HEADER_RE.match(prev_line_stripped):
                    return True  # Import on line right after class header
        return False  # Import is likely between classes — false positive

    @staticmethod
    def _heur09_indent_jump_check(content: str) -> bool:
        """Post-validate HEUR-09: verify the two mismatched-indent
        adjacent lines are within the same block scope (no intervening
        class/def boundary between them).

        Also performs sandwich detection: if the anomalous line is
        flanked both ABOVE AND BELOW by lines at the expected indent
        (e.g., one 8-space line amid 4-space lines), this is
        Algorithm #8's exact signature — stronger signal than a single
        adjacent jump.

        Returns True if there IS a genuine anomalous indent jump within
        a single block scope, False if the jump is across a block
        boundary (e.g., end of method → next method at different indent).
        """
        lines = content.split("\n")
        for m in _HEUR09_JUMP_RE.finditer(content):
            indent_a = len(m.group(1))
            indent_b = len(m.group(2))
            diff = abs(indent_a - indent_b)
            if diff <= 4:
                continue  # Normal indent step — not an anomaly
            line_b = content[:m.end()].count("\n")
            line_a = line_b - 1
            # --- Scope check: same enclosing block? ---
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
            # --- Sandwich detection: is one line flanked by normal indent? ---
            # Check if line A is the anomaly: line A-1 and line B are at same indent
            if line_a > 0:
                prev_prev = lines[line_a - 1].strip()
                if prev_prev:
                    prev_prev_indent = len(lines[line_a - 1]) - len(lines[line_a - 1].lstrip())
                    if prev_prev_indent == indent_b and indent_a != indent_b:
                        return True  # Sandwich: A is anomalous between prev and B
            # Check if line B is the anomaly: line A and line B+1 are at same indent
            if line_b + 1 < len(lines):
                next_next = lines[line_b + 1].strip()
                if next_next:
                    next_next_indent = len(lines[line_b + 1]) - len(lines[line_b + 1].lstrip())
                    if indent_a == next_next_indent and indent_b != indent_a:
                        return True  # Sandwich: B is anomalous between A and next
            # Fallback: plain adjacent jump in same scope is still anomalous
            return True
        return False

    @staticmethod
    def _heur10_dangling_body_check(content: str) -> bool:
        """Post-validate HEUR-10: verify the def genuinely has zero
        body lines (not a single-line stub like `def f(): pass` or
        `def f(): return 1` on the same or next line).

        The HEUR-10 regex finds def headers followed by blank lines
        then another def/class at the same indent. But a single-line
        stub `def f(): pass` is legitimate — it's only a problem when
        there's genuinely zero body.

        Returns True if there IS a genuine dangling function body
        (body was accidentally deleted), False if it's a single-line
        stub or normal empty method.
        """
        lines = content.split("\n")
        for m in _HEUR10_DANGLING_RE.finditer(content):
            func_name = m.group(2)
            # Count blank lines between def header and next def/class
            blank_block = m.group(3)
            blank_count = blank_block.count("\n")
            # A single-line stub `def f(): pass` has the body on the
            # def line itself — the regex requires at least a newline
            # after the colon, so single-line stubs are excluded.
            # But `def f():\n    pass` with one body line and then a
            # blank line before next def would have blank_count >= 1.
            # We check: is the def line immediately followed (after
            # zero or more blank lines) by another def/class at same
            # indent, with ZERO indented body lines between?
            def_line_no = content[:m.start()].count("\n")
            # Walk forward from def_line_no+1 through the blank lines
            has_body = False
            for i in range(def_line_no + 1, len(lines)):
                stripped = lines[i].strip()
                if not stripped:
                    continue  # Blank line
                line_indent = len(lines[i]) - len(lines[i].lstrip())
                def_indent = len(m.group(1))
                if line_indent <= def_indent:
                    # Reached a line at ≤ def indent — if this is the
                    # next class/def, we found NO body.
                    if _DECL_OR_DECORATOR_RE.match(stripped):
                        break  # No body found — genuine anomaly
                    else:
                        has_body = True
                        break
                else:
                    # Line is indented more than def — this IS a body line
                    has_body = True
                    break
            if not has_body:
                return True  # Genuine dangling function body
        return False

    # ------------------------------------------------------------------
    # Escalation Protocol
    # ------------------------------------------------------------------

    def _escalate(
        self,
        filepath: str,
        patterns: list[dict[str, Any]],
        error_details: str,
    ):
        """Escalate detected errors: Telemetry + ERROR_LOG.md + DirectMemory."""
        pattern_names = (
            [p["name"] for p in patterns]
            if patterns
            else ["Syntax/Indent Break"]
        )

        # Telemetry
        if self.telemetry:
            try:
                self.telemetry.emit_error(
                    "destructive_heuristic_edit",
                    "heuristic_recorder",
                    {
                        "filepath": filepath,
                        "patterns": pattern_names,
                        "details": error_details,
                    },
                )
            except Exception as exc:
                note_failure("heuristic_recorder:L902", exc)

        # ERROR_LOG.md
        error_num = int(datetime.now().timestamp())
        log_entry = (
            f"\n## Error #{error_num} — Destructive Heuristic Edit Detected\n"
            f"**Timestamp:** {datetime.now(timezone.utc).isoformat()}\n"
            f"**Severity:** 🔴 Critical\n"
            f"**File(s):** `{filepath}`\n\n"
            f"**Cause:** AI heuristic str_replace failed verification "
            f"checks. Detected patterns: {', '.join(pattern_names)}.\n"
            f"**Details:**\n```\n{error_details}\n```\n\n"
            f"**Prevention:** Must follow PRE-ACTION GATE strict matching. "
            f"Known rules:\n"
            + "\n".join(
                f"- {p['prevention_rule']}" for p in patterns
            )
            + "\n---\n"
        )

        try:
            with open(_ERROR_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception as e:
            # Last resort: write to stdout
            print(f"[HeuristicRecorder] Cannot escalate to ERROR_LOG.md: {e}")

    # ------------------------------------------------------------------
    # Session Summary
    # ------------------------------------------------------------------

    def get_session_errors(self) -> dict[str, Any]:
        """Return session error summary grouped by pattern type."""
        grouped: dict[str, list[dict]] = {}
        for err in self._session_errors:
            for pid in err.get("patterns_detected", []):
                grouped.setdefault(pid, []).append(err)

        return {
            "total_errors": len(self._session_errors),
            "grouped_by_pattern": grouped,
            "raw_events": list(self._session_errors),
            "attempts_logged": len(self._attempts),
        }

    def get_session_summary_text(self) -> str:
        """Generate a human-readable session summary for reporting."""
        summary = self.get_session_errors()
        if summary["total_errors"] == 0:
            return (
                f"HEURISTIC RECORDER: Session clean — "
                f"{summary['attempts_logged']} edits, 0 errors."
            )

        lines = [
            f"HEURISTIC RECORDER: {summary['total_errors']} errors "
            f"detected in {summary['attempts_logged']} edits:",
        ]
        for pid, events in summary["grouped_by_pattern"].items():
            name = events[0].get("pattern_names", [pid])[0] if events else pid
            lines.append(f"  - {name}: {len(events)} occurrence(s)")
        return "\n".join(lines)

    @property
    def total_errors(self) -> int:
        """Count of errors detected this session."""
        return len(self._session_errors)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_recorder_instance: (HeuristicErrorRecorder | None) = None
_recorder_lock = threading.Lock()


def get_heuristic_recorder() -> HeuristicErrorRecorder:
    """Get or create the global HeuristicErrorRecorder singleton."""
    global _recorder_instance
    if _recorder_instance is None:
        with _recorder_lock:
            if _recorder_instance is None:
                _recorder_instance = HeuristicErrorRecorder()
    return _recorder_instance


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """Run a quick self-test of the HeuristicErrorRecorder."""
    import os

    print("=" * 60)
    print("  HEURISTIC ERROR RECORDER SELF-TEST")
    print("=" * 60)

    recorder = HeuristicErrorRecorder()
    passed_all = True

    # Test 1: record_attempt creates valid ID
    print("\n[1/5] Record attempt...")
    aid = recorder.record_attempt(
        "str_replace",
        "test.py",
        "old code",
        "new code",
        "PROCEED",
    )
    if len(aid) == 32:  # UUID hex
        print(f"  PASS: attempt_id={aid[:8]}...")
    else:
        print(f"  FAIL: Invalid attempt_id: {aid}")
        passed_all = False

    # Test 2: pattern detection on broken file
    print("[2/5] Pattern detection...")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write("class Foo:\ndef broken(self):\n    pass\n")
        tmp = f.name

    try:
        outcome = recorder.record_outcome(aid, tmp, success=True)
        if outcome["patterns_detected"]:
            print(f"  PASS: Detected patterns: {outcome['patterns_detected']}")
        else:
            print("  FAIL: Should have detected indentation break")
            passed_all = False
    finally:
        os.unlink(tmp)

    # Test 3: session summary
    print("[3/5] Session summary...")
    summary = recorder.get_session_errors()
    if summary["total_errors"] > 0:
        print(f"  PASS: {summary['total_errors']} errors recorded")
    else:
        print("  FAIL: No errors in session summary")
        passed_all = False

    # Test 4: registry file exists
    print("[4/5] Registry file...")
    if _REGISTRY_FILE.exists():
        with open(_REGISTRY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        pattern_count = len(data["patterns"]) if isinstance(data, dict) else len(data)
        print(f"  PASS: {pattern_count} patterns in registry")
    else:
        print("  FAIL: Registry file not created")
        passed_all = False

    # Test 5: pattern-version consistency check
    print("[5/5] Pattern-version fingerprint...")
    current_fp = _compute_pattern_fingerprint(DEFAULT_PATTERNS)
    if _REGISTRY_FILE.exists():
        with open(_REGISTRY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        stored_fp = data.get("fingerprint", "") if isinstance(data, dict) else ""
        stored_ver = data.get("version", 0) if isinstance(data, dict) else 0
        if stored_ver == _PATTERN_VERSION and stored_fp:
            if stored_fp == current_fp:
                print(f"  PASS: v{_PATTERN_VERSION} fingerprint matches DEFAULT_PATTERNS")
            else:
                print(f"  FAIL: DEFAULT_PATTERNS changed but _PATTERN_VERSION ({_PATTERN_VERSION}) wasn't bumped!")
                print(f"         Stored fingerprint: {stored_fp[:16]}...")
                print(f"         Current fingerprint: {current_fp[:16]}...")
                passed_all = False
        elif stored_ver != _PATTERN_VERSION:
            print(f"  PASS: Version mismatch ({stored_ver}→{_PATTERN_VERSION}) — regenerated on init")
        else:
            print("  PASS: No stored fingerprint yet (first run)")
    else:
        print("  PASS: No registry yet — will be created")

    print("\n" + "=" * 60)
    if passed_all:
        print("  ALL HEURISTIC RECORDER TESTS PASSED!")
    else:
        print("  HEURISTIC RECORDER TESTS FAILED!")
    print("=" * 60)
    return passed_all


if __name__ == "__main__":
    import sys

    success = self_test()
    sys.exit(0 if success else 1)
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
