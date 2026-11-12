#!/usr/bin/env python3
"""
inference_cache.py — File-Backed Inference Citation System (RULE 0Q-A)

Records all inference outputs to .inference_cache/ for verifiable citations.
Replaces text-summary citations ("Gemini: architecture sound") with
file-backed citations (".inference_cache/20260611_120000_gemini.log").

If the cited file doesn't exist on disk -> verifiable lie. No hallucination possible.

Usage:
    from CORE.inference.inference_cache import record_inference

    filepath = record_inference(
        agent_name="thinker-with-files-gemini",
        model="gemini-2.5-pro",
        prompt="Architect a solution for X",
        response="The solution is...",
        metadata={"total_tokens": 1234}
    )
    # Returns: ".inference_cache/20260611_120000_thinker_with_files_gemini.log"
"""

import datetime
import re
import threading
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Auto-record inference spawns in audit trail for RULE 0T verifiable citations
try:
    from CORE.audit_trail import audit_inference_spawn as _audit_inference_spawn
except ImportError as exc:
    note_failure("inference_cache:L32", exc)
    _audit_inference_spawn = None  # audit_trail not available — graceful degradation


# Project-relative cache directory (from project root)
CACHE_DIR = Path(".inference_cache")

# Thread safety lock for atomic writes
_write_lock = threading.Lock()


def _init_cache_dir():
    """Ensure cache directory and .gitignore exist."""
    if not CACHE_DIR.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        gitignore = CACHE_DIR / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")


def record_inference(agent_name, model, prompt, response, metadata=None):
    """Record inference output to a timestamped log file.

    Args:
        agent_name: Agent type string (e.g., "thinker-with-files-gemini")
        model: Model/provider name used
        prompt: The prompt sent to the model
        response: The response received from the model
        metadata: Optional dict with 'total_tokens', 'elapsed_ms', etc.

    Returns:
        str: Relative filepath to the cache log (for citation)
    """
    if metadata is None:
        metadata = {}

    _init_cache_dir()

    now = datetime.datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    iso_timestamp = now.isoformat(timespec="seconds")

    # Sanitize agent name for filename (replace non-alphanumeric with underscore)
    safe_agent = re.sub(r"[^a-zA-Z0-9]", "_", agent_name)

    filename = "%s_%s.log" % (timestamp_str, safe_agent)
    filepath = CACHE_DIR / filename

    log_content = (
        "=== AZAZAEL INFERENCE CACHE ===\n"
        "Timestamp: %s\n"
        "Agent: %s\n"
        "Model: %s\n"
        "Status: SUCCESS\n"
        "Token Count: %s\n"
        "Elapsed: %s ms\n"
        "\n"
        "--- PROMPT ---\n"
        "%s\n"
        "\n"
        "--- RESPONSE ---\n"
        "%s\n"
    ) % (
        iso_timestamp,
        agent_name,
        model,
        metadata.get("total_tokens", 0),
        metadata.get("elapsed_ms", 0),
        prompt,
        response,
    )

    # Semi-atomic write: write to temp, then rename (thread-safe via lock)
    temp_path = filepath.with_suffix(".log.tmp")
    try:
        with _write_lock:
            temp_path.write_text(log_content, encoding="utf-8")
            temp_path.replace(filepath)
        # Record in session audit trail for RULE 0T verifiable citations
        if _audit_inference_spawn is not None:
            try:
                _audit_inference_spawn(agent_name, str(filepath), model)
            except Exception as exc:
                note_failure("inference_cache:L114", exc) # audit trail failure must not block inference recording
    except Exception as exc:
        # Graceful degradation — never crash the caller
        # Graceful degradation — never crash the caller
        note_failure("inference_cache:L116", exc)
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception as exc:
                note_failure("inference_cache:L121", exc)
        return ""

    return str(filepath)


def list_recent_inferences(n=10):
    """list the N most recent cache files (newest first).

    Returns:
        list of str: Relative filepaths
    """
    if not CACHE_DIR.exists():
        return []
    logs = list(CACHE_DIR.glob("*.log"))
    logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p) for p in logs[:n]]


def get_latest_filepath(agent_name):
    """Get the most recent cache file for a specific agent.

    Args:
        agent_name: Agent type string

    Returns:
        str: Relative filepath, or empty string if none found
    """
    if not CACHE_DIR.exists():
        return ""
    safe_agent = re.sub(r"[^a-zA-Z0-9]", "_", agent_name)
    logs = list(CACHE_DIR.glob("*_%s.log" % safe_agent))
    if not logs:
        return ""
    logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(logs[0])


def get_cache_dir():
    """Get the absolute path to the cache directory."""
    return str(CACHE_DIR.resolve())
