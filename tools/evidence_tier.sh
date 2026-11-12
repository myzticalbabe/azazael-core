#!/bin/sh
# Evidence tier -- the whole-tree half of the commit gate (split 2026-11-02).
#
# WHY IT LIVES IN tools/ AND NOT .githooks/lib/. It was written into
# `.githooks/lib/evidence.sh` first, and `.gitignore`'s `.githooks/*` (with a
# single negation for `.githooks/pre-commit`) means that path is NOT in the tree:
# the gate ran on this host and would have evaporated on a clone, while the push
# hook that calls it IS generated on every clone by tools/bible_ship.py. A hook
# pointing at an untracked script is a gate that reads installed and refuses
# nothing. `tools/` is tracked, so the file a push hook calls is the file a
# checkout has.
#
# WHO RUNS THIS: the push hook (`.githooks/pre-push`, generated from
# tools/bible_ship.PRE_PUSH, which calls this path), and
# `COMMIT_TIER=full git commit ...` when an author wants the full answer locally.
# Every stanza below was moved VERBATIM out of .githooks/pre-commit, with its own
# comment, its own override and its own refusal text -- nothing was relaxed and
# nothing was deleted. What changed is which file calls it, and pre-commit's
# header states that change where a session reads it.
#
# WHY IT MOVED: a run cost 182.2 s (measured 2026-11-02, one-file staged index,
# exit 0). Each check below re-derives a property of the TREE, so its answer
# cannot change between two commits and does not depend on the commit's paths;
# answering it per commit taxed every attempt and protected nothing that a push
# was not about to protect anyway.
#
# The evidence gates, in order:
#   4.     anti-pattern ratchet          (src/, tracked files, vs baseline)
#   4b.    unresolved cross-language refs (tools/unresolved_ratchet.py)
#   4c.    canon corpus citations        (tools/bible_check.py --check)
#   4c-bis. emotional corpora            (tools/bible_check.py --corpus ...)
#   4c-ter. themed corpora               (tools/bible_theme.py --all --check)
#   4d.    platform page claims          (tools/platform_claims.py --check)
#   5.     mesh wire-or-cut ledger       (tools/mesh_function_preflight.py --check)
#   5b.    span receipt guard selftest   (tools/span_receipt_guard.py --selftest)
#   5c.    ELIZA shard stubs vs atlas     (Project_ELIZA/tools/shard_intake.py --check)
#   6.     wired-declaration evidence    (tools/wire_evidence_audit.py --check)
#   6b.    evidence gate                 (tools/evidence_gate.py)
#   6c.    failure-evidence ratchet      (tools/failure_evidence_gate.py --check)
#   6d.    METTHEW family catalogue      (tools/metthew_families.py --check)
#   6e.    at-risk systems register      (tools/at_risk_register.py --check)
#   7.     paradigm rule 1 (advisory)    (tools/paradigm_gate.py --check)
#   9.     unified gate registry         (tools/preflight_all.py --check)
#   AP-5.  persisted assistant prose     (tools/transcript_ap5_gate.py, advisory)
#
# A PASS writes logs/hook_evidence.json (tree sha + time). pre-commit quotes it as
# the last evidence reading, so "when did this tree last pass" is a timestamp
# rather than a recollection. logs/ is gitignored: a fact about this host's last
# run, not a claim committed into the tree.
#
# CHANGESET SCOPE (--base REV, 2026-11-03). Two stanzas below read a changeset:
# the anti-pattern ratchet's staged additions and the preflight registry's
# `changeset-focus` / `staged-scanner` rows, which are `git diff --cached` and
# therefore judge the INDEX. At push time the index is not the changeset: on a
# worktree several lanes share it holds whatever a concurrent lane has staged.
# Measured 2026-11-03: a 2-file commit was refused by both rows over 7 forensic
# files staged by another lane, none of them in the commit -- an index-scoped run
# cannot distinguish "my change is too big" from "somebody else's desk drawer is".
# `--base REV` makes the registry judge REV..HEAD and says which scope it used.
# Without it the behaviour is unchanged (pre-commit still wants the index).

# One level up: this file is tools/evidence_tier.sh, so dirname/.. is the root.
# (It was `.githooks/lib/evidence.sh` -- two levels -- until the tier moved to a
# tracked path.)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

