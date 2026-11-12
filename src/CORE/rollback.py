"""rollback.py — Undo log and rollback for destructive operations.

Covers followups: #12 (rollback for merge_sessions).

Usage:
    from CORE.rollback import undo_log, rollback_merge, list_undo_logs
"""


__all__ = [
    "list_undo_logs",
    "record_merge",
    "rollback_merge",
]


import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Optional  # removed — all names have native equivalents


_UNDO_DIR = Path.home() / ".azazael" / "undo_logs"


def _ensure_undo_dir():
    _UNDO_DIR.mkdir(parents=True, exist_ok=True)


def record_merge(source_id: str, target_id: str, count: int) -> str:
    """Record a merge operation for potential rollback.
    
    Returns the undo log filepath.
    """
    _ensure_undo_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log = {
        "action": "merge_sessions",
        "ts": datetime.now(timezone.utc).isoformat(),
        "source_id": source_id,
        "target_id": target_id,
        "count": count,
    }
    path = _UNDO_DIR / f"merge_{ts}.json"
    path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    return str(path)


def rollback_merge(undo_path: str) -> dict:
    """Rollback a merge_sessions operation.
    
    Moves events from target back to source session.
    Returns dict with 'rolled_back' count and 'error' if failed.
    """
    path = Path(undo_path)
    if not path.exists():
        return {"error": "Undo log not found"}
    
    log = json.loads(path.read_text(encoding="utf-8"))
    if log.get("action") != "merge_sessions":
        return {"error": f"Unknown action: {log.get('action')}"}
    
    from CORE.direct_memory import DB_PATH
    db_path = str(DB_PATH)
    
    source_id = log["source_id"]
    target_id = log["target_id"]
    
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        cursor = conn.execute(
            "UPDATE memories SET session_id = ? WHERE session_id = ?",
            (source_id, target_id)
        )
        rolled = cursor.rowcount
        # Re-create source session if deleted
        conn.execute(
            "INSERT OR IGNORE INTO sessions(session_id, started_at, source) "
            "VALUES(?, datetime('now'), 'rollback')",
            (source_id,)
        )
        conn.execute(
            "UPDATE sessions SET event_count = event_count + ? WHERE session_id = ?",
            (rolled, source_id)
        )
        conn.execute(
            "UPDATE sessions SET event_count = event_count - ? WHERE session_id = ?",
            (rolled, target_id)
        )
        conn.commit()
        # Mark undo log as used
        log["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
        log["rolled_back_count"] = rolled
        path.write_text(json.dumps(log, indent=2), encoding="utf-8")
        return {"rolled_back": rolled, "source_id": source_id, "target_id": target_id}
    except Exception as e:
        note_failure("rollback:L97", e)
        return {"error": str(e)}
    finally:
        conn.close()


def list_undo_logs(limit: int = 20) -> list:
    """list recent undo logs."""
    _ensure_undo_dir()
    logs = sorted(_UNDO_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    results = []
    for fp in logs[:limit]:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            results.append({
                "filepath": str(fp),
                "action": data.get("action", "?"),
                "ts": data.get("ts", "?"),
                "rolled_back": "rolled_back_at" in data,
                "source": data.get("source_id", "?")[:20],
                "target": data.get("target_id", "?")[:20],
            })
        except Exception as exc:
            note_failure("rollback:L119", exc)
            continue
    return results
