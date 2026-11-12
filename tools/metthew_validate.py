#!/usr/bin/env python3
# coding: utf-8
"""metthew_validate.py -- M-03: judge a shard manifest before anyone plays it.

What this is, and what it is NOT
--------------------------------
It checks a manifest against ``docs/METTHEW/shard.schema.json`` (draft-07, the
subset the schema actually uses) and then runs the cross-field invariants the
schema cannot state in a sentence. It does **not** execute the shard, so it does
not verify the golden run. That gap is printed on every run rather than implied
by a green line: a schema-valid shard with an invented state hash is exactly the
failure this project's own canon warns about, and a validator that called that
"verified" would be the false green in a new costume.

The determinism partition (v1.1)
--------------------------------
High-end shards live or die on this distinction, so the validator enforces it
rather than trusting the author's prose:

  * a capability that ``affects: sim`` must be ``determinism: exact`` -- an
    approximate value inside the authoritative state means two peers diverge and
    no golden run can be trusted again;
  * any ``presentation`` capability that is not exact requires
    ``golden_run.headless: true``, because a hash taken through an upscaler, a
    frame generator or a denoiser is a hash of the painter and not of the game;
  * ``tier: highend`` requires ``headless: true`` explicitly, and at least one
    declared capability -- a high-end shard that declares none is a low-end
    shard in expensive clothes.

Refusals carry the invariant id that fired (``V7 sim-capability-exact``), so a
disagreement about a verdict is a disagreement about a named rule.

Usage
-----
  python tools/metthew_validate.py --manifest docs/METTHEW/reference-shards/highend_duel/shard.json
  python tools/metthew_validate.py --fixtures                    # every fixture
  python tools/metthew_validate.py --fixtures --json
  python tools/metthew_validate.py --schema                      # schema only

Fixtures follow a naming convention that is itself the control: ``valid_*`` must
PASS, everything else must FAIL, and ``--fixtures`` reports both directions. A
fixture set where every file passes has stopped being a control.

Exit codes: 0 clean, 1 a manifest was refused, 2 the schema or a manifest could
not be read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
METTHEW = REPO / "docs" / "METTHEW"
SCHEMA_PATH = METTHEW / "shard.schema.json"
FIXTURES = METTHEW / "fixtures"

LOWEND_PAYLOAD_CAP = 2 * 1024 * 1024
LOWEND_FILE_CAP = 24
HIGHEND_PAYLOAD_CAP = 8 * 1024 * 1024 * 1024
HIGHEND_FILE_CAP = 1024
DEFAULT_SIM_HZ = 30


class ValidationError(Exception):
    """The schema or a manifest could not be read at all -- not a verdict."""


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def load_json(path: Path) -> Any:
    if not path.is_file():
        raise ValidationError("not found: %s" % path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("%s is not readable JSON: %s" % (path, exc)) from exc


def load_schema(path: Path = SCHEMA_PATH) -> Dict:
    schema = load_json(path)
    if not isinstance(schema, dict):
        raise ValidationError("%s must contain a JSON object" % path)
    return schema


# --------------------------------------------------------------------------- #
# A draft-07 subset, on purpose
# --------------------------------------------------------------------------- #
# The validator is lifted into a circle where nothing is installed, so it may not
# import jsonschema. Implementing only the keywords the schema uses keeps that
# promise honest: an unknown keyword is reported, never silently ignored, because
# a checker that skips a rule it does not understand reports a pass it has not
# earned.

SUPPORTED = {
    "type", "required", "properties", "additionalProperties", "items", "enum",
    "const", "pattern", "minimum", "maximum", "maxItems", "minItems", "default",
    "description", "$schema", "$id", "title",
}


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return True


def schema_problems(instance: Any, schema: Dict, where: str = "$") -> List[str]:
    """Every schema violation, as a sentence naming the path that broke."""
    problems: List[str] = []
    if not isinstance(schema, dict):
        return ["%s: schema node is not an object" % where]

    unknown = set(schema) - SUPPORTED
    for keyword in sorted(unknown):
        problems.append("%s: schema keyword %r is not supported by this validator"
                        % (where, keyword))

    expected = schema.get("type")
    if expected is not None and not _type_ok(instance, expected):
        # A type failure answers everything else about this node; recursing would
        # produce a pile of secondary noise that hides the one real defect.
        return problems + ["%s: expected %s, found %s"
                           % (where, expected, type(instance).__name__)]

    if "const" in schema and instance != schema["const"]:
        problems.append("%s: must be %r, found %r" % (where, schema["const"], instance))
    if "enum" in schema and instance not in schema["enum"]:
        problems.append("%s: %r is not one of %s"
                        % (where, instance, ", ".join(map(str, schema["enum"]))))
    if isinstance(instance, str):
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, instance):
            problems.append("%s: %r does not match %s" % (where, instance, pattern))
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            problems.append("%s: %r is below the minimum %r"
                            % (where, instance, schema["minimum"]))
        if "maximum" in schema and instance > schema["maximum"]:
            problems.append("%s: %r is above the maximum %r"
                            % (where, instance, schema["maximum"]))

    if isinstance(instance, list):
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            problems.append("%s: %d item(s) exceeds the maximum of %d"
                            % (where, len(instance), schema["maxItems"]))
        if "minItems" in schema and len(instance) < schema["minItems"]:
            problems.append("%s: %d item(s) is fewer than the minimum of %d"
                            % (where, len(instance), schema["minItems"]))
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for position, item in enumerate(instance):
                problems.extend(schema_problems(item, item_schema,
                                                "%s[%d]" % (where, position)))

    if isinstance(instance, dict):
        properties = schema.get("properties") if isinstance(
            schema.get("properties"), dict) else {}
        for key in schema.get("required", []) or []:
            if key not in instance:
                problems.append("%s: missing required field %r" % (where, key))
        for key, value in instance.items():
            if key in properties:
                problems.extend(schema_problems(value, properties[key],
                                                "%s.%s" % (where, key)))
            elif schema.get("additionalProperties") is False:
                problems.append("%s: field %r is not in the schema "
                                "(additionalProperties is false)" % (where, key))
    return problems


# --------------------------------------------------------------------------- #
# Cross-field invariants
# --------------------------------------------------------------------------- #

def tier_of(manifest: Dict) -> str:
    return manifest.get("tier") or "any"


def sim_hz(manifest: Dict) -> int:
    value = manifest.get("sim_hz")
    return value if isinstance(value, int) and value > 0 else DEFAULT_SIM_HZ


def invariant_problems(manifest: Dict) -> List[str]:
    """The rules a draft-07 schema cannot state, each tagged with its id."""
    problems: List[str] = []
    tier = tier_of(manifest)
    lowend = tier in ("lowend", "any")
    files = [entry for entry in manifest.get("files") or [] if isinstance(entry, dict)]

    # V1 -- a bundle is a directory tree, so no path may leave it, and no two
    # entries may name the same file.
    seen_paths = set()
    for entry in files:
        path = entry.get("path")
        if not isinstance(path, str):
            continue
        if path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
            problems.append("V1 file-path: %r is absolute or escapes the shard" % path)
        if path in seen_paths:
            problems.append("V1 file-path: %r is listed twice" % path)
        seen_paths.add(path)

    # V2 -- the entrypoint must be in the bundle, and it must be code.
    entrypoint = manifest.get("entrypoint")
    roles = {entry.get("path"): entry.get("role") for entry in files}
    if isinstance(entrypoint, str) and entrypoint not in roles:
        problems.append("V2 entrypoint-in-bundle: %r is not listed in files[]"
                        % entrypoint)
    elif isinstance(entrypoint, str) and roles.get(entrypoint) != "code":
        problems.append("V2 entrypoint-in-bundle: %r is listed with role %r, not code"
                        % (entrypoint, roles.get(entrypoint)))

    # V3 -- the declared payload must actually cover the files it claims.
    budget = manifest.get("budget") or {}
    declared_max = budget.get("payload_bytes_max")
    sizes = [entry.get("size_bytes") for entry in files
             if isinstance(entry.get("size_bytes"), int)]
    total = sum(sizes)
    if isinstance(declared_max, int):
        if total > declared_max:
            problems.append("V3 payload-sum: files total %d bytes, over the declared "
                            "payload_bytes_max of %d" % (total, declared_max))
        if lowend and declared_max > LOWEND_PAYLOAD_CAP:
            problems.append("V4 tier-gate: tier %r may declare at most %d bytes of "
                            "payload, found %d" % (tier, LOWEND_PAYLOAD_CAP, declared_max))
        if tier == "highend" and declared_max > HIGHEND_PAYLOAD_CAP:
            problems.append("V4 tier-gate: tier highend may declare at most %d bytes, "
                            "found %d" % (HIGHEND_PAYLOAD_CAP, declared_max))
    elif sizes:
        problems.append("V3 payload-sum: files declare sizes but budget.payload_bytes_max "
                        "is missing, so the sum cannot be judged")

    # V4b -- the 24-file ceiling is a LOW-END gate (v1.1), not a wire limit.
    if lowend and len(files) > LOWEND_FILE_CAP:
        problems.append("V4 tier-gate: tier %r allows %d files, found %d"
                        % (tier, LOWEND_FILE_CAP, len(files)))
    # V4c -- the high-end ceiling was declared and never checked, so the 1024 in the
    # docs could not fail. A cap nothing enforces is a number, not a gate.
    if tier == "highend" and len(files) > HIGHEND_FILE_CAP:
        problems.append("V4 tier-gate: tier highend allows %d files, found %d"
                        % (HIGHEND_FILE_CAP, len(files)))

    # V5 -- the tick must fit inside the simulation tick.
    tick = manifest.get("tick_budget") or {}
    p50, p99 = tick.get("p50_ms"), tick.get("p99_ms")
    rate = sim_hz(manifest)
    budget_ms = 1000.0 / rate
    if isinstance(p99, (int, float)):
        if p99 > budget_ms:
            problems.append("V5 tick-budget: p99 %sms exceeds one tick at %d Hz (%.2f ms)"
                            % (p99, rate, budget_ms))
        if isinstance(p50, (int, float)) and p50 > p99:
            problems.append("V5 tick-budget: p50 %sms is above p99 %sms" % (p50, p99))

    # V6 -- two copies of one measurement must agree. This is the estate's most
    # repeated defect: a constant mirrored into a second file that drifts.
    alias = (budget.get("tick_budget_ms") or {})
    alias_p99 = alias.get("p99")
    if isinstance(alias_p99, (int, float)) and isinstance(p99, (int, float)):
        if abs(float(alias_p99) - float(p99)) > 1e-9:
            problems.append("V6 tick-alias: budget.tick_budget_ms.p99 %s disagrees with "
                            "tick_budget.p99_ms %s" % (alias_p99, p99))
    alias_scene = alias.get("scene")
    if isinstance(alias_scene, str) and isinstance(tick.get("scene"), str):
        if alias_scene != tick["scene"]:
            problems.append("V6 tick-alias: scene %r disagrees with %r"
                            % (alias_scene, tick["scene"]))

    capabilities = [entry for entry in manifest.get("capabilities") or []
                    if isinstance(entry, dict)]

    # V7 -- the partition. Only exact work may be inside the authoritative state.
    for position, capability in enumerate(capabilities):
        if capability.get("affects") == "sim" and capability.get("determinism") != "exact":
            problems.append("V7 sim-capability-exact: capabilities[%d] %r affects sim "
                            "with determinism %r; an approximate value in the "
                            "authoritative state makes the golden run unreproducible"
                            % (position, capability.get("id"),
                               capability.get("determinism")))

    # V8 -- a high-end shard with no capabilities is a low-end shard in costume.
    if tier == "highend" and not capabilities:
        problems.append("V8 highend-needs-capabilities: tier highend declares no "
                        "capabilities, so there is nothing the higher budget is for")

    # V9 -- presentation may be approximate, but then the proof must be headless.
    golden = manifest.get("golden_run") or {}
    headless = golden.get("headless")
    approximate_presentation = [capability for capability in capabilities
                                if capability.get("affects") == "presentation"
                                and capability.get("determinism") != "exact"]
    if approximate_presentation and headless is not True:
        names = ", ".join(repr(capability.get("id"))
                          for capability in approximate_presentation)
        problems.append("V9 presentation-headless: %s are presentation-side and not "
                        "exact, so golden_run.headless must be true; the hash would "
                        "otherwise describe the renderer, not the game" % names)

    # V10 -- the highest tier must SAY it was run headless, not rely on a default.
    if tier == "highend" and headless is not True:
        problems.append("V10 highend-headless: tier highend requires "
                        "golden_run.headless: true explicitly")

    # V11 -- a wildcard allowlist is not an allowlist.
    for entry in manifest.get("env_allowlist") or []:
        if not isinstance(entry, str):
            continue
        if entry.strip() in ("*", "**", "*=*") or entry.strip().endswith("*"):
            problems.append("V11 env-wildcard: %r admits every key, which defeats the "
                            "allowlist the field exists for" % entry)

    # V12 -- coupling is counted, so a hub must be reachable from a hub-ref file
    # and a hub-ref file must name a declared hub.
    declared_hubs = {hub.get("name") for hub in manifest.get("hubs") or []
                     if isinstance(hub, dict)}
    hub_refs = {entry.get("path") for entry in files if entry.get("role") == "hub-ref"}
    for path in sorted(hub_refs):
        stem = Path(str(path)).stem
        if declared_hubs and stem not in declared_hubs:
            problems.append("V12 hub-declared: %r carries role hub-ref but no hub named "
                            "%r is declared" % (path, stem))
    if declared_hubs and not hub_refs:
        problems.append("V12 hub-declared: hub(s) %s declared but no file carries role "
                        "hub-ref, so the coupling is not visible in the bundle"
                        % ", ".join(sorted(map(str, declared_hubs))))

    problems.extend(id_address_problems(manifest))
    return problems


def bundle_fingerprint(manifest: Dict) -> str:
    """Hash of the file-set, used to spot one bundle shipped under two names.

    The definition is the one both reference shards print in their SHARD.md --
    sha256 over the sorted ``"<path> <sha256>"`` lines -- because a content address
    nobody can recompute is a filename with extra steps. This function used to hash the
    bare sha256 values instead, which disagreed with every shipped ``id`` and with the
    documentation: V14 caught it on 2026-11-01, and the fix was to implement the
    documented rule rather than to rewrite the ids.
    """
    entries = [entry for entry in manifest.get("files") or [] if isinstance(entry, dict)]
    lines = sorted("%s %s" % (entry.get("path"), entry.get("sha256"))
                   for entry in entries)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def id_address_problems(manifest: Dict) -> List[str]:
    """V14 -- the id's address must be the address these files actually have."""
    identifier = manifest.get("id")
    if not isinstance(identifier, str) or "@sha256:" not in identifier:
        return []          # the schema's pattern already refuses a malformed id
    declared = identifier.split("@sha256:", 1)[1]
    computed = bundle_fingerprint(manifest)
    if declared != computed:
        return ["V14 address-recomputes: id claims %s but the sorted '<path> <sha256>' "
                "lines of files[] hash to %s; a peer cannot reproduce the address, so "
                "it is a name" % (declared, computed)]
    return []


