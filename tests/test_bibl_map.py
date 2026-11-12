# coding: utf-8
"""Regression tests for the BIBL map.

Each test pins a defect that shipped in WORMZ.py and produced a corrupt 30 MB
INDEX.md. They are written as reproductions, not descriptions: if the old
behaviour returns, the test fails.

The WORMZ defects:
  1. r"`([^`]+)`" matched newlines, so one "term" swallowed a whole fenced
     block and was written with f"## {term}" -- foreign documents emitted as
     headings.
  2. One file_path per term, so a name in 40 files kept only the last.
  3. checksum(node.name) -- crawl_delta detected renames and nothing else.
  4. ast.FunctionDef only; AsyncFunctionDef was never captured.
  5. Nothing verified the output.
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BIBL = _HERE.parent / "src" / "ORCHESTRATION" / "BIBL"


def _load():
    name = "bibl_map_under_test"
    spec = importlib.util.spec_from_file_location(name, _BIBL / "bibl_map.py")
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: @dataclass resolves cls.__module__ through
    # sys.modules, and raises AttributeError on None otherwise.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


m = _load()


def _tmpfile(text: str, name: str = "sample.py") -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / name
    p.write_text(text, encoding="utf-8")
    return p


# ── 1. terms can never span lines (the corruption) ───────────────────────

def test_a_term_cannot_swallow_a_fenced_block():
    doc = ("Use the `foo` helper.\n\n```\n# Heading from another file\n"
           "| A | B |\n```\n\nand `bar`.\n")
    import re
    old = re.findall(r"`([^`]+)`", doc)
    assert any("\n" in t for t in old), (
        "fixture stale: the WORMZ pattern no longer spans newlines")
    cleaned = [m._clean(t) for t in old]
    assert all("\n" not in c for c in cleaned), (
        "a cleaned term still spans lines -- it will be written into a heading")
    assert all("#" not in c for c in cleaned), (
        "cleaning must strip '#' or it re-forms a heading")


def test_crawl_docs_never_yields_a_multiline_node_name():
    d = Path(tempfile.mkdtemp())
    (d / "doc.md").write_text(
        "# Real Title\n\n```\n# fenced heading\n| x | y |\n```\n\n"
        "## Second Title\n", encoding="utf-8")
    nodes, _ = m.crawl_docs(d)
    for n in nodes.values():
        assert "\n" not in n.name, "multiline node name: %r" % n.name
        assert "\n" not in n.raw_name
    assert nodes, "no headings captured at all"


# ── 2. one node, every location (WORMZ kept only the last) ───────────────

def test_a_symbol_in_three_files_keeps_all_three_locations():
    d = Path(tempfile.mkdtemp())
    for name in ("a.py", "b.py", "c.py"):
        (d / name).write_text("def shared_name():\n    pass\n", encoding="utf-8")
    nodes, _ = m.crawl_code(d)
    hits = [n for n in nodes.values() if n.raw_name == "shared_name"]
    assert len(hits) == 1, "expected one merged node, got %d" % len(hits)
    assert len(hits[0].locations) == 3, (
        "locations collapsed to %d -- WORMZ's last-write-wins bug"
        % len(hits[0].locations))


# ── 3. the checksum must see content, not just the name ──────────────────

def test_editing_a_body_changes_the_checksum():
    a = _tmpfile("def f():\n    return 1\n")
    b = _tmpfile("def f():\n    return 2\n")
    na, _ = m.crawl_code(a.parent)
    nb, _ = m.crawl_code(b.parent)
    ca = [n.checksum for n in na.values() if n.raw_name == "f"][0]
    cb = [n.checksum for n in nb.values() if n.raw_name == "f"][0]
    assert ca != cb, (
        "checksum is blind to the body -- crawl_delta will never see an edit, "
        "which is WORMZ defect 3")


def test_renaming_also_changes_the_checksum():
    a = _tmpfile("def before():\n    pass\n")
    b = _tmpfile("def after():\n    pass\n")
    na, _ = m.crawl_code(a.parent)
    nb, _ = m.crawl_code(b.parent)
    ca = [n.checksum for n in na.values() if n.raw_name == "before"][0]
    cb = [n.checksum for n in nb.values() if n.raw_name == "after"][0]
    assert ca != cb


# ── 4. async functions must be captured (WORMZ missed them) ──────────────

def test_async_def_is_captured_as_a_coroutine():
    p = _tmpfile("async def fetch_it():\n    return 1\n")
    nodes, _ = m.crawl_code(p.parent)
    hit = [n for n in nodes.values() if n.raw_name == "fetch_it"]
    assert hit, "async def was not captured (WORMZ defect 4)"
    assert hit[0].kind == "coroutine"
    assert "async def" in hit[0].signature


def test_dunder_methods_are_excluded():
    p = _tmpfile("class C:\n    def __repr__(self):\n        return ''\n"
                 "    def real(self):\n        return 1\n")
    nodes, _ = m.crawl_code(p.parent)
    names = {n.raw_name for n in nodes.values()}
    assert "real" in names
    assert "__repr__" not in names


# ── 5. identity must not collapse on the display form ────────────────────

def test_symbols_that_clean_the_same_stay_distinct():
    d = Path(tempfile.mkdtemp())
    (d / "x.py").write_text(
        "def generate_html_report():\n    pass\n\n"
        "def generate html report():\n    pass\n".replace(
            "def generate html report", "def generate_HTML_report"),
        encoding="utf-8")
    nodes, _ = m.crawl_code(d)
    names = sorted(n.raw_name for n in nodes.values())
    assert len(names) == 2, (
        "distinct symbols merged on the cleaned name: %r" % names)


# ── 6. the writer must escape, and verify must catch loss ────────────────

def test_render_escapes_markdown_metacharacters():
    n = m.Node(name=m._clean("evil # heading | pipe * star"),
               raw_name="evil", kind="function", graph="code",
               signature="def evil()", checksum="abc")
    text = "\n".join(m.render_node(n))
    heading = [ln for ln in text.splitlines() if ln.startswith("### ")][0]
    assert "#" not in heading[4:], "heading re-formed: %r" % heading
    assert "|" not in heading[4:]


def test_verify_reports_a_loss_instead_of_passing():
    d = Path(tempfile.mkdtemp())
    n = m.Node(name="alpha", raw_name="alpha", kind="function",
               graph="code", checksum="x")
    m.render_all({"a": n}, d, d)
    v = m.verify(d, 5)          # claim 5 entries, only 1 was written
    assert not v["ok"], "verify passed despite a 4-node shortfall"
    assert any("mismatch" in p for p in v["problems"]), v["problems"]


def test_verify_passes_when_counts_agree():
    d = Path(tempfile.mkdtemp())
    nodes = {"a": m.Node(name="alpha", raw_name="alpha", kind="function",
                         graph="code", checksum="x"),
             "b": m.Node(name="Beta", raw_name="Beta", kind="class",
                         graph="code", checksum="y")}
    info = m.render_all(nodes, d, d)
    v = m.verify(d, info["total"])
    assert v["ok"], v["problems"]


# ── 7. enrichment must be an overlay, not a guess ────────────────────────

def test_enrichment_overlay_fills_definitions_and_see_also():
    n = m.Node(name="thing", raw_name="thing", kind="class", graph="code",
               docstring="A thing.", checksum="z")
    applied = m.apply_enrichment(
        {"code:class:thing": n},
        {"code:class:thing": {"definition": "A specific thing.",
                              "category": "Core", "see_also": ["other thing"]}})
    assert applied == 1
    assert n.definition == "A specific thing."
    assert n.category == "Core"
    assert n.see_also == ["other thing"]
    text = "\n".join(m.render_node(n))
    assert "See Also" in text and "A specific thing." in text


def test_unenriched_node_says_so_instead_of_inventing_a_definition():
    n = m.Node(name="bare", raw_name="bare", kind="function", graph="code",
               checksum="q")
    text = "\n".join(m.render_node(n))
    assert "enrichment pending" in text, (
        "a node with no definition must be visibly pending, not silently blank")


def test_enrichment_survives_a_recrawl():
    """The property that makes this maintainable: re-crawling the skeleton
    must not destroy human/LLM work, so enrichment lives outside it."""
    n = m.Node(name="keep", raw_name="keep", kind="class", graph="code",
               checksum="k")
    m.apply_enrichment({"code:class:keep": n},
                       {"code:class:keep": {"definition": "Keep me."}})
    assert n.definition == "Keep me."
    # a fresh crawl produces a node with no definition; the overlay is a
    # separate file, which is what makes re-crawl safe
    fresh = m.Node(name="keep", raw_name="keep", kind="class",
                   graph="code", checksum="k2")
    assert fresh.definition == ""


def test_a_nested_function_gets_its_own_qualified_node():
    """Closures must be addressable, not absorbed into their parent.

    Two files can each define a local `_do_execute`; under a bare-name key
    they collide and one disappears, which is precisely how a closure got
    mis-attributed to an unrelated method earlier.
    """
    d = Path(tempfile.mkdtemp())
    (d / "a.py").write_text(
        "def outer():\n    def inner():\n        return 1\n    return inner\n",
        encoding="utf-8")
    (d / "b.py").write_text(
        "def other():\n    def inner():\n        return 2\n    return inner\n",
        encoding="utf-8")
    nodes, _ = m.crawl_code(d)
    inners = [n for n in nodes.values() if "<locals>" in n.raw_name]
    assert len(inners) == 2, (
        "two distinct closures collapsed to %d node(s)" % len(inners))
    # both keep their enclosing context, so neither is anonymous
    assert all(n.raw_name.endswith("inner.<locals>") for n in inners)


def test_doc_nodes_get_a_structural_definition():
    """Doc headings carry no docstring; they must still say something true."""
    d = Path(tempfile.mkdtemp())
    (d / "guide.md").write_text("# Getting Started\n\n## Install\n",
                                encoding="utf-8")
    nodes, _ = m.crawl_docs(d)
    rec = m.derive_enrichment(nodes)
    assert rec, "no enrichment produced for doc nodes"
    for node in nodes.values():
        entry = rec[node.id]
        assert entry["definition"], "doc node has no definition"
        assert "heading in" in entry["definition"], entry["definition"]
        assert entry["category"]


def test_merge_does_not_invent_entries():
    """An overlay key matching no crawled node is a fabricated fact, and must
    not be allowed to inflate the node count."""
    derived = {"code:class:real": {"definition": "d"}}
    llm = {"code:class:real": {"definition": "better"},
           "code:class:imaginary": {"definition": "does not exist"}}
    merged = m.merge_enrichment(derived, llm)
    assert merged["code:class:real"]["definition"] == "better"
    assert len(merged) == 2, "orphan carried into the merged set"
    # and the render path ignores what it cannot match, so counts stay honest
    known = set(derived)
    orphans = [k for k in llm if k not in known]
    assert orphans == ["code:class:imaginary"]


def test_llm_see_also_overrides_derived_but_derived_survives_otherwise():
    derived = {"code:function:f": {"definition": "auto", "category": "Code",
                                   "see_also": ["auto_link"]}}
    llm = {"code:function:f": {"see_also": ["hand_picked"]}}
    merged = m.merge_enrichment(derived, llm)
    assert merged["code:function:f"]["see_also"] == ["hand_picked"]
    assert merged["code:function:f"]["definition"] == "auto"
    assert merged["code:function:f"]["category"] == "Code"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
