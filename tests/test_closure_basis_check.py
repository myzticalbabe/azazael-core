# coding: utf-8
"""The closure checker must name the convention it counted, and must refuse to
count when it cannot detect one.

EVERY FIXTURE BELOW IS TRANSCRIBED FROM A REAL DOCUMENT at HEAD ``3191c3ff``, and
that is the point: a checker whose detection rules were invented alongside its
fixtures would be validated by its author's assumptions. The three defects this
file pins down were all measured:

  * ``MASTER_CAPABILITY_TODO.md`` carries the ``⬜`` glyph in a LEGEND line as well
    as in 133 status rows. A glyph counter that does not restrict itself to table
    rows counts the legend as an item. ``GLYPH_DOC`` contains the real legend line
    verbatim for exactly this reason -- the test asserts it is not counted.
  * ``TODO_315T_BUILD.md`` puts its ``DONE`` token in DIFFERENT COLUMNS: ``**G-4**``
    carries it in the Est. cell, ``**T-16**`` carries it inside the task text. A
    parser that reads one fixed column reports T-16 as owed forever.
  * ``CODEBASE_AUDIT_TODO.md`` uses three bases at once (``- [ ]`` steps, ``- [x]``
    history, ``✅ DONE`` and ``⏳ Pending`` table rows). Any single-label classifier
    is wrong about it, so the tool must report each basis and label its total a sum.

The negative control is ``UNPARSED_DOC``: a substantial plan with no markers of any
kind. Its correct answer is "I cannot measure this", not "0 open".
"""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

from closure_basis_check import (  # noqa: E402 - after sys.path
    STUB_NONBLANK, analyse, classify, main,
)

#: VERBATIM rows from CODEBASE_AUDIT_TODO.md: `- [x]` history at L159-L171 and
#: `- [ ]` steps at L199-L213.
CHECKBOX_DOC = """\
### Step 1: \u2705 Post-Session Cleanup \u2014 DONE (June 2)
- [x] pip cache purge \u2014 749 MB freed
- [x] Windows Temp cleanup \u2014 ~2.5 GB freed

### Step 4: Parallel Execution
- [ ] Add path traversal protection to all Vault endpoints
- [ ] Implement structured error logging
- [ ] Integration tests for parallel execution (FreeThreadPool, work-stealing)
"""

#: VERBATIM from MASTER_CAPABILITY_TODO.md: the legend is L6 and it contains all
#: three glyphs; the status rows are L33 onward and carry exactly one each.
GLYPH_DOC = """\
> **Stat:** \u2705 = Done, \u2b1c = Not started, \u26a0\ufe0f = In progress/Blocked

| # | Task | Priority | Status |
|---|------|----------|--------|
| 1.1 | **Worm API standardization** | P0 | \u2b1c |
| 1.2 | Fix 44 instantiation errors | P0 | \u2b1c |
| 3.1 | blocked item | P1 | \u26a0\ufe0f |
| 6.7 | verified item | P1 | \u2705 |
"""

#: VERBATIM from PLATFORM_DEPLOYMENT.md L11-L14. The row carries FOUR glyphs across
#: four different columns; only the first is the item's status.
TRAFFIC_LIGHT_DOC = """\
| Platform | Status | Python | Free-Threading | Worms | G: Drive | CI |
|----------|--------|--------|---------------|-------|----------|-----|
| **Windows 11** | \U0001f7e2 Active | 3.14.5t | \u2705 GIL-disabled | 88 | \u2705 Mounted | \U0001f7e1 Partial |
| **Bare Metal Ubuntu 26.04** | \U0001f534 Not started | \u2014 | \u2014 | \u2014 | \U0001f534 NFS/SMB | \U0001f534 None |
"""

#: VERBATIM from TODO_315T_BUILD.md L168-L175 and L355-L358. G-4's DONE sits in the
#: Est. column; T-16's DONE sits inside the task text. Both are complete.
IMPLICIT_DOC = """\
| # | Task | Why (evidence) | Depends | Est. |
|---|---|---|---|---|
| **G-1** | **Build the accelerator sidecar** | the port in \u00a75.1 | \u2014 | M |
| **G-4** | **Benchmark before claiming** | \u00a75.4 | G-1 | **DONE** |
| **T-13** | Host remediation programme | the blueprint asks for adoption | L |
| **T-16** | Swallow remediation: **DONE 2026-10-10 \u2014 premise was stale.** | an unrecorded swallow is indistinguishable from success | L |
"""

#: VERBATIM from CODEBASE_AUDIT_TODO.md: `⏳ Pending` and `✅ DONE` rows at L594-L630,
#: `- [ ]` steps at L199-L213. Three bases, one file.
MIXED_DOC = """\
## Auto-remediation

| ID | Task | Hours | Priority | Status |
|----|------|-------|----------|--------|
| AR-5 | Switch from sys.path.insert to editable pip install | 3h | \U0001f7e1 Medium | \u23f3 Pending |
| 1 | Phase 22+ | Post-session cleanup | P0 | \u2705 DONE |

- [ ] Add path traversal protection to all Vault endpoints
- [x] pip cache purge \u2014 749 MB freed
"""

#: The negative control. Substantial, has headings and prose, and uses NONE of the
#: conventions. Correct answer: UNPARSED. The defect: reporting "0 open".
UNPARSED_DOC = """\
# Remaining work

This document describes what is still owed on the platform.

The deployment matrix is out of date and nobody has reconciled it.

Three environments were planned, one is live, and the other two have no owner.

Nobody has decided whether the second environment should be retired instead.

The numbers in the appendix were measured before the interpreter change.

They have not been re-measured since.

A reader who trusts them is trusting a snapshot that no longer describes the tree.

That is the whole problem with this document: it is still believed.
"""


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ── the invariant ─────────────────────────────────────────────────────────────

