#!/usr/bin/env python3
# coding: utf-8
"""freeze_pool.py — frozen record pools for honest A/B comparisons.

Hand-rolled five times this week: extract once, reuse across arms, so
live-window drift cancels instead of contaminating the comparison.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def freeze(limit: int = 500, tag: str = "") -> dict:
    """Extract info+alert pools once. Returns {info, alert, meta}."""
    from CORE.WORMS.expansion.learning import DataPipelineWorm
    pipe = DataPipelineWorm()
    info = pipe.extract_from_memory(limit=limit) or []
    alert = pipe.extract_from_memory(event_type="db_health_alert",
                                     limit=limit) or []
    return {"info": info, "alert": alert,
            "meta": {"limit": limit, "tag": tag,
                     "n_info": len(info), "n_alert": len(alert)}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="freeze_pool")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--tag", default="")
    ap.add_argument("--out", default="",
                    help="write JSON pool file (default: state/pool_<tag>.json)")
    args = ap.parse_args(argv)
    pool = freeze(args.limit, args.tag)
    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parents[1] / "state" /
        ("pool_%s.json" % (args.tag or "frozen")))
    out.write_text(json.dumps(pool, ensure_ascii=False, default=str),
                   encoding="utf-8")
    print("frozen: info=%d alert=%d -> %s"
          % (pool["meta"]["n_info"], pool["meta"]["n_alert"], out))
    return 0 if (pool["info"] and pool["alert"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
