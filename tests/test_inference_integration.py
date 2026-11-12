# coding: utf-8
"""Integration tests for inference providers — contract tests with mocks,
failure injection, and circuit breaker validation using ParallelInferenceEngine.

Tests exercise:
  - Mocked provider responses (contract tests for broadcast/ensemble/pipeline)
  - Failure injection scenarios (partial failures, timeouts, exceptions)
  - Circuit breaker state transitions (CLOSED → OPEN → HALF_OPEN → CLOSED)

Run with:  pytest tests/test_inference_integration.py -v
            (pass -m slow for multi-threaded integration tests)
Or standalone: python tests/test_inference_integration.py
"""

import sys
import os
import time
import threading
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

# ── Module Under Test ──────────────────────────────────────────────
from CORE.inference.parallel import (
    ParallelInferenceEngine, ProviderResult, BroadcastResult,
    EnsembleResult, PipelineResult, PipelineStep,
)
from CORE.inference.providers import (
    CircuitBreaker as ProviderCircuitBreaker,
    ProviderHealth,
    get_circuit_breaker,
    _global_breaker,
)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def make_mock_provider(provider_id: str, responses: (dict[str, Any] | None) = None):
    """Create a MagicMock that mimics an OpenAIProvider for testing.

    Args:
        provider_id: Provider name (e.g., 'groq', 'mock_a')
        responses: Dict of {prompt: {"content": ..., ...}} to control
                   per-prompt responses. Default returns success.
    """
    mock = MagicMock()
    mock.provider_name = provider_id

    def chat_side_effect(prompt, **kwargs):
        if responses and prompt in responses:
            return responses[prompt]
        return {
            "content": f"Response from {provider_id}: {prompt[:20]}...",
            "model": f"{provider_id}-model-v1",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "finish_reason": "stop",
            "elapsed_ms": 100,
            "provider": provider_id,
        }

    mock.chat = MagicMock(side_effect=chat_side_effect)
    mock.has_valid_key = MagicMock(return_value=True)
    mock.list_models = MagicMock(return_value=[])
    mock.get_stats = MagicMock(return_value={
        "provider": provider_id, "total_requests": 0, "total_tokens": 0,
        "last_error": None, "model": "test-model",
    })
    return mock


def make_mock_provider_info(provider_id: str, has_key: bool = True):
    """Create a provider info dict like list_providers() returns."""
    return {
        "id": provider_id,
        "name": provider_id.capitalize(),
        "has_api_key": has_key,
        "description": f"Mock provider {provider_id}",
        "models": [f"{provider_id}-model-v1"],
        "base_url": f"https://mock.{provider_id}.com/v1",
    }


MOCK_PROVIDER_IDS = ["mock_a", "mock_b", "mock_c"]

MOCK_PROVIDERS_INFO = [make_mock_provider_info(pid) for pid in MOCK_PROVIDER_IDS]

# Parallel module PROVIDER_REGISTRY patch
MOCK_PARALLEL_REGISTRY = {
    pid: {"default_model": f"{pid}-model", "free_models": {}}
    for pid in MOCK_PROVIDER_IDS
}

# providers module PROVIDER_REGISTRY patch (needed by get_provider)
MOCK_PROVIDERS_REGISTRY = {
    pid: {
        "env_var": f"{pid.upper()}_API_KEY",
        "name": pid.capitalize(),
        "description": f"Mock {pid}",
        "base_url": f"https://mock.{pid}.com/v1",
        "default_model": f"{pid}-model",
        "free_models": {},
        "rate_limit_rpm": 30,
        "key_prefix": "",
        "signup_url": "",
        "requires_auth": True,
    }
    for pid in MOCK_PROVIDER_IDS
}


def setup_test_env():
    """Set up test environment: env vars, breaker reset.

    Called by setup_method in each test class.
    Sets env vars for mock providers and resets the global breaker.
    """
    os.environ["MOCK_A_API_KEY"] = "mock_key_a"
    os.environ["MOCK_B_API_KEY"] = "mock_key_b"
    os.environ["MOCK_C_API_KEY"] = "mock_key_c"
    _global_breaker._health.clear()
    _global_breaker._half_open_counters.clear()


def teardown_test_env():
    """Teardown test environment: remove mock env vars, reset breaker."""
    for key in ["MOCK_A_API_KEY", "MOCK_B_API_KEY", "MOCK_C_API_KEY"]:
        os.environ.pop(key, None)
    _global_breaker._health.clear()
    _global_breaker._half_open_counters.clear()


