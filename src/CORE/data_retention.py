"""data_retention.py — Data lifecycle management for Azazael.

Covers followups: #17 (event TTL), #18 (session rotation), #19 (DB size quota),
#20 (audit archival), #21 (scheduled GC), #24 (DB vacuum scheduler).

Usage:
    from CORE.data_retention import enforce_event_ttl, rotate_old_sessions, \
        check_db_size_quota, archive_old_audit_files, run_maintenance
"""


__all__ = [
    "AUDIT_ARCHIVE_DAYS",
    "DB_SIZE_QUOTA_MB",
    "DISTILLED_EVENT",
    "DISTILLED_TTL_DAYS",
    "EVENT_TTL_DAYS",
    "LOG_ROTATE_KEEP",
    "LOG_ROTATE_MB",
    "PROTECTED_EVENTS",
    "SESSION_ROTATION_HOURS",
    "SPAN_TTL_DAYS",
    "TYPE_CAPS",
    "VACUUM_THRESHOLD_PCT",
    "archive_old_audit_files",
    "check_db_size_quota",
    "cmd_maintain",
    "distill_aged_memories",
    "distill_cap_victims",
    "enforce_event_ttl",
    "enforce_global_quota",
    "enforce_span_ttl",
    "enforce_type_caps",
    "newest_backup",
    "rotate_old_sessions",
    "rotate_logs",
    "run_maintenance",
    "vacuum_if_needed",
    "wal_checkpoint",
]


import json
import os
import sqlite3
import sys
import gzip
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List
import shutil as _shutil


#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# -- Lifecycle-owned collaborators (Lane C redo, 2026-10-22) ----------------
# First-party, cycle-free (verified: pattern_memory imports only
# failure_ledger at top; local_memory pulls data_retention lazily, so this
# direction never cycles). Declared once at module load; the quota path and
# _composite_victims read the globals instead of importing per call.
try:
    from CORE.pattern_memory import _cosine as _embed_cosine, _embed as _embed_text
    _EMBED_OK = True
except ImportError as exc:
    note_failure("data_retention:embed-import", exc)
    _embed_cosine = None  # type: ignore
    _embed_text = None  # type: ignore
    _EMBED_OK = False
try:
    from CORE.local_memory import get_memory_store as _get_memory_store
    _STORE_OK = True
except ImportError as exc:
    note_failure("data_retention:store-import", exc)
    _get_memory_store = None  # type: ignore
    _STORE_OK = False


def _note_failure(where: str, exc: BaseException | None = None) -> None:
    """Record a fail-open swallow so the degradation is visible."""
    note_failure(where, exc)


# Default thresholds
EVENT_TTL_DAYS = 90          # Auto-delete events older than this
SESSION_ROTATION_HOURS = 24  # Auto-close sessions older than this
DB_SIZE_QUOTA_MB = 500       # Warn when DB exceeds this
AUDIT_ARCHIVE_DAYS = 30      # Archive .audit/ files older than this
VACUUM_THRESHOLD_PCT = 20    # Auto-vacuum when free pages exceed this %
LOG_ROTATE_MB = 2            # Rotate logs/*.log past this size
LOG_ROTATE_KEEP = 5          # Keep this many generations (.1 .. .5)

# Per-type keep-last-N caps (2026-09-08): high-volume regenerable
# bookkeeping rows. SAFETY: only rolling-window data is capped here —
# evolution pools read the most-recent <=2000 rows, well within every
# cap below. NEVER add evolution_result / evolution_promotion (the
# selection history) or severity-filtered rows (alert evidence).
#
# 2026-09-20: the store reached 984 MB (184% of the 500 MB quota) while every
# cap below was already satisfied, so the quota check was reporting a number
# nothing acted on. Measured by SUM(LENGTH(data)):
#
#   state_saved              4005 rows   316.2 MB   (avg 79 KB each!)
#   nature_cluster_discovery  61362 rows    11.8 MB
#   q_table_persisted         20000 rows    11.3 MB
#   otel_spans (separate table) 112k rows   12.2 MB
#
# `state_saved` is MetaCognitionWorm writing its entire state object once per
# cycle, and the FTS5 index over that JSON (memories_fts is external-content
# with delete triggers) is most of the rest of the file. It is rolling-window
# data by construction: the only reader takes `limit=5`
# (meta_cognition.py:711) and monitor_learning.py only ages the newest one. So
# keeping the newest 50 is a 10x margin on every consumer.
TYPE_CAPS = {
    # 2026-10-31: the RL step tap had NO cap and held 579 686 of the store's
    # 595 212 rows (~23/s across processes, ~7 hours of writing). Nothing reads
    # it back beyond the newest rows -- only trainers_v2 writes it -- so 20000 is
    # the same 10x margin as the caps below. Measured effect of trimming to this
    # cap: 771 MB -> 196 MB after VACUUM, with 559 686 rows archived first to
    # G:/MEMORY_ARCHIVE_2026-10-31 and read back for verification.
    "episode_complete": 20000,
    "training_complete": 20000,
    "q_table_persisted": 20000,
    "model_registered": 20000,
    "db_health_snapshot": 2000,
    "db_health_alert": 5000,
    "generation_complete": 5000,
    # 2026-09-27: the outcome tap itself was uncapped (155k rows and
    # climbing ~60k/night) while every consumer reads most-recent <=2000
    # rows. 20000 is the same 10x margin as the caps above; distill-first
    # keeps day-grain history in rollups before the trim.
    "clusterwormz_worm_executed": 20000,
    # Rolling-window state/telemetry writes (see measurement above).
    "state_saved": 50,
    "nature_cluster_discovery": 2000,
    "category_cluster_executed": 3000,
    "cross_category_route": 2000,
    "dream_generated": 1000,
    "strategy_adaptation": 1000,
    "introspection_report": 500,
    "policy_applied": 500,
}

# otel_spans lives outside `memories`, so enforce_type_caps never saw it. Spans
# are pure telemetry (start_time is a NANOSECOND epoch) and nothing queries
# them beyond the live OTLP viewer. 2026-09-28: 1-day horizon (was 3) --
# steady state was ~350k rows / ~68MB for data no reader consumes.
SPAN_TTL_DAYS = 1

#: Event type for self-distillation rollups (2026-09-26). One rollup row
#: replaces N raw rows: per (source, event_type, day) counts, error counts
#: and first/last timestamps. Honest aggregates only — no model, no
#: invention, so a rollup can never assert what no row said.
DISTILLED_EVENT = "memory_distilled"

