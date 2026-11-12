# coding: utf-8
"""Presence control: a gate in ``tools/`` that no hook runs is not a gate.

This repository's recurring defect is **"mechanized but not enforced"**: the
check is written, reviewed, and then reported as "in place" while nothing
executes it. Two live instances are the reason this file exists:

* ``tools/preflight_all.py`` (38 KB, an 11-gate registry with its own hook-parity
  self-test) was invoked by **nothing** -- so its ``--self-test`` drift check had
  never run either. A control inside an unrun script cannot report.
* ``tools/test_paradigm_gate.py`` *was* wired, which proves the class is about
  the wiring step being skipped, not about the gate being unwanted.

So this test asserts the wiring, not the gate's judgement. It is a **presence
control**: it can prove a script is named by a real (non-comment, non-``echo``)
line of the hook. It cannot prove the gate is correct, that it fails when it
should, or that its baseline is fresh -- those need the gate's own control test,
named in ``preflight_all.py``'s registry.

Two deliberate design choices, both earned from this repo's own mistakes:

1. **Comment lines are stripped before matching.** The hook's header names
   ``tools/install_git_hooks.py`` in prose; a matcher that counts a mention as a
   gate fires on a correct tree. AGENTS.md records a gate that fired on its own
   rulebook for exactly this reason.
2. **``echo`` lines are stripped too.** The hook prints remediation hints such as
   ``echo "[Canon] Reconcile: python tools/canon_drift_check.py --fix"``. A
   matcher that accepted them would keep passing after the real invocation was
   deleted -- a false pass manufactured by a *help message*.

The allowlist is not a parking lot. Each entry carries a reason the test
verifies against the file it describes, so the entry cannot quietly become true
or false: fix the underlying condition and the test fails until the entry is
re-adjudicated.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / ".githooks" / "pre-commit"
TOOLS = REPO / "tools"
PREFLIGHT = TOOLS / "preflight_all.py"

#: ``(?:tools|scripts)/name.py`` -- the same shape ``preflight_all.py`` uses to
#: re-derive the hook's inventory, written independently here on purpose: two
#: instruments that must agree are worth more than one that is trusted.
_SCRIPT_RE = re.compile(r"(?:tools|scripts)/[A-Za-z0-9_]+\.py")

#: An existence test -- ``[ -f path ]``, ``if [[ -x path ]]``, ``[ ! -f path ]``.
#: It asks whether a script is there; it does not execute it, so crediting it as
#: wiring would let the hook pass this control while the real invocation was
#: deleted -- the same false pass as an ``echo`` hint, one step closer to the code.
_GUARD_RE = re.compile(r"^\s*(?:(?:if|elif|while|until)\s+)?\[\[?\s*!?\s*-[fx]\s")

#: Gates on disk that the hook does not invoke directly, each with the *kind* of
#: reason and a one-line justification the test below can verify.
#:
#: kind="registered-slow"  -> registered in ``preflight_all.py`` with
#:                            ``inside_hook=False`` because it is too slow for
#:                            every commit; it is run by the preflight runner.
#: kind="unregistered"     -> in NO registry at all. This is a REAL GAP, kept
#:                            visible as a ratchet instead of being wired to the
#:                            wrong place. It must not silently grow.
#: kind="hardcoded-root"   -> judges a different tree than this repo, so wiring
#:                            it would enforce a verdict about another checkout.
#:                            (No entry uses it today: cain_render_gate.py was
#:                            repointed at this repo and then RETIRED with its
#:                            document on 2026-11-01. The kind is kept so the
#:                            next gate that judges another tree has a name.)
#: kind="runtime-startup"  -> its subject only exists in a live session (a
#:                            daemon, a floor proxy, stdio children), so it runs
#:                            at session start. Verified against the runner:
#:                            the path constant, a command list built from it
#:                            and the ``subprocess.run`` that executes it must
#:                            all be present, so deleting the run breaks this.
#: kind="wired-elsewhere"  -> a third element names the file that executes it;
#:                            verified with the same invocation-line matcher the
#:                            hook uses, on THAT file.
KNOWN_UNWIRED = {
    "aggregate_suite_gate.py": (
        "registered-slow",
        "runs single-process pytest over all of tests/ (slow=True in the "
        "preflight_all.py registry); too slow for a per-commit hook",
    ),
    "type_check_gate.py": (
        "registered-slow",
        "basedpyright over the tree, measured at ~64s (module docstring); "
        "slow=True in the preflight_all.py registry",
    ),
    # The five below moved from pre-commit's FAST tier to the EVIDENCE tier on
    # 2026-11-02, when the hook was split (a whole-tree forensic pass does not
    # belong in the 10s path). They are still invoked, from
    # tools/evidence_tier.sh -- so `wired-elsewhere` with that runner is the
    # accurate entry, and the same matcher proves it. A `wired` entry would now
    # be false, and no entry at all would be a false alarm.
    "failure_evidence_gate.py": (
        "wired-elsewhere",
        "findings reached 0 (29 sites, 10 files) on 2026-11-01 and it was wired; "
        "on 2026-11-02 the hook grew an EVIDENCE tier and this moved there, "
        "because a baseline-ratchet over the whole tree is not a staged-diff check",
        "tools/evidence_tier.sh",
    ),
    "anti_pattern_gate.py": (
        "wired-elsewhere",
        "scans the tree against a recorded anti-pattern baseline, so it lives in "
        "the EVIDENCE tier of the 2026-11-02 hook split rather than the per-commit "
        "path whose subject is the staged diff",
        "tools/evidence_tier.sh",
    ),
    "evidence_gate.py": (
        "wired-elsewhere",
        "re-derives claims about the tree (rotate_secret's success signal, the "
        "NEURAL_MESH_WIRE_OR_CUT rows) rather than reading the staged diff, so it "
        "runs from the EVIDENCE tier of the 2026-11-02 hook split",
        "tools/evidence_tier.sh",
    ),
    "paradigm_gate.py": (
        "wired-elsewhere",
        "the EVIDENCE tier of the 2026-11-02 hook split runs it; it decides a "
        "committed fact (a new top-level src/ function needs a @given) from the "
        "commit range, which per-file staged reading cannot answer",
        "tools/evidence_tier.sh",
    ),
    "transcript_ap5_gate.py": (
        "wired-elsewhere",
        "its subject is assistant prose in the persisted session store, not the "
        "working tree -- so it runs from the EVIDENCE tier, which may read durable "
        "artefacts the per-commit path should not",
        "tools/evidence_tier.sh",
    ),
    "anchor_runtime_gate.py": (
        "registered-slow",
        "registered 2026-11-01 as preflight_all.py 'anchor-runtime' with "
        "inside_hook=False: its subject is a LIVE worm registry and memory store "
        "-- it discovered 257 worms, executed AnchorRetrievalWorm and read "
        "query_memories() evidence back (7/7 PASS in 5.1s) -- so a bare commit "
        "hook cannot judge it",
    ),
    "git_merge_gate.py": (
        "wired-elsewhere",
        "2026-11-01: moved from 'unregistered' to the PUSH hook, because a merge "
        "conflict is a property of the pair (this branch, the remote tip) and "
        "changes when the other side moves, not when you type. --pre-push reads "
        "git's own stdin protocol and tests only the pushes that need a merge; "
        "a per-commit merge simulation was the wrong cost",
        ".githooks/pre-push",
    ),
    "mcp_startup_gate.py": (
        "runtime-startup",
        "runs at SESSION START, not at commit time: tools/session_start.py "
        "executes it with --quiet --fix because its subject -- a live daemon, the "
        "floor proxy and the client's stdio children -- does not exist in a "
        "commit hook",
    ),
    "nurse_ratchet_release_gate.py": (
        "wired-elsewhere",
        "executed by the published project's CI, which runs it after "
        "tools/publicize.py; the trunk copy is the source that CI's "
        "PUBLIC_MANIFEST.json instrument is materialised from",
        "Project_Nurse_Ratchet/.github/workflows/ci.yml",
    ),
    # tools/cain_render_gate.py is no longer allowlisted because it is no
    # longer here: RETIRED 2026-11-01 with its own target document, archived
    # under archive/retired-tools-20261101/. test_retired_tools_stay_retired
    # holds the retirement in place.
}

#: Scripts retired from ``tools/`` with the reason and the archive that holds
#: them. A retirement is a decision, so it is asserted rather than remembered.
RETIRED_TOOLS = {
    "cain_render_gate.py": "archive/retired-tools-20261101/RETIREMENT.md",
    "cain_fix_checks.py": "archive/retired-tools-20261101/RETIREMENT.md",
}


def _kind(name):
    return KNOWN_UNWIRED[name][0]


def _reason(name):
    return KNOWN_UNWIRED[name][1]


def _runner(name):
    entry = KNOWN_UNWIRED[name]
    return entry[2] if len(entry) > 2 else None


# --------------------------------------------------------------------------- #
# Parsing helpers (pure functions, so the negative control can exercise them)
# --------------------------------------------------------------------------- #


def _non_comment_lines(text):
    """Yield ``(lineno, stripped)`` for lines the shell actually EXECUTES.

    A line whose first non-space character is ``#`` is prose. Blank lines carry
    nothing. ``echo`` lines are help text, never invocations -- see the module
    docstring for why accepting them is a false pass.
    """
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("echo "):
            continue
        yield lineno, line


def _is_existence_guard(line):
    """True when the line only TESTS for a script rather than executing it.

    Two shapes: a bare/augmented ``[ -f path ]`` test (``_GUARD_RE``), and any
    shell conditional that opens a block (``... ; then``). The second was added
    2026-11-02: the hook guards a run with
    ``if [ "${ALLOW:-}" != "1" ] && [ -f "$SCRIPT_DIR/tools/g.py" ]; then``,
    whose leading test is a string comparison, so the ``-f`` regex did not see
    it -- and the conditional line was credited as the invocation while the
    real one sat two lines inside the body.
    """
    if _GUARD_RE.match(line):
        return True
    return line.rstrip().endswith("; then") or line.rstrip() == "then"


def _invocation_lines(text):
    """Yield ``(lineno, line)`` for executed lines that name a script to run.

    Existence guards are skipped: they name a script to decide whether to run
    it, and a matcher that counted them would report wiring for a hook that
    only asks the question.
    """
    for lineno, line in _non_comment_lines(text):
        if _is_existence_guard(line):
            continue
        if _SCRIPT_RE.search(line):
            yield lineno, line


def _invoked_scripts(text):
    """Return the ``tools/``/``scripts/`` paths named by executed lines."""
    found = []
    for _lineno, line in _invocation_lines(text):
        for match in _SCRIPT_RE.findall(line):
            if match not in found:
                found.append(match)
    return found


def _gate_blocks(text):
    """Split ``preflight_all.py``'s registry into one block per ``Gate(`` row."""
    blocks = []
    for chunk in text.split("Gate(")[1:]:
        blocks.append(chunk.split("Gate(")[0])
    return blocks