# ═══════════════════════════════════════════════════════════════════
# Base class with common mock patches
# ═══════════════════════════════════════════════════════════════════

class _BaseMockTest:
    """Base test class that sets up mock provider registries and env.

    Provides _patch_registry() context manager that patches:
    - list_providers in parallel module
    - PROVIDER_REGISTRY in parallel and providers modules
    - Sets env vars for mock API keys
    """

    def setup_method(self, method=None):
        setup_test_env()

    def teardown_method(self, method=None):
        teardown_test_env()

    def _patch_registry(self):
        """Return a context manager that patches all provider registries."""
        return patch.multiple(
            "CORE.inference.parallel",
            list_providers=lambda: MOCK_PROVIDERS_INFO,
            PROVIDER_REGISTRY=MOCK_PARALLEL_REGISTRY,
        )

    def _patch_providers_registry(self):
        """Patch PROVIDER_REGISTRY in the providers module."""
        return patch("CORE.inference.providers.PROVIDER_REGISTRY", MOCK_PROVIDERS_REGISTRY)

    def _patch_get_provider(self, providers_map: dict[str, Any]):
        """Patch get_provider in the parallel module."""
        return patch("CORE.inference.parallel.get_provider",
                     side_effect=lambda name, **kw: providers_map[name])

    def _make_providers_map(self, **overrides):
        """Create a providers map with optional overrides for specific mock providers."""
        base = {
            "mock_a": make_mock_provider("mock_a"),
            "mock_b": make_mock_provider("mock_b"),
            "mock_c": make_mock_provider("mock_c"),
        }
        base.update(overrides)
        return base


# ═══════════════════════════════════════════════════════════════════
# Section 1: Contract Tests — Mocked Provider Responses
# ═══════════════════════════════════════════════════════════════════

class TestProviderResultContract:
    """Verify ProviderResult dataclass contract."""

    def test_success_result(self):
        r = ProviderResult(
            provider="groq", model="llama-3.3-70b",
            content="Hello!", elapsed_ms=150, success=True,
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            finish_reason="stop",
        )
        assert r.provider == "groq"
        assert r.content == "Hello!"
        assert r.elapsed_ms == 150
        assert r.success is True
        assert r.tokens_per_second > 0

    def test_failure_result(self):
        r = ProviderResult(
            provider="groq", model="",
            content="", elapsed_ms=5000, success=False,
            error="Rate limit exceeded",
        )
        assert r.success is False
        assert "Rate limit" in r.error
        assert r.tokens_per_second == 0.0

    def test_zero_elapsed_safe_division(self):
        r = ProviderResult(
            provider="test", model="m", content="hi", elapsed_ms=0,
            success=True, usage={"total_tokens": 0},
        )
        assert r.tokens_per_second == 0.0


