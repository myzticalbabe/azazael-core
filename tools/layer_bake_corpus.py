"""layer_bake_corpus.py — split the merged bake set into MoE domain layers.

Reads state/bake_merged.jsonl and routes every row to exactly one layer
(first ruleset match wins; Nowadays-last `misc` bucket is reported, not
hidden, so classifier gaps stay visible). Layers are tailored to THIS box
(Windows dev machine, azazael repo, Python 3.14t, local + free-tier
inference): coding fixes, Windows ops, lint/gate discipline, memory
systems, inference, ML training/evolution, process discipline, and a
quarantined env-inventory layer (context, not training signal — kept out
of the training layers by design so it cannot dilute them).

Same record schema (no new keys — merge, dedupe and demo prompts read
these files unchanged). Writes state/bake_layer_<name>.jsonl plus
state/bake_layers_manifest.json {layer: {rows, top_sources}}.

Usage: python tools/layer_bake_corpus.py [--apply]
  default = dry run (distribution only). --apply writes files.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_MERGED = ROOT / "state" / "bake_merged.jsonl"
OUT_MANIFEST = ROOT / "state" / "bake_layers_manifest.json"

# Ordered (first match wins). Patterns run against title + "\\n" + body.
RULES = [
    ("lint-gates", r"compliance|anti.pattern|scanner|gate|ratch|lint|PF1\d|PF0\d|"
                   r"stale base|except.pass|bare pass|import_inside"),
    ("memory-systems", r"\blocal_memory\b|MemoryBus|PamsRecorder|memory_store|"
                       r"wal_autocheckpoint|pams|engram|mem_save|mem_search|"
                       r"technique.link|reward.ledger|audit trail|audit_trail"),
    ("inference", r"ollama|llama.cpp|llama.server|nim_client|provider|"
                  r"failover|quantiz|gguf|onnx|mmproj|eve-v2u|openrouter|"
                  r"groq|cohere|mistral|gemini|inference|embedding|qwen|"
                  r"gemma|nemotron|glyph|moe\b|mixture.of.experts"),
    ("media-render", r"puppeteer|ffmpeg|manim|\bcain\b|video|timeline|"
                      r"photoreal"),
    ("ml-training", r"trainer|evolution|neural.pipeline|cutting.edge|qd_|"
                    r"qd |ablation|hyperopt|fitness|mutation|QD-7|"
                    r"run_training|meta_evolution|algorithm_lab|nsga"),
    ("windows-ops", r"\bwsl\b|windows|desktop|shortcut|\.bat\b|powershell|"
                    r"cp1252|encoding|UI.TARS|llama.server|llama-server|"
                    r"driver|nvidia|cuda|choco|winget|registry|"
                    r"bluetooth|vlc|obsidian|chrome|clipboard|"
                    r" Berks |grant\b|python 3\.14|interpreter|venv|pip install"),
    ("code-fix", r"fix|repair|resolv|bug|defect|passed|failed|pytest|"
                 r"TypeError|ImportError|NameError|ValueError|AttributeError|"
                 r"traceback|assert|commit|diff|merge\b|predicate|"
                 r"ledger|wire.or.cut|worklist|registry|worm|technique|"
                 r"orphan|blind|absorb|shelfware|swallow|baseline"),
    ("process", r"mandate|rule 0|SOP|lesson|violation|anti.pattern|"
                r"attestat|proof.of.life|lane|brief|engram #|AIO|gate "
                r"semantics|sliced commit|bulk|disposition|triage|"
                r"workflow|session|standup|handoff|roster"),
    ("env-inventory", r"drive contents|directory listing|AZAZAEL_BOOT|"
                      r"inventory|wheels|ISO files|documentation|"
                      r"\.env\.example|D-drive|E:\\|G:\\"),
]


def classify(title, body):
    text = (title + "\n" + body).lower()
    for layer, pat in RULES:
        if re.search(pat, text):
            return layer
    return "misc"


def main():
    apply = "--apply" in sys.argv
    rows = [json.loads(l) for l in
            IN_MERGED.read_text(encoding="utf-8").splitlines() if l.strip()]
    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(classify(r.get("title", ""), r.get("body", "")),
                           []).append(r)
    total = 0
    manifest = {}
    for layer in [l for l, _ in RULES] + ["misc"]:
        rs = buckets.get(layer, [])
        total += len(rs)
        srcs = Counter(x.get("source", "?").split("#")[0].split(":")[0]
                       for x in rs)
        manifest[layer] = {"rows": len(rs),
                           "top_sources": dict(srcs.most_common(5))}
        print("%-15s %5d" % (layer, len(rs)))
    print("%-15s %5d" % ("TOTAL", total))
    if apply:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE.sentinel_enforcer import permit_here
        with permit_here("bake layer split output"):
            for layer, rs in buckets.items():
                fp = ROOT / "state" / ("bake_layer_%s.jsonl" % layer)
                with open(fp, "w", encoding="utf-8") as fh:
                    for r in rs:
                        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            with open(OUT_MANIFEST, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=1)
        print("WROTE %d layer files + manifest" % len(buckets))


if __name__ == "__main__":
    main()
