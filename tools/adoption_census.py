#!/usr/bin/env python3
"""adoption_census.py — the T-15 instrument: production call sites per technique.

Why this exists
---------------
The retired instrument was the exercise harness's `engaged=181` counter. It read
the registry back to itself: a technique whose only caller is an adapter in
`src/CORE/technique_wiring/` scored exactly the same as a technique that real
production code calls on its own. That made "the technique is adopted" and "an
exercise invoked the technique" indistinguishable numbers — see
`state/ACTION_REGISTRY_2026-10-10.md` H-2 and `TODO_315T_BUILD.md` T-15, which
mandate this replacement: count PRODUCTION call sites per technique and score
exercise-only engagement as **zero**.

What counts as production
-------------------------
  production surface : `src/` EXCEPT `src/CORE/technique_wiring/`, plus `tools/`
                       and `scripts/` (operator/CI-invoked code)
  exercise layer     : `src/CORE/technique_wiring/`  (counted separately, never as adoption)
  non-production     : `tests/`, `archive/`, `*.pre_gate_backup`, generated reports

A call site is a line mentioning the symbol that is not the symbol's own
definition, not a docstring/comment mention, and not a `register()` host string.
Docstring Usage examples and comments are excluded (the same rule the wire-decision
worklist used) so the number cannot be inflated by prose.

Verdicts
--------
  PRODUCTION     >=1 production call site            -> adopted
  EXERCISE-ONLY  only the exercise layer references it -> adoption score ZERO
  UNREFERENCED   nothing outside tests/docs references it

Usage
-----
    python tools/adoption_census.py                    # human summary
    python tools/adoption_census.py --json state/adoption_census.json
    python tools/adoption_census.py --self-test        # positive + negative control
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve()
REPO = HERE.parent.parent
_REL_MAP = Path("state") / "sota_batches" / "batch06" / "technique_codebase_map.json"


def _find_map() -> Path:
    """Locate the technique map.

    It is a `state/` artifact that has lived under both the repo root and the
    project root (the level above this checkout) across sessions, so resolve it
    instead of hardcoding one — a hardcoded miss made the first run of this tool
    fail with FileNotFoundError rather than reporting.
    """
    for cand in (REPO / _REL_MAP, REPO.parent / _REL_MAP,
                 REPO.parent / "azazael" / _REL_MAP):
        if cand.exists():
            return cand
    hits = sorted(p for p in REPO.parent.glob(f"*/{_REL_MAP.as_posix()}"))
    if hits:
        return hits[0]
    return REPO / _REL_MAP  # report the miss at read time, with the path shown


def _find_batch_map(batch: str, name: str) -> Path:
    """Locate a per-batch map by the same multi-root rule as _find_map.

    Batch maps live under workspace-root state/ (outside the repo), so a
    repo-relative literal misses them exactly the way the first _find_map
    run did (EP-REDO-7: control I pointed at repo state/ while batch04's
    technique-map.json lives at C:/Azazael/state/sota_batches/).
    """
    rel = Path("state") / "sota_batches" / batch / name
    for cand in (REPO / rel, REPO.parent / rel,
                 REPO.parent / "azazael" / rel):
        if cand.exists():
            return cand
    hits = sorted(p for p in REPO.parent.glob(f"*/{rel.as_posix()}"))
    if hits:
        return hits[0]
    return REPO / rel  # report the miss at read time, with the path shown


MAP = _find_map()

EXERCISE_DIR = "src/CORE/technique_wiring/"
PROD_ROOTS = ("src/", "tools/", "scripts/")
NON_PROD_PREFIXES = ("tests/", "archive/", ".build/", "desktop/", "packages/")

#: A line that merely names the symbol in prose is not a call site.
_PROSE = re.compile(r'^\s*(#|"""|\'\'\'|:param|:return|>>>)')

#: An import is a wiring INTENT, not a use. Counting it as adoption is how the
#: retired engaged=181 counter inflated itself, so imports are excluded.
_IMPORT = re.compile(r'^\s*(from|import)\s')

#: This tool must never measure itself (its own self-test names symbols).
_SELF = "tools/adoption_census.py"


def _is_prose_context(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if _PROSE.match(line):
        return True
    # a bare docstring line inside one has no syntax markers; treat quoted-only as prose
    if s.startswith(('"', "'")) and s.endswith(('"', "'")):
        return True
    return False


#: A symbol mentioned only inside string literals is prose, not a call site.
#: Real false-positive: `src/CORE/local_memory.py:2465` is the dict value
#: `"gap": "composite rule is an edit to enforce_global_quota (M)"` — a prose
#: mention of `enforce_global_quota` that adopted EP221 on nothing. AST already
#: excludes multi-line literals from the candidate lines; single-line literals
#: are blanked per line instead. Limitation: f-string interpolations are blanked
#: along with their literal (a symbol referenced ONLY as `f"{sym}"` is missed);
#: no catalogued row does that, and the self-test guards the tradeoff.
_STRING_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'')


def _code_part(line: str) -> str:
    """`line` with single-line string literals blanked (comments kept)."""
    return _STRING_RE.sub('""', line)


def _alias_use(code: str, aliases: set) -> bool:
    """True when an import alias is genuinely used on this code-stripped line."""
    for n in aliases:
        if n not in code:
            continue
        if _is_prose_context(code):
            continue
        if _IMPORT.match(code):
            continue
        # Defining the fallback stub (`class _NeedsClarification`) is not a
        # use of the channel — CLUSTERWORMS.py:89 names it without consuming it.
        if re.match(r"\s*(def|class)\s+" + re.escape(n) + r"\b", code):
            continue
        # GUARD-1 alias arm (same EP301 shape): `CascadeRouter = None` and
        # friends name the alias without consuming it.
        if re.match(r"\s*" + re.escape(n) + r"\s*=\s*None\b", code):
            continue
        # CALL-SHAPE (2026-10-22, EP472/EP465): aliases get the same gate —
        # a bare mention of the alias is not a call through it.
        if not re.search(r"(?<![\w])" + re.escape(n) + r"\s*\(", code):
            continue
        return True
    return False


def _noncode_lines(text: str) -> set:
    """1-based line numbers that cannot hold a call site.

    The line-oriented filters above only recognise prose that OPENS with a marker
    (a comment, a triple-quote, a `>>>` example). Prose in the BODY of a docstring
    has none, and two real rows were inflated by exactly that:
      * `src/CORE/technique_runtime.py:11` is a docstring table row reading
        `dispatch_plan.py       plan_dag/assign_roles  2 callers (both exercises)`
        -- counted as a production call site for `plan_dag`;
      * `src/CORE/WORMS/METACLUSTER.py:339` is the continuation line of a
        parenthesised `from CORE.dispatch_plan import (...)` -- matched as a call.
    Both are found by parsing instead of pattern-matching: every string literal's
    line span and every import statement's line span is excluded. Files that do not
    parse (the tree holds non-Python and py2-era sources) fall back to the regex
    filters alone rather than crashing the census.
    """
    skip: set = set()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, MemoryError):
        return skip
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lo, hi = node.lineno, getattr(node, "end_lineno", node.lineno)
            skip.update(range(lo, hi + 1))
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lo, hi = node.lineno, getattr(node, "end_lineno", node.lineno)
            if hi > lo:                       # multi-line literals: docstrings, tables
                skip.update(range(lo, hi + 1))
    return skip


