# coding: utf-8
"""SequentialAnalyzer — Multi-Provider Sequential Analysis Workflow

Chains multiple AI models across different providers in sequence,
passing each model's response to the next for progressive refinement.
Designed for production-grade analysis where depth trumps speed.

Architecture:
    Query -> Model A (fast) -> Analysis -> Model B (deep) -> Refinement
    -> Model C (specialist) -> Cross-validation -> Model D (reasoning)
    -> Final Synthesis

Convergence Detection:
    - Semantic similarity between successive responses
    - Confidence scoring from each model
    - Agreement threshold across multiple models
    - Stops when convergence criteria met OR all models exhausted

Usage:
    from CORE.inference.sequential_analyzer import SequentialAnalyzer

    analyzer = SequentialAnalyzer()
    result = analyzer.run(
        prompt="Analyze the security implications of...",
        strategy="deep",  # "fast", "balanced", "deep", "exhaustive"
    )
    print(result.synthesis)
    print(f"Confidence: {result.confidence:.2f}")
    print(f"Models used: {result.models_used}")
"""

import os
import time
import requests
from dataclasses import dataclass, field
# from typing import Dict, List, Optional  # removed — all names have native equivalents

from .providers import get_provider
from typing import List

#: EP201/EP208 -- the confidence-gated cascade. Imported at module level, not
#: inside the method that uses it: this module is imported by the inference
#: package, and `CORE.cascade` depends only on `CORE.failure_ledger` (a leaf), so
#: there is no cycle to break and nothing is gained by deferring it.
try:
    from CORE.cascade import CascadeRouter
except ImportError:                               # never break the analyzer
    CascadeRouter = None

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# --- Public Utilities (shared between SequentialAnalyzer and QueryInLoop) ----

def compute_similarity(a: str, b: str) -> float:
    """Estimate content similarity between two responses (0.0 to 1.0).

    Uses word-level Jaccard similarity as a fast, reliable convergence
    signal without external dependencies.
    """
    if not a or not b:
        return 0.0
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / max(len(union), 1)


def estimate_confidence(content: str, error: str = "") -> float:
    """Estimate confidence from response content (0.0 to 0.95).

    Heuristics:
    - Base confidence from response length
    - Penalty for uncertainty markers ("I think", "maybe", "perhaps")
    - Bonus for structured content (lists, sections, code blocks)
    """
    if error:
        return 0.0
    if not content:
        return 0.0

    word_count = len(content.split())
    base = min(word_count / 500, 0.7)

    uncertainty_phrases = [
        "i think", "maybe", "perhaps", "i'm not sure",
        "i am not sure", "i could be wrong", "not certain",
        "i don't know", "i am uncertain", "might be",
    ]
    uncertainty_count = sum(
        1 for phrase in uncertainty_phrases if phrase in content.lower()
    )
    penalty = min(uncertainty_count * 0.05, 0.3)

    structure_bonus = 0.0
    if "---" in content or "###" in content:
        structure_bonus += 0.05
    if content.count("```") >= 2:
        structure_bonus += 0.05
    if content.count("1.") >= 3:
        structure_bonus += 0.05

    confidence = base - penalty + structure_bonus
    return max(0.0, min(confidence, 0.95))


def check_convergence(
    responses: list['ModelResponse'],
    threshold: float,
) -> tuple:
    """Check if model responses have converged.

    Returns: (converged, confidence, synthesis)
    """
    if len(responses) < 2:
        return False, 0.0, ""

    last = responses[-1]
    prev = responses[-2]

    if not last.success or not prev.success:
        return False, 0.0, ""

    similarity = compute_similarity(last.content, prev.content)

    confidences = [r.confidence for r in responses if r.success]
    avg_confidence = sum(confidences) / max(len(confidences), 1)

    synthesis = last.content
    converged = similarity >= threshold and avg_confidence >= 0.3

    return converged, avg_confidence, synthesis


# --- Data Models ----------------------------------------------------


@dataclass
class ModelResponse:
    """Response from a single model in the analysis chain."""
    provider: str
    model: str
    content: str
    elapsed_ms: int
    tokens_used: int
    success: bool
    error: str = ""
    confidence: float = 0.0  # Self-reported or estimated confidence
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


