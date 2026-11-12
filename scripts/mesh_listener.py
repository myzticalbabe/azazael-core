# coding: utf-8
"""mesh_listener.py — the neural-mesh data-plane listener service.

The seam has two ends. ``MeshRouterWorm.send()`` is the client end; this is
the server end. Forensic 2026-09-27: the wire was correct but dormant --
zero listeners on the canonical ports, zero production callers, zero tests.
This file is the missing owner of the receive end.

Modes:
  (default)   run the listener for this env in the foreground (Ctrl-C stops)
  --keepalive liveness obligation: ensure exactly ONE healthy listener for
              this env; spawn a detached replacement when the heartbeat is
              stale or its pid is dead.

Heartbeat: ``state/mesh_listener.json`` (schema azazael.mesh_listener.v1),
rewritten every HEARTBEAT_S while serving. The keepalive treats a fresh
heartbeat plus a live pid as "serving"; anything else is dead and gets a
replacement. Exactly one spawner exists (the keepalive), and it refuses to
spawn while a fresh heartbeat says a listener is already serving -- so the
port can never be silently double-owned.

Env selection: ``AZAZAEL_MESH_ENV`` overrides, else CORE.env_id.env_id().

WHO RUNS WHERE (deployment contract, 2026-09-27): exactly one listener per
env, on the env's canonical port (mesh_transport.ENV_PORTS). Every host runs
this file for ITS OWN env -- Windows host -> env "windows" (18210), each WSL2
distro -> its env (18211/18212), bare/kali hosts -> theirs. The chain's
``mesh-listener`` keepalive job owns the local listener; a remote env that is
not running its own listener simply shows up as ``env_error`` sends (counted,
not learned) until it is deployed there.

Envelope dispatch (2026-09-27): a received envelope is never left to die in
the inbox. Gossip payloads are merged immediately via GossipSyncWorm (which
applies the spine's trust gate); anything else is spilled to
state/mesh_inbox.jsonl so a human or a later consumer can pick it up.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

STATE_PATH = ROOT / "state" / "mesh_listener.json"
SCHEMA = "azazael.mesh_listener.v1"
HEARTBEAT_S = 15.0
STALE_S = 45.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env() -> str:
    override = os.environ.get("AZAZAEL_MESH_ENV")
    if override:
        return override
    from CORE.env_id import env_id
    return env_id()


def _bind_addr() -> str:
    """Where this listener binds: env override, else the last run's choice.

    `AZAZAEL_MESH_BIND` is the supported override (e.g. ``0.0.0.0`` to admit
    the WSL2->Windows direction: WSL2 NAT does not share loopback with the
    Windows host, so a loopback-bound listener is invisible to it). The
    HEARTBINDER remembers the last bind because the keepalive respawn inherits
    the CHAIN CONTROLLER's environment, not this process's — without the
    memory, a respawned listener would silently drop back to loopback and
    close the cross-host seam again. Non-loopback binds still auto-enable
    ``require_auth`` in MeshTransport; the wire admits only signed gossip.
    """
    from CORE.mesh_transport import DEFAULT_BIND
    override = (os.environ.get("AZAZAEL_MESH_BIND") or "").strip()
    if override:
        return override
    hb = _read_heartbeat()
    bind = str(hb.get("bind") or "").strip()
    return bind or DEFAULT_BIND


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_heartbeat() -> dict:
    try:
        parsed = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_heartbeat(payload: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(STATE_PATH)


def _dispatch(msgs: list, counters: dict) -> None:
    """Every received envelope gets an owner: merge, spill, or count.

    Gossip payloads go to GossipSyncWorm (trust-gated, recency-wins -- and
    the spine's check_gossip_trust is the same gate the transport's
    require_auth path uses). Anything else spills to disk; nothing is
    silently dropped (silent-failure class, 2026-09-27).
    """
    from CORE.mesh_transport import spill_inbox
    unhandled = []
    for msg in msgs:
        payload = msg.get("payload") if isinstance(msg, dict) else None
        if isinstance(payload, dict) and (
                payload.get("kind") == "gossip"
                or any(k in payload for k in ("spine_map", "homeostat", "seq"))):
            try:
                from CORE.WORMS.expansion.mesh import GossipSyncWorm
                out = GossipSyncWorm().gossip(payload, merge_ledger=True)
                counters["gossip_merged"] += 1
                counters["last_dispatch"] = {
                    "kind": "gossip", "at": _now_iso(),
                    "src": msg.get("src"), "result": out,
                }
                continue
            except Exception as exc:
                counters["dispatch_errors"] += 1
                counters["last_dispatch"] = {
                    "kind": "gossip", "at": _now_iso(),
                    "error": str(exc)[:120],
                }
        unhandled.append(msg)
    if unhandled:
        counters["spilled"] += spill_inbox("*", unhandled)



def serve(env: str, bind: str | None = None) -> int:
    """Run the listener until interrupted. Heartbeats while serving."""
    from CORE.mesh_transport import MeshTransport

    bind_addr = bind or _bind_addr()
    transport = MeshTransport(env, bind_addr=bind_addr)
    port = transport.listen()
    counters = {"gossip_merged": 0, "spilled": 0, "dispatch_errors": 0,
                "last_dispatch": None}
    row = {"schema": SCHEMA, "env": env, "port": port, "pid": os.getpid(),
           "bind": bind_addr, "require_auth": transport.require_auth,
           "listening": True, "at": _now_iso(), "at_epoch": time.time()}
    _write_heartbeat(row)
    print(f"mesh-listener env={env} port={port} bind={bind_addr} "
          f"require_auth={transport.require_auth} pid={os.getpid()}",
          flush=True)
    ticks = 0
    try:
        while True:
            time.sleep(1.0)
            ticks += 1
            msgs = transport.drain()
            if msgs:
                _dispatch(msgs, counters)
            if ticks % int(HEARTBEAT_S) == 0:
                row["at"] = _now_iso()
                row["at_epoch"] = time.time()
                stats = transport.stats()
                row["received"] = stats.get("received", 0)
                row["errors"] = stats.get("errors", 0)
                row["rejected"] = stats.get("rejected", 0)
                row["inbox"] = stats.get("inbox", 0)
                row["oldest_inbox_age_s"] = stats.get("oldest_inbox_age_s")
                row["gossip_merged"] = counters["gossip_merged"]
                row["spilled"] = counters["spilled"]
                row["dispatch_errors"] = counters["dispatch_errors"]
                row["last_dispatch"] = counters["last_dispatch"]
                _write_heartbeat(row)
    except KeyboardInterrupt:
        pass
    finally:
        transport.close()
        row["listening"] = False
        row["at"] = _now_iso()
        row["at_epoch"] = time.time()
        _write_heartbeat(row)
    return 0


def _port_in_use(port: int) -> bool:
    """SWIM-style indirect probe: is SOMEBODY serving this port?

    A bind attempt WITHOUT SO_REUSEADDR is the zero-noise liveness test: it
    fails while any listener holds the port and succeeds when the port is
    free. Used to distinguish "listener dead" from "heartbeat stale" before
    spawning -- without it, Windows SO_REUSEADDR would let a second listener
    bind the same port (double ownership, traffic split between two inboxes).
    SWIM's rule, adapted: suspicion is not death; probe before replacement.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        probe.close()