# 3.15t production interpreter, same ladder as pre-commit (python3 on PATH was
# 3.13.14 from WindowsApps -- a fourth interpreter -- so the name is pinned).
PYTHON3="C:/Azazael/.venvs/py315t/Scripts/python.exe"
PY="$PYTHON3"
if [ ! -x "$PY" ]; then
    for cand in "C:/Azazael/.venvs/py315/Scripts/python.exe" \
                "C:/Azazael/.venvs/py314m/Scripts/python.exe" \
                "$(command -v python3.15t 2>/dev/null)" \
                "$(command -v python3 2>/dev/null)"; do
        [ -n "$cand" ] && [ -x "$cand" ] && { PY="$cand"; break; }
    done
fi

# ── Changeset scope (2026-11-03) ──
#   sh tools/evidence_tier.sh               # judge the git index (pre-commit)
#   sh tools/evidence_tier.sh --base <rev>  # judge <rev>..HEAD (push of a commit)
# The base is validated rather than trusted: an empty value, or a second argument
# this script does not understand, exits 2 -- "could not judge" is not a pass, and
# a typo that silently fell back to index mode would reproduce the hijack.
EV_BASE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --base)
            shift
            [ $# -ge 1 ] || { echo "[Evidence] --base needs a revision"; exit 2; }
            EV_BASE="$1"
            ;;
        --base=*)
            EV_BASE="${1#--base=}"
            ;;
        *)
            echo "[Evidence] unknown argument: $1 (known: --base REV)"
            exit 2
            ;;
    esac
    shift
done
case "$EV_BASE" in
    "") ;;
    *" "*)
        echo "[Evidence] --base must be a single ref, got: $EV_BASE"
        exit 2
        ;;
esac
if [ -n "$EV_BASE" ]; then
    echo "[Evidence] changeset scope: $EV_BASE..HEAD (commit mode -- the index is NOT judged)"
else
    echo "[Evidence] changeset scope: the git index (no --base given)"
fi

# ─── Check 4: repo-wide anti-pattern ratchet ───
# Fails on any category COUNT increase over the recorded baseline. This runs
# unconditionally: the scanner snapshot in check 2 cannot see the running total,
# and the backlog is what is ratcheted.
#
# --tracked-only (added 2026-11-01): the tree-wide scan counts UNTRACKED files,
# so on 2026-11-01 three `??` modules created by another lane (CODEX/
# dit_video_pipeline.py, CORE/feast_feature_store.py, CORE/
# langgraph_integration.py) added 6 import_inside_function and 1
# except_return_default and blocked a PSYCHE commit that contained none of them.
# The gate's own --tracked-only help names this case: "gating a commit on the
# desk drawer blocks reviewed changesets for files no commit contains." Staged
# additions are still counted, because `git ls-files` reads the INDEX, so new
# code is measured the moment it is added -- and the untracked files will be
# counted at the commit that first tracks them, which is the ratchet working as
# designed rather than as noise.
echo "[Ratchet] Comparing anti-pattern counts against baseline (tracked files)..."
if [ -f "$SCRIPT_DIR/tools/anti_pattern_gate.py" ]; then
    PYTHONPATH="src:${PYTHONPATH:-}" "$PY" "$SCRIPT_DIR/tools/anti_pattern_gate.py" --check --tracked-only 2>&1
    RATCHET_EXIT=$?
    if [ $RATCHET_EXIT -ne 0 ]; then
        echo ""
        echo "[Ratchet] Anti-pattern count INCREASED over baseline. Fix the new"
        echo "[Ratchet] finding(s), or -- for a reviewed, deliberate increase --"
        echo "[Ratchet] re-run: python tools/anti_pattern_gate.py --baseline"
        exit 1
    fi
else
    echo "[Ratchet] gate not found -- passing loud (gate unrunnable)"
fi
echo ""

# ─── Check 4b: unresolved cross-language references (ratchet) ───
# The census can only place what it can resolve. On 2026-10-31 that was 110 of
# 142 driver references, and the 32 leftovers lived in a JSON file nobody
# re-reads -- so the next session inherited 32 by default rather than by
# decision, and the 33rd would arrive unremarked. Same ratchet semantics as the
# anti-pattern baseline above: the count may fall, never rise. Shell-only on
# purpose: the js extractor is tree-sitter-or-regex depending on the
# interpreter, and a gate that flips between two pythons is useless.
echo "[Refs] Comparing unresolved cross-language references against baseline..."
if [ -f "$SCRIPT_DIR/tools/unresolved_ratchet.py" ]; then
    UNRES_OUT=$("$PY" "$SCRIPT_DIR/tools/unresolved_ratchet.py" --check 2>&1)
    UNRES_EXIT=$?
    echo "$UNRES_OUT" | tail -12
    if [ $UNRES_EXIT -ne 0 ]; then
        echo ""
        echo "[Refs] A reference the census cannot place was added."
        echo "[Refs] Resolve it, declare it in tools/resolution_aliases.json, or --"
        echo "[Refs] for a reviewed, deliberate increase -- re-run:"
        echo "[Refs]   python tools/unresolved_ratchet.py --baseline"
        exit 1
    fi
