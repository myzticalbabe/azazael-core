#!/usr/bin/env python
# coding: utf-8
"""Converge loop: whole-repo pytest on 3.15 GIL + 3.15t, outputs feed next inputs.

Each iteration runs the suite on both interpreters with --junitxml, parses
results, and writes state/converge_315.json:
    {"iteration": N, "gil": {...}, "ft": {...},
     "failures": [{"nodeid":..., "interp":..., "kind":"failure|error|collection",
                   "message":...}, ...]}
The agent (or a fixer step) consumes the manifest, repairs, and re-runs with
--iteration N+1. Convergence = zero failures on BOTH interpreters, or the
remaining items all quarantined in --quarantine (upstream 3.15 bugs, flakes
proven by 3x re-run). Quarantined items are tracked, never silently dropped.

Usage:
    python tools/converge_315.py --iteration 1 [--collect-only] [--run-ft]
    python tools/converge_315.py --iteration 2 --quarantine state/q.json
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # repo root (azazael/)
STATE = HERE / "state" / "converge_315.json"
VENVS = {
    "gil": Path(r"C:\Azazael\.venvs\py315\Scripts\python.exe"),
    "ft": Path(r"C:\Azazael\.venvs\py315t\Scripts\python.exe"),
}


def list_slow_nodeids(python):
    """Nodeids of slow-marked tests (one process each under --slow-solo)."""
    r = subprocess.run(
        [str(python), "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", "-m", "slow"],
        cwd=str(HERE), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)
    ids = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("tests/") and "::" in line:
            ids.append(line.split()[0])
    return ids


_CAPTURE_ON_CRASH = False


def run_suite(python, extra, junit_path, timeout_s):
    cmd = [str(python), "-m", "pytest", "-q", "--tb=line",
           "-p", "no:cacheprovider", "--continue-on-collection-errors",
           f"--junitxml={junit_path}"] + extra
    if _CAPTURE_ON_CRASH and os.name == "nt":
        wrapper = HERE / "tools" / "capture_crash.ps1"
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(wrapper), "--"] + cmd
    try:
        r = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout_s)
        tail = (r.stdout or "").strip().splitlines()[-5:]
    except subprocess.TimeoutExpired:
        return {"ok": False, "timeout": True, "tail": ["TIMEOUT"]}
    return {"ok": r.returncode == 0, "rc": r.returncode, "tail": tail}


def canonical_nodeid(nid):
    """Canonical form for comparing pytest vs JUnit nodeids.

    pytest:  tests/test_x.py::TestY::test_z
    JUnit:   tests.test_x.TestY::test_z  (classname uses dots)
    Both -> tests.test_x.TestY.test_z (dots, no .py, :: -> .).
    Slash-vs-dot mismatches otherwise silently match nothing.
    """
    s = (nid or "").strip().replace("\\", "/")
    parts = s.split("::")
    head = parts[0].replace("/", ".")
    if head.endswith(".py"):
        head = head[:-3]
    rest = [p.strip() for p in parts[1:]]
    return ".".join([head] + rest).strip(".")


def load_quarantine(qpath):
    """Load + validate quarantine file. Returns (set_canonical, warnings)."""
    warnings = []
    try:
        raw = json.loads(Path(qpath).read_text(encoding="utf-8"))
    except Exception as e:
        return set(), [f"unreadable quarantine file: {e}"[:200]]
    items = raw.get("quarantined", []) if isinstance(raw, dict) else []
    if not isinstance(items, list):
        return set(), ["schema error: 'quarantined' must be a list"]
    canon = set()
    seen = {}
    for entry in items:
        if not isinstance(entry, str) or not entry.strip():
            warnings.append(f"schema error: entry must be a non-empty string: {entry!r}"[:200])
            continue
        c = canonical_nodeid(entry)
        if c in seen:
            warnings.append(f"duplicate (after normalization): {entry}")
            continue
        seen[c] = entry
        canon.add(c)
    return canon, warnings


def parse_junit(path):
    out = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failures": [],
           "skip_reasons": {}}
    try:
        root = ET.parse(path).getroot()
    except Exception as e:
        return {**out, "parse_error": str(e)[:200]}
    for tc in root.iter("testcase"):
        cls, name = tc.get("classname", ""), tc.get("name", "")
        nodeid = f"{cls}::{name}" if cls else name
        fail_el = tc.find("failure")
        err_el = tc.find("error")
        skip_el = tc.find("skipped")
        if fail_el is not None:
            out["failed"] += 1
            out["failures"].append({
                "nodeid": nodeid,
                "kind": "failure",
                "message": (fail_el.get("message", "") + " " +
                            (fail_el.text or ""))[:500],
            })
        elif err_el is not None:
            out["errors"] += 1
            out["failures"].append({
                "nodeid": nodeid,
                "kind": "collection" if "collect" in (err_el.text or "").lower()
                        or "error" in name.lower() else "error",
                "message": (err_el.get("message", "") + " " +
                            (err_el.text or ""))[:500],
            })
        elif skip_el is not None:
            out["skipped"] += 1
            reason = (skip_el.get("message", "") or "").strip()[:160] or "no-reason"
            out["skip_reasons"][reason] = out["skip_reasons"].get(reason, 0) + 1
        else:
            out["passed"] += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iteration", type=int, default=None,
                    help="manifest iteration number; defaults to YYYYMMDD "
                         "so unattended scheduled runs self-number")
    ap.add_argument("--collect-only", action="store_true")
    ap.add_argument("--run-ft", action="store_true")
    ap.add_argument("--quarantine", default=None)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--extra", nargs="*", default=[])
    ap.add_argument("--isolate-slow", action="store_true",
                    help="run slow-marked (timing-sensitive) tests FIRST in a "
                         "solo quiet pass, then the rest with -m 'not slow'. "
                         "Production flake defense for this machine.")
    ap.add_argument("--slow-solo", action="store_true",
                    help="run EACH slow-marked test in its own pytest process. "
                         "For C-level crashes (e.g. torch AV) that need a "
                         "cumulative pile-up: no shared process, no pile-up. "
                         "Implies --isolate-slow.")
    ap.add_argument("--capture-on-crash", action="store_true",
                    help="run every phase UNDER tools/capture_crash.ps1 "
                         "(procdump -e monitor). On an access violation a "
                         "full .dmp (with NATIVE stacks) lands in "
                         "state/crashdumps/. Default off: dumps are GB-scale. "
                         "Windows only.")
    ap.add_argument("--force", action="store_true",
                    help="allow a partial run (collect-only, timeout, or "
                         "missing interp) to overwrite the full manifest. "
                         "Without --force, partial runs go to a .partial "
                         "sibling so a narrowed run can never replace a "
                         "full baseline with a subset.")
    a = ap.parse_args()
    if a.iteration is None:
        a.iteration = int(datetime.now(timezone.utc).strftime("%Y%m%d"))

    quarantined = set()
    q_warnings = []
    qpath = a.quarantine
    if qpath and not Path(qpath).is_absolute():
        # Scheduled tasks start in System32; resolve against the repo root.
        qpath = str(HERE / qpath)
    if qpath and Path(qpath).exists():
        quarantined, q_warnings = load_quarantine(qpath)

    interps = ["gil"] + (["ft"] if a.run_ft else [])
    manifest = {"iteration": a.iteration,
                "at": datetime.now(timezone.utc).isoformat(),
                "quarantined_count": len(quarantined),
                "quarantine_warnings": q_warnings,
                "isolate_slow": bool(a.isolate_slow),
                "capture_on_crash": bool(a.capture_on_crash)}
    global _CAPTURE_ON_CRASH
    _CAPTURE_ON_CRASH = bool(a.capture_on_crash)
    all_failures = []
    for interp in interps:
        if not VENVS[interp].exists():
            manifest[interp] = {"ok": False, "missing_venv": str(VENVS[interp])}
            continue
        phases = [("-m slow", ["-m", "slow"]), ("rest", ["-m", "not slow"])] \
            if a.isolate_slow and not a.collect_only else [("all", [])]
        if a.slow_solo and not a.collect_only:
            # Each slow test gets a FRESH interpreter: cumulative C-level
            # pile-ups (torch AV) cannot form across a process boundary.
            solo_ids = list_slow_nodeids(VENVS[interp])
            phases = [(nid, [nid]) for nid in solo_ids] + [("rest", ["-m", "not slow"])]
        agg = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0,
               "failures": [], "ok": True, "skip_reasons": {}}
        timed_out = False
        for phase_name, phase_extra in phases:
            with tempfile.TemporaryDirectory() as tmp:
                junit = str(Path(tmp) / "j.xml")
                extra = ["--collect-only"] if a.collect_only else list(a.extra)
                res = run_suite(VENVS[interp], extra + phase_extra,
                                junit, a.timeout)
                if res.get("timeout"):
                    manifest[interp] = {**res, "live_failures": "?"}
                    timed_out = True
                    break
                parsed = parse_junit(junit)
                for k in ("passed", "failed", "errors", "skipped"):
                    agg[k] += parsed.get(k, 0)
                for reason, n in parsed.get("skip_reasons", {}).items():
                    agg["skip_reasons"][reason] = agg["skip_reasons"].get(reason, 0) + n
                agg["failures"].extend(parsed.get("failures", []))
                agg["ok"] = agg["ok"] and res.get("ok", False)
                agg["tail"] = res.get("tail", [])
        if timed_out:
            continue
        live = [f for f in agg["failures"]
                if canonical_nodeid(f["nodeid"]) not in quarantined]
        agg["live_failures"] = len(live)
        agg["quarantined_hit"] = len(agg["failures"]) - len(live)
        for f in live:
            f["interp"] = interp
            all_failures.append(f)
        manifest[interp] = agg

    manifest["failures"] = all_failures
    manifest["total_live"] = len(all_failures)
    if a.collect_only:
        manifest["collect_only"] = True
    # Skip-reason deltas vs previous manifest (baseline = last STATE).
    prev_manifest = {}
    if STATE.exists():
        try:
            prev_manifest = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    prev_reasons = {}
    for i in interps:
        for reason, n in (prev_manifest.get(i, {}).get("skip_reasons", {}) or {}).items():
            prev_reasons[reason] = prev_reasons.get(reason, 0) + n
    cur_reasons = {}
    for i in interps:
        for reason, n in (manifest.get(i, {}).get("skip_reasons", {}) or {}).items():
            cur_reasons[reason] = cur_reasons.get(reason, 0) + n
    manifest["skip_reason_delta"] = {
        r: cur_reasons.get(r, 0) - prev_reasons.get(r, 0)
        for r in set(cur_reasons) | set(prev_reasons)
        if cur_reasons.get(r, 0) != prev_reasons.get(r, 0)
    }
    # Re-baseline ordering guard: a partial run must never overwrite a full
    # manifest. Partial = collect-only, any timeout, or an interp present in
    # the previous manifest but missing now. Writes to a .partial sibling
    # unless --force. Finalize (full, both interps, no timeout) overwrites.
    partial_reasons = []
    if a.collect_only:
        partial_reasons.append("collect-only")
    for i in interps:
        if isinstance(manifest.get(i), dict) and manifest[i].get("timeout"):
            partial_reasons.append(f"{i}-timeout")
    for i in ("gil", "ft"):
        if i in prev_manifest and i not in manifest:
            partial_reasons.append(f"{i}-missing")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    if partial_reasons and not a.force:
        manifest["partial"] = True
        manifest["partial_reasons"] = partial_reasons
        out = STATE.with_suffix(".partial.json")
        out.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        print(f"partial run ({', '.join(partial_reasons)}): wrote {out}, "
              f"full manifest untouched (use --force to overwrite)")
    else:
        STATE.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"iteration {a.iteration}: "
          + ", ".join(f"{i}={manifest.get(i, {}).get('live_failures', '?')} live" for i in interps)
          + f" | total_live={manifest['total_live']} -> {STATE}")
    for f in all_failures[:20]:
        print(f"  [{f['interp']}/{f['kind']}] {f['nodeid']}")
    if len(all_failures) > 20:
        print(f"  ... +{len(all_failures) - 20} more (see manifest)")
    return 0 if manifest["total_live"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