@dataclass
class AnalysisResult:
    """Result of a full multi-model sequential analysis."""
    prompt: str
    strategy: str
    synthesis: str = ""
    confidence: float = 0.0
    models_used: list[str] = field(default_factory=list)
    responses: dict[str, ModelResponse] = field(default_factory=dict)
    convergence_achieved: bool = False
    convergence_round: int = 0
    total_elapsed_ms: int = 0
    total_tokens: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ChainStep:
    """A single step in the analysis chain."""
    provider: str
    model: str
    system_prompt: str
    max_tokens: int = 2048
    temperature: float = 0.7


# --- Analysis Strategies --------------------------------------------

# Fast: single model, minimal chaining
FAST_CHAIN = [
    ChainStep(
        provider="groq",
        model="llama-3.3-70b-versatile",
        system_prompt="You are a fast analysis assistant. Provide a concise, direct answer.",
        max_tokens=1024,
        temperature=0.3,
    ),
]

# Balanced: two models, fast then deep
BALANCED_CHAIN = [
    ChainStep(
        provider="groq",
        model="llama-3.3-70b-versatile",
        system_prompt="You are an analysis assistant. Provide a thorough initial analysis.",
        max_tokens=2048,
        temperature=0.3,
    ),
    ChainStep(
        provider="openrouter",
        model="google/gemini-2.5-pro",
        system_prompt="You are a deep-analysis assistant. Review the previous analysis and provide corrections, expansions, and deeper insights. If the previous analysis is already comprehensive, state that it is sufficient.",
        max_tokens=2048,
        temperature=0.4,
    ),
]

# Deep: three-model chain for thorough analysis
DEEP_CHAIN = [
    ChainStep(
        provider="groq",
        model="llama-3.3-70b-versatile",
        system_prompt="You are an analysis assistant. Provide a thorough initial analysis covering key points, risks, and observations.",
        max_tokens=2048,
        temperature=0.3,
    ),
    ChainStep(
        provider="openrouter",
        model="google/gemini-2.5-pro",
        system_prompt="You are a deep-analysis reviewer. Review the previous analysis for gaps, errors, and missed insights. Expand with deeper technical detail and alternative perspectives.",
        max_tokens=4096,
        temperature=0.4,
    ),
    ChainStep(
        provider="nvidia_nim",
        model="nvidia/nemotron-4-340b-instruct",
        system_prompt="You are a cross-validation specialist. Review both previous analyses. Identify disagreements, validate claims, and synthesize a final authoritative analysis.",
        max_tokens=2048,
        temperature=0.3,
    ),
]

# Exhaustive: ALL available providers with valid keys in priority order
# Dynamically built from available providers
EXHAUSTIVE_STRATEGY = "exhaustive"

# Ultra: curated top 5 NVIDIA models + best general models
# For maximum quality without the full 80-model time cost
ULTRA_CHAIN = [
    ChainStep("groq", "llama-3.3-70b-versatile",
              "You are a fast analysis assistant. Provide a thorough initial analysis.",
              max_tokens=2048, temperature=0.3),
    ChainStep("openrouter", "google/gemini-2.5-flash",
              "You are a rapid reviewer. Refine and expand the initial analysis.",
              max_tokens=2048, temperature=0.4),
    ChainStep("openrouter", "google/gemini-2.5-pro",
              "You are a deep reasoning specialist. Critically analyze with maximum depth.",
              max_tokens=4096, temperature=0.4),
    ChainStep("nvidia_nim", "nvidia/llama-3.1-nemotron-ultra-253b-v1",
              "You are a large-model specialist. Apply 253B parameters to refine the analysis.",
              max_tokens=4096, temperature=0.3),
    ChainStep("nvidia_nim", "nvidia/nemotron-3-ultra-550b-a55b",
              "You are a 550B-parameter ultra-model. Provide the deepest possible cross-validation.",
              max_tokens=4096, temperature=0.3),
    ChainStep("nvidia_nim", "nvidia/llama-3.3-nemotron-super-49b-v1",
              "You are a domain expert. Validate technical accuracy and add specialized insights.",
              max_tokens=2048, temperature=0.4),
    ChainStep("nvidia_nim", "nvidia/nemotron-3-super-120b-a12b",
              "You are a consensus builder. Synthesize all prior analysis into a definitive answer.",
              max_tokens=2048, temperature=0.4),
    ChainStep("mistral", "mistral-large-latest",
              "You are a final arbiter. Produce the authoritative, complete resolution.",
              max_tokens=4096, temperature=0.3),
]


# --- Strategy Registry ----------------------------------------------