class TestBroadcastContract(_BaseMockTest):
    """Contract tests for ParallelInferenceEngine.broadcast()."""

    def test_broadcast_empty_no_providers(self):
        """Broadcast with no targets should return empty result gracefully."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            result = engine.broadcast("test", exclude_providers=MOCK_PROVIDER_IDS)
        assert isinstance(result, BroadcastResult)
        assert len(result.results) == 0
        assert result.total_providers_attempted == 0
        assert result.success_count == 0

    def test_broadcast_with_include_filter(self):
        """include_providers should restrict broadcast targets."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            providers_map = self._make_providers_map()
            with self._patch_get_provider(providers_map):
                result = engine.broadcast(
                    "Hello world", include_providers=["mock_a"],
                )

        assert result.total_providers_attempted == 1
        assert result.success_count == 1
        assert result.results[0].provider == "mock_a"

    def test_broadcast_all_providers_succeed(self):
        """All providers should succeed and be returned sorted by speed."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            providers_map = self._make_providers_map()
            with self._patch_get_provider(providers_map):
                result = engine.broadcast("Test prompt")

        assert result.total_providers_attempted == 3
        assert result.success_count == 3
        assert result.failure_count == 0
        # Results should be sorted by elapsed_ms (fastest first)
        results = result.results
        for i in range(len(results) - 1):
            assert results[i].elapsed_ms <= results[i + 1].elapsed_ms
        # All provider names should appear
        names = [r.provider for r in results]
        assert "mock_a" in names
        assert "mock_b" in names
        assert "mock_c" in names

    def test_broadcast_result_fields(self):
        """BroadcastResult should populate all fields correctly."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            providers_map = self._make_providers_map()
            with self._patch_get_provider(providers_map):
                result = engine.broadcast("Test prompt",
                                          include_providers=["mock_a", "mock_b"])

        assert result.prompt == "Test prompt"
        assert result.total_providers_available >= 2
        assert result.total_providers_attempted == 2
        assert result.success_count == 2
        assert result.total_tokens > 0
        assert result.fastest is not None
        assert result.slowest is not None
        assert result.best_by_throughput is not None

        # as_dict serialization
        d = result.as_dict()
        assert d["prompt"] == "Test prompt"
        assert d["total_providers_attempted"] == 2
        assert d["fastest"] is not None
        assert len(d["results"]) == 2

    def test_broadcast_respects_exclude(self):
        """Excluded providers should not be called."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            providers_map = self._make_providers_map()
            with self._patch_get_provider(providers_map):
                result = engine.broadcast("Hello",
                                          exclude_providers=["mock_b", "mock_c"])

        assert result.total_providers_attempted == 1
        assert result.results[0].provider == "mock_a"


class TestEnsembleContract(_BaseMockTest):
    """Contract tests for ParallelInferenceEngine.ensemble()."""

    @staticmethod
    def _two_results():
        """Two successful provider results for ensemble."""
        return [
            ProviderResult(
                provider="mock_a", model="m-a",
                content="Paris is the capital of France.",
                elapsed_ms=150, success=True,
                usage={"total_tokens": 20},
            ),
            ProviderResult(
                provider="mock_b", model="m-b",
                content="Paris is the capital of France!",
                elapsed_ms=300, success=True,
                usage={"total_tokens": 25},
            ),
        ]

    def test_ensemble_vote_strategy(self):
        """Vote strategy should pick majority first token."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            result = engine.ensemble(
                "What is the capital of France?",
                providers=["mock_a", "mock_b"],
                strategy="vote",
                existing_results=self._two_results(),
            )
        assert result.strategy == "vote"
        assert result.success_count == 2
        # First token of both: "Paris" → consensus "Paris"
        assert result.consensus == "Paris"

    def test_ensemble_concatenate_strategy(self):
        """Concatenate strategy should join all responses with attribution."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            result = engine.ensemble(
                "Question?", strategy="concatenate",
                existing_results=self._two_results(),
            )
        assert result.strategy == "concatenate"
        assert "[mock_a" in result.concatenated
        assert "[mock_b" in result.concatenated
        assert "Paris" in result.concatenated

    def test_ensemble_best_strategy(self):
        """Best strategy should return fastest successful response."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            results = self._two_results()
            result = engine.ensemble(
                "Question?", strategy="best",
                existing_results=results,
            )
        assert result.strategy == "best"
        assert result.best_result == results[0].content  # mock_a was faster

    def test_ensemble_with_partial_failures(self):
        """Ensemble with some failing providers should still succeed."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            results = [
                ProviderResult(provider="mock_a", model="m-a", content="OK",
                               elapsed_ms=100, success=True, usage={"total_tokens": 5}),
                ProviderResult(provider="mock_b", model="m-b", content="",
                               elapsed_ms=200, success=False, error="Timeout"),
            ]
            result = engine.ensemble("Test", strategy="vote", existing_results=results)
        assert result.success_count == 1
        assert result.failure_count == 1
        assert result.provider_count == 2

    def test_ensemble_all_fail(self):
        """Ensemble with all failures should return gracefully."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            results = [
                ProviderResult(
                    provider="mock_a", model="m-a", content="",
                    elapsed_ms=500, success=False, error="Rate limit",
                ),
            ]
            result = engine.ensemble("Test", strategy="vote", existing_results=results)
        assert result.success_count == 0
        assert result.consensus == ""
        assert result.best_result == "[All providers failed]"

    def test_ensemble_as_dict(self):
        """EnsembleResult.as_dict() should serialize correctly."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            result = engine.ensemble("Test", strategy="vote",
                                     existing_results=self._two_results())
        d = result.as_dict()
        assert d["strategy"] == "vote"
        assert d["provider_count"] == 2
        assert d["consensus"] == "Paris"
        assert len(d["results"]) == 2


class TestPipelineContract(_BaseMockTest):
    """Contract tests for ParallelInferenceEngine.pipeline()."""

    def test_pipeline_single_step(self):
        """Single pipeline step should return its result as final."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            with patch.object(engine, "_call_provider", return_value=ProviderResult(
                provider="mock_a", model="m-a",
                content="Step 1 output", elapsed_ms=100, success=True,
                usage={"total_tokens": 15},
            )):
                steps = [PipelineStep(provider="mock_a",
                                      prompt_template="Process: {previous_output}")]
                result = engine.pipeline("Initial input", steps)

        assert len(result.step_results) == 1
        assert result.final_output == "Step 1 output"
        assert result.total_tokens == 15
        assert result.elapsed_ms >= 0

    def test_pipeline_multiple_steps(self):
        """Multi-step pipeline should chain outputs."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            call_count = 0

            def mock_call(provider_id, prompt, **kw):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    assert "Initial" in prompt
                    return ProviderResult(
                        provider="mock_a", model="m-a",
                        content="Step 1: refined text", elapsed_ms=100, success=True,
                        usage={"total_tokens": 10},
                    )
                else:
                    assert "refined text" in prompt
                    return ProviderResult(
                        provider="mock_b", model="m-b",
                        content="Step 2: final answer", elapsed_ms=200, success=True,
                        usage={"total_tokens": 15},
                    )

            with patch.object(engine, "_call_provider", side_effect=mock_call):
                steps = [
                    PipelineStep(provider="mock_a",
                                 prompt_template="Refine: {previous_output}"),
                    PipelineStep(provider="mock_b",
                                 prompt_template="Summarize: {previous_output}"),
                ]
                result = engine.pipeline("Initial input", steps)

        assert len(result.step_results) == 2
        assert result.final_output == "Step 2: final answer"
        assert result.total_tokens == 25
        assert call_count == 2

    def test_pipeline_failure_early_exit(self):
        """Pipeline should stop on first failure, returning last successful output."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            call_count = 0

            def mock_call(provider_id, prompt, **kw):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return ProviderResult(
                        provider="mock_a", model="m-a",
                        content="Step 1 OK", elapsed_ms=100, success=True,
                        usage={"total_tokens": 5},
                    )
                else:
                    return ProviderResult(
                        provider="mock_b", model="m-b",
                        content="", elapsed_ms=500, success=False,
                        error="Service unavailable",
                    )

            with patch.object(engine, "_call_provider", side_effect=mock_call):
                steps = [
                    PipelineStep(provider="mock_a"),
                    PipelineStep(provider="mock_b"),
                    PipelineStep(provider="mock_c"),  # Should not be reached
                ]
                result = engine.pipeline("Start", steps)

        assert len(result.step_results) == 2  # Only mock_a + mock_b tried
        assert result.final_output == "Step 1 OK"  # Last successful output
        assert call_count == 2  # mock_c never called

    def test_pipeline_empty_steps(self):
        """Empty pipeline should return initial prompt as final output."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            result = engine.pipeline("Initial", [])
        assert result.final_output == "Initial"
        assert len(result.step_results) == 0
        assert result.total_tokens == 0

    def test_pipeline_as_dict(self):
        """PipelineResult.as_dict() should serialize correctly."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            with patch.object(engine, "_call_provider", return_value=ProviderResult(
                provider="mock_a", model="m-a",
                content="Done", elapsed_ms=100, success=True,
                usage={"total_tokens": 10},
            )):
                steps = [PipelineStep(provider="mock_a")]
                result = engine.pipeline("Prompt", steps)

        d = result.as_dict()
        assert d["steps"] == 1
        assert d["final_output"] == "Done"
        assert len(d["step_results"]) == 1


