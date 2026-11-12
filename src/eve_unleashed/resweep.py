"""The recursive walk, on a schedule: re-verify every chain, fail loudly.

Five chains, one verdict. Each chain replays a recorded claim against
live bytes: the Root audit, the funnel seal, the fixture pin, every
resilience number recomputed from the neighbor functions (stale
constants are the lie this catches), and the connectance geometry.
Exit 0: all verify. Exit 1: names the liar -- solid, field, recorded
value, live value. No silent success, no missing column passes.
"""

from __future__ import annotations

import json
import math
import sys
from typing import Dict

from .dodeca_recall import SHAPES
from .honey import root_audit, sha_file
from .proofs import verify_funnel
from .resilience import ENGINE, regime_report
from .shapes import ShapeGraph

FLOAT_FIELDS = ("mean_distance", "moore_efficiency", "spectral_gap")
INT_FIELDS = ("n", "degree", "edges", "diameter")


def sweep(manifest_path: str = "state/honey/honey.json") -> Dict:
    chains: Dict[str, Dict] = {}
    try:
        man = json.load(open(manifest_path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        bad = {"pass": False, "detail": "UNREADABLE %s" % e}
        chains = {k: bad for k in ("root_audit", "funnel_seal",
                                   "fixture_pin", "resilience_numbers",
                                   "connectance")}
        return {"verdict": "FAIL", "chains": chains}

    v = root_audit(manifest_path)
    chains["root_audit"] = {"pass": v["verdict"] == "PASS", "detail": v["verdict"]}

    pf = verify_funnel(man["funnel_proof"], man["artifacts"])
    bad = pf["missing"] + pf["mismatched"]
    chains["funnel_seal"] = {"pass": pf["verdict"] == "PASS",
                             "detail": ",".join(bad) if bad else "seal holds"}

    try:
        live = sha_file("state/honey/" + man.get("fixture_file", "fixture.json"))
        match = live == man["fixture_sha"]
        chains["fixture_pin"] = {
            "pass": match,
            "detail": "MATCH" if match else
            "STALE live=%s recorded=%s" % (live[:12], man["fixture_sha"][:12])}
    except OSError as e:
        chains["fixture_pin"] = {"pass": False, "detail": "UNREADABLE %s" % e}

    stale = []
    for solid in SHAPES:
        rec = man.get("resilience", {}).get("reports", {}).get(solid)
        if rec is None:
            stale.append("%s:missing-column" % solid)
            continue
        live_r = regime_report(solid)
        for f in INT_FIELDS:
            if rec.get(f) != live_r[f]:
                stale.append("%s.%s recorded=%r live=%r"
                             % (solid, f, rec.get(f), live_r[f]))
        for f in FLOAT_FIELDS:
            if not math.isclose(rec.get(f, float("nan")), live_r[f],
                                rel_tol=1e-9):
                stale.append("%s.%s recorded=%r live=%r"
                             % (solid, f, rec.get(f), live_r[f]))
        ls, rs = live_r["survival"], rec.get("survival", {})
        for f in ("fault_diameter", "disconnects"):
            if rs.get(f) != ls[f]:
                stale.append("%s.survival.%s recorded=%r live=%r"
                             % (solid, f, rs.get(f), ls[f]))
        if not math.isclose(rs.get("fault_stretch", float("nan")),
                            ls["fault_stretch"], rel_tol=1e-9):
            stale.append("%s.survival.fault_stretch recorded=%r live=%r"
                         % (solid, rs.get("fault_stretch"),
                            ls["fault_stretch"]))
    chains["resilience_numbers"] = {
        "pass": not stale,
        "detail": ";".join(stale) if stale else "7/7 match (engine=%s)" % ENGINE}

    g = ShapeGraph()
    for e in man["connectance"]["topology"]["edges"]:
        g.declare(e["src"], e["dst"], e["solid"], e.get("note", ""))
    cv = g.audit()
    chains["connectance"] = {"pass": cv["verdict"] == "PASS",
                             "detail": cv["verdict"]}
    ok = all(c["pass"] for c in chains.values())
    return {"verdict": "PASS" if ok else "FAIL", "chains": chains}


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "state/honey/honey.json"
    s = sweep(path)
    for name, c in s["chains"].items():
        print("%s %s: %s" % ("PASS" if c["pass"] else "FAIL", name, c["detail"]))
    print("resweep verdict:", s["verdict"])
    return 0 if s["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
