#!/usr/bin/env python3
"""Shard registry — hand-curated v1 manifest over existing skills and tools.

Each entry names the capability, the repo paths that implement it, the
languages available (in preference order), and the canonical implementation
the router tries first. New shards are added by appending an entry; nothing
moves. Doc-only skill families list ["doc"] — their SHARD.md IS the artifact.
"""

import datetime as _dt
import json
import sys
import time
from pathlib import Path

from INTERPRETERS.go_runner import GoRunner
from INTERPRETERS.node_runner import NodeRunner
from INTERPRETERS.pwsh_runner import PwshRunner
from INTERPRETERS.rust_runner import RustRunner
from INTERPRETERS.sh_runner import ShRunner

from CORE.failure_ledger import note_failure


def _probe_toolchain_versions() -> dict:
    """Probe current toolchain versions (single source of truth, E5).

    Lives here (not polyglot) so both sweep-time recording and
    freshness-time comparison measure the SAME instrument. Probing
    spawns 3 subprocesses (~250ms); sweep calls it once, freshness
    goes through the cached _current_versions() below."""
    versions: dict = {}
    try:
        versions["node"] = NodeRunner().version
        versions["pwsh"] = PwshRunner().version
        versions["bash"] = ShRunner().version
        versions["go"] = "go-present" if GoRunner().has_go else ""
        versions["rust"] = "rust-present" if RustRunner().has_rustc else ""
    except Exception as e:
        note_failure("registry:version-probe", e)
    try:
        versions["python"] = sys.version.split()[0]
    except Exception as e:
        note_failure("registry:version-probe-python", e)
    return versions


_VERSION_CACHE: dict = {"at": 0.0, "versions": {}}
_VERSION_CACHE_TTL_S = 3600.0  # re-probe hourly per process


def _current_versions() -> dict:
    """Current toolchain versions, cached per process (E5).

    sweep_freshness() runs on every route_lang call; uncached probes
    would add ~250ms of spawns to the hot path. The cache keeps routing
    free after the first call; an hourly re-probe bounds staleness."""
    now = time.time()
    if now - float(_VERSION_CACHE.get("at", 0.0)) > _VERSION_CACHE_TTL_S:
        _VERSION_CACHE["versions"] = _probe_toolchain_versions()
        _VERSION_CACHE["at"] = now
    return dict(_VERSION_CACHE.get("versions", {}))

SHARDS: dict = {
    "gateway": {
        "paths": ["gateway-go/main.go", "gateway/src/main.rs"],
        "langs": ["go", "rust"],
        "canonical": "go",
        "notes": "Proven polyglot pair: identical OpenAI-front-end contract, "
                 "two implementations. Template for all future dual-lang shards.",
    },
    "interpreters": {
        "paths": ["src/INTERPRETERS/"],
        "langs": ["python", "go", "rust", "javascript", "powershell", "bash"],
        "canonical": "python",
        "notes": "Runner fleet. Python hosts; every other language executes "
                 "through its dedicated runner module.",
    },
    "antibodies": {
        "paths": ["src/SHARDS/antibodies.py"],
        "langs": ["python"],
        "canonical": "python",
        "notes": "Error-derived detectors. Each antibody is cloned from one "
                 "observed instance and seeks every recurrence; status "
                 "advances seeking->neutralized->immune on recorded evidence.",
    },
    "skills-core": {
        "paths": ["azazael-conventions", "azazael-testing", "azazael-git",
                  "azazael-sop", "azazael-mandates"],
        "langs": ["doc"],
        "canonical": "doc",
        "notes": "Always-on discipline pack. Doc shard: SKILL.md is the artifact.",
    },
    "skills-inference": {
        "paths": ["azazael-inference*", "azazael-inference-protocol",
                  "azazael-inference-worms"],
        "langs": ["doc", "python"],
        "canonical": "doc",
        "notes": "Inference providers, protocol, worm integrations.",
    },
    "skills-worms": {
        "paths": ["azazael-worms*", "azazael-clusters", "azazael-registry"],
        "langs": ["doc", "python"],
        "canonical": "doc",
        "notes": "Worm registry, cluster system, category families.",
    },
    "skills-devtools": {
        "paths": ["azazael-devtools-*"],
        "langs": ["doc", "javascript"],
        "canonical": "doc",
        "notes": "Chrome DevTools panels; JS snippets run via node_runner.",
    },
    "skills-ops": {
        "paths": ["azazael-sync-*", "azazael-deploy", "azazael-exe-build",
                  "azazael-tool-*"],
        "langs": ["doc", "python", "powershell", "bash"],
        "canonical": "doc",
        "notes": "Sync/deploy/tooling. pwsh+shell secondaries for host tasks.",
    },
    "scripts-ops": {
        "paths": ["scripts/*.py"],
        "langs": ["python", "shell"],
        "canonical": "python",
        "notes": "Ops scripts; shell secondary for WSL/Git-Bash automation.",
    },
}

# Canonical language display order (host first, then compiled, then shells).
LANG_ORDER = ["python", "go", "rust", "javascript", "powershell", "bash"]

# Budget strategies (arena BUDGETS shape, adapted).
# A budget names a *strategy*; strategies resolve against the measured
# medians in sweep.json (written by `polyglot.py --sweep`):
#   fast     -> fastest measured language available to the shard
#   balanced -> the shard canonical when present, else fastest measured
#   peak     -> compiled-first (rust, go), then measured order (capability)
BUDGETS = {
    "fast": "fastest",
    "balanced": "canonical",
    "peak": "compiled",
}

_COMPILED_FIRST = ["rust", "go"]


SWEEP_MAX_AGE_HOURS = 24 * 7  # re-sweep weekly or after runner changes (P1)


