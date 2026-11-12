#!/usr/bin/env python3
"""AOA live wallboard — Armorie-of-Anubis umbrella orchestrator, full screen.

Stdlib-only. Owns an ArmRegistry: full arm init once at startup, then a
slow probe (benchmark workflow via subprocess + per-arm get_status) every
PROBE_SECS; file state (milestone, benchmark_data, sessions) every refresh.

Usage:
    python aoa_monitor.py                  # live, in-place redraw
    python aoa_monitor.py --once           # single snapshot to stdout
    python aoa_monitor.py --once --no-color
    python aoa_monitor.py --refresh 15
"""
import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List
import re
import shutil

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# `src/` is one level above this file's own directory (AOA/ -> src/).
# `_SRC_DIR` reuses the single `resolve()` already done above, so this module
# keeps ONE resolution of `__file__`; it is the same path as `parents[1]`.
# REPLACED `Path.home() / "azazael" / "src"`, which names a DIFFERENT, stale
# checkout from the one this file ships in -- with the env unset it put the
# wrong tree on sys.path, and with it whatever stale CORE lives there.
_SRC_DIR = HERE.parent
sys.path.insert(0, str(_SRC_DIR))

C_RED = "\x1b[91m"
C_GRN = "\x1b[92m"
C_YEL = "\x1b[93m"
C_CYN = "\x1b[96m"
C_BLD = "\x1b[1m"
C_RST = "\x1b[0m"

REFRESH = 15
PROBE_SECS = 300
WORKFLOW_TIMEOUT = 150

# rainbow wheel (256-color hue rotation) + frame counter: the title gradient
# shifts every refresh (hue drift) and breathes via a bold pulse.
_RAINBOW = [196, 202, 208, 214, 220, 190, 154, 118, 82, 46,
            49, 51, 45, 39, 33, 27, 93, 129, 165, 201]
_FRAME_NO = [0]
_COLOR = [True]


def _rainbow(text, width, step=3, pulse=True):
    """Per-character gradient that drifts each frame + brightness breathing.

    No-color mode returns plain truncated text. Frame math truncates first
    (visible chars), then colorizes, so lines never wrap.
    """
    import math
    plain = re.sub("\x1b\\[[0-9;]*m", "", text)[:max(0, width)]
    if not _COLOR[0]:
        return plain
    frame = _FRAME_NO[0]
    # breathe: bold for half the 8-frame cycle, normal otherwise
    bold = "\x1b[1m" if math.sin(frame * math.pi / 4) >= 0 else ""
    out = []
    for i, ch in enumerate(plain):
        out.append(f"{bold}\x1b[38;5;{_RAINBOW[(i + frame * step) % len(_RAINBOW)]}m{ch}")
    out.append(C_RST)
    return "".join(out)


def _no_color():
    global C_RED, C_GRN, C_YEL, C_CYN, C_BLD, C_RST
    C_RED = C_GRN = C_YEL = C_CYN = C_BLD = C_RST = ""
    _COLOR[0] = False