#: Distilled rows outlive raw rows 4x. A year of daily rollups for a busy
#: type is ~365 small rows; the raw rows they replace would be 100k+.
DISTILLED_TTL_DAYS = 365

#: Never distill and never quota-trim: selection history (the GA's evidence)
#: and already-distilled rows (re-distilling would compound rounding).
#: Severity-filtered alert evidence is excluded from TYPE_CAPS by the same
#: rule; the quota backstop additionally spares error rows (triage reads).
PROTECTED_EVENTS = ("evolution_result", "evolution_promotion",
                    DISTILLED_EVENT)


# Maintenance seam (2026-09-28): when set, every step that resolves its store
# through _get_db_path() uses this file instead of the import-time constant, so
# a caller can point a whole maintenance run at one store (tests, Docker split).
_DB_OVERRIDE: str | None = None


def _get_db_path() -> str:
    if _DB_OVERRIDE:
        return _DB_OVERRIDE
    from CORE.direct_memory import DB_PATH
    return str(DB_PATH)


def _db_size_mb(db_path: str) -> float:
    try:
        return os.path.getsize(db_path) / 1e6 if os.path.exists(db_path) else 0.0
    except OSError as exc:                        # noqa: BLE001 — unreadable = unknown
        _note_failure("data_retention:db_size_unknown", exc)
        return 0.0


def _chunks(items, n=500):
    """Yield successive n-sized slices. SQLite caps bound variables per
    statement (999 on older builds), so any id-list query must chunk —
    measured 2026-09-27: a 190k-row victim set blew the limit."""
    items = list(items)
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _resolve_db(db_path: str | None) -> str:
    return db_path or _get_db_path()


def distill_aged_memories(ttl_days: int = EVENT_TTL_DAYS,
                          db_path: str | None = None) -> dict:
    """Summarise TTL-bound rows into rollups BEFORE deletion removes them.

    One ``memory_distilled`` row per (source, event_type, day): counts,
    error counts, first/last timestamps, mean duration where the payload
    carries one. Honest aggregates only — counts and extrema of rows that
    exist, never model output. Distills the same victim set the TTL pass
    would delete (minus protected types), so calling this first makes
    deletion lossless at rollup granularity. Does not delete anything.
    """
    db = _resolve_db(db_path)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
    conn = sqlite3.connect(db, timeout=30)
    try:
        rows = conn.execute(
            "SELECT id, source, event_type, severity, timestamp, data "
            "FROM memories WHERE timestamp < ? AND event_type NOT IN ({})"
            .format(",".join("?" * len(PROTECTED_EVENTS))),
            (cutoff, *PROTECTED_EVENTS),
        ).fetchall()
    except Exception as exc:
        conn.close()
        _note_failure("data_retention:fetch_expired_rows", exc)
        raise
    groups: dict = {}
    for _id, source, event_type, severity, ts, data in rows:
        try:
            day = str(ts)[:10]
        except Exception as exc:                  # noqa: BLE001 — unusable row skipped
            _note_failure("data_retention:distill_day", exc)
            continue
        key = (str(source), str(event_type), day)
        g = groups.setdefault(key, {"n": 0, "errors": 0, "first": ts,
                                    "last": ts, "dur_sum": 0.0, "dur_n": 0})
        g["n"] += 1
        if str(severity).lower() == "error":
            g["errors"] += 1
        try:
            if ts < g["first"]:
                g["first"] = ts
            if ts > g["last"]:
                g["last"] = ts
        except Exception as exc:                  # noqa: BLE001 — bounds stay as-is
            _note_failure("data_retention:distill_bounds", exc)
        try:
            import json as _json
            payload = _json.loads(data) if isinstance(data, str) else {}
            dur = payload.get("duration_ms", payload.get("avg_reward"))
            if isinstance(dur, (int, float)):
                g["dur_sum"] += float(dur)
                g["dur_n"] += 1
        except Exception as exc:                  # noqa: BLE001 — mean simply omitted
            _note_failure("data_retention:distill_duration", exc)
    now = datetime.now(timezone.utc).isoformat()
    wrote = 0
    for (source, event_type, day), g in groups.items():
        rollup = {"distilled": {"source": source, "event_type": event_type,
                                "day": day},
                  "count": g["n"], "errors": g["errors"],
                  "first_ts": g["first"], "last_ts": g["last"]}
        if g["dur_n"]:
            rollup["mean_value"] = round(g["dur_sum"] / g["dur_n"], 4)
            rollup["value_n"] = g["dur_n"]
        try:
            conn.execute(
                "INSERT INTO memories (session_id, source, event_type, data,"
                " severity, tags, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("retention", "retention", DISTILLED_EVENT,
                 json.dumps(rollup, ensure_ascii=False, default=str),
                 "info", '["retention", "distilled"]', now),
            )
            wrote += 1
        except Exception as exc:                  # noqa: BLE001 — one rollup never stops the pass
            _note_failure("data_retention:distill_insert", exc)
            continue
    conn.commit()
    conn.close()
    return {"groups": len(groups),
            "rows_summarized": sum(g["n"] for g in groups.values()),
            "rollups": wrote, "cutoff": cutoff}


def distill_cap_victims(caps: dict | None = None,
                        db_path: str | None = None) -> dict:
    """Roll up rows that enforce_type_caps is about to delete.

    Caps delete everything older than the newest N per type with no
    summarization — the 135k-row worm-tap trim would vaporize history no
    rollup ever saw (TTL only covers aged rows; cap victims can be young).
    Same rollup contract as distill_aged_memories, scoped to beyond-keep-N
    rows of capped types. Protected types are never capped, hence never
    victims here either.
    """
    caps = caps if caps is not None else TYPE_CAPS
    db = _resolve_db(db_path)
    conn = sqlite3.connect(db, timeout=30)
    total = 0
    groups = 0
    try:
        for event_type, keep in caps.items():
            if event_type in PROTECTED_EVENTS:
                continue  # defense in depth: selection history and existing
                # rollups are never cap victims, no matter what caps say.
            # No id-list: beyond-keep-N expressed as a NOT IN subquery, so
            # 190k-row victim sets never hit the per-statement variable cap
            # (measured 2026-09-27).
            vic = conn.execute(
                "SELECT source, event_type, substr(timestamp, 1, 10), "
                "COUNT(*), SUM(CASE WHEN severity = 'error' THEN 1 ELSE 0 END),"
                " MIN(timestamp), MAX(timestamp) FROM memories "
                "WHERE event_type = ? AND id NOT IN (SELECT id FROM memories "
                "WHERE event_type = ? ORDER BY id DESC LIMIT ?) "
                "GROUP BY 1, 2, 3",
                (event_type, event_type, keep)).fetchall()
            now = datetime.now(timezone.utc).isoformat()
            for source, ev, day, n, errs, first, last in vic:
                conn.execute(
                    "INSERT INTO memories (session_id, source, event_type,"
                    " data, severity, tags, timestamp) VALUES (?, ?, ?, ?,"
                    " ?, ?, ?)",
                    ("retention", "retention", DISTILLED_EVENT,
                     json.dumps({"distilled": {"source": str(source),
                                               "event_type": str(ev),
                                               "day": str(day)},
                                 "count": n, "errors": errs or 0,
                                 "first_ts": first, "last_ts": last,
                                 "via": "cap_distill"},
                                ensure_ascii=False, default=str),
                     "info", '["retention", "distilled"]', now))
                groups += 1
                total += n
        conn.commit()
    finally:
        conn.close()
    return {"groups": groups, "rows_summarized": total}


