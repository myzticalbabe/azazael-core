#!/usr/bin/env python3
"""Hourly self-improvement cycle: SpineCycleWorm with apply=True.

Closes the loop the audit found open: default run_cycle(apply=False) is a
dry-run, and nothing invoked it on a schedule, so the homeostat ledger
starved (0 samples) and the Red Queen had no pressure. This entry point:
  1. feeds recent mesh route outcomes (keeps ledger above MIN_SAMPLES),
  2. runs run_cycle(apply=True) so proposals land,
  3. persists spine state (restart-safe).

Schedule (user-level, no admin):
  schtasks /create /tn AzazaelSpineCycle /tr "python %AZAZAEL_ROOT%\\scripts\\spine_cycle_apply.py" /sc hourly /f
Logs to ~/.azazael/spine_cycle.log. Checkpoint models stay on G:.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Resolve src/ portably from this file's location (no hardcoded user path).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Quiet the registry's one-INFO-per-worm import chatter by NAME rather than
# with a process-wide `logging.disable(logging.CRITICAL)`. That blanket switch
# also swallowed this process's own operator warnings -- notably the spine
# gate's `_loud` WARNING (CORE.WORMS.expansion.spine) -- and this is the exact
# process the hourly scheduler runs, so a ~30h self-image refusal went unseen.
# Silencing the noisy logger by name keeps the log clean while leaving
# WARNING+ live, so a refusal still reaches stderr and the chain's per-job log.
# The logger list lives in CORE/log_hygiene.py, shared by the whole family.
from CORE.log_hygiene import quiet_noisy_loggers  # noqa: E402
import subprocess as _sp

quiet_noisy_loggers()

LOG = os.path.expanduser("~/.azazael/spine_cycle.log")
_ERR = 100  # chars kept from a subprocess tail when logging a failure

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# graphify-out/ is two of the seven artifacts the self-image gate checks, and
# `tools/refresh_all.py` deliberately does NOT rebuild it -- so a stale graph
# could only ever be cleared by hand. That is the state the loop was stuck in
# from 2026-09-18T20:45. `graphify update` re-extracts CODE with no LLM at all
# (the quota-limited 5 req/min semantic pass is a different command), so the
# graph IS self-repairable; it just runs for ~31 minutes on this tree and
# therefore has to be detached rather than awaited.
GRAPH_REFRESH_SCRIPT = os.path.join(_HERE, "graph_refresh_no_llm.py")
GRAPH_REFRESH_MARKER = os.path.join(os.path.expanduser("~/.azazael"),
                                    "graph_refresh.running")
#: A pass takes ~31 min. Past this, the marker is a corpse from a killed
#: process (or a reboot), not a live run -- it must not block forever.
GRAPH_REFRESH_DEADLINE_S = 3 * 3600

#: Past this age, the marker's recorded pid must still be ALIVE or the marker
#: is treated as a corpse. A bare mtime check is not enough: see
#: `_graph_refresh_running` for the measured case where it was not.
MARKER_GRACE_S = 180.0


def _pid_alive(pid: int) -> bool:
    """Does this pid exist? Fails SAFE (True) when the question is unanswerable.

    Assuming "alive" on an error risks skipping one pass; assuming "dead"
    risks running two graph re-extracts over the same tree at once.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/fi", "PID eq %d" % pid, "/fo", "csv"],
                capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")
            return ("\"%d\"" % pid) in (out.stdout or "") or (" %d " % pid) in (out.stdout or "")
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat()} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _measure(env: str) -> tuple:
    """REAL latency probe per env (no fabricated outcomes)."""
    import socket as _s
    import time as _t
    t0 = _t.perf_counter()
    try:
        if env == "wsl2-ubuntu":
            ok = _sp.run(["wsl", "-d", "Ubuntu-26.04", "--", "echo", "ok"],
                         capture_output=True, timeout=60).returncode == 0
        elif env == "wsl2-kali":
            ok = _sp.run(["wsl", "-d", "kali-linux", "--", "echo", "ok"],
                         capture_output=True, timeout=60).returncode == 0
        elif env == "g_archive":
            p = "G:/source-of-truth/azazael_models/probe.tmp"
            open(p, "w", encoding="utf-8").write("x" * 4096)
            os.stat(p)
            os.remove(p)
            ok = True
        else:
            _s.gethostbyname("localhost")
            ok = True
    except Exception:
        ok = False
    return ok, round((_t.perf_counter() - t0) * 1000, 1)


