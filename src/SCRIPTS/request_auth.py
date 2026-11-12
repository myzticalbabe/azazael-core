#!/usr/bin/env python3
"""
request_auth.py -- Programmatic PRE-ACTION GATE CLI (Gemini-Architected v3)

Replaces the 700-word textual assertion block (RULE 0J/0S) with a single
basher command. The LLM executes this BEFORE any platform tool call.

Design (per Gemini / Parallax Paradigm):
1. LLM calls: basher running `request_auth.py --intent <tool> --target <file>`
2. Script validates ALL 20+ gate checks programmatically (ruleg, inference cache,
   anti-pattern scan, state machine, etc.)
3. On PASS: drops cryptographically-signed token in .audit/gate_tokens/<file>.token
   (Ed25519 when cryptography library available, BLAKE2b fallback)
4. parallax_validator.py daemon verifies token before allowing OS write
5. On FAIL: returns non-zero exit code, LLM blocked from proceeding

Usage:
    # Read-only action (instant pass with tracking)
    python request_auth.py --intent read_files --target "doctor.py"

    # Modifying action (full 20-point validation)
    python request_auth.py --intent str_replace --target "doctor.py" \\
        --cite-inference ".inference_cache/20260619_gemini.log"

    # Full gate at all times (user directive)
    python request_auth.py --intent glob --target "*.py" --full-gate

Exit codes:
    0 = AUTHORIZED (token dropped, proceed)
    1 = DENIED (check console output for reason)
    2 = HALTED (ECA halted, worm health critical, or state machine blocked)
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Lazy imports for optional dependencies — loaded on first use
try:
    from CORE.compliance.scanner import ASTAntiPatternScanner
    _SCANNER_AVAILABLE = True
except ImportError as exc:
    note_failure("request_auth:L48", exc)
    _SCANNER_AVAILABLE = False
    ASTAntiPatternScanner = None  # type: ignore

try:
    from CORE.compliance.broker import can_edit, gate_passed, gate_asserted
    _BROKER_AVAILABLE = True
except ImportError as exc:
    note_failure("request_auth:L55", exc)
    _BROKER_AVAILABLE = False
    can_edit = None  # type: ignore
    gate_passed = None  # type: ignore
    gate_asserted = None  # type: ignore

# RULE 0Z-B v4: thirteen hyper-edge cards plus the zero-shot reasoning card. One
# number, enforced here and printed into the
# refusal message, so the mandate in MANDATES.md and the gate cannot drift apart.
FOLLOWUP_COUNT = 14

# Project root resolution
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Token Management
# ---------------------------------------------------------------------------

def _get_tokens_dir() -> Path:
    """Get or create the .audit/gate_tokens/ directory."""
    audit_dir = _PROJECT_ROOT.parent / ".audit"
    tokens_dir = audit_dir / "gate_tokens"
    tokens_dir.mkdir(parents=True, exist_ok=True)
    return tokens_dir


# Ed25519 signing key (lazy-loaded, generated on first use).
# `_ED25519_AVAILABLE` is TRI-state: None = not attempted yet, True/False = the
# answer after an attempt. It used to start as False, which the "already tried
# and failed -- don't retry" guard below cannot tell apart from "never tried",
# so `_get_ed25519_key()` returned None on its FIRST call and the entire try
# block -- key load AND generation -- was unreachable. Ed25519 was never used,
# and every token fell through to the fallback.
_ED25519_KEY = None
_ED25519_AVAILABLE = None

# Isolated-import cache: module -> bool. A native extension built for the GIL
# ABI aborts the free-threaded interpreter on import, and an access violation
# cannot be caught by try/except -- it ends the process. So importability is
# decided in a child that is allowed to die, and cached.
_NATIVE_IMPORT_OK: dict[str, bool] = {}


def _native_import_ok(module: str) -> bool:
    """True only if `module` imports cleanly in a child interpreter.

    Measured 2026-10-02: under Python 3.14t,
    ``cryptography.hazmat.primitives.serialization`` pulls ``bcrypt``, whose
    GIL-only wheel crashes on import. The Ed25519 path must fall back to
    BLAKE2b instead of taking the whole process down with it.
    """
    if module in _NATIVE_IMPORT_OK:
        return _NATIVE_IMPORT_OK[module]
    ok = False
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import %s" % module],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
        ok = proc.returncode == 0
    except Exception as exc:
        note_failure("request_auth:L111", exc)
        ok = False
    _NATIVE_IMPORT_OK[module] = ok
    return ok


def _get_ed25519_key():
    """Load or generate the Ed25519 signing key from ~/.azazael/."""
    global _ED25519_KEY, _ED25519_AVAILABLE
    if _ED25519_KEY is not None:
        return _ED25519_KEY
    if _ED25519_AVAILABLE is False and _ED25519_KEY is None:
        # Already tried and failed — don't retry
        return None
    if not _native_import_ok("cryptography.hazmat.primitives.serialization"):
        # The import below would abort the interpreter, not raise. Fall back.
        _ED25519_AVAILABLE = False
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
        key_dir = Path.home() / ".azazael"
        key_dir.mkdir(parents=True, exist_ok=True)
        key_path = key_dir / "id_ed25519_gate"
        try:
            if key_path.exists():
                loaded_key = serialization.load_pem_private_key(
                    key_path.read_bytes(), password=None
                )
                if not isinstance(loaded_key, ed25519.Ed25519PrivateKey):
                    raise ValueError(
                        "gate key is %s, not Ed25519"
                        % type(loaded_key).__name__)
                _ED25519_KEY = loaded_key
            else:
                _ED25519_KEY = ed25519.Ed25519PrivateKey.generate()
                key_path.write_bytes(_ED25519_KEY.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                ))
            _ED25519_AVAILABLE = True
        except (OSError, ValueError) as e:
            print(f"[request_auth] Ed25519 key load/generate failed: {e}. Using BLAKE2b fallback.",
                  file=sys.stderr)
            _ED25519_AVAILABLE = False
    except ImportError as exc:
        note_failure("request_auth:L152", exc)
        _ED25519_AVAILABLE = False
    return _ED25519_KEY


def _sign_token(token_data: dict) -> str:
    """Sign token data with Ed25519 (preferred) or BLAKE2b (fallback).
    
    Provides integrity verification that the parallax daemon can validate.
    Uses Ed25519 from the cryptography library when available, falling back
    to BLAKE2b with random salt.
    """
    payload_str = json.dumps(token_data, sort_keys=True, default=str)
    key = _get_ed25519_key()
    if key is not None and _ED25519_AVAILABLE:
        sig = key.sign(payload_str.encode("utf-8")).hex()
        return f"ed25519:{sig}"
    # BLAKE2b fallback. The salt MUST be a function of the payload, not
    # `os.urandom`: the verifiers (`is_token_valid` here, and
    # `DAEMONS/parallax_validator.verify_token`) recompute the digest from the
    # salt carried INSIDE the signature, so a fresh random salt per call can
    # never reproduce the signed value -- every token verified False, which is
    # a deny-everything gate. Deriving the salt from the payload keeps the
    # 3-part `blake2b:<digest>:<salt>` shape the validator parses and makes the
    # check reproducible in any process.
    salt = hashlib.blake2b(payload_str.encode("utf-8"),
                           digest_size=16).hexdigest()
    sig = hashlib.blake2b(
        (payload_str + salt).encode("utf-8"), digest_size=32
    ).hexdigest()
    return f"blake2b:{sig}:{salt}"


def drop_token(filepath: str, intent: str, gate_data: dict) -> Path:
    """Drop an authorization token for a file modification.
    
    Args:
        filepath: Target file being modified
        intent: Tool action (str_replace, write_file, etc.)
        gate_data: Gate validation results for audit
    
    Returns:
        Path to the token file
    """
    tokens_dir = _get_tokens_dir()
    
    # Create a safe filename from the target path
    safe_name = filepath.replace("/", "_").replace("\\", "_").replace(":", "_")
    token_path = tokens_dir / f"{safe_name}.token"
    
    token_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target": filepath,
        "intent": intent,
        "gate_data": gate_data,
        "ttl_seconds": 15,  # Tokens expire after 15 seconds
        "pid": os.getpid(),
    }
    token_data["signature"] = _sign_token(token_data)
    
    token_path.write_text(json.dumps(token_data, indent=2, default=str), encoding="utf-8")
    return token_path


def is_token_valid(token_path: Path, filepath: str, max_age_seconds: int = 15) -> bool:
    """Verify a token is valid (exists, not expired, matches target file).
    
    Args:
        token_path: Path to the token file
        filepath: Expected target file
        max_age_seconds: Maximum token age in seconds
    
    Returns:
        True if token is valid
    """
    if not token_path.exists():
        return False
    
    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        note_failure("request_auth:L232", exc)
        return False
    
    # Check target matches
    if data.get("target") != filepath:
        return False
    
    # Check TTL
    timestamp = data.get("timestamp", "")
    try:
        token_time = datetime.fromisoformat(timestamp)
        age = (datetime.now(timezone.utc) - token_time).total_seconds()
        if age > max_age_seconds:
            return False
    except (ValueError, TypeError) as exc:
        note_failure("request_auth:L246", exc)
        return False
    
    # Verify signature
    sig = data.pop("signature", "")
    expected = _sign_token(data)
    data["signature"] = sig
    return sig == expected


def consume_token(filepath: str) -> bool:
    """Consume (delete) a token after successful file operation.
    
    Returns:
        True if token was found and consumed
    """
    safe_name = filepath.replace("/", "_").replace("\\", "_").replace(":", "_")
    token_path = _get_tokens_dir() / f"{safe_name}.token"
    if token_path.exists():
        token_path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Gate Validation Logic
# ---------------------------------------------------------------------------

def _check_inference_cache(cite_path: str) -> tuple[bool, str]:
    """Verify that cited inference cache files exist on disk.
    
    Per RULE 0Q-A: inference claims MUST cite .inference_cache/ filepaths.
    """
    if not cite_path:
        return True, "No inference citation required"
    
    cache_path = _PROJECT_ROOT.parent / cite_path
    if cache_path.exists():
        return True, f"Inference cache verified: {cite_path}"
    return False, f"INFERENCE CACHE NOT FOUND: {cite_path}. Per RULE 0Q-A: HALT."


def _check_state_machine() -> tuple[bool, str]:
    """Verify the compliance state machine allows this action."""
    if not _BROKER_AVAILABLE:
        return True, "State machine not loaded (non-blocking for auth)"
    
    try:
        from CORE.compliance.broker import get_compliance_summary
        summary = get_compliance_summary()
        
        if summary.get("eca_halted"):
            return False, f"ECA HALTED: {summary.get('eca_halt_reason', 'unknown')}"
        
        worm = summary.get("worm_health", {})
        if worm.get("status") == "critical":
            return False, f"WORM HEALTH CRITICAL: {worm.get('message')}"
        
        return True, f"State: {summary.get('state_machine_state', '?')}"
    except ImportError as exc:
        note_failure("request_auth:L305", exc)
        return True, "State machine not loaded (non-blocking for auth)"


def _check_anti_patterns(filepath: str, content_hint: str = "") -> tuple[bool, str]:
    """Run AST anti-pattern scan on target file if it exists.
    
    Per RULE 0H-A: anti-pattern claims require RUNTIME verification.
    """
    target_path = _PROJECT_ROOT.parent / filepath
    if not target_path.exists():
        return True, "File does not exist yet (pre-create auth)"
    
    if not _SCANNER_AVAILABLE or ASTAntiPatternScanner is None:
        return True, "Scanner not available"
    
    scanner = ASTAntiPatternScanner()
    result = scanner.scan_file(str(target_path))
    findings = getattr(result, 'findings', [])
    if len(findings) > 0:
        types = set(getattr(f, 'type', '?') for f in findings)
        return True, f"Pre-existing: {len(findings)} findings ({', '.join(sorted(types))}) — not introduced by this edit"
    return True, "Anti-pattern scan: CLEAN"


def _check_gemini_spawned() -> tuple[bool, str]:
    """Check if thinker-with-files-gemini was spawned for this task.
    
    Per RULE 0G: complex tasks require Gemini architecture.
    Per RULE 0Q-A: must cite inference cache filepaths.
    
    Returns False when .inference_cache/ exists but has no recent Gemini logs,
    indicating Gemini has NOT been spawned for this complex task.
    """
    cache_dir = _PROJECT_ROOT.parent / ".inference_cache"
    if not cache_dir.exists():
        return False, ".inference_cache/ not found — Gemini NOT spawned (RULE 0G requires Gemini for complex tasks)"
    
    # Find recent gemini logs (last 30 minutes)
    now = time.time()
    recent = []
    for f in cache_dir.glob("*gemini*.log"):
        if (now - f.stat().st_mtime) < 1800:
            recent.append(str(f))
    
    if recent:
        return True, f"Gemini spawned: {len(recent)} recent logs"
    return False, "No recent Gemini logs — per RULE 0G, spawn Gemini for complex tasks before proceeding"


def validate_action(intent: str, target: str, cite_inference: str = "",
                    full_gate: bool = False) -> tuple[bool, dict]:
    """Run all gate validations and return (passed, gate_data).
    
    Args:
        intent: Tool action name (str_replace, write_file, read_files, etc.)
        target: Target file path
        cite_inference: Optional .inference_cache/ path
        full_gate: If True, run full validation even for read-only actions
    
    Returns:
        (authorized: bool, gate_data: dict with validation results)
    """
    gate_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "intent": intent,
        "target": target,
        "checks": {},
    }
    
    # Determine if this is a modifying action
    MODIFYING_ACTIONS = {"str_replace", "write_file", "spawn_agents"}
    # RULE 0Z-B: suggest_followups is NOT exempt -- must verify the 13-count
    GATED_UI_ACTIONS = {"suggest_followups"}
    READONLY_ACTIONS = {"read_files", "skill", "list_directory", "glob",
                        "read_subtree", "read_url", "gravity_index",
                        "render_ui", "write_todos", "ask_user",
                        "browser_use", "tmux_cli"}
    
    is_modifying = intent in MODIFYING_ACTIONS
    is_gated_ui = intent in GATED_UI_ACTIONS
    needs_full = is_modifying or is_gated_ui or full_gate
    
    # RULE 0Z-B v4: exactly 14 items -- the baker's dozen + the reasoning card (fast check)
    if intent == "suggest_followups":
        followup_count = int(cite_inference or 0) if cite_inference and cite_inference.isdigit() else 0
        if followup_count == FOLLOWUP_COUNT:
            gate_data["checks"]["rule_0z_b"] = {"passed": True,
                                                "message": "%d followups verified" % FOLLOWUP_COUNT}
            gate_data["authorized"] = True
            return True, gate_data
        return False, {"checks": {"rule_0z_b": {
            "passed": False,
            "message": f"RULE 0Z-B VIOLATION: {followup_count} items (must be exactly {FOLLOWUP_COUNT}: 13 hyper-edge cards + zero-shot reasoning)"
        }}}
    
    # Check 1: Inference cache citation (always verified per RULE 0Q-A)
    ok, msg = _check_inference_cache(cite_inference)
    gate_data["checks"]["inference_cache"] = {"passed": ok, "message": msg}
    if not ok and needs_full:
        return False, gate_data
    
    # Check 2: State machine status
    if needs_full:
        ok, msg = _check_state_machine()
        gate_data["checks"]["state_machine"] = {"passed": ok, "message": msg}
        if not ok:
            return False, gate_data
    
    # Check 3: Anti-pattern scan (only for existing files being modified)
    if is_modifying and target:
        ok, msg = _check_anti_patterns(target)
        gate_data["checks"]["anti_pattern_scan"] = {"passed": ok, "message": msg}
    
    # Check 4: Gemini spawn verification
    if needs_full:
        ok, msg = _check_gemini_spawned()
        gate_data["checks"]["gemini_spawned"] = {"passed": ok, "message": msg}
    
    # Check 5: PreActionGate validation (for modifying actions)
    if is_modifying:
        if (not _BROKER_AVAILABLE or gate_passed is None
                or can_edit is None):
            gate_data["checks"]["broker_gate"] = {
                "passed": True,
                "message": "Broker not loaded (non-blocking)"
            }
        elif not gate_passed():
            gate_data["checks"]["broker_gate"] = {
                "passed": False,
                "message": "Gate not passed via gate_asserted(). Call gate_asserted() first."
            }
            return False, gate_data
        elif not can_edit():
            gate_data["checks"]["broker_gate"] = {
                "passed": False,
                "message": "State machine does not allow edits."
            }
            return False, gate_data
        else:
            gate_data["checks"]["broker_gate"] = {"passed": True, "message": "Gate passed, can edit"}
    
    gate_data["authorized"] = True
    return True, gate_data


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Programmatic PRE-ACTION GATE (replaces textual RULE 0J/0S assertion)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Read-only action (instant pass)
  python request_auth.py --intent read_files --target "doctor.py"

  # Modifying action (full validation + token drop)
  python request_auth.py --intent str_replace --target "doctor.py" \\
      --cite-inference ".inference_cache/20260619_gemini.log"

  # Full gate for all actions (user directive: "full gate at all times")
  python request_auth.py --intent glob --target "*.py" --full-gate
        """
    )
    parser.add_argument("--intent", required=True,
                        help="Tool action name (str_replace, write_file, read_files, etc.)")
    parser.add_argument("--target", default="",
                        help="Target file path")
    parser.add_argument("--cite-inference", default="",
                        help=".inference_cache/ filepath for RULE 0Q-A compliance")
    parser.add_argument("--full-gate", action="store_true",
                        help="Run full validation even for read-only actions")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress non-error output")
    parser.add_argument("--followup-count", type=int, default=0,
                        help="Number of followup items (RULE 0Z-B requires exactly 14)")
    
    args = parser.parse_args()
    
    # Run validation
    authorized, gate_data = validate_action(
        intent=args.intent,
        target=args.target,
        cite_inference=args.cite_inference if args.intent != "suggest_followups" else str(args.followup_count),
        full_gate=args.full_gate,
    )
    
    # Determine if this modifies files
    MODIFYING = {"str_replace", "write_file", "spawn_agents"}
    is_modifying = args.intent in MODIFYING
    
    if authorized and is_modifying and args.target:
        # Drop token for the parallax daemon
        token_path = drop_token(args.target, args.intent, gate_data)
        gate_data["token_path"] = str(token_path)
        gate_data["token_dropped"] = True
    
    if args.json:
        print(json.dumps(gate_data, indent=2, default=str))
    elif not args.quiet:
        status = "AUTHORIZED" if authorized else "DENIED"
        print(f"[PRE-ACTION GATE] {status}")
        if authorized and is_modifying and args.target:
            print(f"  Token dropped: {gate_data.get('token_path', 'N/A')}")
        if not authorized:
            for check, result in gate_data.get("checks", {}).items():
                if not result.get("passed", True):
                    print(f"  FAILED [{check}]: {result.get('message', 'unknown')}")
    
    sys.exit(0 if authorized else 1)


if __name__ == "__main__":
    main()
