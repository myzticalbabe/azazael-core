#!/usr/bin/env python3
"""Adaptive budgets for the audit / tooling layer (2026-09-12).

Why this exists
---------------
The audit scripts cap their own work with fixed constants:
``repo_audit.py --max-files/--pattern-cap/--phase-timeout`` and a hard
``uncovered[:50]`` truncation, ``mutate.py --max/--timeout``. A cap that is hit
does not fail and does not warn - it silently returns less. The artifact then
looks complete while coverage quietly shrank, so every module added to the repo
makes the reading worse (the uncovered backlog reached 262 modules while the
summary only ever showed 50 of them).

What this does
--------------
A budget is floor / value / ceiling plus a history:

    escalate(name, reason)   grow the value toward the ceiling (default x2),
                             because the last run hit it with work left over.
    settle(name, reason)     halve the value back toward the floor when the
                             last run did NOT hit it, so a budget follows the
                             workload instead of ratcheting one way for ever.

Both are bounded, so auto-expansion cannot run away, and every change is
appended to ``state/budget_policy.json``. An expansion is therefore auditable
rather than invisible.

stdlib only. A missing or corrupt ledger falls back to the defaults instead of
raising - a bad budget file must never stop the audit it is budgeting.

Usage
-----
    import budget_policy as bp

    limit = bp.get("audit.pattern_cap")          # effective value
    if hits >= limit:
        bp.escalate("audit.pattern_cap", "todo count %d hit the cap" % hits)
    else:
        bp.settle("audit.pattern_cap", "not hit at %d" % hits)
"""
from __future__ import annotations

import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state", "budget_policy.json")

# name -> (floor, initial, ceiling). Escalation grows toward the ceiling;
# settling shrinks toward the floor. Floors are the values that were hard-coded
# before this module existed, so a first run behaves exactly as it used to.
DEFAULTS: dict[str, tuple[int, int, int]] = {
    "audit.max_files": (2000, 2000, 40000),
    "audit.pattern_cap": (25, 25, 1000),
    "audit.phase_timeout": (120, 120, 3600),
    "audit.uncovered_shown": (50, 50, 2000),
    "audit.bad_shown": (10, 10, 500),
    "mutate.max_mutants": (20, 20, 2000),
    "mutate.per_test_timeout": (300, 300, 3600),
}

HISTORY_CAP = 400


def _blank() -> dict:
    return {
        "version": 1,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "budgets": {
            name: {"floor": f, "value": v, "ceiling": c}
            for name, (f, v, c) in DEFAULTS.items()
        },
        "history": [],
    }


def _sanitize(doc) -> dict:
    """Return a well-formed document, filling gaps from DEFAULTS.

    Dynamic budgets (registered at runtime via :func:`ensure`, e.g. one per
    audit pattern) are preserved - only their shape is validated.
    """
    out = _blank()
    if not isinstance(doc, dict):
        return out
    budgets = doc.get("budgets")
    if isinstance(budgets, dict):
        for name, row in budgets.items():
            if not isinstance(name, str) or not isinstance(row, dict):
                continue
            default = DEFAULTS.get(name)
            try:
                lo = int(row.get("floor", default[0] if default else 0))
                val = int(row.get("value", default[1] if default else lo))
                hi = int(row.get("ceiling", default[2] if default else val))
            except (TypeError, ValueError):
                continue
            # A hand-edited file must not be able to invert the bounds.
            if hi < lo:
                lo, hi = hi, lo
            lo = max(0, lo)
            out["budgets"][name] = {
                "floor": lo,
                "value": max(lo, min(val, max(lo, hi))),
                "ceiling": max(lo, hi),
            }
    hist = doc.get("history")
    if isinstance(hist, list):
        out["history"] = [h for h in hist if isinstance(h, dict)][-HISTORY_CAP:]
    if isinstance(doc.get("updated"), str):
        out["updated"] = doc["updated"]
    return out


def load() -> dict:
    """Read the ledger; never raise (a bad file degrades to the defaults)."""
    try:
        with open(STATE, encoding="utf-8") as f:
            return _sanitize(json.load(f))
    except Exception:  # noqa: BLE001 - budgets must not break the tool
        return _blank()


def _save(doc: dict) -> None:
    doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        os.replace(tmp, STATE)
    except Exception:  # noqa: BLE001 - a failed write must not break the run
        pass


