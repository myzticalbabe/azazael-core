#!/usr/bin/env python3
"""Polyglot router + golden self-test (arena-64n shard_node shape).

`run_shard(lang)` executes the golden snippet for one language through the
INTERPRETERS dispatcher and returns a JSON-serializable result (no weights,
no handles across the boundary). `self_test()` cold-recomputes every
available toolchain and asserts stdout agreement on the golden token.
A missing toolchain is a clean skip, never a failure.

Golden contract: the snippet prints exactly AZAZAEL-GOLDEN-OK on stdout
and exits 0. stdout agreement across languages IS the test.
"""

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from CORE.atomic_store import atomic_write_json
from CORE.failure_ledger import note_failure
from INTERPRETERS import run_code, supported_languages
from SHARDS.registry import LANG_ORDER, SHARDS, _probe_toolchain_versions, route_lang

GOLDEN_TOKEN = "AZAZAEL-GOLDEN-OK"
WORK_TOKEN = "AZAZAEL-WORK-OK"
WORK_N = 20000  # loop iterations for the `work` tier (D8)

# D8 two-tier goldens: `instant` proves the pipe works (spawn health),
# `work` proves throughput (routing numbers). Spending `instant` medians
# on real jobs misleads — route_lang uses the work table by default.
GOLDEN_SNIPPETS: dict = {
    "python": 'print("AZAZAEL-GOLDEN-OK")',
    "go": ('package main\nimport "fmt"\n'
            'func main(){fmt.Println("AZAZAEL-GOLDEN-OK")}'),
    "rust": 'fn main(){println!("AZAZAEL-GOLDEN-OK");}',
    "javascript": 'console.log("AZAZAEL-GOLDEN-OK");',
    "powershell": 'Write-Output "AZAZAEL-GOLDEN-OK"',
    "bash": "echo AZAZAEL-GOLDEN-OK",
}

WORK_SNIPPETS: dict = {
    "python": ("t=0\nfor i in range(20000): t+=i\n"
                 'print("AZAZAEL-WORK-OK",t)'),
    "go": ('package main\nimport "fmt"\nfunc main(){t:=0;'
             'for i:=0;i<20000;i++{t+=i};'
             'fmt.Println("AZAZAEL-WORK-OK",t)}'),
    "rust": ('fn main(){let mut t:u64=0;'
               'for i in 0..20000u64{t+=i;} '
               'println!("AZAZAEL-WORK-OK {}",t);}'),
    "javascript": ('let t=0;for(let i=0;i<20000;i++){t+=i;}'
                     'console.log("AZAZAEL-WORK-OK",t);'),
    "powershell": ('$t=0;for($i=0;$i -lt 20000;$i++){$t+=$i};'
                     'Write-Output "AZAZAEL-WORK-OK $t"'),
    "bash": ("t=0;for ((i=0;i<20000;i++));do t=$((t+i));done;"
             "echo AZAZAEL-WORK-OK $t"),
}

EXPECTED_SUM = 20000 * 19999 // 2  # 199990000 — work goldens must print this


def supported_shard_langs() -> list:
    """Languages with golden snippets (subset actually runs: toolchain-gated)."""
    return [lang for lang in LANG_ORDER if lang in GOLDEN_SNIPPETS]


def run_shard(lang: str, tier: str = "instant") -> dict:
    """Run one language golden; JSON-serializable result, no handles.

    tier instant: pipe-health golden (token agreement).
    tier work: throughput golden (sum agreement + timing for routing)."""
    key = (lang or "").lower()
    table = WORK_SNIPPETS if tier == "work" else GOLDEN_SNIPPETS
    token = WORK_TOKEN if tier == "work" else GOLDEN_TOKEN
    if key not in table:
        return {"lang": key, "tier": tier, "status": "unknown-language", "ok": False}
    result = run_code(key, table[key])
    # Case-insensitive: toolchain messages vary in case across runners,
    # and the skip contract must not depend on a capital letter.
    if not result.success and "not found" in (result.stderr or "").lower():
        detail = (result.stderr or "").strip().split("\n")[0][:120]
        return {"lang": key, "tier": tier, "status": "skipped", "ok": True,
                "detail": f"toolchain missing: {detail}"}
    token_ok = token in (result.stdout or "")
    sum_ok = True
    if tier == "work" and token_ok:
        sum_ok = str(EXPECTED_SUM) in (result.stdout or "")
    ok = bool(result.success and token_ok and sum_ok)
    return {"lang": key, "tier": tier, "status": "pass" if ok else "fail", "ok": ok,
            "success": result.success, "token": token_ok, "sum": sum_ok,
            "stdout": (result.stdout or "").strip()[:200],
            "stderr": (result.stderr or "").strip()[:300] if not ok else "",
            # E8: raw stderr survives display truncation (D7) for forensics.
            "stderr_bytes": (getattr(result, "stderr_bytes", "") or "")[:500] if not ok else "",
            "duration_ms": round(result.duration_ms, 1)}


