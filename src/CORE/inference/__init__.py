# coding: utf-8
"""
CORE.inference — AI Inference Module for Azazael

Multi-provider inference with OpenAI-compatible APIs. All free.
No credit card required for any provider.

Quickstart:
    from CORE.inference import get_provider, list_providers, get_best_provider
    from CORE.inference.parallel import ParallelInferenceEngine

    # See what's available
    for p in list_providers():
        print(f"{p['name']}: {p['description']}")

    # Use a specific provider
    groq = get_provider("groq")       # reads GROQ_API_KEY
    result = groq.chat("Hello!")

    # Or let Azazael pick the best available
    best = get_best_provider()
    result = best.chat("Hello!")

    # MAXIMUM PARALLEL INFERENCE — ALL providers at once
    engine = ParallelInferenceEngine()
    result = engine.max_inference("Hello every AI!")

    # THREADED INFERENCE — Simple parallel queries with timeouts
    from CORE.inference.threaded import ThreadedInferenceEngine
    engine = ThreadedInferenceEngine(timeout_seconds=30.0)
    result = engine.query_all("Analyze this code")

Providers (truly free, no credit card):
    groq        — Fastest free inference (30 RPM, 6000 TPM)
    openrouter  — Free model hub (Llama, Gemini, DeepSeek)
    gemini      — Google AI Studio, most generous free tier
    cloudflare  — Edge inference with Workers AI
    cerebras    — Wafer-scale, 1M tokens/day, 3000 tok/s
    nvidia_nim  — 40 RPM permanent, Mistral Large 675B
    mistral     — ~1B tokens/month, Mistral Large/Small
    github      — Free for GitHub users, GPT-4o included
    huggingface — Serverless inference, thousands of models
    ovhcloud    — Anonymous, 2 RPM, no key needed
    ollama      — Ollama, local inference, free, unlimited, no key
    ollama_native — Ollama native API, NOT OpenAI-compatible, no key
    llama_cpp_direct — In-process GGUF via llama-cpp-python, no HTTP, no SDK, no key
    huggingface_direct — In-process via transformers, no HTTP, no SDK, no key
    siliconflow — 200+ open models, generous free tier
    cohere      — Cohere, trial API, 1K calls/mo, Command R+
    zhipu       — GLM-4 series, free tier, bilingual


Parallel Inference Engine:
    broadcast()       — ALL providers simultaneously, results sorted by speed
    ensemble()        — Same prompt to multiple providers with voting/concatenation
    pipeline()        — Chain providers (output of one feeds into next)
    max_inference()   — MAXIMUM PARALLEL — everything at once
    multi_model_broadcast() — Multiple models from the SAME provider in parallel
    cross_provider_compare() — Same model across different providers


Or as a worm:
    from CORE.WORMS.NIM_INFERENCE import NimInferenceWorm
"""

from CORE.inference.nim_client import NimClient, NimConfig, NimModel
from CORE.inference.providers import (
    OpenAIProvider,
    OllamaNativeProvider,
    LlamaCppDirectProvider,
    HuggingFaceDirectProvider,
    ProviderConfig,
    ModelInfo,
    PROVIDER_REGISTRY,
    get_provider,
    list_providers,
    get_best_provider,
)
from CORE.inference.parallel import (
    ParallelInferenceEngine,
    EnsembleResult,
    PipelineStep,
    PipelineResult,
    BroadcastResult,
    ProviderResult,
)
from CORE.inference.threaded import ThreadedInferenceEngine
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    # NVIDIA NIM
    "NimClient",
    "NimConfig",
    "NimModel",
    # Multi-provider
    "OpenAIProvider",
    "OllamaNativeProvider",
    "LlamaCppDirectProvider",
    "HuggingFaceDirectProvider",
    "ProviderConfig",
    "ModelInfo",
    "PROVIDER_REGISTRY",
    "get_provider",
    "list_providers",
    "get_best_provider",
    # Parallel Inference
    "ParallelInferenceEngine",
    "EnsembleResult",
    "PipelineStep",
    "PipelineResult",
    "BroadcastResult",
    "ProviderResult",
    # Threaded Inference Engine
    "ThreadedInferenceEngine",
]
__version__ = "1.2.0"