def ensure(name: str, floor: int, initial: int | None = None,
           ceiling: int | None = None) -> int:
    """Register a dynamically-named budget if absent; return its value.

    Needed for per-pattern budgets (``audit.pattern.print_in_src``), whose
    names are discovered at runtime. Registration is idempotent, so a new
    pattern gets the floor and an existing one keeps whatever it grew to.
    """
    doc = load()
    row = doc["budgets"].get(name)
    if row is not None:
        return int(row["value"])
    lo = int(floor)
    hi = int(ceiling) if ceiling is not None else max(lo, int(initial or lo))
    if hi < lo:
        lo, hi = hi, lo
    val = int(initial) if initial is not None else lo
    doc["budgets"][name] = {"floor": lo, "value": max(lo, min(val, hi)),
                            "ceiling": hi}
    _save(doc)
    return int(doc["budgets"][name]["value"])


def get(name: str) -> int:
    """Effective value of a budget (its floor if the name is unknown)."""
    doc = load()
    row = doc["budgets"].get(name)
    if row is None:
        return DEFAULTS.get(name, (0, 0, 0))[0]
    return int(row["value"])


def bounds(name: str) -> tuple[int, int, int]:
    """(floor, value, ceiling) for a budget."""
    row = load()["budgets"].get(name) or {}
    floor, initial, ceiling = DEFAULTS.get(name, (0, 0, 0))
    return (int(row.get("floor", floor)),
            int(row.get("value", initial)),
            int(row.get("ceiling", ceiling)))


def _record(name: str, action: str, old: int, new: int, reason: str) -> None:
    doc = load()
    doc["history"].append({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "budget": name,
        "action": action,
        "from": old,
        "to": new,
        "reason": reason,
    })
    doc["history"] = doc["history"][-HISTORY_CAP:]
    _save(doc)


def escalate(name: str, reason: str, factor: int = 2,
             needed: int | None = None) -> int:
    """Grow a budget toward its ceiling; returns the new value.

    Called when the last run hit the cap with work still left over. ``needed``
    is the size the last run actually wanted, so a cap can jump straight to it
    instead of doubling its way there over many runs (a pattern with 7000 hits
    against a cap of 25 should not take eight runs to become useful). Bounded
    by the ceiling, so repeated escalations still converge.
    """
    doc = load()
    row = doc["budgets"].get(name)
    if row is None:
        return get(name)
    old = int(row["value"])
    ceiling = int(row["ceiling"])
    new = min(ceiling, max(old + 1, int(old * factor),
                           int(needed) if needed else 0))
    row["value"] = new
    _save(doc)
    if new != old:
        _record(name, "escalate", old, new, reason)
    return new


def settle(name: str, reason: str, factor: int = 2) -> int:
    """Shrink a budget toward its floor when the last run did NOT hit it.

    Without this a single busy run would inflate every budget permanently.
    """
    doc = load()
    row = doc["budgets"].get(name)
    if row is None:
        return get(name)
    old = int(row["value"])
    floor = int(row["floor"])
    new = max(floor, old // max(2, factor))
    row["value"] = new
    _save(doc)
    if new != old:
        _record(name, "settle", old, new, reason)
    return new


def snapshot() -> dict:
    """Effective values, for embedding in a tool's own summary artifact."""
    return {name: r["value"] for name, r in load()["budgets"].items()}


def set_value(name: str, value: int, reason: str) -> int:
    """Force a budget to a value (clamped to its bounds) and record why.

    Used when a caller passes an explicit flag: the flag wins, but the ledger
    still shows what changed and why.
    """
    doc = load()
    row = doc["budgets"].get(name)
    if row is None:
        return get(name)
    old = int(row["value"])
    new = max(int(row["floor"]), min(int(value), int(row["ceiling"])))
    row["value"] = new
    _save(doc)
    if new != old:
        _record(name, "set", old, new, reason)
    return new


if __name__ == "__main__":  # pragma: no cover - manual inspection
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "escalate":
        print("escalated %s -> %d" % (sys.argv[2],
                                      escalate(sys.argv[2], "manual")))
    elif len(sys.argv) > 1 and sys.argv[1] == "settle":
        print("settled %s -> %d" % (sys.argv[2], settle(sys.argv[2], "manual")))
    else:
        for _name, _row in load()["budgets"].items():
            print("%-26s %6d  (floor %d, ceiling %d)" % (
                _name, _row["value"], _row["floor"], _row["ceiling"]))
        _h = load()["history"]
        print("\nhistory: %d entr%s" % (len(_h), "y" if len(_h) == 1 else "ies"))
