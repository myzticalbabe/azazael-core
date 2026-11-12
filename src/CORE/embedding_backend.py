# coding: utf-8
"""embedding_backend.py — Real semantic embeddings with disk cache and fail-open chain.

Backend chain (in priority order):
  1. Ollama nomic-embed-text  (768-dim, local, free, semantic)   -- PRIMARY
  2. MD5 bag-of-words          (768-dim, deterministic, offline) -- FALLBACK

All backends emit the SAME canonical dimension (CANONICAL_DIM = 768) so every
vector in the store is comparable via cosine similarity regardless of which
backend produced it.

Disk cache lives on D: (override with AZAZAEL_EMBEDDING_DIR) as a SQLite DB,
keyed by sha256(text) — repeated texts are never re-embedded and vectors
survive restarts.

Usage:
    from CORE.embedding_backend import embed_text, embed_many, health
    vec = embed_text("how to fix a docstring-trapped import")   # 768 floats
    hits = query_similar_patterns(vec, top_k=5)                  # local_memory API
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
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

CANONICAL_DIM = 768
OLLAMA_URL = "http://127.0.0.1:11434/api/embeddings"
DEFAULT_MODEL = "nomic-embed-text"
_NEGATIVE_CACHE_TTL = 30.0  # seconds to remember Ollama being down
_MAX_TEXT = 1500            # chars sent to the model per call

# Embedding models available through Ollama with their NATIVE output dims.
# The optimizer sweeps candidate dims across every model that is pulled
# locally, so pulling a higher-dim model automatically expands the sweep.
MODEL_REGISTRY: Dict[str, int] = {
    "nomic-embed-text": 768,
    "bge-m3": 1024,
    "all-minilm": 384,
    "mxbai-embed-large": 1024,
    "granite-embedding:278m": 1024,
    "qwen3.5-9b": 4096,   # llama-server native (per-token, mean-pooled)
}


def _embedding_dir() -> Path:
    env_dir = os.environ.get("AZAZAEL_EMBEDDING_DIR", "")
    if env_dir:
        return Path(env_dir)
    return Path("D:/azazael_embeddings")


def _cache_db_path() -> Path:
    return _embedding_dir() / "embedding_cache.db"


# --------------------------------------------------------------------------
# Disk cache
# --------------------------------------------------------------------------

class _EmbeddingCache:
    """SQLite disk cache keyed by sha256(text). Thread-safe, WAL mode."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = Path(db_path) if db_path else _cache_db_path()
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_db()

    def _ensure_db(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # D: not writable/absent — fall back to user home
            # D: not writable/absent — fall back to user home
            note_failure("embedding_backend:L82", exc)
            self._db_path = Path.home() / ".azazael" / "embedding_cache.db"
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS embeddings("
                    "key TEXT PRIMARY KEY, vec TEXT NOT NULL, "
                    "backend TEXT NOT NULL, dim INTEGER NOT NULL, "
                    "created_at REAL NOT NULL)"
                )
                conn.commit()
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(str(self._db_path), timeout=10)
            return self._conn

    @staticmethod
    def _key(text: str, model: str) -> str:
        return hashlib.sha256(f"{model}\x00{text}".encode("utf-8", "replace")).hexdigest()

    def get(self, text: str, model: str = DEFAULT_MODEL) -> Optional[List[float]]:
        key = self._key(text, model)
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT vec FROM embeddings WHERE key=?", (key,)
            ).fetchone()
            if not row:
                return None
            vec = json.loads(row[0])
            return vec if isinstance(vec, list) else None
        except Exception as exc:
            note_failure("embedding_backend:L121", exc)
            return None

    def put(self, text: str, vec: List[float], backend: str,
            model: str = DEFAULT_MODEL) -> None:
        if not vec:
            return
        key = self._key(text, model)
        try:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO embeddings(key, vec, backend, dim, created_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (key, json.dumps(vec), backend, len(vec), time.time()),
            )
            conn.commit()
        except Exception as exc:
            note_failure("embedding_backend:L137", exc)

    def stats(self) -> Dict[str, Any]:
        try:
            conn = self._connect()
            total = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
            by_backend = dict(conn.execute(
                "SELECT backend, COUNT(*) FROM embeddings GROUP BY backend"
            ).fetchall())
            return {"cache_path": str(self._db_path), "cached": total, "by_backend": by_backend}
        except Exception as exc:
            note_failure("embedding_backend:L148", exc)
            return {"cache_path": str(self._db_path), "cached": 0, "by_backend": {}}


_cache = _EmbeddingCache()


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