def test_unparsed_document_never_reports_zero_open(tmp_path, capsys):
    """The control. If the UNPARSED branch is removed, this test fails."""
    path = _write(tmp_path, "plan.md", UNPARSED_DOC)

    exit_code = main([str(path)])
    out = capsys.readouterr().out

    record = classify(path)
    assert record["status"] == "UNPARSED"
    assert exit_code == 2, "an unmeasured document must fail the run"
    assert "UNPARSED" in out
    assert "0 open" not in out, (
        "a file whose convention was not detected must never be reported as "
        "'0 open' -- that reading falsified closures on record")
    assert record["bases"] == {}


def test_stub_is_distinguished_from_unparsed(tmp_path):
    """A two-line file owes nothing; a 9-line plan with no markers is unmeasured."""
    stub = _write(tmp_path, "stub.md", "# Empty\n\nnothing owed yet\n")
    assert classify(stub)["status"] == "STUB"

    body = "\n".join("prose line %d" % n for n in range(STUB_NONBLANK + 2))
    assert classify(_write(tmp_path, "long.md", body))["status"] == "UNPARSED"


def test_missing_file_is_its_own_status(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.md"

    exit_code = main([str(missing)])
    out = capsys.readouterr().out

    record = classify(missing)
    assert record["status"] == "MISSING"
    assert exit_code == 2
    assert "MISSING" in out
    assert "0 open" not in out
    assert "Traceback" not in out


# ── one test per basis ───────────────────────────────────────────────────────

def test_checkbox_basis(tmp_path):
    record = classify(_write(tmp_path, "c.md", CHECKBOX_DOC))

    assert record["status"] == "PARSED"
    base = record["bases"]["checkbox"]
    assert base["open"] == 3
    assert base["done"] == 2


def test_glyph_basis_ignores_the_legend_line(tmp_path):
    record = classify(_write(tmp_path, "g.md", GLYPH_DOC))

    assert record["status"] == "PARSED"
    base = record["bases"]["status-glyph"]
    # 2 not started + 1 in progress; the legend's own three glyphs are NOT items.
    assert base["open"] == 3
    assert base["not_started"] == 2
    assert base["in_progress"] == 1
    assert base["done"] == 1
    assert base["vocabulary"] == ["square"]


def test_glyph_basis_counts_a_row_once(tmp_path):
    record = classify(_write(tmp_path, "t.md", TRAFFIC_LIGHT_DOC))

    base = record["bases"]["status-glyph"]
    # Two platform rows, not the seven glyphs they contain.
    assert base["not_started"] + base["in_progress"] + base["done"] == 2
    assert base["extra_glyph_cells"] > 0
    assert base["vocabulary"] == ["traffic-light"]


def test_implicit_basis_finds_done_in_any_column(tmp_path):
    record = classify(_write(tmp_path, "i.md", IMPLICIT_DOC))

    assert record["status"] == "PARSED"
    base = record["bases"]["implicit-open"]
    assert base["open"] == 2, "G-1 and T-13 are unmarked, so they are owed"
    assert base["done"] == 2, (
        "G-4 carries **DONE** in its Est. cell and T-16 carries **DONE 2026-10-10** "
        "inside its task text; a single-column parser misses the second")


# ── mixed basis ──────────────────────────────────────────────────────────────

def test_mixed_document_reports_each_basis_and_labels_the_sum(tmp_path, capsys):
    path = _write(tmp_path, "mixed.md", MIXED_DOC)
    record = classify(path)

    assert record["status"] == "MIXED"
    assert sorted(record["bases"]) == ["checkbox", "implicit-open", "status-glyph"]
    # The AR-5 row is both a glyph row and an identifier row, so the sum
    # double-counts it by construction -- which is why it must be labelled.
    assert record["overlap_rows"] == 1

    main([str(path)])
    out = capsys.readouterr().out
    assert "MIXED" in out
    assert "LABELLED SUM" in out
    assert "no single open count" in out


# ── exit-code contract and self-declared totals ──────────────────────────────

def test_open_items_are_not_a_failure(tmp_path):
    """Work owed is a successful measurement; an unmeasured file is not."""
    assert main([str(_write(tmp_path, "c.md", CHECKBOX_DOC))]) == 0


def test_self_declared_total_disagreement_is_surfaced(tmp_path, capsys):
    """MASTER_CAPABILITY_TODO.md declares 98 items and holds 137 numbered rows."""
    text = GLYPH_DOC + "\n**Total items:** 98\n"
    path = _write(tmp_path, "d.md", text)

    record = classify(path)
    assert record["declared"]["value"] == 98

    main([str(path)])
    out = capsys.readouterr().out
    assert "DISAGREES" in out, (
        "the file's own arithmetic was 39 items short on record; a parser that "
        "trusts the summary under-reports silently")
    assert "neither is resolved here" in out


def test_generated_file_is_not_counted(tmp_path):
    text = "<!-- generated by tools/mesh_function_preflight.py -->\n" + GLYPH_DOC
    record = classify(_write(tmp_path, "gen.md", text))

    assert record["status"] == "GENERATED"
    assert record["counts_suppressed"] is True


def test_analyse_sees_every_basis_present():
    """Detection is not first-match: all bases in the body are reported."""
    found = analyse(MIXED_DOC)["bases"]
    assert set(found) == {"checkbox", "status-glyph", "implicit-open"}