# ═══════════════════════════════════════════════════════════════════
# Section 2: Failure Injection Scenarios
# ═══════════════════════════════════════════════════════════════════

class TestFailureInjection(_BaseMockTest):
    """Failure injection into provider calls and ParallelInferenceEngine."""

    def test_single_provider_failure_others_succeed(self):
        """One failing provider should not affect others in broadcast."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            mock_b = make_mock_provider("mock_b")
            mock_b.chat = MagicMock(return_value={
                "error": "Rate limit exceeded",
                "elapsed_ms": 500, "provider": "mock_b",
            })
            providers_map = self._make_providers_map(mock_b=mock_b)
            with self._patch_get_provider(providers_map):
                result = engine.broadcast("Test prompt")

        assert result.success_count == 2  # mock_a, mock_c succeeded
        assert result.failure_count == 1  # mock_b failed
        assert len(result.results) == 3
        names_failed = [r.provider for r in result.results if not r.success]
        assert "mock_b" in names_failed

    def test_provider_exception_handled_gracefully(self):
        """Provider that raises an exception should be caught as failure."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            mock_c = make_mock_provider("mock_c")
            mock_c.chat = MagicMock(side_effect=ConnectionError("Connection refused"))
            providers_map = self._make_providers_map(mock_c=mock_c)
            with self._patch_get_provider(providers_map):
                result = engine.broadcast("Test",
                                          include_providers=["mock_a", "mock_c"])

        assert result.success_count == 1
        assert result.failure_count == 1
        failed_results = [r for r in result.results if not r.success]
        assert len(failed_results) == 1
        assert "Connection refused" in failed_results[0].error

    def test_all_providers_fail_no_crash(self):
        """All providers failing should return gracefully with zero successes."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            providers_map = {}
            for pid in MOCK_PROVIDER_IDS:
                p = make_mock_provider(pid)
                p.chat = MagicMock(return_value={
                    "error": "Service unavailable",
                    "elapsed_ms": 1000, "provider": pid,
                })
                providers_map[pid] = p

            with self._patch_get_provider(providers_map):
                result = engine.broadcast("Test")

        assert result.success_count == 0
        assert result.failure_count == 3
        assert result.total_tokens == 0
        assert result.fastest is None
        assert result.slowest is None
        assert result.best_by_throughput is None

    def test_mixed_success_failure_broadcast_result(self):
        """BroadcastResult should track both success and failure counts."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()

            mock_a = make_mock_provider("mock_a")
            mock_a.chat = MagicMock(return_value={
                "content": "OK from A", "model": "m-a",
                "usage": {"total_tokens": 10},
                "finish_reason": "stop", "elapsed_ms": 50,
                "provider": "mock_a",
            })
            mock_b = make_mock_provider("mock_b")
            mock_b.chat = MagicMock(return_value={
                "error": "Timeout", "elapsed_ms": 5000,
                "provider": "mock_b",
            })
            mock_c = make_mock_provider("mock_c")
            mock_c.chat = MagicMock(return_value={
                "error": "Quota exceeded", "elapsed_ms": 3000,
                "provider": "mock_c",
            })
            providers_map = {"mock_a": mock_a, "mock_b": mock_b, "mock_c": mock_c}

            with self._patch_get_provider(providers_map):
                result = engine.broadcast("Test")

        assert result.success_count == 1
        assert result.failure_count == 2
        assert result.fastest is not None
        assert result.fastest.provider == "mock_a"