else
    echo "[Refs] gate not found -- passing loud (gate unrunnable)"
fi
echo ""

# ─── Check 4c: the canon corpus cites itself honestly ───
# docs/bible/bible.json is prose that makes claims about this repository: every
# citation must resolve, every figure must name the command that produced it, and
# every statute must name both the RULE it comes from and the check that executes
# it. A canon that contradicts itself is worse than no canon, because it is
# quotable -- so the corpus is data and this is the apparatus. Absent corpus is
# "passing loud", the same as a missing gate: the check cannot judge what is not
# there, and pretending otherwise would make an empty file look clean.
echo "[Bible] Checking that the canon corpus resolves its own citations..."
if [ -f "$SCRIPT_DIR/tools/bible_check.py" ]; then
    BIBLE_OUT=$("$PY" "$SCRIPT_DIR/tools/bible_check.py" --check 2>&1)
    BIBLE_EXIT=$?
    echo "$BIBLE_OUT" | tail -20
    if [ $BIBLE_EXIT -ne 0 ]; then
        echo ""
        echo "[Bible] BLOCKED: the corpus contradicts itself (see above)."
        echo "[Bible] Fix the citation, name the command behind the figure, or bind"
        echo "[Bible] the statute: python tools/bible_check.py --check"
        exit 1
    fi
else
    echo "[Bible] gate not found -- passing loud (gate unrunnable)"
fi
echo ""

# ─── Check 4c-bis: the sibling corpora ───
# The canon governs the tree; the two corpora in docs/bible/emotional/ govern what
# a system may say about a person's feeling. They are read as separate corpora, so
# a citation may not cross between them, and each carries its own generated
# edition. A gate that checks only the canon leaves the two newest documents on
# the honour system -- which is how a corpus acquires a claim nobody can refuse.
echo "[Bible] Checking the emotional corpora..."
if [ -f "$SCRIPT_DIR/tools/bible_check.py" ]; then
    for CORPUS in docs/bible/emotional/emotional.json docs/bible/emotional/female.json; do
        if [ -f "$SCRIPT_DIR/$CORPUS" ]; then
            CORPUS_OUT=$("$PY" "$SCRIPT_DIR/tools/bible_check.py" --check --corpus "$SCRIPT_DIR/$CORPUS" 2>&1)
            CORPUS_EXIT=$?
            echo "$CORPUS_OUT" | tail -12
            if [ $CORPUS_EXIT -ne 0 ]; then
                echo ""
                echo "[Bible] BLOCKED: $CORPUS contradicts itself (see above)."
                echo "[Bible] Fix the citation, name the command behind the figure, or"
                echo "[Bible] regenerate its edition: python tools/bible_check.py --check --corpus $CORPUS"
                exit 1
            fi
        fi
    done
else
    echo "[Bible] gate not found -- passing loud (gate unrunnable)"
fi
echo ""

# ─── Check 4c-ter: the themed corpora ───
# docs/bible/themes/ holds two editions that restate the canon in another
# register. They were gated by nothing at all: no stanza here, no registry row,
# only a test nobody's commit runs -- and `bible_check.py --check` cannot see
# them, because they are not books of the corpus it loads. A themed verse must
# cite a verse of the base corpus and its statute must land on a rule canon
# defines; that is precisely the check a derived corpus has to pass, and it was
# the one check the derived corpora were not subject to.
echo "[Bible] Checking the themed corpora..."
if [ -f "$SCRIPT_DIR/tools/bible_theme.py" ]; then
    THEME_OUT=$("$PY" "$SCRIPT_DIR/tools/bible_theme.py" --all --check 2>&1)
    THEME_EXIT=$?
    echo "$THEME_OUT" | tail -20
    if [ $THEME_EXIT -ne 0 ]; then
        echo ""
        echo "[Bible] BLOCKED: a themed corpus is not derived from the base corpus."
        echo "[Bible] python tools/bible_theme.py --all --check"
        exit 1
    fi
else
    echo "[Bible] gate not found -- passing loud (gate unrunnable)"
fi
echo ""

