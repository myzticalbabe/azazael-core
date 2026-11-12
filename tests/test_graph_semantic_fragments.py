# coding: utf-8
"""Every fragment `build_graph.py` merges must be produced by a pipeline step.

The failure this guards: someone adds a layer to `build_graph.FRAGS` and forgets
to add it to `run_extraction.STEPS` (so the fragment is never written and the merge
silently uses a stale file), or adds a step and forgets the fragment (so its output
is never merged). The list is read from source text, so importing graphify is not
required.
"""
import re
from pathlib import Path

GS = Path(__file__).resolve().parents[1] / "tools" / "graph_semantic"

# fragment file stem -> the step script that writes it
SPECIAL = {"semantic_fragment": "semantic_layer.py"}


def _fragments() -> set[str]:
    text = (GS / "build_graph.py").read_text(encoding="utf-8")
    block = re.search(r"FRAGS = \[(.*?)\]", text, re.S)
    assert block, "FRAGS list not found in build_graph.py"
    return set(re.findall(r'"graphify-out/([A-Za-z0-9_]+_fragment)\.json"', block.group(1)))


def _steps() -> set[str]:
    text = (GS / "run_extraction.py").read_text(encoding="utf-8")
    block = re.search(r"STEPS = \[(.*?)\n\]", text, re.S)
    assert block, "STEPS list not found in run_extraction.py"
    return set(re.findall(r'\("([A-Za-z0-9_]+\.py)"', block.group(1)))


def test_every_merged_fragment_has_a_producing_step():
    steps = _steps()
    missing = []
    for stem in _fragments():
        script = SPECIAL.get(stem, f"{stem.removesuffix('_fragment')}.py")
        if script not in steps:
            missing.append((stem, script))
    assert missing == [], f"fragments with no producing step: {missing}"


def test_pipeline_owns_the_expected_layers():
    steps = _steps()
    for required in ("semantic_layer.py", "doc_layer.py", "concept_layer.py",
                     "coverage_layer.py", "rationale_tranche3.py",
                     "enhanced_semantic.py", "bridge_layer.py"):
        assert required in steps, f"{required} missing from STEPS"
