# coding: utf-8
"""QueryInLoop — Multi-Provider Query-In-Loop Resolution Engine

Cycles through multiple AI models in a directed loop until the
response converges on a stable, high-confidence resolution.

Unlike SequentialAnalyzer which is a linear chain, QueryInLoop
implements a feedback loop: Model A -> Model B -> Model C -> Model A -> ...
until answers stabilize.

Architecture:
    +-----------------------------------------------------------+
    |  QueryInLoop Engine                                        |
    |                                                             |
    |  Query -> [ Groq   -> OpenRouter -> NVIDIA  ] --+          |
    |           [ Gemini -> GitHub    -> DeepSeek ]   |          |
    |           [ Mistral-> HuggingFace -> xAI    ]   |          |
    |                                                  |          |
    |  Loop: feed synthesis back to first model         |          |
    |  -> Check convergence                             |          |
    |  -> If not converged, repeat cycle                 |          |
    |  -> If converged OR max rounds, return result      |          |
    +-----------------------------------------------------------+

Convergence detection:
    - Semantic similarity > threshold (0.85 default)
    - Confidence score stabilized
    - Max rounds reached (safety limit)

Usage:
    from CORE.inference.query_loop import QueryInLoop

    engine = QueryInLoop()
    result = engine.resolve(
        prompt="Generate a production deployment plan for the Azazael ecosystem.",
        rounds=5,
        verbose=True,
    )
    print(result.resolution)
    print(f"Confidence: {result.confidence}")
    print(f"Rounds: {result.rounds_completed}")
"""

import time
from dataclasses import dataclass, field
# from typing import Dict, List, Optional  # removed — all names have native equivalents

from .providers import get_provider
from .sequential_analyzer import (
    ChainStep,
    ModelResponse,
    compute_similarity,
    estimate_confidence,
)
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# --- Data Models --------------------------------------------------------


@dataclass
class LoopRound:
    """A single round in the query-in-loop cycle."""
    round_number: int
    responses: dict[str, ModelResponse] = field(default_factory=dict)
    synthesis: str = ""
    confidence: float = 0.0
    similarity_to_previous: float = 0.0
    elapsed_ms: int = 0
    tokens_used: int = 0


@dataclass
class ResolutionResult:
    """Final result from the query-in-loop engine."""
    prompt: str
    resolution: str = ""
    confidence: float = 0.0
    rounds_completed: int = 0
    rounds: list[LoopRound] = field(default_factory=list)
    total_elapsed_ms: int = 0
    total_tokens: int = 0
    converged: bool = False
    convergence_similarity: float = 0.0
    models_used: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# --- Model Pipeline Definitions -----------------------------------------


TIER1_FAST = [
    ChainStep("groq", "llama-3.3-70b-versatile",
              "You are a fast initial analyst. Provide a thorough but concise response.", max_tokens=2048, temperature=0.3),
    ChainStep("openrouter", "google/gemini-2.5-flash",
              "You are a fast reviewer. Refine the previous response with additional depth.", max_tokens=2048, temperature=0.4),
]

TIER2_DEEP = [
    ChainStep("openrouter", "google/gemini-2.5-pro",
              "You are a deep reasoning specialist. Critically analyze and expand on the previous response.", max_tokens=4096, temperature=0.4),
    ChainStep("nvidia_nim", "meta/llama-3.3-70b-instruct",
              "You are a technical reviewer. Validate all claims and provide evidence-based corrections.", max_tokens=2048, temperature=0.3),
]

TIER3_SPECIALIST = [
    ChainStep("mistral", "mistral-large-latest",
              "You are a domain expert. Apply specialized knowledge to refine the analysis.", max_tokens=4096, temperature=0.5),
    ChainStep("github", "gpt-4o-mini",
              "You are a clarity specialist. Ensure the response is clear, well-structured, and actionable.", max_tokens=2048, temperature=0.3),
]

TIER4_FINAL = [
    ChainStep("deepseek", "deepseek-v4-flash",
              "You are a final reviewer. Verify completeness and accuracy. If the response is already optimal, confirm it.", max_tokens=2048, temperature=0.3),
    ChainStep("huggingface", "meta-llama/Llama-3.3-70B-Instruct",
              "You are a consensus builder. Synthesize all previous work into the final authoritative response.", max_tokens=4096, temperature=0.4),
]

ALL_TIERS = TIER1_FAST + TIER2_DEEP + TIER3_SPECIALIST + TIER4_FINAL


# Cache provider instances to avoid redundant lookups
_provider_cache = {}


def _get_cached_provider(name: str):
    """Get provider with caching."""
    if name not in _provider_cache:
        try:
            _provider_cache[name] = get_provider(name)
        except Exception as exc:
            note_failure("query_loop:L131", exc)
            return None
    return _provider_cache[name]


