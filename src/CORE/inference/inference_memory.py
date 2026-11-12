# coding: utf-8
"""inference_memory.py — PAMS recording seam for inference turns.

Why this exists
---------------
Azazael's *internal* work is already recorded: `AgentPool`, `Agent`,
`ClusterWORMZ`, `RLTrainLoop` and ~10 other sources call `PamsRecorder` and show
up in the memory store (230k+ memories, 3.7k sessions). Inference was the gap.
A model could answer a question and that answer vanished when the process
exited — nothing in `OpenAIProvider.chat()` touched PAMS, so the one thing an
operator most wants to recall (what the model was asked and what it said) was
the one thing never written down.

This module is that seam. `OpenAIProvider.chat()` calls `record_turn()` on
success and `record_failure()` on error, which puts local-model turns into the
same store as everything else — so `recall_turns()` can answer "what did the
local model say about X last week".

Design constraints (deliberate)
-------------------------------
- **Never raises.** Memory is telemetry, not control flow. A recorder failure
  must not break inference, so every path is guarded and returns a bool.
- **Never blocks meaningfully.** `PamsRecorder.record_event` prefers the PAMS
  HTTP API (2 s timeout, and it caches an unreachable server so later calls skip
  the network entirely), then `LocalMemoryStore`, then a locked JSON buffer.
  With PAMS down the call is a local SQLite insert.
- **Bounded payloads.** The memory store is ~800 MB and already emits a size
  warning, so prompts and completions are truncated (`MAX_TEXT`). Storing full
  32k-token prompts would multiply that store for no recall benefit.
- **Off switch.** `AZAZAEL_INFERENCE_MEMORY=off` disables recording entirely
  (for throughput benchmarking, or when the store is being compacted).

Usage
-----
    from CORE.inference.inference_memory import record_turn, recall_turns

    record_turn(provider="llama_mythos", model="qwythos-9b-mtp",
                prompt="...", content="...")
    for turn in recall_turns(limit=5):
        print(turn["data"].get("content"))
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "record_turn",
    "record_failure",
    "recall_turns",
    "memory_enabled",
    "memory_stats",
    "enforce_retention",
    "MAX_TEXT",
    # Operator-console path (Open Interpreter). Distinct from the provider path
    # because OI never calls OpenAIProvider.chat().
    "record_operator_session",
    "recall_operator_sessions",
    "operator_source",
    "EVENT_OPERATOR",
]

# -- Configuration ------------------------------------------------------

#: Per-field character cap. Keeps one pathological prompt from bloating a
#: store that is already large enough to warn.
MAX_TEXT = 2000

#: Source string, following the `pams_source` convention ("agent", "Codebuff").
SOURCE = "inference"

#: Event types, in the repo's snake_case descriptive style
#: (`training_complete`, `dream_generated`, ...).
EVENT_TURN = "inference_turn"
EVENT_ERROR = "inference_error"

_ENV_FLAG = "AZAZAEL_INFERENCE_MEMORY"

# -- Graceful PAMS import (repo convention: AgentPool.py, Agent.py) ------

try:  # pragma: no cover - availability depends on the deployment
    from CORE.PamsRecorder import get_recorder  # type: ignore
    HAS_PAMS = True
except ImportError as exc:  # pragma: no cover
    note_failure("inference_memory:L86", exc)
    HAS_PAMS = False
    get_recorder = None  # type: ignore

_recorder = None
_recorder_init_attempted = False
_lock = threading.Lock()


def _get_recorder():
    """Lazy singleton. Returns None if memory is unavailable, never raises."""
    global _recorder, _recorder_init_attempted
    if _recorder_init_attempted:
        return _recorder
    with _lock:
        if _recorder_init_attempted:
            return _recorder
        _recorder_init_attempted = True
        if not HAS_PAMS or get_recorder is None:
            return None
        try:
            _recorder = get_recorder()
        except Exception as exc:  # noqa: BLE001 — memory is optional
            note_failure("inference_memory:L108", exc)
            _recorder = None
    return _recorder


def memory_enabled() -> bool:
    """False when `AZAZAEL_INFERENCE_MEMORY=off|0|false|no`."""
    raw = (os.environ.get(_ENV_FLAG) or "on").strip().lower()
    return raw not in ("off", "0", "false", "no")


def _clip(value: Any) -> str:
    """Truncate a field for storage. Always returns a str."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if len(text) <= MAX_TEXT:
        return text
    return text[:MAX_TEXT] + f"...[truncated {len(text) - MAX_TEXT} chars]"