def check_doc_shards() -> dict:
    """D9: doc-only shards are verifiable — SKILL.md exists + frontmatter.

    Doc shards (langs == ["doc"]) claim coverage the golden harness cannot
    run. This checks the claim: each doc shard must resolve at least one
    path to an existing SKILL.md with a `name:` frontmatter line, or to
    an existing repo path. Returns {ok, checked, missing}. Never raises."""
    checked = 0
    missing: list = []
    try:
        repo = Path(__file__).resolve().parents[2]
        candidates = [repo / ".agents" / "skills",
                      repo.parent / ".agents" / "skills",
                      repo / "docs",
                      Path(__file__).resolve().parents[1]]
    except Exception:
        return {"ok": False, "checked": 0, "missing": ["repo-unresolvable"]}
    for name in SHARDS:
        # `SHARDS` is already imported from SHARDS.registry at module level;
        # the per-iteration re-import here was the same dict every time, and
        # its except-continue could hide a failure nothing else would show.
        entry = SHARDS.get(name, {})
        langs = entry.get("langs", [])
        if langs != ["doc"]:
            continue
        checked += 1
        found = False
        for pat in entry.get("paths", []):
            stem = pat.rstrip("*").rstrip("/")
            for base in candidates:
                direct = base / stem
                skill_md = direct / "SKILL.md" if direct.is_dir() else None
                if skill_md is not None and skill_md.is_file():
                    try:
                        head = skill_md.read_text(encoding="utf-8")[:2000]
                        if re.search(r"(?m)^name\s*:", head):
                            found = True
                            break
                    except OSError as exc:
                        # An unreadable SKILL.md is not evidence the skill is
                        # missing; recording it keeps "unreadable" from being
                        # reported as "absent".
                        note_failure("polyglot:skill-head", exc)
                        continue
                if direct.exists():
                    found = True
                    break
                for md in base.glob(f"{stem}*/SKILL.md"):
                    try:
                        if re.search(r"(?m)^name\s*:", md.read_text(encoding="utf-8")[:2000]):
                            found = True
                            break
                    except OSError as exc:
                        note_failure("polyglot:skill-glob", exc)
                        continue
                    found = True
                    break
            if found:
                break
        if not found:
            missing.append(name)
    return {"ok": not missing, "checked": checked, "missing": missing}


def self_test() -> bool:
    """Cold recompute across every golden language; True when all pass/skip."""
    results = [run_shard(lang) for lang in supported_shard_langs()]
    ran = [r for r in results if r["status"] in ("pass", "fail")]
    ok = all(r["ok"] for r in results)
    # Agreement: every language that ran emitted the same token.
    tokens = {r["stdout"] for r in ran}
    agree = len(tokens) <= 1
    try:
        doc = check_doc_shards()
    except Exception:
        doc = {"ok": False, "checked": 0, "missing": ["check-crashed"]}
    ok_all = bool(ok and agree and doc.get("ok", False))
    report = {"self_test": "PASS" if ok_all else "FAIL",
              "dispatcher_langs": sorted(supported_languages()),
              "ran": len(ran), "skipped": len(results) - len(ran),
              "agree": agree, "results": results, "doc_shards": doc}
    print(json.dumps(report, indent=1))
    return ok_all