def _log_cycle_outcome(rep: dict) -> int:
    """Log one cycle result and return the process exit code.

    The single place that maps a cycle report onto a log line and an exit code,
    so a refusal cannot be reported as a success. `run_cycle` returns early for
    five reasons (cycle already running, throttled, stale self-image, ...), and
    every one of those carries `skipped` and reports no `homeostat` -- which is
    exactly the shape this wrapper used to print as a normal line. Non-zero on
    any skip so a scheduler shows it as a failure instead of green.
    """
    if rep.get("skipped"):
        log(f"cycle={rep.get('cycles')} SKIPPED={rep.get('skipped')} "
            f"reason={rep.get('reason')} scanned=None proposals=0 "
            f"applied=None queen=None")
        return 1
    home = (rep.get("homeostat") or {})
    log(f"cycle={rep.get('cycles')} scanned={home.get('scanned')} "
        f"proposals={len(home.get('proposals') or [])} "
        f"applied={home.get('applied')} "
        f"queen={(rep.get('red_queen') or {}).get('queen_role')}")
    return 0


def _read_marker():
    """(started_at, pid) from the marker; tolerant of the old time-only format.

    The marker predates the pid field, so a file containing one token must keep
    working -- a reader that crashed on it would turn an upgrade into an outage.
    """
    try:
        with open(GRAPH_REFRESH_MARKER, encoding="utf-8") as fh:
            parts = fh.read().split()
    except OSError:
        return None
    if not parts:
        return None
    try:
        ts = float(parts[0])
    except ValueError:
        return None
    pid = 0
    if len(parts) > 1:
        try:
            pid = int(parts[1])
        except ValueError:
            pid = 0
    return ts, pid


def _graph_refresh_running(now: float | None = None) -> bool:
    """Is a graph pass REALLY in flight? Two facts, not one.

    The marker's AGE says how long ago a pass started; the PID it records says
    whether that pass still exists. mtime alone is not enough, and the cost of
    believing it was measured on 2026-09-21: a marker written at 12:06 by a
    refresh whose worker had already died still read "in flight" at 12:38,
    because the deadline is three hours and the ONLY thing that clears the
    marker is the refresh script's exit path -- the very script this guard
    refuses to launch. A dead holder's lock could only be released by the
    process it was blocking, so the chain re-fired every 60 seconds for over
    half an hour, each fire running its whole job set and changing nothing.
    """
    parsed = _read_marker()
    if parsed is None:
        return False
    ts, pid = parsed
    age = (time.time() if now is None else now) - ts
    if not (0 <= age < GRAPH_REFRESH_DEADLINE_S):
        return False
    if pid > 0 and age > MARKER_GRACE_S and not _pid_alive(pid):
        log("graph refresh: marker is a corpse (pid %d is gone) -- holding nothing"
            % pid)
        return False
    return True


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return float("-inf")


def _graph_stale():
    """Is graphify-out/ behind its sources? None = the probe could not answer.

    None is not False: an unreadable probe is not evidence of freshness, and
    only a definite True justifies launching half an hour of work.
    """
    try:
        import graph_refresh_no_llm as _grn
        return _grn.graph_stale()
    except Exception as exc:
        log(f"graph freshness: unknown ({str(exc)[:_ERR]})")
        return None


def _launch_graph_refresh() -> None:
    """Start the no-LLM graph re-extract detached, at most one at a time."""
    if _graph_refresh_running():
        log("graph refresh: already in flight, not starting a second")
        return
    flags = 0
    for _name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"):
        flags |= getattr(subprocess, _name, 0)
    try:
        os.makedirs(os.path.dirname(GRAPH_REFRESH_MARKER), exist_ok=True)
        # Claim the slot FIRST (our own pid), so two launchers racing here
        # cannot both start a pass; then replace it with the CHILD's pid, which
        # is the one whose liveness answers "is a pass still running".
        with open(GRAPH_REFRESH_MARKER, "w", encoding="utf-8") as f:
            f.write("%.6f %d" % (time.time(), os.getpid()))
        child = subprocess.Popen([sys.executable, GRAPH_REFRESH_SCRIPT], cwd=_ROOT,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=flags,
                                 close_fds=True)
        with open(GRAPH_REFRESH_MARKER, "w", encoding="utf-8") as f:
            f.write("%.6f %d" % (time.time(), int(child.pid)))
        log("graph refresh: launched detached pid=%d (no LLM, ~31 min); the gate"
            " stays refused until it lands" % int(child.pid))
    except Exception as exc:
        log(f"graph refresh: launch failed ({str(exc)[:_ERR]})")