def _gate_blocks_containing(text, script_path):
    """Registry rows whose ``argv`` names ``script_path``."""
    return [block for block in _gate_blocks(text) if script_path in block]


def _hook_text():
    return HOOK.read_text(encoding="utf-8", errors="replace")


def _preflight_text():
    return PREFLIGHT.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Negative control: the matcher itself must be falsifiable
# --------------------------------------------------------------------------- #


def test_matcher_ignores_comments_and_echo_hints():
    """The matcher must not credit a mention or a remediation hint as wiring.

    Without this, a correctly-wired hook and a hook that only *documents* the
    gate are indistinguishable -- and the second is the defect this file exists
    to catch.
    """
    fixture = (
        "# tools/commented_gate.py -- prose mention, not a gate run\n"
        'echo "reconcile: python tools/echo_hint_gate.py --fix"\n'
        '"$PYTHON3" "$SCRIPT_DIR/tools/real_gate.py" --check\n'
    )
    found = _invoked_scripts(fixture)
    assert found == ["tools/real_gate.py"], found


def test_matcher_ignores_existence_guards():
    """``[ -f path ]`` asks whether a gate exists; it does not run the gate.

    Crediting it is a false pass in the same family as an ``echo`` hint: the
    stanza survives the deletion of the invocation it is supposed to guard.
    """
    fixture = (
        'if [ -f "$SCRIPT_DIR/tools/guarded_only.py" ]; then\n'
        '    "$PYTHON3" "$SCRIPT_DIR/tools/guarded_only.py" --check\n'
        "fi\n"
    )
    assert _is_existence_guard(_non_comment_lines(fixture).__next__()[1])
    assert _invoked_scripts(fixture) == ["tools/guarded_only.py"]

    guard_only = 'if [ -f "$SCRIPT_DIR/tools/never_run.py" ]; then\n    :\nfi\n'
    assert _invoked_scripts(guard_only) == [], (
        "a hook that only tests for the script must not count as invoking it"
    )