# EP478 (ECOKV composite rule): cap on redundancy-ranked rows per quota
# batch. Embeddings cost a local model call each; the ranking window stays
# small while the batch fill below preserves eviction throughput.
_COMPOSITE_RANK_CAP = 128


def _composite_victims(conn: sqlite3.Connection, victim_type: str,
                       batch: int) -> tuple[list, dict]:
    """Pick victims by redundancy (EP478 composite rule).

    Candidate pool is the oldest 2x batch; up to _COMPOSITE_RANK_CAP of them
    are embedded and the most-redundant (max cosine to the other candidates)
    are evicted first, filled to `batch` with the oldest unranked remainder
    (throughput preserved, unique old rows survive the pass). Same batch
    SIZE as legacy, different SET: redundancy-preferred, not merely old.
    Any failure raises to the caller, which falls back to legacy.
    """
    if _embed_text is None or _embed_cosine is None:
        raise RuntimeError("embedder-unavailable")
    pool = [r[0] for r in conn.execute(
        "SELECT id FROM memories WHERE event_type = ? "
        "AND severity NOT IN ('error', 'alert', 'critical') "
        "ORDER BY id ASC LIMIT ?", (victim_type, batch * 2))]
    if len(pool) < 2:
        return pool[:batch], {"rule": "legacy-oldest-first", "ranked": 0}
    placeholders = ",".join("?" * len(pool))
    rows = conn.execute(
        "SELECT id, data FROM memories WHERE id IN ({}) "
        "ORDER BY id ASC".format(placeholders),
        (*pool,)).fetchall()
    vecs: dict = {}
    for rid, data in rows[:_COMPOSITE_RANK_CAP]:
        try:
            v = _embed_text(str(data or ""))
        except Exception as exc:
            # An embedding that fails silently makes the row look
            # non-redundant, so it survives a retention pass it should have
            # lost. Count the skip.
            note_failure("data_retention:rank-embed", exc)
            continue
        if v:
            vecs[rid] = v
    if len(vecs) < 2:
        return pool[:batch], {"rule": "legacy-oldest-first", "ranked": 0}
    red: dict = {}
    ids = list(vecs)
    for i, a in enumerate(ids):
        best = 0.0
        for j, b in enumerate(ids):
            if i == j:
                continue
            try:
                s = _embed_cosine(vecs[a], vecs[b])
            except Exception as exc:
                note_failure("data_retention:rank-cosine", exc)
                continue
            if s > best:
                best = s
        red[a] = best
    ranked = sorted(ids, key=lambda r: red.get(r, 0.0), reverse=True)
    ranked_set = set(ranked)
    victims = ranked + [r for r in pool if r not in ranked_set]
    return victims[:batch], {"rule": "composite-redundancy",
                              "ranked": len(ranked)}