# ─── Check 4d: the platform pages still tell the truth ───
# docs/platforms/ tells a reader how to fit this tree to another machine, and it
# had already drifted in three ways nobody caught: a command that did not exist
# (tools/commgate.py), a resolver that was never built (src/CORE/accelerate), and
# a gate count quoted as prose (15, while the registry said 19). All three were
# true once. The fix is not diligence, it is re-running: every claim in
# docs/platforms/claims.json names the command that settles it, and this stanza
# runs them. A claim that cannot run (no Apple hardware) is UNVERIFIABLE with a
# reason, and that is reported, never counted as a pass.
#
# 5.1 s measured 2026-11-01 (11 claims, two of them subprocess probes). The gate
# also appears in the registry, so preflight re-runs it; that duplication is the
# same arrangement the Bible stanza uses, and it costs the same again.
echo "[Claims] Re-running the claims docs/platforms makes about this tree..."
if [ "${ALLOW_PLATFORM_CLAIMS:-}" = "1" ]; then
    echo "[Claims] skipped: ALLOW_PLATFORM_CLAIMS=1 declared."
    echo ""
elif [ -f "$SCRIPT_DIR/tools/platform_claims.py" ]; then
    # Status captured before any filtering: piping into tail makes $? tail's.
    CLAIMS_OUT=$("$PY" "$SCRIPT_DIR/tools/platform_claims.py" --check 2>&1)
    CLAIMS_EXIT=$?
    echo "$CLAIMS_OUT" | tail -15
    if [ $CLAIMS_EXIT -ne 0 ]; then
        echo ""
        echo "[Claims] BLOCKED: a platform page and the tree disagree (see above)."
        echo "[Claims] Fix the page or the code, then: $PY $SCRIPT_DIR/tools/platform_claims.py"
        echo "[Claims] Override (declared): ALLOW_PLATFORM_CLAIMS=1."
        exit 1
    fi
else
    echo "[Claims] gate not found -- passing loud (gate unrunnable)"
fi
echo ""

# ─── Check 5: mesh wire-or-cut ledger freshness ───
# NEURAL_MESH_WIRE_OR_CUT.json is GENERATED, and nothing regenerated it when
# a caller changed -- so it went stale silently. Measured 2026-10-10: the
# restored EP235 loop made read_pressure statically called while the ledger
# still said `dispatched`, and tests/test_mesh_wire_or_cut.py failed on state
# the code had already moved past. Fails when the set of unreferenced
# functions no longer matches the committed ledger.
echo "[Mesh] Checking wire-or-cut ledger freshness..."
if [ -f "$SCRIPT_DIR/tools/mesh_function_preflight.py" ]; then
    # Capture first, then print: piping into tail would make $? tail's
    # status and this gate would read the wrong exit code.
    MESH_OUT=$(PYTHONPATH="src:${PYTHONPATH:-}" "$PY" "$SCRIPT_DIR/tools/mesh_function_preflight.py" --check 2>&1)
    MESH_EXIT=$?
    echo "$MESH_OUT" | tail -12
    if [ $MESH_EXIT -ne 0 ]; then
        echo ""
        echo "[Mesh] Ledger is STALE against the code. Refresh:"
        echo "[Mesh]   python tools/mesh_function_preflight.py --verdict --triage"
        exit 1
    fi
else
    echo "[Mesh] check not found -- passing loud (check unrunnable)"
fi
echo ""

# ─── Check 5b (blocking): span receipt guard still refuses untraced routes ───
# AOA's router writes one span per routed call and never fails a route for
# telemetry, so a route that left no row is invisible in its own result.  The
# guard (tools/span_receipt_guard.py) opens otel_spans READ-ONLY and refuses a
# route whose trace_id+span_id no row carries.
#
# WHY THIS RUNS --selftest AND NOT A LIVE CHECK.  A commit has no route: "no
# span was written since HEAD" is true for every doc edit, so a live check here
# would block all of them and be switched off within a day.  What belongs at the
# commit is proof that the guard still refuses an untraced route and still
# accepts a traced one -- a guard nobody runs rots, and a rotten guard reads
# exactly like a green one.  The live form is `--result <route_result.json>`,
# for a caller claiming a route was traced.
echo "[SpanReceipt] Proving the span receipt guard still refuses an untraced route..."
if [ -f "$SCRIPT_DIR/tools/span_receipt_guard.py" ]; then
    # Capture-then-print, same as [Mesh]: `| tail` would make $? tail's status.
    SPANREc_OUT=$("$PY" "$SCRIPT_DIR/tools/span_receipt_guard.py" --selftest 2>&1)
    SPANREC_EXIT=$?
    echo "$SPANREc_OUT" | tail -6
    if [ $SPANREC_EXIT -ne 0 ]; then
        echo ""
        echo "[SpanReceipt] The guard no longer refuses an untraced route, or no"
        echo "[SpanReceipt] longer accepts a traced one. Either way it has stopped"
        echo "[SpanReceipt] being evidence. Inspect: python tools/span_receipt_guard.py --selftest"
        exit 1
    fi