# --------------------------------------------------------------------------- #
# The hook contract (worker B1's side; fails until the hook lands)
# --------------------------------------------------------------------------- #


def test_hook_is_readable_and_non_trivial():
    """Absence of the hook is MISSING EVIDENCE, never a pass."""
    assert HOOK.is_file(), "hook not found: %s" % HOOK
    executable_lines = sum(1 for _ in _non_comment_lines(_hook_text()))
    assert executable_lines > 10, (
        "hook has no executable body: %d executable lines" % executable_lines
    )


def test_preflight_all_is_invoked_by_the_hook():
    """The registry runner must be executed, not just present in tools/."""
    assert "tools/preflight_all.py" in _invoked_scripts(_hook_text()), (
        "tools/preflight_all.py exists but no executable line of "
        ".githooks/pre-commit invokes it -- its --self-test hook-parity check "
        "has therefore never run"
    )


def test_preflight_invocation_uses_the_pinned_interpreter():
    """Bare ``python``/``python3`` resolves to a different interpreter on this host."""
    lines = _hook_text().splitlines()
    # 1-based line numbers, used as such: the previous version subtracted one
    # here and then indexed 0-based `lines`, so it inspected the line AFTER the
    # invocation and reported a correctly pinned call as bare (measured
    # 2026-11-02 on the EVIDENCE-tier split's preflight guard).
    hits = [
        lineno
        for lineno, line in _invocation_lines(_hook_text())
        if "tools/preflight_all.py" in line
    ]
    assert hits, "no executable invocation line to inspect"
    for lineno in hits:
        index = lineno - 1
        window = " ".join(lines[max(0, index - 3): index + 1])
        assert "$PYTHON3" in window, (
            "line %d invokes preflight_all.py without $PYTHON3 -- bare python is "
            "3.14.7 GIL on this host, not the 3.15t production interpreter"
            % lineno
        )
        assert not re.search(r"(?<![$\w])python3?\b", lines[index]), (
            "line %d uses a bare python token on the invocation itself" % (index + 1)
        )


