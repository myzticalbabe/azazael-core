# coding: utf-8
"""Wire-or-cut contract for the mesh functions nothing reaches (PF16).

Why this is a test and not a to-do list entry
---------------------------------------------
Probe PF16 found 114 mesh functions that nothing in the repository references.
That finding cannot be fixed by editing the function: a function nothing calls
cannot regress visibly, so it can be neither trusted nor repaired by
observation. There are exactly two honest resolutions -- give it a caller, or
delete it -- and choosing between them is a decision, not a patch.

`evolution.py` already encodes this for the v2 engines as `SERVED_ENGINES` /
`UNSERVED_ENGINES`, with a test asserting no UNSERVED name is referenced by the
production driver, and that pattern worked. This test generalises it: every PF16
finding must appear in `NEURAL_MESH_WIRE_OR_CUT.json` with a declared
disposition, and the declaration must agree with reality.

What it catches:

* a NEW unreferenced function -- it fails the undeclared check instead of
  quietly joining a 114-item blind spot;
* a declaration that stopped being true, in both directions: a function
  declared ``wired`` that nothing actually calls, and a function declared
  ``retired`` that something now depends on;
* a declaration for a function that no longer exists, so the table cannot
  accumulate fiction;
* a status typo, which would otherwise silently become a fourth category.

``pending`` is deliberately allowed -- an unreviewed function is not the same as
a decided one, and collapsing those two is how a backlog turns into a lie. The
table is regenerated (decisions preserved) with:

    python tools/mesh_function_preflight.py --verdict
"""

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "tools", REPO / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import mesh_function_preflight as mfp  # noqa: E402

VERDICT = REPO / "NEURAL_MESH_WIRE_OR_CUT.json"