else
    echo "[SpanReceipt] guard not found -- passing loud (check unrunnable)"
fi
echo ""

# ─── Check 5c (blocking): ELIZA's shard stubs still describe the Kali atlas ───
# Project_ELIZA/shards holds one addressable stub per Kali class unit, plus
# SHARD_INDEX.json and WIRED.json, the adoption ledger. Measured 2026-11-03: the
# surface was intact (1,183 stubs, every one importable and every load() raising)
# and `--check` had NO CALLER -- the drift gate existed, its own test file passed,
# and nothing in the commit or push path ran it. A gate nothing runs is a promise.
#
# THE ATLAS IS OUT OF REPO (~/yggdrasil_forest/atlas_kali-linux.json, written by
# tools/domain_trees.py on the Kali lane), so a host that never ran that census
# cannot judge the stubs. That case is named and skipped LOUDLY, and the tool's
# exit 2 -- could not perform the run -- is read as its own thing rather than
# flattened into the drift exit. What must never happen is the third reading:
# silence that looks green.
echo "[ShardIntake] Checking ELIZA shard stubs against the Kali atlas..."
ATLAS_KALI="${YGGDRASIL_ATLAS:-$HOME/yggdrasil_forest/atlas_kali-linux.json}"
if [ "${ALLOW_SHARD_INTAKE:-}" = "1" ]; then
    # Same escape idiom as ALLOW_EVIDENCE_GATE: declared, and declared aloud.
    echo "[ShardIntake] ALLOW_SHARD_INTAKE=1 declared -- gate NOT run for this commit."
elif [ ! -f "$SCRIPT_DIR/Project_ELIZA/tools/shard_intake.py" ]; then
    echo "[ShardIntake] tool not found -- passing loud (check unrunnable)"
elif [ ! -f "$ATLAS_KALI" ]; then
    echo "[ShardIntake] atlas ABSENT ($ATLAS_KALI) -- COULD NOT JUDGE, and this BLOCKS."
    echo "[ShardIntake] Nothing was judged, so nothing is green: generate the census on"
    echo "[ShardIntake] the Kali lane (python tools/domain_trees.py) and copy it, pass"
    echo "[ShardIntake] --atlas via YGGDRASIL_ATLAS, or declare ALLOW_SHARD_INTAKE=1."
    exit 1
else
    # Capture first, then print: piping into tail would make $? tail's status.
    SHARD_OUT=$("$PY" "$SCRIPT_DIR/Project_ELIZA/tools/shard_intake.py" \
        --atlas "$ATLAS_KALI" --check 2>&1)
    SHARD_EXIT=$?
    echo "$SHARD_OUT" | tail -8
    if [ $SHARD_EXIT -eq 2 ]; then
        echo ""
        echo "[ShardIntake] BLOCKED: the atlas exists and could not be read (above)."
        echo "[ShardIntake] This is a corrupt input, not a drifted surface: fix it,"
        echo "[ShardIntake] or regenerate the census, rather than committing past it."
        exit 1
    elif [ $SHARD_EXIT -ne 0 ]; then
        echo ""
        echo "[ShardIntake] The stub surface no longer matches the atlas, or a wired"
        echo "[ShardIntake] entry lost the test that proves it. Regenerate deliberately:"
        echo "[ShardIntake]   python Project_ELIZA/tools/shard_intake.py --write"
        exit 1
    fi
fi
echo ""

# ─── Check 6: wire evidence on every `wired` declaration ───
# The mesh check above compares the ledger's KEY SET. It passed 2026-10-19
# while 136 of 137 `wired` rows carried `module-linked: no` in their own
# evidence: the status claimed a caller, and nothing in the pipeline asked for
# one. tools/wire_evidence_audit.py --check fails when a wired row carries
# neither `link_evidence` nor a named `link_gap`, so a new wired claim has to
# say where its support is. Regenerate the annotation with --apply.
echo "[Wire] Checking that wired declarations carry their link evidence..."
if [ -f "$SCRIPT_DIR/tools/wire_evidence_audit.py" ]; then
    # Same capture-then-print discipline as the mesh check: `| tail` would
    # replace the gate's exit status with tail's.
    WIRE_OUT=$(PYTHONPATH="src:${PYTHONPATH:-}" "$PY" "$SCRIPT_DIR/tools/wire_evidence_audit.py" --check 2>&1)
    WIRE_EXIT=$?
    echo "$WIRE_OUT" | tail -12
    if [ $WIRE_EXIT -ne 0 ]; then
        echo ""
        echo "[Wire] A wired declaration has no recorded link. Annotate:"
        echo "[Wire]   python tools/wire_evidence_audit.py --apply"
        exit 1
    fi