def _refresh_derived_maps(timeout: float = 900.0) -> None:
    """Rebuild the derived maps a stale self-image blocks the cycle on.

    Calls `tools/refresh_all.py` rather than re-deriving the dependency order
    here: that script owns the order (census -> verdict -> map) and audits its
    own output. Deliberately best-effort -- it can exit non-zero for reasons
    unrelated to the artifacts (a hygiene regression), so its exit code is
    logged and the refreshed image is then re-judged by the gate, not by us.

    Then, because refresh_all.py does not touch graphify-out/, it checks whether
    the graph is still the thing blocking and launches that pass detached. The
    two halves together are what make the refusal self-healing instead of
    permanent: refresh_all covers every hard-gate map including the worklist
    (--worklist added 2026-09-28; before that a WORKLIST-stale refusal could
    never clear without a human), leaving only the graphify-out pair to the
    detached pass.
    """
    script = os.path.join(_HERE, "refresh_all.py")
    try:
        r = subprocess.run([sys.executable, script], capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except Exception as exc:
        log(f"refresh: raised {str(exc)[:_ERR]}")
    else:
        tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
        log(f"refresh: rc={r.returncode} {tail[-1][:_ERR] if tail else 'no-output'}")

    if _graph_stale() is True:
        _launch_graph_refresh()


def main() -> int:
    from CORE.WORMS.expansion.mesh import MeshRouterWorm
    from CORE.WORMS.expansion.spine import SpineCycleWorm
    from CORE import spine_registry as sp

    # 0. Gossip round: merge ubuntu/kali ledgers via C: drop dir first, so
    # the cycle below sees the whole fleet's experience, not just local.
    try:
        import subprocess as _sp0
        _gr = _sp0.run([sys.executable, os.path.join(_HERE, "gossip_round.py")],
                       capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace")
        _gl = (_gr.stdout or "").strip().splitlines()
        log(f"gossip: {_gl[-1][:160] if _gl else 'no-output'}")
    except Exception as exc:
        log(f"gossip: skipped ({str(exc)[:80]})")
    # 1. CURIOSITY probe: gemma ranks candidate envs (~20s, guarded); fallback
    # is thinnest-ledger-key. The pick is MEASURED for real, never fabricated.
    m = MeshRouterWorm()
    cands = ["wsl2-ubuntu", "wsl2-kali", "g_archive", "windows"]
    order = list(cands)
    try:
        from CORE.WORMS.expansion.driver import GemmaDriverWorm
        stats = {}
        try:
            gt = json.load(
                open("G:/source-of-truth/azazael_models/groundtruth.json",
                     encoding="utf-8")
            )
            vw = sp.get_homeostat_view()
            for e in cands:
                rk = [k for k in vw if k.endswith(">" + e)]
                n = sum(vw[k].get("successes", 0) + vw[k].get("failures", 0) for k in rk)
                stats[e] = {"ok_rate": gt.get(e, {}).get("ok_rate", 1.0),
                            "median_ms": gt.get(e, {}).get("median_ms", 50.0),
                            "samples": n}
        except Exception:
            stats = {}
        r = GemmaDriverWorm(timeout=90.0).rank_trials(
            cands, context="hourly mesh health probe", stats=stats or None)
        if r.get("order"):
            # `order` is MODEL OUTPUT and it becomes a destination name in the
            # route ledger. Unfiltered, a hallucinated entry becomes a real
            # key: the live ledger carried `route:windows>route` (2 successes)
            # from exactly this path, dated 2026-09-17. Filter to the declared
            # candidates before anything downstream can name a route.
            ranked = [e for e in r["order"] if e in cands]
            dropped = [e for e in r["order"] if e not in cands]
            if dropped:
                log(f"curiosity: dropped {len(dropped)} off-candidate(s) "
                    f"from the ranked order: {dropped[:4]}")
            if ranked:
                order = ranked
                log(f"curiosity: model-ranked {order} (src={r.get('src')})")
    except Exception as exc:
        log(f"curiosity: heuristic order ({str(exc)[:60]})")
    for dst in order[:2]:
        try:
            ok, ms = _measure(dst)
            m.record_route_result(dst, ok and ms < 30000, ms)
        except Exception:
            pass
    # 1b. eBPF sample (WSL Ubuntu, best-effort ~15s): score live syscall
    # rates vs bcc_baseline.json; any baseline alert becomes a ledger
    # failure sample so the homeostat trains on kernel telemetry hourly.
    try:
        import json as _json
        import shutil
        import subprocess as _sp
        # refresh the C: mirror WSL can read (WSL cannot see G:)
        for src, dst in (("G:/source-of-truth/azazael_models/bcc_baseline.json",
                          "C:/Azazael/azazael/output/bcc_baseline.json"),):
            try:
                shutil.copyfile(src, dst)
            except OSError:
                pass
        for distro, env in (("Ubuntu-26.04", "wsl2-ubuntu"),
                             ("kali-linux", "wsl2-kali")):
            r = _sp.run(["wsl", "-d", distro, "--", "python3",
                         "/mnt/c/Azazael/azazael/scripts/bcc_oneshot.py"],
                        capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
            try:
                ebpf = _json.loads((r.stdout or "").strip().splitlines()[-1])
            except Exception:
                ebpf = {"success": False, "error": (r.stderr or "empty")[:80]}
            n_alerts = len(ebpf.get("alerts", [])) if ebpf.get("success") else -1
            if n_alerts >= 0:
                # An eBPF syscall-alert count is NOT an observation of whether
                # this route delivers. It was being written into the shared
                # `route:<src>><env>` key as a route verdict, with the 8s
                # collection window hardcoded as if it were a route latency --
                # which is exactly the alternating [95ms, 8000ms] history and
                # the standing -1 on both wsl2 routes (forensic 2026-09-29).
                # The homeostat still trains on kernel telemetry; it just does
                # so under its own namespace, where it cannot move a route
                # weight. This script runs HOURLY, so it was the dominant
                # writer of the route ledger, not a side channel.
                try:
                    from CORE import spine_registry as _sr
                    _sr.record_outcome("ebpf", env, n_alerts == 0, 8000.0)
                except Exception as exc:
                    log(f"ebpf: record failed ({str(exc)[:60]})")
                log(f"ebpf: {env} comms={(ebpf.get('comms'))} alerts={n_alerts}")
                try:
                    with open("C:/Azazael/azazael/output/ebpf_rates.jsonl",

                              "a", encoding="utf-8") as _f:
                        _f.write(_json.dumps({"env": env, "alerts": n_alerts,
                                              "comms": ebpf.get("comms")}) + "\n")
                except OSError:
                    pass
            else:
                log(f"ebpf: {env} skipped ({str(ebpf.get('error'))[:80]})")
    except Exception as exc:
        log(f"ebpf: skipped ({str(exc)[:80]})")
    # 2. Cycle with apply=True (proposals land, not just reported).
    c = SpineCycleWorm(min_interval_seconds=0)
    rep = c.run_cycle(apply=True)

    # 2a. A REFUSAL MUST NOT BE SILENT.
    #
    # This wrapper used to log only cycles/scanned/proposals/applied/queen, all
    # of which are None/0 on an early return. The proprioception gate refuses
    # with {"skipped": "stale_self_image", "reason": ...} -- so from
    # 2026-09-18T20:45 every hourly run printed a success-shaped line while the
    # cycle wrote nothing for ~30h, and nothing was red. The gate was right; the
    # reporting hid it. `skipped`/`reason` are now logged, the exit code is
    # non-zero so the scheduled task records a failure, and the stale-map case
    # is repaired first because its remedy is the documented, bounded rebuild in
    # tools/refresh_all.py rather than a judgement call.
    if rep.get("skipped") == "stale_self_image":
        log(f"REFUSED: {rep.get('reason')}")
        # Best-effort repair; the gate remains the authority on whether the
        # refreshed image is good enough to write from.
        _refresh_derived_maps()
        rep = SpineCycleWorm(min_interval_seconds=0).run_cycle(apply=True)
        if rep.get("skipped"):
            log(f"still refused: {rep.get('skipped')} -- {rep.get('reason')}")
        else:
            log("recovered: derived maps refreshed, cycle ran")

    # 3. Persist is done inside run_cycle; belt-and-braces save.
    try:
        sp.save_spine_state()
    except Exception:
        pass
    return _log_cycle_outcome(rep)


if __name__ == "__main__":
    sys.exit(main())