def test_preflight_has_a_declared_override():
    """An escape hatch must be declared, not a silent bypass."""
    text = "\n".join(line for _n, line in _non_comment_lines(_hook_text()))
    assert "ALLOW_PREFLIGHT" in text, (
        "no declared override: a blocking gate with no escape hatch gets disabled "
        "by editing the hook, which is the silent bypass this project forbids"
    )


# --------------------------------------------------------------------------- #
# Registry coverage: every gate on disk is either run or explicitly adjudicated
# --------------------------------------------------------------------------- #


def test_gate_inventory_is_not_vacuous():
    """Guard the guard: a glob that finds nothing must fail, not pass."""
    inventory = sorted(p.name for p in TOOLS.glob("*_gate.py"))
    assert len(inventory) >= 8, "only found %r -- the scan is not seeing tools/" % (
        inventory,
    )
    assert PREFLIGHT.is_file(), "registry runner missing: %s" % PREFLIGHT


def test_every_gate_is_wired_or_named():
    """A new unwired gate FAILS: it may not silently join the backlog."""
    inventory = sorted(p.name for p in TOOLS.glob("*_gate.py"))
    invoked = {Path(p).name for p in _invoked_scripts(_hook_text())}

    unaccounted = [
        name for name in inventory if name not in invoked and name not in KNOWN_UNWIRED
    ]
    assert not unaccounted, (
        "gate(s) in tools/ that no hook runs and no allowlist names: %r -- wire "
        "it or add it to KNOWN_UNWIRED with a reason this file can verify"
        % (unaccounted,)
    )


def test_allowlist_entries_still_describe_real_files():
    """A stale allowlist entry is how a deleted gate hides a missing one."""
    missing = [name for name in KNOWN_UNWIRED if not (TOOLS / name).is_file()]
    assert not missing, "allowlisted gate(s) no longer on disk: %r" % (missing,)


def test_registered_slow_reasons_match_the_registry():
    """``registered-slow`` is a claim about preflight_all.py; verify it there."""
    text = _preflight_text()
    for name in sorted(KNOWN_UNWIRED):
        if _kind(name) != "registered-slow":
            continue
        path = "tools/%s" % name
        blocks = _gate_blocks_containing(text, path)
        assert len(blocks) == 1, (
            "%s is allowlisted as registered-slow, but the registry has %d "
            "Gate( row(s) naming %s" % (name, len(blocks), path)
        )
        assert "inside_hook=False" in blocks[0], (
            "%s is registered but NOT marked inside_hook=False -- either wire it "
            "or correct the allowlist reason" % name
        )


