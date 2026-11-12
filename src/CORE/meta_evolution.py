"""Current meta-evolution control-plane contracts.

This module deliberately does not reproduce the historical six-role neural-mesh
census.  The runtime now contains several independent learning rungs, source
mutation tooling, scheduling, evidence ledgers, and effectors.  What was missing
was an authority that connected those current primitives without confusing a
successful process exit with a retained improvement.

The replacement contract is:

    observe -> select -> propose -> evaluate -> retain -> verify

Every transition is explicit.  A proposal is not authority, a test process that
collects zero tests is not evidence, and a mutation is retained only when the
independent before/after objective improves and the evaluator does not regress.
This module contains deterministic, side-effect-free policy and inventory code;
the production CLI supplies adapters for files, models, and tests.

Improvement policy and contracts; not the optimization mechanics
(evolution.py).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

VERSION = "1.0.0"
PHASES: tuple[str, ...] = (
    "observe", "select", "propose", "evaluate", "retain", "verify",
)
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "idle": frozenset({"observe"}),
    "observe": frozenset({"select", "blocked"}),
    "select": frozenset({"propose", "blocked"}),
    "propose": frozenset({"evaluate", "blocked"}),
    "evaluate": frozenset({"retain", "revert", "blocked"}),
    "retain": frozenset({"verify"}),
    "revert": frozenset({"verify"}),
    "verify": frozenset({"done"}),
    "blocked": frozenset({"done"}),
    "done": frozenset(),
}

#: Detector classes for which a one-substitution patch can have a mechanically
#: measurable objective.  ``swallow_all`` and ``bare_except`` remain audit leads:
#: swallowing a best-effort error can be correct, so a language model may not
#: unilaterally convert that context-sensitive judgement into a retained edit.
ACTIONABLE_DETECTORS = frozenset({
    "count_zero_tie", "default_true", "assert_true", "return_true_default",
})
DETECTOR_PRIORITY = {
    "default_true": 0,
    "assert_true": 1,
    "return_true_default": 2,
    "count_zero_tie": 3,
}
SEVERITY_PRIORITY = {"HIGH": 0, "MED": 1, "LOW": 2}
MAX_PATCH_FILES = 1
MAX_PATCH_SUBSTITUTIONS = 1


class MetaEvolutionError(RuntimeError):
    """A contract violation that must stop the lifecycle, not be downgraded."""


@dataclass(frozen=True)
class Candidate:
    path: str
    line: int
    detector: str
    severity: str
    code: str
    why: str
    source_hash: str

    @property
    def id(self) -> str:
        raw = "|".join((self.path, str(self.line), self.detector, self.source_hash))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["id"] = self.id
        return row


class Lifecycle:
    """Small explicit state machine used to prevent silent phase loss."""

    def __init__(self) -> None:
        self.state = "idle"
        self.history: list[str] = [self.state]

    def transition(self, target: str) -> None:
        allowed = LEGAL_TRANSITIONS.get(self.state, frozenset())
        if target not in allowed:
            raise MetaEvolutionError(
                f"illegal meta-evolution transition {self.state!r} -> {target!r}; "
                f"allowed={sorted(allowed)}"
            )
        self.state = target
        self.history.append(target)

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "history": list(self.history)}


def normalize_repo_path(path: str | os.PathLike[str]) -> str:
    """Return a repository-style relative path without permitting traversal."""
    raw = str(path).replace("\\", "/").strip()
    if not raw:
        raise MetaEvolutionError("empty repository path")
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise MetaEvolutionError(f"unsafe repository path: {raw!r}")
    return candidate.as_posix()


def is_protected_path(path: str | os.PathLike[str]) -> bool:
    """Protect evaluators, audit/compliance controls, tests, and generated code.

    The system may improve ordinary runtime code, but it may not grade its own
    homework by editing the grader, deleting tests, or changing the evidence
    pipeline that decides whether a mutation survives.
    """
    rel = normalize_repo_path(path)
    parts = set(Path(rel).parts)
    protected_names = {
        "meta_evolution.py", "self_modify.py", "meta_evolution_cycle.py",
        "vacuous_check_audit.py", "false_zero_audit.py",
    }
    protected_roots = {
        "tests", "tools", "graphify-out", "archive", "archived_unified",
        "src/CORE/compliance", "src/CORE/WORMS/expansion/generated",
    }
    if Path(rel).name in protected_names:
        return True
    if any(part in {"tests", "archive", "archived_unified", "graphify-out", "__pycache__"}
           for part in parts):
        return True
    rel_text = rel + "/"
    return any(rel_text.startswith(root.rstrip("/") + "/") or rel == root
               for root in protected_roots)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _candidate_from_finding(root: Path, finding: Mapping[str, Any]) -> Candidate | None:
    try:
        rel = normalize_repo_path(str(finding["path"]))
        path = root / rel
        line = int(finding["line"])
        detector = str(finding["detector"])
        severity = str(finding.get("severity", "LOW")).upper()
    except (KeyError, TypeError, ValueError, MetaEvolutionError) as exc:
        note_failure("meta_evolution:L164", exc)
        return None
    if not path.is_file() or is_protected_path(rel):
        return None
    if detector not in ACTIONABLE_DETECTORS:
        return None
    return Candidate(
        path=rel,
        line=line,
        detector=detector,
        severity=severity,
        code=str(finding.get("code", ""))[:200],
        why=str(finding.get("why", ""))[:300],
        source_hash=file_sha256(path),
    )


def choose_candidate(
    root: Path,
    findings: Sequence[Mapping[str, Any]],
    covered: Callable[[str], bool],
    attempted_ids: Iterable[str] = (),
) -> Candidate | None:
    """Choose one deterministic, covered, non-stale actionable candidate.

    ``source_hash`` makes a candidate single-use: after an attempt, the same line
    in changed source is a different proposal and must be reviewed anew.
    """
    attempted = set(str(x) for x in attempted_ids)
    candidates: list[Candidate] = []
    for finding in findings:
        candidate = _candidate_from_finding(root, finding)
        if candidate is None or candidate.id in attempted or not covered(candidate.path):
            continue
        candidates.append(candidate)
    if not candidates:
        return None
    candidates.sort(key=lambda c: (
        SEVERITY_PRIORITY.get(c.severity, 9),
        DETECTOR_PRIORITY.get(c.detector, 9),
        c.path,
        c.line,
        c.id,
    ))
    return candidates[0]


#: Max sites for repeat-mode patches (blast-radius cap). A repeated idiom
#: fixed at 13+ sites in one mutation is a rewrite wearing a patch costume.
MAX_REPEAT_SITES = 12


def validate_substitution_patch(
    root: Path,
    payload: Mapping[str, Any],
) -> tuple[str, str, str]:
    """Validate one exact substitution in one unprotected runtime file.

    Repeat mode: payload {"subs": {path: [[old, new]]}, "repeat": true}
    applies the pair at EVERY occurrence (the applier already replaces
    all). Verified count must satisfy 2 <= N <= MAX_REPEAT_SITES — the
    lower bound rejects single-site patches masquerading as patterns;
    the upper bound caps blast radius. Returns (rel, old, new) unchanged
    so all downstream code (apply, re-scan, improvement math) works unmodified.
    """
    subs = payload.get("subs")
    if not isinstance(subs, Mapping) or len(subs) != MAX_PATCH_FILES:
        raise MetaEvolutionError("patch must modify exactly one file")
    raw_rel, pairs = next(iter(subs.items()))
    rel = normalize_repo_path(str(raw_rel))
    if is_protected_path(rel):
        raise MetaEvolutionError(f"protected path in patch: {rel}")
    if not isinstance(pairs, list) or len(pairs) != MAX_PATCH_SUBSTITUTIONS:
        raise MetaEvolutionError("patch must contain exactly one substitution")
    pair = pairs[0]
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise MetaEvolutionError("substitution must be [old, new]")
    old, new = str(pair[0]), str(pair[1])
    if not old or old == new:
        raise MetaEvolutionError("substitution must be non-empty and material")
    path = root / rel
    if not path.is_file():
        raise MetaEvolutionError(f"patch target does not exist: {rel}")
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    count = text.count(old)
    if payload.get("repeat") is True:
        if count < 2:
            raise MetaEvolutionError(
                f"repeat mode needs 2+ sites, found {count} in {rel}")
        if count > MAX_REPEAT_SITES:
            raise MetaEvolutionError(
                f"repeat mode caps at {MAX_REPEAT_SITES} sites, found {count} "
                f"in {rel} — split the change")
        return rel, old, new
    if count != 1:
        raise MetaEvolutionError(
            f"old text occurs {count} times in {rel}; exact-once required "
            f"(or repeat:true for a verified multi-site idiom)"
        )
    return rel, old, new


def targeted_finding_count(findings: Sequence[Mapping[str, Any]], candidate: Candidate) -> int:
    return sum(
        1 for row in findings
        if str(row.get("path", "")).replace("\\", "/") == candidate.path
        and str(row.get("detector", "")) == candidate.detector
    )


def improvement_proved(
    before: int,
    after: int,
    evaluator_ok: bool,
    test_evidence: Mapping[str, Any],
    *,
    min_reduction: int = 1,
) -> tuple[bool, str]:
    """Independent retention predicate for one exact source substitution."""
    if not evaluator_ok:
        return False, "evaluator rejected or reverted the candidate"
    if int(test_evidence.get("passed", 0) or 0) <= 0:
        return False, "test band collected zero tests"
    if test_evidence.get("vacuous"):
        return False, "test band is vacuous"
    if after >= before:
        return False, f"targeted objective did not improve: {before} -> {after}"
    if before - after < min_reduction:
        return False, f"improvement below required {min_reduction}"
    return True, f"targeted objective improved: {before} -> {after}; tests non-vacuous"


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _active_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for base in (root / "src", root / "scripts", root / "tools"):
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(root)
            if any(part in {"__pycache__", "archive", "archived_unified", "generated"}
                   for part in rel.parts):
                continue
            files.append(path)
    return files


def _function_rows(root: Path, rel: str) -> list[dict[str, Any]]:
    path = root / rel
    if not path.is_file():
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError) as exc:
        note_failure("meta_evolution:L327", exc)
        return [{"name": "<module>", "qualname": "<module>", "line": 0,
                 "parse_error": str(exc)}]
    rows: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parent: list[str] = []
        for candidate in ast.walk(tree):
            if isinstance(candidate, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node is not candidate:
                cand_lineno: int = getattr(candidate, "lineno", 10**9)
                cand_end: int = getattr(candidate, "end_lineno", -1)
                node_end: int = node.end_lineno if node.end_lineno is not None else node.lineno
                if cand_lineno <= node.lineno \
                        and cand_end >= node_end:
                    parent.append(candidate.name)
        qualname = ".".join(parent[-2:] + [node.name]) if parent else node.name
        rows.append({"name": node.name, "qualname": qualname, "line": node.lineno})
    return sorted(rows, key=lambda row: (row["line"], row["qualname"]))


def _build_call_index(root: Path) -> dict[str, list[str]]:
    """Parse the active tree once and index every call site by callee name."""
    index: dict[str, list[str]] = {}
    for path in _active_python_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError) as exc:
            note_failure("meta_evolution:L353", exc)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            else:
                continue
            index.setdefault(name, []).append(
                f"{rel}:{getattr(node, 'lineno', 0)}")
    for name in index:
        index[name].sort()
    return index


def _call_sites(call_index: Mapping[str, Sequence[str]], function_name: str) -> list[str]:
    return list(call_index.get(function_name, ()))


def _chain_job_specs(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "scripts" / "run_azazael_chain.py"
    if not path.is_file():
        return {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError) as exc:
        note_failure("meta_evolution:L382", exc)
        return {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "JOBS" for t in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for key, value in zip(node.value.keys, node.value.values):
            if not isinstance(key, ast.Constant) or not isinstance(value, ast.Tuple):
                continue
            try:
                elements = [ast.literal_eval(item) for item in value.elts]
            except (ValueError, TypeError) as exc:
                note_failure("meta_evolution:L397", exc)
                continue
            if len(elements) == 5:
                out[str(key.value)] = {
                    "interval": elements[0], "timeout": elements[1],
                    "kind": elements[2], "target": elements[3], "args": elements[4],
                }
        return out
    return {}


def architecture_snapshot(root: Path) -> dict[str, Any]:
    """Build current static reachability evidence for the adaptive control plane.

    This is intentionally labelled static evidence.  A call site proves
    structural reachability, not semantic correctness; runtime adapters and tests
    supply the remaining obligations listed in ``proof_obligations``.
    """
    root = root.resolve()
    components = (
        "src/CORE/neural_loop.py",
        "src/CORE/spine_registry.py",
        "src/CORE/WORMS/expansion/meta_cognition.py",
        "src/CORE/WORMS/expansion/dreaming.py",
        "src/CORE/WORMS/expansion/spine.py",
        "src/CORE/WORMS/expansion/wiring_genome.py",
        "src/CORE/WORMS/expansion/hyperopt.py",
        "scripts/evolution_cycle.py",
        "scripts/wiring_cycle.py",
        "scripts/hyperopt_cycle.py",
        "scripts/neural_loop.py",
        "tools/self_modify.py",
        "tools/model_driver.py",
        "src/CORE/meta_evolution.py",
        "scripts/meta_evolution_cycle.py",
        "src/CORE/algorithm_qualification.py",
        "scripts/algorithm_lab_cycle.py",
        "src/CORE/WORMS/expansion/policy_optimization.py",
        "src/CORE/WORMS/expansion/diversity.py",
    )
    jobs = _chain_job_specs(root)
    scheduled_targets = {str(spec.get("target")) for spec in jobs.values()}
    call_index = _build_call_index(root)
    component_rows: list[dict[str, Any]] = []
    total_functions = 0
    for rel in components:
        rows = _function_rows(root, rel)
        total_functions += len(rows)
        scheduled = Path(rel).name in scheduled_targets
        enriched: list[dict[str, Any]] = []
        for row in rows:
            name = str(row.get("name"))
            sites = [site for site in _call_sites(call_index, name)
                     if not site.startswith(rel + ":")]
            production_sites = [site for site in sites if not site.startswith("tests/")]
            test_sites = [site for site in sites if site.startswith("tests/")]
            if scheduled and name in {"main", "run_cycle", "step"}:
                reachability = "scheduled_entry"
            elif production_sites:
                reachability = "production_call_site"
            elif test_sites:
                reachability = "test_only"
            elif name.startswith("__"):
                reachability = "implicit"
            else:
                reachability = "no_external_call_site"
            enriched.append({**row, "reachability": reachability,
                             "production_call_sites": production_sites,
                             "test_call_sites": test_sites})
        component_rows.append({
            "component": rel,
            "exists": (root / rel).is_file(),
            "scheduled": scheduled,
            "function_count": len(rows),
            "functions": enriched,
        })
    return {
        "schema": "azazael.meta_evolution.proof.v1",
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "included": [str(p.relative_to(root).as_posix()) for p in _active_python_files(root)],
            "excluded_parts": ["archive", "archived_unified", "__pycache__", "generated"],
            "historical_mesh_census_used": False,
        },
        "summary": {
            "components": len(component_rows),
            "existing_components": sum(1 for row in component_rows if row["exists"]),
            "functions_enumerated": total_functions,
            "scheduled_components": sum(1 for row in component_rows if row["scheduled"]),
            "test_only_functions": sum(
                1 for row in component_rows for fn in row["functions"]
                if fn["reachability"] == "test_only"
            ),
            "uncalled_functions": sum(
                1 for row in component_rows for fn in row["functions"]
                if fn["reachability"] == "no_external_call_site"
            ),
        },
        "chain_jobs": jobs,
        "components": component_rows,
        "proof_obligations": [
            "Static call sites establish reachability only, not semantic correctness.",
            "Model/network and operating-system calls require runtime traces, not mathematical proof.",
            "Source retention requires non-vacuous tests plus an independently reduced targeted objective.",
            "Production liveness requires an enabled scheduler; disabled tasks remain stopped regardless of code correctness.",
            "Concurrency correctness is established by race/deadlock tests and invariants, not a total mathematical proof.",
        ],
    }


__all__ = [
    "VERSION", "PHASES", "LEGAL_TRANSITIONS", "ACTIONABLE_DETECTORS",
    "MetaEvolutionError", "Candidate", "Lifecycle", "architecture_snapshot",
    "choose_candidate", "file_sha256", "improvement_proved", "is_protected_path",
    "normalize_repo_path", "targeted_finding_count", "validate_substitution_patch",
    "write_json_atomic",
]