# ═══════════════════════════════════════════════════════════════════
# Section 3: Circuit Breaker Validation
# ═══════════════════════════════════════════════════════════════════

class TestCircuitBreakerUnit:
    """Unit tests for Provider CircuitBreaker state transitions."""

    def test_initial_state_allows_requests(self):
        """Fresh breaker should allow all requests (CLOSED state)."""
        cb = ProviderCircuitBreaker()
        assert cb.allow_request("test_provider") is True

    def test_consecutive_failures_open_circuit(self):
        """Failure threshold should transition from CLOSED to OPEN."""
        cb = ProviderCircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        assert cb.allow_request("test_provider") is True

        for i in range(3):
            cb.record_failure("test_provider", f"Error {i}")

        assert cb.allow_request("test_provider") is False  # Circuit OPEN

    def test_success_resets_failure_count(self):
        """A single success should reset consecutive_failures to 0."""
        cb = ProviderCircuitBreaker(failure_threshold=3)
        cb.record_failure("test_provider", "Error 1")
        cb.record_failure("test_provider", "Error 2")
        assert cb._get_health("test_provider").consecutive_failures == 2

        cb.record_success("test_provider")
        assert cb._get_health("test_provider").consecutive_failures == 0

    def test_half_open_transition_after_cooldown(self):
        """After cooldown, OPEN circuit should become HALF_OPEN."""
        cb = ProviderCircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)

        cb.record_failure("test_provider", "E1")
        cb.record_failure("test_provider", "E2")
        assert cb.allow_request("test_provider") is False  # OPEN

        time.sleep(0.06)
        # allow_request should now transition to HALF_OPEN
        assert cb.allow_request("test_provider") is True  # HALF_OPEN → allowed

    def test_half_open_success_closes_circuit(self):
        """Successful request in HALF_OPEN should transition to CLOSED."""
        cb = ProviderCircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)

        cb.record_failure("test_provider", "E1")
        cb.record_failure("test_provider", "E2")
        cb.allow_request("test_provider")  # Still OPEN, not yet cooldown

        time.sleep(0.06)
        assert cb.allow_request("test_provider") is True  # HALF_OPEN

        cb.record_success("test_provider")
        # Circuit should now be CLOSED — next allow_request returns True
        assert cb.allow_request("test_provider") is True

    def test_half_open_failure_reopens_circuit(self):
        """Failed request in HALF_OPEN should go back to OPEN."""
        cb = ProviderCircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)

        cb.record_failure("test_provider", "E1")
        cb.record_failure("test_provider", "E2")
        # OPEN

        time.sleep(0.06)
        prev_state = cb.allow_request("test_provider")  # HALF_OPEN
        assert prev_state is True

        cb.record_failure("test_provider", "HALF_OPEN failure")
        # Should be OPEN again
        assert cb.allow_request("test_provider") is False

    def test_half_open_max_requests_limit(self):
        """HALF_OPEN should limit requests to half_open_max."""
        cb = ProviderCircuitBreaker(failure_threshold=2, cooldown_seconds=0.05,
                                    half_open_max=2)

        cb.record_failure("test_provider", "E1")
        cb.record_failure("test_provider", "E2")

        time.sleep(0.06)
        # First and second request allowed
        assert cb.allow_request("test_provider") is True
        assert cb.allow_request("test_provider") is True
        # Third request denied (exceeds half_open_max)
        assert cb.allow_request("test_provider") is False

    def test_is_healthy_read_only(self):
        """is_healthy should not mutate circuit state."""
        cb = ProviderCircuitBreaker(failure_threshold=2)

        cb.record_failure("test_provider", "E1")
        cb.record_failure("test_provider", "E2")

        # is_healthy should report unhealthy
        assert cb.is_healthy("test_provider") is False

        # State should remain OPEN — is_healthy is read-only
        assert cb.allow_request("test_provider") is False

    def test_is_healthy_healthy_provider(self):
        """Healthy provider should return True from is_healthy."""
        cb = ProviderCircuitBreaker()
        cb.record_success("test_provider")
        cb.record_success("test_provider")
        assert cb.is_healthy("test_provider") is True

    def test_health_score_calculation(self):
        """Health score should be ratio of successes to total requests."""
        cb = ProviderCircuitBreaker()

        cb.record_success("test_provider")
        cb.record_success("test_provider")
        cb.record_failure("test_provider", "E")
        health = cb._get_health("test_provider")
        assert health.failure_rate == 1.0 / 3.0
        assert health.circuit_open is False  # threshold=5, only 1 failure

    def test_provider_health_dataclass(self):
        """ProviderHealth should track and report status."""
        h = ProviderHealth(provider="test")
        assert h.provider == "test"
        assert h.consecutive_failures == 0
        assert h.total_requests == 0
        assert h.total_failures == 0
        assert h.circuit_open is False
        assert h.failure_rate == 0.0
        assert h.is_healthy is True  # property, not method

        h.consecutive_failures = 5
        h.total_requests = 10
        h.total_failures = 8
        h.circuit_open = True
        # is_healthy should be False because circuit_open=True
        assert h.is_healthy is False


