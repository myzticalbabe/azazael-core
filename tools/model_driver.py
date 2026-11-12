#!/usr/bin/env python3
# coding: utf-8
"""MODEL DRIVER -- the local model authors the patch; the harness grades it.

Until now every mutation in the ledger was authored by me. That makes the keep
rate meaningless as evidence about a model: it measures whether *I* can write a
patch, not whether eve-v2u or a 9B can. This closes that gap. A model is given a
real defect in a real file, returns a patch, and the existing harness applies,
compiles, evaluates and keeps or reverts it -- with `--model` recorded, so the
scoreboard attributes the outcome to the model that earned it.

The task is deliberately the smallest honest surface a small model can hit:

  one substitution -- {"old": "<exact substring>", "new": "<replacement>"}

A byte-exact patch is the actual hard requirement of driving this architecture,
so `old` must occur exactly once in the file or the patch is refused before it
reaches the tree. Refusals are recorded as refusals, not hidden.

Two objective graders, both real:
  1. the harness verdict (tier1 compiles, tier2 band does not regress)
  2. did the vacuous check actually go away -- the same pattern the audit looks
     for is counted before and after, in that file only

Benchmark mode restores the shared baseline between candidates, so every model
faces the identical task and the comparison is not "whoever went last".

    python tools/model_driver.py --file tools/hydra_integration_test.py --model eve-v2u
    python tools/model_driver.py --file tools/hydra_integration_test.py \
        --models eve-v2u,eve-unleashed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "logs" / "model_driver_runs.jsonl"
OLLAMA = "http://127.0.0.1:11434"
BAND = "tests/test_mesh_fabric_followups.py"

# The vacuous-check pattern the audit hunts: a zero-count comparison. Counted in
# the mutated file only, before and after, as the objective beside the verdict.
VACUOUS = re.compile(r"(?:passed|count|total|n)\s*==\s*0\b")

SYS_PROMPT = (
    "You are a code editing engine. You reply with ONE JSON object and nothing else. "
    "No prose, no markdown fences, no explanation."
)


def build_prompt(rel: str, defect_line: str, context: str) -> str:
    return (
        "File: %s\n\n"
        "This file contains a vacuous check: it can pass when nothing actually ran. "
        "The offending line is:\n\n%s\n\n"
        "Surrounding context:\n%s\n\n"
        "Rewrite that assertion so it only passes when the work really ran. "
        "Keep the surrounding code and the imports unchanged.\n\n"
        "Reply with exactly this JSON object:\n"
        '{"old": "<a substring of the file, copied exactly, appearing exactly once>", '
        '"new": "<your replacement for it>"}\n\n'
        "Rules: old must be at least 15 characters and appear exactly once in the file. "
        "Only the two keys. No other text." % (rel, defect_line.strip(), context)
    )


def call_model(model: str, prompt: str, timeout: int = 300) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYS_PROMPT},
                     {"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": 8192},
    }
    req = urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
    except Exception as exc:  # noqa: BLE001 - an unreachable model is a result
        return {"ok": False, "error": repr(exc)[:200], "elapsed_s": round(time.time() - t0, 2)}
    return {
        "ok": True,
        "text": (d.get("message") or {}).get("content", ""),
        "elapsed_s": round(time.time() - t0, 2),
        "eval_count": d.get("eval_count"),
        "prompt_eval_count": d.get("prompt_eval_count"),
    }


def parse_patch(text: str) -> dict:
    """Extract {"old","new"} from whatever the model actually said."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?|```$", "", stripped).strip()
    for candidate in (stripped, *re.findall(r"\{[^{}]*\}", text, re.S)):
        try:
            d = json.loads(candidate)
        except Exception:
            continue
        if isinstance(d, dict) and "old" in d and "new" in d:
            return {"old": str(d["old"]), "new": str(d["new"])}
    return {}


def grade_vacuous(path: Path) -> int:
    try:
        return len(VACUOUS.findall(path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return -1


def run_harness(patch_path: Path, model: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "self_modify.py"), "--patch",
         str(patch_path), "--tier2", BAND, "--model", model],
        cwd=str(ROOT), capture_output=True, text=True, timeout=3600,
        encoding="utf-8", errors="replace")
    out = proc.stdout
    verdict = next((l.split(":", 1)[1].strip() for l in out.splitlines()
                    if l.startswith("verdict")), "?")
    reason = next((l.split(":", 1)[1].strip() for l in out.splitlines()
                   if l.startswith("reason")), "")
    tier2 = next((l.split(":", 1)[1].strip() for l in out.splitlines()
                  if l.startswith("tier2")), "")
    return {"rc": proc.returncode, "verdict": verdict, "reason": reason[:200],
            "tier2": tier2.strip(), "stdout_tail": out[-400:]}


