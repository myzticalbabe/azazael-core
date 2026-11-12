"""Re-extract the ABSENT technique rows into an actionable triage table.

Why: the map's 19 `ABSENT` rows were graded in one pass against an inventory that
later turned out to be incomplete, and four re-check documents then re-graded them
group by group. The triage below is GENERATED from those two sources so the next
session does not re-derive it -- and so it cannot drift from what was measured.

It invents nothing. Every cell comes from either

  * the map  (`state/sota_batches/batch06/technique_codebase_map.json`), or
  * a re-check verdict (`state/sota_batches/batch06/recheck_*.md`),
    parsed from the rows that begin `| EP<n> | <verdict> |`.

The only computed column is the ROUTE, and its rule is written down in the output
so it can be argued with:

  ACCEPT-GAP-INFRA  the row is model- or training-internal, and this repo consumes
                    models as opaque providers -- there is no host to build in.
  BUILD-IN-HOST     a real, reachable host exists (or the re-check named one), so
                    the row can be implemented where a caller already runs.
  NEEDS-HOST        neither: a host must be found or created first, and creating
                    one makes the row EXERCISE-ONLY by construction. Do not build
                    these until a production consumer exists.

Usage:
    python tools/absent_triage.py                  # write ABSENT_TRIAGE_315T.md
    python tools/absent_triage.py --stdout         # print instead
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAP = Path("C:/Azazael/state/sota_batches/batch06/technique_codebase_map.json")
RECHECK_DIR = Path("C:/Azazael/state/sota_batches/batch06")
OUT = REPO / "ABSENT_TRIAGE_315T.md"

# Rows whose subject is a model or training-stack internal that this repo does
# not own: it calls out to providers (Ollama/GGUF/ONNX) and trains only small
# policy/reward models. Named explicitly rather than inferred, because "is this
# infra" is a judgement and burying it in a heuristic would hide the call.
INFRA = {
    "EP11": "ZeRO optimizer/gradient/parameter partitioning -- no training stack that large",
    "EP14": "LSH attention + reversible residuals -- repo trains no transformer",
    "EP38": "540B dense-scale result -- consumes such models as providers",
    "EP39": "frozen vision-language bridge -- VLM reachable as a provider only",
    "EP52": "multimodal pretraining + RLHF reward models -- no training-time alignment stack",
    "EP68": "PagedAttention/KV block paging -- no owned inference engine",
    "EP226": "per-layer shared KV / looped-LM inference path -- model-internal",
    "EP245": "measure-theoretic OOD bounds -- theory-only input, no runtime counterpart",
    # EP176's host cell names `synapse.py :: AttentionRouterWorm`, which LOOKS like
    # a host, but the re-check records that the module rules itself out in its own
    # docstring ("explicitly NOT token-level attention"). A named path is not a
    # host when the path denies the mechanism.
    "EP176": "token-level KV scoring -- nearest module disclaims the mechanism in its docstring",
}


def load_map_rows() -> dict[str, dict]:
    d = json.loads(MAP.read_text(encoding="utf-8"))
    rows: dict[str, dict] = {}
    for r in d.get("rows", []):
        code = str(r.get("code") or r.get("ep") or "").strip()
        if code and str(r.get("coverage", "")).upper().startswith("ABSENT"):
            rows[code] = r
    return rows


def load_recheck() -> dict[str, dict]:
    """Parse `| EP<n> | <verdict> | <host/nearest> | <reason> |` rows."""
    out: dict[str, dict] = {}
    rx = re.compile(
        r"^\|\s*(EP\d+)\s*\|\s*([A-Z_]+)\s*\|\s*([^|]*)\|\s*([^|]*)", re.M)
    for doc in sorted(RECHECK_DIR.glob("recheck*.md")):
        for m in rx.finditer(doc.read_text(encoding="utf-8", errors="replace")):
            code, verdict, host, reason = (m.group(1), m.group(2),
                                          m.group(3).strip(), m.group(4).strip())
            out[code] = {"verdict": verdict, "host": host,
                         "reason": reason, "doc": doc.name}
    return out


def route(code: str, recheck: dict) -> tuple[str, str]:
    if code in INFRA:
        return "ACCEPT-GAP-INFRA", INFRA[code]
    host = (recheck.get("host") or "").strip()
    # Only count a REAL host: a module path with a symbol (`.../x.py :: Sym`).
    # First version accepted any non-empty cell, so "searched ... -- 0 matches"
    # and "(none - searched)" were read as hosts and four rows (EP110, EP237,
    # EP278, EP300) were mis-routed BUILD-IN-HOST while their own cell said no
    # host exists. A cell that reports an absence is not evidence of presence.
    named_path = bool(re.search(r"[\w./]+\.py", host)) and "::" in host
    if named_path:
        return "BUILD-IN-HOST", host
    return "NEEDS-HOST", (host or recheck.get("reason") or "no host named")[:150]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args(argv)

    rows, recheck = load_map_rows(), load_recheck()
    if not rows:
        print("no ABSENT rows found -- refusing to write an empty triage")
        return 2

    buckets: dict[str, list[tuple[str, str, str]]] = {
        "ACCEPT-GAP-INFRA": [], "BUILD-IN-HOST": [], "NEEDS-HOST": []}
    for code in sorted(rows, key=lambda c: int(c[2:])):
        r = rows[code]
        rc = recheck.get(code, {})
        kind, why = route(code, rc)
        buckets[kind].append(
            (code, str(r.get("name") or r.get("technique") or "")[:52], why))

    lines = [
        "# ABSENT-row triage (generated — do not hand-edit)",
        "",
        f"Source: `{MAP.name}` ({len(rows)} ABSENT rows) + "
        f"`{RECHECK_DIR.name}/recheck_*.md` verdicts "
        f"({len(recheck)} parsed). Regenerate: "
        "`python tools/absent_triage.py`",
        "",
        "| route | rows |",
        "|---|---|",
    ]
    for k in ("ACCEPT-GAP-INFRA", "BUILD-IN-HOST", "NEEDS-HOST"):
        lines.append(f"| {k} | {len(buckets[k])} |")
    lines += [
        "",
        "**Route rule** (written down so it can be argued with): a row is "
        "`ACCEPT-GAP-INFRA` when its subject is a model/training-stack internal "
        "this repo does not own (it consumes models as opaque providers); "
        "`BUILD-IN-HOST` when a real host was named by the re-check; otherwise "
        "`NEEDS-HOST`.",
        "",
        "> **Binding constraint inherited from S1/S3.** A row built as a new "
        "standalone module with no production consumer is EXERCISE-ONLY by "
        "construction — it inflates the adoption count while changing no "
        "behaviour. `BUILD-IN-HOST` rows must be implemented where a caller "
        "already runs, or not at all.",
        "",
    ]
    for k in ("ACCEPT-GAP-INFRA", "BUILD-IN-HOST", "NEEDS-HOST"):
        lines += [f"## {k} ({len(buckets[k])})", ""]
        if not buckets[k]:
            lines += ["_(none)_", ""]
            continue
        lines += ["| row | technique | host / reason |", "|---|---|---|"]
        for code, name, why in buckets[k]:
            lines.append(f"| {code} | {name} | {why.replace('|', '/')[:170]} |")
        lines.append("")

    text = "\n".join(lines)
    if args.stdout:
        print(text)
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.name}: " + ", ".join(
        f"{k}={len(v)}" for k, v in buckets.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