@pytest.fixture(scope="module")
def table():
    if not VERDICT.exists():
        pytest.skip("no verdict table; run "
                    "python tools/mesh_function_preflight.py --verdict")
    return json.loads(VERDICT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tree():
    funcs, _counts = mfp.build(None)
    unreferenced = {fn.label for fn in funcs if "PF16" in fn.failures}
    return funcs, unreferenced


def test_the_table_was_generated_by_the_probe_it_claims(table):
    assert table.get("probe") == "PF16"
    assert table.get("rule")


def test_every_unreferenced_function_is_declared(table, tree):
    _funcs, unreferenced = tree
    declared = set(table.get("declared") or {})
    undeclared = sorted(unreferenced - declared)
    assert not undeclared, (
        "%d unreferenced function(s) have no wire-or-cut decision; run "
        "`python tools/mesh_function_preflight.py --verdict` and set a status "
        "for each: %s" % (len(undeclared), undeclared[:10]))


def test_no_declaration_is_stale(table, tree):
    """A declaration for a function that is gone is fiction, not a decision."""
    funcs, _unreferenced = tree
    live = {fn.label for fn in funcs}
    stale = sorted(set(table.get("declared") or {}) - live)
    assert not stale, (
        "%d declaration(s) refer to functions that no longer exist: %s"
        % (len(stale), stale[:10]))


def test_a_wired_declaration_is_actually_wired(table, tree):
    """`wired` means a production caller exists, so PF16 must not fire."""
    _funcs, unreferenced = tree
    declared = table.get("declared") or {}
    lying = sorted(k for k, v in declared.items()
                   if v.get("status") == "wired" and k in unreferenced)
    assert not lying, (
        "declared wired but nothing reaches them: %s" % lying[:10])


def test_a_retired_declaration_is_still_unreferenced_and_states_why(table, tree):
    _funcs, unreferenced = tree
    declared = table.get("declared") or {}
    retired = {k: v for k, v in declared.items()
               if v.get("status") == "retired"}
    resurrected = sorted(k for k in retired if k not in unreferenced)
    assert not resurrected, (
        "declared retired but something now reaches them -- wire them or "
        "delete the declaration: %s" % resurrected[:10])
    silent = sorted(k for k, v in retired.items() if not str(v.get("reason") or "").strip())
    assert not silent, (
        "a retirement without a reason cannot be reviewed: %s" % silent[:10])


def test_statuses_are_known(table):
    known = set(mfp.VERDICT_STATUSES)
    declared = table.get("declared") or {}
    unknown = sorted({str(v.get("status"))
                      for v in declared.values()} - known)
    assert not unknown, "unknown status(es) %s; expected one of %s" % (
        unknown, sorted(known))


# ---------------------------------------------------------------------------
# `dispatched`: the measurement that made this status necessary
# ---------------------------------------------------------------------------
# Triaged 2026-09-22: of 164 findings, 62 were named by an identifier-like STRING
# in production code -- `getattr(obj, "deliver")`, a registry key, a dispatch
# table -- while PF16 reads only `ast.Name` / `ast.Attribute` and therefore
# cannot see any of them. The table had no way to say that: `wired` fails its own
# contract test while PF16 still fires, and `pending` would have discarded a fact
# the triage had already measured. These tests hold the new status to evidence in
# both directions, so it cannot become a bin for whatever is inconvenient.

def test_a_dispatched_declaration_is_backed_by_a_string_reference(table):
    """`dispatched` is a measurement, so an unbacked one is a lie."""
    refs = mfp.string_references()
    declared = table.get("declared") or {}
    unbacked = []
    for label, entry in declared.items():
        if str(entry.get("status")) != "dispatched":
            continue
        qual = str(entry.get("qualname") or "")
        if not (refs.get(qual.rsplit(".", 1)[-1]) or refs.get(qual)):
            unbacked.append(label)
    assert not unbacked, (
        "declared dispatched with no string reference in production code: %s"
        % sorted(unbacked)[:10])


def test_a_dispatched_declaration_states_where_and_why(table):
    """A disposition a reviewer cannot check is not a disposition."""
    declared = table.get("declared") or {}
    silent = sorted(k for k, v in declared.items()
                    if str(v.get("status")) == "dispatched"
                    and not str(v.get("reason") or "").strip())
    assert not silent, "dispatched without a reason: %s" % silent[:10]
    missing = sorted(k for k, v in declared.items()
                     if str(v.get("status")) == "dispatched"
                     and not v.get("evidence"))
    assert not missing, "dispatched without evidence locations: %s" % missing[:10]


def test_dispatched_is_not_a_way_to_hide_a_static_caller(table, tree):
    """The status must stay a FINDING, not an exemption.

    If a static production caller existed, PF16 would not have reported the
    function at all and it would not be in this table -- so every `dispatched`
    entry must still be unreferenced. (This is the same property `wired` is held
    to, from the other side: `wired` may not be unreferenced, `dispatched` may
    not stop being one.)
    """
    _funcs, unreferenced = tree
    declared = table.get("declared") or {}
    hidden = sorted(k for k, v in declared.items()
                    if str(v.get("status")) == "dispatched" and k not in unreferenced)
    assert not hidden, (
        "declared dispatched but a static caller now reaches them -- declare "
        "wired instead: %s" % hidden[:10])


def test_the_string_index_finds_name_lookup_and_ignores_prose():
    """The evidence source itself, calibrated in both directions.

    A false hit would claim reachability that does not exist, so prose and
    docstrings are excluded by construction; a miss only leaves the entry
    `pending`, which is the truth ("not measured").
    """
    refs = mfp.string_references()
    # A real dispatch site: the worm registry names its entry points as strings.
    assert refs.get("rotate_secret"), (
        "the string index no longer sees a known registry name; the evidence "
        "for every `dispatched` declaration is now unverified")
    assert all(":" in hit for hit in refs["rotate_secret"])
    # Prose must not be read as a reference. `zebra_quixotic_vernacular` occurs
    # nowhere in the source, and a sentence containing a name is not a caller.
    assert "zebra_quixotic_vernacular" not in refs
    assert "a function that nothing calls cannot regress" not in refs, (
        "docstring prose is being indexed as a reference")


def test_counts_agree_with_the_table(table):
    declared = table.get("declared") or {}
    tally = Counter(str(v.get("status")) for v in declared.values())
    for status in mfp.VERDICT_STATUSES:
        assert table["counts"].get(status, 0) == tally.get(status, 0), (
            "count for %r drifted from the table" % status)
    assert table.get("total") == len(declared)


def test_pending_is_visible_rather_than_hidden(table):
    """The backlog must be countable, or it is indistinguishable from zero."""
    declared = table.get("declared") or {}
    pending = [k for k, v in declared.items() if v.get("status") == "pending"]
    assert len(pending) == table["counts"].get("pending", 0)
    # Not an assertion on the SIZE (the backlog is real work), but the number
    # must be reported rather than inferred, so a monitor can trend it.
    assert isinstance(table["counts"].get("pending"), int)


# ---------------------------------------------------------------------------
# PF16 calibration: implicitly-invoked protocol methods
# ---------------------------------------------------------------------------
# PF16 asks whether the bare name appears as a Name or Attribute anywhere.  For a
# method the interpreter invokes itself -- `__repr__` via `repr()`, `__bool__` via
# `bool()`, `__getattr__` via any attribute miss -- no explicit reference can
# exist, so the probe reported a measured false positive rather than dead code.
# These tests hold that calibration in both directions: the protocol methods are
# exempt, and the exemption does not swallow ordinary unreferenced functions.

def test_pf16_never_reports_a_protocol_method(tree):
    """The exemption is by name-set, so no protocol name may survive as a finding."""
    _funcs, unreferenced = tree
    leaked = sorted(
        lbl for lbl in unreferenced
        if lbl.rsplit(":", 1)[-1].rsplit(".", 1)[-1] in mfp.PROTOCOL_METHODS)
    assert not leaked, (
        "PF16 reported implicitly-invoked protocol method(s), which no explicit "
        "reference can ever satisfy: %s" % leaked[:10])


def test_the_calibration_does_not_swallow_the_real_backlog(tree):
    """The exemption must not be a blanket silence.

    If it were too broad the findings would vanish and the probe would look
    clean for the wrong reason -- the failure mode this whole table exists to
    prevent.  So: protocol names are exempt, plain methods are not.
    """
    _funcs, unreferenced = tree
    assert unreferenced, "PF16 found nothing at all; the exemption is too broad"
    non_protocol = [
        lbl for lbl in unreferenced
        if lbl.rsplit(":", 1)[-1].rsplit(".", 1)[-1] not in mfp.PROTOCOL_METHODS]
    assert non_protocol, "every finding was exempted; the exemption is too broad"
    assert len(mfp.PROTOCOL_METHODS) > 20, (
        "PROTOCOL_METHODS looks truncated (%d names)" % len(mfp.PROTOCOL_METHODS))


def test_the_lazytorch_false_positive_that_forced_the_calibration(tree):
    """The concrete evidence, asserted against the real source.

    `nerve._LazyTorch.__bool__` was reported unreachable while `bool(_torch)` is
    called in the same file, and the module's other attribute accesses route
    through `_LazyTorch.__getattr__`.  If this file ever stops invoking them by
    protocol the exemption would be hiding dead code, so the test reads the
    source rather than trusting the name-set.
    """
    unc = tree[1]
    nerve_src = (REPO / "src" / "CORE" / "WORMS" / "expansion" / "nerve.py")
    if not nerve_src.exists():
        pytest.skip("nerve.py not present")
    text = nerve_src.read_text(encoding="utf-8", errors="replace")
    if "_torch" not in text:
        pytest.skip("nerve.py no longer defines a _torch proxy")
    assert "bool(_torch)" in text or "bool(use_torch and bool(_torch))" in text, (
        "nerve.py no longer invokes _LazyTorch.__bool__ by protocol; the "
        "exemption is now hiding dead code and must be revisited")
    assert "nerve:_LazyTorch.__bool__" not in unc
    assert "nerve:_LazyTorch.__getattr__" not in unc


# ---------------------------------------------------------------------------
# `--propose` is re-runnable, and the triage labels are a measurement
# ---------------------------------------------------------------------------

def test_proposing_twice_keeps_the_evidence():
    """Evidence belongs to the FACT, not to the transition that recorded it.

    The first version of `propose` wrote `evidence` only when it flipped a row
    from `pending` to `dispatched`, so a second run -- which is how the table is
    regenerated -- left every already-dispatched row with a reason and no
    locations. `test_a_dispatched_declaration_states_where_and_why` caught it,
    and this test names the cause: run the triage twice over the same data and
    the measurement must survive.
    """
    label = "communication:ProtocolNegotiator.negotiate"
    pending = {
        "declared": {
            label: {
                "module": "communication",
                "qualname": "ProtocolNegotiator.negotiate",
                "line": 205,
                "status": "pending",
            }
        }
    }
    first = mfp.propose(json.loads(json.dumps(pending)))
    assert first["declared"][label]["status"] == "dispatched"
    assert first["declared"][label]["evidence"], (
        "the first pass recorded no evidence, so there is nothing to preserve")

    second = mfp.propose(json.loads(json.dumps(first)))
    entry = second["declared"][label]
    assert entry["status"] == "dispatched"
    assert entry["evidence"], (
        "a re-run dropped the evidence it had already measured: %r" % entry)


def test_a_verdict_rebuild_keeps_the_evidence_too():
    """The rebuild path must preserve a disposition, not just the transition.

    `wire_or_cut` is what `--verdict` runs, and the refresh path runs it WITHOUT
    `--propose`. It rebuilt each entry with four hand-picked keys, so every field
    a disposition had accumulated -- `evidence` among them -- was discarded on a
    plain regeneration. Measured 2026-09-22: the shipped table came back with 62
    `dispatched` rows and 0 of them carrying locations, and the contract test
    failed in the aggregate run against an artifact nothing had edited by hand
    (Error #202). `--propose` twice is the earlier, narrower version of this
    test; this one names the rebuild.
    """
    label = "communication:ProtocolNegotiator.negotiate"
    fn = type("F", (), {})()
    fn.module = "communication"
    fn.qualname = "ProtocolNegotiator.negotiate"
    fn.lineno = 205
    fn.label = label
    fn.failures = ["PF16"]

    triaged = mfp.propose(mfp.wire_or_cut([fn]))
    assert triaged["declared"][label]["evidence"], (
        "nothing to preserve -- the first pass recorded no evidence")

    rebuilt = mfp.wire_or_cut([fn], triaged["declared"])
    entry = rebuilt["declared"][label]
    assert entry["status"] == "dispatched"
    assert entry["evidence"], (
        "a --verdict rebuild dropped the evidence it had not measured: %r" % entry)
    assert entry["reason"], "a --verdict rebuild dropped the recorded reason"
    # ...while the fields that must track the tree are still refreshed.
    assert entry["line"] == 205 and entry["module"] == "communication"


def test_the_worklist_carries_every_pending_row_with_its_evidence(table):
    """The backlog must be work, not a count with a shape.

    `--triage` records a class and its evidence onto every pending row, but only
    into the JSON. The rendered worklist is what a reviewer reads, so this pins
    four properties of it: every pending row appears exactly once, the two
    classes are kept apart, a `test_reached_only` row carries the test location
    that reaches it (checking a row must not start with a search), and -- the
    one that matters most -- rendering the worklist changes no status.
    """
    triaged = mfp.triage(json.loads(json.dumps(table)))
    pending = {k: v for k, v in (triaged.get("declared") or {}).items()
               if str(v.get("status")) == "pending"}
    markdown = mfp.worklist_markdown(triaged)

    rows = [ln for ln in markdown.splitlines() if ln.startswith("- [ ] ")]
    assert len(rows) == len(pending), (
        "the worklist dropped or duplicated a pending row: %d rows for %d rows"
        % (len(rows), len(pending)))
    for label in pending:
        assert label.count(".") >= 0 and label in markdown, (
            "%s is pending but is not on the worklist" % label)

    reached = {k for k, v in pending.items()
               if str(v.get("triage")) == "test_reached_only"}
    for label in reached:
        row = [ln for ln in rows if label in ln]
        assert row, label
        assert "reached by tests/" in row[0], (
            "%s is test-reached and the worklist did not say where: %r"
            % (label, row[0]))

    # Rendering is read-only: a class is a measurement, and a worklist that
    # wrote a status would be an inferred disposition presented as a decision.
    after = json.loads(json.dumps(triaged))
    assert {k: str(v.get("status")) for k, v in after["declared"].items()} == \
           {k: str(v.get("status")) for k, v in triaged["declared"].items()}


def test_the_worklist_refuses_to_guess_a_disposition():
    """A class must not become a status anywhere in the render."""
    table_in = {"declared": {
        "m:a": {"module": "m", "qualname": "a", "line": 1,
                "status": "pending", "triage": "no_reference_anywhere",
                "triage_evidence": []},
        "m:b": {"module": "m", "qualname": "b", "line": 2,
                "status": "pending", "triage": "test_reached_only",
                "triage_evidence": ["tests/t.py:7"]},
    }, "triage_counts": {"test_reached_only": 1, "no_reference_anywhere": 1}}
    markdown = mfp.worklist_markdown(table_in)
    # The page says what it did not do, and nothing in it assigns a status: the
    # two classes name their candidate decisions in prose (`retirement`, `wiring`)
    # and must not read as having taken one.
    assert "does not change a status" in markdown
    for status in mfp.VERDICT_STATUSES:
        if status == "pending":
            continue
        assert ("status: `%s`" % status) not in markdown
        assert ("-> %s" % status) not in markdown
    assert "`m:b`" in markdown and "tests/t.py:7" in markdown


def test_triage_labels_the_pending_backlog_without_deciding_it(table):
    """A class is not a disposition: `triage` must never touch `status`."""
    labelled = mfp.triage(json.loads(json.dumps(table)))
    entries = labelled.get("declared") or {}
    for label, entry in entries.items():
        if str(entry.get("status")) != "pending":
            assert "triage" not in entry or entry["status"] == "pending"
            continue
        assert entry.get("triage") in mfp.TRIAGE_CLASSES, (label, entry.get("triage"))
        if entry["triage"] == "test_reached_only":
            assert entry.get("triage_evidence"), (
                "test_reached_only needs the test location that reaches it: %s" % label)
    counts = labelled.get("triage_counts") or {}
    pending = sum(1 for e in entries.values() if str(e.get("status")) == "pending")
    assert sum(counts.values()) == pending, (counts, pending)

    # And the STATUS must be untouched by a triage pass.
    before = {k: str(v.get("status")) for k, v in (table.get("declared") or {}).items()}
    after = {k: str(v.get("status")) for k, v in entries.items()}
    assert before == after, "triage changed a status; a class is not a disposition"


def test_every_pending_row_carries_a_decider_not_just_a_class(table):
    """A class says how a row was measured; a decider says what to act on.

    101 rows sat for a session with a class and nothing else, which reads as a
    backlog but is really a list of questions nobody had been handed. `decide`
    attaches the fact -- and must still not write a status (Error #190).
    """
    labelled = mfp.triage(json.loads(json.dumps(table)))
    entries = labelled.get("declared") or {}
    pending = [e for e in entries.values() if str(e.get("status")) == "pending"]
    if not pending:
        pytest.skip("no pending rows in fixture")
    for entry in pending:
        assert entry.get("decider") in mfp.DECIDERS, (
            entry.get("qualname"), entry.get("decider"))
        assert "decider_evidence" in entry
    counts = labelled.get("decider_counts") or {}
    assert sum(counts.values()) == len(pending), (counts, len(pending))
    assert set(counts) == set(mfp.DECIDERS)

    # A decider pass is a measurement: it must not touch a status either.
    before = {k: str(v.get("status")) for k, v in (table.get("declared") or {}).items()}
    after = {k: str(v.get("status")) for k, v in entries.items()}
    assert before == after, "decide changed a status; a decider is not a disposition"


def test_the_decider_index_is_not_silently_empty():
    """`protocol_or_override == 0` is only a finding if the index is populated.

    A broken walk returns 0 overrides for everything, which reads exactly like
    "nothing is overridden" -- the shape of every unvalidated instrument in this
    repo. So the index is measured, not assumed: it must find the generic hooks
    that are defined in many classes by construction.
    """
    methods, dynamic, by_stem = mfp._decider_index()
    assert len(methods) > 1000, len(methods)
    assert len(by_stem) > 100, len(by_stem)
    assert len(methods.get("run", ())) > 1, methods.get("run")
    assert len(methods.get("detect", ())) > 1, methods.get("detect")
    assert dynamic, "no file reported a computed getattr; the scan is not running"


def test_resolve_module_refuses_to_guess_between_same_stem_modules():
    """Two `db_monitor.py` files must not lend one another's dispatch evidence."""
    by_stem = {"spine": ["src/a/spine.py", "src/b/spine.py"]}
    assert mfp._resolve_module("spine", "a", by_stem) == "src/a/spine.py"
    assert mfp._resolve_module("spine", "", by_stem) is None
    assert mfp._resolve_module("spine", "nope", by_stem) is None
    assert mfp._resolve_module("absent", "", by_stem) is None


def test_an_overridden_name_is_not_allowed_to_read_as_dead(table):
    """The destructive decider must be last. Falsified against the real index.

    `run` is defined by many classes here, so a pending row named `X.run` must
    come back `protocol_or_override`, never `dead_candidate`. If the order in
    `decide` is ever rearranged, this fails.
    """
    mutated = json.loads(json.dumps(table))
    mutated["declared"] = dict(mutated.get("declared") or {})
    mutated["declared"]["synthetic:X.run"] = {
        "module": "spine", "qualname": "X.run", "line": 1, "status": "pending",
        "triage": "no_reference_anywhere", "triage_evidence": [],
    }
    decided = mfp.decide(mutated)
    row = decided["declared"]["synthetic:X.run"]
    assert row["decider"] == "protocol_or_override", row
    assert row["decider_evidence"], row


def _fresh_data_caches():
    """Drop the module caches so a synthetic tree is actually re-scanned."""
    mfp._DATA_INDEX.clear()
    mfp._DATA_FILES_READ = None


def test_a_config_that_production_code_loads_counts_as_a_wire(tmp_path, monkeypatch):
    """A data file is a dispatcher only if code reads it."""
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "wiring.json").write_text(
        '{"alpha": "resolved-by-config"}', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "loader.py").write_text(
        'PATH = "state/wiring.json"\n', encoding="utf-8")
    monkeypatch.setattr(mfp, "REPO", tmp_path)
    _fresh_data_caches()
    try:
        hits = mfp.data_index({"alpha"})
        assert hits.get("alpha"), "a code-loaded config did not count as a wire"
        assert "wiring.json" in hits["alpha"][0]
    finally:
        _fresh_data_caches()