def _composite_eviction_enabled() -> bool:
    """`retention.composite_eviction` through the shared surface (default False).

    A deletion pass that switches victim SELECTION on is a decision about which
    rows are lost, so it is opt-in and named here rather than inferred from
    whether an embedding model happens to be reachable. A missing reader or an
    unreadable config degrades to the shipped default (legacy, size+age).

    This used to hand-parse `config/defaults.json` with json.loads, which made
    the knob invisible to `tools/settings_shadow.py`: a bypass reader's key is
    not in the audit's read map, so it cannot be checked for an orphan, a
    fallback drift, or the environment overlay that now decides it. Reading
    through `read_default` is what puts this switch on the same surface as every
    other knob (HED-5, measured 2026-11-05).
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
        from defaults_profile import read_default        # noqa: PLC0415
    except Exception as exc:
        _note_failure("data_retention:composite-default", exc)
        return False
    try:
        return bool(read_default("retention.composite_eviction", False))
    except Exception as exc:
        _note_failure("data_retention:composite-default", exc)
        return False


def enforce_global_quota(quota_mb: int | None = None,
                         db_path: str | None = None,
                         max_batches: int = 10,
                         batch: int = 5000) -> dict:
    """Backstop: keep total file size under quota. Distill-then-delete.

    TYPE_CAPS bound listed types and TTL bounds age, but neither bounds the
    TOTAL: uncapped types at high write rates overflowed the quota with every
    cap satisfied (09-20: 984MB at 184%). This deletes oldest-first from the
    largest unprotected types until under quota — distilling each batch into
    rollups first, sparing protected events and error rows, at most
    ``max_batches`` batches per run so one pass cannot cliff the store.
    Reports honestly when eligible rows run out before the quota is met.

    ``quota_mb`` defaults to the live ``DB_SIZE_QUOTA_MB`` resolved at CALL time
    (not def time), so a test can lower the module constant and have it honoured.
    """
    quota_mb = DB_SIZE_QUOTA_MB if quota_mb is None else quota_mb
    db = _resolve_db(db_path)
    freed_est = 0
    batches = 0
    trimmed: dict = {}
    rules: dict = {"legacy": 0, "composite": 0}
    while _db_size_mb(db) - freed_est / 1e6 > quota_mb and batches < max_batches:
        conn = sqlite3.connect(db, timeout=30)
        try:
            row = conn.execute(
                "SELECT event_type, SUM(LENGTH(data)) FROM memories "
                "WHERE event_type NOT IN ({}) AND severity NOT IN "
                "('error', 'alert', 'critical') "
                "GROUP BY event_type ORDER BY 2 DESC LIMIT 1".format(
                    ",".join("?" * len(PROTECTED_EVENTS))),
                (*PROTECTED_EVENTS,),
            ).fetchone()
            if not row:
                conn.close()
                break
            victim_type = row[0]
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM memories WHERE event_type = ? "
                "AND severity NOT IN ('error', 'alert', 'critical') "
                "ORDER BY id ASC LIMIT ?", (victim_type, batch))]
            if not ids:
                conn.close()
                break
            # EP478: consult the composite preview; when the embedder is
            # available, victims are picked by REDUNDANCY (most cosine-
            # similar first) instead of mere age — unique old rows survive
            # the pass. Same batch size (throughput preserved). Any failure
            # keeps the legacy oldest-first set above: the backstop must
            # never depend on the model server.
            #
            # OFF by default: this changes WHICH ROWS GET DELETED, so it is
            # a knob (`retention.composite_eviction`), not a silent swap.
            # `tests/test_enforce_global_quota_victim_rule.py` pins the
            # shipped rule — largest unprotected type by BYTES, lowest id
            # first — and this branch broke those pins the day it landed
            # unconditionally.
            _preview = {}
            if _composite_eviction_enabled():
                try:
                    if _STORE_OK and callable(_get_memory_store):
                        _store = _get_memory_store()
                    else:
                        _store = None
                    _preview = (_store.composite_eviction_preview(
                        "quota:%s" % victim_type)
                        if _store is not None else {})
                except Exception as exc:
                    _note_failure("data_retention:quota-preview", exc)
                    _preview = {}
            if _preview.get("embedding_available"):
                try:
                    ids, _info = _composite_victims(
                        conn, victim_type, batch)
                    rules["composite"] += 1
                except Exception as exc:
                    _note_failure("data_retention:quota-composite", exc)
                    rules["legacy"] += 1
            else:
                rules["legacy"] += 1
            if not ids:
                conn.close()
                break
            freed_est += sum(
                conn.execute(
                    "SELECT SUM(LENGTH(data)) FROM memories WHERE id IN ({})"
                    .format(",".join("?" * len(chunk))), (*chunk,)
                ).fetchone()[0] or 0
                for chunk in _chunks(ids))
            conn.close()
        except Exception as e:
            try:
                conn.close()
            except Exception as close_exc:        # noqa: BLE001 — already failing
                _note_failure("data_retention:quota_close", close_exc)
            return {"enforced": False, "error": str(e)[:200],
                    "freed_est_mb": round(freed_est / 1e6, 2)}
        # Distill this batch's span before deleting (same rollup contract:
        # per source/event/day counts + extrema, honest aggregates only).
        conn = sqlite3.connect(db, timeout=30)
        try:
            vic_rows = []
            for chunk in _chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                vic_rows.extend(conn.execute(
                    "SELECT source, event_type, substr(timestamp, 1, 10), "
                    "COUNT(*), SUM(CASE WHEN severity = 'error' THEN 1 ELSE 0 END),"
                    " MIN(timestamp), MAX(timestamp) FROM memories "
                    "WHERE id IN ({}) GROUP BY 1, 2, 3".format(placeholders),
                    (*chunk,)).fetchall())
            now = datetime.now(timezone.utc).isoformat()
            for source, event_type, day, n, errs, first, last in vic_rows:
                conn.execute(
                    "INSERT INTO memories (session_id, source, event_type,"
                    " data, severity, tags, timestamp) VALUES (?, ?, ?, ?,"
                    " ?, ?, ?)",
                    ("retention", "retention", DISTILLED_EVENT,
                     json.dumps({"distilled": {"source": str(source),
                                               "event_type": str(event_type),
                                               "day": str(day)},
                                 "count": n, "errors": errs or 0,
                                 "first_ts": first, "last_ts": last,
                                 "via": "quota_backstop"},
                                ensure_ascii=False, default=str),
                     "info", '["retention", "distilled"]', now))
            for chunk in _chunks(ids):
                conn.execute("DELETE FROM memories WHERE id IN ({})"
                             .format(",".join("?" * len(chunk))), (*chunk,))
            conn.commit()
            trimmed[victim_type] = trimmed.get(victim_type, 0) + len(ids)
            batches += 1
        finally:
            conn.close()
    size = _db_size_mb(db)
    return {"enforced": size <= quota_mb, "size_mb": round(size, 1),
            "quota_mb": quota_mb, "batches": batches,
            "trimmed": trimmed,
            "eviction_rules": rules,
            "freed_est_mb": round(freed_est / 1e6, 2)}


def enforce_event_ttl(ttl_days: int = EVENT_TTL_DAYS,
                      db_path: str | None = None) -> dict:
    """Delete events older than ttl_days. Returns count deleted.

    Distilled rollups carry a longer horizon (DISTILLED_TTL_DAYS): they are
    the compressed form of deleted history, so aging them out at the raw
    90-day horizon would defeat the distillation. Selection history
    (PROTECTED_EVENTS) is spared entirely, matching the TYPE_CAPS rule
    above: the GA's evidence must never be aged out from under it.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
    distilled_cutoff = (datetime.now(timezone.utc)
                        - timedelta(days=DISTILLED_TTL_DAYS)).isoformat()
    # Distilled rows are protected members with their own horizon, so they
    # need the age clause, not the blanket exemption, or rollups would
    # accumulate forever.
    hard_spared = tuple(e for e in PROTECTED_EVENTS if e != DISTILLED_EVENT)
    conn = sqlite3.connect(_resolve_db(db_path), timeout=30)
    try:
        cursor = conn.execute(
            "DELETE FROM memories WHERE timestamp < ? "
            "AND event_type NOT IN ({}) AND NOT "
            "(event_type = ? AND timestamp >= ?)".format(
                ",".join("?" * len(hard_spared)) or "''"),
            (cutoff, *hard_spared, DISTILLED_EVENT, distilled_cutoff),
        )
        deleted = cursor.rowcount
        conn.commit()
        # Also delete orphaned sessions
        try:
            conn.execute(
                "DELETE FROM sessions WHERE session_id NOT IN "
                "(SELECT DISTINCT session_id FROM memories)"
            )
            conn.commit()
        except Exception as sess_exc:  # noqa: BLE001
            _note_failure("data_retention:enforce_event_ttl:sessions", sess_exc)
        return {"events_deleted": deleted, "cutoff": cutoff,
                "enforced": True}
    except Exception as e:  # noqa: BLE001 — fail-open with citation
        _note_failure("data_retention:enforce_event_ttl", e)
        try:
            conn.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            _note_failure("data_retention:enforce_event_ttl:rollback", rb_exc)
        return {"events_deleted": 0, "cutoff": cutoff,
                "enforced": False, "error": str(e)[:200]}
    finally:
        try:
            conn.close()
        except Exception as close_exc:  # noqa: BLE001
            _note_failure("data_retention:enforce_event_ttl:close", close_exc)