def _build_available_pipeline() -> list[ChainStep]:
    """Build pipeline only from providers with valid API keys."""
    pipeline = []
    seen_providers = set()
    for step in ALL_TIERS:
        if step.provider not in seen_providers:
            provider = _get_cached_provider(step.provider)
            if provider is None or not provider.has_valid_key():
                seen_providers.add(step.provider)
                continue
            seen_providers.add(step.provider)
        pipeline.append(step)
    return pipeline


# --- Query-In-Loop Engine ------------------------------------------------


class QueryInLoop:
    """Multi-provider query-in-loop resolution engine.

    Cycles through models in a feedback loop until the response
    converges on a stable, high-confidence answer.
    """

    VERSION = "1.1.0"

    def __init__(self, verbose: bool = False):
        self._verbose = verbose

    def _log(self, msg: str) -> None:
        if self._verbose:
            print(f"[QueryInLoop] {msg}")

    def _build_loop_prompt(
        self,
        original_prompt: str,
        previous_synthesis: str,
        round_number: int,
        step_index: int,
        total_steps: int,
    ) -> str:
        """Build a context-aware prompt for the current loop step."""
        if not previous_synthesis:
            return original_prompt

        return (
            f"ORIGINAL QUERY:\n{original_prompt}\n\n"
            f"PREVIOUS RESPONSE (Round {round_number}, Step {step_index}/{total_steps}):\n"
            f"{previous_synthesis}\n\n"
            f"Your task: Review, refine, and improve the previous response. "
            f"Add any missing information. Correct any inaccuracies. "
            f"If the response is already complete and accurate, "
            f"state that it is sufficient and explain why."
        )

    def resolve(
        self,
        prompt: str,
        rounds: int = 5,
        convergence_threshold: float = 0.85,
        max_tokens_per_step: int = 4096,
        pipeline: (list[ChainStep] | None) = None,
    ) -> ResolutionResult:
        """Run the query-in-loop resolution engine.

        Args:
            prompt: The query to resolve
            rounds: Maximum number of refinement rounds
            convergence_threshold: Similarity threshold for convergence (0.0-1.0)
            max_tokens_per_step: Max tokens per model call
            pipeline: Custom pipeline (uses all available providers by default)

        Returns:
            ResolutionResult with the final resolution and trace
        """
        if pipeline is None:
            pipeline = _build_available_pipeline()

        if not pipeline:
            return ResolutionResult(
                prompt=prompt,
                resolution="No models available. Configure API keys and try again.",
                confidence=0.0,
                rounds_completed=0,
                errors=["No providers with valid keys available"],
            )

        result = ResolutionResult(prompt=prompt)
        start_time = time.time()

        current_synthesis = ""
        previous_synthesis = ""
        total_steps = len(pipeline)

        self._log(f"Starting query-in-loop resolution: {len(pipeline)} models, "
                  f"max {rounds} rounds")

        for round_num in range(1, rounds + 1):
            self._log(f"\n--- Round {round_num}/{rounds} ---")

            time.time()
            loop_round = LoopRound(round_number=round_num)

            for step_idx, step in enumerate(pipeline):
                step_key = f"({round_num}) {step.provider}/{step.model}"

                provider = _get_cached_provider(step.provider)
                if provider is None or not provider.has_valid_key():
                    self._log(f"  Skipping {step_key} - no key")
                    continue

                self._log(f"  Step {step_idx + 1}/{total_steps}: {step_key}")

                loop_prompt = self._build_loop_prompt(
                    prompt, current_synthesis, round_num, step_idx + 1, total_steps
                )

                t0 = time.time()
                response = provider.chat(
                    prompt=loop_prompt,
                    system_prompt=step.system_prompt,
                    model=step.model,
                    max_tokens=max_tokens_per_step,
                    temperature=step.temperature,
                )
                elapsed = int((time.time() - t0) * 1000)

                content = response.get("content", "")
                error = response.get("error", "")
                model_name = response.get("model", step.model)
                tokens = response.get("usage", {}).get("total_tokens", 0)

                success = bool(content and not error)
                confidence = estimate_confidence(content, error) if success else 0.0

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

                loop_round.responses[step_key] = model_resp
                loop_round.elapsed_ms += elapsed
                loop_round.tokens_used += tokens

                if model_name not in result.models_used:
                    result.models_used.append(model_name)

                if success:
                    current_synthesis = content
                    self._log(f"    -> OK ({elapsed}ms, {tokens} tok, conf={confidence:.2f})")
                else:
                    self._log(f"    -> FAIL: {error[:800]}")
                    result.errors.append(
                        f"Round {round_num}, Step {step_key}: {error[:200]}"
                    )

            # Round complete — compute convergence
            successful_responses = [
                r for r in loop_round.responses.values() if r.success
            ]
            if successful_responses:
                loop_round.synthesis = successful_responses[-1].content
                confidences = [r.confidence for r in successful_responses]
                loop_round.confidence = sum(confidences) / max(len(confidences), 1)

                if previous_synthesis:
                    similarity = compute_similarity(
                        loop_round.synthesis, previous_synthesis
                    )
                    loop_round.similarity_to_previous = similarity
                    self._log(f"  Round similarity to previous: {similarity:.3f}")

                    if similarity >= convergence_threshold:
                        result.converged = True
                        result.convergence_similarity = similarity
                        result.resolution = loop_round.synthesis
                        result.confidence = loop_round.confidence
                        result.rounds_completed = round_num
                        result.total_elapsed_ms = int((time.time() - start_time) * 1000)
                        result.total_tokens += loop_round.tokens_used
                        result.rounds.append(loop_round)

                        self._log(f"CONVERGED at round {round_num}! "
                                  f"(similarity={similarity:.3f} >= {convergence_threshold})")
                        break

                previous_synthesis = loop_round.synthesis
            else:
                self._log(f"  No successful responses in round {round_num}")

            result.total_tokens += loop_round.tokens_used
            result.rounds.append(loop_round)
            result.rounds_completed = round_num

        # Post-loop: use best round's synthesis
        if not result.converged:
            best_round = max(
                result.rounds,
                key=lambda r: r.confidence,
                default=None,
            )
            if best_round and best_round.synthesis:
                result.resolution = best_round.synthesis
                result.confidence = best_round.confidence
                self._log(f"No convergence after {result.rounds_completed} rounds. "
                          f"Best round: {best_round.round_number} "
                          f"(confidence={best_round.confidence:.2f})")
            else:
                result.resolution = "Resolution failed - no valid responses from any model."
                result.confidence = 0.0

        result.total_elapsed_ms = int((time.time() - start_time) * 1000)

        self._log(f"\nResolution complete: {result.rounds_completed} rounds, "
                  f"{len(result.models_used)} unique models, "
                  f"{result.total_elapsed_ms}ms total")
        self._log(f"Converged: {result.converged} | "
                  f"Confidence: {result.confidence:.3f}")

        return result

    def resolve_until_convergence(
        self,
        prompt: str,
        max_rounds: int = 10,
        convergence_threshold: float = 0.85,
        min_confidence: float = 0.5,
    ) -> ResolutionResult:
        """Resolve by looping through models UNTIL convergence or max rounds.

        Automatically selects which models to use based on availability
        and adjusts the threshold dynamically for deeper refinement.
        """
        pipeline = _build_available_pipeline()
        result = self.resolve(
            prompt=prompt,
            rounds=max_rounds,
            convergence_threshold=convergence_threshold,
            pipeline=pipeline,
        )

        # If confidence is too low, run deep refinement with top-tier models
        if not result.converged and result.confidence < min_confidence:
            self._log(f"Confidence too low ({result.confidence:.2f} < {min_confidence}). "
                      f"Running deep refinement...")

            deep_pipeline = [
                step for step in pipeline
                if step.provider in ("openrouter", "nvidia_nim", "mistral", "deepseek")
            ]

            if deep_pipeline and len(deep_pipeline) > 0:
                deep_result = self.resolve(
                    prompt=f"Refine and improve the following analysis:\n\n"
                           f"{result.resolution}\n\n"
                           f"Focus on accuracy, completeness, and actionable insights.",
                    rounds=min(3, max_rounds),
                    convergence_threshold=0.8,
                    pipeline=deep_pipeline,
                )

                if deep_result.confidence > result.confidence:
                    result.resolution = deep_result.resolution
                    result.confidence = deep_result.confidence
                    result.rounds_completed += deep_result.rounds_completed
                    result.total_elapsed_ms += deep_result.total_elapsed_ms
                    result.total_tokens += deep_result.total_tokens
                    result.converged = deep_result.converged
                    result.errors.extend(deep_result.errors)

        return result


# --- Self-Test -----------------------------------------------------------


if __name__ == "__main__":
    print("QueryInLoop v" + QueryInLoop.VERSION)
    print("=" * 60)

    engine = QueryInLoop(verbose=True)

    test_prompt = "What are the top 3 risks in the Azazael codebase based on the 6-API convergence analysis?"

    print(f"\nTest prompt: {test_prompt[:80]}...")
    print("\n--- Running resolve_until_convergence ---")
    result = engine.resolve_until_convergence(
        prompt=test_prompt,
        max_rounds=3,
        convergence_threshold=0.80,
    )

    print(f"\n{'='*60}")
    print(f"Models used: {len(result.models_used)}")
    print(f"Rounds: {result.rounds_completed}")
    print(f"Converged: {result.converged}")
    print(f"Confidence: {result.confidence:.3f}")
    print(f"Total time: {result.total_elapsed_ms}ms")
    print(f"Total tokens: {result.total_tokens}")

    if result.resolution:
        print(f"\nResolution preview: {result.resolution[:400]}...")