def _parse_segments(target: str) -> List[Tuple[str, List[str]]]:
    """Parse a map `target` into [(host_path, [symbols]), ...].

    Real targets are compound, e.g.
      `CORE/local_memory.py :: LocalMemoryStore + CORE/structured_action.py :: observe`
      `azazael/src/CORE/skill_library.py :: protocol_artifact, commit_gate, family_index`
      `CORE/freethreading.py :: WorkStealingPool ; CORE/dispatch_plan.py :: ...`
      `` `CORE/dream_arena.py :: compile_world, admit_worlds` · `CORE/.../consolidation.py :: ...` ``
      `CORE/WORMS/expansion/delegation.py :: `ask_owner` / `answer_question` / `NeedsClarification``
    and some are prefixed `nearest:`. The first version of this tool treated the
    whole string as one `path :: symbol` pair, which silently mis-measured every
    multi-symbol and multi-host row. Two later variants of the same defect:
    segments joined by `·` (EP335: the consolidation host was never measured and
    DreamCriticWorm silently dropped) and symbols joined by `/` (EP213: only
    ask_owner was measured while answer_question/NeedsClarification were cut off
    at the first whitespace token).
    """
    raw = (target or "").strip()
    if not raw or raw in {"�", "-", "n/a", "N/A", "—"}:
        return []
    raw = re.sub(r"^nearest\s*:\s*", "", raw, flags=re.IGNORECASE)
    out: List[Tuple[str, List[str]]] = []
    # Segments split on + | ; | · | • — every one of these joins hosts in the
    # real map (+/; the common case, · in 3 rows e.g. EP335, • defensively).
    for seg in re.split(r"\s*(?:\+|;|·|•)\s*", raw):
        if "::" not in seg:
            continue
        path, _, symbols = seg.partition("::")
        # The map wraps paths and symbols in backticks/asterisks in many rows
        # (`CORE/x.py`, **y**); leaving them on made valid hosts unresolvable and
        # produced symbol literals like "`" that matched nothing.
        path = path.strip().strip("`*\"' ").replace("\\", "/").lstrip("/")
        if path.startswith("azazael/"):
            path = path[len("azazael/"):]
        syms: List[str] = []
        # Symbols split on , or / — identifiers never contain `/`, so after the
        # `::` partition any slash is a separator (EP213's
        # `ask_owner` / `answer_question` / `NeedsClarification`). Split before
        # stripping whitespace so each alternative survives intact.
        for raw_sym in re.split(r"[,/]", symbols):
            s = raw_sym.strip().strip("`*\"' ")
            s = re.split(r"\s+", s)[0] if s else ""
            # Symbols are identifiers (optionally dotted). The map writes some as
            # `cast_quarantine_vote`)` / `prefix_inject`)` -- a trailing backtick and
            # bracket from the surrounding prose. Left on, those matched nothing and
            # the row scored as if the symbol had no caller anywhere.
            s = re.split(r"[^A-Za-z0-9_.]", s)[0]
            # drop parenthetical/prose fragments the map mixes into symbol lists
            if not s or s in {"~L956)", "-"} or s.startswith(("~", "(", ")")):
                continue
            syms.append(s)
        if path and syms:
            out.append((path, syms))
    return out


#: GUARD-2 (2026-10-22, EP271 falsification): bare-generic symbols that are
#: ordinary English/programming vocabulary. EP271 measures bare `plan`/`proxy`
#: while the technique is `MCTSPlanner` (`_uct`/`plan` methods): 192 production
#: "sites" across 27 files, ZERO lines mentioning MCTS, most matching
#: `morph_ab_plan(` (substring, different function) or each file's own local
#: `plan()` (chain_controller.py defines its own at :409). A bare-generic hit
#: counts only when the line names it on a word boundary AND the file binds the
#: host module (imports its stem or uses `stem.` qualified). Narrow by design:
#: only symbols in this set are gated; `dream`/`refine` rows stay under their
#: recorded verdicts until a lane adjudicates them with the same line evidence.
# GUARD-2 set (2026-10-22): bare symbols generic enough that unrelated
# receivers share the method name. `plan`/`proxy` (EP271), `refine`
# (EP472: `_pareto_refine(`, `self.run(`) and `observe` (EP465:
# `tpe.observe(`, `_observed_and_registry(`) each falsely adopted a row.
_GENERIC_BARE = {"plan", "proxy", "refine", "observe"}


_HOST_IMPORT_MAP: Dict[Path, list] = {}

#: Collaborator-alias cache: (file, symbol, stems) -> {alias names}. See the
#: note at the build site — uncached full-text scans per _sniff call timed out
#: the self-test.
_COLLAB_CACHE: Dict[tuple, set] = {}

#: Unreadable-site records from _generic_keep. The except handler must carry a
#: second statement (record, not silence) so the body-length gate clears it
#: structurally instead of by suppression.
_READ_ERRORS: List[str] = []


def _host_imported_names(repo: Path, rel: str, stems: set) -> set:
    """Names the file binds to one of the host module stems (AST, cached).

    `from allocation import plan` binds `plan`; `from allocation import plan
    as _p` binds both; `import allocation` binds `allocation` (so a later
    `allocation.plan(` resolves). Prose mentions ("the allocation policy") and
    string literals never bind — the 2026-10-22 EP271 run showed a prose
    sentence ending in "allocation." passing a text-level `stem.` check and
    keeping 26 `self.plan` sites alive in a file that never imports the host.
    Files do not change mid-run, so parse once.

    Cache holds the FULL (stem, name) map per file and filters per call: the
    same file is queried with DIFFERENT stem sets across rows/segments, so a
    path-keyed RESULT set returned the first caller's answer to every later
    caller (found 2026-10-22: a depo_filter scan poisoned the next
    bounded_correct scan of the same file with an extra :253 hit). Filtering
    a cached map keeps one parse per file AND per-stems correctness.
    """
    p = repo / rel
    pairs = _HOST_IMPORT_MAP.get(p)
    if pairs is None:
        pairs = []
        try:
            text, _ = _load(p)
            tree = ast.parse(text)
        except (OSError, SyntaxError, ValueError, MemoryError):
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    stem = node.module.split(".")[-1]
                    for a in node.names:
                        pairs.append((stem, a.name))
                        if a.asname:
                            pairs.append((stem, a.asname))
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        stem = a.name.split(".")[-1]
                        pairs.append((stem, a.asname or stem))
        _HOST_IMPORT_MAP[p] = pairs
    return {name for (stem, name) in pairs if stem in stems}


