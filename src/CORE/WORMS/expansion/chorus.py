# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: PROPOSER (Chorus)

Chorus extends variation beyond sampling. Every operator today — GA, CMA-ES, DE,
TPE — samples *numbers* inside a search space someone else defined. Chorus is the
first operator that proposes changes to the space itself, reasoning from the
observed failure modes rather than from a distribution.

It is a FunSearch-style operator over the already-installed inference stack
(`src/CORE/inference/providers.py` — 16 providers, circuit-broken, with
`inference_cache.py` for provenance). It reads a digest of what has been going
wrong and answers with structural edits drawn from Morph's operator set:

    {"op": "nest", "gene": "learning_rate", "reason": "flat trajectories from a
     fixed rate; a 3-point schedule lets the learner anneal"}

Hard rules, all three of which are about not letting a language model steer:

1. **Budget-capped.** `budget` proposals per session, hard stop. It is a voice,
   not the driver.
2. **Fail-open.** Any error — no key, no network, malformed JSON, hallucinated
   gene — yields an empty proposal list. The caller must never notice.
3. **Validated.** Proposed genomes pass through `morph.viability`, so a
   hallucinated kwarg cannot enter the population. The model's output is a
   *suggestion*; the viability filter is the authority.

It runs CPU/network-side, so it does not compete with the Oracle for XPU memory.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .morph import (GeneSpec, Genome, STRUCTURAL_OPS, TRAINER_KWARGS,
                    viability, NON_GENE_KWARGS)

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = ["Chorus", "FailureDigest", "parse_proposals"]

PROPOSAL_OPS = ("add_gene", "drop_gene", "rewire_gene", "nest", "tune")

_SYSTEM = (
    "You are a structural search operator for a hyperparameter evolution loop. "
    "You do NOT return numbers. You return STRUCTURAL edits to the search space. "
    "Answer with a single JSON array and nothing else."
)


class FailureDigest:
    """Compact, evidence-backed summary of what the search is failing at.

    Built from artifacts the pipeline already writes, so Chorus is grounded in
    measurement rather than in the model's priors.
    """

    __slots__ = ("holdout_failures", "pinned_genes", "flat_fraction", "starved_arms",
                 "genome_dims", "best_fitness", "notes")

    def __init__(self, holdout_failures: int = 0, pinned_genes: Optional[Sequence[str]] = None,
                 flat_fraction: float = 0.0, starved_arms: Optional[Sequence[str]] = None,
                 genome_dims: Optional[Sequence[int]] = None,
                 best_fitness: Optional[float] = None,
                 notes: Optional[Sequence[str]] = None):
        self.holdout_failures = int(holdout_failures)
        self.pinned_genes = list(pinned_genes or [])
        self.flat_fraction = float(flat_fraction)
        self.starved_arms = list(starved_arms or [])
        self.genome_dims = list(genome_dims or [])
        self.best_fitness = best_fitness
        self.notes = list(notes or [])

    @classmethod
    def from_analysis(cls, peak: Optional[dict] = None,
                      allocator_report: Optional[Dict[str, dict]] = None,
                      top_rows: Optional[Sequence[dict[str, Any]]] = None) -> "FailureDigest":
        """Adapt the artifacts the monitor and analyzer already produce."""
        peak = peak or {}
        holdout = int(peak.get("holdout_fail") or peak.get("holdout_failures") or 0)
        pinned = peak.get("pinned_genes") or peak.get("pinned") or []
        flat = float(peak.get("flat_fraction") or peak.get("flat_frac") or 0.0)
        starved = [a for a, v in (allocator_report or {}).items()
                   if float(v.get("weight", 1.0)) < 0.05]
        dims: List[int] = []
        for r in (top_rows or []):
            try:
                dims.append(int(r.get("dimension", 0)))
            except (TypeError, ValueError) as exc:
                note_failure("chorus:L90", exc)
        best = None
        if top_rows:
            try:
                best = max(float(f) for r in top_rows if (f := r.get("fitness")) is not None)
            except (TypeError, ValueError) as exc:
                note_failure("chorus:L96", exc)
                best = None
        return cls(holdout, pinned, flat, starved, dims, best)

    def to_dict(self) -> dict:
        return {"holdout_failures": self.holdout_failures,
                "pinned_genes": self.pinned_genes, "flat_fraction": self.flat_fraction,
                "starved_arms": self.starved_arms, "genome_dims": self.genome_dims,
                "best_fitness": self.best_fitness, "notes": self.notes}

    def render(self) -> str:
        bits = ["Observed failure modes:"]
        if self.holdout_failures:
            bits.append("- %d holdout evaluations FAILED after a margin-based promotion "
                        "(the snapshot pool is being overfitted)." % self.holdout_failures)
        if self.pinned_genes:
            bits.append("- genes pinned at their bounds: %s (box optimum on the surface)."
                        % ", ".join(self.pinned_genes))
        if self.flat_fraction:
            bits.append("- %.0f%% of cycles produced flat trajectories (no improvement after gen 3)."
                        % (100.0 * self.flat_fraction))
        if self.starved_arms:
            bits.append("- operators starved by the allocator: %s." % ", ".join(self.starved_arms))
        if self.genome_dims:
            bits.append("- genome dimensions seen: %s." % sorted(set(self.genome_dims)))
        if self.best_fitness is not None:
            bits.append("- best fitness so far: %.4f." % self.best_fitness)
        bits.extend("- %s" % n for n in self.notes)
        return "\n".join(bits)


def _spec_line(s: GeneSpec) -> str:
    if s.kind == "choice":
        return "%s: choice%s -> kwarg %s" % (s.name, s.choices, s.kwargs)
    if s.kind == "schedule":
        return "%s: schedule[%.3g..%.3g] %d points -> kwarg %s" % (
            s.name, s.lo, s.hi, s.points, s.kwargs)
    if s.kind == "bool":
        return "%s: bool -> kwarg %s" % (s.name, s.kwargs)
    return "%s: %s[%.4g..%.4g] -> kwarg %s" % (s.name, s.kind, s.lo, s.hi, s.kwargs)


def _build_prompt(digest: FailureDigest, genome: Optional[Genome],
                  pool: Sequence[GeneSpec], max_ops: int) -> str:
    lines = [digest.render(), "", "Current genome:"]
    if genome is not None and genome.dimension:
        lines.append(json.dumps(genome.to_dict(), default=str))
    else:
        lines.append("{}")

    lines.append("")
    lines.append("Available gene pool (candidate dimensions that may be ADDED):")
    for s in pool:
        lines.append("- " + _spec_line(s))

    if genome is not None and genome.dimension:
        lines.append("")
        lines.append("Current genes (may be dropped / rewired / nested):")
        for s in genome.specs:
            lines.append("- " + _spec_line(s))

    lines += [
        "",
        "Trainer kwargs that a gene may target (no others are valid):",
        ", ".join(k for k in TRAINER_KWARGS if k not in NON_GENE_KWARGS),
        "",
        "Reply with a JSON array of at most %d objects. Each object:" % max_ops,
        '  {"op": one of %s,' % list(PROPOSAL_OPS),
        '   "gene": "<gene name>",',
        '   "new_gene": "<pool gene name, only for add_gene>",',
        '   "value": <number, only for tune>,',
        '   "reason": "<one short sentence citing the failure mode>"}',
        "",
        "Prefer proposals that explain a listed failure mode. Return [] if none apply.",
    ]
    return "\n".join(lines)


def parse_proposals(text: str, limit: int = 8) -> List[dict]:
    """Tolerant JSON extraction. Models wrap output in prose and code fences."""
    if not text:
        return []
    cleaned = re.sub(r"```(?:json)?", "", str(text)).strip()
    candidates: List[str] = []
    # try the whole string, then each bracketed region, longest first
    candidates.append(cleaned)
    for m in re.finditer(r"\[[\s\S]*\]", cleaned):
        candidates.append(m.group(0))
    for m in re.finditer(r"\{[\s\S]*\}", cleaned):
        candidates.append("[" + m.group(0) + "]")

    for c in candidates:
        try:
            data = json.loads(c)
        except Exception as exc:
            note_failure("chorus:L190", exc)
            continue
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            continue
        out = [d for d in data if isinstance(d, dict) and d.get("op")]
        if out:
            return out[:max(1, int(limit))]
    return []


def _apply(op: dict, genome: Genome, pool: Sequence[GeneSpec]
           ) -> Tuple[Optional[Genome], str]:
    """Turn one proposal into a genome edit, or (None, reason) if invalid."""
    kind = str(op.get("op", "")).strip()
    gene = str(op.get("gene", "")).strip()
    child: Optional[Genome] = None

    try:
        if kind == "add_gene":
            target = str(op.get("new_gene") or gene).strip()
            spec = next((s for s in pool if s.name == target), None)
            if spec is None:
                return None, "unknown_pool_gene"
            if spec.name in genome.names():
                return None, "already_present"
            child = genome.copy()
            child.specs.append(spec)
            child.values.append(spec.clamp(op.get("value")) if op.get("value") is not None
                                else spec.sample(random.Random(0)))
        elif kind == "drop_gene":
            if gene not in genome.names() or genome.dimension <= 1:
                return None, "cannot_drop"
            i = genome.names().index(gene)
            child = genome.copy()
            del child.specs[i]
            del child.values[i]
        elif kind == "rewire_gene":
            if gene not in genome.names():
                return None, "unknown_gene"
            i = genome.names().index(gene)
            new_kw = str(op.get("value") or op.get("kwargs") or "").strip()
            if new_kw not in TRAINER_KWARGS or new_kw in NON_GENE_KWARGS:
                return None, "invalid_kwarg"
            child = genome.copy()
            s = child.specs[i]
            child.specs[i] = GeneSpec(s.name, s.kind, s.lo, s.hi, s.choices, new_kw, s.points)
        elif kind == "nest":
            if gene not in genome.names():
                return None, "unknown_gene"
            i = genome.names().index(gene)
            s = genome.specs[i]
            if s.kind == "schedule":
                return None, "already_nested"
            child = genome.copy()
            child.specs[i] = GeneSpec(s.name, "schedule", s.lo, s.hi, None, s.kwargs, 3)
            child.values[i] = [s.lo + (s.hi - s.lo) * k / 2.0 for k in range(3)]
        elif kind == "tune":
            if gene not in genome.names():
                return None, "unknown_gene"
            i = genome.names().index(gene)
            child = genome.copy()
            child.values[i] = child.specs[i].clamp(op.get("value"))
        else:
            return None, "unknown_op"
    except Exception as exc:  # never let one bad proposal break the loop
        note_failure("chorus:L256", exc)
        return None, "apply_error:%s" % type(exc).__name__

    ok, why = viability(child)
    if not ok:
        return None, why
    return child, "ok"


class Chorus:
    """Budget-capped LLM structural proposer. Advisory; always fail-open."""

    def __init__(self, budget: int = 8, provider: Any = None, model: Optional[str] = None,
                 max_tokens: int = 700, temperature: float = 0.4,
                 cache: bool = True, agent_name: str = "chorus"):
        self.budget = int(max(1, budget))
        self.calls = 0
        self.accepted = 0
        self.rejected = 0
        self.reasons: Dict[str, int] = {}
        self.history: List[dict] = []
        self.model = model
        self.max_tokens = int(max_tokens)
        self.temperature = float(temperature)
        self.cache = bool(cache)
        self.agent_name = agent_name
        self._provider = provider
        self._provider_ready = provider is not None

    # -- provider plumbing (lazy, fail-open) -------------------------------
    def _get_provider(self) -> Any:
        if self._provider is not None:
            return self._provider
        if self._provider_ready:
            return None
        self._provider_ready = True
        try:
            from CORE.inference.providers import get_best_provider  # type: ignore
        except Exception as exc:
            # No src.-prefixed fallback: src/ is not a package on sys.path,
            # so that import can never resolve (2026-10-22 forensic sweep:
            # the lone UNRESOLVED lazy site). Fail open instead.
            note_failure("chorus:L294", exc)
            self._provider = None
            return None
        try:
            self._provider = get_best_provider()
        except Exception as exc:
            note_failure("chorus:L302", exc)
            self._provider = None
        return self._provider

    @property
    def exhausted(self) -> bool:
        return self.calls >= self.budget

    def _ask(self, prompt: str) -> str:
        provider = self._get_provider()
        if provider is None:
            return ""
        try:
            resp = provider.chat(prompt, system_prompt=_SYSTEM,
                                 model=self.model, max_tokens=self.max_tokens,
                                 temperature=self.temperature)
        except Exception as exc:
            note_failure("chorus:L318", exc)
            return ""
        if not isinstance(resp, dict) or resp.get("error"):
            return ""
        content = str(resp.get("content") or "")
        if self.cache and content:
            try:
                from CORE.inference.inference_cache import record_inference  # type: ignore
                record_inference(self.agent_name, resp.get("model", "unknown"), prompt,
                                 content, metadata={"module": "chorus"})
            except Exception as exc:
                note_failure("chorus:L328", exc)
        return content

    # -- main entry point --------------------------------------------------
    def propose(self, digest: FailureDigest, genome: Optional[Genome],
                pool: Sequence[GeneSpec], max_ops: int = 3
                ) -> List[Tuple[Genome, str, str]]:
        """Return accepted `(genome, op, reason)` structural moves.

        Never raises. Empty list is a normal, expected outcome — no key, no
        network, a hallucinated gene, or simply nothing worth proposing.
        """
        if self.exhausted or genome is None:
            return []
        self.calls += 1
        text = self._ask(_build_prompt(digest, genome, pool, max_ops))
        proposals = parse_proposals(text, limit=max_ops)
        self.history.append({"call": self.calls, "proposals": len(proposals)})

        accepted: List[Tuple[Genome, str, str]] = []
        seen_dims: set = set()
        for op in proposals:
            child, reason = _apply(op, genome, pool)
            if child is None:
                self.rejected += 1
                self.reasons[reason] = self.reasons.get(reason, 0) + 1
                continue
            sig = tuple(sorted(child.names()))
            if sig in seen_dims:
                self.rejected += 1
                self.reasons["duplicate_proposal"] = self.reasons.get("duplicate_proposal", 0) + 1
                continue
            seen_dims.add(sig)
            self.accepted += 1
            accepted.append((child, str(op.get("op")), str(op.get("reason", ""))[:200]))
        return accepted

    def report(self) -> dict:
        return {"budget": self.budget, "calls": self.calls,
                "accepted": self.accepted, "rejected": self.rejected,
                "rejection_reasons": dict(sorted(self.reasons.items(),
                                                 key=lambda kv: -kv[1])),
                "exhausted": self.exhausted}
