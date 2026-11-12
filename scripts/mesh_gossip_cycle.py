# coding: utf-8
"""mesh_gossip_cycle.py — push signed spine gossip to every peer env.

This is the data plane's first real workload (audit 2026-09-27): until now
MeshRouterWorm.send() had no production caller, so the wire carried nothing
and route learning had no signal. Each run exports ONE signed spine payload
(``spine_registry.export_spine_gossip`` -- Ed25519 over the canonical body,
monotonic seq, TOFU key pinning) and delivers it to every known peer's
canonical port via the router's send().

Receiving side: the peer's mesh_listener dispatches spine-shaped payloads to
GossipSyncWorm.gossip(merge_ledger=True), which applies the same
check_gossip_trust gate -- one trust model, both ends.

Delivery semantics ride on the typed status:
  delivered      -> the seam worked
  peer_rejected  -> the peer is alive but refused us (key/seq/policy) -- signal
  env_error      -> nobody listening / unreachable -- counted, never learned

Gossip is best-effort by design: unreachable peers are EXPECTED (their
listener may not be deployed there yet), so the job exits 0 and reports.
Only a local failure (export broken) exits nonzero.

Output: state/mesh_gossip_cycle.json (bounded: last run only).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

STATE_PATH = ROOT / "state" / "mesh_gossip_cycle.json"


def main() -> int:
    from CORE.env_id import VALID_ENVS, env_id
    from CORE.mesh_transport import MAX_ENVELOPE
    from CORE.WORMS.expansion.mesh import MeshRouterWorm

    env = env_id()
    peers = [e for e in VALID_ENVS if e != env]

    try:
        from CORE.spine_registry import export_spine_gossip
        payload = export_spine_gossip()
    except Exception as exc:
        print("gossip export failed: %s" % str(exc)[:200])
        return 1

    size = len(json.dumps(payload).encode())
    report = {
        "schema": "azazael.mesh_gossip_cycle.v1",
        "at": datetime.now(timezone.utc).isoformat(),
        "env": env,
        "payload_bytes": size,
        "oversized": size > MAX_ENVELOPE,
        "peers": {},
        "delivered": 0,
        "env_errors": 0,
        "peer_rejected": 0,
    }

    router = MeshRouterWorm()
    if report["oversized"]:
        # Local policy refusal (the transport would reject it too). Report it
        # as such rather than manufacturing a fake peer failure.
        report["note"] = ("payload exceeds MAX_ENVELOPE; trim q/spine state "
                          "before gossiping")
        print("payload %d bytes exceeds MAX_ENVELOPE %d -- skipped"
              % (size, MAX_ENVELOPE))
    else:
        t0 = time.time()
        for peer in peers:
            res = router.send(peer, payload, timeout=5.0)
            status = res.get("status", "?")
            report["peers"][peer] = {
                "delivered": bool(res.get("delivered")),
                "status": status,
                "weight": res.get("effective_weight"),
                "error": res.get("error"),
            }
            if status == "delivered":
                report["delivered"] += 1
            elif status == "peer_rejected":
                report["peer_rejected"] += 1
            else:
                report["env_errors"] += 1
        report["duration_s"] = round(time.time() - t0, 2)

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(STATE_PATH)

    print("gossip env=%s peers=%d delivered=%d rejected=%d env_errors=%d"
          % (env, len(peers), report["delivered"],
             report["peer_rejected"], report["env_errors"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
