#!/usr/bin/env python3
"""agreement_floor.py -- Aether A-2: retune the agreement floor from evidence.

Every floor in the gate was measured once and trusted since. Corpora shift,
so a static floor rots exactly like a static weight. This tool replays the
live log through the dream agreement metric and writes the measured rate as
`hyper_edge.agreement_floor` -- the floor the A-1 leg holds the line at.

Refuses on thin evidence (fewer than --min-pairs guess/answer pairs): a floor
tuned on noise is worse than an advisory zero, because it fails the next run
for a reason nobody can reproduce.

Exit: 0 wrote the floor, 2 insufficient evidence (nothing written).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

MIN_PAIRS = 20


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=None)
    ap.add_argument("--config", default=str(ROOT / "config" / "defaults.json"))
    ap.add_argument("--min-pairs", type=int, default=MIN_PAIRS)
    args = ap.parse_args(argv)
    from eve_unleashed.live_log import read_events  # noqa: PLC0415
    from eve_unleashed.native_replay import rung_rates  # noqa: PLC0415

    try:
        dr = rung_rates(read_events(args.log)["records"])
    except FileNotFoundError:
        print("agreement_floor: BLIND -- no live log, floor untouched")
        return 2
    if dr["pairs"] < args.min_pairs:
        print("agreement_floor: REFUSED -- %d pairs < %d minimum, floor untouched"
              % (dr["pairs"], args.min_pairs))
        return 2
    weak = [c["rate"] for c in dr["by_rung"].values()
            if c.get("pairs", 0) >= 5]
    rung_floor = min(weak) if weak else dr["hit_rate"]
    cfg_path = Path(args.config)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    he = cfg.setdefault("hyper_edge", {})
    old = he.get("agreement_floor", 0.0)
    old_rung = he.get("agreement_rung_floor", 0.0)
    he["agreement_floor"] = dr["hit_rate"]
    he["agreement_rung_floor"] = rung_floor
    text = json.dumps(cfg, indent=2) + "\n"
    if "\r\n" in cfg_path.read_text(encoding="utf-8")[:2000]:
        text = text.replace("\n", "\r\n")
    cfg_path.write_text(text, encoding="utf-8", newline="")
    print("agreement_floor: pairs=%d hit_rate=%.3f floor %.3f -> %.3f "
          "rung_floor %.3f -> %.3f backend=%s"
          % (dr["pairs"], dr["hit_rate"], old, dr["hit_rate"],
             old_rung, rung_floor, dr["backend"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