def _record(event_type: str, data: Dict[str, Any], severity: str = "info",
            source: Optional[str] = None) -> bool:
    """Internal: guarded write. Never raises, never returns None.

    `source` defaults to SOURCE. The operator path passes its own so that sessions
    stay separable per model, which the pre-flight doc asks for explicitly.
    """
    if not memory_enabled():
        return False
    recorder = _get_recorder()
    if recorder is None:
        return False
    src = source or SOURCE
    try:
        stored = bool(recorder.record_event(
            event_type, data, source=src, severity=severity,
            tags=[src, str(data.get("provider", ""))],
        ))
    except Exception as exc:  # noqa: BLE001 — must never break inference
        note_failure("inference_memory:L147", exc)
        return False
    if stored:
        _maybe_retention()
    return stored


# -- Retention ----------------------------------------------------------
#
# The memory store is ~800 MB and emits its own size warning at 400 MB, so an
# always-on recording seam would grow it forever. Retention is therefore part of
# the seam, not a follow-up: bounded work amortized over N writes, so an
# unattended process plateaus instead of filling the disk.

#: Inference rows kept. 0 (or negative) disables retention entirely.
_DEFAULT_MAX_TURNS = 5000
_ENV_MAX = "AZAZAEL_INFERENCE_MEMORY_MAX"

#: Operator sessions kept. These are one row per RUN, not per turn, so they arrive
#: far more slowly than inference turns and a smaller cap is right for them.
_DEFAULT_MAX_OPERATOR = 2000
_ENV_MAX_OPERATOR = "AZAZAEL_INFERENCE_MEMORY_MAX_OPERATOR"

#: Run retention every N successful writes.
_RETENTION_EVERY = 200
_PAGE = 500

_writes_since_retention = 0
_retention_lock = threading.Lock()


def _max_turns() -> int:
    raw = (os.environ.get(_ENV_MAX) or "").strip()
    if not raw:
        return _DEFAULT_MAX_TURNS
    try:
        return int(raw)
    except ValueError as exc:
        note_failure("inference_memory:L184", exc)
        return _DEFAULT_MAX_TURNS


def _max_operator() -> int:
    raw = (os.environ.get(_ENV_MAX_OPERATOR) or "").strip()
    if not raw:
        return _DEFAULT_MAX_OPERATOR
    try:
        return int(raw)
    except ValueError as exc:
        note_failure("inference_memory:L194", exc)
        return _DEFAULT_MAX_OPERATOR


def _prune(store, cap: int, **filters: Any) -> int:
    """Tombstone rows matching `filters` beyond the newest `cap`. Returns the count.

    Pages via offset past the cap so the newest `cap` rows are never touched.
    """
    if cap <= 0:
        return 0
    stale: List[int] = []
    offset = cap
    while True:
        page = store.query_memories(limit=_PAGE, offset=offset, **filters)
        if not page:
            break
        stale.extend(int(r["id"]) for r in page if r.get("id") is not None)
        offset += len(page)
        if len(page) < _PAGE:
            break
    if not stale:
        return 0
    return int(store.tombstone(stale))


