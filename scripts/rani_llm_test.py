#!/usr/bin/env python3
"""rani_llm_test.py -- Test Eve Unleashed RANI against a live inference backend.

Backends (--backend):
  gateway:<model>   local model via the Rust gateway :18080 (default: gateway:omnicoder)
  gosp              deterministic Gossamer parser (control; needs WSL gossamer on path)
  <provider>[:model]  API provider from CORE.inference registry
                    (groq, cerebras, openrouter, gemini, mistral, nvidia_nim, ...)
                    keys come from ~/.azazael/*_config.json — never printed/logged.

Scoring judges the ANSWER content, not thinking traces: reasoning_content
and <think> blocks are stripped before critique (thinking models otherwise
score perfect by default). Bigger token budget default (250) so thinking
models finish answering. Every round is stamped with its backend.

Run on Windows (gateway/llama must be up for gateway: backends):
    python scripts\\rani_llm_test.py
    python scripts\\rani_llm_test.py --backend cerebras --rounds 2
    python scripts\\rani_llm_test.py --backend gosp --rounds 2
"""
import argparse
import importlib.util
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
import subprocess as _sp

RANI_ENGINE = Path(r"C:\Users\clint\Desktop\EveUnleashed_Selector\agents\adversarial\engine.py")
OUT = Path.home() / "RANI_LLM_evidence.json"
GATEWAY = "http://127.0.0.1:18080/v1/chat/completions"


def _strip_thinking(text: str, reasoning: str = "") -> tuple[str, int]:
    """Remove thinking traces; return (answer, think_chars)."""
    think = reasoning or ""
    stripped, n = re.subn(r"<think>.*?</think>", "", text or "",
                          flags=re.DOTALL | re.IGNORECASE)
    # think_chars covers both reasoning_content and inline <think> blocks
    return stripped.strip(), len(think) + (len(text or "") - len(stripped))


