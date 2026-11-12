#!/usr/bin/env python3
"""DEEP DIVE — forensic audits and a web leg, ON by default, bounded, fallback-safe.

WHY THIS EXISTS
---------------
Two defaults were named together and they belong together: run forensic audits
without being asked, and look things up on the web without being asked. Both are
things a careful operator would do by hand before believing a result, and both
were being done from memory instead.

THE RULE THIS TOOL FOLLOWS
--------------------------
Every leg is bounded, and every leg can fail without taking the run down:

  * a leg that raises is recorded as `error` with the exception text;
  * a leg that exceeds its bound is recorded as `timeout` with the bound;
  * a leg that needs the network and does not have it is `skipped`, with the
    reason spelled out -- an unavailable reading is not a zero, and it is
    certainly not a pass;
  * the process exit code reflects only the FORENSIC legs. A missing web
    provider or an offline host is not a code defect, and a gate that fails on
    it would be switched off within a week.

MODES
-----
    python tools/deep_dive.py                 # forensics + web (settings-driven)
    python tools/deep_dive.py --forensics     # forensics only
    python tools/deep_dive.py --web           # web only
    python tools/deep_dive.py --query "..." --query "..."   # explicit queries
    python tools/deep_dive.py --offline       # force the web leg to fall back
    python tools/deep_dive.py --json out.json

Settings live in `config/defaults.json` (`deep_dive.*`); this tool reads them
through `tools/defaults_profile.py` when it can, and falls back to the same
values when it cannot.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "logs" / "deep_dive.json"
UA = "Mozilla/5.0 (compatible; azazael-deep-dive/1.0; +local)"

try:                                    # settings are optional, never required
    sys.path.insert(0, str(ROOT / "tools"))
    import defaults_profile as _defaults          # noqa: PLC0415
except Exception:                       # pragma: no cover - fallback path
    _defaults = None


def setting(dotted: str, fallback):
    if _defaults is None:
        return fallback
    try:
        return _defaults.read_default(dotted, fallback)
    except Exception:
        return fallback


# ── forensic legs ───────────────────────────────────────────────────────────
def leg_ast_audit(_payload):
    """Parse every file and report the shape of the tree (ast_audit.py).

    A file that will not parse is a defect with a name, so syntax errors are
    lifted out of the reading and into the verdict rather than left in a report
    nobody opens. Device-path skips are not counted -- ast_audit excludes them
    itself and lists them under `skipped_device_paths`.
    """
    out = ROOT / "logs" / "ast_audit_deep_dive.json"
    proc = subprocess.run([sys.executable, "tools/ast_audit.py", "--root", ".",
                           "--out", str(out), "--label", "deep-dive"],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if proc.returncode != 0:
        raise RuntimeError("ast_audit exited %s: %s"
                           % (proc.returncode, (proc.stderr or proc.stdout)[-400:]))
    data = json.loads(out.read_text(encoding="utf-8"))
    errors = data.get("syntax_errors") or []
    summary = {k: data.get(k) for k in ("py_files", "py_parsed", "loc", "classes",
                                        "functions", "methods", "orphan_count",
                                        "duplicate_groups", "markers_total")
               if data.get(k) is not None}
    summary["syntax_errors"] = len(errors)
    summary["bare_except"] = len(data.get("bare_except") or [])
    summary["except_pass"] = len(data.get("except_pass") or [])
    detail = "%d .py parsed, %d syntax error(s), %d bare-except, %d orphan module(s)" % (
        data.get("py_parsed", 0), len(errors),
        len(data.get("bare_except") or []), data.get("orphan_count", 0))
    if errors:
        raise RuntimeError(detail + ": " + ", ".join(
            (e.get("file") if isinstance(e, dict) else str(e)) for e in errors[:5]))
    return {"detail": detail, "summary": summary}


def leg_evidence_gate(_payload):
    """Re-derive the control and ledger claims from source."""
    proc = subprocess.run([sys.executable, "tools/evidence_gate.py"],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    lines = (proc.stdout or "").strip().splitlines()
    tail = lines[-3:] if lines else [(proc.stderr or "").strip()[-200:]]
    if proc.returncode != 0:
        raise RuntimeError("evidence_gate exited %s: %s"
                           % (proc.returncode, " | ".join(tail)))
    return {"detail": " | ".join(tail)}


def leg_golden_arrow_status(_payload):
    """Read the last full-suite run. No report means no reading, not a pass."""
    rep = ROOT / "logs" / "golden_arrow.json"
    if not rep.exists():
        raise RuntimeError("no logs/golden_arrow.json -- run tools/golden_arrow.py")
    data = json.loads(rep.read_text(encoding="utf-8"))
    totals = data.get("totals", {})
    return {"detail": "%d file(s), %d passed, %d failed, %d hung (run %s)"
                       % (totals.get("files", 0), totals.get("passed_files", 0),
                          totals.get("failed_files", 0), totals.get("hung_files", 0),
                          data.get("generated", "?")),
            "summary": totals}


# Keys are written out in full and read at a call site, never assembled as
# "hyper_edge." + name: settings_shadow resolves a read only when the reader is
# called with a literal string, so a computed key (or one parked in a table)
# leaves the knob looking untouched. Measured 2026-11-05: the first version put
# the keys in this table and the auditor reported all four as `orphan_knob` --
# its own finding class, fired by how this file asked for its settings.
def hyper_edge_flags() -> dict:
    return {
        "reading_audit": bool(setting("hyper_edge.reading_audit", True)),
        "settings_shadow": bool(setting("hyper_edge.settings_shadow", True)),
        "leg_ledger": bool(setting("hyper_edge.leg_ledger", True)),
        "head_audit": bool(setting("hyper_edge.head_audit", True)),
        "truncation": bool(setting("hyper_edge.truncation", True)),
        "ctypes_restype_audit": bool(setting("hyper_edge.ctypes_restype", True)),
        # Literal like the rest: a computed key would read as orphan_knob.
        "claim_agreement": bool(setting("hyper_edge.claim_agreement", True)),
    }


HYPER_EDGE_TOOLS = [
    ("reading_audit", "tools/reading_audit.py", "hyper_edge.reading_audit"),
    ("settings_shadow", "tools/settings_shadow.py", "hyper_edge.settings_shadow"),
    ("leg_ledger", "tools/leg_ledger.py", "hyper_edge.leg_ledger"),
    ("head_audit", "tools/head_audit.py", "hyper_edge.head_audit"),
    ("truncation", "tools/truncation_census.py", "hyper_edge.truncation"),
    ("ctypes_restype_audit", "tools/ctypes_restype_audit.py", "hyper_edge.ctypes_restype"),
    ("claim_agreement", "tools/claim_agreement.py", "hyper_edge.claim_agreement"),
]


def leg_hyper_edge(_payload):
    """HED-9..HED-12 -- the readings that decay unless something runs them.

    Each auditor is advisory on its own (findings printed, exit 0), so a tree
    carrying known debt still runs the leg and still reports; `hyper_edge.strict`
    turns findings into a failing leg, which is the same debt model HED-2 uses for
    the test baseline. `leg_ledger` audits the PREVIOUS logs/deep_dive.json, so on
    a first run there is nothing to audit -- that is `skipped`, not a pass and not
    a failure, because the report it would audit is being written by this run.
    """
    strict = bool(setting("hyper_edge.strict", False))
    leg_bound = float(setting("hyper_edge.leg_timeout_s", 300))
    flags = hyper_edge_flags()
    parts, failed, summary = [], [], {}
    for name, rel, key in HYPER_EDGE_TOOLS:
        if not flags[name]:
            parts.append("%s=off (%s=false)" % (name, key))
            summary[name] = "off"
            continue
        if name == "leg_ledger" and not REPORT.exists():
            parts.append("leg_ledger=skipped (no prior %s to audit)" % REPORT.name)
            summary[name] = "skipped"
            continue
        cmd = [sys.executable, rel] + (["--strict"] if strict else [])
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=leg_bound)
        lines = [line for line in (proc.stdout or "").strip().splitlines()
                 if line.strip()]
        first = lines[0] if lines else (proc.stderr or "").strip()[:200]
        # The auditors already prefix their own name; prefixing again produced
        # `reading_audit: reading_audit: 65 file(s)` in the first run.
        if first.startswith(name + ":"):
            first = first[len(name) + 1:].lstrip()
        summary[name] = {"exit": proc.returncode, "line": first}
        parts.append("%s: %s" % (name, first or "no output"))
        if proc.returncode != 0:
            failed.append("%s exit %s" % (name, proc.returncode))
    detail = "; ".join(parts)
    if failed:
        return {"status": "error", "summary": summary,
                "detail": detail + " | FAILED: " + ", ".join(failed)}
    return {"detail": detail, "summary": summary}


def leg_kernel(_payload):
    """The kernel evidence layer proves itself inside the default run.

    A layer that only proves itself when someone remembers the command is the
    "practice that depends on memory" HED-8 warns about. This leg runs the proof
    with the default workers so it is comparable to the recorded baseline, and a
    ratio below it fails the leg -- a native facility silently degrading is
    exactly the reading HED-3 says must not be folded into "ok".
    """
    if not bool(setting("hyper_edge.kernel", True)):
        return {"status": "skipped", "detail": "hyper_edge.kernel=false"}
    if not (ROOT / "src" / "HYPER_EDGE" / "kernel.py").exists():
        return {"status": "skipped", "detail": "HYPER_EDGE/kernel.py not in this tree"}
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + env["PYTHONPATH"]
                                              if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    bound = float(setting("hyper_edge.kernel_timeout_s", 180))
    proc = subprocess.run([sys.executable, "-m", "HYPER_EDGE.cli", "kernel"],
                          cwd=str(ROOT), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=bound)
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    verdict = next((line for line in lines if line.startswith("verdict:")), "")
    baseline = next((line for line in lines if line.startswith("baseline:")), "")
    if proc.returncode != 0 or not verdict:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
        return {"status": "error", "detail": (verdict or baseline or tail[0])[:300]}
    return {"detail": "%s | %s" % (verdict, baseline) if baseline else verdict}


FORENSIC_LEGS = [
    ("ast_audit", leg_ast_audit),
    ("evidence_gate", leg_evidence_gate),
    ("golden_arrow_status", leg_golden_arrow_status),
    ("hyper_edge", leg_hyper_edge),
    ("kernel", leg_kernel),
]


# ── the web leg ─────────────────────────────────────────────────────────────
def default_queries(limit: int) -> list:
    """Queries worth asking, derived from what is actually broken here.

    A web leg with no question is a leg that searches nothing and reports
    success. The failing test files are the honest source of questions: each one
    is a defect whose fix may already be documented upstream.
    """
    rep = ROOT / "logs" / "golden_arrow.json"
    out = []
    if rep.exists():
        try:
            data = json.loads(rep.read_text(encoding="utf-8"))
        except ValueError as exc:
            # A report that will not parse is a fact about the report. Swallowing
            # it would leave the web leg with no question and reporting success --
            # the leg searches nothing and says so nowhere.
            return ["golden_arrow.json unreadable (%s) -- what is broken here?"
                    % str(exc)[:60]]
        for row in data.get("failed", [])[:limit]:
            stem = Path(row.get("file", "") if isinstance(row, dict) else "").stem
            if stem:
                out.append("%s pytest failure" % stem.replace("test_", ""))
    return out[:limit]


def search_stacksx(query: str, timeout: float) -> dict:
    """Stack Overflow search via the public API -- keyless, 300 calls/day.

    This is the provider that actually answers a `pytest`-shaped question: the
    failures this corpus produces are Python defects, and the answer to a
    deterministic one is usually already on Stack Overflow with the version and
    the error text attached.
    """
    url = "https://api.stackexchange.com/2.3/search/advanced?" + urllib.parse.urlencode({
        "order": "desc", "sort": "relevance", "q": query,
        "site": "stackoverflow", "pagesize": 5})
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":                 # SE always gzips its JSON
        import gzip
        raw = gzip.decompress(raw)
    data = json.loads(raw.decode("utf-8", "replace"))
    hits = [{"title": it.get("title", "")[:120], "url": it.get("link", "")[:200]}
            for it in data.get("items", [])]
    return {"provider": "stackexchange", "hits": hits,
            "quota_remaining": data.get("quota_remaining")}


def search_ddg(query: str, timeout: float) -> dict:
    """DuckDuckGo HTML. Reports a CHALLENGE PAGE as blocked, never as 0 hits.

    Measured 2026-11-02: the HTML endpoint answers a scripted query with a
    14 KB anomaly/challenge page containing zero `result__a` anchors. The first
    version of this leg read that as "ok, 0 results" -- a blocked provider
    reported as an empty answer, which is the exact defect HED-3 names. A
    blocked page is `blocked` here, and the leg says so.
    """
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    found = re.findall(r'class="result__a"[^>]*href="(?P<u>[^"]+)"[^>]*>(?P<t>.*?)</a>',
                       body, re.S)
    hits = []
    for link, title in found[:5]:
        title = re.sub(r"<[^>]+>", "", title).strip()
        hits.append({"title": title[:120], "url": link[:200]})
    low = body.lower()
    blocked = not hits and ("anomaly" in low or "challenge" in low)
    return {"provider": "duckduckgo", "hits": hits, "blocked": blocked,
            "bytes": len(body)}


#: Provider chain, tried in order. First provider that returns hits answers the
#: question; every provider's outcome is recorded either way.
PROVIDERS = (("stackexchange", search_stacksx), ("duckduckgo", search_ddg))


def leg_web(payload):
    """Search every query through the provider chain. Never raises.

    Four outcomes, all recorded: `ok` (hits, with the provider named), `empty`
    (a provider answered and nothing matched), `skipped` (no provider answered --
    offline, or every one blocked), and `error` (the leg itself broke). Only the
    first two are statements about the world; the last two are statements about
    the instrument, and the detail text says which.
    """
    queries = payload["queries"]
    if not queries:
        return {"status": "skipped",
                "detail": "no queries: nothing to look up, so nothing was searched"}
    results, providers_used, blocked, errors = [], set(), [], []
    answered_clean = set()      # providers that responded and were NOT blocked
    for query in queries:
        answered = None
        for name, fn in PROVIDERS:
            try:
                got = fn(query, payload["timeout"])
            except (urllib.error.URLError, OSError, ValueError) as exc:
                errors.append("%s/%s: %s" % (name, query[:40], exc))
                continue
            if got.get("blocked"):
                blocked.append(name)
                continue
            answered_clean.add(name)
            if got["hits"]:
                answered = got
                providers_used.add(name)
                break
        results.append({"query": query, "hits": (answered or {}).get("hits", []),
                        "provider": (answered or {}).get("provider", "")})
    total = sum(len(r["hits"]) for r in results)
    if total:
        return {"detail": "%d hit(s) for %d query/queries via %s%s"
                          % (total, len(queries), ",".join(sorted(providers_used)),
                             "; blocked: %s" % ",".join(sorted(set(blocked))) if blocked else ""),
                "results": results, "errors": errors}
    if answered_clean:
        return {"status": "empty",
                "detail": "%s answered, 0 hits for %d query/queries -- an empty "
                          "answer, not a blocked one%s"
                          % (",".join(sorted(answered_clean)), len(queries),
                             "; blocked: %s" % ",".join(sorted(set(blocked))) if blocked else ""),
                "results": results}
    return {"status": "skipped",
            "detail": "no provider answered, fell back: blocked=%s errors=%s"
                      % (",".join(sorted(set(blocked))) or "none",
                         "; ".join(errors[:2]) or "none"),
            "results": results}


# ── runner ──────────────────────────────────────────────────────────────────
def run_leg(name, fn, payload, timeout):
    started = time.monotonic()
    try:
        if name == "web":
            out = fn(payload)
        else:
            out = dict(fn(payload))
            out.setdefault("status", "ok")
    except subprocess.TimeoutExpired:
        out = {"status": "timeout", "detail": "exceeded its bound"}
    except Exception as exc:                     # robust fallback, by design
        out = {"status": "error", "detail": "%s: %s" % (type(exc).__name__, exc)}
    out["leg"] = name
    out["seconds"] = round(time.monotonic() - started, 2)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--forensics", action="store_true", help="forensic legs only")
    ap.add_argument("--web", action="store_true", help="web leg only")
    ap.add_argument("--offline", action="store_true", help="force the web fallback")
    ap.add_argument("--query", action="append", default=[], help="explicit query")
    ap.add_argument("--json", default="",
                    help="where the report lands (default: fallback.record)")
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    enabled = bool(setting("deep_dive.enabled", True))
    cfg_forensics = bool(setting("deep_dive.forensics", True))
    cfg_web = bool(setting("deep_dive.web_search", True))
    do_forensics = enabled and cfg_forensics
    do_web = enabled and cfg_web
    why = []
    if args.forensics:
        do_web, why = False, why + ["--forensics suppressed the web leg"]
    if args.web:
        do_forensics, why = False, why + ["--web suppressed the forensic legs"]
    if args.offline:
        if do_web:
            why.append("--offline suppressed the web leg")
        do_web = False
    timeout = args.timeout or float(setting("deep_dive.web_timeout_s", 20))
    max_q = int(setting("deep_dive.max_queries", 3))
    fallback_mode = str(setting("fallback.on_error", "record-and-continue")).strip().lower()
    record = args.json or str(setting("fallback.record", str(REPORT)))
    out = Path(record)
    if not out.is_absolute():
        out = ROOT / out

    legs, results = [], []
    if do_forensics:
        for name, fn in FORENSIC_LEGS:
            if name == "ast_audit" and not bool(setting("ast_audit", True)):
                why.append("ast_audit leg off (ast_audit=false)")
                continue
            legs.append((name, fn))
    if do_web:
        queries = args.query or default_queries(max_q)
        legs.append(("web", leg_web))
    if not legs:
        print("DEEP DIVE: nothing ran. enabled=%r forensics=%r web_search=%r; %s. "
              "That is a setting or a flag, not a pass."
              % (enabled, cfg_forensics, cfg_web, "; ".join(why) or "no leg was enabled"))
        return 0

    payload = {"queries": args.query or default_queries(max_q), "timeout": timeout}
    started = time.monotonic()
    if fallback_mode == "abort":
        # `fallback.on_error=abort` is the operator saying one broken leg ends the
        # run; it is opt-in because the shipped rule is record-and-continue. Legs
        # after the failure are recorded as `skipped` with the reason, never left
        # out: a leg missing from the report reads exactly like a leg that passed.
        done = set()
        for name, fn in legs:
            row = run_leg(name, fn, payload, timeout)
            results.append(row)
            done.add(name)
            if row.get("status") in ("error", "timeout"):
                for rest, _fn in legs:
                    if rest not in done:
                        results.append({"leg": rest, "status": "skipped",
                                        "seconds": 0.0,
                                        "detail": "not run: fallback.on_error=abort "
                                                  "after %s" % name})
                break
    else:
        with ThreadPoolExecutor(max_workers=len(legs)) as pool:
            jobs = [pool.submit(run_leg, name, fn, payload, timeout)
                    for name, fn in legs]
            for job in jobs:
                results.append(job.result())
    elapsed = round(time.monotonic() - started, 1)

    forensic_failed = [r for r in results
                       if r["leg"] != "web" and r.get("status") in ("error", "timeout")]
    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_s": elapsed,
        "settings": {"enabled": enabled, "forensics": do_forensics, "web": do_web,
                     "timeout_s": timeout, "max_queries": max_q,
                     "fallback": fallback_mode, "suppressed": why},
        "legs": results,
        "verdict": "FAIL" if forensic_failed else "PASS",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print("DEEP DIVE -- %d leg(s) in %ss (forensics=%s web=%s)"
          % (len(results), elapsed, do_forensics, do_web))
    for row in results:
        print("  %-8s %-20s %6ss  %s"
              % (row.get("status", "ok"), row["leg"], row["seconds"],
                 row.get("detail", "")))
        if args.verbose and row.get("results"):
            for hit in row["results"]:
                print("             %s -> %d hit(s)" % (hit["query"], len(hit["hits"])))
                for h in hit["hits"][:3]:
                    print("               - %s" % h["title"][:80])
    print("verdict: %s   report: %s" % (report["verdict"], out))
    return 1 if forensic_failed else 0


if __name__ == "__main__":
    sys.exit(main())