def sweep_freshness() -> dict:
    """Freshness of sweep.json: age, version match, runner-code newer check.

    Returns {fresh: bool, reasons: [..]}. Stale tables warn, never raise —
    routing must degrade to canonical order, not crash (P1-item-2)."""
    reasons: list = []
    sweep_path = Path(__file__).with_name("sweep.json")
    try:
        data = json.loads(sweep_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"fresh": False, "reasons": [f"unreadable sweep.json: {e}"]}
    # D5: versions recorded at sweep time must be present — a table without
    # versions predates the comparability fix and cannot drive routing.
    versions = data.get("versions")
    if not isinstance(versions, dict) or not versions:
        reasons = ["sweep.json predates versions (D5): re-run --sweep"]
        return {"fresh": False, "reasons": reasons}
    # E5: compare sweep-time versions against current toolchains. A node
    # upgrade that changes type-stripping behavior (or a vanished pwsh)
    # silently invalidates the table even when no runner file changed.
    current = _current_versions()
    for key, swept in versions.items():
        now = current.get(key, "")
        if now != swept:
            if not now:
                reasons.append(f"toolchain gone since sweep: {key} was {swept!r}")
            else:
                reasons.append(f"toolchain drift: {key} {swept!r} -> {now!r}")
    try:
        gen = _dt.datetime.fromisoformat(str(data.get("generated_at", "")))
        age_h = (time.time() - gen.timestamp()) / 3600
        if age_h > SWEEP_MAX_AGE_HOURS:
            reasons.append(f"age {age_h:.1f}h > {SWEEP_MAX_AGE_HOURS}h")
    except (ValueError, TypeError, OverflowError) as e:
        reasons.append(f"unparseable generated_at: {e}")
    runners_dir = Path(__file__).parent.parent / "INTERPRETERS"
    try:
        sweep_mtime = sweep_path.stat().st_mtime
        for runner in sorted(runners_dir.glob("*_runner.py")):
            if runner.stat().st_mtime > sweep_mtime:
                reasons.append(f"runner newer than sweep: {runner.name}")
                break
    except OSError as e:
        reasons.append(f"runner mtime check failed: {e}")
    return {"fresh": not reasons, "reasons": reasons}


def _measured_order() -> list:
    """Languages sorted by measured WORK median ms (ascending).

    Falls back to LANG_ORDER with a ledger note when the sweep is stale
    or missing — stale numbers must not silently drive routing (P1).
    Owner decision 2026-10-29: warn AND degrade. A stale table is
    evidence about the past, not a measurement of the present; routing
    spends canonical preference until a fresh --sweep re-earns it."""
    try:
        data = json.loads(
            Path(__file__).with_name("sweep.json").read_text(encoding="utf-8"))
        medians = data.get("medians_ms", {})
    except (OSError, ValueError):
        return list(LANG_ORDER)
    freshness = sweep_freshness()
    if not freshness["fresh"]:
        note_failure("shards:stale-sweep", None)
        return list(LANG_ORDER)
    ranked = sorted(medians, key=lambda lang: (medians[lang], lang))
    ordered = [lang for lang in ranked if lang in LANG_ORDER]
    ordered += [lang for lang in LANG_ORDER if lang not in ordered]
    return ordered


def route_lang(shard_name: str, budget: str = "balanced") -> str:
    """Spend a budget: shard + budget -> language. Never raises.

    Doc-only shards return "doc" (no execution). Unknown shards fall back
    to python (the host). Toolchain availability is checked at run time by
    run_shard (clean skip), not here, so this stays a pure function."""
    entry = SHARDS.get(shard_name)
    if not entry:
        return "python"
    langs = [lang for lang in entry["langs"] if lang != "doc"]
    if not langs:
        return "doc"
    if len(langs) == 1:
        return langs[0]
    strategy = BUDGETS.get(budget, "canonical")
    order = _measured_order()
    if strategy == "fastest":
        ranked = [lang for lang in order if lang in langs]
        ranked += [lang for lang in langs if lang not in ranked]
        return ranked[0]
    if strategy == "compiled":
        ranked = [lang for lang in _COMPILED_FIRST if lang in langs]
        ranked += [lang for lang in order if lang in langs and lang not in ranked]
        ranked += [lang for lang in langs if lang not in ranked]
        return ranked[0]
    canonical = entry.get("canonical")
    if canonical in langs:
        return canonical
    ranked = [lang for lang in order if lang in langs]
    return ranked[0] if ranked else langs[0]


# Keyword router: task text -> shard name. First match wins; order matters.
SHARD_KEYWORDS: dict = {
    "gateway": ["gateway", "llama-server", "upstream", "chat/completions"],
    "interpreters": ["runner", "go_runner", "rust_runner", "node_runner",
                     "pwsh_runner", "sh_runner", "polyglot"],
    "antibodies": ["antibody", "antibodies", "immune", "seek",
                   "drift", "work golden", "stale sweep", "regression"],
    "skills-inference": ["inference", "provider", "nim", "llm"],
    "skills-worms": ["worm", "cluster", "registry"],
    "skills-devtools": ["devtools", "chrome", "lighthouse", "dom"],
    "skills-ops": ["sync", "deploy", "wsl", "kali", "sync_all"],
    "scripts-ops": ["script", "gate", "scanner", "ratchet"],
    "skills-core": [],
}


def route_shard(task: str) -> str:
    """Top-1 router: task text -> shard name (falls back to skills-core)."""
    text = (task or "").lower()
    for shard, keywords in SHARD_KEYWORDS.items():
        for keyword in keywords:
            if keyword and keyword in text:
                return shard
    return "skills-core"


def shard_langs(name: str) -> list:
    """Languages available for a shard (empty list for unknown names)."""
    entry = SHARDS.get(name)
    return list(entry["langs"]) if entry else []
