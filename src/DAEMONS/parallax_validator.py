#!/usr/bin/env python3
"""
parallax_validator.py -- Parallax Watcher Daemon (Gemini-Architected v1)

Independent Executive Validator that the LLM CANNOT control. Implements the
Parallax Paradigm: Cognitive-Executive separation where the reasoning layer
is architecturally incapable of executing unauthorized actions.

Design (per Gemini / Parallax Paradigm):
1. Uses watchdog to monitor ALL file modifications in the workspace
2. When a file is modified, checks .audit/gate_tokens/ for valid token
3. Token must be: Ed25519-signed (or BLAKE2b fallback), <15 seconds old, matching exact file target
4. If token missing/invalid: IMMEDIATELY auto-reverts via git checkout
5. Logs CRITICAL violation to ERROR_LOG.md and DirectMemory
6. Debounce protection: reverted files are not re-scanned for 2 seconds

This daemon makes the PRE-ACTION GATE ARCHITECTURALLY UNBYPASSABLE.
The LLM cannot disable, pause, or override this daemon. Even if the
LLM calls str_replace directly without request_auth.py, the change
is instantly undone.

Usage:
    # Start watching (foreground)
    python azazael/src/DAEMONS/parallax_validator.py --watch

    # Start as daemon (background)
    python azazael/src/DAEMONS/parallax_validator.py --daemon

    # Check daemon status
    python azazael/src/DAEMONS/parallax_validator.py --status

    # Dry run (log violations but don't revert)
    python azazael/src/DAEMONS/parallax_validator.py --watch --dry-run
    
    # Run once (check for existing violations, then exit)
    python azazael/src/DAEMONS/parallax_validator.py --audit-once
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

# Project root resolution
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WORKSPACE = _PROJECT_ROOT.parent  # C:/Users/clint/

# Watch configuration
WATCH_PATTERNS = ["*.py", "*.md", "*.json", "*.toml", "*.yaml", "*.yml",
                  "*.ts", "*.js", "*.html", "*.css", "*.sh", "*.ps1"]
IGNORE_DIRS = {".git", "__pycache__", ".audit", "node_modules", ".venv",
               ".venv-3.14t", "venv", ".agents", ".inference_cache", "npExtractDir"}
IGNORE_PREFIXES = {".pre_gate_backup", ".gate_backup_"}
# Any dir starting with .venv is a virtualenv (e.g. .venv-3.14t) — pip
# installs there fire thousands of modify events; never watch them.
IGNORE_DIR_PREFIXES = {".venv"}

# ---------------------------------------------------------------------------
# Token Verification
# ---------------------------------------------------------------------------

# Debounce registry: tracks recently reverted files to prevent infinite loops
_revert_cooldown: dict[str, float] = {}
_REVERT_COOLDOWN_SECONDS = 2.0

# Violation RECORD throttle (separate from revert debounce above — revert
# always runs; this only limits DB/console writes per record_violation).
_violation_record_cooldown: dict[str, float] = {}
_VIOLATION_RECORD_COOLDOWN_SECONDS = 3600.0  # one record per file per hour
_violation_last_global: float = 0.0
_VIOLATION_MIN_GLOBAL_INTERVAL = 5.0  # at most one record per 5s globally
_violation_suppressed: int = 0
_shared_dm = None


def _get_shared_dm():
    """Single DirectMemory for the daemon lifetime (avoids one
    session_start row per violation)."""
    global _shared_dm
    if _shared_dm is None:
        sys.path.insert(0, str(_PROJECT_ROOT))
        from CORE.direct_memory import DirectMemory
        _shared_dm = DirectMemory()
    return _shared_dm


def _get_tokens_dir() -> Path:
    """Get the .audit/gate_tokens/ directory."""
    return _WORKSPACE / ".audit" / "gate_tokens"


def _get_token_path(filepath: str) -> Path:
    """Get the expected token path for a file."""
    safe_name = filepath.replace("/", "_").replace("\\", "_").replace(":", "_")
    return _get_tokens_dir() / f"{safe_name}.token"


def verify_token(filepath: str, max_age_seconds: int = 15) -> tuple[bool, str]:
    """Verify a valid authorization token exists for this file.
    
    Args:
        filepath: The file that was modified
        max_age_seconds: Maximum token age
    
    Returns:
        (valid: bool, reason: str)
    """
    token_path = _get_token_path(filepath)
    
    if not token_path.exists():
        return False, f"No token found at {token_path}"
    
    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        note_failure("parallax_validator:L120", e)
        return False, f"Token unreadable: {e}"
    
    # Verify target matches
    if data.get("target") != filepath:
        return False, f"Token target mismatch: expected {filepath}, got {data.get('target')}"
    
    # Check TTL
    timestamp = data.get("timestamp", "")
    try:
        token_time = datetime.fromisoformat(timestamp)
        age = (datetime.now(timezone.utc) - token_time).total_seconds()
        if age > max_age_seconds:
            return False, f"Token expired ({age:.1f}s > {max_age_seconds}s max)"
    except (ValueError, TypeError) as exc:
        note_failure("parallax_validator:L134", exc)
        return False, "Token timestamp invalid"
    
    # Verify signature (Ed25519 preferred, BLAKE2b fallback)
    sig = data.pop("signature", "")
    payload = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    valid_sig = False
    
    if sig.startswith("ed25519:"):
        # Ed25519 verification
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric import ed25519
            from cryptography.hazmat.primitives import serialization
            key_path = Path.home() / ".azazael" / "id_ed25519_gate"
            if key_path.exists():
                private_key = serialization.load_pem_private_key(
                    key_path.read_bytes(), password=None
                )
                if not isinstance(private_key, ed25519.Ed25519PrivateKey):
                    return False, (
                        "gate key is %s, not Ed25519"
                        % type(private_key).__name__)
                public_key = private_key.public_key()
                sig_bytes = bytes.fromhex(sig.split(":", 1)[1])
                # InvalidSignature is NOT a ValueError (it derives straight
                # from Exception), so the outer tuple below never caught it: a
                # tampered Ed25519 token made this function RAISE instead of
                # returning `(False, "signature invalid")`. The nested try keeps
                # the name bound before use, so the outer tuple cannot refer to
                # an unimported symbol when `cryptography` is absent.
                try:
                    public_key.verify(sig_bytes, payload)
                    valid_sig = True
                except InvalidSignature as exc:
                    note_failure("parallax_validator:L164", exc)
                    valid_sig = False
        except (ImportError, OSError, ValueError) as e:
            note_failure("parallax_validator:L166", e)
            valid_sig = False
    elif sig.startswith("blake2b:"):
        # BLAKE2b fallback verification
        payload_str = payload.decode("utf-8")
        salt = sig.split(":")[2] if ":" in sig and sig.count(":") >= 2 else ""
        expected = hashlib.blake2b(
            (payload_str + salt).encode("utf-8"), digest_size=32
        ).hexdigest()
        expected_full = f"blake2b:{expected}:{salt}"
        valid_sig = (sig == expected_full)
    
    data["signature"] = sig
    
    if not valid_sig:
        return False, "Token signature invalid (tampered or forged)"
    
    # Consume the token (one-time use)
    token_path.unlink()
    return True, "Token valid and consumed"


# ---------------------------------------------------------------------------
# Auto-Revert
# ---------------------------------------------------------------------------

def revert_file(filepath: str) -> tuple[bool, str]:
    """Auto-revert a file to its last git-committed state.
    
    Also registers the file in the debounce cooldown to prevent
    the revert itself from triggering another violation check.
    
    Args:
        filepath: Path to the file to revert
    
    Returns:
        (success: bool, message: str)
    """
    global _revert_cooldown

    # Prune stale entries so the registry stays bounded (same pattern as
    # the record throttle — without this every touched file leaks one entry).
    # Keeps files reverted in the last 2h; the 2s debounce only needs seconds.
    _revert_cooldown = {k: t for k, t in _revert_cooldown.items()
                        if time.time() - t < _REVERT_COOLDOWN_SECONDS * 3600}

    # Register debounce BEFORE reverting to prevent re-trigger
    _revert_cooldown[filepath] = time.time()
    
    try:
        file_path = _WORKSPACE / filepath
        if not file_path.exists():
            return True, "File no longer exists (already reverted?)"
        
        # Try git checkout first
        result = subprocess.run(
            ["git", "checkout", "--", filepath],
            cwd=str(_WORKSPACE),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        if result.returncode == 0:
            return True, f"Reverted via git checkout: {filepath}"
        
        # Git failed — try backup file
        backup_path = Path(str(file_path) + ".pre_gate_backup")
        if backup_path.exists():
            content = backup_path.read_text(encoding="utf-8")
            file_path.write_text(content, encoding="utf-8")
            return True, f"Reverted from backup: {filepath}"
        
        return False, f"Revert failed: git={result.stderr.strip()}, no backup"
    
    except subprocess.TimeoutExpired as exc:
        note_failure("parallax_validator:L240", exc)
        return False, "Git checkout timed out"
    except Exception as e:
        note_failure("parallax_validator:L242", e)
        return False, f"Revert error: {e}"


# ---------------------------------------------------------------------------
# Violation Recording
# ---------------------------------------------------------------------------

def record_violation(filepath: str, reason: str) -> None:
    """Record a CRITICAL violation when unauthorized changes are detected.

    Records to:
    1. Console output (immediate visibility)
    2. ERROR_LOG.md (canonical error log)
    3. DirectMemory (session audit trail)

    Throttle: a file storm (e.g. pip install into an unwatched dir) must
    not write thousands of DB rows — the 2026-07-02 storm wrote 26k
    session_start+milestone pairs because every violation constructed a
    fresh DirectMemory (one session_start each) plus a milestone row.
    Mitigations: one shared DirectMemory per daemon lifetime, at most one
    recorded violation per file per hour, at most one global record per
    5 seconds (suppressed ones counted). Auto-revert still runs every
    time — only the DB/console recording is throttled.
    """
    global _violation_last_global, _violation_suppressed

    now = time.time()
    # Prune stale per-file cooldown entries (bounded memory)
    for k in [k for k, t in _violation_record_cooldown.items()
              if now - t >= _VIOLATION_RECORD_COOLDOWN_SECONDS]:
        del _violation_record_cooldown[k]

    last_file = _violation_record_cooldown.get(filepath, 0)
    if (now - last_file < _VIOLATION_RECORD_COOLDOWN_SECONDS
            or now - _violation_last_global < _VIOLATION_MIN_GLOBAL_INTERVAL):
        _violation_suppressed += 1
        return
    _violation_record_cooldown[filepath] = now
    _violation_last_global = now

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Console log
    print(f"\n{'='*60}")
    print(f"[PARALLAX VALIDATOR] CRITICAL VIOLATION DETECTED")
    print(f"  Timestamp: {timestamp}")
    print(f"  File: {filepath}")
    print(f"  Reason: {reason}")
    print(f"  Action: AUTO-REVERT initiated")
    if _violation_suppressed:
        print(f"  Suppressed similar: {_violation_suppressed} (throttled)")
        _violation_suppressed = 0
    print(f"{'='*60}\n", flush=True)

    # Try recording to DirectMemory (shared instance — one session_start
    # per daemon lifetime, not one per violation)
    try:
        dm = _get_shared_dm()
        dm.milestone("parallax_violation", {
            "file": filepath,
            "reason": reason,
            "timestamp": timestamp,
            "action": "auto-revert",
        })
    except Exception as e:
        print(f"[PARALLAX] DirectMemory recording failed: {e}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# File Handler (Watchdog Callback)
# ---------------------------------------------------------------------------

class ParallaxHandler:
    """Watchdog event handler that validates every file modification."""
    
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.violations = 0
        self.validated = 0
    
    def _should_watch(self, path: str) -> bool:
        """Check if this file should be monitored."""
        file_path = Path(path)
        
        # Skip ignored directories (exact match or .venv* prefix — virtualenvs
        # fire thousands of pip-install events, e.g. the 2026-07-02 storm
        # that wrote 26k violation rows from .venv-3.14t)
        for part in file_path.parts:
            if part in IGNORE_DIRS:
                return False
            if any(part.startswith(p) for p in IGNORE_DIR_PREFIXES):
                return False
        
        # Skip backup files
        for prefix in IGNORE_PREFIXES:
            if file_path.name.startswith(prefix):
                return False
        
        # Skip token files themselves
        if ".audit/gate_tokens" in str(file_path):
            return False
        
        # Check extension
        suffix = file_path.suffix
        if suffix and f"*{suffix}" not in WATCH_PATTERNS:
            # Allow any .py, .md, .json, etc. files
            if suffix not in {".py", ".md", ".json", ".toml", ".yaml", ".yml",
                              ".ts", ".js", ".html", ".css", ".sh", ".ps1"}:
                return False
        
        return True
    
    def _on_modified(self, filepath: str):
        """Handle a file modification event."""
        if not self._should_watch(filepath):
            return
        
        # Check debounce cooldown — skip files recently reverted
        global _revert_cooldown
        now = time.time()
        last_revert = _revert_cooldown.get(filepath, 0)
        if now - last_revert < _REVERT_COOLDOWN_SECONDS:
            return  # Recently reverted — skip to prevent infinite loop
        
        # Get relative path from workspace
        try:
            rel_path = str(Path(filepath).relative_to(_WORKSPACE))
        except ValueError as exc:
            note_failure("parallax_validator:L370", exc)
            rel_path = filepath
        
        valid, reason = verify_token(rel_path)
        
        if valid:
            self.validated += 1
            return
        
        # Unauthorized modification detected
        self.violations += 1
        record_violation(rel_path, reason)
        
        if not self.dry_run:
            success, msg = revert_file(rel_path)
            if success:
                print(f"  [REVERTED] {rel_path}: {msg}", flush=True)
            else:
                print(f"  [REVERT FAILED] {rel_path}: {msg}", flush=True)
        else:
            print(f"  [DRY RUN] Would revert: {rel_path}", flush=True)
    
    def dispatch(self, event):
        """Watchdog dispatch method."""
        if event.event_type in ("modified", "created"):
            self._on_modified(event.src_path)
    
    def get_stats(self) -> dict:
        """Get handler statistics."""
        return {
            "violations": self.violations,
            "validated": self.validated,
        }


# ---------------------------------------------------------------------------
# Audit-Once Mode
# ---------------------------------------------------------------------------

def audit_once(dry_run: bool = False) -> dict:
    """Scan for existing file modifications without valid tokens.
    
    Checks all tracked Python files for modifications that lack
    corresponding authorization tokens. Does NOT use watchdog —
    this is a one-time snapshot audit.
    
    Returns:
        dict with audit results
    """
    tokens_dir = _get_tokens_dir()
    violations = []
    
    # Check git status for modified files
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=str(_WORKSPACE),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        modified = [f.strip() for f in result.stdout.split("\n") if f.strip()]
    except Exception as exc:
        note_failure("parallax_validator:L432", exc)
        modified = []
    
    for filepath in modified:
        token_path = _get_token_path(filepath)
        if not token_path.exists():
            violations.append({
                "file": filepath,
                "reason": "Modified without authorization token",
            })
    
    audit_result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "files_scanned": len(modified),
        "violations": len(violations),
        "violation_details": violations,
        "dry_run": dry_run,
    }
    
    if not dry_run and violations:
        for v in violations:
            record_violation(v["file"], v["reason"])
            revert_file(v["file"])
    
    return audit_result


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parallax Validator Daemon — Independent file modification watchdog",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python parallax_validator.py --watch          # Start watching files
  python parallax_validator.py --watch --dry-run # Log but don't revert
  python parallax_validator.py --audit-once      # One-time audit scan
  python parallax_validator.py --daemon          # Background daemon mode
  python parallax_validator.py --status          # Check if daemon is running
        """
    )
    parser.add_argument("--watch", action="store_true",
                        help="Start file modification watchdog (foreground)")
    parser.add_argument("--daemon", action="store_true",
                        help="Start as background daemon")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log violations but don't auto-revert")
    parser.add_argument("--audit-once", action="store_true",
                        help="Run one-time audit scan for existing violations")
    parser.add_argument("--status", action="store_true",
                        help="Check if daemon is running")
    
    args = parser.parse_args()
    
    if args.status:
        # Check for running daemon
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
            )
            if "parallax_validator" in result.stdout:
                print("Parallax Validator: RUNNING")
            else:
                print("Parallax Validator: NOT RUNNING")
        except Exception:
            print("Parallax Validator: UNKNOWN (cannot check)")
        return
    
    if args.audit_once:
        print("[PARALLAX] Running one-time audit...")
        result = audit_once(dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))
        return
    
    if args.watch or args.daemon:
        print("[PARALLAX] Starting Parallax Validator Daemon...")
        print(f"[PARALLAX] Workspace: {_WORKSPACE}")
        print(f"[PARALLAX] Tokens dir: {_get_tokens_dir()}")
        print(f"[PARALLAX] Dry run: {args.dry_run}")
        print(f"[PARALLAX] Mode: {'daemon' if args.daemon else 'foreground'}")
        print(f"[PARALLAX] Watchdog starting... Press Ctrl+C to stop.")
        
        handler = ParallaxHandler(dry_run=args.dry_run)
        
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
            
            # Create a simple event handler wrapper
            class EventHandler(FileSystemEventHandler):
                def on_modified(self, event):
                    handler.dispatch(event)
                def on_created(self, event):
                    handler.dispatch(event)
            
            observer = Observer()
            observer.schedule(EventHandler(), str(_WORKSPACE), recursive=True)
            observer.start()
            
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                observer.stop()
                stats = handler.get_stats()
                print(f"\n[PARALLAX] Daemon stopped. Stats: {stats}")
            
            observer.join()
        
        except ImportError:
            print("[PARALLAX] ERROR: watchdog library not installed.")
            print("[PARALLAX] Install with: pip install watchdog")
            print("[PARALLAX] Falling back to audit-once mode...")
            result = audit_once(dry_run=args.dry_run)
            print(json.dumps(result, indent=2, default=str))
            sys.exit(1)
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