def enforce_type_caps(caps: dict | None = None,
                      db_path: str | None = None) -> dict:
    """Keep-last-N trim per high-volume bookkeeping event_type.

    Deletes rows older than the newest N of each capped type. Runs in one
    DELETE per type (indexed on event_type); the nightly slot owns the
    lock window and cycles fail soft on contention. Returns per-type
    deleted counts. Dry-run: pass caps={} (no-op) or compute counts with
    the SELECT in cmd_maintain before enabling.
    """
    caps = caps if caps is not None else TYPE_CAPS
    conn = sqlite3.connect(_resolve_db(db_path), timeout=30)
    deleted: dict = {}
    try:
        for event_type, keep in caps.items():
            cur = conn.execute(
                "DELETE FROM memories WHERE event_type = ? AND id NOT IN "
                "(SELECT id FROM memories WHERE event_type = ? "
                "ORDER BY id DESC LIMIT ?)",
                (event_type, event_type, keep),
            )
            deleted[event_type] = cur.rowcount
        conn.commit()
        return {"deleted": deleted, "total": sum(deleted.values()),
                "enforced": True}
    except Exception as e:  # noqa: BLE001 — fail-open with citation
        _note_failure("data_retention:enforce_type_caps", e)
        try:
            conn.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            _note_failure("data_retention:enforce_type_caps:rollback", rb_exc)
        return {"deleted": deleted, "total": sum(deleted.values()),
                "enforced": False, "error": str(e)[:200]}
    finally:
        try:
            conn.close()
        except Exception as close_exc:  # noqa: BLE001
            _note_failure("data_retention:enforce_type_caps:close", close_exc)


def enforce_span_ttl(ttl_days: int = SPAN_TTL_DAYS,
                     db_path: str | None = None) -> dict:
    """Delete OTel spans older than ttl_days. Returns count deleted.

    `otel_spans` is not a `memories` event_type, so the type-cap mechanism
    above cannot reach it — without this it grew unbounded (112k rows).
    start_time is a nanosecond epoch, so the cutoff is scaled to match.
    2026-09-28 split: spans live in telemetry_store.db; default follows.
    """
    from CORE.direct_memory import _get_telemetry_db_path
    db = db_path or str(_get_telemetry_db_path())
    cutoff_ns = int(
        (datetime.now(timezone.utc) - timedelta(days=ttl_days)).timestamp()
        * 1_000_000_000
    )
    conn = sqlite3.connect(db, timeout=30)
    try:
        cursor = conn.execute(
            "DELETE FROM otel_spans WHERE start_time < ?", (cutoff_ns,)
        )
        deleted = cursor.rowcount
        conn.commit()
        return {"spans_deleted": deleted, "ttl_days": ttl_days,
                "enforced": True}
    except Exception as e:  # noqa: BLE001 — fail-open with citation
        _note_failure("data_retention:enforce_span_ttl", e)
        try:
            conn.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            _note_failure("data_retention:enforce_span_ttl:rollback", rb_exc)
        return {"spans_deleted": 0, "ttl_days": ttl_days,
                "enforced": False, "error": str(e)[:200]}
    finally:
        try:
            conn.close()
        except Exception as close_exc:  # noqa: BLE001
            _note_failure("data_retention:enforce_span_ttl:close", close_exc)


def rotate_old_sessions(max_age_hours: int = SESSION_ROTATION_HOURS) -> int:
    """Auto-close sessions older than max_age_hours. Returns count closed."""
    try:
        from CORE.direct_memory import DirectMemory
        return DirectMemory.close_stale_sessions(max_age_hours=max_age_hours)
    except Exception as e:  # noqa: BLE001 — fail-open with citation
        _note_failure("data_retention:rotate_old_sessions", e)
        return 0


def check_db_size_quota(quota_mb: int = DB_SIZE_QUOTA_MB) -> dict:
    """Check DB size against quota. Returns warning if exceeded."""
    try:
        from CORE.doctor import get_db_size
        size = get_db_size()
        total_mb = size.get("total_mb", 0)
        return {
            "size_mb": total_mb,
            "quota_mb": quota_mb,
            "exceeded": total_mb > quota_mb,
            "pct": round(total_mb / quota_mb * 100, 1) if quota_mb > 0 else 0,
            "enforced": True,
        }
    except Exception as e:  # noqa: BLE001 — fail-open with citation
        _note_failure("data_retention:check_db_size_quota", e)
        return {
            "size_mb": 0,
            "quota_mb": quota_mb,
            "exceeded": False,
            "pct": 0,
            "enforced": False,
            "error": str(e)[:200],
        }


def archive_old_audit_files(max_age_days: int = AUDIT_ARCHIVE_DAYS) -> list:
    """Gzip-compress .audit/ files older than max_age_days.
    
    Returns list of (filepath, archive_path) for archived files.
    Renames original to .jsonl.gz after archiving.
    """
    from CORE.audit_trail import AUDIT_DIR
    archived = []
    if not AUDIT_DIR.exists():
        return archived

    cutoff = datetime.now() - timedelta(days=max_age_days)
    for fp in sorted(AUDIT_DIR.glob("session_*.jsonl")):
        try:
            mtime = datetime.fromtimestamp(fp.stat().st_mtime)
            if mtime < cutoff:
                # Read and compress
                data = fp.read_bytes()
                gz_path = fp.with_suffix(".jsonl.gz")
                with gzip.open(gz_path, "wb", compresslevel=6) as gz:
                    gz.write(data)
                # Remove original
                fp.unlink()
                archived.append((fp.name, gz_path.name))
        except Exception as exc:                  # noqa: BLE001 — one file never stops the sweep
            _note_failure("data_retention:audit_archive", exc)
            continue
    return archived


