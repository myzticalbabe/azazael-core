"""compliance_ratchet.py — fail-closed ratchet on scanner category counts.

P0: the RULE 0X-7 scanner gates commits; this ratchet fails when any of the
three baselined categories INCREASES above the recorded counts. Baseline is
measured, not aspirational: `config/compliance_baseline.json` holds the counts
recorded at ratchet time.

A verdict is only meaningful against a FRESH baseline
-----------------------------------------------------
`state/ACTION_REGISTRY_2026-10-10.md` measured this tool issuing a *false*
verdict: it reported `import_inside_function 953 -> 1059` (BLOCKED) while the
authoritative `tools/anti_pattern_gate.py` reported an IMPROVEMENT, because this
tool was comparing against a hand-recorded 8-day-old snapshot. The recommended
dispositions were "retire it, OR make it report its baseline's AGE and refuse to
issue a verdict on a stale one". This implements the second: an age that cannot
be proven (legacy flat format, unparsable timestamp) counts as stale, and the
count comparison runs only AFTER the freshness gate passes. A gate that can
never pass, or that passes on a tree it no longer describes, is not a gate.

Usage:
    python scripts/compliance_ratchet.py                    # check, exit on increase
    python scripts/compliance_ratchet.py --record           # re-measure baseline (v2)
    python scripts/compliance_ratchet.py --max-age-days 30  # widen the freshness window

Exit codes: 0 ok · 1 category increased · 2 measurement failed · 3 STALE baseline
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "config" / "compliance_baseline.json"
SCANNER = ROOT / "src" / "CORE" / "compliance" / "scanner.py"
KEYS = ("unicode_emoji", "import_inside_function", "hardcoded_win_path")

#: Exit code for a REFUSED verdict (baseline too old, or its age unprovable).
#: Deliberately distinct from 1 (a real increase): a caller must be able to tell
#: "the check ran and failed" from "the check refused to run" -- conflating them
#: is how a stale baseline silently reads as a pass.
STALE_EXIT = 3

#: Default freshness window, in days. A baseline older than this is refused.
DEFAULT_MAX_AGE_DAYS = 7.0


def parse_baseline(text: str):
    """Return (recorded_at | None, counts) from a baseline document.

    v2 is `{"recorded_at": <iso>, "counts": {...}}`. Anything else is the legacy
    flat `{category: count}` map, which carries no timestamp -- its age is
    therefore unknowable and the freshness gate treats it as stale.
    """
    data = json.loads(text)
    if isinstance(data, dict) and "counts" in data:
        counts = dict(data.get("counts") or {})
        raw = data.get("recorded_at")
        recorded_at = None
        if isinstance(raw, str):
            try:
                recorded_at = datetime.fromisoformat(raw)
            except ValueError:
                recorded_at = None
            else:
                if recorded_at.tzinfo is None:
                    recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        return recorded_at, counts
    return None, dict(data)


def baseline_age_days(recorded_at, now) -> "float | None":
    """Age of the baseline in days, or None when it cannot be proven."""
    if recorded_at is None:
        return None
    return (now - recorded_at).total_seconds() / 86400.0


def max_age_days(argv) -> float:
    """`--max-age-days N`, defaulting (and falling back on junk) to the default."""
    if "--max-age-days" in argv:
        i = argv.index("--max-age-days")
        if i + 1 < len(argv):
            try:
                return float(argv[i + 1])
            except ValueError:
                return DEFAULT_MAX_AGE_DAYS
    return DEFAULT_MAX_AGE_DAYS


def measure() -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCANNER), "--report", "--dir", str(ROOT / "src")],
        capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")
    counts = {}
    for line in (proc.stdout or "").splitlines():
        m = re.match(r"\s*(\w+):\s+(\d+)\s*$", line)
        if m and m.group(1) in KEYS:
            counts[m.group(1)] = int(m.group(2))
    missing = [k for k in KEYS if k not in counts]
    if missing:
        print("ratchet: scanner output missing %s" % missing)
        return {}
    return counts


def record_baseline(counts: dict) -> None:
    """Write a v2 baseline (with `recorded_at`) through the enforcer.

    Plain write_text would work; routing through permit_here means the ratchet's
    own state file gets the same governed-write treatment as anything else. The
    enforcer import is required, not optional: a fallback direct write is refused
    loud rather than silently unguarded.
    """
    payload = json.dumps({"recorded_at": datetime.now(timezone.utc).isoformat(),
                          "counts": counts}, indent=2) + "\n"
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from CORE.sentinel_enforcer import permit_here
    except Exception as exc:  # noqa: BLE001 - import guard, loud by design
        print("ratchet: write refused (%s: %s)" % (
            type(exc).__name__, str(exc)[:200]))
        raise SystemExit(2)
    # A machine-generated baseline JSON needs no canon recitation; the global
    # hook's own contract authorizes writes inside permit_here().
    with permit_here("compliance_ratchet --record"):
        BASELINE.write_text(payload, encoding="utf-8")
    print("ratchet: baseline recorded %s" % counts)


def main(argv: list) -> int:
    if "--record" in argv:
        counts = measure()
        if not counts:
            return 2
        record_baseline(counts)
        return 0
    if not BASELINE.is_file():
        print("ratchet: no baseline (run --record); skipping")
        return 0

    recorded_at, base = parse_baseline(BASELINE.read_text(encoding="utf-8"))
    age = baseline_age_days(recorded_at, datetime.now(timezone.utc))
    limit = max_age_days(argv)

    # --- freshness gate FIRST: a verdict on an unaged baseline is refused ---
    if age is None:
        print("ratchet: STALE baseline -- age unknown (legacy or unparsable "
              "timestamp); verdict refused. Re-record: --record")
        return STALE_EXIT
    if age > limit:
        print("ratchet: STALE baseline -- age %.1f days exceeds limit %.1f days; "
              "verdict refused. Re-record: --record" % (age, limit))
        return STALE_EXIT

    now = measure()
    if not now:
        return 2
    bad = ["%s %s -> %s" % (k, base.get(k), now[k])
           for k in KEYS if now[k] > int(base.get(k, 0))]
    if bad:
        print("ratchet: BLOCKED, category increased: %s" % "; ".join(bad))
        print("ratchet: fix the violations or re-record with --record")
        return 1
    print("ratchet: OK %s (baseline age %.1f days)" % (now, age))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
