# coding: utf-8
"""Label-lattice controls for `CORE.camel.capabilities`.

Why this file exists
--------------------
The whole CaMeL build rests on one property: **a label never disappears unless an
explicit policy check says it may**. If `join` could return `TRUST` while either
side was tainted, or if `label_of` looked only at a container's first element,
every downstream rule would be checking a value that no longer remembers where it
came from — and the rules would pass while the attack succeeds.

So these tests are mostly about the *negative* case: a tainted input must stay
tainted through every derivation the package offers. `TRUST` operations are
tested too, because a lattice that taints everything is as useless as one that
taints nothing: a run where every value is untrusted tells a reviewer nothing
about where the untrusted data actually was.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.camel.capabilities import (  # noqa: E402
    Label,
    Labeled,
    SENSITIVE,
    TRUST,
    ToolCapability,
    is_sensitive,
    join_all,
    join_labels,
    label_of,
    origin,
    seal,
    unwrap,
)


class TestLattice:
    def test_trust_is_the_empty_label(self):
        assert TRUST.trusted and not TRUST.tainted
        assert TRUST.origins == frozenset()

    def test_origin_is_tainted(self):
        label = origin("https://example.test/a")
        assert label.tainted
        assert label.origins == frozenset({"https://example.test/a"})

    def test_join_is_idempotent_associative_and_commutative(self):
        a, b, c = origin("a"), origin("b"), origin("c")
        assert a | a == a
        assert (a | b) | c == a | (b | c)
        assert a | b == b | a
        assert (a | b).origins == frozenset({"a", "b"})

    def test_join_with_trust_keeps_the_taint(self):
        tainted = origin("page")
        assert (TRUST | tainted) == tainted
        assert (tainted | TRUST) == tainted

    def test_there_is_no_untaint_operation(self):
        """The absence is the control: no method on Label returns TRUST."""
        public = [name for name in dir(Label) if not name.startswith("_")]
        for forbidden in ("clear", "clean", "untaint", "trust", "strip_label"):
            assert forbidden not in public, (
                "Label gained %r — a value must not be able to shed its origin"
                % forbidden)

    def test_bad_origin_is_rejected(self):
        with pytest.raises(ValueError):
            Label(frozenset({""}))
        with pytest.raises(ValueError):
            Label(frozenset({None}))  # type: ignore[arg-type]

    def test_join_rejects_a_non_label(self):
        with pytest.raises(TypeError):
            TRUST | "page"  # type: ignore[operator]

    def test_describe_truncates_a_long_origin_list(self):
        label = join_labels(*[origin("src%d" % i) for i in range(6)])
        shown = label.describe(limit=2)
        assert shown.startswith("untrusted(")
        assert "+4" in shown, "describe must say how many origins it hid: %r" % shown
        assert "src0" in shown


class TestLabeled:
    def test_derive_keeps_the_label(self):
        page = Labeled.untrusted("hello", "https://example.test")
        assert page.derive(page.value.upper()).tainted

    def test_derive_can_widen_but_not_narrow(self):
        page = Labeled.untrusted("x", "page")
        wider = page.derive("y", origin("tool:other"))
        assert wider.label.origins == frozenset({"page", "tool:other"})
        assert page.derive("z", None).label == page.label

    def test_join_keeps_both_origins(self):
        left = Labeled.untrusted("a", "one")
        right = Labeled.untrusted("b", "two")
        assert left.join(right).label.origins == frozenset({"one", "two"})

    def test_trusted_value_is_not_tainted(self):
        assert not Labeled.trust(42).tainted


class TestLabelOf:
    def test_plain_value_is_trusted(self):
        assert label_of("hello") == TRUST
        assert label_of({"a": [1, 2]}) == TRUST

    def test_one_tainted_element_taints_the_list(self):
        """The bug this prevents: `[page_text]` read as a clean list of one."""
        items = [Labeled.trust("clean"), Labeled.untrusted("dirty", "page")]
        assert label_of(items).origins == frozenset({"page"})

    def test_dict_keys_and_values_both_count(self):
        page = Labeled.untrusted("key", "page")
        assert label_of({page: "value"}).tainted
        assert label_of({"key": Labeled.untrusted("v", "page")}).tainted

    def test_set_and_tuple_are_joined(self):
        page = Labeled.untrusted("x", "page")
        assert label_of((1, page)).tainted
        assert label_of({1, 2}) == TRUST
        assert label_of(frozenset({"a", "b"})) == TRUST


class TestUnwrap:
    def test_unwrap_strips_only_labels(self):
        page = Labeled.untrusted("text", "page")
        tree = {"a": [page, 2], "b": (Labeled.trust("c"),)}
        assert unwrap(tree) == {"a": ["text", 2], "b": ("c",)}
        assert page.value == "text", "unwrap must not mutate the Labeled"

    def test_unwrap_preserves_labels_on_the_original(self):
        page = Labeled.untrusted("text", "page")
        unwrap(page)
        assert page.tainted


class TestSeal:
    def test_seal_taints_clean_values(self):
        """A parsed integer is still attacker-chosen."""
        sealed = seal([7, 8], origin("page"))
        assert [s.value for s in sealed] == [7, 8]
        assert all(s.tainted for s in sealed)

    def test_seal_never_narrows_an_existing_label(self):
        already = Labeled.untrusted("x", "other")
        sealed = seal([already], origin("page"))[0]
        assert sealed.label.origins == frozenset({"other", "page"})


class TestToolCapability:
    def test_acting_capabilities_are_sensitive(self):
        for capability in (ToolCapability.NET_OUT, ToolCapability.EXEC,
                           ToolCapability.FS_WRITE, ToolCapability.MSG_SEND):
            assert capability in SENSITIVE
            assert is_sensitive({capability})

    def test_read_only_capabilities_are_not(self):
        assert not is_sensitive(ToolCapability.NONE)
        assert not is_sensitive({ToolCapability.NET_READ})
        assert not is_sensitive({ToolCapability.NET_READ, ToolCapability.LLM})

    def test_unknown_capability_fails_closed(self):
        """A tool declared with a capability nobody modelled is treated as acting."""
        assert is_sensitive({"quantum.teleport"})


def test_join_all_over_empty_is_trust():
    assert join_all([]) == TRUST