def enforce_retention(max_turns: Optional[int] = None,
                      vacuum: bool = False) -> Dict[str, Any]:
    """Tombstone old inference rows, for BOTH scopes this module writes.

    Uses the repo's sanctioned non-destructive delete
    (`LocalMemoryStore.tombstone`) rather than raw SQL, so the retirement is
    recorded (tombstoned rows become `severity='deleted'` and are already
    excluded from `query_memories`). Deliberately scoped to this module's own
    two sources: the global `purge_old()` would delete every other source's
    history too.

    Two scopes, because they are selected differently:
      * provider turns  -- `source='inference'`, capped by `_max_turns()`
      * operator sessions -- `event_type='operator_session'`, capped by
        `_max_operator()`. Selected by EVENT TYPE rather than source so that one
        cap covers every model's sessions; source is `operator:<model>` and there
        is no bounded list of models to iterate.

    Missing the second scope was a real bug when the operator seam was added:
    those rows were written through the same `_record()` path but were invisible
    to a source-scoped prune, so they would have grown forever.

    Tombstoning does not shrink the file. Pass `vacuum=True` to reclaim space —
    that rewrites the DB, so do it out of band, not from a hot write path.

    Never raises.
    """
    cap = _max_turns() if max_turns is None else int(max_turns)
    if cap <= 0:
        return {"ok": True, "skipped": "retention disabled (cap <= 0)"}

    recorder = _get_recorder()
    if recorder is None:
        return {"ok": False, "reason": "recorder unavailable"}

    # The local store is created lazily on first write, so it may still be None.
    try:
        recorder._ensure_local_store()  # noqa: SLF001 — the only way to force init
        store = getattr(recorder, "_local_store", None)
    except Exception as exc:  # noqa: BLE001
        note_failure("inference_memory:L259", exc)
        store = None
    if store is None:
        return {"ok": False, "reason": "no local store"}

    op_cap = _max_operator()
    try:
        turns = _prune(store, cap, source=SOURCE)
        operators = _prune(store, op_cap, event_type=EVENT_OPERATOR)
    except Exception as exc:  # noqa: BLE001
        note_failure("inference_memory:L268", exc)
        return {"ok": False, "reason": "query or tombstone failed"}

    result: Dict[str, Any] = {
        "ok": True, "cap": cap, "operator_cap": op_cap,
        "turns_tombstoned": turns, "operator_tombstoned": operators,
        "tombstoned": turns + operators,
    }
    if vacuum:
        try:
            store.vacuum()
            result["vacuumed"] = True
        except Exception as exc:  # noqa: BLE001
            note_failure("inference_memory:L280", exc)
            result["vacuumed"] = False
    return result


def _maybe_retention() -> None:
    """Amortized retention trigger. Never raises into the caller."""
    global _writes_since_retention
    try:
        with _retention_lock:
            _writes_since_retention += 1
            if _writes_since_retention < _RETENTION_EVERY:
                return
            _writes_since_retention = 0
        if _max_turns() > 0:
            enforce_retention()
    except Exception as exc:  # noqa: BLE001 — maintenance must never break inference
        note_failure("inference_memory:L296", exc)


# -- Public API ---------------------------------------------------------

def record_turn(
    provider: str,
    model: str,
    prompt: str,
    content: str,
    system_prompt: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
    elapsed_ms: int = 0,
    finish_reason: Optional[str] = None,
) -> bool:
    """Persist one successful inference turn. Returns True if stored.

    Called from `OpenAIProvider.chat()`; safe to call from anywhere.
    """
    return _record(EVENT_TURN, {
        "provider": str(provider or ""),
        "model": str(model or ""),
        "prompt": _clip(prompt),
        "system_prompt": _clip(system_prompt),
        "content": _clip(content),
        "usage": usage or {},
        "elapsed_ms": int(elapsed_ms or 0),
        "finish_reason": finish_reason or "",
        # The silent-empty-turn failure mode, flagged at write time so a
        # recall query can find it without re-deriving it.
        "empty_content": not (content or "").strip(),
        "thought_leaked": bool(finish_reason == "length"),
    })


