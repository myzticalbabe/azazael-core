#!/usr/bin/env python3
"""MARS claim layer: deterministic taxonomy, additive policy, retained recompute.

Hermetic - no PAMS writes, no network, no model calls. Bundles are built
from the BINARY_ANALYSIS_TYPES dataclasses so the shapes tested are the
shapes the real analyzers produce (proven at HEAD 2026-10-22:
PeHeaders.imports = DLL module names, PeHeaders.exports = function names).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hypothesis import given, settings, strategies as st

from CORE.WORMS.BINARY_ANALYSIS_TYPES import (
    ElfHeaders,
    EntropyResult,
    PeHeaders,
    SectionHeader,
    SymbolEntry,
)
from CORE.WORMS.expansion.mars_claims import (
    CLAIM_FAMILIES,
    DEFAULT_CUTOFFS,
    DEFAULT_POLICY,
    MarsClaim,
    emit_verdict_links,
    evaluate,
    extract_claims,
    extract_export_claims,
    extract_import_claims,
    recompute,
    score_claims,
    verdict_of_score,
)


def _pe(imports=(), exports=(), subsystem="WINDOWS_CUI", sections=None):
    if sections is None:
        sections = [SectionHeader(
            name=".text", virtual_address=0x1000, virtual_size=0x2000,
            raw_size=0x2000, raw_offset=0x600, characteristics=[],
            entropy=6.1)]
    return PeHeaders(
        path="C:/probe/bundle.exe", is_pe=True, machine="AMD64",
        timestamp="2026-10-22T10:00:00", entry_point=0x1400,
        image_base=0x140000000, sections=sections,
        imports=list(imports), exports=list(exports),
        subsystem=subsystem, characteristics=["EXECUTABLE_IMAGE"])


def _entropy(overall, packed=None, sections=None):
    return EntropyResult(
        path="C:/probe/bundle.exe", overall_entropy=overall,
        max_possible=8.0, is_packed=bool(packed) if packed is not None
        else overall > 7.0, section_entropy=dict(sections or {}),
        histogram=[])


def _symbols(exported, undefined=1):
    rows = [SymbolEntry(
        address=0x81000000 + i, name="exported_%02d" % i, symbol_type="T",
        section=".text") for i in range(exported)]
    rows += [SymbolEntry(
        address=0, name="KERNEL32.dll", symbol_type="U", section="IMPORT")
        for _ in range(undefined)]
    return rows


def _packed_bundle():
    """Known shape-faithful bundle used by the deterministic-score tests."""
    return dict(
        pe=_pe(
            imports=["WININET.dll", "kernel32.dll"],
            exports=["VirtualAlloc"],
        ),
        entropy=_entropy(7.71, packed=True, sections={".text": 7.5}),
        symbols=_symbols(exported=26),
    )


def test_known_bundle_scores_known_verdict():
    """Full four-axis bundle -> exact additive score 6.0 -> verdict high.

    wininet 1.0 + suspicious export 1.0 + undefined 1.0 - exported 0.5
    - rich 0.5 + packed 2.5 + section-high 1.5 = 6.0.
    """
    bundle = _packed_bundle()
    result = evaluate(**bundle)
    assert result.score == 6.0, result
    assert result.verdict == "high", result
    families = {c.family for c in result.claims}
    assert families == {"import", "export", "symbol", "entropy"}, families
    # canonical claim order: sorted by (family, name)
    keys = [(c.family, c.name) for c in result.claims]
    assert keys == sorted(keys), keys


def test_claim_removal_changes_verdict():
    """MARS subset finding: dropping one axis may flip the verdict."""
    with_entropy = evaluate(**_packed_bundle())
    without_entropy = evaluate(
        pe=_packed_bundle()["pe"], symbols=_packed_bundle()["symbols"])
    assert with_entropy.verdict == "high"
    # 1.0 + 1.0 + 1.0 - 0.5 - 0.5 = 2.0 -> below the 2.5 cutoff
    assert without_entropy.score == 2.0, without_entropy
    assert without_entropy.verdict == "low"
    assert without_entropy.verdict != with_entropy.verdict


def test_retained_recompute_reproduces_verdict_exactly():
    """Retention contract: persisted claims recompute the same verdict."""
    result = evaluate(**_packed_bundle())
    from_rows = recompute(result.claims)
    from_json = recompute(result.to_json()["claims"])
    for other in (from_rows, from_json):
        assert other.score == result.score
        assert other.verdict == result.verdict
        assert [(c.family, c.name, c.weight) for c in other.claims] == \
            [(c.family, c.name, c.weight) for c in result.claims]


def test_recompute_dedupes_retained_rows():
    """Duplicate retained rows collapse; score cannot be double-fired."""
    single = recompute([MarsClaim("entropy", "entropy_packed_flag", 2.5)])
    doubled = recompute([
        MarsClaim("entropy", "entropy_packed_flag", 2.5),
        MarsClaim("entropy", "entropy_packed_flag", 2.5),
    ])
    assert single.score == 2.5
    assert doubled.score == 2.5


def test_empty_bundle_is_low():
    """Absence of evidence scores as absence of evidence."""
    result = evaluate()
    assert result.score == 0.0
    assert result.verdict == "low"
    assert result.claims == tuple()


def test_fail_open_on_malformed_axes():
    """Garbage axes never raise; missing PE sections still named positively.

    A bare string PE record yields no imports/exports/NATIVE subsystem,
    but also no section list -> header_no_sections 1.5 -> low. A bare
    int ELF record, an object entropy record and a plain-string symbol
    axis contribute nothing.
    """
    result = evaluate("garbage", 42, object(), "not-a-list")
    assert isinstance(result, type(evaluate()))
    assert result.score == 1.5, result
    assert result.verdict == "low"
    assert [(c.family, c.name) for c in result.claims] == \
        [("header", "header_no_sections")]


def test_import_export_matching_is_boundary_safe():
    """Basename DLL match and token API match, both with boundaries."""
    pe = _pe(
        imports=["NotaWininet.dll", "C:/Windows/wininet.DLL", "ws2_32.dll"],
        exports=["NotaVirtualAlloc", "my.VirtualAlloc"],
    )
    claims = extract_claims(pe=pe)
    names = [(c.family, c.name) for c in claims]
    assert names == [
        ("export", "export_suspicious_api"),
        ("import", "import_dll_wininet"),
        ("import", "import_dll_ws2_32"),
    ], names  # canonical order is sorted (family, name)
    assert score_claims(claims) == 2.5  # 1.0 + 0.5 + 1.0
    assert verdict_of_score(score_claims(claims)) == "elevated"


def test_export_api_claims_dedupe():
    """Several suspicious exports fire exactly one export claim."""
    claims = extract_export_claims(
        ["VirtualAlloc", "WriteProcessMemory", "GetProcAddress"])
    assert len(claims) == 1
    assert claims[0].family == "export"
    assert claims[0].name == "export_suspicious_api"
    assert claims[0].weight == 1.0


def test_packed_and_elevated_entropy_are_exclusive():
    """overall > 7.0 fires the packed claim, NOT the elevated one too."""
    packed = list(extract_claims(entropy=_entropy(7.71, packed=True)))
    elevated = list(extract_claims(entropy=_entropy(6.8, packed=False)))
    quiet = list(extract_claims(entropy=_entropy(5.0, packed=False)))
    assert [(c.name, c.weight) for c in packed] == [
        ("entropy_packed_flag", 2.5)]
    assert [(c.name, c.weight) for c in elevated] == [
        ("entropy_overall_elevated", 2.0)]
    assert quiet == []


def test_elf_missing_text_section_claim():
    """ELF parsed but .text absent -> header_family claim fires once."""
    elf = ElfHeaders(
        path="/probe/lib.so", is_elf=True, elf_class="64-bit",
        endianness="little", entry_point=0x1000, phoff=64, shoff=0,
        program_headers=[], section_headers=[{"name": ".data"}])
    claims = extract_claims(elf=elf)
    assert [(c.family, c.name) for c in claims] == [
        ("header", "elf_no_text_section")]
    assert score_claims(claims) == 1.5


def test_verdict_bands_at_exact_cutoffs():
    """Band semantics: >= cutoff goes up, strictly-below stays down."""
    assert verdict_of_score(2.4) == "low"
    assert verdict_of_score(2.5) == "elevated"
    assert verdict_of_score(4.9) == "elevated"
    assert verdict_of_score(5.0) == "high"
    assert DEFAULT_CUTOFFS == (2.5, 5.0)


def test_policy_object_carries_the_table():
    """The additive policy is a fixed, inspectable table."""
    policy = DEFAULT_POLICY
    assert policy.weights["entropy_packed_flag"] == 2.5
    assert policy.weights["symbol_exported_present"] == -0.5
    assert policy.bands == ("low", "elevated", "high")
    assert policy.cutoffs == (2.5, 5.0)


def test_emit_verdict_links_is_fail_open_out_of_band():
    """The emitter cannot be exercised against the live ledger in unit
    scope (hermetic), but its module import and signature stay callable
    without writing anything when the verdict has no claims."""
    from CORE.WORMS.expansion import mars_claims as mc
    empty = evaluate()
    rows = mc.emit_verdict_links.__doc__ and emit_verdict_links(empty, "unit")
    assert rows == tuple()  # no claims -> no rows, even out of band


# -- Hypothesis properties (testing-paradigm Rule 1 / Engram #784) -------
# Behavior contracts the unit fixtures cannot cover alone: order/duplicate
# invariance of the additive score, the retention recompute algebra, and
# the verdict-band mapping across the whole score domain.

_UNIQUE_CLAIMS = st.lists(
    st.tuples(
        st.sampled_from(CLAIM_FAMILIES),
        st.text(alphabet="abcdefghkmnprstuwz_", min_size=3, max_size=12),
        st.floats(
            min_value=-20.0, max_value=20.0, allow_nan=False, allow_infinity=False
        ),
    ).map(lambda t: MarsClaim(*t)),
    max_size=12,
    unique_by=lambda c: (c.family, c.name),
)


@settings(max_examples=100, deadline=None)
@given(_UNIQUE_CLAIMS)
def test_score_invariant_under_order_and_duplicates(rows):
    """Scoring canonicalizes on (family, name) before summing, so any
    permutation and any exact-duplicate injection scores identically."""
    expected = score_claims(rows)
    assert score_claims(list(reversed(rows))) == expected
    assert score_claims(rows + rows) == expected


@settings(max_examples=100, deadline=None)
@given(_UNIQUE_CLAIMS)
def test_fragment_recompute_reassembly_reproduces_verdict(rows):
    """Recompute minus one claim, then that claim re-added, reproduces the
    exact score/verdict/claim set; the removed weight accounts for the
    fragment delta."""
    total = recompute(rows)
    for dropped in rows:
        kept = [
            c for c in rows
            if (c.family, c.name) != (dropped.family, dropped.name)
        ]
        fragment = recompute(kept)
        assert abs(total.score - (fragment.score + dropped.weight)) <= 1e-9
        reassembled = recompute(list(fragment.claims) + [dropped])
        assert reassembled.score == total.score
        assert reassembled.verdict == total.verdict
        assert [(c.family, c.name, c.weight) for c in reassembled.claims] == [
            (c.family, c.name, c.weight) for c in total.claims
        ]


@settings(max_examples=100, deadline=None)
@given(
    st.floats(
        min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
    ),
    st.floats(
        min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
    ),
)
def test_verdict_bands_monotone_and_boundary_exact(base, step):
    """Bands are right-continuous: a higher-or-equal score never yields a
    lower band, and each cutoff opens its band exactly (a hair below the
    cutoff still falls in the strictly lower band)."""
    bands = DEFAULT_POLICY.bands
    assert bands.index(verdict_of_score(base)) <= bands.index(
        verdict_of_score(base + step)
    )
    for cutoff in DEFAULT_CUTOFFS:
        assert bands.index(verdict_of_score(cutoff)) == bands.index(
            verdict_of_score(cutoff - 1e-6)
        ) + 1
