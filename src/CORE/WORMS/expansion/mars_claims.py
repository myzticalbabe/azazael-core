# coding: utf-8
"""
MARS — Model-free Analysis from Retained Signs.

Lane B claim layer over BINARY_ANALYSIS static evidence bundles
(finding ref 2610.09553 carried by Lane B brief, Engram plan #1682):
a deterministic claim taxonomy plus an additive scoring policy over
*identical* static bundles, so every verdict can be recomputed from
the retained claims alone — no model call, no network, no re-analysis.

Why a claim layer instead of another analyzer. A verdict produced by a
model call is unaccountable: nothing in the record explains the number,
and reproducing it costs another call that may answer differently. MARS
anchors the verdict to evidence: each bundle field becomes an atomic
claim with a fixed weight in a fixed policy table, the score is the
sum of the weights of the fired claims, and the verdict is a band of
that score. All weights and cutoffs are multiples of 0.25 on magnitudes
small enough that every partial sum is exactly representable in
binary64: summation order cannot change the result, and the same
bundle always yields the same verdict.

Retention contract. ``evaluate()`` returns the claims next to the
verdict. Persist those claims (``MarsVerdict.to_json``) and any later
process can recompute the verdict bit-identically via ``recompute()``.
Retained ROW weights are authoritative at recompute time, so the
verdict survives policy edits: fresh evaluations pick up the new
policy, retained ones stay reproducible. Removing claims changes the
score, and may change the verdict — that is the point: retained claims
are sufficient AND necessary inputs for the verdict they produced.

Fail-open. Malformed or absent bundle parts contribute zero claims and
never raise into the caller; extraction notes the failure in the shared
failure ledger. An empty bundle scores 0.0 ("low") — absence of
evidence is scored as absence of evidence, not packed.

Families and indicators. Criteria reuse the BINARY_ANALYSIS
definitions where one exists, notably is_packed = overall entropy
> 7.0, so the two layers can never disagree. Shape taken from HEAD
(proven 2026-10-22): ``PeHeaders.imports`` carries DLL module names and
``PeHeaders.exports`` carries exported function names, so indicators
are DLL-borne for import and API-name-borne for export::

    import    security-relevant DLL imports   wininet/urlmon +1.0,
              ws2_32 +0.5, crypt32 +1.0 (basename token match)
    export    analyzed DLL exporting injection APIs           +1.0
    symbol    undefined-import symbols present                +1.0
              exported symbols present / plentiful            -0.5 each
    header    PE NATIVE subsystem / zero PE sections       +0.5/+1.5
              ELF parsed with no .text section                +1.5
    entropy   packed flag (overall > 7.0)                     +2.5
              overall > 6.5 (elevated, sub-packed)            +2.0
              any section > 7.0                               +1.5

The packed and elevated entropy claims are deliberately exclusive
(``elif``): >7.0 fires exactly one of them, so one bundle cannot
double-score for the same bytes. Bands (additive score -> verdict):
< 2.5 "low", 2.5 <= s < 5.0 "elevated", >= 5.0 "high".
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, Tuple

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# technique_runtime is CALL-ONLY (brief #1682) — bind its join once at
# import scope (ratchet: no import_inside_function), fail-open when the
# host runs without it.
try:
    from CORE.technique_runtime import record_link as _record_link
except ImportError:                               # join not available here
    _record_link = None

__all__ = [
    "FAMILY_IMPORT", "FAMILY_EXPORT", "FAMILY_SYMBOL", "FAMILY_HEADER",
    "FAMILY_ENTROPY", "CLAIM_FAMILIES", "MarsPolicy", "MarsClaim",
    "MarsVerdict", "DEFAULT_POLICY", "DEFAULT_CUTOFFS", "DEFAULT_BANDS",
    "ENTROPY_PACKED_THRESHOLD", "ENTROPY_ELEVATED_THRESHOLD",
    "DEFAULT_EXPORT_RICH_COUNT", "extract_claims",
    "extract_import_claims", "extract_export_claims",
    "extract_symbol_claims", "extract_header_claims",
    "extract_elf_header_claims", "extract_entropy_claims",
    "score_claims", "verdict_of_score", "verdict_from_claims",
    "evaluate", "recompute", "emit_verdict_links",
]

# -- claim families (deterministic taxonomy) --------------------------

FAMILY_IMPORT = "import"
FAMILY_EXPORT = "export"
FAMILY_SYMBOL = "symbol"
FAMILY_HEADER = "header"
FAMILY_ENTROPY = "entropy"

CLAIM_FAMILIES = (
    FAMILY_IMPORT,
    FAMILY_EXPORT,
    FAMILY_SYMBOL,
    FAMILY_HEADER,
    FAMILY_ENTROPY,
)

# -- thresholds (same criterion set as BINARY_ANALYSIS where one exists)

ENTROPY_PACKED_THRESHOLD = 7.0    # BINARY_ANALYSIS_TOOLS.analyze_entropy
ENTROPY_ELEVATED_THRESHOLD = 6.5  # sub-packed but elevated-entropy zone
DEFAULT_EXPORT_RICH_COUNT = 25    # many exports demean the packing story

# -- deterministic additive policy ------------------------------------


@dataclass(frozen=True)
class MarsPolicy:
    """Fixed weights per claim name plus verdict bands.

    The additive score is the sum of the fired claims' weights. Bands
    are verdict names named in ``bands`` such that ``bands[k]`` covers
    scores from ``cutoffs[k]`` upward, with ``bands[0]`` covering
    everything below ``cutoffs[0]``.
    """

    weights: Dict[str, float]
    cutoffs: Tuple[float, ...]
    bands: Tuple[str, ...]


DEFAULT_CUTOFFS: Tuple[float, ...] = (2.5, 5.0)
DEFAULT_BANDS: Tuple[str, ...] = ("low", "elevated", "high")

DEFAULT_WEIGHTS: Dict[str, float] = {
    # import family — DLL-borne indicators (shape: PeHeaders.imports)
    "import_dll_wininet": 1.0,
    "import_dll_urlmon": 1.0,
    "import_dll_ws2_32": 0.5,
    "import_dll_crypt32": 1.0,
    # export family — suspicious API names in PeHeaders.exports
    "export_suspicious_api": 1.0,
    # symbol family
    "symbol_undefined_present": 1.0,
    "symbol_exported_present": -0.5,
    "symbol_exported_rich": -0.5,
    # header family
    "header_native_subsystem": 0.5,
    "header_no_sections": 1.5,
    "elf_no_text_section": 1.5,
    # entropy family
    "entropy_packed_flag": 2.5,
    "entropy_overall_elevated": 2.0,
    "entropy_section_high": 1.5,
}

DEFAULT_POLICY = MarsPolicy(
    weights=DEFAULT_WEIGHTS,
    cutoffs=DEFAULT_CUTOFFS,
    bands=DEFAULT_BANDS,
)

# import DLL indicator: normalized DLL basename -> (claim, weight)
_IMPORT_DLLS: Dict[str, str] = {
    "wininet": "import_dll_wininet",
    "urlmon": "import_dll_urlmon",
    "ws2_32": "import_dll_ws2_32",
    "crypt32": "import_dll_crypt32",
}

# export API indicators (boundary token match, case-insensitive)
_EXPORT_APIS: Tuple[str, ...] = (
    "VirtualAlloc",
    "VirtualAllocEx",
    "WriteProcessMemory",
    "CreateRemoteThread",
    "LoadLibrary",
    "GetProcAddress",
)

# dataclass SymbolEntry.is_exported covers these nm types
_EXPORTED_TYPES = frozenset(("T", "t", "D", "d", "B", "b", "R", "r"))


# -- claims ------------------------------------------------------------


@dataclass(frozen=True)
class MarsClaim:
    """One atomic, deterministic indicator from a static bundle."""

    family: str
    name: str
    weight: float
    evidence: str = ""

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarsVerdict:
    """Scored verdict with the retained claims that produced it."""

    score: float
    verdict: str
    claims: Tuple[MarsClaim, ...]

    def to_json(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "verdict": self.verdict,
            "claims": [c.to_json() for c in self.claims],
        }


def _get(record: Any, key: str, default: Any = None) -> Any:
    """Duck-typed field access over dataclass instances or plain dicts."""
    try:
        if isinstance(record, dict):
            return record.get(key, default)
        return getattr(record, key, default)
    except Exception as exc:
        note_failure("mars_claims:_get", exc)
        return default


def _claim(family: str, name: str, evidence: str = "") -> MarsClaim:
    """Fire one claim with the CURRENT policy weight (extraction time)."""
    return MarsClaim(
        family=family,
        name=name,
        weight=DEFAULT_POLICY.weights.get(name, 0.0),
        evidence=evidence,
    )


def _dedupe_sorted(claims: Iterable[MarsClaim]) -> Tuple[MarsClaim, ...]:
    """One claim per (family, name); canonical (sorted) order."""
    seen: Dict[Tuple[str, str], MarsClaim] = {}
    for c in claims:
        seen.setdefault((c.family, c.name), c)
    return tuple(seen[k] for k in sorted(seen))


def _dll_basename(imp: Any) -> str:
    """Normalize one imports-list entry to its DLL basename token."""
    if not isinstance(imp, str):
        return ""
    base = imp.strip().split("\\")[-1].split("/")[-1].strip().lower()
    for ext in (".dll", ".drv", ".sys", ".ocx", ".ax"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def _api_tokens(name: Any) -> set:
    """Boundary tokens of an export/function name, lowercase."""
    if not isinstance(name, str):
        return set()
    out = set()
    token = ""
    for ch in name:
        if ch.isalnum() or ch == "_":
            token += ch.lower()
        elif token:
            out.add(token)
            token = ""
    if token:
        out.add(token)
    return out


# -- family extractors (each is fail-open at its public entry) --------


def extract_import_claims(imports: Any) -> Tuple[MarsClaim, ...]:
    """Claims from PE DLL import names (basename indicator match)."""
    try:
        out: list[MarsClaim] = []
        for imp in list(imports or ()):
            dll = _dll_basename(imp)
            if dll and dll in _IMPORT_DLLS:
                out.append(_claim(
                    FAMILY_IMPORT, _IMPORT_DLLS[dll], evidence=str(imp)))
        return _dedupe_sorted(out)
    except Exception as exc:
        note_failure("mars_claims:extract_import", exc)
        return tuple()


def extract_export_claims(exports: Any) -> Tuple[MarsClaim, ...]:
    """Claims from exported function names (suspicious API exports)."""
    try:
        out: list[MarsClaim] = []
        for exp in list(exports or ()):
            tokens = _api_tokens(exp)
            for api in _EXPORT_APIS:
                if api.lower() in tokens:
                    out.append(_claim(
                        FAMILY_EXPORT, "export_suspicious_api",
                        evidence=str(exp)))
                    break
        return _dedupe_sorted(out)
    except Exception as exc:
        note_failure("mars_claims:extract_export", exc)
        return tuple()


def extract_symbol_claims(symbols: Any) -> Tuple[MarsClaim, ...]:
    """Claims from nm-style symbol rows (undefined vs exported mix)."""
    try:
        rows = tuple(symbols or ())
    except Exception as exc:
        note_failure("mars_claims:extract_symbol_list", exc)
        rows = tuple()
    try:
        undefined = False
        exported = 0
        for sym in rows:
            try:
                stype = _get(sym, "symbol_type", "")
                if stype == "U":
                    undefined = True
                if _get(sym, "is_exported", False) or stype in _EXPORTED_TYPES:
                    exported += 1
            except Exception as exc2:
                note_failure("mars_claims:extract_symbol_row", exc2)
        out: list[MarsClaim] = []
        if undefined:
            out.append(_claim(
                FAMILY_SYMBOL, "symbol_undefined_present",
                evidence="undefined import symbols present"))
        if exported >= DEFAULT_EXPORT_RICH_COUNT:
            out.append(_claim(
                FAMILY_SYMBOL, "symbol_exported_rich",
                evidence="exported=%d" % exported))
        if exported > 0:
            out.append(_claim(
                FAMILY_SYMBOL, "symbol_exported_present",
                evidence="exported=%d" % exported))
        return _dedupe_sorted(out)
    except Exception as exc:
        note_failure("mars_claims:extract_symbol", exc)
        return tuple()


def extract_header_claims(pe: Any) -> Tuple[MarsClaim, ...]:
    """Claims from PE header records (subsystem + section sanity)."""
    try:
        if pe is None:
            return tuple()
        out: list[MarsClaim] = []
        subsystem = _get(pe, "subsystem", "")
        if subsystem == "NATIVE":
            out.append(_claim(
                FAMILY_HEADER, "header_native_subsystem",
                evidence="subsystem=NATIVE"))
        sections = _get(pe, "sections", None)
        if not sections:
            out.append(_claim(
                FAMILY_HEADER, "header_no_sections", evidence="0 sections"))
        return _dedupe_sorted(out)
    except Exception as exc:
        note_failure("mars_claims:extract_header", exc)
        return tuple()


def extract_elf_header_claims(elf: Any) -> Tuple[MarsClaim, ...]:
    """Claims from ELF header records (missing .text section)."""
    try:
        if elf is None or not _get(elf, "is_elf", False):
            return tuple()
        names = {_get(sec, "name", "")
                 for sec in _get(elf, "section_headers", [])}
        names.discard("")
        names.discard(None)
        if ".text" not in names:
            out = [_claim(
                FAMILY_HEADER, "elf_no_text_section",
                evidence="sections=%r" % sorted(str(n) for n in names))]
        else:
            out = []
        return _dedupe_sorted(out)
    except Exception as exc:
        note_failure("mars_claims:extract_elf_header", exc)
        return tuple()


def extract_entropy_claims(entropy: Any) -> Tuple[MarsClaim, ...]:
    """Claims from entropy records (same thresholds as the host analyzer)."""
    try:
        if entropy is None:
            return tuple()
        out: list[MarsClaim] = []
        overall = float(_get(entropy, "overall_entropy", 0.0))
        packed = _get(entropy, "is_packed", overall > ENTROPY_PACKED_THRESHOLD)
        if packed:
            out.append(_claim(
                FAMILY_ENTROPY, "entropy_packed_flag",
                evidence="overall=%.2f" % overall))
        elif overall > ENTROPY_ELEVATED_THRESHOLD:
            out.append(_claim(
                FAMILY_ENTROPY, "entropy_overall_elevated",
                evidence="overall=%.2f" % overall))
        sections = _get(entropy, "section_entropy", {}) or {}
        for sec_name in sorted(sections):
            try:
                if float(sections[sec_name]) > ENTROPY_PACKED_THRESHOLD:
                    out.append(_claim(
                        FAMILY_ENTROPY, "entropy_section_high",
                        evidence="section %s=%.2f" % (
                            sec_name, float(sections[sec_name]))))
                    break  # one firing section names the evidence
            except (TypeError, ValueError) as exc2:
                note_failure("mars_claims:extract_entropy_section", exc2)
        return _dedupe_sorted(out)
    except Exception as exc:
        note_failure("mars_claims:extract_entropy", exc)
        return tuple()


# -- combined extraction -----------------------------------------------


def extract_claims(
    pe: Any = None,
    elf: Any = None,
    entropy: Any = None,
    symbols: Any = None,
) -> Tuple[MarsClaim, ...]:
    """Full taxonomy extraction over a static bundle.

    Every axis is optional; missing axes contribute nothing. Import and
    export lists are read off the PE record (their only producer at
    HEAD). Never raises — malformed parts are noted in the failure
    ledger and drop out of the claim set.
    """
    claims: list[MarsClaim] = []
    claims.extend(extract_import_claims(_get(pe, "imports") if pe is not None else None))
    claims.extend(extract_export_claims(_get(pe, "exports") if pe is not None else None))
    claims.extend(extract_symbol_claims(symbols))
    claims.extend(extract_header_claims(pe))
    claims.extend(extract_elf_header_claims(elf))
    claims.extend(extract_entropy_claims(entropy))
    return _dedupe_sorted(claims)


# -- scoring and verdicts ----------------------------------------------


def score_claims(
    claims: Iterable[MarsClaim], policy: MarsPolicy = DEFAULT_POLICY
) -> float:
    """Deterministic additive score: retained row weights are authoritative."""
    try:
        return _score(_dedupe_sorted(claims))
    except Exception as exc:
        note_failure("mars_claims:score", exc)
        return 0.0


def _score(rows: Iterable[MarsClaim]) -> float:
    """Sum retained row weights (exact: weights are 0.25-multiples)."""
    total = 0.0
    for c in rows:
        try:
            total += float(c.weight)
        except (TypeError, ValueError) as exc:
            note_failure("mars_claims:_score_weight", exc)
    return total


def verdict_of_score(
    score: float, policy: MarsPolicy = DEFAULT_POLICY
) -> str:
    """Band lookup: ``bands[k]`` covers ``cutoffs[k]`` and above; strictly
    below ``cutoffs[0]`` falls to ``bands[0]``."""
    try:
        idx = bisect_right(policy.cutoffs, float(score))
        return policy.bands[min(idx, len(policy.bands) - 1)]
    except Exception as exc:
        note_failure("mars_claims:verdict_of_score", exc)
        return DEFAULT_BANDS[0]


def verdict_from_claims(
    claims: Iterable[MarsClaim], policy: MarsPolicy = DEFAULT_POLICY
) -> MarsVerdict:
    """Score the retained claims and band the score. No bundle needed."""
    rows = _dedupe_sorted(claims)
    value = _score(rows)
    return MarsVerdict(
        score=value,
        verdict=verdict_of_score(value, policy),
        claims=rows,
    )


def evaluate(
    pe: Any = None,
    elf: Any = None,
    entropy: Any = None,
    symbols: Any = None,
    policy: MarsPolicy = DEFAULT_POLICY,
) -> MarsVerdict:
    """Extract claims from a bundle and produce the scored verdict."""
    try:
        claims: Tuple[MarsClaim, ...] = extract_claims(
            pe=pe, elf=elf, entropy=entropy, symbols=symbols)
    except Exception as exc:
        note_failure("mars_claims:evaluate", exc)
        claims = tuple()
    return verdict_from_claims(claims, policy)


def recompute(
    claims: Iterable[MarsClaim | Dict[str, Any]] = tuple(),
    policy: MarsPolicy = DEFAULT_POLICY,
) -> MarsVerdict:
    """Verdict recompute from RETAINED claims (dicts or claims).

    The retention contract: feed this the claims persisted next to an
    earlier ``evaluate()`` result and the verdict reproduces exactly —
    identical bundles, identical claims, identical verdict, no model
    call. Duplicate retained rows collapse on (family, name) identity,
    so writing claims twice cannot double-score.
    """
    try:
        rows: list[MarsClaim] = []
        for row in claims:
            if isinstance(row, MarsClaim):
                rows.append(row)
            elif isinstance(row, dict):
                rows.append(MarsClaim(
                    family=str(row.get("family", "")),
                    name=str(row.get("name", "")),
                    weight=float(row.get("weight", 0.0)),
                    evidence=str(row.get("evidence", "")),
                ))
        out = verdict_from_claims(rows, policy)
    except Exception as exc:
        note_failure("mars_claims:recompute", exc)
        out = verdict_from_claims(tuple(), policy)
    return out


# -- unity-ledger join emission (technique_runtime is call-only) -------


def emit_verdict_links(
    verdict: MarsVerdict,
    subject: str,
    citation: str = "",
    worm: str = "expansion.mars_claims",
) -> Tuple[bool, ...]:
    """One technique_runtime.record_link join row per active family.

    Hook identity: ``mars_claim:<family>`` (brief #1682); subject is the
    analyzed path/label. Row ``ok`` records the DIRECTION of the
    family's evidence against the verdict: True when the final verdict
    is ``low`` (benign pass-through, info severity), False when the
    verdict went non-low on that family's data (warning severity). One
    joining reader can therefore filter adverse evidence by ok=False
    while every row still names its producer via ``worm``. Families
    with no claims emit nothing (no fabrication). Never raises.
    """
    fams = {c.family for c in verdict.claims}
    ok_flag = verdict.verdict == "low"
    results: list[bool] = []
    base = str(citation or "") or (
        "score=%.2f verdict=%s" % (verdict.score, verdict.verdict))
    for fam in CLAIM_FAMILIES:
        if fam not in fams:
            continue
        try:
            if _record_link is None:
                raise RuntimeError("technique_runtime.record_link unavailable")
            results.append(bool(_record_link(
                "mars_claim:%s" % fam,
                str(subject or ""),
                ok_flag,
                citation="%s family=%s claims=%d" % (
                    base, fam, sum(1 for c in verdict.claims
                                   if c.family == fam)),
                worm=str(worm))))
        except Exception as exc:
            note_failure("mars_claims:emit_links", exc)
            results.append(False)
    return tuple(results)
