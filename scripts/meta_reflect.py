#!/usr/bin/env python3
# coding: utf-8
"""meta_reflect.py — scheduled meta-reflection loop (skill: azazael-meta-reflection).

Once per day (Chain `reflect` job): gather recent trial trajectories
(meta lineage + evolution margins), distill ONE generalized instruction via
the local model, validate it (material, non-duplicate, evidence-linked),
append to state/meta_reflections.jsonl + a memory row.

Exit codes: 0 stored | 1 error | 2 model/store unavailable (fail-open) |
3 insufficient evidence (< MIN_TRAJECTORIES supporting rows — not a failure).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
LEDGER = STATE_DIR / "meta_reflections.jsonl"
SCHEMA = "azazael.meta-reflection.v1"
MIN_TRAJECTORIES = 3
LEDGER_CAP = 200
OLLAMA_URL = os.environ.get("AZAZAEL_META_OLLAMA", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.environ.get("AZAZAEL_REFLECT_MODEL", "eve-v2u")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(text: str) -> str:
    """Canonical form for duplicate detection."""
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def load_ledger() -> list[dict]:
    out = []
    try:
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def gather_trajectories(limit: int = 25) -> tuple[list[dict], str]:
    """Recent trial outcomes from lineage + evolution margins (read-only).

    Returns (trajectories, source_note). Each trajectory: {kind, verdict,
    detail}. Fail-open: store trouble yields ([], reason), never raises.
    """
    trajs: list[dict] = []
    try:
        lines = (STATE_DIR / "meta_evolution" / "lineage.jsonl").read_text(
            encoding="utf-8").splitlines()
        for line in lines[-limit:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            trajs.append({"kind": "meta", "verdict": r.get("verdict"),
                          "reason": str(r.get("reason", ""))[:160],
                          "at": r.get("at")})
    except OSError as exc:
        return [], f"lineage unreadable: {exc}"
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        rows = store.query_memories(event_type="evolution_result", limit=limit)
        flat = tot = 0
        margins = []
        for r in rows:
            d = r.get("data", {}) or {}
            if isinstance(d, str):
                try:
                    d = json.loads(d)
                except ValueError:
                    continue
            t = d.get("trajectory")
            if isinstance(t, list) and len(t) >= 3:
                tot += 1
                if max(t) - min(t) < 1e-9:
                    flat += 1
            m = d.get("margin")
            if isinstance(m, (int, float)):
                margins.append(m)
        if tot:
            trajs.append({"kind": "evolution",
                          "verdict": f"flat {flat}/{tot}",
                          "reason": "avg margin %+.3f over %d cycles" % (
                              sum(margins) / len(margins), len(margins)) if margins else "no margins",
                          "at": _now()})
    except Exception as exc:
        trajs.append({"kind": "evolution", "verdict": "unavailable",
                      "reason": str(exc)[:160], "at": _now()})
    return trajs, f"{len(trajs)} trajectories"


def distill(trajs: list[dict], timeout: int = 300) -> dict:
    """Ask the local model for ONE generalized instruction. Raises on failure."""
    brief = "\n".join(
        "- [%s] %s: %s" % (t.get("kind"), t.get("verdict"), t.get("reason"))
        for t in trajs[-15:])
    system = (
        "You distill operating instructions from trial outcomes. Reply with "
        "exactly one JSON object, no markdown: "
        '{"instruction": "...", "evidence": "...", "scope": "..."}. '
        "The instruction must generalize beyond any single incident "
        "(would have changed a CLASS of decisions). Evidence names the "
        "trajectories used. Scope states where it does NOT apply, or null. "
        "Max 280 chars for instruction.")
    body = {"model": MODEL,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": "Recent trials:\n" + brief}],
            "stream": False, "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.0, "num_ctx": 4096}}
    req = urllib.request.Request(
        OLLAMA_URL + "/api/chat", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    text = str((payload.get("message") or {}).get("content", ""))
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model reply held no JSON object")
    parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict) or "instruction" not in parsed:
        raise ValueError("model reply was not an instruction object")
    return parsed


def validate_candidate(parsed: dict, existing: list[dict]) -> tuple[bool, str, dict]:
    """Dedicated validation step (never skipped): material, bounded,
    evidence-linked, non-duplicate. Returns (ok, reason, record)."""
    instruction = str(parsed.get("instruction", "")).strip()
    if not instruction:
        return False, "empty instruction", {}
    if len(instruction) > 280:
        return False, "over 280 chars (not an instruction, an essay)", {}
    evidence = str(parsed.get("evidence", "")).strip()
    if not evidence:
        return False, "no evidence linkage", {}
    norm = normalize(instruction)
    for row in existing:
        if normalize(row.get("instruction", "")) == norm:
            return False, "duplicate of stored instruction", {}
    return True, "", {"instruction": instruction, "evidence": evidence,
                      "scope": parsed.get("scope")}


def store_record(record: dict) -> dict:
    """Append to the ledger (capped) + best-effort memory row."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_ledger()
    rows.append({"schema": SCHEMA, "at": _now(), **record,
                 "retrievals": 0})
    rows = rows[-LEDGER_CAP:]
    tmp = str(LEDGER) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, LEDGER)
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE.local_memory import get_memory_store
        get_memory_store().record_event(
            source="meta_reflect", event_type="meta_reflection_stored",
            data={"instruction": record["instruction"]},
            severity="info", tags=["meta-reflection"])
    except Exception:
        pass
    return {"path": str(LEDGER.relative_to(ROOT)).replace("\\", "/"),
            "count": len(rows)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="meta_reflect")
    ap.add_argument("--list", action="store_true", help="print stored instructions")
    ap.add_argument("--dry-run", action="store_true", help="distill + validate, store nothing")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args(argv)

    if args.list:
        rows = load_ledger()
        print(f"{len(rows)} stored instruction(s)")
        for r in rows[-10:]:
            print(f"  [{r.get('at', '?')[:10]}] {r.get('instruction', '')[:100]}")
        return 0

    trajs, note = gather_trajectories()
    print(f"trajectories: {note}", flush=True)
    if len(trajs) < MIN_TRAJECTORIES:
        print(f"insufficient evidence ({len(trajs)} < {MIN_TRAJECTORIES}) — skipping")
        return 3
    try:
        parsed = distill(trajs, timeout=args.timeout)
    except Exception as exc:
        print(f"distillation unavailable: {exc}")
        return 2
    ok, reason, record = validate_candidate(parsed, load_ledger())
    if not ok:
        print(f"rejected: {reason}")
        return 0 if "duplicate" in reason else 1
    if args.dry_run:
        print(f"would store: {record['instruction'][:120]}")
        return 0
    info = store_record(record)
    print(f"stored ({info['count']} total): {record['instruction'][:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