def test_a_data_file_no_code_loads_is_not_a_wire(tmp_path, monkeypatch):
    """Negative control: the same file, with nothing loading it, wires nothing.

    This is the rule that ended the first two attempts: the probe's own
    `NEURAL_MESH_INTEGRATION_MAP.json`, and then graphify's AST cache, each
    named 101 of 101 candidates because they are records OF the tree rather
    than inputs TO it.
    """
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "orphan.json").write_text(
        '{"alpha": 1}', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "unrelated.py").write_text("X = 1\n", encoding="utf-8")
    monkeypatch.setattr(mfp, "REPO", tmp_path)
    _fresh_data_caches()
    try:
        assert mfp.data_index({"alpha"}) == {}, "an unread file wired a function"
    finally:
        _fresh_data_caches()


def test_an_inventory_naming_every_candidate_is_not_a_wire(tmp_path, monkeypatch):
    """Naming many candidates at once is a census, not a wiring declaration."""
    (tmp_path / "state").mkdir()
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
    (tmp_path / "state" / "inventory.json").write_text(
        json.dumps({n: 1 for n in names}), encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "reader.py").write_text(
        'P = "state/inventory.json"\n', encoding="utf-8")
    monkeypatch.setattr(mfp, "REPO", tmp_path)
    _fresh_data_caches()
    try:
        assert mfp.data_index(set(names)) == {}, "an inventory was read as a wire"
        excluded = mfp.data_inventory_exclusions(set(names))
        assert any("inventory.json" in e for e in excluded), excluded
    finally:
        _fresh_data_caches()