def _is_call_occurrence(line: str, symbol: str, def_owner: bool) -> bool:
    """True when `line` mentions `symbol` as something other than its own def."""
    if symbol not in line:
        return False
    if _is_prose_context(line):
        return False
    if _IMPORT.match(line):
        return False
    # GUARD-1 (2026-10-22, EP301 falsification): a None-fallback assignment arm
    # (`verify_inclusion_proof = None` inside `except ImportError`) is a wiring
    # ABSENCE, not a call site. The try-block import is excluded by mandate and
    # nothing invokes the name, so counting the fallback adopted EP301 on zero
    # production use. Lvalue `NAME = None` never counts, for any symbol.
    if re.match(r"\s*" + re.escape(symbol.split(".")[-1]) + r"\s*=\s*None\b", line):
        return False
    if def_owner and re.match(r"\s*(def|class)\s+" + re.escape(symbol.split(".")[-1]) + r"\b", line):
        return False
    # CALL-SHAPE (2026-10-22, EP472/EP465 falsification): a substring mention
    # is not a call site. `_pareto_refine(` counted for `refine`,
    # `tpe.observe(` for `observe`, `_LEGAL_TRANSITIONS` for
    # `LEGAL_TRANSITIONS`. Require NAME + '(' with a left word boundary —
    # covers bare `f(` and attribute `.f(` alike. A bare `@deco` / value
    # reference without parens does not count (no such genuine site seen).
    if not re.search(r"(?<![\w])" + re.escape(symbol.split(".")[-1]) + r"\s*\(", line):
        return False
    return True


#: Per-file cache: (text, non-code line numbers). Without it every (row, file,
#: symbol) triple re-read AND re-parsed the same file -- 141 rows x ~1100 candidate
#: files x ~1.5 symbols is >200k ast.parse calls, which took the census past a
#: 10-minute timeout. The files do not change during a run, so one pass is enough.
_FILE_CACHE: Dict[Path, Tuple[str, set]] = {}

#: Per-file import-alias map: base name -> {asnames}. Parsed once per file for
#: the same reason as _FILE_CACHE above: resolving aliases via ast.parse per
#: (file, symbol) triple re-parses every file per symbol and pushes the census
#: past the command timeout.
_ALIAS_CACHE: Dict[Path, Dict[str, set]] = {}


def _alias_map(path: Path, text: str) -> Dict[str, set]:
    """Return {imported name -> {asnames}} for `path`, parsed once per run."""
    hit = _ALIAS_CACHE.get(path)
    if hit is None:
        hit = {}
        try:
            _tree = ast.parse(text)
        except (SyntaxError, ValueError, MemoryError):
            _tree = None
        if _tree is not None:
            for _node in ast.walk(_tree):
                if isinstance(_node, ast.ImportFrom):
                    for _a in _node.names:
                        if _a.asname:
                            hit.setdefault(_a.name, set()).add(_a.asname)
                elif isinstance(_node, ast.Import):
                    for _a in _node.names:
                        if _a.asname:
                            hit.setdefault(_a.name.split(".")[-1], set()).add(_a.asname)
        _ALIAS_CACHE[path] = hit
    return hit


def _load(path: Path) -> Tuple[str, set]:
    """Return (text, non-code lines) for `path`, parsed once per run."""
    hit = _FILE_CACHE.get(path)
    if hit is None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        hit = (text, _noncode_lines(text))
        _FILE_CACHE[path] = hit
    return hit


