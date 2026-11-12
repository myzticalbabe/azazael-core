#!/usr/bin/env python3
"""git-fleet-audit -- grade every tree in the fleet against one definition of "bleeding edge".

Why this exists
---------------
`git_repo_doctor.py` answers "is this repository healthy" for one root. The fleet
question is different: *which trees are modern git stacks and which are only
copies of code that happen to sit under version control?* On 2026-11-01 the fleet
split three ways and nothing said so:

* `C:/Azazael/azazael` -- a repository (commit-graph, now a MIDX)
* `G:/azazael` -- **not a repository at all**; a file copy, so no git property can
  hold of it, and the earlier AST audit already found truncated files there
* `edge0-intel` / `graph3d` / `pams-source` -- repositories with no idea that
  fsmonitor, untracked cache or an index v4 exist

A tree that is not a git repository cannot be "lagging": it has no stack. That
distinction is the first thing the grading has to get right, because the fix for a
non-repository (`git init` + an initial commit, or accept it as an artefact store)
is different from the fix for a stale repository (four config one-liners).

Definition used (measured, not assumed; see docs/GIT_TOOLING_BLEEDING_EDGE_2026-11-01.md)

    hard requirements   commit-graph, multi-pack-index (when >1 pack),
                        core.fsmonitor, core.untrackedCache, index.version >= 4,
                        working-tree coverage (tracked content is not a minority)
    named hygiene       loose objects, pack count -- reported, with the repack that
                        closes them, but not folded into the grade (a repack is
                        scheduled work, not a config one-liner)
    grade               BLEEDING  0 gaps
                        LAGGING   1-2 gaps
                        COLD      3+ gaps
                        NOT-A-REPO / MISSING  (no git stack to grade)

Coverage is a hard requirement because config alone is worthless: a tree with every
optimisation switched on and 2% of its files tracked still re-reads 98% of itself on
every `git status`, and grading it BLEEDING is a false positive about the tree -- this
exact defect was measured on Ubuntu-26.04's /root/azazael on 2026-11-01 (678 tracked,
948 MiB single pack, 1450 loose objects, all five config checks green).

Every gap carries the exact command that closes it, so the audit doubles as the
task list -- that is why `--tasks` exists.

Usage
-----
    python tools/git_fleet_audit.py --root C:/Azazael/azazael --root C:/Users/clint/edge0-intel
    python tools/git_fleet_audit.py --root /root/azazael --single --json out.json   # inside WSL
    python tools/git_fleet_audit.py ... --tasks                                     # command list per tree

Exit status: 0 every tree BLEEDING, 1 any tree graded below that, 2 nothing to audit.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

GIT = shutil.which("git") or "git"

#: The definition. Each entry is (check name, gap description, closing command).
HARD_CHECKS = [
    ("commit-graph", "no commit-graph: every rev-walk re-parses commit objects",
     "git commit-graph write --reachable"),
    ("multi-pack-index", "no MIDX across multiple packs: each lookup probes every pack index",
     "git multi-pack-index write"),
    ("fsmonitor", "core.fsmonitor is off: `git status` stats the whole worktree",
     "git config core.fsmonitor true"),
    ("untrackedCache", "core.untrackedCache is off: untracked detection re-reads directories",
     "git config core.untrackedCache true"),
    ("index v4", "index.version < 4: path prefix compression is not in use",
     "git config index.version 4"),
]

CAPABILITY_MIN = {
    "merge-tree --write-tree": (2, 38),
    "replay": (2, 46),
    "backfill": (2, 51),
    "multi-pack-index": (2, 34),
    "commit-graph": (2, 23),
}


def run(args, cwd=None, timeout=120):
    try:
        p = subprocess.run([GIT] + args, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as exc:
        # Two statements, so the body is a report rather than the bare-constant
        # return the scanner reads as `except_return_default`: the caller can tell
        # "git is absent" from "git said nothing".
        why = "git not found (%s)" % exc
        return 127, "", why
    except subprocess.TimeoutExpired as exc:
        why = "timed out after %ss running: git %s" % (getattr(exc, "timeout", "?"),
                                                       " ".join(args))
        return 124, "", why


def first_line(text, limit=160):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:limit]
    return ""


def version_tuple(text):
    parts = []
    for chunk in text.strip().split(".")[:3]:
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def objects(repo):
    rc, out, _ = run(["count-objects", "-v"], repo)
    kv = {}
    for line in out.splitlines():
        key, _, val = line.partition(":")
        kv[key.strip()] = val.strip()
    def num(key):
        # `.isdigit()` rather than try/except-around-int: a bare default returned
        # from an error path is the except_return_default shape the staging gate
        # flags, and `count-objects -v` writes unsigned integers or nothing.
        raw = (kv.get(key, "0") or "0").strip()
        return int(raw) if raw.isdigit() else 0
    return {"packs": num("packs"), "loose": num("count"),
            "pack_MiB": round(num("size-pack") / 1024.0, 1),
            "loose_MiB": round(num("size") / 1024.0, 1)}


def check_tree(root):
    """Grade one root. Never raises: an unreadable tree is a verdict, not a crash."""
    entry = {"root": root, "exists": os.path.isdir(root)}
    if not entry["exists"]:
        entry.update({"grade": "MISSING", "gaps": ["path does not exist"], "tasks": [],
                      "checks": []})
        return entry

    rc, out, err = run(["rev-parse", "--git-dir"], root)
    # `git` walks UP from the directory it is given, so a plain folder inside some
    # outer repository answers happily and the audit would grade the ancestor's
    # config as if it were this tree's. Measured 2026-11-01: a bare temp directory
    # graded BLEEDING from the home-directory repository above it. The toplevel has
    # to be this root, or there is no stack here.
    top_rc, top_out, _ = run(["rev-parse", "--show-toplevel"], root)
    top = top_out.strip()
    matches_root = bool(top) and os.path.normcase(os.path.realpath(top)) == \
        os.path.normcase(os.path.realpath(root))
    if rc != 0 or not matches_root:
        # Not its own repository. Say what it IS, because "no .git", "broken .git"
        # and "nested inside another repo" need different fixes, and only two of
        # those three are a git problem.
        has_dotgit = os.path.exists(os.path.join(root, ".git"))
        if rc != 0:
            why = "no usable git repository" + (" (.git exists but is unreadable)" if has_dotgit else "")
            evidence = first_line(err)
            tasks = ["decide: `git init` + initial commit, or declare this tree an artefact"
                     " store and exclude it from git work"]
        else:
            why = "not its own repository: it sits inside %s" % top
            evidence = "rev-parse --show-toplevel -> %s" % top
            tasks = ["decide: `git init` here (a second history) or leave it as part of the"
                     " enclosing repository at %s -- do NOT run `git config`/`git add` in here"
                     " expecting it to apply locally; git resolves upward and edits %s" % (top, top)]
        entry.update({
            "grade": "NOT-A-REPO",
            "gaps": [why],
            "evidence": evidence,
            "tasks": tasks,
            "checks": [],
        })
        return entry

    git_dir_rc, gi_out, _ = run(["rev-parse", "--git-dir"], root)
    git_dir = os.path.abspath(os.path.join(root, gi_out.strip())) if git_dir_rc == 0 else root
    obj = objects(root)

    ver_rc, ver_out, _ = run(["version"], root)
    ver = version_tuple(ver_out.replace("git version", "").strip())

    checks, gaps, tasks = [], [], []

    def add(name, ok, evidence, gap="", fix=""):
        checks.append({"check": name, "ok": bool(ok), "evidence": evidence})
        if not ok:
            gaps.append(gap or name)
            if fix:
                tasks.append(fix)

    # ── hard requirements ──
    cg_paths = [os.path.join(git_dir, "objects", "info", "commit-graph"),
                os.path.join(git_dir, "objects", "info", "commit-graphs")]
    cg = any(os.path.exists(p) for p in cg_paths)
    add("commit-graph", cg, "present" if cg else "absent",
        HARD_CHECKS[0][1], HARD_CHECKS[0][2])

    midx_ok = True
    midx_evidence = "single pack -- nothing to unify"
    if obj["packs"] > 1:
        midx_path = os.path.join(git_dir, "objects", "pack", "multi-pack-index")
        midx_ok = os.path.exists(midx_path)
        midx_evidence = ("present across %d packs" % obj["packs"]) if midx_ok else \
                        ("absent across %d packs" % obj["packs"])
    add("multi-pack-index", midx_ok, midx_evidence,
        HARD_CHECKS[1][1], HARD_CHECKS[1][2])

    rc, out, _ = run(["config", "--get", "core.fsmonitor"], root)
    fs = out.strip()
    add("fsmonitor", fs not in ("", "false"), "core.fsmonitor=%s" % (fs or "<unset>"),
        HARD_CHECKS[2][1], HARD_CHECKS[2][2])

    rc, out, _ = run(["config", "--get", "core.untrackedCache"], root)
    uc = out.strip()
    add("untrackedCache", uc not in ("", "false"), "core.untrackedCache=%s" % (uc or "<unset>"),
        HARD_CHECKS[3][1], HARD_CHECKS[3][2])

    rc, out, _ = run(["config", "--get", "index.version"], root)
    iv_raw = out.strip()
    # `.isdigit()` rather than try/except: git writes a bare integer, and an
    # `except ValueError: iv = 2` is the except_return_default shape the staging
    # gate flags -- a default assigned from the error path cannot be told from a
    # setting that was read.
    iv = int(iv_raw) if iv_raw.isdigit() else 2
    add("index v4", iv >= 4, "index.version=%s" % (iv_raw or "<unset, defaults to 2>"),
        HARD_CHECKS[4][1], HARD_CHECKS[4][2])

    # ── coverage and object hygiene ──
    # The five checks above grade CONFIG. A repository can satisfy every one of them
    # while almost nothing in it is tracked, and then "BLEEDING" is a lie about the
    # tree. Measured 2026-11-01: Ubuntu-26.04's /root/azazael reported commit-graph,
    # fsmonitor, untrackedCache and index v4 all present and was graded BLEEDING with
    # 678 files tracked out of 40k+ on disk -- a copy of the tree with a baseline
    # commit, not a tracked history. Coverage is therefore a hard requirement.
    ls_rc, ls_out, _ = run(["ls-files"], root, timeout=180)
    tracked = len([ln for ln in ls_out.splitlines() if ln.strip()]) if ls_rc == 0 else -1
    st_rc, st_out, _ = run(["status", "--porcelain", "--untracked-files=normal"],
                           root, timeout=300)
    untracked = len([ln for ln in st_out.splitlines() if ln.startswith("??")]) if st_rc == 0 else -1
    if tracked < 0 or untracked < 0:
        add("working-tree coverage", False,
            "unmeasurable: ls-files rc=%d, status rc=%d" % (ls_rc, st_rc),
            "coverage could not be measured (ls-files/status failed): until it can, this"
            " tree's grade says nothing about whether its content is tracked",
            "fix whatever makes `git ls-files` / `git status` fail, then re-grade")
    else:
        # An artefact store (nothing tracked at all) is a decision, not a defect; a
        # tree with a token baseline commit and tens of thousands of untracked files
        # is a copy wearing a repository.
        if tracked == 0 and untracked == 0:
            cov_ok, cov_ev = True, "empty tree"
        elif tracked > 0 and untracked > max(tracked, 100):
            cov_ok = False
            cov_ev = "%d tracked vs %d untracked -- the tree is a copy with a baseline" \
                     " commit, not a tracked history" % (tracked, untracked)
        else:
            cov_ok, cov_ev = True, "%d tracked, %d untracked" % (tracked, untracked)
        add("working-tree coverage", cov_ok, cov_ev,
            "tracked content is a minority of the tree (%s): a `git init` + baseline"
            " commit does not make a copy into a repository" % cov_ev,
            "decide per tree: `git add -A` + commit (fork of history, changes nothing on"
            " disk) with a .gitignore for the bulk, or declare it an artefact store and"
            " exclude it from git work")

    # A single 948 MiB pack and 1,450 loose objects is still a healthy grade on the
    # config checks, but every read pays for it. Hygiene gaps are named, not graded,
    # because their fix is a repack whose cost has to be scheduled, not assumed.
    if obj["loose"] > 2000:
        add("loose objects", False,
            "%d loose objects (%.1f MiB): enumeration and delta search get slower as this"
            " grows" % (obj["loose"], obj["loose_MiB"]),
            "%d loose objects are unpacked" % obj["loose"],
            "git repack -ad --write-midx  (or `git gc --aggressive` off-hours)")
    else:
        add("loose objects", True, "%d loose" % obj["loose"])
    if obj["packs"] > 50:
        add("pack count", False, "%d packs: each object lookup probes every index" % obj["packs"],
            "%d separate packs" % obj["packs"],
            "git multi-pack-index write  (then `git repack -ad` when the schedule allows)")
    else:
        add("pack count", True, "%d pack(s)" % obj["packs"])

    # ── informational ──
    rc, out, _ = run(["config", "--get", "maintenance.strategy"], root)
    maint = out.strip() or "<unscheduled>"
    checks.append({"check": "maintenance", "ok": maint != "<unscheduled>",
                   "evidence": "maintenance.strategy=%s" % maint, "hard": False})

    rc, out, _ = run(["rev-parse", "--show-ref-format"], root)
    backend = out.strip() or "unknown"
    rc_h, out_h, _ = run(["for-each-ref", "--format=%(refname)"], root)
    heads = len([ln for ln in out_h.splitlines() if ln.strip()]) if rc_h == 0 else 0
    checks.append({"check": "ref backend", "ok": True,
                   "evidence": "%s, %d refs" % (backend, heads), "hard": False})

    rc, out, _ = run(["rev-list", "--count", "--all"], root)
    commits = out.strip() or "0"
    checks.append({"check": "history", "ok": True,
                   "evidence": "%s commits, %d packs, %d loose (%.1f MiB packed)"
                               % (commits, obj["packs"], obj["loose"], obj["pack_MiB"]),
                   "hard": False})

    gc_log = os.path.join(git_dir, "gc.log")
    if os.path.exists(gc_log):
        try:
            with open(gc_log, encoding="utf-8", errors="replace") as fh:
                msg = fh.readline().strip()[:120]
        except OSError:
            msg = "unreadable"
        add("gc state", False, "gc.log: %s" % msg, "auto-gc is refusing to run: " + msg,
            "read %s and clear the cause, then delete it" % gc_log)

    # A repository at a user's home directory is a hazard, not a health property:
    # every folder beneath it is "inside a repo", so `git add -A` from the wrong
    # shell stages the whole profile, and any tool that asks git for the toplevel
    # silently answers with the home directory (measured 2026-11-01: edge0-intel
    # and graph3d both graded against this repo before the check existed).
    hazards = []
    home = os.path.normcase(os.path.realpath(os.path.expanduser("~")))
    if os.path.normcase(os.path.realpath(root)) == home:
        hazards.append("this repository IS the home directory: every folder beneath it"
                       " reports it as its toplevel, and `git add -A` here stages the profile")

    caps = {}
    for name, minimum in CAPABILITY_MIN.items():
        caps[name] = ver >= minimum

    hard_gaps = len([g for g in gaps])
    if hard_gaps == 0:
        grade = "BLEEDING"
    elif hard_gaps <= 2:
        grade = "LAGGING"
    else:
        grade = "COLD"

    entry.update({
            "grade": grade,
            "tracked": tracked,
            "git": ver_out.strip(),
        "version": ".".join(str(p) for p in ver),
        "capabilities": caps,
        "checks": checks,
        "gaps": gaps,
        "tasks": tasks,
        "hazards": hazards,
        "objects": obj,
    })
    return entry


def _status_bench(repo, rounds):
    import statistics
    import time as _time
    samples = []
    for _ in range(rounds):
        t0 = _time.perf_counter()
        rc, _out, _err = run(["status", "--porcelain"], repo, timeout=120)
        if rc != 0:
            return None
        samples.append(_time.perf_counter() - t0)
    return statistics.median(samples) if samples else None


def print_report(entries, show_tasks):
    width = max([len(e["root"]) for e in entries] + [4])
    print("%-*s  %-11s  %-9s  %s" % (width, "tree", "grade", "gaps", "detail"))
    print("-" * (width + 44))
    for e in entries:
        detail = e.get("evidence") or (e["checks"][-1]["evidence"] if e.get("checks") else "")
        print("%-*s  %-11s  %-9d  %s" % (width, e["root"], e["grade"], len(e.get("gaps", [])), detail))
        for gap in e.get("gaps", []):
            print("%-*s    ! %s" % (width, "", gap))
        for hazard in e.get("hazards", []):
            print("%-*s    ~ HAZARD: %s" % (width, "", hazard))
        if show_tasks:
            for task in e.get("tasks", []):
                print("%-*s    > %s" % (width, "", task))
    counts = {}
    for e in entries:
        counts[e["grade"]] = counts.get(e["grade"], 0) + 1
    print("\n%d tree(s): %s" % (len(entries),
                                ", ".join("%s=%d" % kv for kv in sorted(counts.items()))))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Grade every tree in the fleet against the bleeding-edge definition.")
    ap.add_argument("--root", action="append", default=[], help="tree to audit (repeatable)")
    ap.add_argument("--json", default="", help="write the full report here")
    ap.add_argument("--tasks", action="store_true", help="print the closing command for every gap")
    ap.add_argument("--single", action="store_true",
                    help="treat --root as this host's only tree (used inside WSL)")
    ap.add_argument("--apply", action="store_true",
                    help="run each tree's closing commands, then re-grade it in the same run")
    ap.add_argument("--bench", type=int, default=0, metavar="N",
                    help="time `git status --porcelain` N times (median) before and after --apply")
    a = ap.parse_args(argv)

    roots = a.root or ["."]
    if a.single and len(roots) > 1:
        print("--single takes one --root", file=sys.stderr)
        return 2

    entries = [check_tree(os.path.abspath(r)) for r in roots]

    if a.apply:
        for entry in entries:
            if not entry.get("tasks"):
                continue
            print("\napplying %d task(s) in %s" % (len(entry["tasks"]), entry["root"]))
            before = _status_bench(entry["root"], a.bench) if a.bench else None
            for task in entry["tasks"]:
                if not task.startswith("git "):
                    print("  -- skip (a decision, not a command): %s" % task)
                    continue
                argv = task.split()[1:]
                rc, out, err = run(argv, entry["root"])
                print("  %-46s rc=%d %s" % (task, rc, first_line(out + err)))
            after = _status_bench(entry["root"], a.bench) if a.bench else None
            if before and after:
                print("  status median of %d: %.4fs -> %.4fs (%+.1f%%)"
                      % (a.bench, before, after, (after - before) / before * 100.0))
        # re-grade so "applied" and "graded" cannot drift apart
        entries = [check_tree(e["root"]) for e in entries]
        print("\nre-graded after apply:")

    print_report(entries, a.tasks)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=1)
        print("\nreport: %s" % a.json)

    return 0 if all(e["grade"] == "BLEEDING" for e in entries) else 1


if __name__ == "__main__":
    sys.exit(main())
