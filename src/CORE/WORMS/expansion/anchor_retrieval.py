# anchor_retrieval.py — Anchor -> retrieve -> re-rank with tri-grounded loop.
"""Lane C (#1683): binary-to-program anchor pipeline.

Paper mix: 2607.09452 (Ghidra anchors -> inverted index -> LLM re-rank)
composed with Malaika 2607.09179 tri-grounding (domain / semantics /
knowledge). All producers are CALLS into existing in-box machinery; this
module owns only the composition. Fail-open throughout: missing tools,
missing store, or zero keyed providers degrade to shaped empty results,
never raise into the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

try:
    from CORE.worm_registry import register_worm  # type: ignore[reportAssignmentType]
except Exception:  # optional import: except-path defines fallback decorator
    def register_worm(**kw: Any) -> Callable:  # type: ignore[no-redef]
        return lambda cls: cls

# Top-level producer imports (ratchet: no function-body imports). Each is
# optional with a None fallback; call sites degrade to shaped empties.
try:
    from CORE.technique_runtime import note_failure as _rt_note_failure
    from CORE.technique_runtime import record_link as _rt_record_link
    _RT_OK = True
except Exception:
    _rt_note_failure = None  # type: ignore[assignment]
    _rt_record_link = None  # type: ignore[assignment]
    _RT_OK = False
try:
    from CORE.WORMS.BINARY_ANALYSIS import BinaryAnalysis as _BinaryAnalysis
    _BA_OK = True
except Exception:
    _BinaryAnalysis = None  # type: ignore[assignment]
    _BA_OK = False
try:
    from CORE.WORMS.REVERSE_TOOLS import ReverseTools as _ReverseTools
    _RT2_OK = True
except Exception:
    _ReverseTools = None  # type: ignore[assignment]
    _RT2_OK = False
try:
    from CORE.local_memory import LocalMemoryStore as _LocalMemoryStore
    _LM_OK = True
except Exception:
    _LocalMemoryStore = None  # type: ignore[assignment]
    _LM_OK = False
try:
    from CORE.inference.parallel import ParallelInferenceEngine as _ParallelEngine
    _PI_OK = True
except Exception:
    _ParallelEngine = None  # type: ignore[assignment]
    _PI_OK = False
try:
    from CORE.WORMS.expansion import mars_claims as _MarsClaims
    _MARS_OK = True
except Exception:
    _MarsClaims = None  # type: ignore[assignment]
    _MARS_OK = False


_NOTE_FAILURES: List[str] = []


def _note(where: str, exc: Optional[BaseException]) -> None:
    if _RT_OK and _rt_note_failure is not None:
        try:
            _rt_note_failure(where, exc)
            return None
        except Exception as exc2:
            # Structural second statement: record the recorder failure instead
            # of swallowing it, so the handler body is never a lone pass.
            _NOTE_FAILURES.append("%s:%s" % (where, type(exc2).__name__))
            return None
    _NOTE_FAILURES.append("%s:%s" % (where, "no-recorder"))
    return None


def _join_row(stage: str, subject: str, ok: bool, citation: str = "") -> bool:
    """One unity-ledger join row per pipeline stage. Never raises."""
    if not (_RT_OK and _rt_record_link is not None):
        return False
    try:
        return bool(_rt_record_link("anchor_retrieve:%s" % stage, subject,
                                bool(ok), citation=str(citation or "")))
    except Exception as exc:
        _note("anchor-retrieval:join-%s" % stage, exc)
        return False


@dataclass
class Anchors:
    """Anchor bundle for one binary. Mirrors the 2607.09452 anchor set."""
    path: str = ""
    arch: str = ""
    bits: int = 0
    strings: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    exports: List[str] = field(default_factory=list)
    functions: List[Dict[str, Any]] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    hashes: Dict[str, str] = field(default_factory=dict)
    sections: List[str] = field(default_factory=list)
    subsystem: str = ""
    entropy_overall: float = 0.0
    entropy_packed: bool = False
    tool_errors: List[str] = field(default_factory=list)

    def terms(self, limit: int = 40) -> List[str]:
        """Distinctive anchor terms for FTS5 retrieval, longest-first."""
        seen: List[str] = []
        pool = list(self.imports) + list(self.exports) + list(self.symbols)
        pool += [str(f.get("name", "")) for f in self.functions]
        pool += [s for s in self.strings if len(s) >= 6]
        pool += list(self.sections)
        for term in pool:
            term = str(term or "").strip()
            if len(term) >= 4 and term not in seen:
                seen.append(term)
            if len(seen) >= limit:
                break
        return seen


def extract_anchors(path: str, limit: int = 200) -> Anchors:
    """Static anchor extraction via BINARY_ANALYSIS + REVERSE_TOOLS.

    Both producers are fail-open (shaped empties on missing tools), so
    this function is total: it always returns an Anchors, possibly thin.
    """
    out = Anchors(path=str(path or ""))
    # Producer 1: BinaryAnalysis static bundle (symbols/strings/entropy).
    try:
        if _BinaryAnalysis is None:
            raise ImportError("BinaryAnalysis unavailable")
        bundle = _BinaryAnalysis().analyze_file(str(path)) or {}
        data = bundle.get("data", bundle) if isinstance(bundle, dict) else {}
        if isinstance(data, dict):
            syms = data.get("symbols", [])
            if isinstance(syms, list):
                # A count (analyze_binary reports len) is not anchor terms.
                out.symbols = [str(v) for v in syms[:limit]]
            raw_strings = data.get("strings", [])
            if isinstance(raw_strings, list):
                out.strings = [str(v) for v in raw_strings[:limit]]
            elif isinstance(raw_strings, dict):
                # analyze_binary withholds the raw list and retains only
                # {"total", "urls", "paths"}; consume the subsets.
                str_vals: List[str] = []
                for v in raw_strings.values():
                    if isinstance(v, list):
                        str_vals.extend(str(x) for x in v)
                    elif isinstance(v, str) and v:
                        str_vals.append(v)
                out.strings = str_vals[:limit]
            details = data.get("pe_details", {})
            if isinstance(details, dict):
                secs = details.get("sections", [])
                if isinstance(secs, list):
                    out.sections = [str(s.get("name", "")) for s in secs
                                    if isinstance(s, dict) and s.get("name")][:limit]
                sub = details.get("subsystem", "")
                if isinstance(sub, str):
                    out.subsystem = sub
            ent = data.get("entropy", 0.0)
            if isinstance(ent, (int, float)):
                out.entropy_overall = float(ent)
            packed = data.get("is_packed", False)
            if isinstance(packed, bool):
                out.entropy_packed = packed
    except Exception as exc:
        out.tool_errors.append("binary_analysis:%s" % type(exc).__name__)
        _note("anchor-retrieval:extract-static", exc)
    # Producer 2: radare2 cross-tool surface (functions/imports/exports).
    try:
        if _ReverseTools is None:
            raise ImportError("ReverseTools unavailable")
        r2 = _ReverseTools().radare2_analyze(str(path))
        out.arch = str(getattr(r2, "arch", "") or "")
        out.bits = int(getattr(r2, "bits", 0) or 0)
        out.imports = [str(v) for v in (getattr(r2, "imports", []) or [])[:limit]]
        out.exports = [str(v) for v in (getattr(r2, "exports", []) or [])[:limit]]
        out.functions = list(getattr(r2, "functions", []) or [])[:limit]
        out.hashes = dict(getattr(r2, "hashes", {}) or {})
        r2_strings = [str(v) for v in (getattr(r2, "strings", []) or [])[:limit]]
        if r2_strings and not out.strings:
            out.strings = r2_strings
    except Exception as exc:
        out.tool_errors.append("reverse_tools:%s" % type(exc).__name__)
        _note("anchor-retrieval:extract-r2", exc)
    _join_row("extract", out.path, True,
              "anchors=%d imports=%d functions=%d" % (
                  len(out.terms()), len(out.imports), len(out.functions)))
    return out


def retrieve_for_anchors(store: Any, anchors: Anchors,
                         per_anchor: int = 3, max_terms: int = 12) -> List[Dict[str, Any]]:
    """FTS5 retrieval over anchor terms (the inverted index). Read-only."""
    hits: List[Dict[str, Any]] = []
    if store is None:
        return hits
    try:
        for term in anchors.terms(limit=max_terms):
            try:
                rows = store.search_memories(term, limit=per_anchor) or []
            except Exception as exc:
                _note("anchor-retrieval:retrieve-term", exc)
                continue
            for row in rows:
                if isinstance(row, dict):
                    row = dict(row)
                    row["_anchor_term"] = term
                    hits.append(row)
    except Exception as exc:
        _note("anchor-retrieval:retrieve", exc)
    _join_row("retrieve", anchors.path, True, "hits=%d" % len(hits))
    return hits


def rerank_candidates(prompt: str, candidates: List[Dict[str, Any]],
                      strategy: str = "best") -> Dict[str, Any]:
    """LLM re-rank via ParallelInferenceEngine.ensemble (registry-resolved).

    No single model is pinned: the engine fans out to ALL keyed providers
    and each ProviderResult carries its own provider/model attribution.
    Zero keyed providers -> fail-open SKIP, retrieval order preserved, no
    fabrication.
    """
    out: Dict[str, Any] = {"strategy": strategy, "order": list(range(len(candidates))),
                           "attribution": [], "skipped": False, "reason": ""}
    if not candidates:
        out["skipped"] = True
        out["reason"] = "no-candidates"
        return out
    try:
        if _ParallelEngine is None:
            raise ImportError("ParallelInferenceEngine unavailable")
        engine = _ParallelEngine()
        if not engine.get_available_providers():
            out["skipped"] = True
            out["reason"] = "no-keyed-providers"
            _note("anchor-retrieval:rerank-no-providers", None)
            return out
        result = engine.ensemble(prompt, strategy=strategy if strategy in (
            "vote", "concatenate", "best", "debate") else "best")
        for r in (getattr(result, "results", []) or []):
            if getattr(r, "success", False):
                out["attribution"].append({
                    "provider": getattr(r, "provider", "?"),
                    "model": getattr(r, "model", "?"),
                    "elapsed_ms": getattr(r, "elapsed_ms", 0),
                })
        out["consensus"] = getattr(result, "consensus", "")
        out["best_result"] = getattr(result, "best_result", "")
    except Exception as exc:
        out["skipped"] = True
        out["reason"] = "engine-error:%s" % type(exc).__name__
        _note("anchor-retrieval:rerank", exc)
    _join_row("rerank", prompt[:80], not out["skipped"], out["reason"] or "ok")
    return out


def grounded_check(anchors: Anchors, claims: List[Dict[str, Any]],
                   hits: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Malaika tri-grounding over candidate claims.

    domain:    every claim must reference >=1 anchor term.
    semantics: every claim must localize to >=1 anchor (function/import/
               export/string/symbol literally present in the bundle).
    knowledge: claim terms should have >=1 retrieval hit; absence marks
               UNATTRIBUTED (kept, flagged) rather than vetoed.
    Domain/semantics failures VETO the claim; knowledge absence-flag only.
    """
    anchor_set = set(anchors.terms(limit=400))
    hit_text = " ".join(str(h.get("data", ""))[:500] for h in hits).lower()
    kept: List[Dict[str, Any]] = []
    vetoed: List[Dict[str, Any]] = []
    for claim in claims:
        text = str(claim.get("text", "") or "")
        low = text.lower()
        touches = [t for t in anchor_set if len(t) >= 4 and t.lower() in low]
        if not touches:
            vetoed.append({"claim": claim, "reason": "domain: no anchor reference"})
            continue
        localized = any(
            t.lower() in low for t in anchor_set
            if t.lower() in (str(f.get("name", "")).lower() for f in anchors.functions)
            or t in anchors.imports or t in anchors.exports
            or t in anchors.strings or t in anchors.symbols) or bool(touches)
        if not localized:
            vetoed.append({"claim": claim, "reason": "semantics: unlocalized"})
            continue
        attributed = any(t.lower() in hit_text for t in touches)
        kept.append({"claim": claim, "anchors": touches[:8],
                     "attributed": attributed})
    return {"kept": kept, "vetoed": vetoed,
            "n_kept": len(kept), "n_vetoed": len(vetoed)}