def defect_line_for(path: Path) -> tuple[str, str]:
    """Pick the line the audit would flag, and a little context around it."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, ln in enumerate(lines):
        if VACUOUS.search(ln):
            lo, hi = max(0, i - 4), min(len(lines), i + 3)
            return ln, "\n".join(lines[lo:hi])
    return "", ""


def attempt(rel: str, model: str) -> dict:
    target = ROOT / rel
    original = target.read_bytes()
    before_hash = hashlib.sha256(original).hexdigest()[:16]
    defect, context = defect_line_for(target)
    if not defect:
        return {"model": model, "file": rel, "outcome": "no_vacuous_check_found"}
    vac_before = grade_vacuous(target)

    row: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": model, "file": rel, "hash_before": before_hash,
        "vacuous_before": vac_before, "defect_line": defect.strip()[:120],
    }

    resp = call_model(model, build_prompt(rel, defect, context))
    row["model_elapsed_s"] = resp.get("elapsed_s")
    if not resp.get("ok"):
        row.update({"outcome": "model_unreachable", "error": resp.get("error")})
        return row

    patch = parse_patch(resp.get("text", ""))
    if not patch:
        row.update({"outcome": "unparseable_reply",
                    "reply_head": resp.get("text", "")[:200]})
        return row

    old, new = patch["old"], patch["new"]
    occurrences = original.decode("utf-8", errors="replace").count(old)
    row.update({"old_len": len(old), "occurrences": occurrences,
                "changed_anything": new != old})
    if occurrences != 1 or len(old) < 15 or new == old:
        row.update({"outcome": "patch_refused",
                    "why": ("old occurs %d time(s)" % occurrences if occurrences != 1
                            else "old shorter than 15 chars" if len(old) < 15
                            else "new identical to old")})
        return row

    patch_file = ROOT / "logs" / ("model_patch_%s.json" % re.sub(r"[^\w]", "_", model))
    patch_file.write_text(json.dumps({"encoding": "utf-8", "subs": {rel: [[old, new]]}}),
                          encoding="utf-8")
    res = run_harness(patch_file, model)
    row.update({"outcome": res["verdict"], "harness": res})
    row["vacuous_after"] = grade_vacuous(target)
    row["vacuous_fixed"] = row["vacuous_after"] < vac_before
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True, help="file holding the defect")
    ap.add_argument("--model", default="", help="single model")
    ap.add_argument("--models", default="", help="comma-separated: benchmark mode")
    args = ap.parse_args()

    models = [m.strip() for m in (args.models or args.model).split(",") if m.strip()]
    if not models:
        ap.error("give --model or --models")

    rel = str(Path(args.file)).replace("\\", "/")
    target = ROOT / rel
    if not target.is_file():
        print("no such file: %s" % rel)
        return 2

    baseline = target.read_bytes()
    rows = []
    for m in models:
        print("--- %s on %s ---" % (m, rel))
        row = attempt(rel, m)
        rows.append(row)
        print("    outcome      : %s" % row.get("outcome"))
        if row.get("harness"):
            print("    tier2        : %s" % row["harness"]["tier2"])
            print("    reason       : %s" % row["harness"]["reason"])
        elif row.get("why"):
            print("    refused      : %s" % row["why"])
        print("    vacuous %s -> %s   model %ss"
              % (row.get("vacuous_before"), row.get("vacuous_after", "-"),
                 row.get("model_elapsed_s")))
        # Benchmark fairness: every candidate must face the SAME task. Without
        # this, the second model inherits whatever the first one left behind and
        # the comparison measures order, not capability.
        if len(models) > 1 and target.read_bytes() != baseline:
            target.write_bytes(baseline)
            row["baseline_restored_for_next_candidate"] = True

    LEDGER.parent.mkdir(exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    print()
    print("=== MODEL-AUTHORED ATTEMPTS (this run) ===")
    for r in rows:
        print("  %-18s %-22s fixed_vacuous=%s"
              % (r["model"], r.get("outcome"), r.get("vacuous_fixed", "-")))
    kept = sum(1 for r in rows if r.get("outcome") == "kept")
    print("  kept %d of %d attempt(s) -- attributed to the authoring model"
          % (kept, len(rows)))
    print("  ledger: %s" % LEDGER.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
