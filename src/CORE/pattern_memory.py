# coding: utf-8
"""pattern_memory.py — Semantic search over distilled pattern tables.

Query interface for knowledge_patterns + agent_patterns populated by the
distiller and enriched with nomic-embed-text vectors. Stdlib-only, best
effort (returns [] when Ollama is unreachable).

Usage:
    from CORE.pattern_memory import query_patterns, pattern_stats
    hits = query_patterns("database growth", top_k=5)
"""

import json
import math
import os
import sqlite3
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _db_path() -> Path:
    env_dir = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    base = Path(env_dir) if env_dir else Path.home() / ".azazael"
    return base / "memory_store.db"


def _embed(text: str, timeout: int = 30) -> Optional[List[float]]:
    """Embed via local Ollama. None when unavailable (fail open)."""
    try:
        body = json.dumps({"model": "nomic-embed-text",
                           "prompt": text[:1500]}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/embeddings", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        data = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        return data["embedding"]
    except Exception as exc:
        note_failure("pattern_memory:L38", exc)
        return None


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def query_patterns(text: str, top_k: int = 5,
                   min_confidence: float = 0.0,
                   pattern_types: Optional[List[str]] = None,
                   db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Semantic search over pattern tables.

    Score blends cosine similarity (70%) with stored confidence (30%).
    Returns [{table, id, pattern_type, confidence, score, metadata}].
    """
    qv = _embed(text)
    if not qv:
        return []
    db = Path(db_path) if db_path else _db_path()
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db), timeout=30)
    try:
        rows = conn.execute(
            "SELECT 'knowledge' AS tbl, id, pattern_type, confidence, metadata, embedding "
            "FROM knowledge_patterns WHERE embedding != '[]'").fetchall()
        rows += conn.execute(
            "SELECT 'agent' AS tbl, id, pattern_type, confidence, metadata, embedding "
            "FROM agent_patterns WHERE embedding != '[]'").fetchall()
    finally:
        conn.close()
    hits = []
    for tbl, rid, ptype, conf, meta, emb in rows:
        if conf is None or conf < min_confidence:
            continue
        if pattern_types and ptype not in pattern_types:
            continue
        try:
            vec = json.loads(emb)
        except (json.JSONDecodeError, TypeError) as exc:
            note_failure("pattern_memory:L84", exc)
            continue
        score = 0.7 * _cosine(qv, vec) + 0.3 * float(conf or 0)
        try:
            meta_obj = json.loads(meta) if meta else {}
        except (json.JSONDecodeError, TypeError) as exc:
            note_failure("pattern_memory:L89", exc)
            meta_obj = {"raw": str(meta)[:200]}
        hits.append({"table": tbl, "id": rid, "pattern_type": ptype,
                     "confidence": conf, "score": round(score, 4),
                     "metadata": meta_obj})
    hits.sort(key=lambda h: -h["score"])
    return hits[:top_k]


def pattern_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Counts + embedding coverage for both pattern tables."""
    db = Path(db_path) if db_path else _db_path()
    if not db.exists():
        return {"error": "no db"}
    conn = sqlite3.connect(str(db), timeout=30)
    try:
        k = conn.execute("SELECT COUNT(*) FROM knowledge_patterns").fetchone()[0]
        ke = conn.execute("SELECT COUNT(*) FROM knowledge_patterns WHERE embedding != '[]'").fetchone()[0]
        a = conn.execute("SELECT COUNT(*) FROM agent_patterns").fetchone()[0]
        ae = conn.execute("SELECT COUNT(*) FROM agent_patterns WHERE embedding != '[]'").fetchone()[0]
    finally:
        conn.close()
    return {"knowledge": k, "knowledge_embedded": ke,
            "agent": a, "agent_embedded": ae}