STRATEGIES = {
    "fast": {
        "description": "Single model, fastest response, minimal depth",
        "chain": FAST_CHAIN,
        "convergence_threshold": 0.6,
        "max_rounds": 1,
    },
    "balanced": {
        "description": "Two models: fast analysis + deep review",
        "chain": BALANCED_CHAIN,
        "convergence_threshold": 0.7,
        "max_rounds": 2,
    },
    "deep": {
        "description": "Three models: analysis + review + cross-validation",
        "chain": DEEP_CHAIN,
        "convergence_threshold": 0.8,
        "max_rounds": 3,
    },
    "exhaustive": {
        "description": "ALL available providers with valid keys, ALL NVIDIA models, maximum depth",
        "chain": [],  # Built dynamically
        "convergence_threshold": 0.95,  # Very high threshold - runs nearly all models before converging
        "max_rounds": 10,
    },
    "ultra": {
        "description": "Curated top 5 NVIDIA models + best general models (8 total, highest quality)",
        "chain": ULTRA_CHAIN,
        "convergence_threshold": 0.95,  # High threshold - doesn't converge early
        "max_rounds": 8,
    },
}


# -- BACKWARD COMPAT: private aliases for existing callers --

_compute_similarity = compute_similarity
_estimate_confidence = estimate_confidence
_check_convergence = check_convergence


# --- Sequential Analyzer --------------------------------------------


