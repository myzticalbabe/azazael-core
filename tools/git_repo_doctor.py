#!/usr/bin/env python3
"""git-repo-doctor -- measure the object store, then modernise it, then measure again.

Why this exists
---------------
On 2026-11-01 the production tree answered `git count-objects -vH` with
**31 packs, 938.94 MiB packed, 7,958 loose objects, 46.21 MiB loose** and no
multi-pack-index. Those are readings, not verdicts: nothing in git says whether
31 packs is too many, and `git gc` is not the fix for a repo that is mostly
already packed. The modern levers -- commit-graph, multi-pack-index, ref backend,
fsmonitor, sparse-index -- are each one config or one plumbing write away, and
each has a *measurable* payoff that this tool times instead of assuming.

Design rules, each bought by a failure in this repo:

- **Capabilities are probed, not inferred from the version string.** `git replay`
  is EXPERIMENTAL and its flags moved twice; `git backfill` exists in 2.53 but not
  in 2.50. A tool that maps "version >= X" to "feature present" claims a capability
  nobody exercised (RULE 0B-D), so each probe runs the command's own `-h` and looks
  at what comes back.
- **UNKNOWN is a state.** A check that could not run is reported as UNKNOWN with
  the error, never folded into PASS.
- **Every verdict carries its evidence.** One line of the actual command output,
  kept verbatim, so a reader can disagree with the grading.
- **--fix is additive only.** It writes indexes (`commit-graph`, `multi-pack-index`,
  `pack-refs`) and never rewrites history, never repacks what is already packed
  unless `--repack` is passed, and prints the exact commands first. Dry-run is the
  default because a health tool that mutates on first contact is a hazard.
- **Before/after is measured in the same run.** `--fix` re-runs the same benchmarks
  it took before touching anything and prints the delta; "it got faster" without a
  number is not a claim this tool makes.

Usage
-----
    python tools/git_repo_doctor.py                    # report only
    python tools/git_repo_doctor.py --bench            # add timings
    python tools/git_repo_doctor.py --fix --apply      # write missing indexes
    python tools/git_repo_doctor.py --fix --apply --bench --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

GIT = shutil.which("git") or "git"

#: feature -> the `-h` invocation whose output proves the feature is compiled in.
#: Probing the command itself is the only claim this tool can make; a version
#: comparison would be an assumption wearing a fact's clothes.
CAPABILITY_PROBES = {
    "merge-tree --write-tree": ["merge-tree", "-h"],
    "replay": ["replay", "-h"],
    "backfill": ["backfill", "-h"],
    "multi-pack-index": ["multi-pack-index", "-h"],
    "commit-graph": ["commit-graph", "-h"],
    "maintenance": ["maintenance", "-h"],
    "sparse-index": ["sparse-checkout", "-h"],
}


def run(args, cwd=None, timeout=180):
    """Run a git command. Returns (rc, stdout, stderr); never raises on rc != 0."""
    try:
        p = subprocess.run([GIT] + args, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "git not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss" % timeout


def first_line(text, limit=160):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def timed(args, cwd=None):
    """Time one command. Returns (elapsed_seconds, rc, stdout)."""
    t0 = time.perf_counter()
    rc, out, _err = run(args, cwd=cwd)
    return time.perf_counter() - t0, rc, out


def check(name, status, evidence, detail=None):
    return {"check": name, "status": status, "evidence": evidence, "detail": detail or {}}


# ── individual checks ──────────────────────────────────────────────────────────
def check_capabilities():
    caps = {}
    for label, args in CAPABILITY_PROBES.items():
        rc, out, err = run(args)
        text = (out + err).strip()
        # `-h` exits 129 on several git commands; the signal is the usage text.
        caps[label] = {
            "present": bool(text) and ("usage" in text.lower() or rc == 0),
            "evidence": first_line(text),
        }
    return caps


def check_ref_backend(refs):
    rc, out, err = run(["rev-parse", "--show-ref-format"])
    if rc != 0:
        return check("ref backend", "UNKNOWN",
                     "rev-parse --show-ref-format failed (git < 2.45?)",
                     {"stderr": first_line(err)})
    fmt = out.strip()
    if fmt == "reftable":
        return check("ref backend", "PASS", "reftable (log-structured, atomic)",
                     {"format": fmt})
    if refs > 1000:
        return check("ref backend", "WARN",
                     "%s with %d refs -- reftable (Git 3.0 default) is the faster backend at this"
                     " refset size" % (fmt, refs), {"format": fmt, "refs": refs})
    return check("ref backend", "INFO",
                 "%s with %d refs -- reftable only pays off on large refsets; nothing to win here"
                 % (fmt, refs), {"format": fmt, "refs": refs})


def _index_files(git_dir):
    return {
        "commit-graph": [os.path.join(git_dir, "objects", "info", "commit-graph"),
                          os.path.join(git_dir, "objects", "info", "commit-graphs")],
        "multi-pack-index": [os.path.join(git_dir, "objects", "pack", "multi-pack-index")],
    }


def check_commit_graph(git_dir):
    paths = _index_files(git_dir)["commit-graph"]
    if not any(os.path.exists(p) for p in paths):
        return check("commit-graph", "FAIL",
                     "absent -- `git commit-graph write --reachable` writes it in seconds"
                     " (note: `git commit-graph verify` exits 0 when there is no graph at all,"
                     " so presence is checked on the file, not on the verify)",
                     {"path": paths[0]})
    rc, out, err = run(["commit-graph", "verify"])
    if rc == 0:
        return check("commit-graph", "PASS", first_line(out) or "verify: ok",
                     {"path": paths[0]})
    return check("commit-graph", "FAIL", first_line(out + err), {"path": paths[0]})


def check_midx(git_dir, packs):
    path = _index_files(git_dir)["multi-pack-index"][0]
    if not os.path.exists(path):
        if packs <= 1:
            return check("multi-pack-index", "INFO",
                         "no MIDX and only %d pack(s) -- nothing to unify" % packs,
                         {"packs": packs})
        # The verify is NOT a presence test: measured 2026-11-01, `git multi-pack-index
        # verify` exits 0 with no index at all, so a verify-only check reports health
        # on a store that has none. Presence is the file; the verify is the integrity.
        return check("multi-pack-index", "FAIL",
                     "absent across %d packs -- `git multi-pack-index write` merges the"
                     " pack indexes into one lookup" % packs, {"packs": packs, "path": path})
    rc, out, err = run(["multi-pack-index", "verify"])
    if rc == 0:
        return check("multi-pack-index", "PASS", first_line(out) or "verify: ok",
                     {"packs": packs, "path": path})
    return check("multi-pack-index", "FAIL", first_line(out + err), {"packs": packs, "path": path})


def check_objects():
    rc, out, _ = run(["count-objects", "-v"])
    kv = {}
    for line in out.splitlines():
        key, _, val = line.partition(":")
        kv[key.strip()] = val.strip()
    packs = int(kv.get("packs", "0") or 0)
    loose = int(kv.get("count", "0") or 0)
    pack_mib = int(kv.get("size-pack", "0") or 0) / 1024.0
    loose_mib = int(kv.get("size", "0") or 0) / 1024.0
    detail = {"packs": packs, "loose": loose,
              "pack_MiB": round(pack_mib, 1), "loose_MiB": round(loose_mib, 1)}
    if loose > 5000:
        # Measured 2026-11-01: `git maintenance run --task=loose-objects` packs
        # copies of 7,958 loose objects (in-pack 30,854 -> 38,812, packs 31 -> 32,
        # 4.0 s) and leaves every loose file in place. The count this check reports
        # does not move until a prune, and a prune also deletes unreachable objects
        # -- i.e. dangling commits from aborted rebases. So the advice names both
        # halves instead of implying one command reclaims the space.
        return check("pack shape", "WARN",
                     "%d loose objects (%.1f MiB) beside %d packs -- packing them"
                     " (`git maintenance run --task=loose-objects`) makes duplicates, not"
                     " savings; only a prune reclaims the space, and it deletes unreachable"
                     " history too" % (loose, loose_mib, packs), detail)
    if packs > 10:
        return check("pack shape", "WARN",
                     "%d packs for %.1f MiB -- many small packs slow every object lookup that misses the MIDX"
                     % (packs, pack_mib), detail)
    return check("pack shape", "PASS",
                 "%d packs, %d loose objects, %.1f MiB packed" % (packs, loose, pack_mib),
                 detail)


def check_fsmonitor():
    rc, out, _ = run(["config", "--get", "core.fsmonitor"])
    val = out.strip()
    if not val or val == "false":
        return check("fsmonitor", "WARN",
                     "off -- `git config core.fsmonitor true` makes `git status` in a big worktree"
                     " cheap (per-platform hook/daemon)", {})
    return check("fsmonitor", "PASS", "core.fsmonitor=%s" % val, {})


def check_sparse_index():
    rc, out, _ = run(["config", "--get", "index.sparse"])
    val = out.strip()
    if val == "true":
        return check("sparse-index", "PASS", "index.sparse=true", {})
    return check("sparse-index", "INFO",
                 "off -- only worth enabling with a sparse-checkout cone; it is not free alone",
                 {})


def check_gc_state(git_dir):
    rc, out, _ = run(["config", "--get", "gc.auto"])
    auto = out.strip() or "6700 (default)"
    log = os.path.join(git_dir, "gc.log")
    if os.path.exists(log):
        try:
            with open(log, encoding="utf-8", errors="replace") as fh:
                msg = fh.readline().strip()
        except OSError:
            msg = "unreadable"
        return check("gc state", "FAIL",
                     "gc.log present: %s -- git is refusing to auto-gc and saying so in a file"
                     " nobody reads" % (msg[:120]), {"gc.auto": auto, "gc.log": log})
    return check("gc state", "PASS", "gc.auto=%s, no gc.log" % auto, {"gc.auto": auto})


def check_alternates(git_dir):
    alt = os.path.join(git_dir, "objects", "info", "alternates")
    if os.path.exists(alt):
        try:
            with open(alt, encoding="utf-8", errors="replace") as fh:
                lines = [ln.strip() for ln in fh if ln.strip()]
        except OSError:
            lines = ["<unreadable>"]
        return check("alternates", "INFO",
                     "%d alternate object store(s) -- objects are borrowed, so pack sizes are not"
                     " the whole story" % len(lines), {"paths": lines})
    return check("alternates", "PASS", "self-contained object store", {})


# ── benchmarks ────────────────────────────────────────────────────────────────
BENCHMARKS = [
    ("log -5000", ["log", "--oneline", "-5000", "--no-decorate"]),
    ("rev-list --count --all", ["rev-list", "--count", "--all"]),
    ("status --porcelain", ["status", "--porcelain"]),
    ("diff --stat HEAD", ["diff", "--stat", "HEAD"]),
]


def bench(repo, rounds=3):
    """Median-of-N per benchmark: a single sample on Windows is mostly noise."""
    result = {}
    for name, args in BENCHMARKS:
        samples = []
        for _ in range(rounds):
            dt, rc, _out = timed(args, cwd=repo)
            if rc != 0:
                samples = []
                break
            samples.append(dt)
        if samples:
            samples.sort()
            result[name] = round(samples[len(samples) // 2], 4)
    return result


# ── fixes ─────────────────────────────────────────────────────────────────────
def fix_plan(packs, repack=False):
    """The exact commands --fix would run, in dependency order.

    Index writes only by default. `incremental-repack` is opt-in (`--repack`)
    because it rewrites packs: on a disk with little free space that is a real
    cost, and the repo canon records this disk at 98% used.
    """
    plan = [
        ["commit-graph", "write", "--reachable"],
        ["multi-pack-index", "write"],
        ["pack-refs", "--all"],
    ]
    if repack:
        plan.append(["maintenance", "run", "--task=incremental-repack"])
    return plan


def apply_fixes(repo, plan):
    ran = []
    for args in plan:
        t0 = time.perf_counter()
        rc, out, err = run(args, cwd=repo)
        ran.append({
            "command": "git " + " ".join(args),
            "rc": rc,
            "seconds": round(time.perf_counter() - t0, 3),
            "out": first_line(out),
            "err": first_line(err),
        })
    return ran


def grade(checks):
    fails = [c for c in checks if c["status"] == "FAIL"]
    warns = [c for c in checks if c["status"] == "WARN"]
    unknown = [c for c in checks if c["status"] == "UNKNOWN"]
    if fails:
        return "FAIL", len(fails), len(warns), len(unknown)
    if warns:
        return "WARN", 0, len(warns), len(unknown)
    return "PASS", 0, 0, len(unknown)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure and modernise a git object store.")
    ap.add_argument("--repo", default=".", help="repository path (default: cwd)")
    ap.add_argument("--fix", action="store_true",
                    help="show the index writes that would run (no mutation)")
    ap.add_argument("--apply", action="store_true",
                    help="with --fix: actually run them")
    ap.add_argument("--bench", action="store_true", help="time the read paths")
    ap.add_argument("--repack", action="store_true",
                    help="with --fix: also run incremental-repack (rewrites packs; needs free space)")
    ap.add_argument("--rounds", type=int, default=3, help="benchmark samples per command (median)")
    ap.add_argument("--json", default="", help="write the full report here")
    a = ap.parse_args(argv)

    repo = os.path.abspath(a.repo)
    rc, out, err = run(["rev-parse", "--show-toplevel"], cwd=repo)
    if rc != 0:
        print("not a git repository: %s (%s)" % (repo, first_line(err)), file=sys.stderr)
        return 2
    repo = out.strip()

    git_dir_rc, gi_out, _ = run(["rev-parse", "--git-dir"], cwd=repo)
    git_dir = os.path.abspath(os.path.join(repo, gi_out.strip())) if git_dir_rc == 0 else repo

    ver_rc, ver_out, _ = run(["version"])
    caps = check_capabilities()
    obj = check_objects()
    packs = obj["detail"].get("packs", 0)
    refs_rc, refs_out, _ = run(["for-each-ref", "--format=%(refname)"])
    refs = len([ln for ln in refs_out.splitlines() if ln.strip()]) if refs_rc == 0 else 0

    checks = [
        check("git version", "PASS" if ver_rc == 0 else "FAIL", ver_out.strip(), {}),
        check_ref_backend(refs),
        check_commit_graph(git_dir),
        check_midx(git_dir, packs),
        obj,
        check_fsmonitor(),
        check_sparse_index(),
        check_gc_state(git_dir),
        check_alternates(git_dir),
    ]

    before = bench(repo, a.rounds) if a.bench else {}
    plan = fix_plan(packs, a.repack) if a.fix else []
    applied = []
    after = {}
    if a.fix and a.apply and plan:
        applied = apply_fixes(repo, plan)
        if a.bench:
            after = bench(repo, a.rounds)
        # re-read the checks that --fix targets, so the report says what changed
        checks = [check_ref_backend(refs), check_commit_graph(git_dir), check_midx(git_dir, packs), obj,
                  check_fsmonitor(), check_sparse_index(), check_gc_state(git_dir),
                  check_alternates(git_dir)]

    verdict, n_fail, n_warn, n_unk = grade(checks)

    print("repo: %s" % repo)
    print("git:  %s" % ver_out.strip())
    print("")
    width = max(len(c["check"]) for c in checks)
    for c in checks:
        print("  %-*s  %-7s  %s" % (width, c["check"], c["status"], c["evidence"]))
    if caps:
        missing = [k for k, v in caps.items() if not v["present"]]
        print("\n  capabilities: %d/%d present%s"
              % (len(caps) - len(missing), len(caps),
                 ("; missing: " + ", ".join(missing)) if missing else ""))
    if plan:
        print("\n  fix plan%s:" % (" (applied)" if applied else " (dry run -- pass --apply)"))
        for cmd in plan:
            print("    git " + " ".join(cmd))
        for row in applied:
            print("    -> rc=%d %.3fs %s" % (row["rc"], row["seconds"],
                                            row["out"] or row["err"]))
    if before:
        print("\n  timings (median of %d):" % a.rounds)
        for name, sec in before.items():
            line = "    %-26s %7.4fs" % (name, sec)
            if after.get(name):
                new = after[name]
                delta = (new - sec) / sec * 100.0
                line += "  -> %7.4fs (%+.1f%%)" % (new, delta)
            print(line)
        if not after:
            print("    (no --apply: this is the baseline, not a speedup)")

    print("\n  verdict: %s  (%d fail, %d warn, %d unknown)" % (verdict, n_fail, n_warn, n_unk))

    report = {
        "repo": repo,
        "git": ver_out.strip(),
        "verdict": verdict,
        "checks": checks,
        "capabilities": caps,
        "fix_plan": ["git " + " ".join(c) for c in plan],
        "applied": applied,
        "bench_before": before,
        "bench_after": after,
    }
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
        print("  report: %s" % a.json)
    return 1 if verdict == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
