"""promotion_signals.py - outside-world signals for the promotion decision.

Hive mesh health, RANI battery verdicts, repo audit state, and human
feedback, each reduced to a 0..1 multiplier. Design rules (speed first):

  - STATE FILES ONLY. No memory-store queries (the 400MB+ DB is the hot
    contention point), no subprocesses, no imports beyond stdlib.
    A full read costs single-digit milliseconds; safe to call every cycle.
  - FAIL-OPEN. Missing/stale/unparseable input returns 1.0 (neutral), never
    blocks. Only an explicit human DOWN veto blocks, and it says so loudly.
  - RECORDED. Every value ships with its source + age so rows, logs, and
    monitors can show WHY promotion got harder, not just that it did.

Usage:
    from promotion_signals import combined
    sig = combined(production_hash=None)   # cheapest useful call
    # sig = {"combined": 0.85, "veto": False,
    #        "parts": {"hive": {...}, "rani": {...}, "repo": {...}, "human": {...}}}
"""
import json
import os
import time
import hashlib

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_AZ_HOME = os.path.expanduser("~")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None  # scanner:ignore -- probe contract: a missing/corrupt state file is "no signal"; callers render None as the neutral part


def _age_h(path):
    try:
        return (time.time() - os.path.getmtime(path)) / 3600.0
    except Exception:
        return None  # scanner:ignore -- probe contract: an unstat-able path has no age; None is the designed missing-age marker


def hive_signal():
    """Mesh/hive operational health from the 1-minute cycle state."""
    out = {"value": 1.0, "source": "state/mesh_cycle.json", "note": "missing"}
    s = _read_json(os.path.join(_ROOT, "state", "mesh_cycle.json"))
    if not isinstance(s, dict):
        return out
    age_m = None
    try:
        age_m = (time.time() - os.path.getmtime(
            os.path.join(_ROOT, "state", "mesh_cycle.json"))) / 60.0
    except Exception:
        pass  # scanner:ignore -- best-effort freshness probe; age_m stays None and the signal reports its unset/missing path
    if age_m is not None and age_m > 10:
        return {"value": 1.0, "source": out["source"],
                "note": "stale (%.0fm), neutral" % age_m}
    fails = int(s.get("fail") or 0)
    drag = min(0.5, 0.1 * fails)
    return {"value": round(1.0 - drag, 3), "source": out["source"],
            "note": "fail=%d" % fails}


def rani_signal(max_age_h=30.0):
    """Newest RANI battery verdict. Adversarial pressure damps promotion."""
    cands = [os.path.join(_AZ_HOME, "RANI_LLM_evidence.json"),
             os.path.join(_AZ_HOME, "GOSP_RANI_INTEGRATION_evidence.json")]
    best, best_age = None, None
    for p in cands:
        d = _read_json(p)
        if not isinstance(d, dict):
            continue
        a = _age_h(p)
        if best is None or (a is not None and (best_age is None or a < best_age)):
            best, best_age = d, a
    if best is None:
        return {"value": 1.0, "source": "rani evidence", "note": "missing"}
    if best_age is not None and best_age > max_age_h:
        return {"value": 1.0, "source": "rani evidence",
                "note": "stale (%.1fh), neutral" % best_age}
    sm = best.get("engine_summary") or {}
    try:
        rounds = int(sm.get("total_rounds") or 0)
        adv = int(sm.get("adversarial_detected") or 0)
    except Exception:
        rounds, adv = 0, 0
    rate = (adv / rounds) if rounds else 0.0
    return {"value": round(1.0 - min(0.4, rate), 3),
            "source": best.get("backend") or "rani",
            "note": "adv=%d/%d age=%.1fh" % (adv, rounds, best_age or -1)}


def repo_signal(max_age_h=48.0):
    """Repo audit state. Compile breakage damps promotion."""
    p = os.path.join(_ROOT, "state", "audit_summary.json")
    d = _read_json(p)
    if not isinstance(d, dict):
        return {"value": 1.0, "source": "audit_summary", "note": "missing"}
    age = _age_h(p)
    if age is not None and age > max_age_h:
        return {"value": 1.0, "source": "audit_summary",
                "note": "stale (%.1fh), neutral" % age}
    try:
        bad = len(((d.get("compile") or {}).get("bad")) or [])
    except Exception:
        bad = 0
    return {"value": 0.7 if bad > 0 else 1.0, "source": "audit_summary",
            "note": "compile_bad=%d files=%s" % (bad, d.get("files_scanned"))}


FEEDBACK_PATH = os.path.join(_ROOT, "state", "promo_feedback.json")


def human_signal():
    """Latest human verdict. DOWN on current production is a VETO (blocks).

    Anything else (UP, missing file, unparsable) is neutral 1.0. The rater
    script (rate_promotion.py) stamps the production genes hash; a DOWN for
    older genes does not veto the current champion.
    """
    out = {"value": 1.0, "veto": False, "source": "promo_feedback",
           "note": "no feedback"}
    d = _read_json(FEEDBACK_PATH)
    items = d.get("items") if isinstance(d, dict) else None
    if not items:
        return out
    last = items[-1]
    prod = _read_json(os.path.join(_ROOT, "state", "evolved_rl_params.json")) or {}
    cur = json.dumps((prod.get("genes") or {}), sort_keys=True)
    cur_h = hashlib.sha256(cur.encode()).hexdigest()[:12]
    if (last.get("genes_hash") or "") != cur_h:
        return {"value": 1.0, "veto": False, "source": "promo_feedback",
                "note": "latest rates older genes, neutral"}
    if str(last.get("verdict", "")).lower().startswith("down"):
        return {"value": 0.0, "veto": True, "source": "promo_feedback",
                "note": "HUMAN VETO: %s" % str(last.get("note", ""))[:80]}
    return {"value": 1.0, "veto": False, "source": "promo_feedback",
            "note": "UP %s" % str(last.get("note", ""))[:60]}


def combined():
    """Product of parts + veto flag. Missing parts are neutral 1.0."""
    parts = {"hive": hive_signal(), "rani": rani_signal(),
             "repo": repo_signal(), "human": human_signal()}
    prod = 1.0
    for v in parts.values():
        try:
            prod *= float(v.get("value", 1.0))
        except Exception:
            pass  # scanner:ignore -- documented tolerance: a non-numeric part value is skipped as neutral 1.0 (combined()'s contract)
    veto = bool(parts["human"].get("veto"))
    return {"combined": round(prod, 4), "veto": veto, "parts": parts}