def prune_build_dirs(max_age_days: int = 14) -> dict:
    """Delete hashed inline-run files older than max_age_days (D4).

    build/<lang>/azazael_* grows unboundedly: every run_code writes a new
    hashed file and nothing ever removes it. Sweep calls this by default;
    safe to call anytime (only azazael_* files, only by mtime).
    Returns {removed, kept} counts. Never raises."""
    removed = 0
    kept = 0
    try:
        build_root = Path(__file__).resolve().parents[2] / "build"
    except Exception:
        return {"removed": 0, "kept": 0}
    if not build_root.is_dir():
        return {"removed": 0, "kept": 0}
    cutoff = time.time() - max_age_days * 86400
    try:
        for lang_dir in build_root.iterdir():
            if not lang_dir.is_dir():
                continue
            for child in lang_dir.glob("azazael_*"):
                try:
                    if child.is_file() and child.stat().st_mtime < cutoff:
                        child.unlink()
                        removed += 1
                    else:
                        kept += 1
                except OSError as e:
                    note_failure("polyglot:prune-entry", e)
                    kept += 1
    except OSError as e:
        note_failure("polyglot:prune", e)
    return {"removed": removed, "kept": kept}


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Atomic JSON write (D1) via CORE.atomic_store — single implementation."""
    atomic_write_json(path, payload, "shards")


def sweep(n: int = 10) -> dict:
    """Measure both golden tiers n times; write sweep.json; print tables.

    D8: instant tier = pipe health; work tier = routing numbers.
    route_lang spends the WORK medians by default. Cold recompute per
    iteration. D1: atomic write. D5: toolchain versions recorded.
    D4: prune stale build/<lang>/azazael_* files before measuring."""
    t_start = time.time()
    try:
        prune_build_dirs()
    except Exception as e:
        note_failure("polyglot:prune-call", e)
    work_medians: dict = {}
    instant_medians: dict = {}
    rows = []
    for lang in supported_shard_langs():
        work_durations: list = []
        ok_all = True
        for _ in range(max(1, n)):
            res = run_shard(lang, tier="work")
            if res["status"] == "skipped":
                ok_all = False
                break
            ok_all = ok_all and res["ok"]
            work_durations.append(res["duration_ms"])
        if not work_durations:
            rows.append({"lang": lang, "status": "skipped"})
            continue
        work_medians[lang] = round(statistics.median(work_durations), 1)
        rows.append({"lang": lang, "status": "pass" if ok_all else "fail",
                     "n": len(work_durations), "median_ms": work_medians[lang],
                     "min_ms": round(min(work_durations), 1),
                     "max_ms": round(max(work_durations), 1)})
    for lang in supported_shard_langs():
        durations: list = []
        for _ in range(max(1, min(n, 3))):
            res = run_shard(lang, tier="instant")
            if res["status"] == "skipped":
                break
            if res["ok"]:
                durations.append(res["duration_ms"])
        if durations:
            instant_medians[lang] = round(statistics.median(durations), 1)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "n": n, "elapsed_s": round(time.time() - t_start, 1),
              "medians_ms": work_medians,  # routing table = WORK (D8)
              "instant_medians_ms": instant_medians,  # pipe health only
              "versions": _probe_toolchain_versions(),
              "rows": rows}
    _atomic_write_json(Path(__file__).with_name("sweep.json"), report)
    print(f"{'lang':<12}{'n':>4}  work-median  min/max  status")
    for row in sorted(rows, key=lambda r: r.get("median_ms", float("inf"))):
        if row["status"] == "skipped":
            print(f"{row['lang']:<12}   -  skipped (toolchain missing)")
        else:
            print(f"{row['lang']:<12}{row['n']:>4}  "
                  f"median={row['median_ms']:.1f}ms "
                  f"min={row['min_ms']:.1f} max={row['max_ms']:.1f} "
                  f"{row['status']}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Azazael polyglot shard router")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--lang", default=None, choices=sorted(GOLDEN_SNIPPETS))
    ap.add_argument("--budget", default="balanced",
                    choices=["fast", "balanced", "peak"])
    ap.add_argument("--shard", default=None)
    args = ap.parse_args(argv)

    if args.list:
        print(json.dumps(
            {"langs": supported_shard_langs(),
             "shards": {k: {"langs": v["langs"], "canonical": v["canonical"],
                             "notes": v["notes"]} for k, v in SHARDS.items()}},
            indent=1))
        return 0
    if args.self_test:
        return 0 if self_test() else 1
    if args.sweep:
        sweep(args.n)
        return 0
    if args.shard:
        print(route_lang(args.shard, args.budget))
        return 0
    if args.run:
        if not args.lang:
            print(json.dumps(run_shard("python"), indent=1))
            return 0
        res = run_shard(args.lang)
        print(json.dumps(res, indent=1))
        return 0 if res["ok"] else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
