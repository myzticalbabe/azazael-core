#!/usr/bin/env python3
"""rate_promotion.py - one-keystroke human feedback on the current production.

    python scripts/rate_promotion.py up [note]    # champion is good, keep shipping
    python scripts/rate_promotion.py down [note]  # champion is bad, VETO promotion

Appends to state/promo_feedback.json (last 50 kept). The evolution cycle
reads the latest entry: a DOWN stamped on the CURRENT production genes vetoes
the next promotion loudly; anything else is neutral. Microsecond cost.
"""
import hashlib
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
FEEDBACK = os.path.join(_ROOT, "state", "promo_feedback.json")
PROD = os.path.join(_ROOT, "state", "evolved_rl_params.json")


def main():
    if len(sys.argv) < 2 or sys.argv[1].lower() not in ("up", "down"):
        print("usage: rate_promotion.py up|down [note]")
        return 2
    verdict = sys.argv[1].lower()
    note = " ".join(sys.argv[2:])[:200]
    try:
        prod = json.load(open(PROD, encoding="utf-8"))
    except Exception:
        prod = {}
    genes = prod.get("genes") or {}
    h = hashlib.sha256(json.dumps(genes, sort_keys=True).encode()).hexdigest()[:12]
    try:
        d = json.load(open(FEEDBACK, encoding="utf-8"))
        items = d.get("items", [])
    except Exception:
        items = []
    items.append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "verdict": verdict,
                  "genes_hash": h, "note": note})
    items = items[-50:]
    os.makedirs(os.path.dirname(FEEDBACK), exist_ok=True)
    json.dump({"items": items}, open(FEEDBACK, "w", encoding="utf-8"), indent=1)
    print("rated %s (%s) %s" % (verdict.upper(), h, ("- " + note) if note else ""))
    if verdict == "down":
        print("VETO ARMED: next promotion of these genes is blocked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
