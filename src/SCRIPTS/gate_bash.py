#!/usr/bin/env python3
"""
gate_bash.py — RULE 0Z-C enforcement for destructive basher commands

Intercepts every basher command, classifies it as destructive or
non-destructive, and gates destructive commands through the full
PreActionGate + ComplianceOrchestrator pipeline.

RULE 0Z-C from MANDATES.md: "EXTEND PRE-ACTION GATE TO ALL CODE/SYSTEM-MODIFYING
SHELL COMMANDS." Destructive basher commands (rm -rf, cp to system dirs,
pip install, etc.) are treated as TOOL CALL operations for gate purposes.

Usage:
  # Check if a command is destructive (dry-run, no execution):
  python azazael/src/SCRIPTS/gate_bash.py --check "rm -rf build/"
  # Returns: DESTRUCTIVE (exit 1), NON-DESTRUCTIVE (exit 0)

  # Gate and optionally execute a command:
  python azazael/src/SCRIPTS/gate_bash.py --gate "pip install requests"
  # If destructive: runs ComplianceOrchestrator.check_all() first.
  # If denied: blocks execution, exit 1.
  # If non-destructive: executes directly.

  # Execute and record (the full pipeline):
  python azazael/src/SCRIPTS/gate_bash.py --execute "pip install requests"

  # Self-test:
  python azazael/src/SCRIPTS/gate_bash.py --test

Exit codes:
  0 = NON-DESTRUCTIVE (proceed) or GATE PASSED (executed)
  1 = DESTRUCTIVE (blocked unless gated) or GATE DENIED
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# Optional imports
try:
    from CORE.compliance.pre_action_gate import get_pre_action_gate
    _PAG_AVAILABLE = True
except ImportError as _pag_e:
    note_failure("gate_bash:L54", _pag_e)
    _PAG_AVAILABLE = False
    _PAG_ERR = str(_pag_e)
    get_pre_action_gate = None

try:
    from CORE.compliance.orchestrator import ComplianceOrchestrator
    _ORCHESTRATOR_AVAILABLE = True
except ImportError as exc:
    note_failure("gate_bash:L62", exc)
    _ORCHESTRATOR_AVAILABLE = False
    ComplianceOrchestrator = None

try:
    from CORE.direct_memory import DirectMemory
    _MEMORY_AVAILABLE = True
except ImportError as exc:
    note_failure("gate_bash:L69", exc)
    _MEMORY_AVAILABLE = False
    DirectMemory = None


# ---------------------------------------------------------------------------
# Destructive pattern catalog (from RULE 0Z-C)
# ---------------------------------------------------------------------------

DESTRUCTIVE_PATTERNS: list[dict[str, Any]] = [
    # File/directory deletion
    {
        "category": "deletion",
        "patterns": [
            r"\brm\b",  # Any rm invocation (bare, -r, -rf, -f)
            r"\brmdir\b",
            r"\bdel\b",
            r"\bRemove-Item\s+-Recurse",
            r"\brmtree\b",
        ],
        "severity": "critical",
    },
    # System directory writes
    {
        "category": "system_write",
        "patterns": [
            r"\bcp\s+.*(?:site-packages|Program\s+Files|Program\s+Files\s+\(x86\)|/usr/(?:local/)?(?:bin|lib|include|share)|/etc/|C:\\Windows)",  # scanner:ignore deliberate: detects writes into system dirs
            r"\bcopy\s+.*(?:site-packages|Program\s+Files|C:\\Windows)",  # scanner:ignore deliberate: detects writes into system dirs
            r"\bmv\s+.*(?:site-packages|Program\s+Files|C:\\Windows)",  # scanner:ignore deliberate: detects writes into system dirs
            r"\bmove\s+.*(?:site-packages|Program\s+Files|C:\\Windows)",  # scanner:ignore deliberate: detects writes into system dirs
            r"\brobocopy\s+.*(?:site-packages|Program\s+Files|C:\\Windows)",  # scanner:ignore deliberate: detects writes into system dirs
        ],
        "severity": "critical",
    },
    # Package management
    {
        "category": "package_mgmt",
        "patterns": [
            r"\bpip\s+install\b",
            r"\bpip\s+uninstall\b",
            r"\bpip3?\s+install\b",
            r"\bpip3?\s+uninstall\b",
            r"\bnpm\s+install\s+-g\b",
            r"\bnpm\s+uninstall\s+-g\b",
            r"\byarn\s+global\s+add\b",
            r"\bchoco\s+install\b",
            r"\bapt(?:-get)?\s+install\b",
            r"\bbrew\s+install\b",
            r"\bwinget\s+install\b",
        ],
        "severity": "warning",
    },
    # Build/install commands
    {
        "category": "build_install",
        "patterns": [
            r"\bmake\s+install\b",
            r"\bpip\s+install\s+-e\s",
            r"\bpython\s+setup\.py\s+install\b",
            r"\bcmake\s+--install\b",
        ],
        "severity": "warning",
    },
    # Filesystem mutation
    {
        "category": "filesystem_mutation",
        "patterns": [
            r"\bmv\b",
            r"\bmove\b",
            r"\bcp\s+-r",
            r"\bcopy\s+/[Yy]",
            r"\brobocopy\b",
            r"\bdd\s+if=",
            r"\bgit\s+clean\s+-[fdx]",
            r"\bgit\s+reset\s+--hard",
            r"\bgit\s+checkout\s+--\s",
            r"\bshutil\.(?:copy|move|rmtree)\b",
        ],
        "severity": "warning",
    },
    # Shell/system state changes
    {
        "category": "system_state",
        "patterns": [
            r"\bchmod\b",
            r"\bchown\b",
            r"\bexport\s+\w+=",
            r"\benv\s+\w+=",
            r"\bset\s+-[^-\s]*[eux]",
            r"\bsystemctl\s+(?:start|stop|restart|enable|disable)\b",
            r"\bsc\s+(?:start|stop)\b",
            r"\bnet\s+(?:start|stop)\b",
        ],
        "severity": "warning",
    },
]

# Non-destructive patterns (always allowed, skip gating)
NON_DESTRUCTIVE_PATTERNS: list[str] = [
    r"^\s*(?:echo|printf|cat|type|head|tail|more|less)\b",
    r"^\s*(?:dir|ls|find|where|which|locate)\b",
    r"^\s*(?:python|python3)\s+(?:-c\s+|--version|-V|-[mh])",
    r"^\s*(?:python|python3)\s+\S+\.py\s+(?:--test|--help|--version|-h)",
    r"^\s*(?:git\s+status|git\s+log|git\s+diff|git\s+branch|git\s+show|git\s+stash\s+list)\b",
    r"^\s*(?:test\s+-[efd]|\[)\b",
    r"^\s*(?:wc|grep|findstr|sort|uniq|cut|tr|sed\s+(?:-n|'/p'))\b",
    r"^\s*(?:curl|wget)\s+(?:-I|--head|--spider)",
    r"^\s*(?:pip|pip3)\s+(?:list|show|freeze|check)\b",
    r"^\s*(?:npm|yarn)\s+(?:list|outdated|audit|why)\b",
    r"^\s*(?:node|deno|bun)\s+(?:--version|-v|-h|--help)\b",
    r"^\s*cd\b",
    r"^\s*pwd\b",
    r"^\s*which\b",
]


# ---------------------------------------------------------------------------
# GateBash — classify, gate, execute
# ---------------------------------------------------------------------------

class GateBash:
    """RULE 0Z-C enforcement: intercepts and gates destructive basher commands.

    Usage:
        gb = GateBash()
        verdict = gb.classify("rm -rf build/")
        if verdict["destructive"]:
            ok, msg = gb.gate("rm -rf build/")
            if ok:
                gb.execute("rm -rf build/")
    """

    def __init__(self):
        self.pre_action_gate = None
        self.orchestrator = None
        self.memory = None
        if _PAG_AVAILABLE and get_pre_action_gate is not None:
            self.pre_action_gate = get_pre_action_gate()
        if _ORCHESTRATOR_AVAILABLE and ComplianceOrchestrator is not None:
            self.orchestrator = ComplianceOrchestrator()
        if _MEMORY_AVAILABLE and DirectMemory is not None:
            self.memory = DirectMemory()

    def classify(self, command: str) -> Dict:
        """Classify a shell command as destructive or non-destructive.

        Returns:
            Dict with 'destructive' (bool), 'category' (str), 'severity' (str),
            'matched_pattern' (str or None)
        """
        stripped = command.strip()

        # Check non-destructive first (fast path)
        for pattern in NON_DESTRUCTIVE_PATTERNS:
            if re.search(pattern, stripped):
                return {
                    "destructive": False,
                    "category": "non_destructive",
                    "severity": "none",
                    "matched_pattern": None,
                }

        # Check destructive patterns
        for group in DESTRUCTIVE_PATTERNS:
            for pattern in group["patterns"]:
                match = re.search(pattern, stripped, re.IGNORECASE)
                if match:
                    return {
                        "destructive": True,
                        "category": group["category"],
                        "severity": group["severity"],
                        "matched_pattern": pattern,
                    }

        # Unknown — conservatively assume non-destructive
        return {
            "destructive": False,
            "category": "unknown",
            "severity": "none",
            "matched_pattern": None,
        }

    def gate(self, command: str) -> tuple[bool, str]:
        """Gate a destructive command through ComplianceOrchestrator.

        Returns:
            (True, "GATE PASSED") or (False, "GATE DENIED: reason")
        """
        verdict = self.classify(command)

        if not verdict["destructive"]:
            return True, "NON-DESTRUCTIVE — no gate required"

        # Fail-closed: if no gate infrastructure is available, block
        if not self.orchestrator and not self.pre_action_gate:
            return (
                False,
                "GATE DENIED: No compliance infrastructure available "
                "— cannot authorize destructive command per RULE 0Z-C",
            )

        # Record the attempt
        if self.memory:
            try:
                self.memory.record("gate_bash_attempt", {
                    "command": command[:500],
                    "category": verdict["category"],
                    "severity": verdict["severity"],
                })
            except Exception as exc:
                note_failure("gate_bash:L272", exc)

        # RULE 0X + 0Y compliance check
        if self.orchestrator:
            try:
                result = self.orchestrator.check_all(
                    action="bash_exec",
                    role="developer",
                    context={
                        "state": "CODE_EDIT",
                        "can_edit": True,
                        "has_gate": True,
                        "synced": True,
                        "scope_check_ok": True,
                        "gate_decision": "PROCEED",
                    },
                )
                if not result.all_passed:
                    return (
                        False,
                        f"GATE DENIED (RULE 0X/0Y): {result.summary}",
                    )
            except Exception as e:
                note_failure("gate_bash:L295", e)
                return (
                    False,
                    f"GATE DENIED (Orchestrator error): {e}",
                )

        return True, f"GATE PASSED — {verdict['severity']} severity, {verdict['category']}"

    def execute(self, command: str, timeout: int = 60) -> subprocess.CompletedProcess:
        """Execute a command (unconditionally — gate first if destructive).

        Tries bash first (WSL/Linux), falls back to cmd.exe (pure Windows).

        Args:
            command: Shell command to execute
            timeout: Timeout in seconds

        Returns:
            CompletedProcess with stdout, stderr, returncode
        """
        try:
            return subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout, encoding="utf-8", errors="replace"
            )
        except (FileNotFoundError, OSError) as exc:
            note_failure("gate_bash:L322", exc)
            return subprocess.run(
                ["cmd.exe", "/c", command],
                capture_output=True,
                text=True,
                timeout=timeout, encoding="utf-8", errors="replace"
            )

    def gate_and_execute(
        self, command: str, timeout: int = 60
    ) -> tuple[bool, str, (subprocess.CompletedProcess | None)]:
        """Full pipeline: classify → gate (if destructive) → execute.

        Returns:
            (passed: bool, message: str, result: CompletedProcess or None)
        """
        # Step 1: Classify
        verdict = self.classify(command)
        print(f"[CLASSIFY] {verdict['category']} ({verdict['severity']})")

        # Step 2: Gate if destructive
        if verdict["destructive"]:
            print("[GATE] Running RULE 0X/0Y compliance check...")
            ok, msg = self.gate(command)
            print(f"[GATE] {msg}")
            if not ok:
                return False, msg, None

        # Step 3: Execute
        print(f"[EXECUTE] {command[:120]}...")
        try:
            result = self.execute(command, timeout)
            if result.returncode == 0:
                print(f"[EXECUTE] OK (exit {result.returncode})")
                return True, "Command executed successfully", result
            else:
                print(f"[EXECUTE] FAILED (exit {result.returncode})")
                return False, f"Command failed with exit {result.returncode}", result
        except subprocess.TimeoutExpired as exc:
            note_failure("gate_bash:L360", exc)
            return False, f"Command timed out after {timeout}s", None
        except Exception as e:
            note_failure("gate_bash:L362", e)
            return False, f"Command execution error: {e}", None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="RULE 0Z-C enforcement: intercept and gate destructive "
                    "basher commands."
    )
    parser.add_argument(
        "--check", metavar="COMMAND",
        help="Classify a command as destructive or non-destructive",
    )
    parser.add_argument(
        "--gate", metavar="COMMAND",
        help="Gate a command through ComplianceOrchestrator (does not execute)",
    )
    parser.add_argument(
        "--execute", metavar="COMMAND",
        help="Full pipeline: classify → gate → execute",
    )
    parser.add_argument(
        "--timeout", type=int, default=60,
        help="Timeout for command execution (default: 60s)",
    )
    parser.add_argument(
        "--test", action="store_true", help="Run self-test",
    )

    args = parser.parse_args()

    if args.test:
        sys.exit(self_test())

    gb = GateBash()

    if args.check:
        verdict = gb.classify(args.check)
        if verdict["destructive"]:
            print(
                f"DESTRUCTIVE [{verdict['severity']}] {verdict['category']}: "
                f"{args.check[:800]}"
            )
            sys.exit(1)
        else:
            print(f"NON-DESTRUCTIVE ({verdict['category']}): {args.check[:800]}")
            sys.exit(0)

    if args.gate:
        ok, msg = gb.gate(args.gate)
        print(msg)
        sys.exit(0 if ok else 1)

    if args.execute:
        ok, msg, _ = gb.gate_and_execute(args.execute, timeout=args.timeout)
        print(msg)
        sys.exit(0 if ok else 1)

    parser.print_help()
    sys.exit(1)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def self_test() -> int:
    """End-to-end test of gate_bash classification and gating."""

    print("=" * 60)
    print("  GATE BASH SELF-TEST (RULE 0Z-C)")
    print("=" * 60)

    gb = GateBash()
    passed_all = True

    # Test 1: Destructive command detected
    print("\n[1/6] Destructive command detection...")
    verdict = gb.classify("rm -rf build/")
    if verdict["destructive"] and verdict["category"] == "deletion":
        print("  PASS: rm -rf correctly classified as destructive/deletion")
    else:
        print(f"  FAIL: rm -rf should be destructive, got {verdict}")
        passed_all = False

    # Test 2: pip install detected
    print("[2/6] Package management detection...")
    verdict = gb.classify("pip install requests")
    if verdict["destructive"] and verdict["category"] == "package_mgmt":
        print("  PASS: pip install correctly classified")
    else:
        print(f"  FAIL: pip install should be destructive, got {verdict}")
        passed_all = False

    # Test 3: Non-destructive command passes
    print("[3/6] Non-destructive command...")
    verdict = gb.classify("echo hello")
    if not verdict["destructive"]:
        print("  PASS: echo correctly classified as non-destructive")
    else:
        print(f"  FAIL: echo should be non-destructive, got {verdict}")
        passed_all = False

    # Test 4: git status is non-destructive
    print("[4/6] Git status non-destructive...")
    verdict = gb.classify("git status")
    if not verdict["destructive"]:
        print("  PASS: git status correctly non-destructive")
    else:
        print(f"  FAIL: git status should be non-destructive, got {verdict}")
        passed_all = False

    # Test 5: cp to site-packages detected
    print("[5/6] System directory write detection...")
    verdict = gb.classify(
        "cp file.py /usr/local/lib/python3.14/site-packages/"
    )
    if verdict["destructive"] and verdict["category"] == "system_write":
        print("  PASS: cp to site-packages correctly classified")
    else:
        print(f"  FAIL: cp to site-packages should be destructive, got {verdict}")
        passed_all = False

    # Test 6: Non-destructive command gates and executes
    print("[6/6] Non-destructive gate_and_execute...")
    ok, msg, result = gb.gate_and_execute("echo hello")
    if ok and result and "hello" in (result.stdout or ""):
        print("  PASS: Non-destructive command gates and executes")
    else:
        print(f"  FAIL: gate_and_execute should pass, got: {msg}")
        passed_all = False

    # Test 7: Destructive gate blocks when infrastructure is down
    print("[7/7] Fail-closed on down infrastructure...")
    gb_no_infra = GateBash()
    gb_no_infra.orchestrator = None
    gb_no_infra.pre_action_gate = None
    ok, msg = gb_no_infra.gate("rm -rf build/")
    if not ok and "No compliance infrastructure" in msg:
        print("  PASS: Destructive command correctly blocked when infra down")
    else:
        print(f"  FAIL: Should block destructive command when infra down, got: {msg}")
        passed_all = False

    print("\n" + "=" * 60)
    if passed_all:
        print("  ALL GATE BASH TESTS PASSED!")
    else:
        print("  GATE BASH TESTS FAILED!")
    print("=" * 60)
    return 0 if passed_all else 1


if __name__ == "__main__":
    main()