def gateway_infer(prompt: str, model: str, max_tokens: int) -> tuple[str, int]:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }).encode()
    req = urllib.request.Request(
        GATEWAY, data=body, headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    routed = str(data.get("model") or "")
    # Match against the model dir/file segments only: full Windows paths
    # contain junk substrings ("jan" matches OmniCoder's \Jan\data dir).
    _segs = "/".join(routed.replace("\\", "/").split("/")[-2:]).lower()
    if model.lower() not in _segs:
        raise RuntimeError(
            f"gateway routed '{model}' to '{routed}' — no upstream matches; "
            f"battery would silently test the wrong model")
    msg = data["choices"][0].get("message") or {}
    return _strip_thinking(msg.get("content") or "",
                           msg.get("reasoning_content") or "")


def provider_infer(provider_name: str, model: str | None, prompt: str,
                   max_tokens: int) -> tuple[str, int]:
    """Stdlib OpenAI-compatible call (works on 3.14t where the openai SDK
    can't import). URL/model/key resolve via the provider registry."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
    from CORE.inference.providers import get_provider
    prov = get_provider(provider_name)
    if not prov.has_valid_key():
        raise RuntimeError(f"no valid key for provider '{provider_name}'")
    base = prov.config.base_url.rstrip("/")
    key = prov.config.api_key  # never printed or logged
    use_model = model or prov.config.model
    body = json.dumps({
        "model": use_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }).encode()
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}"}
    headers.update(getattr(prov.config, "extra_headers", {}) or {})
    req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    msg = data["choices"][0].get("message") or {}
    text, think_n = _strip_thinking(msg.get("content") or "",
                                    msg.get("reasoning_content") or "")
    return text or "[empty response]", think_n


def gosp_infer(prompt: str, max_tokens: int) -> tuple[str, int]:
    # (2026-09-10 fix: the WSL path was hardcoded, so --backend gosp
    # always died on Windows with a bare ImportError. GOSP_PARSER_PATH
    # env overrides; error names every location tried.)
    # (2026-09-10 bridge: the parser lives in Ubuntu-26.04 WSL
    # (pure-stdlib, 3.4ms parses). Try direct import first (works when
    # GOSP_PARSER_PATH points at a Windows-readable copy), then fall
    # back to the `wsl` subprocess bridge. GOSP_WSL_DISTRO overrides.)
    candidates = [os.environ.get("GOSP_PARSER_PATH") or "",
                  "/root/gossamer-oxford-parser"]
    for _base in candidates:
        if not _base:
            continue
        try:
            if os.path.isdir(_base):
                if _base not in sys.path:
                    sys.path.insert(0, _base)
                from semantical_main import GossamerOxfordParser
                res = GossamerOxfordParser(max_workers=4).parse(
                    prompt, depth="full", learn=False)
                return _gosp_format(prompt, res), 0
        except Exception:
            continue
    # WSL bridge.
    _distro = os.environ.get("GOSP_WSL_DISTRO", "Ubuntu-26.04")
    _distros = [_distro] if _distro else []
    _distros.append("")  # default distro last
    _errs = []
    for _d in _distros:
        _cmd = (["wsl", "-d", _d] if _d else ["wsl"]) + [
            "python3", "/root/gossamer-oxford-parser/"
            "semantical_main.py",
            "--text", prompt, "--format", "json"]
        try:
            _p = _sp.run(_cmd, capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
        except Exception as e:
            _errs.append("%s: %s" % (_d or "default", str(e)[:100]))
            continue
        if _p.returncode != 0:
            _errs.append("%s: exit %s %s" % (
                _d or "default", _p.returncode, _p.stderr.strip()[-160:]))
            continue
        try:
            _out = _p.stdout
            _res = json.loads(_out[_out.index("{"): _out.rindex("}") + 1])
            return _gosp_format(prompt, _res), 0
        except Exception as e:
            _errs.append("%s: bad json (%s)" % (_d or "default", str(e)[:80]))
            continue
    raise RuntimeError(
        "GOSP parser unreachable (tried: %s; bridge: %s). Set "
        "GOSP_PARSER_PATH." % (
            ", ".join(candidates),
            "; ".join(_errs) or "(nothing configured)"))


def _gosp_format(prompt: str, res: dict) -> str:
    lines = [f"INPUT: {prompt}"]
    for i, t in enumerate(res.get("parse_trees", [])[:2]):
        lines.append(f"TREE[{i}]: {json.dumps(t, default=str)[:600]}")
    return "\n".join(lines)


def main() -> int:
    # (2026-09-10: Windows console is cp1252 — model output with unicode
    # dashes crashed printing mid-battery, losing the evidence write.
    # Replace unencodables instead of dying.)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="RANI battery vs selectable backend")
    ap.add_argument("--backend", default="gateway:omnicoder",
                    help="gateway:<model> | gosp | <provider>[:<model>]")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="override per-model budget (default: model profile)")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel inference workers (1 = sequential; use 1 for "
                         "single small local models that serialize anyway)")
    args = ap.parse_args()

    spec = importlib.util.spec_from_file_location("rani_engine", RANI_ENGINE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    t0 = time.time()
    spec_lower = args.backend.lower()

    # Per-model token budgets (2026-09-09): thinking models burn the whole
    # budget reasoning and answer nothing; direct answerers waste wall time
    # on oversized budgets. Substring match, first hit wins.
    # (2026-09-09 fix: adversarial rounds inflate thinking — OmniCoder
    # burned 400/400 tokens on reasoning_content and answered empty, so
    # its battery read as 0.00 "nonresponsive" even though the model is
    # fine. Thinking-heavy models get headroom: content must survive the
    # adversarial rounds, not just R0. Bumped again to 4096 so deep
    # reasoning + full answer both fit comfortably inside one budget.)
    # (2026-09-10: budgets increased across the board — truncation was
    # still cutting deep-reasoning answers short on fresh evidence.)
    MODEL_BUDGETS = [("omni", 8192), ("heretic", 8192), ("gemma", 600),
                     ("jan", 300), ("qwen", 400), ("llama", 400)]
    budget = args.max_tokens
    if budget is None:
        budget = 500
        for _sub, _b in MODEL_BUDGETS:
            if _sub in spec_lower:
                budget = _b
                break

    if spec_lower == "gosp":
        backend = "GOSP"
        fn = lambda p: gosp_infer(p, budget)  # noqa: E731
    elif spec_lower.startswith("gateway:"):
        model = args.backend.split(":", 1)[1] or "omnicoder"
        backend = f"{model}-via-gateway"
        fn = lambda p: gateway_infer(p, model, budget)  # noqa: E731
    else:
        parts = args.backend.split(":", 1)
        prov_name, prov_model = parts[0], (parts[1] if len(parts) > 1 else None)
        backend = f"{prov_name}:{prov_model}" if prov_model else prov_name
        fn = lambda p: provider_infer(prov_name, prov_model, p, budget)  # noqa: E731

    def infer(prompt: str) -> str:
        text, think_n = fn(prompt)
        infer.last_think = think_n
        infer.think_by_prompt[prompt[:120]] = think_n
        return text
    infer.last_think = 0
    infer.think_by_prompt = {}

    engine = mod.AdversarialEngine(
        mod.AdversarialConfig(adversarial_rounds=args.rounds, recursion_depth=1,
                              max_workers=args.workers))
    engine.register_inference(infer, name=backend)

    probe = "Explain why the sky is blue in one sentence."
    rounds = engine.run_adversarial_battery(probe, num_rounds=args.rounds)
    sm = engine.get_summary()
    report = {
        "test": "RANI x live backend",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backend": backend,
        "max_tokens": budget,
        "elapsed_s": round(time.time() - t0, 2),
        "engine_summary": {
            "total_rounds": sm.get("total_rounds"),
            "adversarial_detected": sm.get("adversarial_detected"),
            "avg_latency_ms": round(sm.get("avg_latency_ms", 0), 1),
            "avg_score": round(sm.get("avg_score", 0), 4),
        },
        "rounds": [{
            "round_id": r.round_id,
            "backend": r.backend,
            "prompt": r.prompt[:100],
            "score": round(r.score, 3),
            "critique": (r.critique or "")[:80],
            "adversarial_flag": r.adversarial_flag,
            "latency_ms": round(r.latency_ms, 1),
            "think_chars": infer.think_by_prompt.get(r.prompt[:120], 0),
            "response_prefix": (r.response or "")[:200],
        } for r in rounds],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print("=== RANI x live backend ===")
    print(f"backend: {backend}  rounds: {len(rounds)}  "
          f"adv={sm.get('adversarial_detected')} avg_score={sm.get('avg_score', 0):.3f}")
    for r in report["rounds"]:
        print(f"  R{r['round_id']} backend={r['backend']} score={r['score']:.2f} "
              f"flag={r['adversarial_flag']} lat={r['latency_ms']:.0f}ms")
        print(f"      resp: {r['response_prefix'][:120]}")
    print(f"Evidence: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())