def _listener_pids() -> list[int]:
    """PIDs of live listener processes (this script, serve mode only).

    Excludes this process and --keepalive supervisor runs: those are
    short-lived and must never be reaped. Best-effort; [] on any failure.
    """
    me = os.getpid()
    try:
        if os.name == "nt":
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
                 "| Select-Object ProcessId,CommandLine | ConvertTo-Json"],
                capture_output=True, text=True, timeout=25, encoding="utf-8", errors="replace")
            try:
                procs = json.loads(r.stdout or "null") or []
            except Exception:
                return []
            if isinstance(procs, dict):
                procs = [procs]
            out = []
            for p in procs:
                try:
                    pid = int(p.get("ProcessId", 0))
                except (TypeError, ValueError):
                    continue
                cmd = str(p.get("CommandLine") or "")
                if (pid and pid != me and "mesh_listener.py" in cmd
                        and "--keepalive" not in cmd):
                    out.append(pid)
            return out
        # POSIX: pgrep preferred, ps fallback.
        import shutil as _sh
        if _sh.which("pgrep"):
            r = subprocess.run(["pgrep", "-f", "mesh_listener.py"],
                               capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")
            pids = [int(x) for x in r.stdout.split() if x.strip().isdigit()]
        else:
            r = subprocess.run(["ps", "-eo", "pid,args"],
                               capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")
            pids = []
            for line in (r.stdout or "").splitlines()[1:]:
                parts = line.strip().split(None, 1)
                if (len(parts) == 2 and parts[0].isdigit()
                        and "mesh_listener.py" in parts[1]):
                    pids.append(int(parts[0]))
        return [p for p in pids
                if p != me and _pid_alive(p)]
    except Exception:
        return []


def _pick_reap_victims(candidates: list[int], keep_pid: int) -> list[int]:
    """Which duplicate listeners to kill. Pure (unit-tested).

    Keep the heartbeat owner when it is alive among the candidates;
    otherwise keep the newest (highest pid, an approximation) so exactly
    one survives. Never returns keep_pid<=0 as keep.
    """
    cands = sorted(set(candidates))
    if not cands:
        return []
    if keep_pid > 0 and keep_pid in cands:
        return [p for p in cands if p != keep_pid]
    return cands[:-1]


def _kill(pid: int) -> bool:
    try:
        if os.name == "nt":
            r = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=15)
            return r.returncode == 0
        os.kill(pid, 9)
        return True
    except Exception:
        return False


def _reap_duplicates(keep_pid: int) -> list[int]:
    """Kill surplus listeners; return reaped pids. Never raises."""
    try:
        victims = _pick_reap_victims(_listener_pids(), keep_pid)
        return [p for p in victims if _kill(p)]
    except Exception:
        return []


def keepalive(env: str) -> int:
    """Liveness obligation: healthy heartbeat or a detached replacement."""
    from CORE.mesh_transport import port_for

    port = port_for(env)
    hb = _read_heartbeat()
    try:
        age = time.time() - float(hb.get("at_epoch", 0.0))
    except (TypeError, ValueError):
        age = float("inf")
    healthy = (hb.get("listening") is True
               and hb.get("env") == env
               and age <= STALE_S
               and _pid_alive(int(hb.get("pid", 0) or 0)))
    if healthy:
        # Reap-before-report (2026-09-28): the keepalive spawns replacements
        # but never reaped the stale, so duplicates accumulated (two served
        # 19h side by side; SO_REUSEADDR lets both bind). Exactly one stays.
        hb_pid = 0
        try:
            hb_pid = int(hb.get("pid", 0) or 0)
        except (TypeError, ValueError):
            hb_pid = 0
        reaped = _reap_duplicates(hb_pid)
        print(json.dumps({"action": "none", "env": env, "port": port,
                          "pid": hb.get("pid"), "age_s": round(age, 1),
                          "reaped": reaped}))
        return 0

    if _port_in_use(port):
        # Suspect, not dead: a listener holds the port but the heartbeat is
        # stale (file lost, process wedged between beats). Never spawn on top
        # of a live port -- report and let the operator or the next beat win.
        print(json.dumps({"action": "none", "env": env, "port": port,
                          "reason": "port in use but heartbeat stale",
                          "was_age_s": None if age > 10_000_000
                          else round(age, 1)}))
        return 0

    env_vars = dict(os.environ)
    env_vars["AZAZAEL_MESH_ENV"] = env
    # Explicit, not inherited: the respawn must not silently drop a
    # cross-host bind back to loopback (see _bind_addr).
    env_vars["AZAZAEL_MESH_BIND"] = _bind_addr()
    # Explicit kwargs rather than a `**popen_kwargs` dict (2026-10-14): a
    # forwarded dict made the encoding audit report this spawn as UNDETERMINED,
    # because it cannot prove a forwarded key does not add `text=True`. A
    # detached respawn has no pipes at all, so nothing is decoded -- spelling
    # the flags out is what lets the audit clear the call.
    #
    # The platform split is computed into LOCALS so there is still exactly ONE
    # Popen call site. Two branches each naming Popen is a second counted site
    # -- the staged scanner read that split as subprocess_no_timeout 1 -> 2, and
    # it was right -- and it is a divergence hazard besides. `creationflags=0`
    # and `start_new_session=False` are the neutral values on the other
    # platform, so this is the same call the kwargs dict used to make.
    if os.name == "nt":
        _creationflags = (subprocess.CREATE_NEW_PROCESS_GROUP
                          | getattr(subprocess, "DETACHED_PROCESS", 0))
        _new_session = False
    else:
        _creationflags = 0
        _new_session = True
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=str(ROOT), env=env_vars,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=_creationflags, start_new_session=_new_session)
    never_ran = (not hb) or age == float("inf")
    print(json.dumps({"action": "spawned", "env": env, "port": port,
                      "pid": proc.pid,
                      "was_age_s": None if never_ran or age > 10_000_000
                      else round(age, 1)}))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="mesh_listener")
    parser.add_argument("--keepalive", action="store_true",
                        help="ensure one healthy listener; spawn if stale")
    parser.add_argument("--env", default=None,
                        help="mesh env id (default: CORE.env_id.env_id())")
    parser.add_argument("--bind", default=None,
                        help="bind address (default: AZAZAEL_MESH_BIND, else "
                             "the last heartbeat's bind, else 127.0.0.1; "
                             "0.0.0.0 admits cross-host peers)")
    args = parser.parse_args(argv)
    env = args.env or _env()
    if args.bind:
        # Exported so keepalive respawns and children inherit the choice.
        os.environ["AZAZAEL_MESH_BIND"] = args.bind
    if args.keepalive:
        return keepalive(env)
    return serve(env, bind=args.bind)


if __name__ == "__main__":
    raise SystemExit(main())