"""Controls for the high-end Shard catalogue and the ladder reference shard.

Two artefacts claim something about themselves and this file is where the claims are
checked rather than asserted:

  * ``docs/METTHEW/highend_families.json`` says a shard may only declare capability ids
    defined in it. That rule now has a CLI (``tools/metthew_families.py``, M-13), so the
    tests drive the tool and check the manifests that actually exist rather than
    reimplementing its rules. The tool's own controls are exercised here too, because a
    checker whose self-test is never run is a checker nobody has seen fail.
  * ``reference-shards/highend_ladder`` says every tier replays to the SAME state hash.
    That equality is worth nothing until something shows it can fail, so
    ``test_a_tier_that_touches_the_sim_breaks_the_hash`` injects the exact defect the
    gate is meant to catch (a tier leaking one point of HP per tick) and requires the
    hashes to diverge.

The validator is exercised through its CLI rather than re-implemented, so a change to the
gates is picked up here instead of drifting into a second copy of the rules.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
METTHEW = REPO / "docs" / "METTHEW"
CATALOG = METTHEW / "highend_families.json"
SHARDS = METTHEW / "reference-shards"
LADDER = SHARDS / "highend_ladder"
GOLDEN_INPUT = "0f1e2d3c4b5a6978"
GOLDEN_TICKS = 600

AFFECTS = {"sim", "presentation"}
DETERMINISM = {"exact", "approximate"}


def catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def vocabulary() -> dict:
    """Every capability object the catalogue defines, keyed by id."""
    data = catalog()
    out = {}
    for capability in data["shared"]:
        out[capability["id"]] = capability
    for family in data["families"]:
        for capability in family["capabilities"]:
            out[capability["id"]] = capability
    return out


def shard_manifests() -> list:
    return sorted(SHARDS.glob("*/shard.json"))


def load_ladder():
    spec = importlib.util.spec_from_file_location("highend_ladder_main", LADDER / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ladder():
    return load_ladder()


# --------------------------------------------------------------------------- #
# The catalogue's own claims
# --------------------------------------------------------------------------- #

def test_capability_ids_are_defined_once_with_one_meaning():
    data = catalog()
    seen = {}
    entries = list(data["shared"])
    for family in data["families"]:
        entries.extend(family["capabilities"])
    for capability in entries:
        key = (capability["affects"], capability["determinism"])
        if capability["id"] in seen:
            assert seen[capability["id"]] == key, (
                "%r is defined twice with different semantics: %s vs %s"
                % (capability["id"], seen[capability["id"]], key))
        seen[capability["id"]] = key


def test_capability_domain_and_the_sim_rule():
    for capability_id, capability in vocabulary().items():
        assert capability["affects"] in AFFECTS, capability_id
        assert capability["determinism"] in DETERMINISM, capability_id
        if capability["affects"] == "sim":
            assert capability["determinism"] == "exact", (
                "%r affects the simulation with determinism %r; an approximate value in "
                "the authoritative state makes a golden run unreproducible"
                % (capability_id, capability["determinism"]))


def test_every_family_states_a_gate_and_a_negative_control():
    for family in catalog()["families"]:
        acceptance = family.get("acceptance") or {}
        gate = acceptance.get("gate") or ""
        control = acceptance.get("negative_control") or ""
        assert len(gate) > 20, "%s has no usable gate" % family["id"]
        assert len(control) > 20, "%s has no usable negative control" % family["id"]
        assert gate != control, family["id"]
        assert family["host"]["requires"] in ("python-resident", "wasm3-det", "native")


def test_reference_shards_only_declare_catalogue_capabilities():
    known = set(vocabulary())
    for path in shard_manifests():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for capability in manifest.get("capabilities") or []:
            assert capability["id"] in known, (
                "%s declares %r, which is not defined in highend_families.json"
                % (path.parent.name, capability["id"]))


def test_the_catalogue_is_not_decorative():
    """At least one family capability must actually be declared by a shipped shard."""
    known = set(vocabulary())
    declared = set()
    for path in shard_manifests():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        declared.update(capability["id"] for capability in manifest.get("capabilities") or [])
    used = declared & known
    family_only = used - {capability["id"] for capability in catalog()["shared"]}
    assert family_only, (
        "no reference shard declares a family-specific capability, so the families are "
        "prose; declared from the catalogue were %s" % sorted(used))


# --------------------------------------------------------------------------- #
# The ladder's claim: four tiers, one hash
# --------------------------------------------------------------------------- #

def test_one_state_hash_across_every_tier(ladder):
    raw = bytes.fromhex(GOLDEN_INPUT)
    hashes = {ladder.state_hash(ladder.run(GOLDEN_TICKS, raw)) for _ in ladder.LEVELS}
    assert len(hashes) == 1, "tiers disagree: %s" % sorted(hashes)


def test_presentation_actually_differs_between_tiers(ladder):
    """The opposite failure: identical tiers would pass the hash test trivially."""
    raw = bytes.fromhex(GOLDEN_INPUT)
    state = ladder.run(GOLDEN_TICKS, raw)
    rendered = {json.dumps(ladder.presentation(level, state), sort_keys=True)
                for level in ladder.LEVELS}
    assert len(rendered) == len(ladder.LEVELS)


def test_a_tier_that_touches_the_sim_breaks_the_hash(ladder):
    """NEGATIVE CONTROL.

    The gate above asserts equality across tiers. This injects the exact defect that
    equality exists to catch -- the tier leaking one point of HP per tick -- and requires
    the hashes to diverge. If this test ever passes for the wrong reason (hashes equal
    anyway), the equality gate is measuring nothing.
    """
    raw = bytes.fromhex(GOLDEN_INPUT)

    def run_with_sim_tier(level: int) -> str:
        state = ladder.initial_state()
        for tick in range(GOLDEN_TICKS):
            ladder.step(state, ladder.decode_input(raw, tick))
            if state["hp"][0] > 0:
                state["hp"][0] -= level          # the injected leak
        return ladder.state_hash(state)

    hashes = {run_with_sim_tier(level["level"]) for level in ladder.LEVELS}
    assert len(hashes) > 1, ("a per-tier simulation leak did not change the hash, so the "
                             "one-hash gate cannot detect a renderer touching the sim")


def test_golden_run_reproduces_the_manifest(ladder):
    manifest = json.loads((LADDER / "shard.json").read_text(encoding="utf-8"))
    raw = bytes.fromhex(manifest["golden_run"]["input"])
    state = ladder.run(manifest["golden_run"]["ticks"], raw)
    assert ladder.state_hash(state) == manifest["golden_run"]["state_sha256"]


def test_no_float_reaches_the_canonical_state(ladder):
    text = ladder.canonical(ladder.initial_state())
    assert "." not in text, "canonical state carries a decimal point: %r" % text


def test_ladder_cli_prints_the_golden_hash():
    completed = subprocess.run(
        [sys.executable, str(LADDER / "main.py"), "--ticks", str(GOLDEN_TICKS),
         "--input", GOLDEN_INPUT, "--headless"],
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    manifest = json.loads((LADDER / "shard.json").read_text(encoding="utf-8"))
    assert payload["state_sha256"] == manifest["golden_run"]["state_sha256"]
    assert payload["token"] == manifest["golden_run"]["token"]


def test_forced_tier_reports_whether_it_actually_fits():
    """`--level 3 --budget 0` must not claim the tier fits its budget."""
    completed = subprocess.run(
        [sys.executable, str(LADDER / "main.py"), "--level", "3", "--budget", "0",
         "--headless"],
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["level"] == 3
    assert payload["fits_budget"] is False


# --------------------------------------------------------------------------- #
# The validator, through its own CLI
# --------------------------------------------------------------------------- #

def test_high_end_file_cap_is_enforced_not_just_declared():
    """V4c's control: HIGHEND_FILE_CAP existed for a year as a number nothing checked.

    A cap that cannot fail is documentation. This drives the validator's own function
    with a manifest one file over the ceiling and requires the refusal to name it.
    """
    spec = importlib.util.spec_from_file_location(
        "metthew_validate", REPO / "tools" / "metthew_validate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = json.loads((METTHEW / "fixtures" / "valid_highend_rollback_duel.json")
                      .read_text(encoding="utf-8"))
    over = json.loads(json.dumps(base))
    filler = [{"path": "assets/f_%04d.bin" % index, "sha256": "%064x" % index,
               "role": "asset", "size_bytes": 1}
              for index in range(module.HIGHEND_FILE_CAP + 1)]
    over["files"] = filler
    over["budget"]["payload_bytes_max"] = 1 << 40
    over["tick_budget"] = {"p50_ms": 6.0, "p99_ms": 15.2, "scene": "duel-4p"}
    over["budget"]["tick_budget_ms"] = {"p50": 6.0, "p99": 15.2, "scene": "duel-4p"}
    problems = module.invariant_problems(over)
    assert any("V4 tier-gate" in problem and str(module.HIGHEND_FILE_CAP) in problem
               for problem in problems), problems


@pytest.mark.parametrize("manifest", shard_manifests(), ids=lambda p: p.parent.name)
def test_reference_shard_passes_the_validator(manifest):
    completed = subprocess.run(
        [sys.executable, str(REPO / "tools" / "metthew_validate.py"),
         "--manifest", str(manifest)],
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr


# --------------------------------------------------------------------------- #
# The catalogue gate (M-13) -- F1..F7, through its own CLI
# --------------------------------------------------------------------------- #

def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPO / "tools" / script), *args],
                          capture_output=True, text=True, timeout=180)


def test_the_catalogue_gate_accepts_the_shipped_catalogue():
    completed = _run("metthew_families.py")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PASS" in completed.stdout
    # Coverage is printed, never implied: the run must say how much is checkable.
    assert "lineage:" in completed.stdout and "checkable" in completed.stdout


def test_the_catalogue_gate_controls_are_green():
    """NEGATIVE CONTROL, in full: ten mutations of the real catalogue must be refused.

    A gate is unvalidated until its healthy fixture passes AND every known defect is
    caught. ``--selftest`` applies each mutation to the shipped data, so this test fails
    when the catalogue changes in a way that makes a control unreachable.
    """
    completed = _run("metthew_families.py", "--selftest")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "SELFTEST OK" in completed.stdout, completed.stdout
    assert "healthy control" in completed.stdout


def test_a_linked_family_shard_really_declares_that_family_capability():
    """The reference_shards link is only worth having if it can be wrong.

    F6's control lives in the tool; this asserts the shipped state: every shard a family
    names exists, and declares at least one capability id that family defines. A family
    whose shard declared only shared[] ids would be a citation of nothing.
    """
    data = catalog()
    by_family = {family["id"]: family for family in data["families"]}
    checked = 0
    for family in data["families"]:
        for link in family.get("reference_shards") or []:
            manifest_path = SHARDS / link / "shard.json"
            assert manifest_path.is_file(), "%s names %s, which does not exist" % (
                family["id"], link)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            declared = {entry["id"] for entry in manifest.get("capabilities") or []}
            own = {entry["id"] for entry in by_family[family["id"]]["capabilities"]}
            assert declared & own, "%s names %s, which uses none of its capabilities" % (
                family["id"], link)
            checked += 1
    assert checked, "no family names a reference shard, so F6 is untested by the data"


def test_no_byte_figure_survives_without_a_measurement():
    """The eight payload_bytes_estimate values are gone, and measured claims carry proof."""
    for family in catalog()["families"]:
        assert "payload_bytes_estimate" not in family, family["id"]
        claim = family["payload_claim"]
        if claim["status"] == "measured":
            assert claim["bytes"] > 0 and claim["command"] and claim["measured_on"]
        else:
            assert claim["status"] == "unmeasured" and claim["reason"]


# --------------------------------------------------------------------------- #
# V13 / V14 / V15 -- the gates added because their absence was measured
# --------------------------------------------------------------------------- #

def _validator():
    spec = importlib.util.spec_from_file_location(
        "metthew_validate_gates", REPO / "tools" / "metthew_validate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_both_reference_shard_addresses_recompute():
    """V14. Before this, bundle_fingerprint hashed the bare sha values while both
    SHARD.md files documented '<path> <sha256>' lines, so no id could be reproduced."""
    module = _validator()
    for path in shard_manifests():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        declared = manifest["id"].split("@sha256:")[1]
        assert declared == module.bundle_fingerprint(manifest), path.parent.name


def test_a_changed_byte_breaks_the_declared_bundle(tmp_path):
    """V15 -- the one that would have caught an unresealed edit.

    The manifest's files[] used to be trusted: a shard whose SHARD.md was edited kept a
    stale hash and still validated. Copy a real shard, change one byte, require the
    refusal, then require the untouched copy to pass.
    """
    module = _validator()
    schema = module.load_schema()
    source = LADDER
    copy_dir = tmp_path / "highend_ladder"
    copy_dir.mkdir()
    for name in ("main.py", "SHARD.md"):
        (copy_dir / name).write_bytes((source / name).read_bytes())
    manifest = json.loads((source / "shard.json").read_text(encoding="utf-8"))
    assert module.validate_manifest(manifest, schema, copy_dir) == []
    (copy_dir / "SHARD.md").write_bytes((source / "SHARD.md").read_bytes() + b"\n")
    problems = module.validate_manifest(manifest, schema, copy_dir)
    assert any(problem.startswith("V15 bundle-bytes") for problem in problems), problems


def test_tick_drift_refuses_an_invented_budget_and_accepts_a_measured_one():
    """V13. The band is 4x: wide enough for a loaded box, narrow enough for a lie."""
    module = _validator()
    manifest = {"id": "x@sha256:" + "0" * 64,
                "tick_budget": {"p50_ms": 0.00074, "p99_ms": 0.00078}}
    measured = {"p50_ms": 0.000741, "p99_ms": 0.0008, "ticks": 360, "repeats": 5,
                "entrypoint": "main.py"}
    assert module.tick_drift_problems(manifest, measured) == []
    invented = dict(manifest, tick_budget={"p50_ms": 100.0, "p99_ms": 200.0})
    problems = module.tick_drift_problems(invented, measured)
    assert problems and all(problem.startswith("V13") for problem in problems), problems
    # Noise the band is meant to survive: a 3x slower box must NOT be refused.
    noisy = {"p50_ms": 0.0022, "p99_ms": 0.0024, "ticks": 360, "repeats": 5,
             "entrypoint": "main.py"}
    assert module.tick_drift_problems(manifest, noisy) == []


def test_the_tick_measurement_contract_is_real(ladder):
    """measure_tick_budget times the shard's own run(), on its own golden input."""
    module = _validator()
    manifest = json.loads((LADDER / "shard.json").read_text(encoding="utf-8"))
    measured = module.measure_tick_budget(LADDER, manifest, repeats=3)
    assert measured["ticks"] == manifest["golden_run"]["ticks"]
    assert measured["p50_ms"] > 0 and measured["p99_ms"] >= measured["p50_ms"]
    # And the same timing, from the module the test already holds, agrees on the hash.
    raw = bytes.fromhex(manifest["golden_run"]["input"])
    state = ladder.run(manifest["golden_run"]["ticks"], raw)
    assert ladder.state_hash(state) == manifest["golden_run"]["state_sha256"]


def test_a_shard_without_run_is_unmeasurable_not_healthy(tmp_path):
    module = _validator()
    (tmp_path / "main.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    manifest = {"entrypoint": "main.py",
                "golden_run": {"input": "00", "ticks": 1}}
    with pytest.raises(module.MeasureError):
        module.measure_tick_budget(tmp_path, manifest, repeats=1)