def _enable_vt():
    """Windows strips ANSI codes unless Virtual Terminal Processing is on.

    Double-clicked consoles (the .bat path) don't inherit it — enable via
    SetConsoleMode, fall back to the os.system('') side effect.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        _k = ctypes.windll.kernel32
        _h = _k.GetStdHandle(-11)
        _m = ctypes.c_ulong()
        if _k.GetConsoleMode(_h, ctypes.byref(_m)):
            _k.SetConsoleMode(_h, _m.value | 0x0004)
            return
    except Exception as exc:
        note_failure("aoa_monitor:L90", exc)
    try:
        os.system("")  # scanner:ignore deliberate: empty command enables ANSI escapes on Windows consoles
    except Exception as exc:
        note_failure("aoa_monitor:L94", exc)


def _fit(s, width):
    plain = re.sub("\x1b\\[[0-9;]*m", "", s)
    if len(plain) <= width:
        return s
    # truncate ansi-safe: cut plain text, drop colors on overflow
    return plain[:max(0, width - 1)] + ">"


def _age_txt(ts):
    if ts is None:
        return "--"
    mins = (time.time() - ts) / 60.0
    if mins < 1:
        return f"{mins * 60:.0f}s"
    if mins < 120:
        return f"{mins:.0f}m"
    if mins < 48 * 60:
        return f"{mins / 60:.1f}h"
    # Recorded-history files run to weeks: "1820.0h" is not readable, and a
    # 76-day-old artifact must not render as a plausible hour count.
    return f"{mins / 1440:.1f}d"


def _iso_to_epoch(iso):
    """ISO timestamp -> epoch seconds, or None when it will not parse.

    Returns a *timestamp*, because `_age_txt` subtracts `time.time()` itself;
    handing it a duration instead double-subtracts and reports a value in the
    1970s.
    """
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except Exception as exc:  # noqa: BLE001
        note_failure("aoa_monitor:L133", exc)
        return None


class AOAState:
    """Owns registry + cached probes. Slow work happens on probe ticks only."""

    def __init__(self):
        self.registry = None
        self.instances = {}
        self.init_results = {}
        self.arm_meta = {}     # arm_id -> registry list_arms entry
        self.cap_ids = {}      # arm_id -> [capability ids] (cached once)
        self.startup_elapsed = 0.0
        self.last_probe = 0.0
        self.arm_status = {}   # arm_id -> status dict
        self.workflow = {}     # parsed benchmark workflow result
        self.wf_history = []   # last slow-probe pass/fail booleans
        self.routing = {}      # elastic-routing + provenance telemetry
        self._bootstrap()

    def _bootstrap(self):
        t0 = time.perf_counter()
        import arms.azazael_arm, arms.codex_arm, arms.eve_arm  # noqa: F401
        import arms.libra_arm, arms.support_arms, arms.kali_arm  # noqa: F401
        # silence AFTER imports (azazael's worm discovery logs INFO per
        # worm during init — the wallboard must never spam its own paint).
        try:
            import logging as _lg
            _lg.disable(_lg.WARNING)
        except Exception as exc:
            note_failure("aoa_monitor:L163", exc)
        try:
            from loguru import logger as _lq
            _lq.remove()
            _lq.add(sys.stderr, level="WARNING")
        except Exception as exc:
            note_failure("aoa_monitor:L169", exc)
        from core.arm_registry import ArmRegistry
        self.registry = ArmRegistry()
        try:
            self.init_results = self.registry.initialize_all() or {}
        except Exception as e:  # noqa: BLE001 — wallboard must never crash
            note_failure("aoa_monitor:L175", e)
            self.init_results = {"_error": str(e)[:120]}
        try:
            for a in self.registry.list_arms():
                aid = a.get("arm_id") or a.get("id")
                if aid:
                    self.arm_meta[aid] = a
                    try:
                        self.instances[aid] = self.registry.get_arm(aid)
                    except Exception as exc:
                        note_failure("aoa_monitor:L184", exc)
        except Exception as exc:
            note_failure("aoa_monitor:L186", exc)
        # capability ids cached once (they never change at runtime)
        for aid, inst in self.instances.items():
            try:
                caps = inst.get_capabilities() or []
                ids = []
                for c in caps:
                    if isinstance(c, str):
                        ids.append(c)
                    else:
                        ids.append(str(getattr(c, "capability_id",
                                      getattr(c, "id", getattr(c, "name", "?")))))
                self.cap_ids[aid] = ids
            except Exception as exc:
                note_failure("aoa_monitor:L200", exc)
        self.startup_elapsed = time.perf_counter() - t0
        self.probe(force=True)

    def probe(self, force=False):
        """Slow probe: per-arm get_status (threaded) + benchmark workflow."""
        now = time.time()
        if not force and now - self.last_probe < PROBE_SECS:
            return
        self.last_probe = now
        with cf.ThreadPoolExecutor(max_workers=len(self.instances) or 4,
                                   thread_name_prefix="aoa-probe") as pool:
            futs = {pool.submit(self._one_status, aid, inst): aid
                    for aid, inst in self.instances.items()}
            for f in cf.as_completed(futs, timeout=120):
                try:
                    aid, st = f.result(timeout=5)
                    self.arm_status[aid] = st
                except Exception as exc:
                    note_failure("aoa_monitor:L219", exc)
        self.workflow = self._run_workflow()
        if self.workflow.get("ran") and "completed" in self.workflow:
            self.wf_history.append(bool(self.workflow.get("passed"))
                                   and self.workflow.get("failed", 1) == 0)
            self.wf_history = self.wf_history[-8:]
        # kali families need a direct (cheap, cached-tools) call — the
        # workflow detail line carries counts only.
        try:
            ki = self.instances.get("kali")
            if ki is not None:
                r = ki.execute("get_status", {})
                if isinstance(r, dict) and isinstance(r.get("data"), dict):
                    self.arm_status["kali"] = {"ok": True, "data": r["data"]}
        except Exception as exc:
            note_failure("aoa_monitor:L234", exc)
        # Elastic routing + provenance telemetry (added 2026-09-14). Ran last
        # and in its own guard so a routing probe can never cost the panel its
        # arm table.
        self.routing = self._routing_probe()

    def _routing_probe(self):
        """Measure elastic selection, provenance, and learned-state durability.

        Three deliberate choices, each because the cheap version would report
        something untrue:

        1. SELECTION is exercised on `get_status` (eight arms declare it) but
           never executed. `kali`'s get_status probes 39 tools over WSL and
           `llama`'s shells out behind a 30 s timeout, and the router has no
           latency model -- only success/failure Q. Executing an elastic pick
           from a wallboard is therefore not safe, while *choosing* one costs
           microseconds.

        2. EXECUTION is exercised once on `get_metrics`, which only `perfmon`
           declares, so the pick is deterministic and bounded (~100 ms of
           psutil). That yields a real envelope, a real contract check and a
           real write-back.

        3. DURABILITY is read from the SAVED snapshot on disk, never from
           this process's live Q dict. The live dict contains the write-back
           made seconds earlier, so reading it would report a durable-looking
           1 for learning that vanishes at exit -- reporting the in-process
           value as "learned" is precisely the failure this line exists to
           catch. The in-process count is shown alongside, labelled.
        """
        out = {}
        spine = None
        try:
            from CORE import spine_registry as _spine
            spine = _spine
        except Exception as exc:
            note_failure("aoa_monitor:L271", exc)
            spine = None
        try:
            from core.router import CrossArmRouter
            router = CrossArmRouter(registry=self.registry)
            index = router.capability_index()
            out["caps"] = len(index)
            out["pairs"] = sum(len(v) for v in index.values())

            decided = router.select_arm("get_status")
            out["pick_cap"] = "get_status"
            out["pick_arm"] = decided.get("arm_id") or "-"
            out["pick_reason"] = decided.get("reason") or "-"
            out["pick_degraded"] = bool(decided.get("degraded"))
            out["pick_read_ms"] = decided.get("read_ms")
            evidence = decided.get("evidence") or {}
            out["pick_measured"] = sum(1 for v in evidence.values()
                                       if v.get("known"))

            routed = router.route_task(None, "get_metrics", {})
            meta = routed.get("metadata") or {}
            out["route_status"] = routed.get("status")
            out["route_arm"] = routed.get("arm_id") or "-"
            out["route_ms"] = meta.get("duration_ms")
            out["route_violations"] = list(meta.get("contract_violations") or [])
            stats = router.get_selection_stats()
            out["write_backs"] = stats.get("write_backs", 0)
            out["suppressed"] = stats.get("write_backs_suppressed", 0)
            out["contract"] = stats.get("contract", {})
            # Durability is the other half of the loop. The write-back above is
            # real and bounded, but until something calls save_spine_state() it
            # lives only in this process's dict and the PROV line reports a
            # PERSIST GAP forever. Nothing else saves it: the spine cycle runs
            # in a DIFFERENT process and never sees an Arm outcome, so an
            # aoa_route: key could only ever be durable if an AOA process wrote
            # it out. Measured before this: 0 `aoa_route:` keys on disk while
            # an in-process routed call produced 1 -- the exact state the
            # tripwire test pinned. Persist only when a write landed, so an
            # idle refresh still costs nothing.
            if out.get("write_backs") and spine is not None:
                try:
                    _saved = spine.save_spine_state()
                    out["persisted"] = bool(_saved.get("saved"))
                    out["persist_error"] = _saved.get("error")
                except Exception as _pe:  # noqa: BLE001
                    note_failure("aoa_monitor:L315", _pe)
                    out["persisted"] = False
                    out["persist_error"] = str(_pe)[:120]
        except Exception as e:  # noqa: BLE001 -- wallboard must never crash
            note_failure("aoa_monitor:L318", e)
            out["error"] = str(e)[:120]
            return out

        # In-process view (contains the write-back just made).
        try:
            out["inproc"] = sum(1 for k in spine.get_q_weights()
                                if k.startswith("aoa_route:")) if spine is not None else None
        except Exception as exc:
            note_failure("aoa_monitor:L326", exc)
            out["inproc"] = None
        # Durable view: the saved snapshot only.
        try:
            _state = Path.home() / ".azazael" / "spine_state.json"
            _raw = _state.read_text(encoding="utf-8")
            out["learned"] = _raw.count("aoa_route:")
            out["state_age"] = time.time() - _state.stat().st_mtime
        except Exception as exc:
            note_failure("aoa_monitor:L334", exc)
            out["learned"] = None
            out["state_age"] = None
        return out

    @staticmethod
    def _one_status(aid, inst):
        try:
            st = inst.get_status() if hasattr(inst, "get_status") else {}
            return aid, {"ok": True, "data": st if isinstance(st, dict) else {}}
        except Exception as e:  # noqa: BLE001
            note_failure("aoa_monitor:L344", e)
            return aid, {"ok": False, "error": str(e)[:800]}

    @staticmethod
    def _run_workflow():
        """Benchmark workflow via subprocess; parse its summary lines."""
        out: dict[str, Any] = {"ran": False}
        try:
            r = subprocess.run(
                [sys.executable, str(HERE / "test_benchmark_workflow.py")],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=WORKFLOW_TIMEOUT,
                cwd=str(HERE))
            txt = (r.stdout or "") + (r.stderr or "")[-500:]
            out["ran"] = True
            out["rc"] = r.returncode
            import re
            m = re.search(r"Completed:\s*(\d+)/(\d+)", txt)
            if m:
                out["completed"], out["total"] = int(m.group(1)), int(m.group(2))
            m = re.search(r"Failed:\s*(\d+)", txt)
            if m:
                out["failed"] = int(m.group(1))
            m = re.search(r"Elapsed:\s*(\d+)ms", txt)
            if m:
                out["elapsed_ms"] = int(m.group(1))
            m = re.search(r"tools=(\d+)/(\d+)\s+wsl=(\S+)", txt)
            if m:
                out["kali"] = f"{m.group(1)}/{m.group(2)} {m.group(3)}"
            m = re.search(r"worms (\d+)", txt)
            if m:
                out["worms"] = int(m.group(1))
            # per-step details: `[OK] step_id: arm.cap -> status | detail`
            steps = {}
            for sm in re.finditer(r"\[(?:OK|FAIL)\]\s+(\w+):\s+(\w+)\.(\w+)\s+->\s+(\w+)\s+\|\s*(.*)", txt):
                steps[sm.group(2)] = sm.group(5).strip()[:60]
            out["steps"] = steps
            out["passed"] = ("BENCHMARK WORKFLOW PASSED" in txt)
        except Exception as e:  # noqa: BLE001
            note_failure("aoa_monitor:L381", e)
            out["error"] = str(e)[:800]
        out["at"] = time.time()
        return out

    def files(self):
        """Cheap per-refresh file state."""
        f = {}
        try:
            ms = json.load(open(HERE / "AOA_MILESTONE.json", encoding="utf-8"))
            sess = ms.get("session") or {}
            f["ms_ver"] = ms.get("version")
            f["ms_n"] = sess.get("milestone_count")
            f["ms_sid"] = sess.get("last_session_id") or sess.get("session_id")
            f["ms_age"] = os.path.getmtime(HERE / "AOA_MILESTONE.json")
            f["milestones"] = sess.get("milestones") or []
            # The file mtime is not the age of this history. A refresh rewrites
            # the descriptive sections (and with them the mtime) while the
            # milestone rows underneath stay in June, so mtime would report
            # 52s for a 76-day-old log. Age the rows by their own timestamps.
            _ts = [str(m.get("timestamp")) for m in f["milestones"]
                   if m.get("timestamp")]
            f["ms_newest"] = max(_ts) if _ts else None
            f["ms_newest_ts"] = _iso_to_epoch(f["ms_newest"])
            f["routes"] = ms.get("cross_arm_routes") or {}
            f["arch"] = ms.get("architecture") or {}
        except Exception as exc:
            note_failure("aoa_monitor:L407", exc)
        try:
            f["bench"] = json.load(open(HERE / "benchmark_data.json", encoding="utf-8"))
            f["bench_age"] = os.path.getmtime(HERE / "benchmark_data.json")
        except Exception as exc:
            note_failure("aoa_monitor:L412", exc)
        f["sessions_dir"] = False
        try:
            sdir = Path.home() / ".aoa" / "sessions"
            f["sessions_dir"] = sdir.exists()
            sfiles = sorted(sdir.glob("*.json"), key=os.path.getmtime) if f["sessions_dir"] else []
            f["sessions_n"] = len(sfiles)
            f["sessions_age"] = os.path.getmtime(sfiles[-1]) if sfiles else None
        except Exception as exc:
            note_failure("aoa_monitor:L421", exc)
        return f


def _metric(aid, st, wfsteps=None):
    """One-glance metric: workflow step detail first (live execution
    evidence), then cached get_status keys."""
    if wfsteps and aid in wfsteps:
        return wfsteps[aid]
    d = (st or {}).get("data", {}) if isinstance(st, dict) else {}
    if not isinstance(d, dict):
        return "?"
    if aid == "kali":
        return f"tools {d.get('tools_installed', '?')}/{d.get('tools_total', '?')}"
    if aid == "azazael":
        return f"worms {d.get('worm_count', d.get('total_worms', '?'))}"
    if aid == "perfmon":
        return f"cpu {d.get('cpu_percent', '?')}% mem {d.get('memory_percent', '?')}%"
    if aid == "eve":
        return f"init={d.get('initialized', '?')}"
    if aid == "agent_selector":
        return f"selected={d.get('selected', '?')}"
    if aid == "libra":
        return f"count={d.get('count', d.get('templates', '?'))}"
    if aid == "codex":
        return "studio exists" if d.get("codex_studio_exists") else "?"
    if aid == "llama":
        return "up" if d.get("initialized", d.get("ok", "?")) else "?"
    if aid == "pams":
        return f"event={d.get('event', 'ok')}"
    return "ok"


def _control_line(state, meta=None):
    """Return the first-frame control-plane summary for the AOA wallboard.

    The monitor is read-only: it re-renders on a timer but never re-probes on
    render, so a fresh-looking number is not evidence that the arms are current.
    This banner states the evidence boundary before the detail panels.

    Banner contract (pinned by tests/test_terminal_monitor_controls.py, shared
    with the Azazael wallboard and the Ex Machina viewer): every evidence count
    is printed as `label N/M` and `next=` is the OPERATOR ACTION slot, so the
    reader of a DEGRADED banner is told what to DO rather than what will run
    next. `meta["ms_newest_ts"]` is the milestone heartbeat: None means no
    evidence at all (producer never ran or the state file is unreadable), which
    must read as DEGRADED, never as a quiet pass.
    """
    meta = meta or {}
    init = {k: v for k, v in (getattr(state, "init_results", {}) or {}).items()
            if not str(k).startswith("_")}
    arms_ok = sum(1 for v in init.values() if v is True)
    workflow = getattr(state, "workflow", {}) or {}
    wf_total = workflow.get("total")
    routing = getattr(state, "routing", {}) or {}
    violations = int(((routing.get("contract") or {}).get("violations")) or 0)
    newest = meta.get("ms_newest_ts")

    evidence = [f"arms {arms_ok}/{len(init)}"]
    evidence.append(f"workflow {workflow.get('completed', 0)}/{wf_total}"
                    if wf_total else "workflow unknown")
    if violations:
        evidence.append(f"provenance violations {violations}")

    if newest is None:
        state_word, colour = "DEGRADED", C_RED
        evidence.append("loop evidence unknown")
        action = ("repair routing contract" if violations
                  else "refresh scheduler/producers")
    elif violations:
        state_word, colour = "DEGRADED", C_RED
        action = "repair routing contract"
    else:
        state_word, colour = "LIVE", C_GRN
        action = "continue observing evidence"

    return (f"{C_BLD}CONTROL {colour}{state_word}{C_RST}  "
            + "  ".join(evidence) + f"  next={action}")


def render(st, now, width, height=40):
    """Full-screen frame. Core (always): header 3 + arms 11 + session 1 +
    routes 1 + kali 1 + bench 1 + footer 1 = ~19. Tall windows add:
    milestones (h>=30), bench detail (h>=34), capability matrix (h>=42)."""
    _FRAME_NO[0] += 1
    L = []
    f = st.files()
    L.append(_rainbow("=" * width, width, step=1, pulse=False))
    L.append(_rainbow(f"  ARMORIE-OF-ANUBIS  v{f.get('ms_ver', '?')}   "
                      f"{now.strftime('%Y-%m-%d %H:%M:%S')}   "
                      f"(probe {_age_txt(st.last_probe)} ago, refresh {REFRESH}s, Ctrl+C to stop)",
                      width, step=2))
    L.append(_rainbow("=" * width, width, step=1, pulse=False))

    # --- arms table ---
    try:
        arms = st.registry.list_arms()
    except Exception as exc:
        note_failure("aoa_monitor:L519", exc)
        arms = []
    L.append(_fit(f"ARMS   {len(arms)} registered  init {sum(1 for v in st.init_results.values() if v is True)}/"
                  f"{len([k for k in st.init_results if not k.startswith('_')])} "
                  f"in {st.startup_elapsed:.1f}s", width))
    for a in arms:
        aid = a.get("arm_id") or a.get("id") or "?"
        name = str(a.get("name", "?"))[:26]
        try:
            ncaps = len(st.instances.get(aid).get_capabilities()) if aid in st.instances else \
                a.get("caps", "?")
        except Exception as exc:
            note_failure("aoa_monitor:L530", exc)
            ncaps = a.get("caps", "?")
        ok = st.init_results.get(aid)
        stt = st.arm_status.get(aid, {})
        if ok is True and stt.get("ok", True):
            flag = f"{C_GRN}OK{C_RST}"
        elif ok is False:
            flag = f"{C_RED}FAIL{C_RST}"
        else:
            flag = f"{C_YEL}??{C_RST}"
        _meta = st.arm_meta.get(aid, {}) if hasattr(st, "arm_meta") else {}
        _ver = str(_meta.get("version", "?"))[:7]
        _cat = str(_meta.get("category", "?"))[:7]
        _wfsteps = st.workflow.get("steps", {}) if hasattr(st, "workflow") else {}
        L.append(_fit(f"  {aid:<13} {name:<22} v{_ver:<7} {_cat:<7} caps={ncaps:<3} "
                      f"{flag}  {_metric(aid, stt, _wfsteps)}", width))

    # --- session / lifecycle ---
    # Three fixes of one shape here: the line used to print the literal
    # ladder "INIT>DISCOVERY>INITIALIZE>READY" (so it claimed READY even when
    # init failed), a milestone *count* with no age while
    # `AOA_MILESTONE.json` was 76 days old, and a missing `~/.aoa/sessions`
    # dir as "0 sessions" — an unreadable number is not a zero.
    _arm_ids = [k for k in st.init_results if not k.startswith("_")]
    _ok_n = sum(1 for v in st.init_results.values() if v is True)
    _phase = (C_GRN + "READY" + C_RST if _arm_ids and _ok_n == len(_arm_ids)
              else C_YEL + "DEGRADED" + C_RST)
    _ms_age = _age_txt(f.get("ms_newest_ts")) if f.get("ms_newest_ts") else "?"
    if "sessions_dir" not in f:
        _sess = f"{C_YEL}sessions ?{C_RST}"
    elif not f.get("sessions_dir"):
        _sess = f"{C_YEL}sessions DIR MISSING{C_RST}"
    else:
        _sess = f"sessions {f.get('sessions_n', 0)} on disk"
    L.append(_fit(f"SESS   phase {_phase}  {_sess}  ms-session {f.get('ms_sid', '?')}  "
                  f"{f.get('ms_n', '?')} milestones (newest {_ms_age} old)", width))

    # --- session milestones (height-gated: the recorded history) ---
    if height >= 30:
        for _ms in (f.get("milestones") or [])[:8]:
            _mid = str(_ms.get("milestone_id", "?"))
            _marm = str(_ms.get("arm_id", "?"))
            _mact = str(_ms.get("action", "?"))[:40]
            _mts = str(_ms.get("timestamp", "?"))[:10]
            _mok = _ms.get("status") == "completed"
            L.append(_fit(f"  {_mid:<8} {_marm:<8} {_mact:<42} "
                          f"{C_GRN if _mok else C_YEL}{_ms.get('status', '?')}{C_RST} {_mts}", width))

    # --- routes + last workflow (with pass/fail history strip) ---
    # The route/capability surface is DERIVED from the live registry. It used
    # to be read straight out of `AOA_MILESTONE.json`, which is hand-authored
    # documentation with no producer: it still claimed 56 total / 12 quartet
    # while the registry now exposes 71 / 22 across 9 arms. The documented
    # figure is kept alongside so the drift is visible instead of hidden.
    routes = f.get("routes") or {}
    _live_caps = sum((a.get("capabilities") or 0) for a in arms)
    _live_quartet = sum((a.get("capabilities") or 0) for a in arms
                        if a.get("category") == "quartet")
    _doc_tot = routes.get("total_routes")
    _doc_q = routes.get("quartet_routes")
    # `caps` was the wrong noun here: this sums each arm's capability COUNT,
    # which counts (arm, capability) SLOTS -- 71 across 9 arms -- not distinct
    # capabilities, of which there are 63. The new ROUTE line reports the
    # distinct figure, so leaving both labelled "caps" made two true numbers
    # look like one wrong one.
    _route_seg = f"slots {_live_caps} live ({_live_quartet} quartet)"
    if _doc_tot is not None and _doc_tot != _live_caps:
        _route_seg += f"  doc {_doc_tot}/{_doc_q} stale"
    w = st.workflow
    _strip = "".join(f"{C_GRN}#{C_RST}" if p else f"{C_RED}x{C_RST}"
                     for p in st.wf_history[-8:]) if hasattr(st, "wf_history") else ""
    if w.get("ran") and "completed" in w:
        wcol = C_GRN if w.get("passed") else C_RED
        L.append(_fit(f"ROUTES {_route_seg}  workflow "
                      f"{wcol}{w['completed']}/{w['total']}{C_RST} "
                      f"fail={w.get('failed', '?')} {w.get('elapsed_ms', '?')}ms "
                      f"{_age_txt(w.get('at'))} ago  [{_strip}]", width))
    else:
        L.append(_fit(f"ROUTES {_route_seg}  workflow pending", width))

    # --- elastic routing / provenance telemetry (added 2026-09-14) ---
    # Keeps two facts visible that no other line carries: every declared
    # capability is now ADDRESSABLE (the router could not reach KaliArm at
    # all until its export was fixed), and whether the router's learned state
    # is DURABLE. The durable count is read from the saved snapshot rather
    # than the live dict, because the live dict already contains the write
    # just performed and would render a durable-looking 1 for learning that
    # evaporates at process exit.
    _rt = getattr(st, "routing", None) or {}
    if _rt.get("error"):
        L.append(_fit(f"ROUTE  {C_YEL}probe error: {_rt['error']}{C_RST}", width))
    elif _rt:
        _pick = f"{_rt.get('pick_cap', '?')}->{_rt.get('pick_arm', '?')} " \
                f"{_rt.get('pick_reason', '?')}"
        _pcol = C_RED if _rt.get("pick_degraded") else C_GRN
        _ms = _rt.get("route_ms")
        _mss = f"{_ms:.0f}ms" if isinstance(_ms, (int, float)) else "?ms"
        L.append(_fit(
            f"ROUTE  {_rt.get('caps', '?')} distinct caps "
            f"({_rt.get('pick_measured', 0)} measured)  "
            f"pick {_pcol}{_pick}{C_RST}  exec {_rt.get('route_arm', '?')} "
            f"{_rt.get('route_status', '?')} {_mss}", width))
        _seg = []
        _con = _rt.get("contract") or {}
        if _con:
            _vc = C_GRN if _con.get("clean") else C_YEL
            _seg.append(f"provenance {_vc}{_con.get('violations', 0)}/"
                        f"{_con.get('checked', 0)} undeclared{C_RST}")
        _learned, _inproc = _rt.get("learned"), _rt.get("inproc")
        _wb = _rt.get("write_backs", 0)
        if _learned is None:
            _seg.append(f"learned {C_YEL}state unreadable{C_RST}")
        else:
            _seg.append(f"learned {_learned} durable / {_inproc} in-process")
        if _wb and not _learned:
            # The honest signal: the bounded write-back works, and nothing has
            # made it durable yet. Reported as a gap rather than as "0 learned",
            # because "0 learned" reads as "nothing has run".
            _seg.append(f"{C_YEL}PERSIST GAP: {_wb} write(s) not saved "
                        f"(no save_spine_state){C_RST}")
        elif not _wb and not _learned:
            _seg.append(f"{C_YEL}idle: no routing run yet{C_RST}")
        L.append(_fit("PROV   " + "  ".join(_seg), width))

    # --- kali families ---
    kst = st.arm_status.get("kali", {}).get("data", {}) or {}
    fam = kst.get("per_family") or {}
    if fam:
        cells = "  ".join(f"{k}{v}" for k, v in sorted(fam.items()))
        L.append(_fit(f"KALI   wsl={kst.get('wsl_distro', '?')} "
                      f"{kst.get('tools_installed', '?')}/{kst.get('tools_total', '?')}  {cells}", width))

    # --- baselines vs live ---
    bench = f.get("bench") or {}
    blines = []
    if "eve" in bench:
        blines.append(f"eve {bench['eve'].get('discovery_total_models', '?')}mdl bm")
    if w.get("worms") is not None:
        blines.append(f"worms live {w['worms']} (bm {(bench.get('azazael') or {}).get('total_worms', '?')})")
    if w.get("kali"):
        blines.append(f"kali live {w['kali']} (bm {(bench.get('kali') or {}).get('tools_total', '?')})")
    if blines:
        L.append(_fit("BENCH  " + "  ".join(blines), width))
    # --- bench detail (height-gated: model bests + platform) ---
    if height >= 34:
        _ev = bench.get("eve") or {}
        _det = []
        if _ev.get("best_gguf_model"):
            _det.append(f"gguf {_ev['best_gguf_model'][:22]} {_ev.get('best_gguf_tps', '?')}t/s")
        if _ev.get("best_ollama_model"):
            _det.append(f"ollama {_ev.get('best_ollama_model', '?')} {_ev.get('best_ollama_tps', '?')}t/s")
        _sys = bench.get("system") or {}
        if _sys:
            _det.append(f"py{_sys.get('python_version', '?')} "
                        f"{'no-GIL' if _sys.get('free_threaded') else 'GIL'} "
                        f"{_sys.get('cpu_count', '?')}c {_sys.get('node', '?')}")
        _cx = bench.get("codex") or {}
        if _cx.get("codex_studio_exists"):
            _det.append("codex studio ok")
        if _det:
            L.append(_fit("DETAIL " + "  ".join(_det), width))
    # --- capability matrix (height-gated: every arm's full cap list) ---
    if height >= 42 and hasattr(st, "cap_ids"):
        for aid in [a.get("arm_id") or a.get("id") for a in arms]:
            _caps = (st.cap_ids.get(aid) or []) if aid else []
            if not _caps:
                continue
            _txt = " ".join(_caps)
            while _txt:
                L.append(_fit(f"  {aid:<13} {_txt[:width - 18]}", width))
                _txt = _txt[width - 18:]

    L.append(_fit(f"    probe every {PROBE_SECS}s  bench {_age_txt(f.get('bench_age'))} ago", width))
    return "\n".join(L)


def main():
    global REFRESH
    ap = argparse.ArgumentParser(description="AOA live wallboard")
    ap.add_argument("--refresh", type=int, default=REFRESH)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()
    REFRESH = max(5, args.refresh)
    if args.no_color:
        _no_color()
    # wallboard must never spam: worm discovery logs INFO per worm.
    try:
        import logging as _lg
        _lg.disable(_lg.WARNING)
    except Exception as exc:
        note_failure("aoa_monitor:L720", exc)
    try:
        from loguru import logger as _lq
        _lq.remove()
        _lq.add(sys.stderr, level="WARNING")
    except Exception as exc:
        note_failure("aoa_monitor:L726", exc)

    _enable_vt()
    _size = shutil.get_terminal_size((100, 40))
    width = max(80, min(_size.columns, 200))
    height = max(20, _size.lines)
    st = AOAState()
    if args.once:
        st.probe(force=True)
        print(render(st, datetime.now(), width, height))
        return 0
    sys.stdout.write("\033[2J\033[H")
    while True:
        st.probe()
        frame = render(st, datetime.now(), width, height).split("\n")
        out = ["\033[H"]
        for i, line in enumerate(frame):
            if i:
                out.append("\r\n")
            out.append(line)
            out.append("\033[K")
        out.append("\033[J")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        time.sleep(REFRESH)


if __name__ == "__main__":
    sys.exit(main())
