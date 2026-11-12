#!/usr/bin/env python3
# coding: utf-8
"""LOCAL-INFERENCE -- drive this architecture from models running on this machine.

Measured 2026-09-19: Ollama is serving on :11434 (`eve-v2u:latest`, qwen35 family,
4.7B, Q4_K_M, context_length 262144) and llama-server was not up on :8080.
graphify 0.8.8 reaches a local model through OLLAMA_BASE_URL / OLLAMA_MODEL /
OLLAMA_API_KEY (llm.py), so semantic extraction no longer has to depend on a
free-tier quota of 5 requests per minute -- which is what truncated the graph's
semantic layer to 9 of 71 chunks.

Three jobs in one file because they are one decision -- may this model drive, and
what happens when it is wrong:

  1. REGISTRY    per-model endpoint, context and output budget (--probe)
  2. GUARD       refuse a task whose input exceeds the model's window, instead of
                 truncating the input and reporting success (--check-in TOKENS)
  3. SCOREBOARD  kept vs reverted per model, from the mutation ledger
                 (--scoreboard). The only honest answer to "is this model up to
                 the task" is the keep rate on real proposals, not an impression.

    python tools/local_inference.py --probe
    python tools/local_inference.py --check-in 180000 --model eve-v2u
    python tools/local_inference.py --scoreboard
    python tools/local_inference.py --graphify-env --model eve-v2u
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]


def _ledger_paths() -> list[Path]:
    """Rotated generations oldest-first, then the live ledger.

    The scheme lives in `self_modify`, which owns the rotator. This reader used
    to re-state the generation count and the path template itself, so changing
    the rotator silently desynchronised it -- and the failure is quiet: a reader
    that names fewer generations than exist still returns rows, it just stops
    seeing the archived ones. The scorecard below counts verdicts across the
    whole history, so it would reset at a rotation it could not see.
    """
    tools_dir = str(ROOT / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import self_modify  # noqa: PLC0415 -- lazy, so this stays runnable as a script
    return self_modify.ledger_paths()


def _ledger_rows() -> list[dict]:
    """Every ledger row, oldest generation first, tolerant of torn lines.

    The ledger rotates (`mutation_ledger.jsonl.N`, 5 generations). The scorecard
    below counts verdicts across the whole history, so reading only the live
    file would silently reset it at every rotation.
    """
    rows: list[dict] = []
    torn = 0
    for path in _ledger_paths():
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                torn += 1  # a torn append: counted, never fatal
    if torn:
        print("[ledger] %d unreadable line(s) skipped" % torn, file=sys.stderr)
    return rows

# Declared budgets, not discovered ones. A model's window is a constraint that
# either holds or is silently negotiated away, so it is stated here and enforced
# by --check-in rather than hoped for.
REGISTRY: dict[str, dict[str, Any]] = {
    "eve-v2u": {
        "backend": "ollama", "endpoint": "http://localhost:11434/v1",
        "family": "qwen35", "params": "4.7B", "quant": "Q4_K_M",
        "context": 262144, "max_output": 8192, "role": "operator/telemetry",
        "note": "served and reachable; large window, small model -- good for "
                "classification, tool calls and retrieval-grounded edits",
    },
    "mythos": {
        "backend": "llama-server", "endpoint": "http://localhost:8080/v1",
        "family": "unknown", "params": "9B?", "quant": "unknown",
        "context": 32768, "max_output": 4096, "role": "patch drafting",
        "note": "not serving on :8080 at probe time; budget is a declared "
                "placeholder until the launcher reports its real n_ctx",
    },
    "qwopus-9b-v3": {
        "backend": "llama-server", "endpoint": "http://localhost:8080/v1",
        "family": "unknown", "params": "9B", "quant": "unknown",
        "context": 32768, "max_output": 4096, "role": "patch drafting",
        "note": "not serving at probe time; declared budget, not measured",
    },
    "ornith-1.5-9b": {
        "backend": "llama-server", "endpoint": "http://localhost:8080/v1",
        "family": "unknown", "params": "9B", "quant": "unknown",
        "context": 32768, "max_output": 4096, "role": "patch drafting",
        "note": "not serving at probe time; declared budget, not measured",
    },
    "qwen2.5-coder-7b": {
        "backend": "ollama", "endpoint": "http://localhost:11434/v1",
        "family": "qwen2", "params": "7B", "quant": "unknown",
        "context": 32768, "max_output": 4096, "role": "graphify semantic chunks",
        "note": "graphify's OLLAMA_MODEL default -- the local semantic path",
    },
}


def _endpoint_models(url: str) -> list[str]:
    """Ask a serving endpoint what it has. Absence is a fact, not an error."""
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            doc = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    if isinstance(doc.get("models"), list):  # ollama native shape
        return sorted(m.get("name", "?") for m in doc["models"])
    if isinstance(doc.get("data"), list):  # openai-compatible shape
        return sorted(m.get("id", "?") for m in doc["data"])
    return []


def probe() -> dict:
    endpoints = {
        "http://localhost:11434/api/tags": "ollama",
        "http://localhost:8080/v1/models": "llama-server",
        "http://localhost:1234/v1/models": "lm-studio",
        "http://localhost:8000/v1/models": "vllm",
    }
    serving: dict[str, list[str]] = {}
    for url, name in endpoints.items():
        models = _endpoint_models(url)
        serving[name] = models
    reachable = {k: v for k, v in serving.items() if v}
    return {"serving": serving, "reachable": sorted(reachable),
            "registry_ready": sorted(
                m for m, cfg in REGISTRY.items()
                if any(cfg["backend"].replace("-", "").startswith(r.replace("-", ""))
                       for r in reachable))}


def estimate_tokens(text: str) -> int:
    """Rough, deliberately conservative: ~3.6 chars per token for code."""
    return int(len(text) / 3.6) + 16


def check_input(model: str, text: str) -> tuple[bool, str]:
    """Refuse, or admit with the numbers. Never truncate and continue silently.

    This is the guard the 100-character diagnostic cap needed and did not have:
    an input that does not fit is a decision, and a silent slice of it produces
    a confident answer about the wrong question.
    """
    cfg = REGISTRY.get(model)
    if cfg is None:
        return False, "unknown model %r -- add it to REGISTRY" % model
    tokens = estimate_tokens(text)
    budget = cfg["context"] - cfg["max_output"]
    if tokens > budget:
        return False, ("REJECTED: ~%d tokens in, budget %d (context %d - output %d). "
                       "Chunk it or raise n_ctx; do not truncate silently."
                       % (tokens, budget, cfg["context"], cfg["max_output"]))
    return True, "fits: ~%d of %d usable tokens (%.1f%%)" % (
        tokens, budget, 100.0 * tokens / budget)


def scoreboard() -> dict:
    """Kept vs reverted per model, from the ledger.

    Rows carry no model field yet, so unattributed attempts are reported as
    exactly that -- an unattributed score is not a model score, and inventing an
    owner would make the board useless in the same way a fitness function with
    the wrong optimum is worse than none.
    """
    if not any(p.is_file() for p in _ledger_paths()):
        return {"attempts": 0, "models": {}, "note": "no ledger yet"}
    per: dict[str, dict[str, int]] = {}
    for row in _ledger_rows():
        key = str(row.get("model") or "unattributed")
        bucket = per.setdefault(key, {"kept": 0, "reverted": 0, "rejected": 0})
        verdict = str(row.get("verdict", "unknown"))
        bucket[verdict if verdict in bucket else "rejected"] = \
            bucket.get(verdict if verdict in bucket else "rejected", 0) + 1
    for stats in per.values():
        total = sum(stats.values())
        stats["attempts"] = total
        stats["keep_rate"] = round(stats["kept"] / total, 3) if total else 0.0
    return {"attempts": sum(v["attempts"] for v in per.values()), "models": per,
            "note": "record a model on each attempt (harness --model) or the board stays unattributed"}


def graphify_env(model: str) -> list[str]:
    cfg = REGISTRY.get(model, {})
    base = cfg.get("endpoint", "http://localhost:11434/v1")
    return ["OLLAMA_BASE_URL=%s" % base, "OLLAMA_MODEL=%s" % model,
            "OLLAMA_API_KEY=local", "GRAPHIFY_API_TIMEOUT=600"]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", action="store_true", help="what is actually serving")
    ap.add_argument("--scoreboard", action="store_true", help="kept/reverted per model")
    ap.add_argument("--check-in", type=int, metavar="TOKENS",
                    help="admit or refuse this input size for --model")
    ap.add_argument("--model", default="eve-v2u")
    ap.add_argument("--graphify-env", action="store_true",
                    help="print the env recipe that routes graphify at a local model")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    out: dict[str, Any] = {"registry": list(REGISTRY)}
    if args.probe:
        out["probe"] = probe()
    if args.graphify_env:
        out["graphify_env"] = graphify_env(args.model)
    if args.check_in is not None:
        ok, msg = check_input(args.model, "x" * max(args.check_in, 1) * 4)
        out["check_in"] = {"model": args.model, "requested": args.check_in,
                           "ok": ok, "message": msg}
    if args.scoreboard:
        out["scoreboard"] = scoreboard()

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    print("registered models: %s" % ", ".join(REGISTRY))
    if "probe" in out:
        p = out["probe"]
        print("\nlocal inference reachable now: %s" % (p["reachable"] or "none"))
        for backend, models in p["serving"].items():
            if models:
                print("  %-12s %d model(s): %s" % (backend, len(models), models[:6]))
        print("  registry entries servable: %s" % (p["registry_ready"] or "none"))
    if "graphify_env" in out:
        print("\nroute graphify at %s:" % args.model)
        for kv in out["graphify_env"]:
            print("  export %s" % kv)
        print("  %s -m graphify extract src --out graphify-out" % sys.executable)
    if "check_in" in out:
        c = out["check_in"]
        print("\ncontext guard: %s  %s" % ("OK" if c["ok"] else "REFUSED", c["message"]))
    if "scoreboard" in out:
        s = out["scoreboard"]
        print("\nattempts: %d" % s["attempts"])
        for name, stats in s["models"].items():
            print("  %-16s kept=%d reverted=%d rejected=%d  keep_rate=%s"
                  % (name, stats["kept"], stats["reverted"], stats["rejected"],
                     stats["keep_rate"]))
        print("  note: %s" % s["note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
