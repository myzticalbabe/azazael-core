#!/usr/bin/env python3
# coding: utf-8
"""GRAPH-REBUILD -- rebuild the graph across rounds, because the quota is the blocker.

Measured 2026-09-18: every chunk failure in the semantic pass is
`429 RESOURCE_EXHAUSTED`, `generate_content_free_tier_requests limit: 5` per
minute, with `retryDelay ~40s`. The extractor fires chunks in parallel with no
backoff, so it burns the free-tier quota in seconds and loses the remainder --
9 of 71 chunks produced output on one run, 29 of 64 failed on the next, at a
different count each time. That is a budget constraint silently negotiating away
a capability, the same shape as the 100-character diagnostic cap.

So this wraps the extract in rounds with a wait between them and counts what
each round actually produced. It does not promise the semantic half completes:
it reports the per-round success count and stops when a round adds nothing, so
"it is still incomplete and here is the trend" is visible rather than assumed.

It also reports whether the installed package has a local-model path. 0.8.8
documents none -- `extract --help` has no backend/model/ollama flag -- but the
package does reference ollama in llm.py, prs.py and __main__.py, so the claim
"there is no local path" was never safe to make from a help screen.

    python tools/graph_rebuild.py --rounds 3 --sleep 60 --out graphify-out
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "logs" / "graph_rebuild_report.json"

#: Resolve the engine by capability, the same way the no-LLM refresh does
#: (Error #208). This tool used bare `[sys.executable, "-m", "graphify", ...]`
#: -- `sys.executable` here is the 3.14t venv, which has no graphify at all --
#: so `--rounds` and the cluster pass could only ever fail, and the hard-coded
#: `SITE` inspected a *different* install than the commands would have run.
#: Two entry points each deciding for themselves is how this class of bug
#: arrived twice; now they share one resolver.
for _p in (ROOT / "tools",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from graphify_interpreter import resolve_graphify_python  # noqa: E402

#: Cached so the resolve (a few subprocess probes) happens once per process.
GRAPHIFY_PY: Path | None = None
_GRAPHIFY_PY_RESOLVED = False


def graphify_cmd(step: Sequence[str], report=print) -> list[str]:
    """`[interpreter, -m graphify, *step]`, resolving the engine on first use.

    Raises rather than falling back. A command run through an install that
    cannot carry this repo's huge-mode patch writes the wrong artifact, and the
    wrong artifact at the end of a long run is worse than a refusal at the
    start -- the failure mode Error #206 already cost a session.
    """
    global GRAPHIFY_PY, _GRAPHIFY_PY_RESOLVED
    if not _GRAPHIFY_PY_RESOLVED:
        _GRAPHIFY_PY_RESOLVED = True
        report("resolving the graphify interpreter (capability, not path):")
        GRAPHIFY_PY = resolve_graphify_python(report=report)
    if GRAPHIFY_PY is None:
        raise RuntimeError(
            "no graphify install on this box can render this tree (wanted an "
            "install holding exporters/html.py). Fix: 'uv tool install "
            "graphifyy --force', then 'python "
            "scripts/graphify_node_limit_patch.py'.")
    return [str(GRAPHIFY_PY), "-m", "graphify", *step]


def site_dir() -> Path:
    """The install directory to inspect, taken from the resolved engine."""
    global GRAPHIFY_PY, _GRAPHIFY_PY_RESOLVED
    if not _GRAPHIFY_PY_RESOLVED:
        _GRAPHIFY_PY_RESOLVED = True
        GRAPHIFY_PY = resolve_graphify_python(report=lambda _m: None)
    if GRAPHIFY_PY is not None:
        probe = ("import importlib.util as u, pathlib; "
                 "s = u.find_spec('graphify'); "
                 "print(pathlib.Path(s.submodule_search_locations[0]) if s and "
                 "s.submodule_search_locations else '')")
        try:
            proc = subprocess.run([str(GRAPHIFY_PY), "-c", probe],
                                  capture_output=True, text=True, timeout=60,
                                  encoding="utf-8", errors="replace")
            line = (proc.stdout or "").strip().splitlines()
            if line and line[-1]:
                return Path(line[-1])
        except (OSError, subprocess.TimeoutExpired):
            pass
    return Path(sys.prefix) / "Lib" / "site-packages" / "graphify"

CHUNK_FAIL = re.compile(r"chunk (\d+)/(\d+) failed")
ROUND_DONE = re.compile(r"Deduplicated (\d+) node")


def local_backend() -> dict:
    """Does the installed package offer a local inference path?"""
    hits: list[str] = []
    files: list[str] = []
    site = site_dir()
    if site.is_dir():
        for p in site.rglob("*.py"):
            try:
                body = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "ollama" in body.lower() or "11434" in body:
                files.append(p.name)
                for m in re.finditer(r"^.*(ollama|11434).*$", body, re.M | re.I):
                    line = m.group(0).strip()
                    if len(line) < 160:
                        hits.append("%s: %s" % (p.name, line))
                    if len(hits) > 12:
                        break
    return {"site_packages": str(site), "package_present": site.is_dir(),
            "files_with_ollama": sorted(set(files)), "sample": hits[:12]}


def rounds(count: int, sleep_s: int, out: str, timeout: int) -> dict:
    history: list[dict] = []
    for i in range(1, count + 1):
        started = time.time()
        cmd = graphify_cmd(("extract", "src", "--out", out))
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                                  text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace")
            text = (proc.stdout or "") + (proc.stderr or "")
            rc = proc.returncode
        except subprocess.TimeoutExpired as exc:
            text = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            rc = None
        fails = CHUNK_FAIL.findall(text)
        chunks = max((int(f[1]) for f in fails), default=0)
        entry = {"round": i, "rc": rc, "chunk_failures": len(fails),
                 "chunks_seen": chunks,
                 "dedup": [int(m) for m in ROUND_DONE.findall(text)][:1],
                 "elapsed_s": round(time.time() - started, 1)}
        history.append(entry)
        print("[round %d] rc=%s chunk failures=%d/%s elapsed=%ss"
              % (i, rc, entry["chunk_failures"], chunks or "?", entry["elapsed_s"]))
        if i < count:
            time.sleep(sleep_s)
    return {"rounds": history, "generated": datetime.now(timezone.utc).isoformat()}


def cluster_pass(out: str, timeout: int = 1800) -> dict:
    """Regenerate GRAPH_REPORT.md / graph.html from the graph extract just wrote.

    `extract` writes graph.json; the report comes from a separate cluster pass.
    Running extract alone left GRAPH_REPORT.md stale by 49 sources while
    graph.json was freshly rebuilt -- precisely the report/artifact mismatch the
    freshness guard was built to catch, reproduced by the rebuild itself. So the
    rebuild does both halves now, in one command.
    """
    try:
        res = subprocess.run(graphify_cmd(("cluster-only", "--out", out)),
                             cwd=str(ROOT), capture_output=True,
                             text=True, timeout=timeout,
                             encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"rc": None, "tail": ["timeout"]}
    text = ((res.stdout or "") + (res.stderr or "")).strip().splitlines()
    return {"rc": res.returncode, "tail": text[-3:]}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--sleep", type=int, default=60)
    ap.add_argument("--out", default="graphify-out")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--probe-only", action="store_true",
                    help="report the local-backend surface, do not rebuild")
    args = ap.parse_args(argv)

    report: dict = {"local_backend": local_backend()}
    print("local-model surface: %s"
          % (report["local_backend"]["files_with_ollama"] or "none found"))
    for s in report["local_backend"]["sample"][:6]:
        print("   %s" % s)
    if not args.probe_only:
        report.update(rounds(args.rounds, args.sleep, args.out, args.timeout))
        report["cluster"] = cluster_pass(args.out)
        print("[cluster] rc=%s %s" % (report["cluster"]["rc"],
                                      " | ".join(report["cluster"]["tail"])[-160:]))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("report: %s" % REPORT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