class TestCircuitBreakerIntegration(_BaseMockTest):
    """Circuit breaker integration with ParallelInferenceEngine."""

    def test_broadcast_respects_circuit_breaker(self):
        """broadcast with respect_circuit_breaker=True should skip unhealthy providers."""
        setup_test_env()
        with self._patch_registry():
            engine = ParallelInferenceEngine()

            # Open circuit for mock_b
            _global_breaker.record_failure("mock_b", "E1")
            _global_breaker.record_failure("mock_b", "E2")
            _global_breaker.record_failure("mock_b", "E3")
            _global_breaker.record_failure("mock_b", "E4")
            _global_breaker.record_failure("mock_b", "E5")
            assert _global_breaker.allow_request("mock_b") is False

            providers_map = self._make_providers_map()
            with self._patch_get_provider(providers_map):
                # respect_circuit_breaker=True (default) — mock_b should be skipped
                result = engine.broadcast("Test")
                assert result.total_providers_attempted == 2  # only mock_a, mock_c
                assert result.success_count == 2

    def test_broadcast_ignores_circuit_breaker(self):
        """respect_circuit_breaker=False should include all providers."""
        setup_test_env()
        with self._patch_registry():
            engine = ParallelInferenceEngine()

            # Open circuit for mock_b
            for _ in range(5):
                _global_breaker.record_failure("mock_b", "E")
            assert _global_breaker.allow_request("mock_b") is False

            providers_map = self._make_providers_map()
            with self._patch_get_provider(providers_map):
                # respect_circuit_breaker=False — all 3 providers targeted
                result = engine.broadcast("Test", respect_circuit_breaker=False)
                assert result.total_providers_attempted == 3

    def test_circuit_breaker_records_success_via_chat(self):
        """Provider.chat() should call circuit breaker record_success on success."""
        setup_test_env()
        from CORE.inference.providers import OpenAIProvider, ProviderConfig

        with self._patch_providers_registry():
            prov = OpenAIProvider(config=ProviderConfig(
                provider="mock_a", api_key="test_key",
                base_url="https://test.com/v1",
                model="test-model", requires_auth=True,
            ))

            # Mock the OpenAI client to avoid real HTTP calls
            mock_client = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = "Hello"
            mock_choice.finish_reason = "stop"
            mock_response = MagicMock()
            mock_response.choices = [mock_choice]
            mock_response.model = "test-model"
            mock_response.usage.prompt_tokens = 10
            mock_response.usage.completion_tokens = 20
            mock_response.usage.total_tokens = 30
            mock_client.chat.completions.create.return_value = mock_response

            with patch.object(prov, "_get_client", return_value=mock_client):
                result = prov.chat("Hello")

        assert "error" not in result
        assert result["content"] == "Hello"
        assert _global_breaker.is_healthy("mock_a") is True

    def test_circuit_breaker_records_failure_via_chat(self):
        """Provider.chat() should call circuit breaker record_failure on error."""
        setup_test_env()
        from CORE.inference.providers import OpenAIProvider, ProviderConfig

        with self._patch_providers_registry():
            prov = OpenAIProvider(config=ProviderConfig(
                provider="mock_a", api_key="test_key",
                base_url="https://test.com/v1",
                model="test-model", requires_auth=True,
            ))

            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = RuntimeError("API rejected")

            with patch.object(prov, "_get_client", return_value=mock_client):
                result = prov.chat("Hello")

        assert "error" in result
        assert "API rejected" in result["error"]
        health = _global_breaker._get_health("mock_a")
        assert health.consecutive_failures >= 1

    def test_chat_returns_circuit_open_error(self):
        """chat() should return circuit_open error when breaker is OPEN."""
        setup_test_env()
        from CORE.inference.providers import OpenAIProvider, ProviderConfig

        with self._patch_providers_registry():
            prov = OpenAIProvider(config=ProviderConfig(
                provider="mock_b", api_key="test_key",
                base_url="https://test.com/v1",
                model="test-model", requires_auth=True,
            ))

            # Open the circuit
            for _ in range(5):
                _global_breaker.record_failure("mock_b", "Error")
            assert _global_breaker.allow_request("mock_b") is False

            result = prov.chat("Hello")
        assert "circuit_open" in result
        assert result["circuit_open"] is True
        assert "Circuit breaker OPEN" in result["error"]