class SequentialAnalyzer:
    """Multi-provider sequential analysis workflow.

    Chains models across providers, passing context forward for
    progressive refinement. Detects convergence to stop early.
    """

    VERSION = "1.0.0"

    def __init__(self, verbose: bool = False):
        self._verbose = verbose

    def _log(self, msg: str) -> None:
        """Print log message if verbose."""
        if self._verbose:
            print(f"[SequentialAnalyzer] {msg}")

    def _build_exhaustive_chain(self, max_nvidia_models: int = -1) -> list[ChainStep]:
        """Build an exhaustive chain from ALL available providers.

        Fetches ALL NVIDIA models from their API - no cap by default.
        Uses every model from every provider with valid keys.

        Args:
            max_nvidia_models: Max NVIDIA models to include (default -1 = ALL).
        """
        # Ordered by typical speed + depth profile
        provider_models = [
            # Tier 1: Fast, proven models
            ("groq", "llama-3.3-70b-versatile"),
            ("openrouter", "google/gemini-2.5-flash"),
            ("openrouter", "google/gemini-2.5-pro"),
            # Tier 2: Deep analysis
            ("mistral", "mistral-large-latest"),
            # Tier 3: Frontier models via GitHub
            ("github", "gpt-4o"),
            ("github", "gpt-4o-mini"),
            ("github", "o4-mini"),
            # Tier 4: NVIDIA NIM large models (best free tier)
            ("nvidia_nim", "mistralai/mistral-large-3-675b-instruct-2512"),
            ("nvidia_nim", "nvidia/nemotron-3-ultra-550b-a55b"),
            ("nvidia_nim", "nvidia/llama-3.1-nemotron-ultra-253b-v1"),
            ("nvidia_nim", "nvidia/nemotron-4-340b-instruct"),
            ("nvidia_nim", "nvidia/nemotron-3-super-120b-a12b"),
            ("nvidia_nim", "qwen/qwen3.5-397b-a17b"),
            ("nvidia_nim", "nvidia/llama-3.3-nemotron-super-49b-v1.5"),
            # Tier 5: Specialist models
            ("huggingface", "meta-llama/Llama-3.3-70B-Instruct"),
            # Tier 6: Additional providers (when keys/account configured)
            ("xai", "grok-4.3"),
            ("cloudflare", "@cf/meta/llama-3.3-70b-instruct"),
            ("deepseek", "deepseek-v4-pro"),
        ]

        chain = []
        system_templates = [
            "You are an analysis assistant. Provide a comprehensive initial analysis of the query.",
            "You are a deep-analysis reviewer. Expand on the previous analysis with deeper insights.",
            "You are a cross-validation specialist. Identify gaps and validate claims from previous analyses.",
            "You are a synthesis expert. Combine all previous analyses into a cohesive final assessment.",
            "You are a domain expert. Apply specialized knowledge to refine the analysis further.",
            "You are a critical reviewer. Challenge assumptions and identify weaknesses in the analysis.",
            "You are a consensus builder. Find common ground across all perspectives.",
            "You are a final arbiter. Produce the definitive, authoritative answer.",
        ]

        # --- NVIDIA models: dynamically fetched from API ---
        nvidia_models = self._fetch_nvidia_models(max_nvidia_models)

        # --- Build chain: standard models first, then all NVIDIA models ---
        step_idx = 0
        for provider, model in provider_models:
            try:
                p = get_provider(provider)
                if not p.has_valid_key():
                    self._log(f"Skipping {provider}/{model} - no API key")
                    continue
                if provider == "cloudflare" and not os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
                    self._log(f"Skipping {provider}/{model} - CLOUDFLARE_ACCOUNT_ID not set")
                    continue
            except Exception:
                self._log(f"Skipping {provider}/{model} - provider error")
                continue

            sys_prompt = system_templates[step_idx % len(system_templates)]
            temp = min(0.3 + (step_idx * 0.05), 0.7)
            max_toks = min(2048 + (step_idx * 256), 8192)

            chain.append(ChainStep(
                provider=provider, model=model,
                system_prompt=sys_prompt,
                max_tokens=max_toks, temperature=temp,
            ))
            step_idx += 1            # --- ALL NVIDIA models (dynamically fetched, pre-capped) ---
        for model_id in nvidia_models:
            sys_prompt = system_templates[step_idx % len(system_templates)]
            temp = min(0.3 + (step_idx * 0.05), 0.7)
            max_toks = min(2048 + (step_idx * 256), 8192)

            chain.append(ChainStep(
                provider="nvidia_nim", model=model_id,
                system_prompt=sys_prompt,
                max_tokens=max_toks, temperature=temp,
            ))
            step_idx += 1

        self._log(f"Built chain with {len(chain)} models "
                  f"({len(nvidia_models)} from NVIDIA)")
        return chain

    def _fetch_nvidia_models(self, max_nvidia_models: int = -1) -> list[str]:
        """Fetch ALL chat-compatible NVIDIA NIM models from their API.

        Excludes: embedding, vision-only, safety guards, reward models,
        translation, and specialized non-chat models.
        Returns a list of ALL model IDs ordered by quality - no cap by default.

        Args:
            max_nvidia_models: Max models to include. set to -1 for ALL.
        """
        try:
            p = get_provider("nvidia_nim")
            if not p.has_valid_key():
                self._log("NVIDIA NIM - no key, skipping dynamic model fetch")
                return []

            headers = {
                "Authorization": f"Bearer {p.config.api_key}",
                "Accept": "application/json",
            }
            resp = requests.get(
                "https://integrate.api.nvidia.com/v1/models",
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                self._log(f"NVIDIA API error {resp.status_code}")
                return []

            all_models = resp.json().get("data", [])

            # Exclude non-chat model types
            exclude_keywords = [
                "embed", "retriever", "guard", "safety", "reward",
                "translate", "deplot", "kosmos", "fuyu", "vila",
                "nvclip", "clip", "neva", "cosmos", "detector",
                "parse", "calibration", "ising", "recurrentgemma",
                "bge", "multimodal",  # embedding + multimodal models
            ]

            chat_models = []
            for m in all_models:
                mid = m.get("id", "")
                mid_lower = mid.lower()

                # Skip if it contains an exclusion keyword
                skip = any(kw in mid_lower for kw in exclude_keywords)
                if skip:
                    continue

                # Skip vision-only models (multimodal tends to fail text-only)
                if "vision" in mid_lower or "vl" in mid_lower.lower():
                    continue

                chat_models.append(mid)

            # Remove duplicates while preserving order
            seen = set()
            unique_models = []
            for m in chat_models:
                if m not in seen:
                    seen.add(m)
                    unique_models.append(m)

            # Prioritize: Nemotron Ultra > Nemotron Super > Llama > others
            def sort_key(model_id: str) -> int:
                mid = model_id.lower()
                if "ultra" in mid or "550b" in mid:
                    return 0
                if "super" in mid or "120b" in mid or "397b" in mid:
                    return 1
                if "70b" in mid or "253b" in mid or "675b" in mid:
                    return 2
                if "instruct" in mid:
                    return 3
                return 4

            unique_models.sort(key=sort_key)

            # Apply cap inside fetch so the return value is already trimmed
            if max_nvidia_models > 0:
                unique_models = unique_models[:max_nvidia_models]

            self._log(f"NVIDIA: {len(all_models)} total, "
                      f"{len(unique_models)} chat-compatible models added to chain")
            return unique_models

        except Exception as e:
            self._log(f"NVIDIA model fetch error: {e}")
            return []

    # -- EP201/EP208: confidence-gated cascade (cheap triage -> deep tier) --------

    #: Calibrated by OOF ECE in CORE/cascade.py; the router's own default.
    CASCADE_THRESHOLD = 0.65

    def _chat(self, step: ChainStep, prompt: str,
              max_tokens: (int | None) = None) -> ModelResponse:
        """Run one chain step and wrap it. Never raises."""
        t0 = time.time()
        try:
            provider = get_provider(step.provider)
            if not provider.has_valid_key():
                raise RuntimeError(f"provider {step.provider} has no valid API key")
            response = provider.chat(
                prompt=prompt,
                system_prompt=step.system_prompt,
                model=step.model,
                max_tokens=max_tokens or step.max_tokens,
                temperature=step.temperature,
            )
        except Exception as exc:  # noqa: BLE001 -- one bad step is not fatal
            note_failure("sequential_analyzer:_chat", exc)
            return ModelResponse(
                provider=step.provider, model=step.model, content="",
                elapsed_ms=int((time.time() - t0) * 1000), tokens_used=0,
                success=False, error=str(exc)[:200])
        elapsed = int((time.time() - t0) * 1000)
        content = response.get("content", "")
        error = response.get("error", "")
        model_name = response.get("model", step.model)
        tokens = response.get("usage", {}).get("total_tokens", 0)
        success = bool(content and not error)
        return ModelResponse(
            provider=step.provider, model=model_name, content=content,
            elapsed_ms=elapsed, tokens_used=tokens, success=success, error=error,
            confidence=_estimate_confidence(content, error) if success else 0.0)

    def _run_single_with_cascade(self, prompt: str, step: ChainStep, strategy: str,
                                 max_tokens: (int | None) = None) -> AnalysisResult:
        """One-step chain plus a confidence-gated escalation to the deep tier.

        REACHABILITY: `run()` -- the production entry point documented in this
        module's header and the one every caller uses -- delegates here whenever the
        resolved chain has a single step, which is the documented `fast` strategy.

        WHY THIS EXISTS: a single-step chain can never satisfy `check_convergence`
        (it needs two responses), so the `fast` strategy accepted its cheap answer at
        ANY confidence and the deep tier was reachable only by asking for `deep` up
        front. `CascadeRouter` closes that: the triage answer is accepted at or above
        `CASCADE_THRESHOLD`, and below it the deep-tier model is consulted and its
        answer returned instead. Fail-open -- if the router or the escalation call
        fails, the triage answer stands exactly as before.
        """
        result = AnalysisResult(prompt=prompt, strategy=strategy)
        start_time = time.time()
        triage = self._chat(step, prompt, max_tokens)
        responses = [triage]
        escalated = False
        escalated_resp: (ModelResponse | None) = None

        if triage.success:
            # Lazy import: this analyzer stays importable without the router.
            try:

                router_cls = CascadeRouter
                if router_cls is None:
                    raise ImportError("CORE.cascade unavailable; skipping cascade")
                router = router_cls(threshold=self.CASCADE_THRESHOLD)

                def _triage(_task: str) -> tuple:
                    return triage.content, triage.confidence

                def _big(_task: str) -> str:
                    # The expensive tier: only ever reached below threshold.
                    nonlocal escalated_resp
                    escalated_resp = self._chat(DEEP_CHAIN[1], prompt, max_tokens)
                    return escalated_resp.content

                routed = router.route(prompt, _triage, _big)
                escalated = bool(routed.get("escalated"))
                self._log(f"  cascade: via={routed.get('via')} "
                          f"confidence={float(routed.get('confidence') or 0.0):.2f} "
                          f"threshold={self.CASCADE_THRESHOLD}")
            except Exception as exc:  # noqa: BLE001 -- an optimisation, never fatal
                note_failure("sequential_analyzer:cascade", exc)

        if escalated and escalated_resp is not None and escalated_resp.success:
            responses.append(escalated_resp)
            result.synthesis = escalated_resp.content
            result.confidence = escalated_resp.confidence
        elif triage.success:
            result.synthesis = triage.content
            result.confidence = triage.confidence
        else:
            result.synthesis = "All models failed to produce a response."
            result.confidence = 0.0

        for r in responses:
            result.responses[f"{r.provider}/{r.model}"] = r
            result.models_used.append(f"{r.provider}/{r.model}")
            result.total_tokens += r.tokens_used
            if not r.success:
                result.errors.append(f"{r.provider}/{r.model}: {r.error[:200]}")

        result.total_elapsed_ms = int((time.time() - start_time) * 1000)
        self._log(f"Analysis complete: {len(result.models_used)} models, "
                  f"{result.total_elapsed_ms}ms total, escalated={escalated}")
        return result

    def run(
        self,
        prompt: str,
        strategy: str = "balanced",
        custom_chain: (list[ChainStep] | None) = None,
        max_tokens: (int | None) = None,
        max_nvidia_models: int = -1,
    ) -> AnalysisResult:
        """Run sequential analysis with the given strategy.

        Args:
            prompt: The query or problem to analyze
            strategy: "fast", "balanced", "deep", "exhaustive"
            custom_chain: Override the strategy's chain with custom steps
            max_tokens: Override max_tokens for all steps
            max_nvidia_models: Max NVIDIA models (exhaustive only, default -1 = ALL).

        Returns:
            AnalysisResult with synthesis, confidence, and trace
        """
        if strategy not in STRATEGIES:
            strategy = "balanced"

        strat_info = STRATEGIES[strategy]

        if custom_chain:
            chain = custom_chain
        elif strategy == "exhaustive":
            chain = self._build_exhaustive_chain(max_nvidia_models=max_nvidia_models)
        else:
            chain = strat_info["chain"]

        if not chain:
            return AnalysisResult(
                prompt=prompt,
                strategy=strategy,
                synthesis="No models available for analysis.",
                confidence=0.0,
                errors=["Empty analysis chain - no providers with valid keys"],
            )

        # A single-step chain (the documented `fast` strategy) has no second
        # response for `check_convergence` to compare against, so its cheap answer
        # used to be final at ANY confidence. Route it through the gated cascade:
        # cheap first, deep tier only when the triage confidence is below threshold.
        if len(chain) == 1:
            return self._run_single_with_cascade(prompt, chain[0], strategy, max_tokens)

        result = AnalysisResult(prompt=prompt, strategy=strategy)
        start_time = time.time()

        # Track previous response for context passing
        previous_content = ""
        responses: list[ModelResponse] = []

        self._log(f"Starting {strategy} analysis with {len(chain)} models")

        for step_idx, step in enumerate(chain):
            self._log(f"Step {step_idx + 1}/{len(chain)}: {step.model} ({step.provider})")

            try:
                provider = get_provider(step.provider)
            except Exception as e:
                err_msg = f"Provider {step.provider} not available: {e}"
                self._log(err_msg)
                result.errors.append(err_msg)
                continue

            if not provider.has_valid_key():
                err_msg = f"Provider {step.provider} has no valid API key"
                self._log(err_msg)
                result.errors.append(err_msg)
                continue

            # Build context-aware prompt
            context_prompt = prompt
            if previous_content:
                context_prompt = (
                    f"Original query: {prompt}\n\n"
                    f"Previous analysis:\n{previous_content}\n\n"
                    f"Based on the above, provide your analysis. "
                    f"Expand, correct, or refine the previous analysis as needed. "
                    f"If the previous analysis is already complete and accurate, "
                    f"state that it is sufficient and why."
                )

            # Run the model
            t0 = time.time()
            response = provider.chat(
                prompt=context_prompt,
                system_prompt=step.system_prompt,
                model=step.model,
                max_tokens=max_tokens or step.max_tokens,
                temperature=step.temperature,
            )
            elapsed = int((time.time() - t0) * 1000)

            # Parse response
            content = response.get("content", "")
            error = response.get("error", "")
            model_name = response.get("model", step.model)
            tokens = response.get("usage", {}).get("total_tokens", 0)

            success = bool(content and not error)
            confidence = _estimate_confidence(content, error) if success else 0.0

            model_resp = ModelResponse(
                provider=step.provider,
                model=model_name,
                content=content,
                elapsed_ms=elapsed,
                tokens_used=tokens,
                success=success,
                error=error,
                confidence=confidence,
            )

            responses.append(model_resp)
            result.responses[f"{step.provider}/{model_name}"] = model_resp
            result.models_used.append(f"{step.provider}/{model_name}")
            result.total_elapsed_ms += elapsed
            result.total_tokens += tokens

            if success:
                previous_content = content
                self._log(f"  -> OK ({elapsed}ms, {tokens} tokens, confidence={confidence:.2f})")
            else:
                self._log(f"  -> FAIL: {error[:800]}")
                result.errors.append(f"Step {step_idx + 1} ({step.provider}/{step.model}): {error[:200]}")
                # Continue chain even if one model fails

            # Check convergence
            if len(responses) >= 2:
                converged, avg_conf, synthesis = check_convergence(
                    responses,
                    strat_info["convergence_threshold"],
                )
                if converged:
                    result.synthesis = synthesis
                    result.confidence = avg_conf
                    result.convergence_achieved = True
                    result.convergence_round = step_idx + 1
                    self._log(f"Convergence achieved at step {step_idx + 1} "
                              f"(confidence={avg_conf:.2f}, "
                              f"similarity>={strat_info['convergence_threshold']})")
                    break

        # If no convergence, use last successful response
        if not result.convergence_achieved:
            successful = [r for r in responses if r.success]
            if successful:
                last = successful[-1]
                result.synthesis = last.content
                confidences = [r.confidence for r in successful]
                result.confidence = sum(confidences) / max(len(confidences), 1)
                self._log(f"No convergence after {len(responses)} models. "
                          f"Using last response (confidence={result.confidence:.2f})")
            else:
                result.synthesis = "All models failed to produce a response."
                result.confidence = 0.0

        result.total_elapsed_ms = int((time.time() - start_time) * 1000)

        self._log(f"Analysis complete: {len(result.models_used)} models, "
                  f"{result.total_elapsed_ms}ms total")

        return result

    def run_with_feedback(
        self,
        prompt: str,
        strategy: str = "deep",
        max_iterations: int = 3,
    ) -> AnalysisResult:
        """Run analysis with iterative feedback refinement.

        After initial analysis, sends a critique prompt back through
        the chain for additional refinement rounds.
        """
        result = self.run(prompt, strategy=strategy)

        for iteration in range(max_iterations):
            if result.convergence_achieved:
                break

            # Create critique prompt from current synthesis
            critique_prompt = (
                f"Original query: {prompt}\n\n"
                f"Current analysis:\n{result.synthesis}\n\n"
                f"Critique the above analysis. What is missing? What could be wrong? "
                f"What needs more evidence? Provide specific improvements."
            )

            # Run one more deep pass
            refine_result = self.run(critique_prompt, strategy="deep")

            if refine_result.synthesis and refine_result.confidence > result.confidence:
                result.synthesis = refine_result.synthesis
                result.confidence = refine_result.confidence
                result.total_elapsed_ms += refine_result.total_elapsed_ms
                result.total_tokens += refine_result.total_tokens
                result.models_used.extend(refine_result.models_used)
                result.responses.update(refine_result.responses)

                self._log(f"Iteration {iteration + 1}: confidence improved to {result.confidence:.2f}")
            else:
                self._log(f"Iteration {iteration + 1}: no improvement, stopping")
                break

        return result

    def compare_strategies(
        self,
        prompt: str,
        strategies: (list[str] | None) = None,
    ) -> dict[str, AnalysisResult]:
        """Run multiple strategies on the same prompt and compare results."""
        if not strategies:
            strategies = ["fast", "balanced", "deep"]

        results = {}
        for strat in strategies:
            self._log(f"Running strategy: {strat}")
            results[strat] = self.run(prompt, strategy=strat)

        return results


# --- Self-Test ------------------------------------------------------


if __name__ == "__main__":
    print("SequentialAnalyzer v" + SequentialAnalyzer.VERSION)
    print("=" * 60)

    analyzer = SequentialAnalyzer(verbose=True)

    # Quick test
    test_prompt = "What are the key considerations for productionizing an AI-powered code analysis system?"

    print(f"\nTest prompt: {test_prompt[:60]}...")
    print("\n--- Strategy: balanced ---")
    result = analyzer.run(test_prompt, strategy="balanced")

    print(f"\n{'='*60}")
    print(f"Result: {len(result.models_used)} models, "
          f"{result.total_elapsed_ms}ms, "
          f"confidence={result.confidence:.2f}")
    print(f"Converged: {result.convergence_achieved} "
          f"(round {result.convergence_round})")
    print(f"Total tokens: {result.total_tokens}")

    if result.synthesis:
        print(f"\nSynthesis preview: {result.synthesis[:300]}...")