def vacuum_if_needed(db_path: str | None = None) -> dict:
    """Run VACUUM on both stores if fragmentation exceeds threshold."""
    from CORE.direct_memory import _get_telemetry_db_path
    targets = [db_path] if db_path else [_get_db_path(), str(_get_telemetry_db_path())]
    out: dict = {"vacuumed": False, "files": {}}
    for target in targets:
        try:
            conn = sqlite3.connect(target, timeout=10)
        except Exception as exc:                  # noqa: BLE001 — target skipped
            _note_failure("data_retention:vacuum_connect", exc)
            continue
        try:
            freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
            page_count = conn.execute("PRAGMA page_count").fetchone()[0]
            if page_count > 0:
                free_pct = freelist / page_count * 100
                if free_pct > VACUUM_THRESHOLD_PCT:
                    conn.execute("VACUUM")
                    out["vacuumed"] = True
                    out["files"][str(target)] = {"vacuumed": True,
                                                 "free_pct_before": round(free_pct, 1)}
                    continue
            out["files"][str(target)] = {
                "vacuumed": False,
                "free_pct": round(freelist / max(page_count, 1) * 100, 1) if page_count > 0 else 0}
        except Exception as exc:  # noqa: BLE001 — fail-open with citation
            _note_failure("data_retention:vacuum", exc)
            out["files"][str(target)] = {"error": str(exc)[:200]}
        finally:
            try:
                conn.close()
            except Exception as close_exc:  # noqa: BLE001
                _note_failure("data_retention:vacuum:close", close_exc)
    return out


def wal_checkpoint(db_path: str | None = None) -> dict:
    """Run WAL checkpoint on both stores to keep WAL files bounded."""
    from CORE.direct_memory import _get_telemetry_db_path
    targets = [db_path] if db_path else [_get_db_path(), str(_get_telemetry_db_path())]
    out: dict = {"freed_bytes": 0, "files": {}}
    for target in targets:
        try:
            conn = sqlite3.connect(target, timeout=30)
        except Exception as exc:                  # noqa: BLE001 — target skipped
            _note_failure("data_retention:checkpoint_connect", exc)
            continue
        try:
            wal = Path(target).with_suffix(".db-wal")
            before = wal.stat().st_size if wal.exists() else 0
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            after = wal.stat().st_size if wal.exists() else 0
            out["files"][str(target)] = {"before_bytes": before,
                                         "after_bytes": after,
                                         "freed_bytes": before - after}
            out["freed_bytes"] += before - after
        except Exception as exc:  # noqa: BLE001 — fail-open with citation
            _note_failure("data_retention:wal_checkpoint", exc)
            out["files"][str(target)] = {"error": str(exc)[:200]}
        finally:
            try:
                conn.close()
            except Exception as close_exc:  # noqa: BLE001
                _note_failure("data_retention:wal_checkpoint:close", close_exc)
    return out


def _logs_dir() -> Path:
    return Path(_get_db_path()).resolve().parent.parent / "azazael" / "logs"


def _state_dir() -> Path:
    return Path(_get_db_path()).resolve().parent.parent / "azazael" / "state"


def _backup_dir() -> Path:
    d = Path(_get_db_path()).resolve().parent.parent / "azazael" / "backup" / "db"
    d.mkdir(parents=True, exist_ok=True)
    return d


def newest_backup() -> dict:
    """Newest local backup + age (for the monitor STORE line)."""
    try:
        cands = sorted(_backup_dir().glob("memory_store.*.db.gz"))
        if not cands:
            return {"exists": False}
        latest = cands[-1]
        import time as _time
        return {"exists": True, "name": latest.name,
                "mb": round(latest.stat().st_size / 1e6, 1),
                "age_h": round((_time.time() - latest.stat().st_mtime) / 3600.0, 1)}
    except Exception as e:
        note_failure("data_retention:L639", e)
        return {"exists": False, "error": str(e)[:800]}