def test_unregistered_gap_is_named_and_does_not_grow():
    """A gate in no registry is a real gap; it is visible as a ratchet.

    This does not demand the gap be zero -- it demands the gap be *known*. A
    new unregistered gate, or someone quietly registering this one, fails here
    so the decision is re-made rather than assumed.
    """
    declared = {n for n in KNOWN_UNWIRED if _kind(n) == "unregistered"}
    text = _preflight_text()
    observed = {
        name
        for name in KNOWN_UNWIRED
        if _kind(name) == "unregistered"
        and not _gate_blocks_containing(text, "tools/%s" % name)
    }
    assert observed == declared, (
        "unregistered-gate set drifted: declared=%r observed=%r" % (declared, observed)
    )


def test_runtime_startup_entries_are_executed_by_their_runner():
    """``runtime-startup`` claims a runner runs it; verify the chain, not the name.

    A mention is not an invocation (that is the whole point of this file), so the
    check demands all three links: the path constant, a command list built from
    that constant, and the ``subprocess.run`` that consumes the list. Delete the
    run and the entry fails instead of quietly describing a dead gate.
    """
    text = (TOOLS / "session_start.py").read_text(encoding="utf-8", errors="replace")
    checked = 0
    for name in sorted(KNOWN_UNWIRED):
        if _kind(name) != "runtime-startup":
            continue
        checked += 1
        constant = re.search(r"^(\w+)\s*=\s*Path\(\"([^\"]*%s)\"\)" % re.escape(name),
                             text, re.M)
        assert constant, (
            "%s is allowlisted as runtime-startup, but tools/session_start.py has "
            "no `NAME = Path(\"...%s\")` constant" % (name, name)
        )
        var = constant.group(1)
        assert re.search(r"cmd\s*=\s*\[[^\]]*str\(%s\)" % var, text, re.S), (
            "%s is allowlisted as runtime-startup, but session_start.py never puts "
            "str(%s) into a command list -- the constant is decoration" % (name, var)
        )
        assert "subprocess.run(cmd" in text, (
            "%s is allowlisted as runtime-startup, but the command list it builds "
            "is never executed" % name
        )
    assert checked, "no runtime-startup entries to verify -- the allowlist lost mcp_startup_gate.py"


def test_wired_elsewhere_entries_are_invoked_by_the_named_runner():
    """Same matcher as the hook, against the runner each entry names.

    The runner is a third element in the allowlist entry; an entry without one
    fails here, so "wired elsewhere" cannot be asserted without saying where.
    """
    for name in sorted(KNOWN_UNWIRED):
        if _kind(name) != "wired-elsewhere":
            continue
        runner = _runner(name)
        assert runner, (
            "%s is allowlisted as wired-elsewhere but names no runner file" % name
        )
        path = REPO / runner
        assert path.is_file(), "runner not found for %s: %s" % (name, path)
        invoked = _invoked_scripts(path.read_text(encoding="utf-8", errors="replace"))
        assert "tools/%s" % name in invoked, (
            "%s is allowlisted as wired-elsewhere, but no executed line of %s "
            "names tools/%s (found %r)" % (name, runner, name, invoked)
        )


def test_retired_tools_stay_retired():
    """A retired tool must be gone from tools/, archived, and documented.

    Retiring is a decision (2026-11-01: cain_render_gate.py's target document was
    deleted, so it could not judge anything). Asserting all three halves stops
    the file reappearing as an "orphan" that a future inventory has to
    re-adjudicate from scratch -- and stops the archive from being emptied
    while the docs still point at it.
    """
    for name, record in sorted(RETIRED_TOOLS.items()):
        assert not (TOOLS / name).exists(), (
            "%s was retired and is back in tools/ -- restore it only by "
            "answering the reason in %s" % (name, record)
        )
        archived = REPO / record.replace("RETIREMENT.md", name)
        assert archived.is_file(), (
            "%s was retired but its archived copy is missing: %s" % (name, archived)
        )
        assert (REPO / record).is_file(), (
            "%s has no retirement record at %s -- a retirement with no reason "
            "on disk is an unexplained deletion" % (name, record)
        )
    for name in RETIRED_TOOLS:
        assert name not in KNOWN_UNWIRED, (
            "%s is retired, so an allowlist entry describes a file that is gone"
            % name
        )


