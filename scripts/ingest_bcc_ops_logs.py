#!/usr/bin/env python3
"""ingest_bcc_ops_logs.py — Ingest BCC investigation OPS logs as training memories.

Only operational telemetry is ingested (tool checks, inference server logs,
extraction notes). Legal/forensic HTML docs are deliberately EXCLUDED —
their severity carries no action→reward signal for RLTrainLoop.

Severity mapping (honest, matches _compute_reward semantics):
  kali_test*.log  [OK ..]   -> info,    success=True,  duration_ms parsed
                  [FAIL/EXC] -> error,   success=False, duration_ms parsed
  llama_*.log      I ...    -> info     (sampled 1:20, timing spam)
                   W ...    -> warning  (deduped by message, xN counted)
                   E ...    -> error    (all kept)
  extract.log      warning: -> warning; BadRequest/InternalServerError -> error
  stakeout/deep3   run markers -> single info summary row per file

Idempotent: per-file sha256 fingerprints stored in bcc_ops_ingest marker
events; unchanged files are skipped. Safe to re-run (and to schedule).

Usage:
    python scripts/ingest_bcc_ops_logs.py --dry-run
    python scripts/ingest_bcc_ops_logs.py
"""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

CORPUS = Path(r"C:\Users\clint\BCC_Investigation_Corpus")
SOURCE_TAG = "bcc-ingest"

KALI_RE = re.compile(r"^\[(OK|FAIL|EXC|SKIP)\s+([\d.]+)s\]\s+(\w+):\s*(.*)$")
LLAMA_RE = re.compile(r"^(\S+)\s+([IEW])\s+(\S+)\s+(.*)$")


def sha_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_kali(path: Path):
    """Yields (source, event_type, data, severity, tags) — one per line."""
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = KALI_RE.match(line.strip())
        if not m:
            continue
        status, secs, worm, detail = m.groups()
        ok = status == "OK"
        out.append((worm, "tool_check",
                    {"action": f"check: {detail[:120]}", "target": worm,
                     "duration_ms": round(float(secs) * 1000, 1),
                     "success": ok, "status": status,
                     "log_file": path.name},
                    "info" if ok else "error",
                    ["tool-check", SOURCE_TAG]))
    return out


def _warn_key(msg: str) -> str:
    """Normalize a warning message so repeated variants collapse.

    llama.cpp token warnings differ only by token id/name
    ('...token: 128247 '</s>'...') — without normalization each of the
    thousands is a distinct row. Returns (key, example) grouping.
    """
    return re.sub(r"'[^']*'|\"[^\"]*\"|\d+", "#", msg)


def parse_llama(path: Path, info_every: int = 20, warn_cap: int = 200):
    """Yields rows; info sampled, warnings deduped by message, errors kept."""
    out, info_n, warns = [], 0, Counter()
    warn_first, errors = {}, []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LLAMA_RE.match(line.strip())
        if not m:
            continue
        _ts, level, comp, msg = m.groups()
        msg = msg.strip()[:200]
        if level == "E":
            errors.append(("LlamaServer", "inference_server_log",
                           {"action": f"server error: {msg[:120]}",
                            "target": comp, "success": False,
                            "log_file": path.name},
                           "error", ["inference", "server-error", SOURCE_TAG]))
        elif level == "W":
            key = _warn_key(msg)
            warns[key] += 1
            if key not in warn_first:
                warn_first[key] = (comp, msg)
        else:
            info_n += 1
            if info_n % info_every == 0:
                out.append(("LlamaServer", "inference_server_log",
                            {"action": f"server: {msg[:120]}", "target": comp,
                             "success": True, "log_file": path.name,
                             "sampled": f"1:{info_every}"},
                            "info", ["inference", SOURCE_TAG]))
    for key, n in warns.most_common(warn_cap):
        comp, example = warn_first[key]
        out.append(("LlamaServer", "inference_server_log",
                    {"action": f"server warning: {example[:120]}",
                     "target": comp, "success": True, "occurrences": n,
                     "log_file": path.name},
                    "warning", ["inference", "server-warning", SOURCE_TAG]))
    return errors + out


def parse_extract(path: Path):
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        if "BadRequestError" in line or "InternalServerError" in line:
            out.append(("Graphify", "extract_note",
                        {"action": f"extract failed: {line[:150]}",
                         "target": "graphify-extract", "success": False,
                         "log_file": path.name},
                        "error", ["extract", SOURCE_TAG]))
        elif line.startswith("warning:"):
            out.append(("Graphify", "extract_note",
                        {"action": line[:150], "target": "graphify-extract",
                         "success": True, "log_file": path.name},
                        "warning", ["extract", SOURCE_TAG]))
    return out


def parse_marker(path: Path):
    lines = [l for l in path.read_text(encoding="utf-8",
                                       errors="replace").splitlines() if l.strip()]
    return [("BCCInvestigation", "ops_log_file",
             {"action": f"log file present: {path.name} ({len(lines)} lines)",
              "target": path.name, "success": True, "log_file": path.name},
             "info", ["ops-log", SOURCE_TAG])]


PARSERS = {"kali_test": parse_kali, "llama_": None, "extract.log": parse_extract,
           "stakeout.log": parse_marker, "deep3.log": parse_marker}


def collect():
    """Gather (path, rows) for all known ops logs. Pure — no DB writes."""
    plan = []
    for path in sorted(CORPUS.glob("*.log")):
        if not path.stat().st_size:
            continue
        rows = None
        if path.name.startswith("kali_test"):
            rows = parse_kali(path)
        elif path.name.startswith("llama_"):
            rows = parse_llama(path)
        elif path.name == "extract.log":
            rows = parse_extract(path)
        elif path.name in ("stakeout.log", "deep3.log"):
            rows = parse_marker(path)
        if rows is not None:
            plan.append((path, rows))
    return plan


def already_ingested(store, path: Path) -> bool:
    fp = sha_of(path)
    try:
        rows = store.query_memories(event_type="bcc_ops_ingest", limit=200)
    except Exception:
        return False
    for r in rows:
        d = r.get("data", {})
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except (json.JSONDecodeError, TypeError):
                continue
        if d.get("file") == path.name and d.get("sha256") == fp:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest BCC ops logs as memories")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    plan = collect()
    total = sum(len(rows) for _, rows in plan)
    sev = Counter(s for _, rows in plan for (_, _, _, s, _) in rows)
    print(f"files: {len(plan)}  rows: {total}  severity: {dict(sev)}")
    for path, rows in plan:
        print(f"  {path.name:<20} {len(rows):>5} rows")
    if args.dry_run:
        print("dry run — no writes")
        return 0

    from CORE.direct_memory import DirectMemory
    from CORE.local_memory import get_memory_store
    dm = DirectMemory()
    store = get_memory_store()
    wrote, skipped = 0, []
    for path, rows in plan:
        if already_ingested(store, path):
            skipped.append(path.name)
            continue
        for source, etype, data, sev, tags in rows:
            dm.record(etype, data, severity=sev, tags=tags, source=source)
            wrote += 1
        dm.record("bcc_ops_ingest",
                  {"action": f"ingested {path.name}",
                   "file": path.name, "sha256": sha_of(path),
                   "rows": len(rows)},
                  tags=["ops-log", SOURCE_TAG])
    print(f"wrote {wrote} rows, skipped {len(skipped)} unchanged: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