def test_the_threshold_scales_with_the_candidate_count():
    assert mfp.data_inventory_threshold(101) == 20
    assert mfp.data_inventory_threshold(3) == mfp.DATA_INVENTORY_MIN


def test_the_probes_own_artifacts_are_excluded_on_this_repo():
    """The regression that matters: the probe must not read its own report.

    Non-vacuous on the real tree. If either guard is removed, this repository's
    audit artifacts come back as wiring for 101 of 101 rows -- which would
    retire the entire backlog in one run, by citing the audit that produced it.
    """
    table = json.loads((Path(mfp.__file__).resolve().parents[1]
                        / "NEURAL_MESH_WIRE_OR_CUT.json").read_text(encoding="utf-8"))
    names = {str(v.get("qualname") or "").rsplit(".", 1)[-1]
             for v in (table.get("declared") or {}).values()
             if str(v.get("status")) == "pending"}
    if not names:
        pytest.skip("no pending rows; this assertion would be vacuous")
    excluded = " | ".join(mfp.data_inventory_exclusions(names))
    assert "NEURAL_MESH_INTEGRATION_MAP.json" in excluded, excluded
    assert "NEURAL_MESH_WIRE_OR_CUT.json" in excluded, excluded
    hits = mfp.data_index(names)
    assert len(hits) < len(names), (
        "every candidate came back data-declared: the probe is reading its own "
        "inventory again (%d of %d)" % (len(hits), len(names)))