def record_failure(provider: str, prompt: str, error: str, elapsed_ms: int = 0) -> bool:
    """Persist a failed inference call (severity=error)."""
    return _record(EVENT_ERROR, {
        "provider": str(provider or ""),
        "prompt": _clip(prompt),
        "error": _clip(error),
        "elapsed_ms": int(elapsed_ms or 0),
    }, severity="error")


def recall_turns(limit: int = 20, provider: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read back recent inference turns, newest first. Returns [] on failure."""
    recorder = _get_recorder()
    if recorder is None:
        return []
    try:
        rows = recorder.query_memories(source=SOURCE, event_type=EVENT_TURN, limit=limit)
    except Exception as exc:  # noqa: BLE001
        note_failure("inference_memory:L349", exc)
        return []
    if provider:
        rows = [r for r in rows
                if str((r.get("data") or {}).get("provider", "")) == provider]
    return list(rows)


# -- Operator-console path ---------------------------------------------
#
# `record_turn` above covers azazael's own provider layer, which the WORMS use.
# Open Interpreter does not: it talks to llama-server over HTTP itself, so every
# operator run was invisible to memory and each session started amnesiac. These
# functions cover that path. They record at SESSION granularity on purpose --
# per-turn rows for a 25-message transcript would flood a store that is already
# large enough to warn, and what an operator needs to recall is the outcome.

#: Event type for a completed operator session.
EVENT_OPERATOR = "operator_session"

_OPERATOR_SOURCE_PREFIX = "operator"


def operator_source(model: str) -> str:
    """Per-model source string, so one model's sessions never mix with another's."""
    return f"{_OPERATOR_SOURCE_PREFIX}:{str(model or 'unknown').strip()}"


def record_operator_session(rec: Dict[str, Any], source: Optional[str] = None) -> bool:
    """Persist one finished operator run. Returns True if stored.

    `rec` is the harness's training record. Only the fields that make a session
    recallable are kept, and all of them are clipped.
    """
    if not isinstance(rec, dict):
        return False
    ver = rec.get("verification") or {}
    gates = ver.get("gates") or {}
    model = str(rec.get("model") or "")
    return _record(EVENT_OPERATOR, {
        "provider": f"operator:{model}",
        "model": model,
        "label": _clip(rec.get("label")),
        "task": _clip(rec.get("task")),
        "success": bool(rec.get("success")),
        "termination": _clip(rec.get("termination")),
        "elapsed_s": rec.get("elapsed_s"),
        "code_blocks": rec.get("code_blocks"),
        "tracebacks": rec.get("tracebacks"),
        "nudges": rec.get("nudges"),
        "max_tokens": rec.get("max_tokens"),
        "server_config": _clip(rec.get("server_config")),
        "checks_passed": sum(1 for v in (ver.get("checks") or {}).values() if v),
        "gates_failed": [k for k, v in gates.items() if not v],
        # Recorded so a recall query can ask "what does this model fail at?"
        # without replaying the transcript.
        "artifact_written": bool(ver.get("file_exists")),
    }, severity="info" if rec.get("success") else "warning",
        source=source or operator_source(model))


def recall_operator_sessions(limit: int = 10,
                             model: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read back recent operator sessions, newest first. Returns [] on failure."""
    recorder = _get_recorder()
    if recorder is None:
        return []
    try:
        rows = recorder.query_memories(
            event_type=EVENT_OPERATOR, limit=limit,
            source=operator_source(model) if model else None)
    except Exception as exc:  # noqa: BLE001
        note_failure("inference_memory:L420", exc)
        return []
    return list(rows)


def memory_stats() -> Dict[str, Any]:
    """Diagnostics: is the seam live, and where is it writing?"""
    recorder = _get_recorder()
    stats: Dict[str, Any] = {
        "enabled": memory_enabled(),
        "pams_module_available": HAS_PAMS,
        "recorder": recorder is not None,
    }
    if recorder is not None:
        try:
            stats["store"] = recorder.get_stats()
        except Exception as exc:  # noqa: BLE001
            note_failure("inference_memory:L436", exc)
            stats["store"] = "unavailable"
    return stats
