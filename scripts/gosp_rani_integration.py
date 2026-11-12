#!/usr/bin/env python3
"""
gosp_rani_integration.py -- Test Gossamer Oxford Semantical Parser with Eve Unleashed RANI

Wires the Gossamer Oxford Semantical Parser (WSL2: /root/gossamer-oxford-parser)
into RANI's (Recursive Adversarial Native Intelligence, EveUnleashed_Selector) AdversarialEngine
as the registered inference backend. RANI runs adversarial batteries + recursive deep-dives;
GOSP supplies every response via Montague-grammar/lambda-calculus semantic parsing with
8-dimension native intelligence. Deterministic -- no LLM/ollama needed, so it does not
disturb background RL training.

Usage (run inside WSL2 Ubuntu, python 3.14):
    python3 /mnt/c/Azazael/azazael/scripts/gosp_rani_integration.py
"""
import sys
import json
import time
from pathlib import Path

GOSSAMER = "/root/gossamer-oxford-parser"
RANI = "/mnt/c/Users/clint/Desktop/EveUnleashed_Selector/agents/adversarial"
OUT = "/mnt/c/Users/clint/GOSP_RANI_INTEGRATION_evidence.json"

for p in (GOSSAMER, RANI):
    if p not in sys.path:
        sys.path.insert(0, p)

# ── GOSP ────────────────────────────────────────────────────────────────────
from semantical_main import GossamerOxfordParser  # noqa: E402


def gosp_infer(prompt: str) -> str:
    """GOSP as RANI inference backend: prompt -> semantic analysis text."""
    try:
        res = GossamerOxfordParser(max_workers=4).parse(prompt, depth="full", learn=False)
        lines = []
        lines.append(f"INPUT: {prompt}")
        trees = res.get("parse_trees", [])
        graphs = res.get("semantic_graphs", [])
        for i, t in enumerate(trees[:2]):
            lines.append(f"TREE[{i}]: {json.dumps(t, default=str)[:600]}")
        for i, g in enumerate(graphs[:3]):
            lines.append(f"SEMANTICS[{i}]: {json.dumps(g, default=str)[:600]}")
        for ins in res.get("native_intelligence_insights", [])[:8]:
            lines.append(f"INSIGHT: {ins[:400]}")
        return "\n".join(lines) if lines else "[GOSP: empty parse]"
    except Exception as e:  # noqa: BLE001
        return f"[GOSP ERROR: {e}]"


def summarize(res: dict) -> dict:
    keys = ("total_rounds", "adversarial_detected", "avg_latency_ms", "avg_score")
    return {k: res.get(k) for k in keys}


def main() -> int:
    started = time.time()
    report = {
        "test": "GOSP x RANI integration",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backend": "GOSP",
        "gosp": "gossamer-oxford-parser (Montague/lambda, 26/26 tests)",
        "rani": "EveUnleashed_Selector agents/adversarial AdversarialEngine",
        "mode": "deterministic (no LLM backend)",
        "probes": [],
    }

    # 1) Single round sanity -- import RANI engine.py by explicit path (WSL has
    #    another top-level `engine` package that would shadow it)
    import importlib.util  # noqa: E402

    _spec = importlib.util.spec_from_file_location(
        "rani_engine", Path(RANI) / "engine.py"
    )
    _mod = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_mod)
    AdversarialEngine = _mod.AdversarialEngine
    AdversarialConfig = _mod.AdversarialConfig

    engine = AdversarialEngine(AdversarialConfig(adversarial_rounds=4, recursion_depth=2))
    engine.register_inference(gosp_infer, name="GOSP")

    probe = "Every board member who approved the compensation plan voted."
    probe_rec = {"base_prompt": probe, "rounds": [], "layers": []}

    # Battery (parallel where free-threaded)
    rounds = engine.run_adversarial_battery(probe, num_rounds=4)
    for r in rounds:
        probe_rec["rounds"].append(
            {
                "round_id": r.round_id,
                "backend": r.backend,
                "prompt": r.prompt[:120],
                "score": r.score,
                "critique": r.critique,
                "adversarial_flag": r.adversarial_flag,
                "latency_ms": round(r.latency_ms, 1),
                "response_prefix": (r.response or "")[:200],
            }
        )

    # 2) Recursive deep-dive (synthesis loop)
    layers = engine.recursive_deep_dive(probe, depth=2)
    for li, layer in enumerate(layers):
        probe_rec["layers"].append(
            {
                "layer": li,
                "rounds": [
                    {
                        "round_id": r.round_id,
                        "score": r.score,
                        "critique": r.critique,
                        "prompt_prefix": r.prompt[:80],
                    }
                    for r in layer
                ],
            }
        )

    probe_rec["engine_summary"] = summarize(engine.get_summary())
    report["probes"].append(probe_rec)
    report["elapsed_s"] = round(time.time() - started, 2)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print("=== GOSP x RANI integration evidence ===")
    print(json.dumps(probe_rec["engine_summary"], indent=2))
    print(f"Battery rounds: {len(probe_rec['rounds'])}  Deep-dive layers: {len(probe_rec['layers'])}")
    for r in probe_rec["rounds"]:
        print(
            f"  R{r['round_id']} score={r['score']:.2f} flag={r['adversarial_flag']} "
            f"critique={r['critique'][:60]}"
        )
    print(f"Evidence written: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
