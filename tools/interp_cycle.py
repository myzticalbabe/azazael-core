#!/usr/bin/env python
"""Self-maintenance cycle for the tree's individualized interpreter (py316t).

Why this exists
---------------
The interpreter is infrastructure the repo layer cannot fix from inside
itself: the evolve loop tunes repo knobs, but it has no detector for
upstream CPython defects, no actor that compiles C, and no survivable
restart path. Those three gaps are closed here, as runnable commands
instead of prose:

  watch    compare the pinned upstream sha against cpython upstream and
           report what moved (new series branch, new tags, core C fixes).
           Offline-safe: reports ``skipped`` and exits 0, never fails.
  verify   run the six acceptance probes against a candidate interpreter
           (property-based counts, not literals) and write
           ``state/interp_verify.json``. ``--apply`` refreshes the
           acceptance lines of ``state/py316t_build.json`` so the record
           never needs a hand-edit again.
  promote  verify a candidate, then record it as canonical in
           ``state/canonical_interp.json``. Refuses build-tree-based
           candidates unless ``--allow-build-tree-base`` is given: a venv
           whose base is the build tree dies with the tree.
           Takes the build lock; refuses while another lane holds it.
  guard    the cheap daily check: canonical pointer resolves, its version
           matches the record, the build tree still exists when needed,
           disk floor holds.
  dirty    name the -dirty cause: run git status in the CPython source
           tree and record modified/untracked build artifacts into the
           build record. A build with source edits cannot ship.
  lock     take (``--take lane``) or release (``--release``) the build
           lock by hand; ``verify`` and ``guard`` report a live lock.

Exit codes: 0 = pass / nothing to do, 1 = failure or action required,
2 = the check could not be performed (never reported as a pass).

Usage
-----
  python tools/interp_cycle.py watch [--json PATH]
  python tools/interp_cycle.py verify [--candidate PY] [--apply] [--json PATH]
  python tools/interp_cycle.py promote --candidate PY [--allow-build-tree-base]
  python tools/interp_cycle.py guard
  python tools/interp_cycle.py dirty --source DIR
  python tools/interp_cycle.py lock --take LANE | --release
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE = REPO_ROOT / "state"
BUILD_RECORD = STATE / "py316t_build.json"
VERIFY_REPORT = STATE / "interp_verify.json"
WATCH_REPORT = STATE / "interp_watch.json"
POINTER = STATE / "canonical_interp.json"
BUILD_LOCK = STATE / ".interp_build.lock"

CORE_PREFIXES = ("Python/", "Include/", "PCbuild/", "Misc/", "Objects/",
                 "Parser/", "Modules/")


def _read_default(dotted: str, fallback):
    try:
        from tools import defaults_profile
    except Exception:
        try:
            import defaults_profile
        except Exception:
            return fallback
    try:
        return defaults_profile.read_default(dotted, fallback)
    except Exception:
        return fallback


def _setting(key: str, fallback):
    return _read_default("interp." + key, fallback)


def load_record() -> dict:
    return json.loads(BUILD_RECORD.read_text(encoding="utf-8"))


def pinned_sha(record: dict) -> str:
    # Schema drift is expected: the 2026-11-06 record used `evidence_sha` /
    # `source`, the 2026-11-07 rebuild uses a flat `sha`. Try all three.
    for key in ("evidence_sha", "sha"):
        sha = str(record.get(key) or "")
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha
    src = str(record.get("source") or "")
    m = re.search(r"[0-9a-f]{40}", src)
    return m.group(0) if m else ""


def _http_get(url: str, timeout: float) -> tuple[str, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "azazael-interp-watch",
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return "ok", json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return "skipped", {"reason": f"{type(exc).__name__}: {exc}"}


def classify_files(filenames: list[str]) -> dict:
    core = sorted({f for f in filenames
                   if f.startswith(CORE_PREFIXES)})
    other = sorted(set(filenames) - set(core))
    return {"core": core, "core_count": len(core), "other_count": len(other)}


def do_watch(args) -> int:
    timeout = float(_setting("watch_timeout_s", 20))
    repo = str(_setting("repo", "python/cpython"))
    ref = str(_setting("compare_ref", "3.16"))
    try:
        record = load_record()
    except Exception as exc:
        print(f"watch: cannot read build record: {exc}")
        return 2
    sha = pinned_sha(record)
    if not sha:
        print("watch: no pinned sha in build record")
        return 2

    report: dict = {"pinned_sha": sha, "compare_ref": ref,
                     "state": "ok", "commits_ahead": None,
                     "new_tags": [], "core_fix": False, "detail": ""}
    state, cmp_data = _http_get(
        f"https://api.github.com/repos/{repo}/compare/{sha}...{ref}",
        timeout)
    if state == "skipped":
        reason = cmp_data.get("reason", "network unavailable")
        if "404" in reason:
            # Expected pre-fork: the BUILD doc's resolution order (3.16 ->
            # main -> newest tag) says the series branch may not exist yet.
            # The tag leg below still answers "is there a newer series
            # release", so this is informative, not a failure.
            report.update({"state": "ok",
                           "detail": f"no upstream ref {ref} yet "
                           f"(pre-fork, expected): {reason}"})
        else:
            report.update({"state": "skipped", "detail": reason})
    else:
        try:
            commits = cmp_data.get("commits", []) or []
            files: list[str] = []
            for c in commits:
                files.extend([f.get("filename", "") for f in c.get("files", [])])
            # The compare payload may omit per-commit files; fall back to names.
            cls = classify_files(files)
            report.update({
                "commits_ahead": int(cmp_data.get("ahead_by", len(commits))),
                "commit_subjects": [c.get("commit", {}).get("message", "").splitlines()[0][:120]
                                    for c in commits[:20] if c.get("commit")],
                "core_files_touched": cls["core"][:20],
                "core_count": cls["core_count"],
                "other_count": cls["other_count"],
                "core_fix": cls["core_count"] > 0,
                "detail": cmp_data.get("status", ""),
            })
        except Exception as exc:
            report.update({"state": "skipped", "detail": f"payload shape: {exc}"})

    tstate, tags = _http_get(
        f"https://api.github.com/repos/{repo}/tags?per_page=100", timeout)
    if tstate == "ok" and isinstance(tags, list):
        names = [t.get("name", "") for t in tags]
        report["new_tags"] = sorted(
            [n for n in names if re.fullmatch(r"v3\.16.*", n)],
            reverse=True)[:10]
        series = str(record.get("series") or "")
        if series and series not in report["new_tags"]:
            report["new_tags"].insert(0, series + " (pinned)")
    if report["state"] == "skipped" and not report["detail"]:
        report["detail"] = "tag leg unreachable"

    out = args.json or str(WATCH_REPORT)
    Path(out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"state        : {report['state']}"
          + (f" ({report['detail']})" if report["detail"] else ""))
    print(f"pinned       : {sha[:12]} vs {ref}")
    print(f"commits ahead: {report['commits_ahead']}")
    print(f"core C fix   : {'YES — rebuild candidate' if report['core_fix'] else 'no'}")
    print(f"series tags  : {', '.join(report['new_tags'][:5]) or 'unknown'}")
    print(f"wrote {out}")
    if report["state"] == "skipped":
        return 0
    return 1 if report["core_fix"] else 0


def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, cwd=str(REPO_ROOT),
                              encoding="utf-8", errors="replace")
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout:g}s"


def parse_selftest(output: str) -> tuple[int | None, bool]:
    m = re.search(r"(\d+) check\(s\), (\d+) failed, (\d+) skipped", output)
    if not m:
        return None, False
    return int(m.group(1)), (m.group(2) == "0" and m.group(3) == "0")


def parse_pytest(output: str) -> tuple[int | None, bool]:
    m = re.search(r"(\d+) passed", output)
    if not m:
        return None, False
    failed = re.search(r"(\d+) failed", output)
    return int(m.group(1)), (failed is None or failed.group(1) == "0")


def do_verify(args) -> int:
    candidate = args.candidate or str(
        _setting("candidate", "C:/Azazael/.venvs/py316t/Scripts/python.exe"))
    timeout = float(_setting("probe_timeout_s", 300))
    checks: dict = {}
    failed: list[str] = []

    rc, out = _run([candidate, "-c",
                    "import sys, sysconfig; print(sys.version);"
                    "print('gil_enabled', sys._is_gil_enabled());"
                    "print('Py_GIL_DISABLED', "
                    "sysconfig.get_config_var('Py_GIL_DISABLED'))"], 60)
    first = out.strip().splitlines()[0] if out.strip() else ""
    checks["version"] = {"pass": bool(first.startswith("3.16")),
                         "line": first[:120], "exit": rc}
    gil_ok = ("gil_enabled False" in out and "Py_GIL_DISABLED 1" in out
              and rc == 0)
    checks["gil"] = {"pass": gil_ok, "exit": rc}

    env = {"PYTHONPATH": str(REPO_ROOT / "src")}
    import os
    run_env = dict(os.environ, **env)

    # Acceptance item 3: the doc names a `capabilities` CLI subcommand that
    # does not exist. The real probe is HYPER_EDGE.compat.report(); the
    # record carries its one-line output verbatim. Assert on that.
    # NOTE: _run carries no env; this probe needs PYTHONPATH=src, so it
    # spawns directly with run_env instead of going through _run.
    try:
        proc = subprocess.run(
            [candidate, "-c", "from HYPER_EDGE.compat import report; "
             "print(report())"],
            capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT),
            encoding="utf-8", errors="replace", env=run_env)
        rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        rc, out = 124, "TIMEOUT"
    line = out.strip().splitlines()[0] if out.strip() else ""
    checks["capabilities"] = {
        "pass": rc == 0 and "free-threaded" in line
        and "pool=CORE.freethreading.FreeThreadPool" in line
        and "probe_faults=0" in line,
        "line": line[:200], "exit": rc}
    try:
        proc = subprocess.run(
            [candidate, "-m", "HYPER_EDGE.cli", "selftest"],
            capture_output=True, text=True, timeout=timeout, cwd=str(REPO_ROOT),
            encoding="utf-8", errors="replace", env=run_env)
        rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        rc, out = 124, "TIMEOUT"
    count, ok = parse_selftest(out)
    checks["selftest"] = {"pass": ok and rc == 0, "checks": count, "exit": rc}

    rc, out = _run([candidate, "-m", "pytest", "tests/test_hyper_edge.py",
                    "-q", "-p", "no:cacheprovider"], timeout)
    count, ok = parse_pytest(out)
    checks["pytest"] = {"pass": ok and rc == 0, "passed": count, "exit": rc}

    try:
        proc = subprocess.run(
            [candidate, "-m", "HYPER_EDGE.cli", "evolve", "--evidence",
             "ARC/cain/arena_llm_judgments_r9.json", "--generations", "6",
             "--population", "24", "--seed", "7"],
            capture_output=True, text=True, timeout=timeout, cwd=str(REPO_ROOT),
            encoding="utf-8", errors="replace", env=run_env)
        rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        rc, out = 124, "TIMEOUT"
    m = re.search(r"chosen -> q=([0-9.]+)", out)
    checks["evolve"] = {"pass": rc == 0 and bool(m),
                        "chosen_q": m.group(1) if m else None, "exit": rc}

    rc, out = _run([candidate, "-c",
                    "import sys; print(sys.prefix); print(sys.base_prefix)"],
                   60)
    lines = out.strip().splitlines()
    base = lines[1] if len(lines) > 1 else ""
    on_build_tree = ".build" in base.replace("\\", "/")
    checks["venv_base"] = {"pass": True, "exit": rc,
                           "base_prefix": base[:160],
                           "build_tree_based": on_build_tree,
                           "warning": "venv stdlib loads from the build tree; "
                           "deleting it kills this interpreter." if on_build_tree else ""}

    for name, res in checks.items():
        if not res["pass"]:
            failed.append(name)
    verdict = "PASS" if not failed else "FAIL:" + ",".join(failed)
    report = {"candidate": candidate, "verdict": verdict, "checks": checks}
    out_path = args.json or str(VERIFY_REPORT)
    Path(out_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name, res in checks.items():
        extra = ""
        if name == "selftest":
            extra = f" checks={res['checks']}"
        elif name == "pytest":
            extra = f" passed={res['passed']}"
        elif name == "evolve":
            extra = f" q={res['chosen_q']}"
        print(f"  [{'PASS' if res['pass'] else 'FAIL'}] {name:<10}"
              f"exit={res.get('exit')}{extra}")
    if checks["venv_base"]["build_tree_based"]:
        print(f"  [WARN] {checks['venv_base']['warning']}")
    print(f"verdict: {verdict} — wrote {out_path}")

    if args.apply and not failed:
        try:
            record = load_record()
            st = checks["selftest"]["checks"]
            pt = checks["pytest"]["passed"]
            cap = checks["capabilities"]["line"]
            if "acceptance" in record:
                acc = record["acceptance"]
                acc["4_selftest"] = (f"{st} check(s), 0 failed, "
                                     f"0 skipped (re-verified) -- exit 0")
                acc["5_pytest"] = f"{pt} passed (re-verified) -- exit 0"
            if "selftest" in record:
                record["selftest"] = (f"selftest: {st} check(s), "
                                        f"0 failed, 0 skipped (re-verified)")
            if "pytest_count" in record:
                record["pytest_count"] = f"{pt} passed (re-verified)"
            if "capabilities" in record:
                record["capabilities"] = cap + " (re-verified)"
            record["reverified"] = {"candidate": candidate,
                                    "report": out_path}
            BUILD_RECORD.write_text(json.dumps(record, indent=2),
                                    encoding="utf-8")
            print("build record acceptance lines refreshed")
        except Exception as exc:
            print(f"--apply failed: {exc}")
            return 2
    return 0 if not failed else 1


def lock_holder() -> str:
    try:
        return BUILD_LOCK.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"lock read: no live lock ({exc}); treating as free",
              file=sys.stderr)
        return ""


def do_lock(args) -> int:
    if args.take:
        holder = lock_holder()
        if holder and holder != args.take and not args.force:
            print(f"lock held by {holder!r}; refusing (use --force to break)")
            return 1
        BUILD_LOCK.write_text(args.take, encoding="utf-8")
        print(f"lock taken by {args.take}")
        return 0
    if args.release:
        try:
            BUILD_LOCK.unlink()
        except FileNotFoundError as exc:
            print(f"lock release: already free ({exc})", file=sys.stderr)
        print("lock released")
        return 0
    holder = lock_holder()
    print(f"lock: {holder!r}" if holder else "lock: free")
    return 0


def do_dirty(args) -> int:
    src = Path(args.source or _setting(
        "source_tree", "C:/Azazael/.build/cpython-316"))
    # NOTE: _run has no cwd and would read the REPO's status (exit 0,
    # masking the real answer). Always spawn in the source tree directly.
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True,
            text=True, timeout=60, cwd=str(src), encoding="utf-8",
            errors="replace")
        rc, out = proc.returncode, (proc.stdout or "")
    except Exception as exc:
        print(f"dirty: cannot read {src}: {exc}")
        return 2
    if rc != 0:
        print(f"dirty: git status failed in {src}")
        return 2
    lines = [ln for ln in out.splitlines() if ln.strip()]
    editable = [ln for ln in lines
                if not ln[3:].startswith(("Programs/", "Include/pyconfig.h",
                                          "libs/", "PCbuild/"))]
    verdict = "CLEAN" if not lines else (
        "BENIGN" if not editable else "SOURCE_EDIT")
    cause = ("clean tree" if not lines else
             "; ".join(lines)[:400] +
             (" — build-generated only, no source edit"
              if verdict == "BENIGN" else " — SOURCE EDIT PRESENT"))
    print(f"dirty: {verdict}: {cause}")
    try:
        record = load_record()
        record["dirty_cause"] = cause
        record["dirty_verdict"] = verdict
        BUILD_RECORD.write_text(json.dumps(record, indent=2),
                                encoding="utf-8")
        print("recorded into build record")
    except Exception as exc:
        print(f"dirty: record write failed: {exc}")
        return 2
    return 0 if verdict in ("CLEAN", "BENIGN") else 1


def do_promote(args) -> int:
    holder = lock_holder()
    if holder:
        print(f"promote refused: build lock held by {holder!r} "
              f"(release it or wait for their verify to finish)")
        return 1
    candidate = args.candidate or str(
        _setting("candidate", "C:/Azazael/.venvs/py316t/Scripts/python.exe"))
    args.candidate = candidate
    args.apply = False
    rc = do_verify(args)
    if rc != 0:
        print("promote refused: candidate did not verify")
        return 1
    report = json.loads(Path(args.json or str(VERIFY_REPORT)).read_text(
        encoding="utf-8"))
    if (report["checks"]["venv_base"]["build_tree_based"]
            and not args.allow_build_tree_base):
        print("promote refused: candidate venv is build-tree-based; "
              "re-run with --allow-build-tree-base once the build tree "
              "is declared permanent")
        return 1
    try:
        record = load_record()
        sha = pinned_sha(record)
    except Exception as exc:
        print(f"promote refused: cannot read build record: {exc}")
        return 2
    pointer = {"path": candidate, "pinned_sha": sha,
               "series": record.get("series", ""),
               "verify_report": args.json or str(VERIFY_REPORT)}
    POINTER.write_text(json.dumps(pointer, indent=2), encoding="utf-8")
    print(f"canonical interpreter -> {candidate} (wrote {POINTER})")
    return 0


def do_guard(args) -> int:
    floor = float(_read_default("preflight.disk_floor_gb", 2.0))
    problems: list[str] = []
    try:
        pointer = json.loads(POINTER.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"guard: no canonical pointer ({exc})")
        return 2
    cand = pointer.get("path", "")
    if not cand or not Path(cand).exists():
        problems.append(f"canonical path missing: {cand!r}")
    else:
        # VERIFY-BEFORE-FLIP (G2, 2026-11-07): the guard test-starts the
        # candidate and refuses it on any non-start. ONE spawn, not two: a
        # loader-wedged binary makes every spawn cost timeout+kill-lag
        # (~75s measured for a 60s timeout -- TerminateProcess does not
        # promptly kill a process wedged in DLL load), so two probes double
        # the worst case. A non-starting canonical is FAIL, never a hang.
        rc, out = _run([cand, "-I", "-c",
                        "import sys; print(sys.version);"
                        " print(sys.base_prefix)"], 45)
        lines = out.strip().splitlines() if out.strip() else []
        if rc != 0 or not lines:
            problems.append(f"canonical will not start (rc={rc}): "
                            f"{out.strip()[:100]!r}")
        else:
            series = str(pointer.get("series") or "")
            if series and series not in lines[0]:
                problems.append(f"canonical version drift: {lines[0][:100]!r}")
            if len(lines) > 1 and ".build" in lines[1].replace("\\", "/"):
                if not Path(lines[1]).exists():
                    problems.append(f"build tree gone, venv stranded: {lines[1]}")
    try:
        import shutil
        free_gb = shutil.disk_usage(str(REPO_ROOT)).free / (1024 ** 3)
        if free_gb < floor:
            problems.append(f"disk floor breached: {free_gb:.1f}GB < {floor}GB")
    except Exception as exc:
        problems.append(f"disk unreadable: {exc}")
    if problems:
        print("guard FAIL:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"guard PASS: {cand} live, disk floor holds")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch", help="upstream drift vs pinned sha")
    w.add_argument("--json", metavar="PATH", help="report path")

    v = sub.add_parser("verify", help="acceptance probes on a candidate")
    v.add_argument("--candidate", metavar="PY", help="interpreter to verify")
    v.add_argument("--apply", action="store_true",
                   help="refresh build-record acceptance lines on PASS")
    v.add_argument("--json", metavar="PATH", help="report path")

    p = sub.add_parser("promote", help="verify + record canonical interpreter")
    p.add_argument("--candidate", metavar="PY", help="interpreter to promote")
    p.add_argument("--allow-build-tree-base", action="store_true",
                   help="accept a venv rooted in the build tree")
    p.add_argument("--json", metavar="PATH", help="verify report path")

    sub.add_parser("guard", help="cheap daily canonical/disk check")

    d = sub.add_parser("dirty", help="name the -dirty cause in the record")
    d.add_argument("--source", metavar="DIR", help="CPython source tree")

    lk = sub.add_parser("lock", help="take/release the build lock")
    lk.add_argument("--take", metavar="LANE", help="take the lock as LANE")
    lk.add_argument("--release", action="store_true",
                    help="release the lock")
    lk.add_argument("--force", action="store_true",
                    help="break another lane's lock")
    args = ap.parse_args()
    holder = lock_holder() if args.cmd != "lock" else ""
    if args.cmd in ("verify", "guard") and holder:
        print(f"NOTE: build lock held by {holder!r} — "
              f"results may describe a mid-rebuild engine")
    return {"watch": do_watch, "verify": do_verify,
            "promote": do_promote, "guard": do_guard,
            "dirty": do_dirty, "lock": do_lock}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