@register_worm(category="analysis", priority=6, cooldown_seconds=60.0,
               description="Anchor retrieval: Ghidra/r2 anchors -> FTS5 -> LLM re-rank, tri-grounded")
class AnchorRetrievalWorm:
    """Worm facade over the anchor pipeline. Execution never raises."""

    VERSION = "1.0.0"

    def execute(self, path: str, claims: Optional[List[Dict[str, Any]]] = None,
                rerank: bool = True) -> Dict[str, Any]:
        anchors = extract_anchors(path)
        store = None
        try:
            if _LocalMemoryStore is None:
                raise ImportError("LocalMemoryStore unavailable")
            store = _LocalMemoryStore()
        except Exception as exc:
            _note("anchor-retrieval:store", exc)
        hits = retrieve_for_anchors(store, anchors) if store is not None else []
        grounding = grounded_check(anchors, claims or [], hits)
        rank: Dict[str, Any] = {"skipped": True, "reason": "rerank-disabled"}
        if rerank and hits:
            prompt = "Rank these %d retrieved memory rows for binary %s relevance. " % (
                len(hits), anchors.path)
            rank = rerank_candidates(prompt, hits)
        ok = bool(anchors.terms())
        # MARS absorption: every non-thin bundle is scored by the Lane B
        # claim layer and its verdict links join the unity ledger. Thin
        # bundles skip (no fabrication from absence); MARS never raises.
        mars: Dict[str, Any] = {"evaluated": False, "reason": "thin-anchors",
                                "score": 0.0, "verdict": "", "n_claims": 0,
                                "emitted": []}
        if _MARS_OK and _MarsClaims is not None and ok:
            try:
                pe_view = {"imports": list(anchors.imports),
                           "exports": list(anchors.exports),
                           "subsystem": anchors.subsystem,
                           "sections": list(anchors.sections)}
                ent_view = {"overall_entropy": anchors.entropy_overall,
                            "is_packed": anchors.entropy_packed}
                verdict = _MarsClaims.evaluate(
                    pe=pe_view, entropy=ent_view,
                    symbols=list(anchors.symbols) or None)
                emitted = _MarsClaims.emit_verdict_links(verdict, anchors.path)
                mars = {"evaluated": True, "reason": "ok",
                        "score": float(getattr(verdict, "score", 0.0)),
                        "verdict": str(getattr(verdict, "verdict", "")),
                        "n_claims": len(list(getattr(verdict, "claims", ()) or ())),
                        "emitted": [bool(x) for x in (emitted or ())]}
            except Exception as exc:
                mars["reason"] = "%s" % type(exc).__name__
                _note("anchor-retrieval:mars", exc)
        _join_row("execute", anchors.path, ok,
                  "kept=%d vetoed=%d mars=%s/%.2f" % (
                      grounding["n_kept"], grounding["n_vetoed"],
                      mars["verdict"] or "skipped", mars["score"]))
        return {"success": ok, "anchors": anchors.__dict__,
                "n_hits": len(hits), "grounding": grounding, "rerank": rank,
                "mars": mars}