# ═══════════════════════════════════════════════════════════════════
# Section 4: Global Breaker & Health Check
# ═══════════════════════════════════════════════════════════════════

class TestGlobalBreaker:
    """Tests for the global circuit breaker singleton."""

    def test_get_circuit_breaker_returns_singleton(self):
        """get_circuit_breaker should return the _global_breaker."""
        cb = get_circuit_breaker()
        assert cb is _global_breaker

    def test_singleton_isolation(self):
        """Breaker state should be isolated per provider."""
        cb = get_circuit_breaker()
        cb.record_success("alpha")
        cb.record_success("alpha")
        cb.record_failure("beta", "Error")

        assert cb.is_healthy("alpha") is True
        assert cb._get_health("beta").consecutive_failures == 1

        # Clean up
        cb._health.clear()

    def test_global_breaker_type(self):
        """_global_breaker should be a ProviderCircuitBreaker instance."""
        assert isinstance(_global_breaker, ProviderCircuitBreaker)


# ═══════════════════════════════════════════════════════════════════
# Section 5: Engine Summary & Report
# ═══════════════════════════════════════════════════════════════════

class TestEngineSummary(_BaseMockTest):
    """Test ParallelInferenceEngine summary and report methods."""

    def test_summary_initial_state(self):
        """Summary should reflect initial state."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            s = engine.summary
        assert s["version"] == "1.0.0"
        assert s["total_parallel_runs"] == 0
        assert s["total_ensemble_runs"] == 0
        assert s["total_pipeline_runs"] == 0
        assert s["total_tokens_aggregated"] == 0

    def test_summary_after_runs(self):
        """Summary should accumulate after operations."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            providers_map = self._make_providers_map()
            with self._patch_get_provider(providers_map):
                engine.broadcast("Test", include_providers=["mock_a", "mock_b"])
                engine.ensemble("Test", providers=["mock_a"], strategy="vote",
                                existing_results=[
                                    ProviderResult(provider="mock_a", model="m",
                                                   content="X", elapsed_ms=10,
                                                   success=True,
                                                   usage={"total_tokens": 5}),
                                ])

        s = engine.summary
        assert s["total_parallel_runs"] == 1
        # ensemble() with existing_results uses _compute_ensemble with record_stats=False
        # so total_ensemble_runs is not incremented for non-API ensemble calls
        assert s["total_ensemble_runs"] == 0
        assert s["total_tokens_aggregated"] > 0

    def test_report_returns_status(self):
        """Report should return comprehensive status."""
        with self._patch_registry():
            engine = ParallelInferenceEngine()
            r = engine.report()
        assert r["engine_version"] == "1.0.0"
        assert r["total_providers"] >= 0
        assert "usage" in r
        assert "providers" in r