class _OllamaBackend:
    """Real semantic embeddings via local Ollama (any pulled embedding model)."""

    name = "ollama"

    def __init__(self) -> None:
        self._down_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def available(self, model: str = DEFAULT_MODEL) -> bool:
        if time.time() < self._down_until.get(model, 0.0):
            return False
        try:
            req = urllib.request.Request(
                OLLAMA_URL,
                data=json.dumps({"model": model, "prompt": "ping"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                resp.read()
            return True
        except Exception as exc:
            note_failure("embedding_backend:L181", exc)
            with self._lock:
                self._down_until[model] = time.time() + _NEGATIVE_CACHE_TTL
            return False

    def embed(self, text: str, model: str = DEFAULT_MODEL) -> Optional[List[float]]:
        if time.time() < self._down_until.get(model, 0.0):
            return None
        try:
            body = json.dumps({"model": model, "prompt": text[:_MAX_TEXT]}).encode()
            req = urllib.request.Request(
                OLLAMA_URL, data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            vec = data.get("embedding")
            if not isinstance(vec, list) or len(vec) < 64:
                return None
            return [float(x) for x in vec]
        except Exception as exc:
            note_failure("embedding_backend:L201", exc)
            with self._lock:
                self._down_until[model] = time.time() + _NEGATIVE_CACHE_TTL
            return None


class _LlamaServerBackend:
    """High-dim embeddings via llama.cpp server (e.g. Qwen3.5) in --embedding
    mode. Returns per-token vectors; mean-pooled into one document vector.

    Endpoint contract (llama-server /embedding):
      POST -> [{"index": 0, "embedding": [[tok0...], [tok1...], ...]}]
    """

    name = "llama_server"

    def __init__(self, url: str = "http://127.0.0.1:8613/embedding",
                 model: str = "qwen3.5-9b", native_dim: int = 4096) -> None:
        self._url = url
        self._model = model
        self._native_dim = native_dim
        self._down_until = 0.0
        self._lock = threading.Lock()

    def available(self) -> bool:
        if time.time() < self._down_until:
            return False
        try:
            base = self._url.rsplit("/", 1)[0]
            req = urllib.request.Request(base + "/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
            ok = data.get("status") == "ok"
            if not ok:
                raise RuntimeError("not ready")
            return True
        except Exception as exc:
            note_failure("embedding_backend:L237", exc)
            with self._lock:
                self._down_until = time.time() + _NEGATIVE_CACHE_TTL
            return False

    def embed(self, text: str) -> Optional[List[float]]:
        if time.time() < self._down_until:
            return None
        try:
            body = json.dumps({"content": text[:_MAX_TEXT]}).encode()
            req = urllib.request.Request(
                self._url, data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            toks = data[0]["embedding"] if isinstance(data, list) else []
            if not isinstance(toks, list) or not toks or not isinstance(toks[0], list):
                return None
            dim = len(toks[0])
            pooled = [0.0] * dim
            for tok in toks:
                for i, x in enumerate(tok):
                    pooled[i] += float(x)
            pooled = [x / len(toks) for x in pooled]
            norm = math.sqrt(sum(x * x for x in pooled))
            if norm > 0:
                pooled = [x / norm for x in pooled]
            return pooled
        except Exception as exc:
            note_failure("embedding_backend:L266", exc)
            with self._lock:
                self._down_until = time.time() + _NEGATIVE_CACHE_TTL
            return None


class _Md5Backend:
    """Deterministic offline fallback: MD5 bag-of-words, CANONICAL_DIM wide.

    Each distinct word token maps to multiple hash-seeded bucket positions so
    the vector spreads across all 768 dims. Deterministic across runs and
    machines — the same text always yields the same vector.
    """

    name = "md5_bow"

    _TOKEN_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * CANONICAL_DIM
        tokens = self._TOKEN_RE.findall(text.lower())
        if not tokens:
            return vec
        # 1.5x coverage: each token writes to dims derived from 3 hash seeds
        for tok in tokens:
            for seed in (0, 1, 2):
                h = hashlib.md5(f"{seed}:{tok}".encode("utf-8")).hexdigest()
                idx = int(h[:8], 16) % CANONICAL_DIM
                vec[idx] += 1.0
        # L2-normalize so cosine behaves well
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


# --------------------------------------------------------------------------
# Optimal-dimension selection (self-tuning)
# --------------------------------------------------------------------------

_CANDIDATE_DIMS = [64, 128, 192, 256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096]


def _pulled_models() -> List[str]:
    """Embedding models actually available (Ollama pulls + llama-server)."""
    pulled: List[str] = []
    try:
        req = urllib.request.Request(OLLAMA_URL.replace("/api/embeddings", "/api/tags"))
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = {m.get("name", "").split(":")[0] for m in data.get("models", [])}
    except Exception as exc:
        note_failure("embedding_backend:L317", exc)
        names = set()
    for name in names:
        if name in MODEL_REGISTRY:
            pulled.append(name)
    if not pulled and _ollama.available(DEFAULT_MODEL):
        pulled.append(DEFAULT_MODEL)
    # llama-server high-dim model (Qwen3.5), if it's up
    if _llama.available():
        pulled.append(_llama._model)
    return pulled


def _config_get(key: str, default: Any = None) -> Any:
    try:
        conn = _cache._connect()
        row = conn.execute(
            "SELECT value FROM config WHERE key=?", (key,)
        ).fetchone()
        if not row:
            return default
        return json.loads(row[0])
    except Exception as exc:
        note_failure("embedding_backend:L339", exc)
        return default


def _config_put(key: str, value: Any) -> None:
    try:
        conn = _cache._connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO config(key, value) VALUES(?, ?)",
            (key, json.dumps(value)),
        )
        conn.commit()
    except Exception as exc:
        note_failure("embedding_backend:L354", exc)


def optimal_dim() -> int:
    """The dimension the system chose for retrieval (defaults to native 768)."""
    d = _config_get("optimal_dim", CANONICAL_DIM)
    return int(d) if isinstance(d, (int, float)) and d > 0 else CANONICAL_DIM


def optimal_model() -> str:
    """The model the system chose alongside the optimal dim (store's vectors)."""
    m = _config_get("optimal_model", DEFAULT_MODEL)
    return str(m) if isinstance(m, str) and m else DEFAULT_MODEL


def project(vec: List[float], k: int) -> List[float]:
    """Project a native-dim vector to the first k dims, L2-normalized.

    Deterministic and stable: changing k never requires re-embedding or
    rewriting the store — projection happens at query time on both sides.
    """
    if not vec:
        return []
    k = max(1, min(int(k), len(vec)))
    sub = vec[:k]
    norm = math.sqrt(sum(v * v for v in sub))
    if norm > 0:
        sub = [v / norm for v in sub]
    return sub


def _corpus_for_optimization(limit: int = 200) -> List[Dict[str, Any]]:
    """Gather (text, group) pairs from the live store for self-retrieval eval.

    group = trace_id for reasoning traces, else pattern_type. The eval treats
    same-group docs as the correct retrieval target (query by problem text,
    must retrieve its own group above others).
    """
    import sqlite3 as _sqlite3

    db = Path.home() / ".azazael" / "memory_store.db"
    docs: List[Dict[str, Any]] = []
    if not db.exists():
        return docs
    try:
        conn = _sqlite3.connect(str(db), timeout=10)
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            "SELECT pattern_type, metadata FROM knowledge_patterns "
            "WHERE metadata IS NOT NULL AND metadata != '' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        note_failure("embedding_backend:L408", exc)
        return docs
    for r in rows:
        try:
            m = json.loads(r["metadata"])
        except Exception as exc:
            note_failure("embedding_backend:L413", exc)
            continue
        if not isinstance(m, dict):
            continue
        text = " ".join(str(m.get(k, "")) for k in
                        ("problem", "evidence", "solution", "text", "key",
                         "source", "event_type", "generalization"))
        text = text.strip()
        if len(text) < 10:
            continue
        group = m.get("trace_id") or r["pattern_type"] or "unknown"
        docs.append({"text": text, "group": str(group)})
    return docs


# Paraphrase ground truth: (query, expected group). Evaluates how well the
# dim/model generalizes to NEW phrasings, not just exact self-retrieval.
_PARAPHRASE_EVAL: List[tuple] = [
    ("pyflakes undefined name import in docstring", "TR-03"),
    ("missing import after refactor renamed module", "TR-02"),
    ("np undefined in type annotation needs module level import", "TR-04"),
    ("f-string code generator forgot escape braces", "TR-05"),
    ("function called but never defined call site is the spec", "TR-06"),
    ("pyflakes false positive injected fragment host module", "TR-08"),
    ("dynamic globals false positive worm classes", "TR-09"),
    ("verification protocol five layer compile import tests", "TR-10"),
    ("structural corruption nested classes rewrite or delete", "TR-07"),
    ("broken try except repeated copies fix all not one", "TR-01"),
]


def _mrr_at_dim(docs: List[Dict[str, Any]], k: int, model: str = DEFAULT_MODEL) -> float:
    """Mean reciprocal rank@5 at projection dim k, blending two signals:

    - 60% paraphrase MRR: known query->group pairs (generalization — does the
      dim/model still retrieve the right trace when the wording changes?)
    - 40% self-retrieval MRR: each doc queried by its own text

    Vectors come from the given model (cached — no re-embedding cost) and are
    projected to k dims. Uses numpy matrix ops for speed when available.
    """
    if not docs:
        return 0.0
    texts = [d["text"] for d in docs]
    groups = [d["group"] for d in docs]
    full = embed_many(texts, model)
    proj = [project(v, k) for v in full]
    try:
        import numpy as np
        M = np.asarray(proj, dtype=np.float64)
        M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
        sim = M @ M.T  # (n, n) cosine
        np.fill_diagonal(sim, -1.0)  # exclude self
        mat = sim
    except Exception as exc:
        note_failure("embedding_backend:L467", exc)
        mat = None

    def _order(i: int) -> List[int]:
        if mat is not None:
            return np.argsort(-mat[i]).tolist()
        return sorted(range(len(docs)), key=lambda j: -cosine(proj[i], proj[j]))

    # 1) paraphrase MRR (generalization)
    para_total = 0.0
    para_hits = 0
    q_vecs = {q: embed_text(q, model) for q, _ in _PARAPHRASE_EVAL}
    q_proj = {q: project(v, k) for q, v in q_vecs.items() if v}
    if q_proj:
        try:
            import numpy as np
            Q = np.asarray(list(q_proj.values()), dtype=np.float64)
            Q = Q / (np.linalg.norm(Q, axis=1, keepdims=True) + 1e-9)
            qsim = Q @ M.T
            qmat = True
        except Exception as exc:
            note_failure("embedding_backend:L487", exc)
            qmat = False
        for qi, (q, expected) in enumerate(_PARAPHRASE_EVAL):
            if q not in q_proj:
                continue
            para_hits += 1
            if qmat:
                order = np.argsort(-qsim[qi]).tolist()
            else:
                scores = [cosine(q_proj[q], proj[j]) for j in range(len(docs))]
                order = sorted(range(len(docs)), key=lambda j: -scores[j])
            for rank, j in enumerate(order[:5]):
                if groups[j] == expected:
                    para_total += 1.0 / (rank + 1)
                    break
    para_mrr = para_total / para_hits if para_hits else 0.0

    # 2) self-retrieval MRR
    self_total = 0.0
    for i in range(len(docs)):
        for rank, j in enumerate(_order(i)[:5]):
            if groups[j] == groups[i]:
                self_total += 1.0 / (rank + 1)
                break
    self_mrr = self_total / len(docs)

    return 0.6 * para_mrr + 0.4 * self_mrr


def optimize_dimension(verbose: bool = True) -> Dict[str, Any]:
    """Evaluate candidate dims across ALL pulled embedding models and pick the winner.

    Every candidate dim is evaluated with the pulled model whose native dim
    is >= that candidate (the highest-dim model available covers the top of
    the range). Returns {best_dim, scores, models, corpus_size, evaluated_at}.
    The winner is stored in the D: cache DB config table and used by the
    query paths from then on.
    """
    docs = _corpus_for_optimization()
    scores: Dict[str, float] = {}
    models_used: Dict[str, str] = {}
    if len(docs) < 2:
        result = {"best_dim": CANONICAL_DIM, "scores": scores,
                  "corpus_size": len(docs), "evaluated_at": time.time(),
                  "note": "corpus too small for evaluation"}
        _config_put("dim_optimization", result)
        return result
    pulled = _pulled_models()
    # map each candidate dim to the pulled model with the smallest native
    # dim that still covers it (bigger models cover the high end)
    def _cover_model(k: int) -> Optional[str]:
        cands = [m for m in pulled if MODEL_REGISTRY.get(m, 0) >= k]
        if not cands:
            return None
        # prefer the model with the closest native dim >= k; ties go to the
        # richer model so high dims stay real (e.g. k=1536 uses qwen3.5)
        return min(cands, key=lambda m: (MODEL_REGISTRY.get(m, 0) - k, -MODEL_REGISTRY.get(m, 0)))
    for d in _CANDIDATE_DIMS:
        m = _cover_model(d)
        if m is None:
            scores[str(d)] = 0.0
            models_used[str(d)] = "unavailable"
            continue
        models_used[str(d)] = m
        try:
            scores[str(d)] = round(_mrr_at_dim(docs, d, m), 4)
        except Exception as exc:
            note_failure("embedding_backend:L553", exc)
            scores[str(d)] = 0.0
    best_dim = max(_CANDIDATE_DIMS, key=lambda d: scores[str(d)])
    best_model = models_used.get(str(best_dim)) or DEFAULT_MODEL
    result = {"best_dim": best_dim, "best_model": best_model,
              "scores": scores, "models": models_used,
              "pulled_models": pulled, "corpus_size": len(docs),
              "evaluated_at": time.time()}
    _config_put("optimal_dim", best_dim)
    _config_put("optimal_model", best_model)
    _config_put("dim_optimization", result)
    if verbose:
        print(f"corpus: {len(docs)} docs | pulled models: {pulled}")
        for d in _CANDIDATE_DIMS:
            m = models_used.get(str(d), "-")
            bar = "#" * int(scores[str(d)] * 40)
            print(f"  {d:4d} dims  MRR@5 {scores[str(d)]:.4f}  [{m:20s}] {bar}")
        print(f"--> optimal dim: {best_dim}")
    return result


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

_ollama = _OllamaBackend()
_llama = _LlamaServerBackend()
_md5 = _Md5Backend()
_pick_lock = threading.Lock()
_last_backend: str = "md5_bow"


def _backend_order() -> List[Any]:
    pref = os.environ.get("AZAZAEL_EMBEDDING_BACKEND", "").strip().lower()
    if pref == "md5":
        return [_md5, _ollama, _llama]
    return [_ollama, _llama, _md5]


def embed_text(text: str, model: str = DEFAULT_MODEL) -> List[float]:
    """Embed a single text with the given model. Never raises.

    Returns the model's native-dim vector (nomic=768, bge-m3=1024, ...).
    Falls back to MD5 (768-dim) when Ollama is unreachable.
    """
    global _last_backend
    if not text or not text.strip():
        return [0.0] * CANONICAL_DIM
    cached = _cache.get(text, model)
    if cached is not None:
        return cached
    for backend in _backend_order():
        bname = getattr(backend, "name", "")
        if bname == "ollama" and not _ollama.available(model):
            continue
        if bname == "llama_server" and model != _llama._model:
            continue
        try:
            if bname == "ollama":
                vec = backend.embed(text, model)
            else:
                vec = backend.embed(text)
        except Exception as exc:
            note_failure("embedding_backend:L615", exc)
            vec = None
        if vec is not None:
            with _pick_lock:
                _last_backend = f"{backend.name}:{model}"
            _cache.put(text, vec, backend.name, model)
            return vec
    # absolute last resort (never cached: md5 is microseconds to recompute,
    # and caching it would poison the store with weak vectors on transient
    # backend hiccups)
    return _md5.embed(text)


def embed_many(texts: List[str], model: str = DEFAULT_MODEL) -> List[List[float]]:
    """Embed a list of texts with the given model. Order preserved; never raises."""
    out: List[List[float]] = []
    for t in texts:
        try:
            out.append(embed_text(t, model))
        except Exception as exc:
            note_failure("embedding_backend:L634", exc)
            out.append([0.0] * CANONICAL_DIM)
    return out


def cosine(a: List[float], b: List[float]) -> float:
    """Dimension-agnostic cosine similarity; 0.0 on empty/mismatched."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def health() -> Dict[str, Any]:
    """Backend status: which backend is active, cache stats, model reachability."""
    ollama_up = _ollama.available(DEFAULT_MODEL)
    models = {}
    for name, dim in MODEL_REGISTRY.items():
        if name == "qwen3.5-9b":
            models[name] = {"native_dim": dim, "reachable": _llama.available(),
                            "backend": "llama_server"}
        else:
            models[name] = {"native_dim": dim, "reachable": _ollama.available(name),
                            "backend": "ollama"}
    return {
        "canonical_dim": CANONICAL_DIM,
        "optimal_dim": optimal_dim(),
        "optimal_model": optimal_model(),
        "ollama_reachable": ollama_up,
        "llama_server_reachable": _llama.available(),
        "active_backend": "ollama_nomic" if ollama_up else "md5_bow",
        "last_used_backend": _last_backend,
        "model": DEFAULT_MODEL,
        "endpoint": OLLAMA_URL,
        "models": models,
        "cache": _cache.stats(),
        "dim_optimization": _config_get("dim_optimization", {}),
    }


if __name__ == "__main__":
    import sys
    tests = [
        "undefined name where the import is in a docstring",
        "missing import after a refactor renamed the module",
        "f-string code generator forgot to escape braces",
        "pyflakes says name undefined but grep shows import inside docstring",
    ]
    for t in tests:
        v = embed_text(t)
        print(f"{len(v):3d} dims | {health()['active_backend']:12s} | {t[:60]}")
    print(json.dumps(health(), indent=2))
    print("\n=== self-tuning dimension selection ===")
    opt = optimize_dimension()
    print(json.dumps(opt, indent=2))