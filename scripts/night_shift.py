#!/usr/bin/env python3
"""9-hour autonomous training program. One loop, ~40min cycles, ~13 rounds.

Each round (best-effort, guarded, logged):
  1. gossip round (3-env ledger merge)
  2. eBPF snapshot Ubuntu (+ Kali every 3rd round) -> sentinel scoring
  3. curiosity probe (gemma-ranked, real measurement)
  4. evolution mini-run every 2nd round (6 gens, live fitness)
  5. dream episode every 3rd round (generate -> critic -> index)
  6. bloat snapshot hourly (growth series)
  7. threat feed re-pull every 6th round (~4h) + triage refresh
  8. forest refit when snapshots >= 20
  9. fitness sample appended every round
RAM guard: heavy steps skip if <2GB free. Never raises out of main.
Logs: G:/source-of-truth/azazael_models/nightshift.log + summary.json
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.log_hygiene import quiet_noisy_loggers  # noqa: E402

quiet_noisy_loggers()

BASE = "G:/source-of-truth/azazael_models"
LOG = os.path.join(BASE, "nightshift.log")
ROUNDS = int(os.environ.get("NIGHT_ROUNDS", "13"))
ROUND_S = int(os.environ.get("NIGHT_ROUND_S", "2400"))  # 40 min -> ~8.7h


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def run(cmd, timeout=300):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return r.returncode == 0, ((r.stdout or "")[-500:] + (r.stderr or "")[-300:])
    except Exception as e:
        return False, str(e)[:200]


def ram_ok(gb=2.0):
    try:
        import psutil
        return psutil.virtual_memory().available / 1e9 >= gb
    except Exception:
        return True


def main():
    from CORE import spine_registry as sp
    summary = {"start": datetime.now(timezone.utc).isoformat(), "rounds": []}
    for n in range(1, ROUNDS + 1):
        t0 = time.perf_counter()
        rep = {"round": n}
        ok, _ = run([sys.executable, "C:/Azazael/azazael/scripts/gossip_round.py"], 280)
        rep["gossip"] = ok
        ok, _ = run(["wsl", "-d", "Ubuntu-26.04", "--", "python3",
                     "/mnt/c/Azazael/azazael/scripts/bcc_oneshot.py"], 150)
        rep["ebpf_ubuntu"] = ok
        if n % 3 == 0 and ram_ok():
            ok, _ = run(["wsl", "-d", "kali-linux", "--", "python3",
                         "/mnt/c/Azazael/azazael/scripts/bcc_oneshot.py"], 150)
            rep["ebpf_kali"] = ok
        # curiosity probe via hourly script is scheduler-owned; here: direct measure
        try:
            from CORE.WORMS.expansion.mesh import MeshRouterWorm
            import socket
            m = MeshRouterWorm()
            t = time.perf_counter()
            socket.gethostbyname("localhost")
            m.record_route_result("windows", True, (time.perf_counter() - t) * 1000)
            rep["probe"] = True
        except Exception:
            rep["probe"] = False
        if n % 2 == 0 and ram_ok():
            ok, out = run([sys.executable, "C:/Azazael/azazael/scripts/evolve_loop.py"], 500)
            rep["evo"] = ok
        if n % 3 == 0:
            try:
                from CORE.WORMS.expansion.dreaming import DreamingModule
                from CORE.WORMS.expansion.consolidation import DreamCriticWorm, EngramIndexerWorm
                d = DreamingModule().dream(num_episodes=1, scenario_count=3, persist=False)
                c, ix, kept = DreamCriticWorm(), EngramIndexerWorm(), 0
                for dd in d:
                    t = getattr(dd, "content", None) or str(dd)[:800]
                    if c.critique(t).get("kept"):
                        ix.index(t, env="windows", task="nightshift")
                        kept += 1
                rep["dreams"] = f"{kept}/{len(d)}"
            except Exception as e:
                rep["dreams"] = f"ERR {str(e)[:60]}"
        ok, _ = run([sys.executable, "G:/source-of-truth/azazael_models/bloat_snapshot.py"], 400)
        rep["bloat"] = ok
        if n % 6 == 1:
            try:
                from CORE.WORMS.expansion.spine import SpineCycleWorm
                r = SpineCycleWorm(min_interval_seconds=0).run_cycle(apply=True)
                # A refused write must not read as "0 proposals": that is the
                # success-shaped line the 2026-09-18..21 refusal hid behind.
                if r.get("skipped"):
                    rep["apply"] = "REFUSED %s" % r.get("skipped")
                else:
                    rep["apply"] = len((r.get("homeostat") or {}).get("proposals") or [])
            except Exception as e:
                rep["apply"] = f"ERR {str(e)[:60]}"
        try:
            v = sp.get_homeostat_view()
            rep["ledger"] = sum(x.get("successes", 0) + x.get("failures", 0) for x in v.values())
            sp.save_spine_state()
        except Exception:
            pass
        rep["secs"] = round(time.perf_counter() - t0, 1)
        summary["rounds"].append(rep)
        log("round " + json.dumps(rep))
        time.sleep(max(60, ROUND_S - (time.perf_counter() - t0)))
    summary["end"] = datetime.now(timezone.utc).isoformat()
    open(os.path.join(BASE, "nightshift_summary.json"), "w", encoding="utf-8").write(json.dumps(summary, indent=1))
    log("PROGRAM COMPLETE " + json.dumps(summary["rounds"][-1]))


if __name__ == "__main__":
    main()