else
    echo "[Wire] check not found -- passing loud (check unrunnable)"
fi
echo ""

# ─── Check 6b (blocking): evidence gate (2026-10-30) ───
# Three failures this month had one shape: a statement true when written was
# repeated after the code moved, and nothing re-derived it -- a `rotate_secret`
# with no callers that returned success while writing nothing, an
# `enforce_global_quota` with no caller, and 135 `wired` ledger rows whose only
# support is a bare token in a file that does not import the module.
# tools/evidence_gate.py re-derives instead of reading prose: it parses every
# .py (an unparseable file is invisible to every other AST check), finds
# control-named functions with no module-linked caller, and asks the mesh probe
# whether its own ledger is fresh. Legacy debt is recorded in
# state/evidence_gate_baseline.json and does NOT fail; NEW debt does. A gate
# that fails on day one is a gate somebody disables.
# Deliberately NOT wired_claims: Check 6 above already gates wired-row link
# evidence through tools/wire_evidence_audit.py.
# Declared override, never silent: ALLOW_EVIDENCE_GATE=1.
if [ "${ALLOW_EVIDENCE_GATE:-}" = "1" ]; then
    echo "[Evidence] gate skipped: ALLOW_EVIDENCE_GATE=1 declared."
    echo ""
fi
if [ "${ALLOW_EVIDENCE_GATE:-}" != "1" ] && [ -f "$SCRIPT_DIR/tools/evidence_gate.py" ]; then
    echo "[Evidence] Re-deriving control and ledger claims from source..."
    # Capture first, then print: `cmd | tail` makes $? tail's status, and a gate
    # that reads the wrong exit code is worse than no gate (3 false passes).
    EVIDENCE_OUT=$(PYTHONPATH="src:${PYTHONPATH:-}" "$PY" "$SCRIPT_DIR/tools/evidence_gate.py" \
        --only ast_parse_coverage,dead_control,ledger_freshness 2>&1)
    EVIDENCE_EXIT=$?
    echo "$EVIDENCE_OUT" | tail -22
    if [ $EVIDENCE_EXIT -ne 0 ]; then
        echo ""
        echo "[Evidence] BLOCKED: a control or ledger claim no longer re-derives."
        echo "[Evidence] New debt above is not in state/evidence_gate_baseline.json."
        echo "[Evidence] Fix the code, or accept it explicitly:"
        echo "[Evidence]   python tools/evidence_gate.py --update-baseline"
        echo "[Evidence] Override (declared): ALLOW_EVIDENCE_GATE=1."
        exit 1
    fi
    echo ""
else
    echo "[Evidence] gate not found or skipped -- passing loud (check unrunnable)"
    echo ""
fi

# ─── Check 6c (blocking): failure-evidence ratchet (2026-11-01) ───
# The REVIVE-RUNNER lane fixed five sites where a runner DROPPED the evidence of
# its own failure. Two of the three defect classes are statically visible: an
# unbounded `subprocess.run/call/check_output/check_call` (a wait that can outlive
# the thing that started it) and a text-mode call with no `encoding=` (on Windows
# the platform codec decodes the output, a non-ASCII byte raises inside
# subprocess's reader thread where it never reaches the caller, and the call looks
# SUCCESSFUL while yielding EMPTY stdout). The ratchet that catches both,
# tools/anti_pattern_gate.py, is src/-scoped, so every subprocess call in tools/
# and scripts/ was invisible to it. Measured on wiring day: 29 sites across 10
# files -> 0, 550 files, and the one remaining unbounded wait is declared exempt
# in-call (tools/unified_cli.py, a foreground dispatcher on inherited stdio).
# Baseline is per (file, category) and NEW debt fails; legacy debt does not.
# Declared override, never silent: ALLOW_FAILURE_EVIDENCE=1.
if [ "${ALLOW_FAILURE_EVIDENCE:-}" = "1" ]; then
    echo "[FailureEvidence] gate skipped: ALLOW_FAILURE_EVIDENCE=1 declared."
    echo ""