# --------------------------------------------------------------------------- #
# V13 -- a declared tick budget must be re-measurable
# --------------------------------------------------------------------------- #
# The two reference shards declared p50 0.0007 and 0.00071 ms per tick: numbers close
# enough to be one measurement copied sideways, and nothing compared either of them to a
# clock. A budget nobody re-measures is a claim about hardware that drifts silently when
# the code gets slower -- the exact failure the estate keeps catching in its gates.
#
# The band is 4x, deliberately wide. Timing on a shared box varies by more than 2x between
# a quiet run and a loaded one, so a tight band would false-fail a healthy shard and get
# switched off. 4x still catches the defect that matters: an invented order of magnitude.

TICK_DRIFT_FACTOR = 4.0
MEASURE_REPEATS = 5


class MeasureError(Exception):
    """The shard's tick could not be measured at all -- not a verdict about its speed."""


def measure_tick_budget(shard_dir: Path, manifest: Dict,
                        repeats: int = MEASURE_REPEATS) -> Dict[str, Any]:
    """Time ``run(ticks, bytes)`` over the manifest's own golden input.

    The measurement contract is deliberately the smallest one a reference shard already
    satisfies: a module-level ``run(ticks, raw_bytes) -> state``. Nothing is imported from
    the shard's CLI and no flag is required, so measuring cannot change what was measured.
    """
    import importlib.util
    import time

    entrypoint = manifest.get("entrypoint")
    golden = manifest.get("golden_run") or {}
    ticks = golden.get("ticks")
    payload_hex = golden.get("input")
    if not isinstance(entrypoint, str) or not entrypoint.endswith(".py"):
        raise MeasureError("entrypoint %r is not a python file, so the tick cannot be "
                           "timed here" % (entrypoint,))
    if not isinstance(ticks, int) or ticks < 1:
        raise MeasureError("golden_run.ticks is missing, so there is no run to time")
    path = Path(shard_dir) / entrypoint
    if not path.is_file():
        raise MeasureError("no entrypoint at %s" % path)
    spec = importlib.util.spec_from_file_location("shard_under_measure", path)
    if spec is None or spec.loader is None:
        raise MeasureError("cannot import %s" % path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError) as exc:
        raise MeasureError("importing %s failed: %s" % (path, exc)) from exc
    run = getattr(module, "run", None)
    if not callable(run):
        raise MeasureError("the shard exposes no run(ticks, bytes), so V13 has no "
                           "contract to time")
    try:
        raw = bytes.fromhex(str(payload_hex))
    except ValueError as exc:
        raise MeasureError("golden_run.input is not hex: %s" % exc) from exc
    if not raw:
        raise MeasureError("golden_run.input is empty")

    per_tick: List[float] = []
    for _ in range(max(1, repeats)):
        start = time.perf_counter()
        run(ticks, raw)
        per_tick.append((time.perf_counter() - start) * 1000.0 / ticks)
    per_tick.sort()
    return {"p50_ms": per_tick[len(per_tick) // 2], "p99_ms": per_tick[-1],
            "ticks": ticks, "repeats": len(per_tick), "entrypoint": entrypoint}


def tick_drift_problems(manifest: Dict, measured: Dict[str, Any]) -> List[str]:
    """V13 -- compare the declared budget with the number this box just produced."""
    declared = manifest.get("tick_budget") or {}
    problems: List[str] = []
    p50 = declared.get("p50_ms")
    if isinstance(p50, (int, float)) and not isinstance(p50, bool) and p50 > 0:
        ratio = measured["p50_ms"] / float(p50)
        if ratio > TICK_DRIFT_FACTOR or ratio < 1.0 / TICK_DRIFT_FACTOR:
            problems.append("V13 tick-budget-drift: declared p50 %s ms/tick, measured "
                            "%.6f ms/tick (%.1fx, band %.1fx); re-base the declared "
                            "number or find what got slower"
                            % (p50, measured["p50_ms"], ratio, TICK_DRIFT_FACTOR))
    p99 = declared.get("p99_ms")
    if isinstance(p99, (int, float)) and not isinstance(p99, bool) and p99 > 0:
        if measured["p99_ms"] > float(p99) * TICK_DRIFT_FACTOR:
            problems.append("V13 tick-budget-drift: declared p99 %s ms/tick, measured "
                            "%.6f ms/tick on this box; the tail is more than %.1fx "
                            "optimistic" % (p99, measured["p99_ms"], TICK_DRIFT_FACTOR))
    return problems


# --------------------------------------------------------------------------- #
# Judging one manifest
# --------------------------------------------------------------------------- #

def bundle_file_problems(shard_dir: Optional[Path], manifest: Dict) -> List[str]:
    """V15 -- the declared bytes must be the bytes that are on disk.

    Until this existed, ``files[].sha256`` was checked only for being 64 characters: a
    manifest could describe a bundle that no longer existed, and V14 would happily
    recompute an address over hashes nobody compared to a file. Editing a shard's own
    SHARD.md was therefore invisible. Callers that hold the shard directory pass it;
    fixtures pass None (they describe bundles that were never materialised) and the
    report says the check did not run.
    """
    if shard_dir is None:
        return []
    problems: List[str] = []
    for entry in manifest.get("files") or []:
        if not isinstance(entry, dict):
            continue
        relative = entry.get("path")
        if not isinstance(relative, str):
            continue
        path = Path(shard_dir) / relative
        if not path.is_file():
            problems.append("V15 bundle-bytes: files[] lists %r, which is not on disk"
                            % relative)
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.get("sha256"):
            problems.append("V15 bundle-bytes: %r hashes to %s, but the manifest declares "
                            "%s; the manifest describes a file that is no longer here"
                            % (relative, digest[:16], str(entry.get("sha256"))[:16]))
        size = entry.get("size_bytes")
        if isinstance(size, int) and not isinstance(size, bool) and size != len(data):
            problems.append("V15 bundle-bytes: %r is %d bytes, declared as %d"
                            % (relative, len(data), size))
    return problems


def validate_manifest(manifest: Dict, schema: Dict,
                      shard_dir: Optional[Path] = None) -> List[str]:
    problems = schema_problems(manifest, schema)
    # The invariants read fields the schema has already checked; running them on a
    # structurally broken manifest produces a cascade that buries the real defect.
    if not problems:
        problems.extend(invariant_problems(manifest))
        problems.extend(bundle_file_problems(shard_dir, manifest))
    return problems


def _replay_note() -> str:
    return ("replay: NOT RUN -- this validator reads manifests, it does not execute "
            "shards; the golden run is unverified until a peer replays it")


def budget_note(measured: Optional[Dict[str, Any]]) -> str:
    """Say out loud whether the declared tick was re-measured, and never imply it was."""
    if measured is None:
        return ("tick budget: UNMEASURED -- the declared p50/p99 are the author's numbers; "
                "run --measure to compare them with this box's clock")
    return ("tick budget: measured %d x %d ticks, p50 %.6f ms, p99 %.6f ms per tick"
            % (measured["repeats"], measured["ticks"], measured["p50_ms"],
               measured["p99_ms"]))


def bytes_note(bundle_checked: bool) -> str:
    if bundle_checked:
        return "bundle bytes: verified -- every files[] sha256 and size_bytes matches disk"
    return ("bundle bytes: NOT VERIFIED -- no shard directory was supplied, so the "
            "declared hashes were never compared to a file")


def report(path: Path, problems: Sequence[str],
           measured: Optional[Dict[str, Any]] = None,
           bundle_checked: bool = False) -> str:
    name = path.name
    if not problems:
        return "PASS %s (schema + invariants; %s; %s; %s)" % (
            name, _replay_note(), budget_note(measured), bytes_note(bundle_checked))
    lines = ["FAIL %s" % name]
    lines.extend("     %s" % problem for problem in problems)
    lines.append("     %s" % budget_note(measured))
    lines.append("     %s" % bytes_note(bundle_checked))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def run_fixtures(directory: Path, schema: Dict) -> Tuple[int, List[Dict]]:
    """Judge every fixture; valid_* must pass, everything else must fail."""
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValidationError("no fixtures in %s" % directory)
    results: List[Dict] = []
    failures = 0
    by_fingerprint: Dict[str, List[Tuple[str, str]]] = {}
    for path in paths:
        manifest = load_json(path)
        if not isinstance(manifest, dict):
            raise ValidationError("%s is not a JSON object" % path)
        problems = validate_manifest(manifest, schema)
        expects_pass = path.name.startswith("valid_")
        if not problems:
            by_fingerprint.setdefault(bundle_fingerprint(manifest), []).append(
                (path.name, str(manifest.get("id"))))
        verdict_ok = (not problems) if expects_pass else bool(problems)
        if not verdict_ok:
            failures += 1
            if expects_pass:
                problems = list(problems) + ["fixture is named valid_* but was refused"]
            else:
                problems = ["fixture is malformed by name but PASSED the validator"]
        results.append({"fixture": path.name, "expected": "pass" if expects_pass
                        else "fail", "problems": problems})

    # V13 -- one bundle, two names. Judged across the set, because a single
    # manifest cannot see that another one claims the same files.
    for fingerprint, claimants in sorted(by_fingerprint.items()):
        ids = {identifier for _name, identifier in claimants}
        if len(ids) > 1:
            failures += 1
            names = ", ".join(sorted(name for name, _identifier in claimants))
            results.append({"fixture": names,
                            "expected": "one content address",
                            "problems": ["V13 duplicate-bundle: %s declare identical file "
                                         "sets under different ids %s"
                                         % (names, sorted(ids))]})
    return failures, results


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a Metthew shard manifest (M-03).")
    parser.add_argument("--manifest", action="append", default=[],
                        help="a shard.json to judge (repeatable)")
    parser.add_argument("--fixtures", nargs="?", const=str(FIXTURES), default=None,
                        help="judge every fixture in a directory (default: the shipped set)")
    parser.add_argument("--schema", action="store_true", help="check the schema alone")
    parser.add_argument("--measure", action="store_true",
                        help="time each --manifest's golden run and judge the declared "
                             "tick budget against it (V13); fixtures are not measured")
    parser.add_argument("--measure-repeats", type=int, default=MEASURE_REPEATS,
                        help="repetitions for --measure (default %d)" % MEASURE_REPEATS)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        schema = load_schema()
    except ValidationError as exc:
        print("UNUSABLE: %s" % exc, file=sys.stderr)
        return 2

    if args.schema:
        print("schema: %s (%s)" % ("readable", SCHEMA_PATH.name))
        print("required: %d field(s)" % len(schema.get("required", [])))
        print(_replay_note())
        return 0

    if not args.manifest and args.fixtures is None:
        parser.print_help()
        return 2

    failures = 0
    payload: Dict[str, Any] = {"manifests": [], "fixtures": []}

    for entry in args.manifest:
        path = Path(entry)
        try:
            manifest = load_json(path)
        except ValidationError as exc:
            print("UNUSABLE: %s" % exc, file=sys.stderr)
            return 2
        problems = validate_manifest(manifest, schema, path.parent)
        measured: Optional[Dict[str, Any]] = None
        if args.measure:
            try:
                measured = measure_tick_budget(path.parent, manifest,
                                               args.measure_repeats)
                problems = list(problems) + tick_drift_problems(manifest, measured)
            except MeasureError as exc:
                # A shard that declares a budget and cannot be measured is refused when
                # measurement was requested: 'unmeasurable' is not 'fine'.
                problems = list(problems) + ["V13 tick-budget-drift: requested a "
                                             "measurement and got none: %s" % exc]
        failures += 1 if problems else 0
        payload["manifests"].append({"path": str(path), "problems": problems,
                                     "measured": measured})
        if not args.json:
            print(report(path, problems, measured, bundle_checked=True))

    if args.fixtures is not None:
        try:
            fixture_failures, results = run_fixtures(Path(args.fixtures), schema)
        except ValidationError as exc:
            print("UNUSABLE: %s" % exc, file=sys.stderr)
            return 2
        failures += fixture_failures
        payload["fixtures"] = results
        if not args.json:
            for result in results:
                print("PASS %s (expected %s)" % (result["fixture"], result["expected"])
                      if not result["problems"] else
                      "FAIL %s\n     %s" % (result["fixture"],
                                            "\n     ".join(result["problems"])))
            print("")
            passes = sum(1 for result in results if not result["problems"])
            print("%d fixture(s): %d passed, %d refused as expected, %d problem(s)"
                  % (len(results), passes, len(results) - passes, fixture_failures))

    if args.json:
        print(json.dumps(payload, indent=1, sort_keys=True))
    if failures:
        print("REFUSED: %d problem(s)" % failures)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