def _maybe_full_drill(gz: Path, max_age_days: int = 30) -> dict:
    """Gunzip newest backup to temp + integrity_check, at most monthly."""
    import gzip as _gzip
    import tempfile as _tempfile
    import time as _time
    state = _backup_dir() / "last_drill.json"
    try:
        if state.exists():
            last = json.loads(state.read_text(encoding="utf-8"))
            if _time.time() - float(last.get("at", 0)) < max_age_days * 86400:
                return {"status": "deferred", "last": last.get("at_str", "?")}
    except Exception as exc:                      # noqa: BLE001 — unreadable state runs the drill
        _note_failure("data_retention:drill_state_read", exc)
    tmp = Path(_tempfile.mkdtemp(prefix="dbdrill_")) / "drill.db"
    try:
        with _gzip.open(gz, "rb") as f_in, open(tmp, "wb") as f_out:
            _shutil.copyfileobj(f_in, f_out, length=4 * 1024 * 1024)
        vconn = sqlite3.connect(str(tmp), timeout=60)
        try:
            ok = vconn.execute("PRAGMA integrity_check").fetchone()[0]
            n = vconn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        finally:
            vconn.close()
        result = {"status": "pass" if str(ok).lower() == "ok" else "FAIL",
                  "integrity": str(ok)[:60], "rows": n,
                  "at_str": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
        # Mirror parity (2026-09-10): the G: copy rots independently —
        # CRC-read it too so the drill covers both restore paths.
        try:
            _mirror = Path(r"G:\source-of-truth\azazael\db-backup") / gz.name
            if _mirror.exists():
                with _gzip.open(_mirror, "rb") as f:
                    while f.read(4 * 1024 * 1024):
                        pass
                result["mirror_crc"] = True
            else:
                result["mirror_crc"] = "mirror missing"
        except Exception as e:
            note_failure("data_retention:L681", e)
            result["mirror_crc"] = "CRC FAIL: %s" % str(e)[:800]
            result["status"] = "FAIL"
        state.write_text(json.dumps(
            {"at": _time.time(), "at_str": result["at_str"],
             "result": result["status"]}), encoding="utf-8")
        return result
    finally:
        try:
            _shutil.rmtree(tmp.parent, ignore_errors=True)
        except Exception as exc:                  # noqa: BLE001 — temp dir left behind
            _note_failure("data_retention:drill_cleanup", exc)


def _backup_one(src, stem: str, keep: int, stamp: str) -> dict:
    """Online backup of one SQLite file: backup API, integrity-checked
    copy, gzip, prune, G: mirror, CRC + monthly full drill. Fail-open."""
    import gzip as _gzip
    raw = _backup_dir() / ("%s.%s.db" % (stem, stamp))
    try:
        sconn = sqlite3.connect(src, timeout=30)
        try:
            dconn = sqlite3.connect(str(raw), timeout=30)
            try:
                sconn.backup(dconn)
            finally:
                dconn.close()
        finally:
            sconn.close()
        vconn = sqlite3.connect(str(raw), timeout=30)
        try:
            integrity = vconn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            vconn.close()
        if str(integrity).lower() != "ok":
            raw.unlink(missing_ok=True)
            return {"error": "backup integrity check failed: %s" % str(integrity)[:120]}
        gz = raw.with_suffix(".db.gz")
        with open(raw, "rb") as f_in, _gzip.open(gz, "wb") as f_out:
            _shutil.copyfileobj(f_in, f_out, length=4 * 1024 * 1024)
        raw.unlink()
        pruned = []
        existing = sorted(_backup_dir().glob("%s.*.db.gz" % stem))
        while len(existing) > keep:
            old = existing.pop(0)
            try:
                old.unlink()
                pruned.append(old.name)
            except OSError as exc:                # noqa: BLE001 — stop pruning, keep the rest
                _note_failure("data_retention:prune_local", exc)
                break
        out = {"path": str(gz), "gz_bytes": gz.stat().st_size,
               "integrity": "ok", "pruned": pruned}
        # Mirror to Source of Truth (fail-open).
        try:
            _mirror = Path(r"G:\source-of-truth\azazael\db-backup")
            _mirror.mkdir(parents=True, exist_ok=True)
            _dest = _mirror / gz.name
            _shutil.copyfile(str(gz), str(_dest))
            _mexisting = sorted(_mirror.glob("%s.*.db.gz" % stem))
            while len(_mexisting) > keep:
                _old = _mexisting.pop(0)
                try:
                    _old.unlink()
                except OSError as exc:            # noqa: BLE001 — stop mirror-pruning
                    _note_failure("data_retention:prune_mirror", exc)
                    break
            out["mirror"] = str(_dest)
        except Exception as e:
            note_failure("data_retention:L750", e)
            out["mirror"] = "skipped: %s" % str(e)[:800]
        # Restore drill (light): re-read the gz (CRC check) so a
        # bit-rotted archive is caught here, not at 3am during an
        # actual incident.
        try:
            with _gzip.open(gz, "rb") as f:
                _n = 0
                while f.read(4 * 1024 * 1024):
                    _n += 1
            out["verified"] = True
        except Exception as e:
            note_failure("data_retention:L761", e)
            out["verified"] = "CRC FAIL: %s" % str(e)[:800]
        # Full drill, monthly (2026-09-10): gunzip to temp + real
        # integrity_check proves the archive actually restores, not just
        # CRC-passes. Gated by last-drill state so nightly stays fast.
        try:
            out["full_drill"] = _maybe_full_drill(gz)
        except Exception as e:
            note_failure("data_retention:L768", e)
            out["full_drill"] = "drill error: %s" % str(e)[:800]
        return out
    except Exception as e:
        try:
            if raw.exists():
                raw.unlink()
        except OSError as cleanup_exc:            # noqa: BLE001 — already returning the error
            _note_failure("data_retention:backup_cleanup", cleanup_exc)
        return {"error": str(e)[:200]}


def backup_database(keep: int = 3) -> dict:
    """Nightly online backup of both stores (2026-09-28 split).

    memory_store.db (memories, FTS) + telemetry_store.db (spans, bus
    queue) via the online backup API (safe against concurrent
    readers/writers), each verified, gzipped, pruned, and G:-mirrored.
    Fail-open per file: one store's failure never blocks the other's.
    Returns {"memory_store": {...}, "telemetry_store": {...}}.
    """
    from CORE.direct_memory import _get_telemetry_db_path
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out: dict = {}
    try:
        out["memory_store"] = _backup_one(_get_db_path(), "memory_store", keep, stamp)
    except Exception as e:
        note_failure("data_retention:L794", e)
        out["memory_store"] = {"error": str(e)[:200]}
    try:
        out["telemetry_store"] = _backup_one(
            str(_get_telemetry_db_path()), "telemetry_store", keep, stamp)
    except Exception as e:
        note_failure("data_retention:L799", e)
        out["telemetry_store"] = {"error": str(e)[:200]}
    return out


def rotate_logs(max_mb: int = LOG_ROTATE_MB, keep: int = LOG_ROTATE_KEEP,
                log_dir: (Path | str | None) = None) -> dict:
    """Rotate logs/*.log past max_mb, keeping .1 .. .keep generations.

    (2026-09-09: evolution_cycle.log was growing unbounded — 0.6 MB and
    climbing 5 KB per cycle with no rotation anywhere. The nightly
    maintenance task runs this, so no log can grow more than ~1 day past
    the cap between runs.)

    Shift-up scheme: .keep is dropped, .N -> .N+1, live .log -> .1.
    A concurrently-written .log is never moved mid-line — writers here
    open append-per-line, and rotation runs from the nightly task when
    cycles aren't mid-write... in practice a torn last line is possible
    and acceptable for logs (never for state/DB, which use tmp+replace).
    Returns {rotated: [...], skipped: [...]}.
    """
    d = Path(log_dir) if log_dir else _logs_dir()
    rotated, skipped = [], []
    if not d.exists():
        return {"rotated": rotated, "skipped": skipped, "log_dir": str(d)}
    cap = max_mb * 1024 * 1024
    for logf in sorted(d.glob("*.log")):
        try:
            if logf.stat().st_size < cap:
                skipped.append(logf.name)
                continue
            oldest = logf.with_name(logf.name + ".%d" % keep)
            if oldest.exists():
                oldest.unlink()
            for n in range(keep - 1, 0, -1):
                src = logf.with_name(logf.name + ".%d" % n)
                if src.exists():
                    src.replace(logf.with_name(logf.name + ".%d" % (n + 1)))
            logf.replace(logf.with_name(logf.name + ".1"))
            rotated.append(logf.name)
        except Exception as e:  # noqa: BLE001 — one bad log never blocks the rest
            note_failure("data_retention:L839", e)
            skipped.append("%s (%s)" % (logf.name, str(e)[:80]))
    return {"rotated": rotated, "skipped": skipped, "log_dir": str(d)}


def run_maintenance(light: bool = False,
                    db_path: str | None = None) -> dict:
    """Run all maintenance tasks, optionally against an explicit store.

    ``db_path`` (test / Docker seam) routes every step that resolves its store
    through ``_get_db_path()`` at that file, restoring the previous value after
    the run. Steps that call ``get_memory_store()`` directly (memory_purge,
    error triage) still resolve via ``AZAZAEL_MEMORY_DIR``, so an isolated
    caller must set that env var too.
    """
    global _DB_OVERRIDE
    _prev_override = _DB_OVERRIDE
    if db_path:
        _DB_OVERRIDE = str(db_path)
    try:
        return _run_maintenance_impl(light)
    finally:
        _DB_OVERRIDE = _prev_override


def _run_maintenance_impl(light: bool = False) -> dict:
    """Run all maintenance tasks in sequence.

    Returns dict with results of each task.

    light=True skips the heavy writers (quota backstop batches, VACUUM):
    used when the chain fired recently and the DB lock is contested. TTL
    and type caps are small indexed deletes and always run.
    """
    results = {}
    # Backup FIRST (2026-09-10): the pre-maintenance snapshot is the
    # true restore point — post-prune state is reproducible, pre-prune
    # deletions are not. Destructive ops (TTL, caps, vacuum) follow.
    try:
        results["db_backup"] = backup_database()
    except Exception as e:
        note_failure("data_retention:L879", e)
        results["db_backup"] = {"error": str(e)}
    try:
        results["distilled"] = distill_aged_memories()
    except Exception as e:
        note_failure("data_retention:L883", e)
        results["distilled"] = {"error": str(e)}
    try:
        results["event_ttl"] = enforce_event_ttl()
    except Exception as e:
        note_failure("data_retention:L887", e)
        results["event_ttl"] = {"error": str(e)}
    try:
        results["session_rotation"] = {"closed": rotate_old_sessions()}
    except Exception as e:
        note_failure("data_retention:L891", e)
        results["session_rotation"] = {"error": str(e)}
    try:
        # Distill cap victims BEFORE the trim below deletes them: cap
        # victims can be young (TTL never saw them), so without this the
        # first bounded trim vaporizes history no rollup ever summarized.
        results["cap_distilled"] = distill_cap_victims()
    except Exception as e:
        note_failure("data_retention:L898", e)
        results["cap_distilled"] = {"error": str(e)}
    try:
        results["type_caps"] = enforce_type_caps()
    except Exception as e:
        note_failure("data_retention:L902", e)
        results["type_caps"] = {"error": str(e)}
    try:
        results["span_ttl"] = enforce_span_ttl()
    except Exception as e:
        note_failure("data_retention:L906", e)
        results["span_ttl"] = {"error": str(e)}
    try:
        # Backstop AFTER the targeted passes: caps/TTL bound listed types
        # and age, but neither bounds the total (09-20: 984MB with every
        # cap satisfied). Distill-then-delete oldest-first from the largest
        # unprotected types until under quota.
        if light:
            results["quota_backstop"] = {"skipped": "chain-active window"}
        else:
            results["quota_backstop"] = enforce_global_quota()
    except Exception as e:
        note_failure("data_retention:L917", e)
        results["quota_backstop"] = {"error": str(e)}
    try:
        # Memory-store retention (2026-09-27): the store's own size warning
        # (586MB against a 400MB threshold) had no scheduled remedy --
        # purge_old() is the documented one, and the pre-prune snapshot is
        # the backup taken first in this same run. Heavy pass only: the first
        # run can delete years of rows and must not race the chain's writers.
        if light:
            results["memory_purge"] = {"skipped": "chain-active window"}
        else:
            from CORE.local_memory import get_memory_store
            results["memory_purge"] = {
                "purged": get_memory_store().purge_old(days=90)}
    except Exception as e:
        note_failure("data_retention:L931", e)
        results["memory_purge"] = {"error": str(e)}
    try:
        results["db_quota"] = check_db_size_quota()
    except Exception as e:
        note_failure("data_retention:L935", e)
        results["db_quota"] = {"error": str(e)}
    try:
        results["audit_archive"] = {"archived": archive_old_audit_files()}
    except Exception as e:
        note_failure("data_retention:L939", e)
        results["audit_archive"] = {"error": str(e)}
    try:
        if light:
            results["vacuum"] = {"skipped": "chain-active window"}
        else:
            results["vacuum"] = vacuum_if_needed()
    except Exception as e:
        note_failure("data_retention:L946", e)
        results["vacuum"] = {"error": str(e)}
    try:
        results["wal_checkpoint"] = wal_checkpoint()
    except Exception as e:
        note_failure("data_retention:L950", e)
        results["wal_checkpoint"] = {"error": str(e)}
    try:
        results["log_rotation"] = rotate_logs()
    except Exception as e:
        note_failure("data_retention:L954", e)
        results["log_rotation"] = {"error": str(e)}
    try:
        # Error triage (2026-09-09): cluster the week's real errors for
        # the maintenance log. Summary only — no new store rows.
        from CORE.local_memory import get_memory_store
        tri = get_memory_store().triage_real_errors(days=7)
        # Persist for the live monitor (atomic; monitor reads + ages it).
        try:
            _td = _state_dir()
            _td.mkdir(parents=True, exist_ok=True)
            _tmp = _td / "error_triage.json.tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump(tri, f, ensure_ascii=False, default=str)
            _tmp.replace(_td / "error_triage.json")
        except Exception as e:  # noqa: BLE001 — triage summary still reported
            note_failure("data_retention:L969", e)
            tri["_persist_error"] = str(e)[:120]
        results["error_triage"] = {
            "errors_exact": tri.get("total_errors_exact", 0),
            "warnings_exact": tri.get("total_warnings_exact", 0),
            "clusters": tri.get("cluster_count", 0),
            "capped": tri.get("capped", False),
            "top": [{"source": c["source"], "severity": c["severity"],
                     "count": c["count"], "newest": c["newest"],
                     "signature": c["signature"][:120]}
                    for c in tri.get("clusters", [])[:5]],
        }
    except Exception as e:
        note_failure("data_retention:L981", e)
        results["error_triage"] = {"error": str(e)}
    return results


def cmd_maintain(db_path: str | None = None):
    """CLI command: run all maintenance tasks and print results.

    ``db_path`` routes the DB-touching steps at one store (test / Docker seam);
    see run_maintenance() for the get_memory_store() caveat.
    """
    results = run_maintenance(db_path=db_path)
    print("=== Maintenance Run ===")
    for task, result in results.items():
        status = "[OK]" if "error" not in result else "[FAIL]"
        print(f"  {status} {task}: {json.dumps(result, default=str)}")
    print("  Done.")


if __name__ == "__main__":
    cmd_maintain()