fi
if [ "${ALLOW_FAILURE_EVIDENCE:-}" != "1" ] && [ -f "$SCRIPT_DIR/tools/failure_evidence_gate.py" ]; then
    echo "[FailureEvidence] Re-deriving waits and encodings from the AST..."
    # Capture first, then print: `cmd | tail` makes $? tail's status.
    FE_OUT=$("$PY" "$SCRIPT_DIR/tools/failure_evidence_gate.py" --check 2>&1)
    FE_EXIT=$?
    echo "$FE_OUT" | tail -16
    if [ $FE_EXIT -ne 0 ]; then
        echo ""
        echo "[FailureEvidence] BLOCKED: a new call cannot report its own failure."
        echo "[FailureEvidence] Add timeout= to the wait, encoding=utf-8 to the decode."
        echo "[FailureEvidence] Accept legacy debt explicitly instead of by accident:"
        echo "[FailureEvidence]   $PY tools/failure_evidence_gate.py --baseline"
        echo "[FailureEvidence] Override (declared): ALLOW_FAILURE_EVIDENCE=1."
        exit 1
    fi
    echo ""
else
    echo "[FailureEvidence] gate not found or skipped -- passing loud (check unrunnable)"
    echo ""
fi

# ─── Check 6d (blocking): the METTHEW family catalogue (2026-11-01) ───
# docs/METTHEW/highend_families.json used to describe itself as governed while its
# enforcement block read `"status": "pending"`: the vocabulary rule, the
# affects/determinism partition and "no claim without a gate" were checked only by
# pytest, and a file cannot tell whether pytest ran. tools/metthew_families.py is
# that checker (F1..F7), and F7 now refuses the catalogue if the block goes back to
# claiming a gate that does not run. It also refuses, by name, the two defect classes
# this project keeps finding in prose: a dated claim with no source tier (F4) and a
# byte figure nobody measured (F5) -- the eight payload_bytes_estimate values were
# arithmetic about files that do not exist and are now gone.
# Declared override, never silent: ALLOW_METTHEW_CATALOGUE=1.
if [ "${ALLOW_METTHEW_CATALOGUE:-}" = "1" ]; then
    echo "[Metthew] catalogue gate skipped: ALLOW_METTHEW_CATALOGUE=1 declared."
    echo ""
fi
if [ "${ALLOW_METTHEW_CATALOGUE:-}" != "1" ] && [ -f "$SCRIPT_DIR/tools/metthew_families.py" ]; then
    echo "[Metthew] Judging the high-end family catalogue (F1..F7)..."
    MC_OUT=$("$PY" "$SCRIPT_DIR/tools/metthew_families.py" --check 2>&1)
    MC_EXIT=$?
    echo "$MC_OUT" | tail -8
    if [ $MC_EXIT -ne 0 ]; then
        echo ""
        echo "[Metthew] BLOCKED: the catalogue makes a claim its own gate refuses."
        echo "[Metthew] Every lineage entry needs source_tier (A/B/C/D) and, above D, a url."
        echo "[Metthew] A payload is measured (bytes + command + measured_on) or unmeasured."
        echo "[Metthew] Override (declared): ALLOW_METTHEW_CATALOGUE=1."
        exit 1
    fi
    echo ""
else
    echo "[Metthew] no verdict: the checker is absent, or this run was declared skipped."
    echo ""
fi

# ─── Check 6e (blocking): the register of dying game systems (2026-11-01) ───
# docs/METTHEW/AT_RISK_SYSTEMS.json is the guardian's register of systems threatened
# with extinction, and like the family catalogue it used to describe itself as
# governed while nothing ran it. tools/at_risk_register.py is that checker (E1..E8).
# E4 refuses an entry with no next act or no gate; E8 refuses a register that hides
# its own edge -- the cohorts it has NOT counted must be named, each with a next
# sweep, so "all the systems threatened" never reads as a finished count.
# Declared override, never silent: ALLOW_AT_RISK_REGISTER=1.
if [ "${ALLOW_AT_RISK_REGISTER:-}" = "1" ]; then
    echo "[AtRisk] register gate skipped: ALLOW_AT_RISK_REGISTER=1 declared."
    echo ""
fi
if [ "${ALLOW_AT_RISK_REGISTER:-}" != "1" ] && [ -f "$SCRIPT_DIR/tools/at_risk_register.py" ]; then
    echo "[AtRisk] Judging the register of dying systems (E1..E8)..."
    AR_OUT=$("$PY" "$SCRIPT_DIR/tools/at_risk_register.py" --check 2>&1)
    AR_EXIT=$?
    echo "$AR_OUT" | tail -8
    if [ $AR_EXIT -ne 0 ]; then
        echo ""
        echo "[AtRisk] BLOCKED: the register makes a claim its own gate refuses."
        echo "[AtRisk] Every dying entry needs a guardian (next act) AND a gate (the number)."
        echo "[AtRisk] Every uncounted cohort needs a next_sweep beginning with a verb."
        echo "[AtRisk] Override (declared): ALLOW_AT_RISK_REGISTER=1."
        exit 1
    fi
    echo ""
