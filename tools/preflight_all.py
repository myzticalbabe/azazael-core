#!/usr/bin/env python3
# coding: utf-8
"""preflight_all.py — the tracked gate registry, runner, and self-test.

Why this exists
---------------
STRUCTURAL_FLAWS S10 recorded that the pre-commit gate lived only in
`.githooks/pre-commit`, which is gitignored, so "which gates exist" was readable
only by reading a shell hook, and a fresh clone had no gate at all. The hook is
now force-added, but the inventory problem remains: the checks are assembled
inline, so nothing can answer -- mechanically -- "what gates does this repo run,
on which interpreter, against which artifact, and what proves each one CAN
fail".

The false-green protocol
(`.agents/skills/azazael-false-green-protocol/SKILL.md`) states its Enforcement
clause as a requirement nobody executes:

    "every gate has tree hash + mtime + --force + missing evidence fail"

This tool executes it as a MEASUREMENT rather than an assertion:

  * ``--list``      the inventory, transcribed from the hook (the producer).
  * ``--check``     run the gates. A gate that cannot run is a FAILURE, never a
                    silent pass, unless declared via ``--allow-unrunnable``.
  * ``--self-test`` prove fail-ability: every declared interpreter must actually
                    execute, every declared control artifact must exist on disk,
                    every executable control must fail as required, every gate's
                    REQUIRED artifact must be present (a gate whose input is gone
                    cannot judge at all -- it is BLIND, not green), and the number
                    of gates with NO control is PRINTED rather than rounded to a
                    pass. Add ``--run-controls`` to EXECUTE each declared control
                    artifact, which turns declared fail-ability into demonstrated;
                    add ``--strict`` to make a BLIND gate a failure.
  * ``--force``     re-run. A cached PASS is reused only while its evidence still
                    describes the tree it was recorded against (the gate's own
                    bytes + the staged and worktree state it judged), so a gate
                    -- or the tree -- edited after its last green run re-runs.

A control the instrument cannot fail is not a control, so a gate that has no
control is reported as UNCONTROLLED with its reason -- never counted as green.

Exit codes: 0 pass, 1 a gate failed / a control did not fail / evidence missing,
2 the registry or the interpreter environment is unusable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

# Ensure repo root is on sys.path for tools imports
REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.defaults_profile import read_default
HOOK = REPO / ".githooks" / "pre-commit"
#: The commit gate is TWO files since the 2026-11-02 tier split: `pre-commit`
#: holds the checks about what a commit IS, `tools/evidence_tier.sh` holds the
#: whole-tree re-derivations that the push hook runs before anything leaves the
#: machine. Parity is a question about the GATE SET, not about one file, so it
#: reads both -- reading only `pre-commit` reported fourteen gates as
#: registered-but-not-run on a tree where they ran, from a copy of the file that
#: is no longer the whole hook.
#:
#: Resolved at CALL time, not import time. As a module constant this made
#: ``REPO``/``HOOK`` un-overridable: a caller that retargeted them still read this
#: host's hook. Measured 2026-11-02 -- every fixture in
#: tests/test_preflight_all.py compared a one-gate registry against the real
#: 24-gate hook and reported fourteen gates of drift that did not exist, and the
#: one test that WANTED drift could not see it.
#:
#: The evidence tier is in `tools/`, not `.githooks/lib/`, because `.gitignore`
#: excludes `.githooks/*` apart from `pre-commit`: a parity check that reads an
#: ignored path reads whatever this host happens to have and calls it the gate.
def hook_tier_files() -> Tuple[Path, ...]:
    return (HOOK, REPO / "tools" / "evidence_tier.sh")
#: Generated and gitignored (`.githooks/*`), planted by tools/bible_ship.py; it
#: carries the push-time gates, which no other check can reach.
PUSH_HOOK = REPO / ".githooks" / "pre-push"
EVIDENCE = REPO / "logs" / "preflight_all_evidence.json"

# Read gates config from defaults
GATES_MCP_STARTUP = read_default("gates.mcp_startup", "blocking")
GATES_EVIDENCE_GATE = read_default("gates.evidence_gate", "blocking")
GATES_HYPER_EDGE = read_default("gates.hyper_edge", "advisory")
GATES_COMMIT_HOOK = read_default("gates.commit_hook", "blocking")
GATES_SETTINGS_SHADOW = read_default("gates.settings_shadow", "advisory")
GATES_READING_AUDIT = read_default("gates.reading_audit", "advisory")
GATES_HEAD_AUDIT = read_default("gates.head_audit", "advisory")
GATES_LEG_LEDGER = read_default("gates.leg_ledger", "advisory")

#: A gate that hangs must not hang the gate runner. The suite gates are the long
#: ones (the two-interpreter gate runs pytest twice); this is the ceiling.
RUN_TIMEOUT = 3600.0
PROBE_TIMEOUT = 120.0
GIT_TIMEOUT = 120.0

#: `(?:prefix/)*(?:tools|scripts)/name.py` -- used to re-derive the hook's gate
#: inventory from the hook text itself, so the registry cannot drift from its
#: producer.
#:
#: THE LEADING GROUP IS LOAD-BEARING. A pattern anchored at `tools/` reads only the
#: two top-level script dirs, so a gate that lives under a project directory
#: (`Project_ELIZA/tools/shard_intake.py`, wired 2026-11-03) was derived as
#: `tools/shard_intake.py`: a path that does not exist, matched against a registry
#: entry that does, reported as registered-but-not-run on a tree where the gate ran
#: on every push.
#:
#: `(?<![\\w$])` stops a match starting INSIDE a longer token. The tier file
#: expresses paths as `"$SCRIPT_DIR/Project_ELIZA/tools/x.py"`: without the guard the
#: engine could begin at `SCRIPT_DIR` and yield `SCRIPT_DIR/Project_ELIZA/tools/x.py`,
#: which is not a repo-relative path and would be compared against one that is.
_SCRIPT_RE = re.compile(
    r"(?<![\w$])(?:[A-Za-z0-9_.-]+/)*(?:tools|scripts)/[A-Za-z0-9_]+\.py")


class Gate(NamedTuple):
    """One registered gate.

    ``interpreter`` is a NAME, not a path: resolution happens at run time so the
    same registry works on every host. ``env`` holds ``K=V`` entries that the
    hook prepends (``PYTHONPATH=src`` is how the tools import ``CORE``).
    """

    name: str
    argv: Tuple[str, ...]
    interpreter: str
    env: Tuple[str, ...]
    inside_hook: bool
    blocking: bool
    slow: bool
    reads: str
    control_test: Optional[str]
    control: Optional[str]
    uncontrolled_because: Optional[str]
    #: Repo-relative artifacts the gate READS to reach a verdict. Their absence
    #: means the gate cannot judge at all -- BLIND. A baseline that was never
    #: recorded on this machine is the common case: the gate exits 2 rather than
    #: reporting green, and nothing before this surfaced that as a fact.
    requires: Tuple[str, ...] = ()


#: The registry. Every ``inside_hook`` row is transcribed verbatim from
#: `.githooks/pre-commit`; `--self-test` re-derives that list from the hook text
#: and fails if the two disagree, so this table cannot silently drift from the
#: gate that actually runs.
GATES: Tuple[Gate, ...] = (
    Gate(
        name="disk-floor",
        argv=("tools/disk_floor_gate.py",),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads=("free space on the repo's volume (and any --path), against "
               "preflight.disk_floor_gb"),
        control_test="tests/test_disk_floor_gate.py",
        control="disk-floor",
        uncontrolled_because=None,
        requires=(),  # reads the filesystem itself, no artifact
    ),
    Gate(
        name="changeset-focus",
        argv=("scripts/commit_gate.py", "--hook"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads=("the staged changeset (git index), or BASE..HEAD under --base "
               "-- file count and changed lines"),
        control_test="tests/test_commit_gate.py",
        control=None,
        uncontrolled_because=None,
        requires=(),  # reads the git index, which repo_state_hash covers
    ),
    Gate(
        name="stale-index-guard",
        argv=("tools/index_drift_check.py",),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="every staged path vs its worktree content",
        control_test="tests/test_index_drift_check.py",
        control=None,
        uncontrolled_because=None,
        requires=(),  # two git queries, no file
    ),
    Gate(
        name="staged-scanner",
        argv=("tools/staged_scanner_gate.py",),
        interpreter="python3",
        env=("PYTHONPATH=src",),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads=("each staged .py at :0: vs HEAD, or 'BASE:path' vs 'HEAD:path' "
               "under --base, per finding category"),
        control_test="tests/test_staged_scanner_gate.py",
        control=None,
        uncontrolled_because=None,
        requires=(),  # the index, not a file
    ),
    Gate(
        name="canon-drift",
        argv=("tools/canon_drift_check.py", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="skills/ (source corpus, RULE 0R) vs .agents/skills/<n>/SKILL.md",
        control_test="tests/test_canon_drift_check.py",
        control=None,
        uncontrolled_because=None,
        requires=("skills", ".agents/skills"),  # SOURCE_DIR / LOAD_DIR
    ),
    Gate(
        name="anti-pattern-ratchet",
        # --tracked-only, matching .githooks/pre-commit (2026-11-01): the tree
        # scan counts untracked files, so three `??` modules from another lane
        # blocked a commit that contained none of them. Staged additions are
        # still counted -- `git ls-files` reads the index.
        argv=("tools/anti_pattern_gate.py", "--check", "--tracked-only"),
        interpreter="python3",
        env=("PYTHONPATH=src",),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="git-tracked src/ files vs logs/anti_pattern_baseline.json",
        control_test="tests/test_anti_pattern_gate.py",
        control="anti-pattern-ratchet",
        uncontrolled_because=None,
        requires=("logs/anti_pattern_baseline.json",),
    ),
    Gate(
        name="span-receipt",
        # --selftest, not a live check, and the reason is in the hook comment:
        # a commit has no route, so "no span since HEAD" is true for every doc
        # edit. What the commit can prove is that the guard still refuses an
        # untraced route AND still accepts a traced one; the live form is
        # `tools/span_receipt_guard.py --result <route_result.json>`.
        argv=("tools/span_receipt_guard.py", "--selftest"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="a temporary otel_spans table (written and read back in-process)",
        control_test="tests/test_span_receipt_guard.py",
        control=None,
        uncontrolled_because=None,
        requires=(),  # builds its own database; reads nothing in the tree
    ),
    Gate(
        name="unresolved-refs-ratchet",
        argv=("tools/unresolved_ratchet.py", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="the shell/batch cross-language graph vs tools/unresolved_baseline.json",
        control_test="tests/test_yggdrasil_instruments.py",
        control=None,
        uncontrolled_because=None,
        requires=("tools/unresolved_baseline.json",),
    ),
    Gate(
        name="mesh-ledger-freshness",
        argv=("tools/mesh_function_preflight.py", "--check"),
        interpreter="python3",
        env=("PYTHONPATH=src",),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="NEURAL_MESH_WIRE_OR_CUT.json vs the live call graph",
        control_test="tests/test_mesh_wire_or_cut.py",
        control=None,
        uncontrolled_because=None,
        requires=("NEURAL_MESH_WIRE_OR_CUT.json",),
    ),
    Gate(
        name="shard-intake",
        # Registered 2026-11-03 with the tier stanza that calls it. Before this the
        # gate existed and had no caller at all: Project_ELIZA/shards holds 1,183
        # generated stubs against the Kali atlas, every one addressable and every
        # load() raising, and `--check` -- the only thing that compares that surface
        # to the atlas it claims to describe -- ran nowhere.
        #
        # BLOCKING, and the missing-atlas case is why the tool grew a third state.
        # The atlas is out of repo (~/yggdrasil_forest/atlas_kali-linux.json, written
        # by tools/domain_trees.py on the Kali lane), so a host that never ran that
        # census cannot judge the surface. The tool now exits 2 for that -- "could not
        # perform the run" -- instead of 1, so the refusal says which of the two
        # things happened. It still BLOCKS: `non-blocking` is reserved for gates whose
        # exit code is advisory by design (test_hook_non_blocking_gates_are_exactly_
        # the_advisory_ones), and a drift gate parked there would be decoration. The
        # remedy is one command, named in the output.
        #
        # Escape, same idiom as ALLOW_EVIDENCE_GATE: ALLOW_SHARD_INTAKE=1.
        argv=("Project_ELIZA/tools/shard_intake.py", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads=("Project_ELIZA/shards/SHARD_INDEX.json + every stub it indexes + "
               "WIRED.json, re-derived against ~/yggdrasil_forest/atlas_kali-linux.json"),
        control_test="Project_ELIZA/tests/test_shard_intake.py",
        control=None,
        uncontrolled_because=None,
        requires=("Project_ELIZA/shards/SHARD_INDEX.json",),
    ),
    Gate(
        name="wire-evidence",
        argv=("tools/wire_evidence_audit.py", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="each `wired` row of NEURAL_MESH_WIRE_OR_CUT.json vs the AST of src/scripts/tools",
        control_test="tests/test_wire_evidence_audit.py",
        control=None,
        uncontrolled_because=None,
        requires=("NEURAL_MESH_WIRE_OR_CUT.json",),
    ),
    Gate(
        name="testing-paradigm-rule1",
        # The hook runs tools/test_paradigm_gate.py (check 7b). This row named
        # tools/paradigm_gate.py, a different script, so the registry described
        # a gate nobody runs while the real one was invisible to parity.
        argv=("tools/test_paradigm_gate.py",),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="NEW staged src/ modules vs tests/ (G1); Hypothesis coverage advisory (G2)",
        control_test="tests/test_paradigm_gate.py",
        control=None,
        uncontrolled_because=None,
        requires=(),  # reads the git index
    ),
    Gate(
        name="testing-paradigm-advisory",
        argv=("tools/paradigm_gate.py", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        # Check 7: exit code is printed, not enforced, unless
        # PARADIGM_GATE_STRICT=1. A presence heuristic must not block commits
        # until it has been measured, so it is registered as non-blocking.
        blocking=False,
        slow=False,
        reads="new src/ functions vs staged test files carrying a Hypothesis property",
        control_test=None,
        control=None,
        uncontrolled_because=(
            "advisory in the hook by design (exit code printed, enforced only "
            "under PARADIGM_GATE_STRICT=1); a non-zero exit is not a defect signal"
        ),
        requires=(),
    ),
    Gate(
        name="agent-orders",
        # `--hook`, not `--check`: the targets are host-absolute lane files, so the
        # commit path compares only those that exist and reports the rest. Strict
        # mode would block every commit on a host that does not carry all nine.
        argv=("tools/agent_orders_sync.py", "--hook"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads=(
            "each generated block in the nine instruction files vs "
            "docs/agent_orders/*.md (the single source for each block)"
        ),
        control_test="tests/test_agent_orders_sync.py",
        control="agent-orders-negative-control",
        uncontrolled_because=None,
        requires=(
            "docs/agent_orders/00_session_order.md",
            "docs/agent_orders/flock_canon.md",
            "docs/agent_orders/meta_levels_canon.md",
        ),
    ),
    Gate(
        name="prompt-claims",
        # The companion to `agent-orders`, and registered separately because the two
        # false-pass in different directions. `agent-orders` compares each GENERATED
        # block to its source; it cannot see the prose between blocks, and measured
        # 2026-11-02 that prose claimed 5,380 tests against a live 6,277, named six
        # paths that no longer existed, and carried a hand-copied RENDER section that
        # existed in no source at all. The block check was green throughout.
        argv=("tools/prompt_check.py",),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads=(
            "the seven lane instruction files: backticked paths stat'd, counts "
            "compared to state/head.json, the newest handoff's commit resolved, "
            "and the START-HERE contract's offset in the file"
        ),
        control_test="tests/test_prompt_check.py",
        control="prompt-check-controls",
        uncontrolled_because=None,
        # `requires` names an artifact whose ABSENCE makes the gate blind. This one
        # is different: `state/head.json` is gitignored by design, and the gate now
        # records it on demand (head_state.py --write) before judging, so a missing
        # reading is a condition it repairs, not a blindness. If the recording
        # itself fails the gate exits 2 -- a failed gate, not a BLIND registration.
        requires=(),
    ),
    Gate(
        name="supervisor-verdict",
        argv=("tools/supervisor_mcp.py", "--verdict"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="engram doctor + conflict count, PMB daemon liveness, PAMS liveness probes",
        control_test=None,
        control=None,
        uncontrolled_because=(
            "no dedicated control artifact: the leg behaviour is asserted by the "
            "pre-commit wiring test (tests/test_preflight_gate_wiring.py) and the "
            "verdict is re-derived live on every commit; a recorded baseline "
            "would pin a memory-state answer that changes between runs"
        ),
        requires=(),  # external daemons, not repo paths
    ),
    Gate(
        name="transcript-ap5",
        argv=("tools/transcript_ap5_gate.py", "--limit", "500"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        # Advisory IN THE HOOK (`|| true`): the gate reads assistant text AFTER
        # emission, so it can make a violation visible but cannot prevent it.
        blocking=False,
        slow=False,
        reads="assistant output parts in opencode.db / the Claude transcripts",
        control_test="tests/test_ap5_gate_stores.py",
        control=None,
        uncontrolled_because=None,
        requires=(),  # transcript stores, not repo paths
    ),
    Gate(
        name="bible-citations",
        argv=("tools/bible_check.py", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="docs/bible/bible.json -- verse numbering, citation targets, the "
              "command behind every figure, statute rule/check bindings",
        control_test="tests/test_bible_check.py",
        control=None,
        uncontrolled_because=None,
        # The corpus is the thing judged. Absent, this gate is BLIND rather than
        # green: an empty file passes every citation check ever written.
        requires=("docs/bible/bible.json",),
    ),
    Gate(
        name="bible-emotional",
        argv=("tools/bible_check.py", "--check", "--corpus",
              "docs/bible/emotional/emotional.json"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="docs/bible/emotional/emotional.json -- the affect corpus: citations, "
              "digit-free prose, statute bindings, and its own generated edition",
        control_test="tests/test_bible_corpora.py",
        control=None,
        uncontrolled_because=None,
        requires=("docs/bible/emotional/emotional.json",),
    ),
    Gate(
        name="bible-female",
        argv=("tools/bible_check.py", "--check", "--corpus",
              "docs/bible/emotional/female.json"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="docs/bible/emotional/female.json -- the specialised corpus: citations, "
              "corrected readings, refusals, and its own generated edition",
        control_test="tests/test_bible_corpora.py",
        control=None,
        uncontrolled_because=None,
        requires=("docs/bible/emotional/female.json",),
    ),
    Gate(
        name="bible-themes",
        # Registered 2026-11-02. The two themed editions (star-wars, star-trek)
        # restate the canon in another register and were gated by NOTHING: no
        # hook stanza, no registry row, only a test no commit runs. Measured
        # 2026-11-02: both printed OK, so the gate would have been green for
        # months -- which is the point. A green light nobody wired is an
        # unmeasured claim, and the first theme to invent a verse would have
        # shipped inside a document that quotes scripture.
        argv=("tools/bible_theme.py", "--all", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads="docs/bible/themes/*.json -- every themed verse's citation into the "
              "base corpus, its statute binding against canon, and its rendered "
              "edition",
        control_test="tests/test_bible_theme.py",
        control=None,
        uncontrolled_because=None,
        requires=("docs/bible/themes", "docs/bible/bible.json"),
    ),
    Gate(
        name="platform-claims",
        argv=("tools/platform_claims.py", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads=(
            "docs/platforms/claims.json -- every claim a platform page makes, "
            "re-run: the documented command, its output substring and its exit code"
        ),
        control_test="tests/test_platform_claims.py",
        control="platform-claims-self-test",
        uncontrolled_because=None,
        # The ledger is the thing judged; absent, the check would pass on an
        # empty claim list, which is how a guide stops being checked quietly.
        requires=("docs/platforms/claims.json",),
    ),
    Gate(
        name="pmb-outcome-observer",
        argv=("tools/pmb_session_observe.py", "--commit-ok", "--quiet"),
        interpreter="pmb-venv",
        env=(),
        inside_hook=True,
        # Deliberately non-blocking IN THE HOOK: "a memory side-effect must never
        # reject a commit", so its exit code is ignored there. Registered anyway,
        # so that the inventory is complete and `--check` still reports it.
        blocking=False,
        slow=False,
        reads="the PMB agent_actions table (Earned-Memory loop)",
        control_test=None,
        control=None,
        uncontrolled_because=(
            "side-effect recorder, exit code ignored by the hook; a non-zero exit "
            "is not a defect signal, so there is nothing to control"
        ),
        requires=(),  # the PMB daemon's own store, not a repo path
    ),
    Gate(
        name="arc-container",
        # Inside no hook on purpose: the container grades an artifact (ARC's
        # wings), not a change in the diff. Registered anyway so the inventory
        # is complete and the evidence leg is never a tool nobody runs.
        argv=("tools/arc_check.py",),
        interpreter="python3",
        env=(),
        inside_hook=False,
        # Deliberately non-blocking: the exit code IS the artifact's graded state
        # (READY / GAPS / INCOMPLETE / BLIND), so a wing holding a named defect
        # -- eliza's publication gate, esquire's uncovered tier-A pins -- would
        # otherwise turn the shared sweep red for a condition its own lane owns.
        # Flipping this to blocking is that lane's call, once the container reads
        # READY and the reading means "regressed" instead of "still short".
        blocking=False,
        slow=False,
        reads=(
            "ARC/ -- each declared wing's own checker plus the evidence leg "
            "(tools/arc_provenance.py), folded into one container verdict"
        ),
        control_test=None,
        control=None,
        uncontrolled_because=(
            "the exit code reports the ARC artifact's state, not a repository "
            "instrument's health; non-zero is the expected reading while a wing "
            "is at GAPS or a pin is uncovered, so it is not a defect signal"
        ),
        requires=("ARC/README.md",),
    ),
    Gate(
        name="two-interpreter-suite",
        argv=("scripts/ci_two_interpreter_gate.py",),
        interpreter="python3",
        env=(),
        inside_hook=False,
        blocking=True,
        slow=True,
        reads="the suite on both interpreters vs logs/ci_two_interpreter_gate_baseline.json",
        control_test="tests/test_ci_two_interpreter_gate.py",
        control=None,
        uncontrolled_because=None,
        requires=("logs/ci_two_interpreter_gate_baseline.json",),
    ),
    Gate(
        name="aggregate-suite-isolation",
        argv=("tools/aggregate_suite_gate.py", "--check", "--profile", "isolation"),
        interpreter="python3",
        env=(),
        inside_hook=False,
        blocking=True,
        slow=True,
        reads="single-process tests/ vs logs/aggregate_isolation_baseline.json",
        control_test="tests/test_aggregate_suite_gate.py",
        control=None,
        uncontrolled_because=None,
        requires=("logs/aggregate_isolation_baseline.json",),
    ),
    Gate(
        # Registered 2026-11-01 after the wiring control named this gate as
        # unwired-but-present. What decides it is the SUBJECT: the gate needs a
        # live worm registry and a live memory store (it discovered 257 worms,
        # executed AnchorRetrievalWorm and read query_memories() evidence back),
        # so a bare commit hook -- which may run with neither -- cannot judge it.
        # inside_hook=False is therefore the honest placement, and `--check`
        # still runs it as part of the measurement instead of leaving a gate that
        # nothing executes. Measured the same day: 7/7 PASS in 5.1s.
        name="anchor-runtime",
        argv=("tools/anchor_runtime_gate.py",),
        interpreter="python3",
        env=("PYTHONPATH=src",),
        inside_hook=False,
        blocking=True,
        slow=True,
        reads=("the live worm registry (discover_worms), one executed "
               "AnchorRetrievalWorm run, and query_memories(source='technique-link') "
               "evidence rows -- no baseline file"),
        control_test=None,  # its controls are internal; see uncontrolled_because
        control=None,
        uncontrolled_because=(
            "no separate control artifact: the gate runs its own negative "
            "controls on every invocation (N1 a bogus worm name must NOT resolve "
            "through discover_worms, N2 a bogus subject must match ZERO evidence "
            "rows). Those are demonstrated in the gate's own output rather than "
            "by a test file, so this row is reported UNCONTROLLED, never green"),
        requires=(),  # reads live processes/store, not a file this runner can hash
    ),
    Gate(
        # Registered 2026-11-01, the day its 29 findings across 10 files reached
        # 0 (7 of them in tools/mcp_startup_gate.py, the one this lane had to be
        # able to trust before wiring it). The reason it was NOT registered
        # before is the reason it could be registered now: it was RED, and a gate
        # that fails on day one is a gate somebody disables.
        name="failure-evidence-ratchet",
        argv=("tools/failure_evidence_gate.py", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads=("every subprocess.run/call/check_output/check_call under tools/ and "
               "scripts/ (AST, 550 files): an unbounded wait, or a text-mode call "
               "with no encoding= -- vs logs/failure_evidence_baseline.json"),
        control_test="tests/test_failure_evidence_gate.py",
        control=None,
        uncontrolled_because=None,
        requires=("logs/failure_evidence_baseline.json",),
    ),
    Gate(
        name="type-check",
        argv=("tools/type_check_gate.py", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=False,
        blocking=True,
        slow=True,
        reads="basedpyright output vs its own baseline",
        control_test=None,
        control=None,
        uncontrolled_because=(
            "no dedicated control artifact is asserted for this gate at "
            "registration time; its baseline-ratchet shape is the same as "
            "anti-pattern-ratchet, whose control is executable"
        ),
        requires=("logs/basedpyright_baseline.json",),
    ),
    Gate(
        name="evidence-gate",
        # wired_claims is NOT in this argv: .githooks/pre-commit Check 6 already
        # gates wired-row link evidence via tools/wire_evidence_audit.py, and
        # running the same judgement twice in one commit buys nothing. The
        # check stays available directly (`--only wired_claims`) and its 135
        # measured rows are recorded in the baseline rather than re-litigated
        # on every commit.
        argv=("tools/evidence_gate.py", "--only",
              "ast_parse_coverage,dead_control,ledger_freshness"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads=("every .py under src/tools/scripts/tests (AST), plus the PF16 ledger's "
               "own evidence and its freshness check"),
        control_test="tests/test_evidence_gate.py",
        control=None,
        uncontrolled_because=None,
        requires=("state/evidence_gate_baseline.json", "NEURAL_MESH_WIRE_OR_CUT.json"),
    ),
    Gate(
        name="metthew-catalogue",
        # Registered 2026-11-01 with the checker it names. Before this the
        # catalogue carried `"status": "pending"` and nothing ran it: the gate
        # existed only as a promise inside the file it was supposed to judge.
        # It also walks each family's reference shard through metthew_validate
        # (V15 included), so a shard whose files drifted from its hashes is
        # refused here as well as in the shard gate.
        argv=("tools/metthew_families.py", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads=("docs/METTHEW/highend_families.json (F1..F7) plus every manifest a "
               "family lists in reference_shards[]"),
        control_test="tests/test_metthew_families.py",
        control=None,
        uncontrolled_because=(
            "the controls ship inside the gate (`--selftest` applies ten mutations to the "
            "real catalogue, requires each named refusal, and requires two healthy "
            "claims to be ACCEPTED), and preflight has no subcommand that runs them; "
            "tests/test_metthew_families.py drives both directions"
        ),
        requires=("docs/METTHEW/highend_families.json",),
    ),
    Gate(
        name="at-risk-register",
        # Registered 2026-11-01. docs/METTHEW/AT_RISK_SYSTEMS.json is the guardian's
        # register of game systems under threat; without a checker it was an essay.
        # E4 is the load-bearing one: every dying entry must carry BOTH a next act and
        # a gate, because "raise awareness" is a wish with no number in it. E8 keeps
        # the register honest about its own edge -- the cohorts it has NOT counted are
        # named, each with a next sweep, so "all the systems threatened with
        # extinction" stays a sample with a stated boundary instead of a claim.
        argv=("tools/at_risk_register.py", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=True,
        blocking=True,
        slow=False,
        reads=("docs/METTHEW/AT_RISK_SYSTEMS.json (E1..E8), plus the register at HEAD "
               "for E6's append-only comparison"),
        control_test="tests/test_at_risk_register.py",
        control=None,
        uncontrolled_because=(
            "the controls ship inside the gate (`--selftest` mutates the real register "
            "fifteen ways and requires each named refusal, then requires a tombstoned "
            "retirement to be ACCEPTED), and preflight has no subcommand that runs "
            "them; tests/test_at_risk_register.py drives both directions"
        ),
        requires=("docs/METTHEW/AT_RISK_SYSTEMS.json",),
    ),
    Gate(
        name="task-shape",
        # Registered 2026-11-01 with the executor change. docs/METTHEW/tasks.json is the
        # queue a model works through, and a task whose finish line is an adjective is
        # satisfied by any output that looks like an attempt. T2 requires a command and
        # an outcome, T3 a mutation that must be refused, T5 refuses acceptance language.
        # Legacy tasks are COUNTED not failed (13% shaped on the day it landed): a gate
        # that retroactively fails finished work teaches routing around it.
        #
        # inside_hook=False, deliberately: it runs through this file, which the hook runs
        # as Check 9, and hook_parity compares only gates the hook names directly. Marking
        # it True would claim a stanza it does not have.
        argv=("tools/task_shape_check.py", "--check"),
        interpreter="python3",
        env=(),
        inside_hook=False,
        blocking=True,
        slow=False,
        reads=("docs/METTHEW/tasks.json: every task carrying shape=defensive-v1, plus "
               "execution_contract.known_failure_modes"),
        control_test="tests/test_task_shape_check.py",
        control=None,
        uncontrolled_because=(
            "the controls ship inside the gate (`--selftest` mutates the real task file "
            "eleven ways and requires each named refusal, then requires a correctly "
            "shaped task to be ACCEPTED), and preflight has no subcommand that runs "
            "them; tests/test_task_shape_check.py drives both directions"
        ),
        requires=("docs/METTHEW/tasks.json",),
    ),
    Gate(
        name="flaming-sword-defender",
        # The defender prompter in Project_FLAMING_SWORD frames a change before it is
        # made (what survives, what must still pass, the mutation that must be refused,
        # and the posture for preservation work). Its selftest is the gate: 12 controls,
        # both directions, including that a DRAFT is refused by the strict checker.
        # inside_hook=False for the same reason as task-shape: it runs via this file.
        argv=("Project_FLAMING_SWORD/tools/defender.py", "--selftest"),
        interpreter="python3",
        env=(),
        inside_hook=False,
        blocking=True,
        slow=False,
        reads=("nothing on disk: the selftest builds frames in memory and mutates them"),
        control_test="Project_FLAMING_SWORD/tests/test_defender.py",
        control=None,
        uncontrolled_because=(
            "the gate IS the control run (`--selftest`), so a separate artifact would "
            "duplicate it; tests/test_defender.py additionally drives the CLI's two "
            "exit codes"
        ),
        requires=("Project_FLAMING_SWORD/tools/defender.py",),
    ),
    Gate(
        name="flaming-sword-sentinel",
        # The defender writes the frame; this refuses to let a frame be ACTED ON when
        # its claims do not resolve. Every assertion stats a path or checks a closed
        # vocabulary -- S3 is the one that earns its keep, because "don't break the
        # tests" is unfalsifiable while "tests/test_defender.py must pass" is a file.
        argv=("Project_FLAMING_SWORD/tools/sentinel.py", "--selftest"),
        interpreter="python3",
        env=(),
        inside_hook=False,
        blocking=True,
        slow=False,
        reads=("the working tree: every must_not_break path is stat'ed, and the target "
               "must exist or be declared new:"),
        control_test="Project_FLAMING_SWORD/tests/test_sentinel_memory.py",
        control=None,
        uncontrolled_because=(
            "the gate IS the control run (`--selftest`, 12 controls), so a second "
            "artifact would duplicate it; the pytest file adds the ghost-target and "
            "placeholder-defence cases directly"
        ),
        requires=("Project_FLAMING_SWORD/tools/sentinel.py",),
    ),
    Gate(
        name="flaming-sword-memory",
        # The prompter's memory bridge. Its gate exists for one distinction: an absent
        # backend must report UNMEMORED with a reason, never "no lessons found" -- the
        # estate's most expensive recurring defect is a recall that returns nothing
        # because the store was unreachable reading as evidence nothing was learned.
        argv=("Project_FLAMING_SWORD/tools/memory_bridge.py", "--selftest"),
        interpreter="python3",
        env=(),
        inside_hook=False,
        blocking=True,
        slow=False,
        reads=("a temp directory: the selftest builds ledgers, malformed lines and "
               "failing commands, then removes them"),
        control_test="Project_FLAMING_SWORD/tests/test_sentinel_memory.py",
        control=None,
        uncontrolled_because=(
            "the gate IS the control run (`--selftest`, 9 controls covering missing "
            "ledger, no backend, exit 3, unparseable stdout, malformed line and append)"
        ),
        requires=("Project_FLAMING_SWORD/tools/memory_bridge.py",),
    ),
    Gate(
        name="cradle-demo",
        # PROJECT CRADLE is the composition -- sword + defender + sentinel + memory behind
        # one command -- and this gate runs its demo, which is the only end-to-end proof
        # in the tree: a real frame, a real verification that FAILS on purpose, the lesson
        # that failure writes, the recall of that lesson, and a ghost target BLOCKED with
        # no prompt rendered. It needs no model, no network and no arguments, so it can
        # run on every commit.
        #
        # The HTTP surface is covered by Project_CRADLE's tests rather than here: a gate
        # that binds a port on every commit is a gate that collides with itself.
        argv=("Project_CRADLE/cradle.py", "--demo"),
        interpreter="python3",
        env=(),
        inside_hook=False,
        blocking=True,
        slow=False,
        reads=("a temp directory only; it writes frames, verifications and lessons there "
               "and deletes them"),
        control_test="Project_CRADLE/tests/test_cradle.py",
        control=None,
        uncontrolled_because=(
            "the demo IS the control run: it requires a deliberate FAILED verification "
            "to produce a lesson and a ghost target to be BLOCKED, and it exits 1 if "
            "either stops happening; tests/test_cradle.py adds the broken-installation "
            "case (a missing part must make --doctor exit 1)"
        ),
        requires=("Project_CRADLE/cradle.py",),
    ),
)


# --------------------------------------------------------------------------- #
# Interpreter resolution and probing                                            #
# --------------------------------------------------------------------------- #


def resolve_interpreter(name: str) -> Optional[str]:
    """Resolve an interpreter NAME to an executable path, or None if absent.

    ``python3`` and ``py315t`` mean the PINNED production interpreter that
    `.githooks/pre-commit` already uses ($PYTHON3). Resolving the bare name was
    measured wrong on this host: ``python3`` is WindowsApps 3.13.14, which
    cannot import ``mcp``, so the supervisor gate failed in 0.16s with an
    ImportError while the same command passed under 3.15t. A name is not an
    interpreter; prefer the venv the hook pins, and fall back to PATH only when
    that venv is absent on this host.
    """
    if name in ("python3", "py315t"):
        for candidate in (
            REPO.parent / ".venvs" / "py315t" / "Scripts" / "python.exe",
            REPO.parent / ".venvs" / "py315t" / "bin" / "python",
            REPO / ".venvs" / "py315t" / "Scripts" / "python.exe",
            REPO / ".venvs" / "py315t" / "bin" / "python",
        ):
            if candidate.exists():
                return str(candidate)
        return shutil.which(name)
    if name == "pmb-venv":
        override = os.environ.get("PMB_VENV_PYTHON")
        if override and Path(override).exists():
            return override
        home = Path.home()
        for candidate in (
            home / ".pmb-venv" / "Scripts" / "python.exe",
            home / ".pmb-venv" / "bin" / "python",
        ):
            if candidate.exists():
                return str(candidate)
        return None
    return shutil.which(name)


def probe_interpreter(path: str) -> Tuple[bool, str]:
    """Prove an interpreter RUNS, rather than that a name resolves.

    `shutil.which` answers "is there a file called python3 on PATH?". On this
    host that file is Python 3.13.14 while `python` is 3.14.7, so the hook's
    check 1 and checks 1b-5 run on different interpreters. Resolving a NAME is
    therefore not evidence that the gate runs -- so probe it.
    """
    script = "import json,sys;print(json.dumps({'v':sys.version.split()[0],'exe':sys.executable}))"
    try:
        proc = subprocess.run(
            [path, "-c", script],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)
    if proc.returncode != 0:
        return False, "exit=%d %s" % (proc.returncode, (proc.stderr or "").strip()[:200])
    return True, (proc.stdout or "").strip()


# --------------------------------------------------------------------------- #
# Hook parity -- the registry must describe the gate that actually runs        #
# --------------------------------------------------------------------------- #


#: A line that executes a Python interpreter. Case-insensitive because the hook
#: uses a pinned interpreter variable everywhere: a literal lower-case "python"
#: test is how `scripts/commit_gate.py` came to be reported as
#: registered-but-not-run while the hook runs it on line 44.
#:
#: `$PY` is matched as well as `$PYTHON3`: the hook aliases `PY="$PYTHON3"` and
#: invokes every gate as `"$PY" tools/...`, so a detector that only knew the old
#: name reported all six gates as not-run and failed a correct tree. `$PYTHONPATH`
#: must NOT match, hence the trailing word boundary on the unbraced form.
_RUNS_PYTHON_RE = re.compile(
    r"(?i)(?:\bpython3?\b|PMB_PY|\$\{(?:PY|PYTHON3?)\}|\$(?:PY|PYTHON3?)\b)"
)


def hook_gate_scripts(hook_text: str) -> List[str]:
    """Re-derive the gate scripts the hook RUNS (not the ones it mentions).

    A gate is counted only when the SAME line both names an interpreter and
    names a scripts path, so a variable assignment cannot credit a gate.

    Comment lines are skipped on purpose: the hook's own header mentions
    `tools/install_git_hooks.py` in prose, and counting a mention as a gate would
    make this control fail on a correct tree. ``echo`` lines are skipped for the
    same reason one step removed from prose: the supervisor stanza prints
    ``then: python tools/supervisor_doctor.py --fix``, and a remediation hint is
    not an execution. Counting it reported the doctor as ran-but-unregistered.
    """
    found: List[str] = []
    for raw in hook_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("echo "):
            continue
        if not _RUNS_PYTHON_RE.search(line):
            continue
        for match in _SCRIPT_RE.findall(line):
            if match not in found:
                found.append(match)
    return found


def hook_parity(gates: Sequence[Gate], hook_text: Optional[str]) -> Tuple[bool, str]:
    """Return (ok, message). Absence of the hook is MISSING EVIDENCE, not a pass."""
    if hook_text is None:
        return False, ("MISSING_EVIDENCE: %s not readable -- cannot verify the registry"
                       % " + ".join(str(p.relative_to(REPO)) for p in hook_tier_files()))
    ran = set(hook_gate_scripts(hook_text))
    # The runner cannot register itself: --check would then execute --check on
    # itself, one level deeper per commit. It is the backstop, not a gate.
    ran.discard("tools/preflight_all.py")
    registered = {g.argv[0] for g in gates if g.inside_hook}
    only_hook = sorted(ran - registered)
    only_registry = sorted(registered - ran)
    if only_hook or only_registry:
        return False, "registry drift: ran-but-unregistered=%s registered-but-not-run=%s" % (
            only_hook,
            only_registry,
        )
    # Two rows can share one script: the canon and its sibling corpora are all
    # judged by tools/bible_check.py with different --corpus arguments. The
    # distinct-script set is what the hook text can prove; the row count is what
    # the registry declares, and a reader comparing the message to the table is
    # counting rows. Report both rather than letting one silently stand for the
    # other -- the earlier message said "21 gates" beside a table of 23.
    rows = sum(1 for gate in gates if gate.inside_hook)
    return True, "registry matches the hook (%d gates, %d distinct scripts)" % (
        rows, len(registered))


# --------------------------------------------------------------------------- #
# Evidence: a cached PASS must still describe the tree it was recorded against  #
# --------------------------------------------------------------------------- #


def repo_state_hash() -> Optional[str]:
    """Hash the staged + worktree state that the commit-time gates judge.

    This is the template's "tree hash", in the terms these gates actually use:
    they read the git INDEX and the worktree (staged scanner, index-drift guard,
    ratchet, mesh ledger), so a cached PASS goes stale when either moves.

    Returns None when git cannot answer, and None means NO cached pass is
    reused: reusing evidence that cannot be shown to still describe the tree is
    the exact defect this file exists to prevent.

    HEAD IS PART OF THE HASH. The two diffs alone describe the CHANGE, not the
    tree it lands on: a run recorded on a clean worktree hashes two empty
    diffs, and so does the same clean worktree one commit later - identical
    fingerprints across a changed tree, so the later run would reuse a PASS
    recorded against the earlier commit. Measured 2026-10-31: nothing hashed
    HEAD, so that collision was reachable. The commit hook still forced real
    execution during those commits (the staged diff was non-empty), but a
    `--check` afterwards reported CACHED for a tree no gate had judged.

    NOT hashed: untracked files. `git diff` never sees them, so a cached PASS
    survives a new untracked file. Named here rather than left implicit;
    hashing their names would invalidate every cache whenever any tool drops a
    stray file, which is a different failure.
    """
    digest = hashlib.sha256()
    for args in (("rev-parse", "HEAD"), ("diff", "--cached", "--binary"),
                 ("diff", "--binary")):
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(REPO), capture_output=True, timeout=GIT_TIMEOUT
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print("[preflight] git %s could not run (%s) -- no cached pass will be reused"
                  % (" ".join(args), type(exc).__name__))
            return None
        if proc.returncode != 0:
            print("[preflight] git %s exited %d -- no cached pass will be reused"
                  % (" ".join(args), proc.returncode))
            return None
        digest.update(proc.stdout)
        digest.update(b"\0")
    return digest.hexdigest()


def gate_fingerprint(gate: Gate, interpreter_path: str, state: Optional[str]) -> str:
    """Hash the things that make a recorded result still true.

    The gate's own bytes (a gate edited after its last green run must re-run),
    its argv, the interpreter that ran it, and the repo state it judged.
    """
    digest = hashlib.sha256()
    script = REPO / gate.argv[0]
    if script.is_file():
        digest.update(script.read_bytes())
    digest.update(b"\0".join(a.encode("utf-8") for a in gate.argv))
    digest.update(interpreter_path.encode("utf-8"))
    digest.update(b"\0".join(e.encode("utf-8") for e in gate.env))
    digest.update((state or "no-git-state").encode("utf-8"))
    return digest.hexdigest()


def load_evidence() -> Dict[str, dict]:
    try:
        text = EVIDENCE.read_text(encoding="utf-8")
    except FileNotFoundError:
        # No evidence file is a NORMAL first run, not a failure -- but it is
        # said out loud, because "no evidence" must never be mistaken for a
        # recorded pass.
        print("[preflight] no evidence recorded yet -- every gate will be run")
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        print("[preflight] evidence unreadable (%s) -- treating as absent" % exc)
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print("[preflight] evidence corrupt (%s) -- treating as absent" % exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_evidence(evidence: Dict[str, dict]) -> None:
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Running gates                                                                 #
# --------------------------------------------------------------------------- #


#: Rows whose changeset defaults to the git INDEX. At push time that index holds
#: whatever a concurrent lane has staged, so an index-scoped run can refuse a
#: push over files the commit never touched (measured 2026-11-03: a 2-file
#: commit blocked by 7 forensic files staged by another lane). Under --base they
#: judge BASE..HEAD instead.
#:
#: The base travels in ARGV, not in the environment, because gate_fingerprint()
#: hashes argv: a recorded PASS from index mode must never be reused to answer a
#: commit-mode question, nor the reverse. An env var would let one scope inherit
#: the other's evidence silently -- the stale-evidence defect this registry
#: exists to prevent.
CHANGESET_GATES: Tuple[str, ...] = ("changeset-focus", "staged-scanner")


def with_changeset_base(gate: Gate, base: Optional[str]) -> Gate:
    """`gate`, pointed at BASE..HEAD when a commit is named and the row reads one.

    Every other row is returned untouched, so --base cannot narrow the run to
    two gates: the tree-scoped checks still judge the tree.
    """
    if not base or gate.name not in CHANGESET_GATES:
        return gate
    return gate._replace(argv=gate.argv + ("--base", base))


class Result(NamedTuple):
    gate: str
    outcome: str  # PASS | FAIL | UNRUNNABLE | CACHED
    exit_code: Optional[int]
    seconds: float
    detail: str


def run_gate(gate: Gate, interpreter_path: str, timeout: float = RUN_TIMEOUT) -> Result:
    started = time.monotonic()
    env = dict(os.environ)
    for entry in gate.env:
        key, _, value = entry.partition("=")
        env[key] = value + (os.pathsep + env.get(key, "")) if env.get(key) else value
    cmd = [interpreter_path, str(REPO / gate.argv[0]), *gate.argv[1:]]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return Result(gate.name, "FAIL", None, time.monotonic() - started,
                      "TIMEOUT after %.0fs -- a run that never completed is not a pass" % timeout)
    except OSError as exc:
        return Result(gate.name, "FAIL", None, time.monotonic() - started,
                      "could not spawn %s: %s" % (interpreter_path, exc))
    seconds = time.monotonic() - started
    tail = "\n".join((proc.stdout or "").splitlines()[-8:])
    outcome = "PASS" if proc.returncode == 0 else "FAIL"
    return Result(gate.name, outcome, proc.returncode, seconds, tail)


# --------------------------------------------------------------------------- #
# The executable control: drive the ratchet into a sandbox and make it fail     #
# --------------------------------------------------------------------------- #


def control_anti_pattern_ratchet(interpreter_path: str) -> Tuple[bool, str]:
    """A real negative control for the repo-wide ratchet.

    Fabricate, entirely inside a temp directory: a clean file, a baseline
    recorded against it, then one file carrying an anti-pattern. `--check` must
    exit non-zero. `--baseline-file` is passed EXPLICITLY because a narrowed
    `--dir` redirects the default baseline on purpose (measured: same shape as
    `aggregate_suite_gate`'s `.partial` redirect), and silently comparing against
    the repo baseline would make this control test nothing.
    """
    clean = "def ok():\n    return 1\n"
    # Assembled from parts so this file's own AST carries no anti-pattern
    # literal; the staged scanner parses statements, not string constants, but
    # an explicit construction also keeps the intent readable.
    dirty = "def bad():\n    try:\n        return 1\n    " + "except" + ":\n        " + "pass" + "\n"
    with tempfile.TemporaryDirectory(prefix="az744_ratchet_") as tmp:
        root = Path(tmp)
        (root / "clean.py").write_text(clean, encoding="utf-8")
        baseline = root / "baseline.json"
        record = run_control(
            [interpreter_path, str(REPO / "tools/anti_pattern_gate.py"),
             "--baseline", "--dir", str(root), "--baseline-file", str(baseline)],
            env=("PYTHONPATH=src",),
        )
        if record.exit_code != 0:
            return False, "control setup failed: --baseline exited %s (%s)" % (
                record.exit_code, record.detail)
        (root / "dirty.py").write_text(dirty, encoding="utf-8")
        check = run_control(
            [interpreter_path, str(REPO / "tools/anti_pattern_gate.py"),
             "--check", "--dir", str(root), "--baseline-file", str(baseline)],
            env=("PYTHONPATH=src",),
        )
        if check.exit_code == 0:
            return False, ("control did NOT fail: ratchet exited 0 on an added "
                           "anti-pattern -- the gate cannot fail on its own subject")
        return True, "ratchet exits %s on an added anti-pattern" % check.exit_code


def run_control(cmd: Sequence[str], env: Tuple[str, ...], timeout: float = RUN_TIMEOUT) -> Result:
    child_env = dict(os.environ)
    for entry in env:
        key, _, value = entry.partition("=")
        child_env[key] = value
    try:
        proc = subprocess.run(
            list(cmd), cwd=str(REPO), env=child_env, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return Result("control", "FAIL", None, timeout, "TIMEOUT")
    except OSError as exc:
        return Result("control", "FAIL", None, 0.0, str(exc))
    tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-4:])
    return Result("control", "PASS" if proc.returncode == 0 else "FAIL",
                  proc.returncode, 0.0, tail)


def control_agent_orders(interpreter_path: str) -> Tuple[bool, str]:
    """A real negative control for the standing-order drift gate.

    `tools/agent_orders_sync.py` resolves its targets from module globals, so a
    sandbox has to be installed rather than passed on the command line. The
    control therefore runs that module under the SAME interpreter the hook uses
    and drives the real `check()` twice, in a temp directory: once on a target
    whose block is stale (must return 1) and once after `apply=True` repaired it
    (must return 0). Both matter -- a gate that always fails is as useless as one
    that never does, and `--apply` moving a block instead of rewriting it in
    place would look healthy on the first leg alone.

    Why this is not the same as the pytest file: the artifact proves the logic,
    this proves the CLI-shaped entry point under the hook's interpreter. The
    registry refuses a `control=` name with no implementation, which is how this
    function came to exist.
    """
    script = """
import pathlib, sys, tempfile
sys.path.insert(0, %r)
import agent_orders_sync as orders

tmp = pathlib.Path(tempfile.mkdtemp(prefix="az_orders_ctl_"))
src = tmp / "orders"
src.mkdir()
(src / "00_session_order.md").write_text("## ORDER\\n\\nbody\\n", encoding="utf-8")
target = tmp / "lane.md"
target.write_text(
    "intro\\n<!-- AO:session-order START -->\\nstale\\n<!-- AO:session-order END -->\\ntail\\n",
    encoding="utf-8")
orders.ORDERS = src
orders.TARGETS = {"session-order": ("00_session_order.md", [target])}

stale = orders.check()
if stale != 1:
    sys.exit("control did not fail: check() returned %%r on a stale block" %% (stale,))
if orders.check(apply=True) != 0:
    sys.exit("apply did not repair the stale block")
if orders.check() != 0:
    sys.exit("repaired tree still reports drift")
text = target.read_text(encoding="utf-8")
if not text.startswith("intro") or "stale" in text:
    sys.exit("apply moved the block or left the old body: %%r" %% (text[:60],))
sys.exit(0)
""" % (str(REPO / "tools"),)
    record = run_control([interpreter_path, "-X", "utf8", "-c", script], env=())
    if record.exit_code == 0:
        return True, "check() returns 1 on a stale block, 0 after apply, position kept"
    return False, "control failed (exit %s): %s" % (record.exit_code, record.detail)


def control_platform_claims(interpreter_path: str) -> Tuple[bool, str]:
    """Drive the claims CLI both ways: a true claim passes, a false one goes STALE.

    The gate's whole job is to make a page's claims falsifiable, so the control
    asserts exit 1 on a ledger that contradicts the tree, not merely that the
    healthy path exits 0. `--no-page-coverage` is passed because a two-claim
    fixture cannot cite every page; coverage is asserted by the real run.
    """
    import json as _json
    import tempfile as _tempfile

    claims = REPO / "tools" / "platform_claims.py"
    if not claims.is_file():
        return False, "tools/platform_claims.py is absent, so there is nothing to control"
    with _tempfile.TemporaryDirectory() as tmp:
        good = Path(tmp) / "healthy.json"
        bad = Path(tmp) / "stale.json"
        doc = "docs/platforms/windows.md"
        if not (REPO / doc).is_file():
            return False, "no docs/platforms/windows.md to cite in the fixture"
        good.write_text(_json.dumps({
            "schema": "platform-claims/1",
            "claims": [{"id": "h", "doc": doc, "claim": "gate exists", "kind": "path",
                        "scope": "repo", "path": "tools/platform_claims.py"}],
        }), encoding="utf-8")
        bad.write_text(_json.dumps({
            "schema": "platform-claims/1",
            "claims": [{"id": "s", "doc": doc, "claim": "gate is absent", "kind": "absent",
                        "scope": "repo", "path": "tools/platform_claims.py"}],
        }), encoding="utf-8")
        base = [interpreter_path, "-X", "utf8", str(claims), "--check", "--no-page-coverage", "--ledger"]
        healthy = run_control(base + [str(good)], env=())
        if healthy.exit_code != 0:
            return False, "a true claim was rejected (exit %s): %s" % (healthy.exit_code, healthy.detail)
        stale = run_control(base + [str(bad)], env=())
        if stale.exit_code == 0:
            return False, "a claim that contradicts the tree passed -- the oracle cannot fail"
    return True, "true claim exits 0, contradicting claim exits 1"


def control_prompt_check(interpreter_path: str) -> Tuple[bool, str]:
    """Drive `tools/prompt_check.py` on fixtures whose verdicts are known.

    The gate judges whatever instruction files are on the host, which is exactly
    what a control must NOT depend on: on a host with clean prompts it would pass
    while being unable to fail. So both directions are built in a temp directory
    and the CLI is run against each:

      stale   -- START-HERE present, a count 16% below the recorded reading and no
                 date: must exit 1. This is the real 2026-11-02 defect, verbatim.
      healthy -- the same file with the recorded reading: must exit 0. A gate that
                 fails everything is as useless as one that never fails.
      clean   -- the built-in controls (P1-P4 both directions) must exit 0.

    --no-selftest on the first two so a broken control does not masquerade as a
    stale-prompt finding and send the reader to the wrong file.
    """
    import json as _json

    script = REPO / "tools" / "prompt_check.py"
    if not script.is_file():
        return False, "tools/prompt_check.py is absent"
    state = REPO / "state" / "head.json"
    if not state.is_file():
        return False, "state/head.json is absent, so no count can be compared (BLIND)"
    try:
        measured = _json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, "state/head.json unreadable: %s" % exc
    tests = measured.get("collected_tests")
    if not isinstance(tests, int) or not tests:
        return False, "state/head.json carries no collected_tests reading"

    head = "<!-- AO:start-here START -->\n\n"
    with tempfile.TemporaryDirectory() as tmp:
        stale = Path(tmp) / "stale.md"
        healthy = Path(tmp) / "healthy.md"
        stale.write_text(head + "5,380 tests exist and nothing gates them.\n", encoding="utf-8")
        healthy.write_text(head + "%s tests exist.\n" % format(tests, ","), encoding="utf-8")
        base = [interpreter_path, "-X", "utf8", str(script), "--no-selftest"]
        bad = run_control(base + ["--prompt", str(stale)], env=())
        if bad.exit_code != 1:
            return False, ("a stale count exited %s, not 1 -- the gate cannot fail on the "
                           "defect it was built for: %s" % (bad.exit_code, bad.detail))
        good = run_control(base + ["--prompt", str(healthy)], env=())
        if good.exit_code != 0:
            return False, ("a prompt carrying the recorded reading exited %s: %s"
                           % (good.exit_code, good.detail))
        built_in = run_control([interpreter_path, "-X", "utf8", str(script), "--selftest"], env=())
        if built_in.exit_code != 0:
            return False, "the gate's own controls failed: %s" % built_in.detail
    return True, "stale exits 1, recorded reading exits 0, built-in controls pass"


def _unmeasurable_path() -> str:
    """A path the floor gate CANNOT measure, discovered rather than assumed.

    A nonexistent DIRECTORY on a live volume is still measurable (measured
    2026-11-02: `--path C:/.../no-such-dir-xyz` reported C:'s free space), so the
    fixture has to name a volume that is not mounted: on Windows the first letter
    with no root, on POSIX a path under a directory that cannot be a mount.
    """
    if sys.platform == "win32":
        for letter in "QRSTUVWXYZ":
            if not os.path.exists(letter + ":/"):
                return letter + ":/no-such-volume"
        return r"\\.\no-such-volume"
    return "/nonexistent-mount-%d/x" % os.getpid()


def control_disk_floor(interpreter_path: str) -> Tuple[bool, str]:
    """Drive the floor into all three states it must distinguish.

    One direction is not a control. A gate that fails everywhere would "pass" a
    bare must-fail test, so this also demands that the SAME gate stays green on a
    volume it should accept, and that an unmeasurable volume is neither the pass
    nor the below-floor code -- exit 2 exists precisely because "could not read"
    must not be squeezed into either answer.
    """
    script = REPO / "tools" / "disk_floor_gate.py"
    base = [interpreter_path, "-X", "utf8", str(script)]
    try:
        free_gb = shutil.disk_usage(str(REPO)).free / (1024.0 ** 3)
    except OSError as exc:
        return False, "control cannot read its own fixture's free space: %s" % exc

    healthy = run_control(base + ["--threshold-gb", "0.001"], env=())
    if healthy.exit_code != 0:
        return False, ("a volume with 0.0 GB required exited %s -- the gate fires on a "
                       "healthy fixture: %s" % (healthy.exit_code, healthy.detail))

    floor = "%.3f" % (free_gb * 4 + 1024)
    low = run_control(base, env=("AZ_DISK_FLOOR_GB=" + floor,))
    if low.exit_code != 1:
        return False, ("a %.0f GB floor against %.1f GB free exited %s, not 1: %s"
                       % (float(floor), free_gb, low.exit_code, low.detail))

    absent = run_control(base + ["--path", _unmeasurable_path()], env=())
    if absent.exit_code != 2:
        return False, ("an unreadable path exited %s, not 2 (unmeasurable must not read as "
                       "a pass or as below-floor): %s" % (absent.exit_code, absent.detail))
    return True, ("healthy exits 0, %.0f GB floor against %.1f GB free exits 1, "
                  "unreadable path exits 2" % (float(floor), free_gb))


CONTROLS = {
    "disk-floor": control_disk_floor,
    "anti-pattern-ratchet": control_anti_pattern_ratchet,
    "agent-orders-negative-control": control_agent_orders,
    "platform-claims-self-test": control_platform_claims,
    "prompt-check-controls": control_prompt_check,
}


# --------------------------------------------------------------------------- #
# Commands                                                                      #
# --------------------------------------------------------------------------- #


def cmd_list(gates: Sequence[Gate], as_json: bool) -> int:
    if as_json:
        print(json.dumps(
            [{"name": g.name, "argv": list(g.argv), "interpreter": g.interpreter,
              "env": list(g.env), "inside_hook": g.inside_hook, "blocking": g.blocking,
              "slow": g.slow, "reads": g.reads, "requires": list(g.requires),
              "control_test": g.control_test, "control": g.control,
              "uncontrolled_because": g.uncontrolled_because}
             for g in gates], indent=2))
        return 0
    print("registered gates (source of truth for what --check runs):\n")
    for g in gates:
        where = "hook" if g.inside_hook else "CI"
        kind = "blocking" if g.blocking else "NON-blocking"
        print("  %-26s %-4s %-12s %-9s %s %s" % (
            g.name, where, kind, g.interpreter, g.argv[0], " ".join(g.argv[1:])))
        print("      reads:   %s" % g.reads)
        if g.requires:
            print("      needs:   %s (absent = BLIND, cannot judge)" % ", ".join(g.requires))
        if g.control:
            print("      control: executable (%s)" % g.control)
        elif g.control_test:
            print("      control: artifact %s" % g.control_test)
        else:
            print("      control: NONE -- %s" % g.uncontrolled_because)
    print("\n%d gates: %d hook, %d CI-only"
          % (len(gates), sum(1 for g in gates if g.inside_hook),
             sum(1 for g in gates if not g.inside_hook)))
    return 0


def cmd_check(gates: Sequence[Gate], force: bool, include_slow: bool,
              allow_unrunnable: Sequence[str], base: Optional[str] = None) -> int:
    ok, message = hook_parity(gates, read_hook())
    print("[preflight] hook parity: %s" % message)
    if not ok:
        return 1

    # An installed push hook that lost its merge gate is a regression, and one
    # thing no other gate can see: nothing runs at push time until a push.
    push_state, push_message = push_hook_status(read_push_hook())
    print("[preflight] push hook: %s -- %s" % (push_state, push_message))
    if push_state in ("STALE", "UNREADABLE"):
        return 1

    # Which changeset the two index-scoped rows judge. Printed either way: "the
    # index" and "a commit" are different questions, and a run that does not say
    # which one it answered cannot be cited for either.
    if base:
        print("[preflight] changeset scope: %s..HEAD (%s)"
              % (base, ", ".join(CHANGESET_GATES)))
    else:
        print("[preflight] changeset scope: the git index -- no commit named "
              "(--base REV to judge a commit)")

    evidence = load_evidence()
    state = repo_state_hash()
    results: List[Result] = []
    for gate in gates:
        if gate.slow and not include_slow:
            continue
        gate = with_changeset_base(gate, base)
        interpreter_path = resolve_interpreter(gate.interpreter)
        if interpreter_path is None:
            if gate.name in allow_unrunnable:
                print("[preflight] %-26s DECLARED UNRUNNABLE (interpreter %r absent)"
                      % (gate.name, gate.interpreter))
                continue
            results.append(Result(gate.name, "UNRUNNABLE", None, 0.0,
                                  "interpreter %r not found -- a gate that cannot run is "
                                  "not a pass" % gate.interpreter))
            continue
        fingerprint = gate_fingerprint(gate, interpreter_path, state)
        cached = evidence.get(gate.name)
        if (not force and state is not None and isinstance(cached, dict)
                and cached.get("fingerprint") == fingerprint
                and cached.get("exit_code") == 0):
            results.append(Result(gate.name, "CACHED", 0, 0.0,
                                  "evidence still describes this tree (--force to re-run)"))
            continue
        result = run_gate(gate, interpreter_path)
        evidence[gate.name] = {
            "fingerprint": fingerprint,
            "exit_code": result.exit_code,
            "seconds": round(result.seconds, 3),
            "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        results.append(result)

    save_evidence(evidence)
    print()
    for result in results:
        mark = {"PASS": "PASS", "CACHED": "PASS", "FAIL": "FAIL", "UNRUNNABLE": "FAIL"}[result.outcome]
        print("[%s] %-26s %6.2fs %s" % (mark, result.gate, result.seconds, result.outcome))
        if result.outcome in ("FAIL", "UNRUNNABLE") and result.detail:
            for line in result.detail.splitlines():
                print("        %s" % line)

    failed = [r for r in results if r.outcome in ("FAIL", "UNRUNNABLE")]
    blocking_failed = [r for r in failed if is_blocking(gates, r.gate)]
    print("\n%d ran, %d failed (%d blocking), %d reused from evidence"
          % (len(results), len(failed), len(blocking_failed),
             sum(1 for r in results if r.outcome == "CACHED")))
    return 1 if blocking_failed else 0


def is_blocking(gates: Sequence[Gate], name: str) -> bool:
    for gate in gates:
        if gate.name == name:
            return gate.blocking
    return True


def read_hook() -> Optional[str]:
    """The gate text of every tier file that is present, or None if none is."""
    parts: List[str] = []
    errors: List[str] = []
    for path in hook_tier_files():
        try:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            # Two statements on purpose: a lone `return None` here is the shape the
            # compliance scanner flags as `except_return_default`, and it IS a real
            # swallow -- the caller cannot tell "unreadable hook" from "empty hook"
            # unless this says so. `hook_parity` turns the None into MISSING_EVIDENCE.
            # A tier file that is ABSENT is not an error: a clone may only carry
            # one tier. It is only an error when NOTHING is readable, because then
            # parity has no hook to be about.
            errors.append("%s: %s" % (path.name, type(exc).__name__))
    if not parts:
        print("[preflight] hook unreadable (%s) -- parity cannot be verified"
              % "; ".join(errors))
        return None
    return "\n".join(parts)


class HookRead(NamedTuple):
    """What reading the push hook produced: its text, or the reason there is none.

    A bare ``Optional[str]`` conflated two states that need different severities:
    ABSENT (no hook on a fresh clone, a warning) and UNREADABLE (a hook that is
    there and cannot be judged). The second is a real defect and was reported as
    the first -- the `except_return_default` shape the staged scanner flags, caught
    here on 2026-11-01 while landing the catalogue gate.
    """

    text: Optional[str]
    error: Optional[str] = None


def read_push_hook() -> HookRead:
    try:
        return HookRead(PUSH_HOOK.read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        # Absence is a normal state (`.githooks/*` is gitignored and the hook is
        # planted by the installer), so this says so rather than pretending the
        # hook exists and is empty. `push_hook_status` turns it into ABSENT.
        return HookRead(None)
    except OSError as exc:
        # Present but unjudgeable: a directory, a permission error, a locked file.
        # It is NOT absence, and calling it absence would hide a broken hook.
        print("[preflight] push hook unreadable (%s: %s) -- parity cannot be verified"
              % (type(exc).__name__, exc))
        return HookRead(None, "%s: %s" % (type(exc).__name__, exc))


def push_hook_status(text) -> Tuple[str, str]:
    """(state, message) for the PUSH-time hook, in that hook's own terms.

    States:
      OK      the hook exists AND runs the merge gate with --pre-push.
      ABSENT  no hook at all -- a clone that has not run the installer. A
              warning with the remedy, never a failure: the clone has no
              core.hooksPath either, so nothing here is half-wired.
      STALE   a hook exists and DOES NOT run the merge gate. This is the real
              defect: it is the state an edit or a stale template leaves behind,
              and it is silent at push time (the push just goes).
      UNREADABLE
              a hook exists and cannot be read at all, so none of the above can
              be established. A failure, not a warning: "cannot tell" is not
              "fine".

    Accepts a `HookRead` (the normal path) or a bare string/None (the old shape,
    still used by callers that hand in fixture text).

    A mention is not an invocation, so comments and `echo` hints are skipped by
    the same rule `hook_gate_scripts` uses for the commit hook.
    """
    if isinstance(text, HookRead):
        if text.error:
            return "UNREADABLE", ("the push hook exists but cannot be read (%s); "
                                  "whether it runs the merge gate is unknown. Fix or "
                                  "remove %s." % (text.error, PUSH_HOOK.name))
        text = text.text
    if text is None:
        return "ABSENT", ("push hook not installed -- plant it with: "
                          "python tools/bible_ship.py --install-hooks . --hooks pre-push")
    runs_gate = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("echo "):
            continue
        if "tools/git_merge_gate.py" in line and "--pre-push" in line:
            runs_gate = True
            break
    if not runs_gate:
        return "STALE", ("the push hook exists but does not run "
                         "tools/git_merge_gate.py --pre-push, so a branch that "
                         "cannot merge is only discovered after the push. "
                         "Re-plant it: python tools/bible_ship.py "
                         "--install-hooks . --hooks pre-push")
    return "OK", "the push hook runs the merge gate (tools/git_merge_gate.py --pre-push)"


def cmd_self_test(gates: Sequence[Gate], run_controls: bool = False,
                  strict: bool = False, allow_blind: Sequence[str] = ()) -> int:
    """Prove fail-ability. A check that passes on nothing has proved nothing.

    ``strict=False`` (the default) reports a BLIND gate -- one whose required
    artifact is absent -- as a counted note rather than a failure, because a
    baseline that was never recorded on this machine is a normal state for the
    CI-only gates and failing on it would make the self-test noise. It is still
    PRINTED, so absence of evidence is never presented as a pass. ``--strict``
    promotes it, which is the form for a gate that is supposed to be runnable.
    """
    failures: List[str] = []
    notes: List[str] = []

    print("== 1. hook parity (the registry must describe the gate that runs) ==")
    parity_ok, parity_message = hook_parity(gates, read_hook())
    print("   %s: %s" % ("OK" if parity_ok else "FAIL", parity_message))
    if not parity_ok:
        failures.append("hook parity: %s" % parity_message)

    # The PUSH hook is a second, separately-generated artefact: the commit hook
    # can be perfectly wired while every push leaves the machine ungated.
    push_state, push_message = push_hook_status(read_push_hook())
    print("   %s: %s" % ({"OK": "OK", "ABSENT": "WARN", "STALE": "FAIL",
                          "UNREADABLE": "FAIL"}[push_state], push_message))
    if push_state == "STALE":
        failures.append("push hook: %s" % push_message)

    print("\n== 2. interpreters actually execute (%d distinct) ==" % len({g.interpreter for g in gates}))
    # A missing interpreter is a failure only for a gate that is supposed to
    # BLOCK. The PMB observer is non-blocking by design ("a memory side-effect
    # must never reject a commit"), so its absent venv is a note, not a red --
    # but it is still printed, never silently dropped.
    blocking_interpreters = {g.interpreter for g in gates if g.blocking}
    seen: Dict[str, Tuple[bool, str]] = {}
    for name in sorted({g.interpreter for g in gates}):
        required = name in blocking_interpreters
        path = resolve_interpreter(name)
        if path is None:
            seen[name] = (False, "not on PATH")
            print("   %s %-10s not found%s" % ("FAIL" if required else "note", name,
                                               "" if required else " (non-blocking gate only)"))
            if required:
                failures.append("interpreter %r does not resolve" % name)
            else:
                notes.append("interpreter %r absent; only non-blocking gates use it" % name)
            continue
        ok, info = probe_interpreter(path)
        seen[name] = (ok, info)
        print("   %s %-10s %s" % ("OK  " if ok else ("FAIL" if required else "note"), name, info))
        if not ok:
            if required:
                failures.append("interpreter %r does not execute: %s" % (name, info))
            else:
                notes.append("interpreter %r does not execute: %s" % (name, info))
    paths = {name: resolve_interpreter(name) for name in seen}
    distinct = {p for p in paths.values() if p}
    if len(distinct) > 1:
        notes.append(
            "the hook runs its gates on %d different interpreters (%s) -- resolved by "
            "NAME; a gate may behave differently depending on which one it lands on"
            % (len(distinct), ", ".join(sorted(str(p) for p in distinct))))

    print("\n== 3. declared control artifacts exist on disk ==")
    for gate in gates:
        if not gate.control_test:
            continue
        if (REPO / gate.control_test).is_file():
            print("   OK   %-26s %s" % (gate.name, gate.control_test))
        else:
            print("   FAIL %-26s %s MISSING" % (gate.name, gate.control_test))
            failures.append(
                "%s declares control artifact %s, which is not in the tree"
                % (gate.name, gate.control_test))

    blind: List[Tuple[Gate, str]] = []
    print("\n== 4. required artifacts: a gate whose input is ABSENT cannot judge ==")
    for gate in gates:
        for rel in gate.requires:
            if not (REPO / rel).exists():
                blind.append((gate, rel))
                break
    if not blind:
        print("   OK   every gate's required artifact is present")
    for gate, rel in blind:
        declared = gate.name in allow_blind
        tag = "FAIL" if (strict and not declared) else ("note" if not declared else "ok  ")
        print("   %s %-26s BLIND -- %s is absent, so it cannot produce a verdict%s"
              % (tag, gate.name, rel, " [declared via --allow-blind]" if declared else ""))
        if strict and not declared:
            failures.append("%s is BLIND: %s is absent, so it cannot judge" % (gate.name, rel))

    print("\n== 5. executable controls must FAIL as required ==")
    for gate in gates:
        if not gate.control:
            continue  # artifact-declared or uncontrolled; reported in section 5
        interpreter_path = resolve_interpreter(gate.interpreter)
        if interpreter_path is None:
            failures.append("%s: interpreter %r absent" % (gate.name, gate.interpreter))
            continue
        control = CONTROLS.get(gate.control)
        if control is None:
            failures.append("%s names control %r with no implementation" % (gate.name, gate.control))
            continue
        ok, message = control(interpreter_path)
        print("   %s %-26s %s" % ("OK  " if ok else "FAIL", gate.name, message))
        if not ok:
            failures.append("%s: %s" % (gate.name, message))

    # Fail-ability is not a boolean. Three honest buckets, because calling an
    # artifact-declared gate "uncontrolled" while printing the artifact it
    # declares is a self-contradiction:
    #   executable  -- demonstrated here, by making the gate fail for real;
    #   artifact    -- a named test is asserted as its control, and that test is
    #                  verified to EXIST, but its fail-ability is not re-run here;
    #   none        -- nothing declared, with the reason.
    executable = [g for g in gates if g.control]
    artifact_only = [g for g in gates if not g.control and g.control_test]
    none_declared = [g for g in gates if not g.control and not g.control_test]
    print("\n== 6. fail-ability by evidence class ==")
    print("   executable control, demonstrated here : %d" % len(executable))
    print("   artifact-declared, existence verified : %d" % len(artifact_only))
    print("   nothing declared                      : %d" % len(none_declared))
    for gate in artifact_only:
        print("     artifact  %-24s %s" % (gate.name, gate.control_test))
    for gate in none_declared:
        print("     NONE      %-24s %s" % (gate.name, gate.uncontrolled_because))

    if run_controls:
        print("\n== 7. control artifacts EXECUTED -- declared fail-ability, now demonstrated ==")
        for gate in gates:
            if not gate.control_test:
                continue
            interpreter_path = resolve_interpreter(gate.interpreter)
            if interpreter_path is None:
                print("   FAIL %-26s interpreter %r absent" % (gate.name, gate.interpreter))
                failures.append("%s: cannot run its control, interpreter %r absent"
                                % (gate.name, gate.interpreter))
                continue
            started = time.monotonic()
            # Controls inherit the production addopts (blocklist + -n auto):
            # a control run serially while production runs parallel does not
            # demonstrate the production configuration. Measured 2026-10-20:
            # single-file controls pass under xdist on 3.15t (~2.6s vs 0.3s
            # serial); the startup cost is the price of a faithful control.
            # A single transient collection failure (e.g. a just-written
            # control file not yet visible to the subprocess, or a momentary
            # interpreter/FS hiccup under xdist load) must not flip a healthy
            # tree red: retry once before declaring the control dead.
            result = None
            for _attempt in (1, 2):
                result = run_control(
                    [interpreter_path, "-m", "pytest", gate.control_test, "-q",
                     "-p", "no:xdist", "-o", "addopts="],
                    env=("PYTHONPATH=src",),
                )
                if result.exit_code == 0:
                    break
                if _attempt == 1 and "FileNotFoundError" in (result.detail or ""):
                    time.sleep(0.25)
                    continue
                break
            seconds = time.monotonic() - started
            ok = result.exit_code == 0
            print("   %s %-26s %5.1fs %s"
                  % ("OK  " if ok else "FAIL", gate.name, seconds, gate.control_test))
            if not ok:
                failures.append("%s: control artifact %s exited %s"
                                % (gate.name, gate.control_test, result.exit_code))
                for line in result.detail.splitlines()[-4:]:
                    print("        %s" % line)

    for note in notes:
        print("\n[note] %s" % note)
    print("\n%d gate(s), %d failure(s), %d blind, %d with no declared control"
          % (len(gates), len(failures), len(blind), len(none_declared)))
    if run_controls:
        print("   (artifact-declared controls were EXECUTED above: their fail-ability")
        print("    is demonstrated for THIS run, and expires with the tree it ran on)")
    else:
        print("   (an artifact-declared control is NOT a demonstrated one -- re-run")
        print("    with --run-controls to execute it, or read the test named beside it)")
    if failures:
        for failure in failures:
            print("  FAIL %s" % failure)
        return 1
    return 0


def main(argv: Optional[Sequence[str]] = None, gates: Sequence[Gate] = GATES) -> int:
    parser = argparse.ArgumentParser(
        prog="preflight_all.py",
        description="Tracked gate registry, runner and self-test.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="print the registry and exit")
    group.add_argument("--check", action="store_true",
                       help="run the registered gates (default)")
    group.add_argument("--self-test", action="store_true",
                       help="prove fail-ability: interpreters, controls, uncontrolled count")
    parser.add_argument("--include-slow", action="store_true",
                        help="also run the CI-only suite gates")
    parser.add_argument("--force", action="store_true",
                        help="re-run instead of reusing evidence that still matches")
    parser.add_argument("--allow-unrunnable", action="append", default=[], metavar="GATE",
                        help="declare a gate unrunnable; repeatable, and it is printed")
    parser.add_argument("--run-controls", action="store_true",
                        help="with --self-test: EXECUTE each declared control artifact")
    parser.add_argument("--strict", action="store_true",
                        help="with --self-test: a BLIND gate is a failure")
    parser.add_argument("--allow-blind", action="append", default=[], metavar="GATE",
                        help="with --strict: declare a gate known to be blind; repeatable")
    parser.add_argument("--json", action="store_true", help="machine-readable --list")
    parser.add_argument("--base", metavar="REV", default=None,
                        help="judge REV..HEAD instead of the git index "
                             "(changeset-focus, staged-scanner)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not (REPO / "tools").is_dir():
        print("[preflight] %s is not a repo root" % REPO, file=sys.stderr)
        return 2
    if args.json and not args.list:
        print("[preflight] --json applies to --list", file=sys.stderr)
        return 2
    if (args.run_controls or args.strict or args.allow_blind) and not args.self_test:
        print("[preflight] --run-controls/--strict/--allow-blind apply to --self-test",
              file=sys.stderr)
        return 2

    if args.base:
        if args.list or args.self_test:
            print("[preflight] --base applies to --check", file=sys.stderr)
            return 2
        probe = subprocess.run(["git", "rev-parse", "--verify", "--quiet",
                                "%s^{commit}" % args.base],
                               cwd=str(REPO), capture_output=True, text=True,
                               timeout=30, encoding="utf-8", errors="replace")
        if probe.returncode != 0:
            # An unresolvable base would hand the gates an empty range, which
            # reads as PASS. Refusing here is the difference between "no
            # changes to worry about" and "the question was never asked".
            print("[preflight] --base %r does not resolve to a commit in this "
                  "clone -- refusing to judge an empty range" % args.base,
                  file=sys.stderr)
            return 2

    if args.list:
        return cmd_list(gates, args.json)
    if args.self_test:
        return cmd_self_test(gates, args.run_controls, args.strict, args.allow_blind)
    return cmd_check(gates, args.force, args.include_slow, args.allow_unrunnable,
                     args.base)


if __name__ == "__main__":
    raise SystemExit(main())