# ═══════════════════════════════════════════════════════════════════
# Section 6: Thread Safety (Slow — marked)
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestThreadSafety(_BaseMockTest):
    """Concurrent access to ParallelInferenceEngine (slow tests).
    
    Marked slow via @pytest.mark.slow (used by pytest) and SLOW = True
    class attribute (used by standalone runner). Automatically skipped
    in standalone mode for faster validation.
    """

    SLOW = True  # Standalone runner check

    def test_concurrent_broadcast_calls(self):
        """Multiple threads calling broadcast should not corrupt state."""
        setup_test_env()
        with self._patch_registry():
            providers_map = self._make_providers_map()

            # Apply the get_provider patch ONCE in the parent thread before
            # spawning worker threads. This avoids a race condition where
            # multiple threads each enter their own `with patch(...):` context
            # on the same module-level ``get_provider`` attribute — thread A
            # saving+restoring can clobber thread B's patch, causing workers
            # to call the real provider instead of the mock (0 successes).
            with self._patch_get_provider(providers_map):
                engine = ParallelInferenceEngine()

                # Use a barrier to synchronize thread start
                barrier = threading.Barrier(5, timeout=10)
                errors = []
                lock = threading.Lock()

                def run_broadcast(thread_id):
                    try:
                        barrier.wait()  # All threads start simultaneously
                        # No self._patch_get_provider here — the patch is
                        # already active from the parent thread's context.
                        result = engine.broadcast("Test",
                                                  include_providers=["mock_a", "mock_b"])
                        with lock:
                            if result.success_count != 2:
                                errors.append(f"Thread {thread_id}: expected 2 successes, got {result.success_count}, failures={result.failure_count}")
                    except Exception as e:
                        with lock:
                            errors.append(f"Thread {thread_id}: {e}")

                threads = [threading.Thread(target=run_broadcast, args=(i,))
                           for i in range(5)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=30)

        assert len(errors) == 0, f"Errors: {errors}"
        s = engine.summary
        assert s["total_parallel_runs"] == 5


# ═══════════════════════════════════════════════════════════════════
# Standalone Runner
# ═══════════════════════════════════════════════════════════════════

def _run_standalone():
    """Run all tests standalone (for 3.14t where pytest may segfault).

    Skips @pytest.mark.slow tests for faster validation.
    """
    passed = 0
    failed = 0
    errors = []
    skipped = 0

    print("Inference Integration Tests (standalone — slow tests skipped)")
    print("=" * 60)
    print()

    for name, obj in sorted(globals().items(), key=lambda x: x[0]):
        if isinstance(obj, type) and name.startswith("Test"):
            is_slow = getattr(obj, "SLOW", False) or (
                hasattr(obj, "__pytestmark__") and any(
                    getattr(m, "name", "").lower() == "slow" for m in obj.__pytestmark__
                )
            )
            if is_slow:
                print(f"[{name}] (SKIPPED — slow)")
                skipped += 1
                continue

            print(f"[{name}]")
            inst = obj()
            # Call setup_method if present
            if hasattr(inst, "setup_method"):
                inst.setup_method()
            for mn in sorted(dir(inst)):
                if mn.startswith("test_"):
                    try:
                        sys.stdout.flush()
                        getattr(inst, mn)()
                        print(f"  [OK] {mn}")
                        passed += 1
                    except Exception as e:
                        import traceback
                        print(f"  [FAIL] {mn}: {e}")
                        traceback.print_exc()
                        failed += 1
                        errors.append(f"{name}.{mn}: {e}")
            # Call teardown_method if present
            if hasattr(inst, "teardown_method"):
                inst.teardown_method()
            print()

    total = passed + failed
    print(f"  Total: {total} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")
    if errors:
        print("  Errors:")
        for e in errors[:10]:
            print(f"    {e}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_standalone())