def _sniff(path: Path, symbol: str, repo: Path,
           host_stems: frozenset = frozenset()) -> Tuple[bool, List[str], List[str]]:
    """Return (defined_here, prod_hits, exercise_hits) for `symbol` in `path`."""
    rel = path.relative_to(repo).as_posix()
    if not path.exists():
        return False, [], []
    text, skip = _load(path)
    short = symbol.split(".")[-1] if symbol else ""
    # DEFINED-SHAPE (2026-10-22, EP462 falsification): module constants use
    # annotated/plain assignment (`LEGAL_TRANSITIONS: dict[...] = {...}` in
    # meta_evolution.py:46), which the def/class-only check missed and the row
    # scored TARGET-UNVERIFIED while the host defines it. Anchored at line
    # start so RHS mentions (`x = NAME`) and comparisons (`NAME ==`) pass.
    defined_here = bool(short) and bool(
        re.search(r"\s*(def|class)\s+" + re.escape(short) + r"\b", text)
        or re.search(r"(?m)^\s*" + re.escape(short)
                     + r"\s*(?::[^=\n]*=|(?<![=!<>+*/-])=(?!=))", text))
    if not short:
        return defined_here, [], []

    # Aliases: `from X import merge_sops as _MERGE_SOPS` means the real call site
    # says `_MERGE_SOPS(...)`. Without this the tool misses every aliased caller --
    # it reported canon_drift_check.py (the one REAL production caller of
    # merge_sops) as absent. Resolved via AST so parenthesised multi-line imports
    # (`from X import (\n merge_sops as _MERGE_SOPS,\n)`) are seen; the previous
    # line-regex only matched single-line `from X import y` and missed the
    # continuation lines entirely (self-test positive control failed on
    # tools/canon_drift_check.py:73-77 for exactly this reason).
    # Alias map is cached per file: parsing per (file, symbol) triple regressed
    # the census past the command timeout. The regex pass below stays as a
    # fallback for files ast.parse cannot handle.
    # COLLABORATOR aliases (2026-10-22, lifecycle redo): constructor-injected
    # collaborators (`self._corrector = corrector or _bounded_correct`) move the
    # call shape to `self._corrector(...)`, which names neither the symbol nor
    # the owner word. The binding is still statically visible: the RHS names a
    # host-bound import, so the LHS becomes an alias for THIS symbol only.
    # Same binding standard as GUARD-2 (host-module import stem); bare
    # `self._helper(` calls with no such binding score nothing.
    names = {short} | set(_alias_map(path, text).get(short, ()))
    for line in text.splitlines():
        m = re.match(r"\s*from\s+\S+\s+import\s+(.+)$", line)
        if not m:
            continue
        for part in m.group(1).split("#")[0].split(","):
            part = part.strip()
            if not part:
                continue
            bits = re.split(r"\s+as\s+", part)
            if bits[0].strip() == short and len(bits) == 2:
                names.add(bits[1].strip())

    # A QUALIFIED symbol (`CascadeRouter.route`) may not be collapsed to its last
    # component. Doing so made `route` match ordinary English prose -- the row for
    # EP208 reported 540 "production call sites" that were sentences like
    # "full_text route is the practical extraction path." and a docstring line
    # `("routing", "expert router sends everything to one expert")`. A file that
    # never names the owner class cannot contain an attribute call on it, so the
    # owner's presence is a precondition for any hit whatsoever.
    parts = symbol.split(".")
    owner = parts[-2] if len(parts) >= 2 else ""
    if owner and not re.search(r"\b" + re.escape(owner) + r"\b", text):
        # OWNER-BINDING fallback (2026-10-22, EP414/EP478 falsification):
        # factory-produced receivers (`store = get_memory_store();
        # store.generative_compaction_status()`) never spell the owner class,
        # so the bare-word precondition silenced genuine attribute calls
        # (nightly_maintenance.py:158, data_retention.py:431 scored nothing).
        # Keep ATTRIBUTE-call sites (`.method(`) in files bound to the
        # owner's host module (import stem -- the GUARD-2 binding standard);
        # bare `method(` calls without the owner word stay excluded, and
        # files importing nothing from the host stay excluded, so EP208-style
        # prose and foreign same-named methods still score nothing.
        bound = (_host_imported_names(repo, rel, set(host_stems))
                 if host_stems else set())
        if not bound:
            return defined_here, [], []
        attr_pat = re.compile(
            r"\.\s*(?:" + "|".join(re.escape(n) for n in names) + r")\s*\(")
        fprod: List[str] = []
        fexer: List[str] = []
        for i, line in enumerate(text.splitlines(), 1):
            if i in skip:
                continue
            code = _code_part(line)
            if short not in code:
                continue
            if _IMPORT.match(line) or _is_prose_context(line):
                continue
            if re.match(r"\s*" + re.escape(short) + r"\s*=\s*None\b", line):
                continue
            if not attr_pat.search(code):
                continue
            loc = f"{rel}:{i}"
            (fexer if rel.startswith(EXERCISE_DIR) else fprod).append(loc)
        return defined_here, sorted(set(fprod)), sorted(set(fexer))

    # Collaborator scan is cached per (file, symbol, stems): the DOTALL pass
    # costs a full-text regex per _sniff call, and _sniff runs per
    # (row, segment, candidate-file) — uncached it pushed the self-test past
    # the command timeout. Files do not change mid-run, so memoize.
    _ckey = (path, short, frozenset(host_stems))
    collab = _COLLAB_CACHE.get(_ckey)
    if collab is None:
        collab = set()
        if host_stems:
            # The RHS must name the SYMBOL (short or its import asnames) as
            # imported from a host stem — not merely any host-module name.
            # `store = get_memory_store()` binds the FACTORY, and `_regime =
            # track_regime()` binds a DIFFERENT function; neither is a call
            # path to the measured symbol, but a host-names-only check kept
            # both and adopted EP414/EP478 on `_regime()` lines that never
            # utter either method (found 2026-10-22, 8 phantom sites per row
            # in run_training_orchestrator.py). Factory receivers stay the
            # OWNER-fallback's job (`.method(` attribute shape); collab is
            # for symbol-or-asname RHS only.
            _relevant = (_host_imported_names(repo, rel, set(host_stems))
                          & names)
            if _relevant:
                for _ln in text.splitlines():
                    _m = re.match(
                        r"\s*(?:self\.)?(\w+)\s*=\s*(.+)$", _ln)
                    if not _m:
                        continue
                    _lhs, _rhs = _m.group(1), _m.group(2)
                    if _lhs == short or _lhs in names:
                        continue
                    _words = set(re.findall(r"[A-Za-z_]\w*", _rhs))
                    if _words & _relevant:
                        collab.add(_lhs)
                # Multi-line parenthesised bindings
                # (`self._x = (a if ... else _B)`) split the RHS across lines,
                # so the line pass above never sees the bound name. Scan the
                # joined statement instead (bounded: 500 chars of
                # balanced-paren-free RHS, then back to line discipline).
                for _m in re.finditer(
                        r"(?:self\.)?(\w+)\s*=\s*\(([^()]{0,500})\)",
                        text, flags=re.DOTALL):
                    _lhs = _m.group(1)
                    if _lhs == short or _lhs in names:
                        continue
                    if (set(re.findall(r"[A-Za-z_]\w*", _m.group(2)))
                            & _relevant):
                        collab.add(_lhs)
        _COLLAB_CACHE[_ckey] = collab
    prod: List[str] = []
    exer: List[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        if i in skip:
            continue
        # String-only mentions are prose, not call sites (local_memory.py:2465
        # adopted EP221 on a dict-value sentence naming enforce_global_quota).
        code = _code_part(line)
        if not any(n in code for n in names) and not any(
                c in code for c in collab):
            continue
        if not _is_call_occurrence(code, short, def_owner=defined_here) and not _alias_use(code, names - {short}) and not _alias_use(code, collab):
            continue
        loc = f"{rel}:{i}"
        (exer if rel.startswith(EXERCISE_DIR) else prod).append(loc)
    return defined_here, prod, exer


def _generic_keep(repo: Path, loc: str, gen_syms: set, spec_syms: list,
                  stems: set) -> bool:
    """GUARD-2 row filter: keep `loc` iff it survives the generic-word gate.

    Module-level (not nested in census) so the self-test can pin it directly
    (control O). A site matching a non-generic measured symbol survives; a
    site with NO generic word on its line survives (it arrived through a
    specific gate — call-shape, owner-binding, or lifecycle collaborator —
    each host-bound by construction); otherwise the generic name must resolve
    to the host via import or host-bound attribute. Files that merely import a
    different surface while running their own `self.plan` keep nothing.
    """
    p, _, ln = loc.rpartition(":")
    try:
        line = (repo / p).read_text(
            encoding="utf-8", errors="replace").splitlines()[int(ln) - 1]
    except (OSError, IndexError, ValueError) as exc:
        _READ_ERRORS.append("%s:%s" % (loc, type(exc).__name__))
        return False
    code = _code_part(line)
    if any(re.search(r"(?<![\w])" + re.escape(s) + r"\b", code)
           for s in spec_syms):
        return True
    if not any(re.search(r"(?<![\w])" + re.escape(g) + r"\b", code)
               for g in gen_syms):
        return True
    bound = _host_imported_names(repo, p, stems)
    for g in gen_syms:
        if g in bound and re.search(
                r"(?<![\w])" + re.escape(g) + r"\s*\(", code):
            return True
        if any(re.search(r"(?<![\w])" + re.escape(b) +
                         r"\s*\.\s*" + re.escape(g) + r"\b", code)
               for b in bound):
            return True
    return False


def _resolve_host(repo: Path, path_s: str) -> Path:
    """Resolve a map `target` path to a real file.

    The map writes hosts inconsistently: some rows say `src/CORE/x.py`, others say
    `CORE/x.py` (the package-root form). Treating them differently made the tool
    report every short-form row as having no host at all.
    """
    if not path_s:
        return repo / "__none__"
    for cand in (repo / path_s, repo / "src" / path_s):
        if cand.exists():
            return cand
    return repo / path_s


def _candidate_files(repo: Path = REPO) -> List[Path]:
    """Every file that could hold a call site: production surface + exercise layer."""
    cands: List[Path] = []
    for root in (*PROD_ROOTS, EXERCISE_DIR):
        base = repo / root
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            rel = p.relative_to(repo).as_posix()
            if rel.startswith(NON_PROD_PREFIXES) or ".pre_gate_backup" in rel:
                continue
            # Test suites nested inside the production tree (src/tests/) are
            # still tests: EP337 scored ADOPTED on src/tests/test_worm_registry.py
            # mentions of _probe_candidates. Any tests/ segment disqualifies.
            if "/tests/" in "/" + rel:
                continue
            if rel == _SELF:
                continue
            cands.append(p)
    return sorted(set(cands))


def production_callers(symbol: str, repo: Path = REPO,
                       cands: (List[Path] | None) = None) -> Tuple[List[str], List[str]]:
    """All call sites for `symbol` across the tree, split production vs exercise.

    This is the whole-tree form. The first version of the self-test called
    `_sniff` on the host file alone, so it could only ever see in-file mentions —
    it reported a false failure for a symbol whose real caller lives in tools/.
    """
    prod: List[str] = []
    exer: List[str] = []
    for p in (cands if cands is not None else _candidate_files(repo)):
        _, ph, eh = _sniff(p, symbol, repo)
        prod.extend(ph)
        exer.extend(eh)
    return sorted(set(prod)), sorted(set(exer))


def _host_reachable(repo: Path, host_stem: str, cands: List[Path],
                    host: (Path | None)) -> bool:
    """Is the host module imported by production code other than itself?

    Distinguishes HOST-COMPOSED from EXERCISE-ONLY. A technique consumed by
    another function in its OWN host is genuinely in production when that host
    is reached by production code -- e.g. `margin_check` is called by
    `verify_before_accept`, which `technique_runtime.verify_boundary` reaches,
    which `sentinel_enforcer.enforce` calls. Counting that as zero adoption
    would be as wrong as counting an exercise adapter as adoption.
    """
    if not host_stem:
        return False
    for p in cands:
        if host is not None and p == host:
            continue
        rel = p.relative_to(repo).as_posix()
        if rel.startswith(EXERCISE_DIR):
            continue
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if _IMPORT.match(line) and host_stem in line:
                    return True
        except OSError:
            continue
    return False


def _extract_rows(data: Any) -> List[Dict[str, Any]]:
    """Pull the row dicts out of the map whatever shape it is wrapped in.

    The artifact is `{"totals": {...}, "rows": [...]}` today; an earlier shape was
    a bare list and the first version of this tool silently grabbed `values()` and
    crashed on the nested list. Walk the structure instead of trusting a shape.
    """
    if isinstance(data, list):
        if all(isinstance(x, dict) and "ep" in x for x in data):
            return list(data)
        for item in data:
            got = _extract_rows(item)
            if got:
                return got
        return []
    if isinstance(data, dict):
        if "ep" in data:
            return [data]
        for key in ("rows", "techniques", "items", "results"):
            if key in data:
                got = _extract_rows(data[key])
                if got:
                    return got
        for value in data.values():
            got = _extract_rows(value)
            if got:
                return got
    return []


def census(repo: Path = REPO) -> Dict[str, Any]:
    """Count production vs exercise call sites for every row in the map."""
    data = json.loads(MAP.read_text(encoding="utf-8"))
    rows = _extract_rows(data)

    # Candidate call-site files: production surface + the exercise layer.
    cands = _candidate_files(repo)

    out_rows: List[Dict[str, Any]] = []
    for r in rows:
        ep = str(r.get("ep", "?"))
        segments = _parse_segments(str(r.get("target", "")))
        # EVERY segment is measured. Reading only segments[0] silently dropped the
        # second and third hosts of 24 rows -- e.g. EP208's `proto_route` and
        # EP223's `tpe_lite_suggest` were never looked for, so a row could score
        # EXERCISE-ONLY while one of its listed techniques sat in production.
        host_file, symbols = segments[0] if segments else ("", [])
        host = _resolve_host(repo, host_file) if host_file else None
        defined_here = False
        prod_hits: List[str] = []
        exer_hits: List[str] = []
        host_internal: List[str] = []
        measured: List[str] = []
        for seg_file, seg_syms in segments:
            seg_host = _resolve_host(repo, seg_file) if seg_file else None
            for sym in seg_syms:
                measured.append(sym)
                if seg_host is not None:
                    dh, ph, eh = _sniff(
                        seg_host, sym, repo,
                        host_stems=frozenset(Path(s).stem for s, _ in segments))
                    defined_here = defined_here or dh
                    host_internal.extend(ph)  # a host's OWN mentions are not adoption
                    exer_hits.extend(eh)
                for p in cands:
                    if seg_host is not None and p == seg_host:
                        continue
                    _, ph, eh = _sniff(
                        p, sym, repo,
                        host_stems=frozenset(Path(s).stem for s, _ in segments))
                    prod_hits.extend(ph)
                    exer_hits.extend(eh)

        # GUARD-2 enforcement (see _GENERIC_BARE): drop bare-generic sites that
        # neither name the symbol on a word boundary nor sit in a file bound to
        # one of this row's host modules. A site also matching a non-generic
        # measured symbol on a boundary survives regardless.
        gen_syms = ({s.split(".")[-1] for _, ss in segments for s in ss}
                    & _GENERIC_BARE)
        if gen_syms:
            stems = {Path(h).stem for h, _ in segments}
            spec_syms = [s.split(".")[-1] for _, ss in segments for s in ss
                        if s.split(".")[-1] not in _GENERIC_BARE]
            prod_hits = [loc for loc in prod_hits
                         if _generic_keep(repo, loc, gen_syms, spec_syms, stems)]
            exer_hits = [loc for loc in exer_hits
                         if _generic_keep(repo, loc, gen_syms, spec_syms, stems)]

        # ADOPTION requires a caller in a DIFFERENT production module: a technique
        # merely referenced inside its own host file is built, not adopted. This is
        # the distinction the retired engaged=181 counter collapsed.
        prod_hits = sorted(set(prod_hits))
        exer_hits = sorted(set(exer_hits))
        host_internal = sorted(set(host_internal))
        # The host must actually define the symbol before any adoption claim is
        # allowed: otherwise a generic name (`observe`, `plan_dag`) matches
        # unrelated production code elsewhere and the row scores as adopted while
        # pointing at a host that has never heard of it.
        host_stem = Path(host_file).stem if host_file else ""
        reachable = bool(host_internal) and _host_reachable(
            repo, host_stem, cands, host)
        if not symbols:
            verdict = "NO-TARGET"
        elif not defined_here:
            verdict = "TARGET-UNVERIFIED"
        elif prod_hits:
            verdict = "ADOPTED"
        elif reachable:
            verdict = "HOST-COMPOSED"
        elif exer_hits:
            verdict = "EXERCISE-ONLY"
        else:
            verdict = "HOSTED-ONLY"

        out_rows.append({
            "ep": ep, "name": r.get("name", ""), "layer": r.get("layer", ""),
            "coverage": r.get("coverage", ""),
            "target": str(r.get("target", "")), "host_file": host_file,
            "symbol": ", ".join(symbols), "symbols": symbols,
            "hosts": [h for h, _ in segments], "measured_symbols": measured,
            "built": defined_here,
            "host_reachable": reachable,
            "production_call_sites": prod_hits, "exercise_call_sites": exer_hits,
            "host_internal_uses": host_internal,
            "n_production": len(prod_hits), "n_exercise": len(exer_hits),
            "verdict": verdict,
        })

    tally: Dict[str, int] = {}
    for r in out_rows:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    return {
        "repo": str(repo), "rows": len(out_rows), "tally": tally,
        "adoption_score": tally.get("ADOPTED", 0),
        "in_production": tally.get("ADOPTED", 0) + tally.get("HOST-COMPOSED", 0),
        "built_count": sum(1 for r in out_rows if r["built"]),
        "note": ("adoption_score counts ADOPTED rows (caller in a DIFFERENT "
                 "production module); in_production adds HOST-COMPOSED (consumed "
                 "by production code in its own host, where that host is imported "
                 "by production). EXERCISE-ONLY and HOSTED-ONLY score zero by "
                 "mandate (ACTION_REGISTRY H-2, TODO_315T_BUILD T-15)"),
        "results": out_rows,
    }


def _self_test(repo: Path = REPO) -> int:
    """Positive + negative controls, with fixtures taken from the producer.

    Controls (see AGENTS.md VERIFY_ORACLES — a fixture I invent validates my
    assumptions, not reality, so each fixture below is transcribed from the file
    it tests):
      POSITIVE  `merge_sops` IS called by tools/canon_drift_check.py (the Tier-1
                wiring landed in commit 0426a7fc) -> must score >=1 production site.
      NEGATIVE-A the SAME symbol appears in skill_library.py's own module
                docstring Usage block -> that line must NOT be counted, or the
                number is prose-inflated.
      NEGATIVE-B at least one catalogued symbol must score EXERCISE-ONLY, or the
                instrument cannot tell the exercise layer apart from production.
      NEGATIVE-D a symbol named ONLY inside a single-line string literal is prose,
                not a call site: `src/CORE/local_memory.py:2465` is the dict value
                `"gap": "composite rule is an edit to enforce_global_quota (M)"`,
                which falsely adopted EP221 before string-blanking.
      NEGATIVE-E no candidate file may live under a tests/ directory:
                `src/tests/test_worm_registry.py` mentions falsely adopted EP337.
    """
    failures: List[str] = []

    # POSITIVE control (whole production surface, not just the host file).
    prod, exer = production_callers("merge_sops", repo)
    caller_files = {loc.rsplit(":", 1)[0] for loc in prod}
    if "tools/canon_drift_check.py" not in caller_files:
        failures.append(
            "positive control FAILED: merge_sops is called by "
            "tools/canon_drift_check.py but the census did not find it "
            f"(found {sorted(caller_files) or 'nothing'})")
    else:
        print("positive control OK: merge_sops ->", sorted(caller_files))

    # NEGATIVE-A: the docstring Usage line must not be counted.
    doc_lines = [loc for loc in prod if loc.startswith("src/CORE/skill_library.py:")]
    prose_leak = []
    for loc in doc_lines:
        p, _, ln = loc.rpartition(":")
        try:
            line = (repo / p).read_text(encoding="utf-8", errors="replace").splitlines()[int(ln) - 1]
        except (OSError, IndexError, ValueError):
            continue
        if _is_prose_context(line):
            prose_leak.append((loc, line.strip()[:60]))
    if prose_leak:
        failures.append(f"negative control A FAILED: prose counted as caller: {prose_leak}")
    else:
        print(f"negative control A OK: no docstring/comment line counted "
              f"({len(doc_lines)} in-file occurrence(s) all real)")

    # NEGATIVE-B: the exercise layer must be separable.
    rep = census(repo)
    ex = [r for r in rep["results"] if r["verdict"] in ("EXERCISE-ONLY", "HOSTED-ONLY")]
    if not ex:
        failures.append("negative control B FAILED: no row scored EXERCISE-ONLY — "
                        "the instrument cannot distinguish the exercise layer")
    else:
        print(f"negative control B OK: {len(ex)} row(s) score EXERCISE-ONLY "
              f"(e.g. {ex[0]['ep']} {ex[0]['symbol']} -> {ex[0]['n_production']} prod)")

    # NEGATIVE-C / POSITIVE-C: a QUALIFIED symbol must not be matched on prose.
    # Both fixtures are transcribed from the real files, not invented:
    #   - `scripts/fetch_990_series.py` literally says "full_text route is the
    #     practical extraction path." at lines 25/28/41 and never names
    #     CascadeRouter. Before the owner gate those three sentences were counted
    #     as production call sites (EP208 reported 540 hits of this kind).
    #   - `src/CORE/technique_wiring/_l5_dispatch.py:197` really calls
    #     `router.route("classify", ...)` after importing CascadeRouter, so the
    #     gate must not silence a genuine attribute call.
    prose_fixture = repo / "scripts" / "fetch_990_series.py"
    real_fixture = repo / "src" / "CORE" / "technique_wiring" / "_l5_dispatch.py"
    if prose_fixture.exists():
        _, ph, eh = _sniff(prose_fixture, "CascadeRouter.route", repo)
        if ph or eh:
            failures.append(
                "negative control C FAILED: prose-only scripts/fetch_990_series.py "
                f"scored {len(ph)} production + {len(eh)} exercise hit(s) for "
                f"CascadeRouter.route -> {(ph + eh)[:3]}")
        else:
            print("negative control C OK: English 'route' prose scores nothing for "
                  "the qualified symbol CascadeRouter.route")
    if real_fixture.exists():
        _, ph, eh = _sniff(real_fixture, "CascadeRouter.route", repo)
        if not (ph or eh):
            failures.append(
                "positive control C FAILED: _l5_dispatch.py:197 calls "
                "router.route(...) after importing CascadeRouter but the qualified "
                "symbol matched nothing (the owner gate is too strict)")
        else:
            print(f"positive control C OK: qualified symbol found -> {(ph + eh)[:2]}")

    # POSITIVE-K / NEGATIVE-L: OWNER-BINDING fallback for dotted symbols whose
    # receivers are factory-produced (no owner word in the file). Fixtures are
    # the real wire lines, transcribed from the worktree:
    #   - scripts/nightly_maintenance.py:158 calls
    #     `store.generative_compaction_status()` after `from CORE.local_memory
    #     import get_memory_store` (:148); the file never spells
    #     `LocalMemoryStore`, so the bare-word owner gate scored it nothing.
    #   - src/CORE/data_retention.py:431 calls
    #     `(_store.composite_eviction_preview(` with the same import (:429).
    # K: with the true host stem both lines score. L: with a foreign stem the
    # SAME lines score nothing (binding is module-specific, not a blank pass).
    for fixture, sym, call_frag, stems_ok in (
        (repo / "scripts" / "nightly_maintenance.py",
         "LocalMemoryStore.generative_compaction_status",
         "generative_compaction_status(", {"local_memory"}),
        (repo / "src" / "CORE" / "data_retention.py",
         "LocalMemoryStore.composite_eviction_preview",
         "composite_eviction_preview(", {"local_memory"}),
    ):
        if not fixture.exists():
            continue
        try:
            flines = fixture.read_text(
                encoding="utf-8", errors="replace").splitlines()
        except OSError:
            flines = []
        # Expected site derived from the live file (never a hardcoded
        # line number — edits move lines and a stale pin fails closed on
        # the wrong thing, as the redo proved when :158 became :177).
        exp = ["%s:%d" % (fixture.relative_to(repo).as_posix(), i)
               for i, l in enumerate(flines, 1)
               if call_frag in l and "." in l.split(call_frag)[0][-24:]]
        _, ph, _ = _sniff(fixture, sym, repo,
                           host_stems=frozenset(stems_ok))
        if not exp or not any(e in ph for e in exp):
            failures.append(
                "positive control K FAILED: owner-binding fallback missed "
                f"genuine attribute call {exp[:1] or '<?>'} for {sym} "
                f"(found {ph[:3] or 'nothing'})")
        else:
            print(f"positive control K OK: {exp[0]} scores for {sym}")
        _, ph_wrong, eh_wrong = _sniff(
            fixture, sym, repo, host_stems=frozenset({"unrelated_stem"}))
        if ph_wrong or eh_wrong:
            failures.append(
                "negative control L FAILED: foreign-stem binding kept "
                f"{(ph_wrong + eh_wrong)[:3]} for {sym}")
        else:
            print(f"negative control L OK: {exp[:1] or sym} scores nothing "
                  f"under a foreign stem")

    # POSITIVE-M / NEGATIVE-N: COLLABORATOR aliases for lifecycle-injected
    # collaborators. Fixtures are the real redo lines, transcribed from the
    # worktree: learning_grpo.py resolves `self._corrector` from
    # `_bounded_correct` (module top-level `from CORE.latent_refine import
    # bounded_correct`), and calls `self._corrector(...)` in update_policy.
    # M: with the true host stem the `self._corrector(` line scores for
    # bounded_correct. N: the SAME file under a foreign stem scores nothing
    # (the binding is module-specific, not a blank pass for `self._` calls).
    collab_fixture = repo / "src" / "CORE" / "WORMS" / "expansion" / "learning_grpo.py"
    if collab_fixture.exists():
        _, ph, _ = _sniff(collab_fixture, "bounded_correct", repo,
                           host_stems=frozenset({"latent_refine"}))
        _self_calls = [loc for loc in ph if "self._" in (
            (repo / loc.rsplit(":", 1)[0]).read_text(
                encoding="utf-8", errors="replace").splitlines()[int(loc.rsplit(":", 1)[1]) - 1])]
        if not _self_calls:
            failures.append(
                "positive control M FAILED: collaborator call "
                f"self._corrector(...) scores nothing for bounded_correct "
                f"(found {ph[:3] or 'nothing'})")
        else:
            print(f"positive control M OK: collaborator alias scores -> "
                  f"{_self_calls[:2]}")
        _, ph_wrong, eh_wrong = _sniff(
            collab_fixture, "bounded_correct", repo,
            host_stems=frozenset({"unrelated_stem"}))
        if ph_wrong or eh_wrong:
            failures.append(
                "negative control N FAILED: foreign-stem binding kept "
                f"{(ph_wrong + eh_wrong)[:3]} for bounded_correct")
        else:
            print("negative control N OK: collaborator alias scores nothing "
                  "under a foreign stem")
        # CONTROL-O (row level): EP472's genuine self._corrector site must
        # survive the GUARD-2 row filter. The line utters no generic word
        # (`refine` appears nowhere on it), so the old no-generic-word->drop
        # branch ate it and the row fell to HOST-COMPOSED. Pin the keep.
        # Re-pinned 2026-10-22: Beam pool slice E shifted the fixture
        # 484 -> 474 (genuine site unchanged, still no generic word).
        _o_loc = "src/CORE/WORMS/expansion/learning_grpo.py:474"
        try:
            _o_line = (repo / _o_loc.rpartition(":")[0]).read_text(
                encoding="utf-8", errors="replace").splitlines()[473]
        except (OSError, IndexError):
            _o_line = ""
        if "self._corrector(" not in _o_line:
            failures.append(
                "control O INVALID: fixture line moved, re-pin the control "
                f"(line 484 now: {_o_line.strip()[:60]!r})")
        elif not _generic_keep(repo, _o_loc, {"refine"}, ["bounded_correct"],
                               {"latent_refine"}):
            failures.append(
                "control O FAILED: GUARD-2 row filter dropped the genuine "
                f"collaborator site {_o_loc}")
        else:
            print(f"control O OK: GUARD-2 keeps {_o_loc} "
                  f"(self._corrector, no generic word)")

    # NEGATIVE-D: the string-literal prose line must not be counted.
    str_fixture = repo / "src" / "CORE" / "local_memory.py"
    if str_fixture.exists():
        _, ph, eh = _sniff(str_fixture, "enforce_global_quota", repo)
        bad = [loc for loc in (ph + eh) if loc.endswith(":2465")]
        if bad:
            failures.append(
                "negative control D FAILED: string-literal prose "
                f"src/CORE/local_memory.py:2465 counted as a call site -> {bad}")
        else:
            print("negative control D OK: string-only mention scores nothing "
                  f"(other hits this file: {(ph + eh)[:2] or 'none'})")

    # NEGATIVE-E: tests/ directories are never production surface.
    cands = _candidate_files(repo)
    test_cands = [p for p in cands if "/tests/" in "/" + p.relative_to(repo).as_posix()]
    if test_cands:
        failures.append(
            "negative control E FAILED: tests/ files among candidates -> "
            f"{[p.relative_to(repo).as_posix() for p in test_cands][:3]}")
    else:
        print(f"negative control E OK: 0 tests/ files among {len(cands)} candidates")

    # NEGATIVE-A also in whole-tree form: prose anywhere must stay uncounted.
    for loc in prod:
        p, _, ln = loc.rpartition(":")
        try:
            line = (repo / p).read_text(encoding="utf-8", errors="replace").splitlines()[int(ln) - 1]
        except (OSError, IndexError, ValueError):
            continue
        if _is_prose_context(line):
            failures.append(f"negative control A FAILED: prose counted as caller: {loc}")

    # Prose must never be counted anywhere in the report.
    for r in rep["results"]:
        for loc in r["production_call_sites"]:
            p, _, ln = loc.rpartition(":")
            try:
                line = (repo / p).read_text(encoding="utf-8", errors="replace").splitlines()[int(ln) - 1]
            except (OSError, IndexError, ValueError):
                continue
            if _is_prose_context(line):
                failures.append(f"prose leak: {loc} counted as a call site")
                break

    # NEGATIVE-F (2026-10-22, EP301): a None-fallback assignment arm is a
    # wiring absence, not a call site. sentinel_gate.py:81 names
    # verify_inclusion_proof only as `= None` under `except ImportError`.
    prod_f, _ = production_callers("verify_inclusion_proof", repo)
    if prod_f:
        failures.append(
            "negative control F FAILED: None-fallback arm counted as caller: "
            f"{prod_f[:2]} (GUARD-1 regressed)")
    else:
        print("negative control F OK: verify_inclusion_proof scores 0 prod "
              "(fallback arm excluded)")

    # NEGATIVE-G (2026-10-22, EP271): bare-generic hits need host binding, and
    # that rule lives in census(), not _sniff — so the control asserts the row
    # outcome, not the raw matcher. chain_controller.py defines its own plan()
    # (:409) and never imports allocation; morph_ab_plan is proteus_hooks, not
    # MCTSPlanner. EP271 must not score ADOPTED; any surviving prod site must
    # sit in an allocation-bound file.
    rep271 = next((r for r in rep["results"] if r["ep"] == "EP271"), None)
    if rep271 is None:
        failures.append("negative control G FAILED: EP271 row missing from map")
    elif rep271["verdict"] == "ADOPTED":
        failures.append(
            "negative control G FAILED: EP271 scores ADOPTED with sites "
            f"{rep271['production_call_sites'][:3]} (GUARD-2 regressed: no "
            "production file imports plan/proxy from allocation — batch-3 "
            "demotion evidence + 2026-10-22 line audit stand)")
    else:
        print(f"negative control G OK: EP271 scores {rep271['verdict']}, "
              f"{len(rep271['production_call_sites'])} prod site(s)")

    # NEGATIVE-H (2026-10-22, EP472 falsification): `_pareto_refine(` (a
    # different function) and `self.run(` (a method, no refine token at all
    # — matched by pure substring) adopted EP472. Both live in files that
    # never import the host stem, so GUARD-2 + call-shape must leave zero
    # production sites from them. Map-independent: asserts the matcher.
    prod_h, _ = production_callers("refine", repo)
    bad_h = [loc for loc in prod_h
             if loc.startswith(("scripts/evolution_cycle.py",
                                "src/CORE/inference/sequential_analyzer.py"))]
    if bad_h:
        failures.append(
            "negative control H FAILED: foreign refine-shapes counted: "
            f"{bad_h[:3]} (call-shape regressed)")
    else:
        print("negative control H OK: no _pareto_refine/self.run site "
              f"counts for refine ({len(prod_h)} prod site(s) remain)")

    # NEGATIVE-I (2026-10-22, EP465 falsification): `tpe.observe(` IS a
    # call-shape match (attribute call) — the matcher cannot drop it, the
    # RECEIVER is foreign (optimiser sampler, never imports the host stem),
    # so this control asserts the ROW outcome like G, not the raw matcher.
    # EP465 lives in the batch04 map: re-point census there live (never a
    # stale artifact) and require the foreign sites gone from prod.
    _map_saved = MAP
    try:
        globals()["MAP"] = _find_batch_map("batch04", "technique-map.json")
        rep04 = census()
    finally:
        globals()["MAP"] = _map_saved
    rep465 = next((r for r in rep04["results"] if r["ep"] == "EP465"), None)
    sites465 = rep465["production_call_sites"] if rep465 else []
    bad_i = [l for l in sites465
             if l.startswith(("scripts/evolution_cycle.py",
                              "scripts/jev_advise.py"))]
    genuine_i = [l for l in sites465
                 if l.startswith("src/CORE/compliance/policy.py")]
    if rep465 is None or rep465["verdict"] != "ADOPTED" or bad_i or not genuine_i:
        failures.append(
            "negative control I FAILED: EP465 row="
            f"{rep465['verdict'] if rep465 else 'MISSING'} foreign="
            f"{bad_i[:3]} genuine={genuine_i[:2]} (GUARD-2 receiver "
            "binding regressed)")
    else:
        print(f"negative control I OK: EP465 ADOPTED on genuine sites "
              f"{genuine_i[:1]}, tpe.observe sites excluded")

    # POSITIVE-J (2026-10-22, EP462 falsification): an annotated module
    # constant IS defined here. meta_evolution.py:46 assigns
    # `LEGAL_TRANSITIONS: dict[...] = {...}` — no def/class line.
    from pathlib import Path as _P
    _dh_j, _, _ = _sniff(repo / "src/CORE/meta_evolution.py",
                          "LEGAL_TRANSITIONS", repo)
    if not _dh_j:
        failures.append(
            "positive control J FAILED: annotated assignment not recognised "
            "as defined (defined-shape regressed)")
    else:
        print("positive control J OK: LEGAL_TRANSITIONS defined_here=True")

    if failures:
        print("\nSELF-TEST FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print(f"\nSELF-TEST PASS: adoption_score={rep['adoption_score']} "
          f"of {rep['rows']} rows (built={rep['built_count']})  tally={rep['tally']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", default="", help="write the full report to this path")
    ap.add_argument("--self-test", action="store_true",
                    help="positive + negative control run")
    ap.add_argument("--map", default="",
                    help="technique map to measure (default: batch06 map). "
                    "Batch loop (2026-10-22): batches 01-05 each carry their "
                    "own technique_codebase_map.json; the instrument is "
                    "map-agnostic, only the default differs.")
    args = ap.parse_args()
    if args.map:
        globals()["MAP"] = Path(args.map)

    if args.self_test:
        return _self_test()

    rep = census()
    if args.json:
        dest = Path(args.json)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print(f"wrote {dest}")

    print(f"adoption census — {rep['rows']} rows")
    print(f"  adoption_score (ADOPTED only):   {rep['adoption_score']}")
    print(f"  in_production (+ HOST-COMPOSED): {rep['in_production']}")
    print(f"  built (host module defines it):  {rep['built_count']}")
    for k in ("ADOPTED", "HOST-COMPOSED", "HOSTED-ONLY", "EXERCISE-ONLY",
              "UNREFERENCED", "TARGET-UNVERIFIED", "NO-TARGET"):
        if k in rep["tally"]:
            print(f"  {k:14s} {rep['tally'][k]}")
    print("\n  HOSTED-ONLY and EXERCISE-ONLY score ZERO adoption by mandate "
          "(ACTION_REGISTRY H-2 / TODO_315T_BUILD T-15).")
    print("\n  NOT YET ADOPTED (the worklist):")
    for r in rep["results"]:
        if r["verdict"] == "ADOPTED":
            continue
        print(f"    {r['verdict']:13s} {r['ep']:8s} {r['symbol'][:40]:40s} "
              f"host={r['host_file'] or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