# --------------------------------------------------------------------------- #
# The PUSH hook: a second artefact, planted separately, judged separately
# --------------------------------------------------------------------------- #
#
# `.githooks/pre-push` is generated by `tools/bible_ship.py --install-hooks` and
# gitignored, so on a fresh clone it does not exist at all -- and until 2026-11-02
# nothing asserted that it did. The commit hook could be perfectly wired while
# every push left the machine unjudged, which is the one state no gate in the
# commit path can see: the merge gate runs at PUSH time and nowhere else.
#
# So the status is three-valued and the middle value is the defect:
#   OK      the hook exists and an executed line runs the merge gate
#   ABSENT  no hook (a clone that never ran the installer) -- a warning, because
#           nothing is half-wired: there is also no core.hooksPath
#   STALE   a hook exists and does not run the merge gate -- a FAILURE, because
#           this is what a template edit or a stale regeneration leaves behind,
#           and it is silent at push time


def _load_preflight():
    """Import ``tools/preflight_all.py`` by path (it is a script, not a package)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("preflight_all_under_test", PREFLIGHT)
    assert spec and spec.loader, "preflight_all.py could not be loaded from %s" % PREFLIGHT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_planted_push_hook_runs_the_merge_gate():
    """The hook on THIS disk must run the merge gate, not merely mention it.

    Read as text and matched with the same rules the commit-hook matcher uses,
    because that is the pair of instruments that must agree: a matcher that
    accepts a comment would pass a hook whose gate had been deleted.
    """
    preflight = _load_preflight()
    state, message = preflight.push_hook_status(preflight.read_push_hook())
    assert state == "OK", "push hook is %s: %s" % (state, message)
    invoked = _invoked_scripts((REPO / ".githooks" / "pre-push").read_text(
        encoding="utf-8", errors="replace"))
    assert "tools/git_merge_gate.py" in invoked, invoked


def test_a_push_hook_that_only_mentions_the_gate_is_stale():
    """Negative control: the shape that gets shipped by accident must fail."""
    preflight = _load_preflight()
    mentions_only = (
        "#!/bin/sh\n"
        "# tools/git_merge_gate.py --pre-push -- prose, not an invocation\n"
        'echo "run tools/git_merge_gate.py --pre-push to judge this push"\n'
        "exit 0\n"
    )
    state, message = preflight.push_hook_status(mentions_only)
    assert state == "STALE", (state, message)
    assert "--install-hooks" in message, message


def test_a_push_hook_naming_the_gate_without_the_push_mode_is_stale():
    """`--pre-push` is not decoration: without it the gate ignores git's stdin."""
    preflight = _load_preflight()
    wrong_mode = "#!/bin/sh\npython tools/git_merge_gate.py --base origin/master\n"
    state, _message = preflight.push_hook_status(wrong_mode)
    assert state == "STALE", state


def test_a_missing_push_hook_is_absent_and_not_a_failure():
    """A clone with no hook has nothing half-wired; the remedy is named."""
    preflight = _load_preflight()
    state, message = preflight.push_hook_status(None)
    assert state == "ABSENT", (state, message)
    assert "--install-hooks" in message, message
    assert "pre-push" in message, message


def test_an_unreadable_push_hook_is_not_reported_as_absent(tmp_path, monkeypatch):
    """The state a bare `except OSError: return None` could not express.

    A directory where the hook should be makes read_text raise IsADirectoryError (an
    OSError). That used to come back as None, which the caller renders as ABSENT -- a
    warning about a fresh clone -- when the truth is that a hook is present and cannot
    be judged. The staged scanner flagged the shape as `except_return_default` while
    this commit was landing, so the defect is fixed and pinned here.
    """
    preflight = _load_preflight()
    blocked = tmp_path / "pre-push"
    blocked.mkdir()
    monkeypatch.setattr(preflight, "PUSH_HOOK", blocked)
    read = preflight.read_push_hook()
    assert read.text is None and read.error, read
    state, message = preflight.push_hook_status(read)
    assert state == "UNREADABLE", (state, message)
    assert "cannot be read" in message, message


def test_preflight_check_reports_the_push_hook():
    """The check has to be CALLED, not merely defined -- the whole point."""
    source = PREFLIGHT.read_text(encoding="utf-8", errors="replace")
    assert source.count("push_hook_status(read_push_hook())") >= 2, (
        "push_hook_status is not consulted by both --check and --self-test"
    )