else
    echo "[AtRisk] no verdict: the checker is absent, or this run was declared skipped."
    echo ""
fi

# ─── Check 7 (advisory): testing-paradigm rule 1 (Engram #784) ───
# A new src/ function needs a staged Hypothesis property test. Advisory by
# default: a presence heuristic must not block commits until it is measured.
# Set PARADIGM_GATE_STRICT=1 to make it blocking. Its exit code is printed,
# not enforced, unless strict mode is on.
if [ -f "$SCRIPT_DIR/tools/paradigm_gate.py" ]; then
    PARADIGM_OUT=$("$PY" "$SCRIPT_DIR/tools/paradigm_gate.py" --check 2>&1)
    PARADIGM_EXIT=$?
    echo "$PARADIGM_OUT"
    if [ $PARADIGM_EXIT -ne 0 ]; then
        echo "[Paradigm] BLOCKED (strict mode). Stage a Hypothesis property test."
        exit 1
    fi
fi

# ─── Check 9 (blocking): unified gate registry (tools/preflight_all.py) ───
# The gate set above is hand-listed: a gate is enforced only if somebody
# remembers to add a stanza to this file. tools/preflight_all.py holds the
# registry (gate, artifact, control test) in one tracked runner, so the commit
# path can ask the registry instead of a copy of it. It runs LAST among the
# blocking checks because it re-runs several of them; it is the backstop for
# whatever the hand-written list does not name.
# MEASURED? NO. The per-commit latency of `--check` has NOT been measured by
# the author of this stanza. Its placement is not a claim that it is cheap, and
# that measurement is owed before this scope is tightened further.
# --include-slow is deliberately NOT passed: commit latency stays at default.
# Declared override, never silent: ALLOW_PREFLIGHT=1.
if [ "${ALLOW_PREFLIGHT:-}" = "1" ]; then
    echo "[Preflight] gate registry skipped: ALLOW_PREFLIGHT=1 declared."
    echo ""
fi
if [ "${ALLOW_PREFLIGHT:-}" != "1" ]; then
    if [ -f "$SCRIPT_DIR/tools/preflight_all.py" ]; then
        if [ -x "$PYTHON3" ]; then
            echo "[Preflight] Checking unified gate registry..."
            # Status captured immediately. The gate's own output is NOT piped
            # through head/tail before $? is read: that pipeline rc trap has
            # produced a false pass in this repo three times.
            # Scope reaches the registry as an ARGUMENT, never as an env var:
            # gate_fingerprint() hashes argv, so a recorded PASS from index mode
            # cannot be reused to answer a commit-mode question.
            if [ -n "$EV_BASE" ]; then
                set -- --base "$EV_BASE"
            else
                set --
            fi
            PREFLIGHT_OUT=$("$PYTHON3" "$SCRIPT_DIR/tools/preflight_all.py" --check "$@" 2>&1)
            PREFLIGHT_EXIT=$?
            echo "$PREFLIGHT_OUT"
            if [ $PREFLIGHT_EXIT -ne 0 ]; then
                echo ""
                echo "[Preflight] BLOCKED: gate registry reported failures (see above)."
                echo "[Preflight] Override (declared): ALLOW_PREFLIGHT=1."
                exit 1
            fi
            echo ""
        else
            echo "[Preflight] 3.15t python not found -- passing loud (gate unrunnable)"
            echo ""
        fi
    else
        echo "[Preflight] tools/preflight_all.py absent -- passing loud (registry unrunnable)"
        echo ""
    fi
fi

# ─── Advisory: transcript AP-5 gate (Engram #1012) ───
# Scans persisted assistant prose for permission-seeking language (AP-5).
# Fail-open: a missing/locked store or any error never blocks the commit.
# Report only — adjudication is human. Runs on 3.15t (pinned above).
if [ -f "$SCRIPT_DIR/tools/transcript_ap5_gate.py" ]; then
    echo "[AP5] Scanning persisted assistant prose..."
    "$PYTHON3" "$SCRIPT_DIR/tools/transcript_ap5_gate.py" --limit 500 2>&1 || true
    echo ""
fi


# ─── Evidence marker: when this tree last passed ───
# Written only on PASS, so its presence is a reading and its absence is the truth.
TREE_SHA=$(git write-tree 2>/dev/null || echo unknown)
mkdir -p "$SCRIPT_DIR/logs" 2>/dev/null || true
printf '{"verdict":"PASS","ran_at":"%s","tree":"%s","tier":"evidence","checks_passed":14}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TREE_SHA" > "$SCRIPT_DIR/logs/hook_evidence.json"

exit 0
