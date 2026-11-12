# coding: utf-8
"""
providers.py — Multi-Provider Inference Registry for Azazael

Generic OpenAI-compatible provider that works with ANY OpenAI-compatible
inference API. Covers Groq, OpenRouter, Google Gemini, Cloudflare, and more.

All free. OpenAI-compatible, native API, and in-process providers. No API format lock-in.

Provider       Base URL                                    Free Tier
-----------    -----------------------------------------   ---------------------
Groq           https://api.groq.com/openai/v1             30 RPM, 6000 TPM
OpenRouter     https://openrouter.ai/api/v1               Free models hub
Gemini         https://generativelanguage.googleapis.com/  10-15 RPM, 1M TPM
                 v1beta/openai/
Cloudflare     https://api.cloudflare.com/client/v4/       Neurons free tier
                 accounts/{id}/ai/v1
Cohere         https://api.cohere.ai/compatibility/v1          1K calls/mo, no CC
Cerebras       https://api.cerebras.ai/v1                  1M tokens/day, no CC
NVIDIA NIM     https://integrate.api.nvidia.com/v1         40 RPM, permanent
Ollama         http://localhost:11434/v1                   Local, free, unlimited, no CC
OllamaNative   http://localhost:11434/api/generate        Local, free, unlimited, no CC (native API)
GeminiNative   https://generativelanguage.googleapis.com/  Google RPC, free tier, own protocol
                    v1beta/models/{m}:generateContent
HuggingFaceN   https://api-inference.huggingface.co/      Task-specific, free tier, own protocol
                    models/{model_id}
MistralFIMNat  https://api.mistral.ai/v1/fim/completions  FIM code completion, ~1B tokens/mo
CohereNative   https://api.cohere.com/v2/chat             Command R+, 1K calls/mo, no CC
LlamaCppDirect n/a (in-process)                     Local GGUF, direct load, no HTTP, no SDK
HuggingFaceD   n/a (in-process)                     Local transformers, direct load, no HTTP, no SDK
Mistral        https://api.mistral.ai/v1                   ~1B tokens/mo
GitHub Models  https://models.github.ai/inference          10-15 RPM
HuggingFace    https://router.huggingface.co/v1            Free tier, no CC
OVHcloud       https://endpoints.kepler.ai                 2 RPM, anonymous, no CC
SiliconFlow    https://api.siliconflow.com/v1               200+ models, generous free

Zhipu AI       https://api.z.ai/api/paas/v4                 GLM series, free tier

Usage:
    from CORE.inference.providers import get_provider, list_providers

    # List available providers
    for p in list_providers():
        print(f"{p['name']}: {p['description']}")

    # Get a provider by name
    groq = get_provider("groq")  # reads GROQ_API_KEY
    result = groq.chat("Hello!")
"""

import os
import json
import socket
import threading
import time
from urllib.parse import urlparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Inference cache for file-backed citations (RULE 0Q-A) — graceful degradation
try:
    from CORE.inference.inference_cache import record_inference as _cache_record_inference
except ImportError as exc:
    note_failure("providers:optional-dep-missing", exc)
    _cache_record_inference = None

# PAMS recording seam: puts inference turns into the same persistent store the
# internal worms already write to, so `recall_turns()` can answer "what did the
# model say about X last week". Optional and guarded — memory is telemetry, not
# control flow, and must never break a call.
try:
    from CORE.inference.inference_memory import (
        record_turn as _memory_record_turn,
        record_failure as _memory_record_failure,
    )
except ImportError as exc:
    note_failure("providers:optional-dep-missing", exc)
    _memory_record_turn = None
    _memory_record_failure = None
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Iterator


# 3.14t port: the `openai` SDK depends on pydantic_core, which ships no
# free-threaded build — a top-level import made this entire registry
# (keys, validation, model lists) unusable on 3.14t. Import lazily so
# everything except live OpenAI-SDK calls works everywhere.
try:
    from openai import OpenAI
except ImportError as exc:
    note_failure("providers:optional-dep-missing", exc)
    OpenAI = None

# --- Configuration --------------------------------------------------

@dataclass
class ProviderConfig:
    """Configuration for any OpenAI-compatible inference provider."""
    provider: str                          # Provider name (e.g., "groq", "gemini")
    api_key: str = ""                      # API key
    base_url: str = ""                     # Base URL for /v1 endpoints
    model: str = ""                        # Default model
    max_tokens: int = 1024
    temperature: float = 0.7
    top_p: float = 1.0
    timeout_seconds: float = 60.0
    max_retries: int = 3
    rate_limit_rpm: int = 30               # Default rate limit
    extra_headers: Dict[str, str] = field(default_factory=dict)  # Provider-specific headers
    key_prefix: str = ""                   # Expected API key prefix for validation
    signup_url: str = ""                   # Where to get an API key
    requires_auth: bool = True             # False for anonymous providers (OVHcloud)

    def validate(self) -> List[str]:
        """Validate configuration. Returns list of issues (empty = valid)."""
        issues = []
        if not self.api_key:
            issues.append(f"No API key configured (set {self.provider.upper()}_API_KEY)")
        elif self.key_prefix and not self.api_key.startswith(self.key_prefix):
            issues.append(f"API key must start with '{self.key_prefix}'")
        if self.temperature < 0 or self.temperature > 2:
            issues.append("temperature must be 0.0-2.0")
        if self.max_tokens < 1 or self.max_tokens > 131072:
            issues.append("max_tokens must be 1-131072")
        return issues


@dataclass
class ModelInfo:
    """Metadata for an available model."""
    id: str
    description: str = ""
    max_tokens: int = 131072
    free_tier: bool = True


# --- Provider Registry ----------------------------------------------

# All known free providers with their defaults
# Reasoning-overhead pad for the local llama-server reasoning models. Only
# `llama_qwopus` has been measured -- ~127 completion tokens before the first
# `content` token, 326 for a one-function answer -- and 512 is that measurement
# rounded up to a floor. The other local entries whose description says
# "Requires --reasoning off server-side" are the same kind of model on the same
# server, so they inherit the floor rather than being left unpadded and
# re-discovering the blank-turn failure; each should replace it with its own
# measurement when one exists. Entries that do NOT declare a server-side
# reasoning requirement (`llama_mythos_v2`, whose description credits FTPO
# training with clean turns where v1 ran away, and `llamaserver`, which may
# serve anything) are deliberately left unpadded: a pad there would be inventing
# a budget for a model that has not shown it needs one.
_LOCAL_REASONING_OVERHEAD = 512

PROVIDER_REGISTRY: Dict[str, Dict[str, Any]] = {
    "groq": {
        "name": "Groq Cloud",
        "description": "Fastest free inference. LPU-powered. No credit card. IDs verified against live /v1/models (11) Oct 14 2026. NOTE: raw urllib probes get Cloudflare 1010 — use browser UA or SDK.",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "openai/gpt-oss-120b",
        "rate_limit_rpm": 30,
        "key_prefix": "gsk_",
        "env_var": "GROQ_API_KEY",
        "signup_url": "https://console.groq.com",
        "free_models": {
            "openai/gpt-oss-120b": "GPT OSS 120B (OpenAI open weights, flagship)",
            "openai/gpt-oss-20b": "GPT OSS 20B (OpenAI open weights, fast)",
            "qwen/qwen3.8-27b": "Qwen 3.8 27B (Alibaba, multilingual)",
            "openai/gpt-oss-safeguard-20b": "GPT OSS Safeguard 20B (safety)",
            "allam-2-7b": "ALLAM 2 7B (Arabic+English, lightweight)",
        },
    },
    "openrouter": {
        "name": "OpenRouter",
        "description": "Free model hub. Routes to multiple providers. No credit card.",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openrouter/free",
        "rate_limit_rpm": 20,
        "key_prefix": "",
        "env_var": "OPENROUTER_API_KEY",
        "signup_url": "https://openrouter.ai/keys",
        "extra_headers": {
            "HTTP-Referer": "https://github.com/azazael",
            "X-Title": "Azazael AI Inference",
        },
        "free_models": {
            "openrouter/free": "OpenRouter Free (auto-routes to best available free model)",
            "google/gemini-2.5-pro": "Gemini 2.5 Pro (Google, best reasoning, 1M context)",
            "google/gemini-2.5-flash": "Gemini 2.5 Flash (Google, fast, efficient)",
            "google/gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite (Google, fastest)",
            "google/gemini-2.0-flash-001": "[DEPRECATED] Use google/gemini-2.5-pro or google/gemini-2.5-flash instead",
            "deepseek/deepseek-r1": "DeepSeek R1 (deep chain-of-thought reasoning)",
            "meta-llama/llama-3.3-70b-instruct": "Llama 3.3 70B (Meta, general-purpose)",
            "meta-llama/llama-3.1-8b-instruct": "Llama 3.1 8B (Meta, lightweight)",
            "mistralai/mistral-nemo": "Mistral Nemo (efficient reasoning)",
            "nousresearch/hermes-3-llama-3.1-405b:free": "Hermes 3 405B (Nous, largest open model, free tier)",
            "nvidia/nemotron-3-ultra-550b-a55b:free": "Nemotron Ultra 550B (NVIDIA, largest free model)",
            "nvidia/nemotron-3-super-120b-a12b:free": "Nemotron Super 120B (NVIDIA, strong general)",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": "Nemotron Reasoning 30B (NVIDIA, free reasoning)",
            "nvidia/nemotron-nano-9b-v2:free": "Nemotron Nano 9B (NVIDIA, lightweight, free)",
            "nvidia/nemotron-nano-12b-v2-vl:free": "Nemotron Nano 12B VL (NVIDIA, vision, free)",
            "nvidia/nemotron-3-nano-30b-a3b:free": "Nemotron Nano 30B (NVIDIA, balanced, free)",
            "nvidia/nemotron-3.5-content-safety:free": "Nemotron Content Safety (NVIDIA, free)",
            "qwen/qwen3-coder:free": "Qwen3 Coder (Alibaba, code specialist, free)",
            "qwen/qwen3-next-80b-a3b-instruct:free": "Qwen3 Next 80B (Alibaba, MoE, free)",
            "z-ai/glm-4.5-air:free": "GLM 4.5 Air (Z-AI, bilingual, free)",
            "google/gemma-4-26b-a4b-it:free": "Gemma 4 26B (Google, open, free)",
            "google/gemma-4-31b-it:free": "Gemma 4 31B (Google, open, free)",
            "google/lyria-3-pro-preview": "Lyria 3 Pro (Google, preview)",
            "moonshotai/kimi-k2.6:free": "Kimi K2.6 (Moonshot, free tier)",
            "poolside/laguna-xs.2:free": "Laguna XS (Poolside, software dev, free)",
            "poolside/laguna-m.1:free": "Laguna M (Poolside, software dev, free)",
            "cognitivecomputations/dolphin-mistral-24b-venice-edition:free": "Dolphin Mistral 24B (free tier)",
            "liquid/lfm-2.5-1.2b-thinking:free": "LFM 2.5 Thinking 1.2B (fast reasoning, free)",
            "liquid/lfm-2.5-1.2b-instruct:free": "LFM 2.5 1.2B (ultra-light, free)",
            "meta-llama/llama-3.2-3b-instruct:free": "Llama 3.2 3B (Meta, tiny, free)",
            "openrouter/owl-alpha": "OWL Alpha (OpenRouter, experimental)",
        },
    },
    "gemini": {
        "name": "Google Gemini",
        "description": "Most generous free tier. Gemini Flash via AI Studio. Default per Google migration notice Oct 2026 (2.x retired).",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-3.8-flash",
        "rate_limit_rpm": 15,
        "key_prefix": "",
        "env_var": "GEMINI_API_KEY",
        "signup_url": "https://aistudio.google.com/apikey",
        "free_models": {
            "gemini-3.8-flash": "Gemini 3.8 Flash (current, vendor-directed default)",
            "gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite (fastest)",
            "gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite (legacy, existing users)",
        },
    },
    "cloudflare": {
        "name": "Cloudflare Workers AI",
        "description": "Edge inference. Generous free neurons. Set CLOUDFLARE_ACCOUNT_ID+TOKEN.",
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        "default_model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "rate_limit_rpm": 30,
        "key_prefix": "",
        "env_var": "CLOUDFLARE_API_TOKEN",
        "signup_url": "https://dash.cloudflare.com/ai",
        "free_models": {
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast": "Llama 3.3 70B FP8 (Cloudflare edge, fastest)",
            "@cf/meta/llama-4-scout-17b-16e-instruct": "Llama 4 Scout 17B (Cloudflare edge, newest)",
            "@cf/meta/llama-3.1-8b-instruct-fp8-fast": "Llama 3.1 8B FP8 (Cloudflare edge, fast)",
        },
    },
    "cohere": {
        "name": "Cohere",
        "description": "Trial API. 1K calls/month free. No CC. Command R+, RAG-optimized models.",
        "base_url": "https://api.cohere.ai/compatibility/v1",
        "default_model": "command-r-plus-08-2024",
        "rate_limit_rpm": 20,
        "key_prefix": "",
        "env_var": "COHERE_API_KEY",
        "signup_url": "https://dashboard.cohere.com/api-keys",
        "free_models": {
            "command-r-plus-08-2024": "Command R+ 08-2024 (best, RAG-optimized, 128K context)",
            "command-r-08-2024": "Command R 08-2024 (efficient, multilingual, 128K context)",
            "command-a-plus-05-2026": "Command A+ 05-2026 (newest, strongest reasoning)",
        },
    },
    "cerebras": {
        "name": "Cerebras Cloud",
        "description": "Wafer-scale inference. NOTE 2026-10-14: permanent free tier is over — now $5/30-day payment-gated trial. Stored key returns 402.",
        "base_url": "https://api.cerebras.ai/v1",
        "default_model": "gpt-oss-120b",
        "rate_limit_rpm": 30,
        "key_prefix": "",
        "env_var": "CEREBRAS_API_KEY",
        "signup_url": "https://cloud.cerebras.ai",
        "free_models": {
            "gpt-oss-120b": "GPT OSS 120B (production, ~3000 tok/s)",
            "zai-glm-4.7": "GLM 4.7 355B (preview, ~1000 tok/s)",
        },
    },
    "nvidia_nim": {
        "name": "NVIDIA NIM",
        "description": "Rate-limited trial. Permanent free access. 40 RPM. No credit card. IDs verified against live /v1/models catalog (80 models) Oct 14 2026.",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "openai/gpt-oss-20b",
        "rate_limit_rpm": 40,
        "key_prefix": "nvapi-",
        "env_var": "NVIDIA_NIM_API_KEY",
        "signup_url": "https://build.nvidia.com",
        "free_models": {
            "deepseek-ai/deepseek-v4.1-flash": "DeepSeek V4.1 Flash (fast inference)",
            "nvidia/llama-3.1-nemotron-ultra-253b-v1": "Nemotron Ultra 253B (NVIDIA flagship)",
            "nvidia/nemotron-4-340b-instruct": "Nemotron 4 340B (NVIDIA instruct)",
            "nvidia/nemotron-3-ultra-550b-a55b": "Nemotron Ultra 550B (NVIDIA largest)",
            "nvidia/nemotron-3-super-120b-a12b": "Nemotron Super 120B (NVIDIA MoE)",
            "nvidia/nemotron-3.5-lightning-30b-a3b": "Nemotron Lightning 30B (fast MoE)",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning": "Nemotron Nano Omni Reasoning 30B",
            "nvidia/nemotron-3.5-content-safety": "Nemotron Content Safety",
            "moonshotai/kimi-k3": "Kimi K3 (Moonshot, 1M context)",
            "z-ai/glm-5.3-flash": "GLM 5.3 Flash (Z-AI, fast)",
            "openai/gpt-oss-20b": "GPT OSS 20B (OpenAI open weights)",
            "mistralai/mistral-large": "Mistral Large (latest release)",
            "mistralai/codestral-22b-instruct-v0.1": "Codestral 22B (Mistral, code)",
            "ibm/granite-34b-code-instruct": "Granite 34B Code (IBM, code specialized)",
            "google/gemma-4-31b-it": "Gemma 4 31B (Google, latest)",
            "meta/codellama-70b": "CodeLlama 70B (Meta, code)",
        },
    },
    "ollama": {
        "name": "Ollama (Local)",
        "description": "Local inference. Zero cost. No API key. No rate limits. No internet. Runs on your hardware.",
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3.2:1b",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "OLLAMA_API_KEY",
        "signup_url": "https://ollama.com",
        "requires_auth": False,
        "free_models": {
            "llama3.2": "Llama 3.2 (Meta, lightweight, fast, local)",
            "llama3.2:1b": "Llama 3.2 1B (Meta, ultra-light, local)",
            "gemma2:2b": "Gemma 2 2B (Google, efficient, local)",
            "gemma3:4b": "Gemma 3 4B (Google, latest, local)",
            "llava:7b": "LLaVA 7B (vision-language, local)",
            "minicpm-v": "MiniCPM-V (vision-language, local)",
            "moondream": "Moondream (vision-language, local)",
            "nomic-embed-text": "Nomic Embed Text (embeddings, local)",
            "eve-unleashed": "Eve Unleashed (local)",
            "olmoe-1b-7b": "OLMoE 1B-7B (Ollama, sam860/olmoe-1b-7b-0924, local MoE)",
        },
    },
    "mistral": {
        "name": "Mistral AI",
        "description": "Free Experiment plan. ~1B tokens/month. Strong models.",
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-large-latest",
        "rate_limit_rpm": 15,
        "key_prefix": "",
        "env_var": "MISTRAL_API_KEY",
        "signup_url": "https://console.mistral.ai/api-keys",
        "free_models": {
            "mistral-large-latest": "Mistral Large (top-tier reasoning)",
            "mistral-small-latest": "Mistral Small (fast, efficient)",
            "codestral-latest": "Codestral (code generation)",
            "mistral-nemo-latest": "Mistral Nemo (multilingual, latest)",
        },
    },
    "github": {
        "name": "GitHub Models",
        "description": "[RETIRED by GitHub 2026-07-30 — playground, catalog and inference API shut down. Entry kept for history.]",
        "base_url": "https://models.github.ai/inference",
        "default_model": "gpt-4o-mini",
        "rate_limit_rpm": 10,
        "key_prefix": "",
        "env_var": "GITHUB_TOKEN",
        "signup_url": "https://github.com/settings/tokens",
        "retired": True,
        "retired_date": "2026-07-30",
        "retired_reason": "GitHub Models playground, catalog and inference API shut down",
        "free_models": {
            "gpt-4o-mini": "GPT-4o Mini (OpenAI, fast)",
            "gpt-4o": "GPT-4o (OpenAI, frontier)",
            "o4-mini": "o4-mini (OpenAI, reasoning)",
            "deepseek/deepseek-r1": "DeepSeek R1 (reasoning)",
        },
    },
    "huggingface": {
        "name": "Hugging Face Inference",
        "description": "Free tier, no credit card. Serverless inference for thousands of models.",
        "base_url": "https://router.huggingface.co/v1",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct",
        "rate_limit_rpm": 10,
        "key_prefix": "",  # hf_ or rc_ prefix both valid
        "env_var": "HF_TOKEN",
        "signup_url": "https://huggingface.co/settings/tokens",
        "free_models": {
            "meta-llama/Llama-3.3-70B-Instruct": "Llama 3.3 70B (Hugging Face)",
            "Qwen/Qwen2.5-72B-Instruct": "Qwen 2.5 72B (Hugging Face)",
            "deepseek-ai/DeepSeek-R1": "DeepSeek R1 (Hugging Face)",
            "microsoft/Phi-4-mini-instruct": "Phi-4 Mini (Microsoft, efficient)",
        },
    },
    "ovhcloud": {
        "name": "OVHcloud AI Endpoints",
        "description": "Anonymous free tier. No API key required. 2 RPM. OpenAI-compatible.",
        "base_url": "https://endpoints.kepler.ai",
        "default_model": "Llama-3.1-70B-Instruct",
        "rate_limit_rpm": 2,
        "key_prefix": "",
        "env_var": "OVHCLOUD_API_KEY",
        "signup_url": "https://www.ovhcloud.com/en/public-cloud/ai/endpoints/",
        "requires_auth": False,
        "free_models": {
            "Llama-3.1-70B-Instruct": "Llama 3.1 70B (OVHcloud, anonymous)",
            "Mistral-7B-Instruct-v0.3": "Mistral 7B (OVHcloud, anonymous)",
            "Mixtral-8x7B-Instruct-v0.1": "Mixtral 8x7B (OVHcloud, anonymous)",
        },
    },
    "siliconflow": {
        "name": "SiliconFlow",
        "description": "200+ open models. Generous free tier. No credit card. Qwen, DeepSeek, Llama.",
        "base_url": "https://api.siliconflow.com/v1",
        "default_model": "Qwen/Qwen3-32B",
        "rate_limit_rpm": 30,
        "key_prefix": "",
        "env_var": "SILICONFLOW_API_KEY",
        "signup_url": "https://siliconflow.cn",
        "free_models": {
            "Qwen/Qwen3-32B": "Qwen3 32B (Alibaba, multilingual, fastest)",
            "Qwen/Qwen2.5-72B-Instruct": "Qwen 2.5 72B (Alibaba, general-purpose)",
            "deepseek-ai/DeepSeek-R1": "DeepSeek R1 (deep reasoning)",
            "deepseek-ai/DeepSeek-V3": "DeepSeek V3 (fast, efficient)",
            "meta-llama/Meta-Llama-3.1-8B-Instruct": "Llama 3.1 8B (Meta, lightweight)",
            "meta-llama/Meta-Llama-3.3-70B-Instruct": "Llama 3.3 70B (Meta, general)",
        },
    },

    "zhipu": {
        "name": "Zhipu AI (Z.ai)",
        "description": "GLM series. Free tier. No credit card. Strong bilingual models. Sign up at z.ai.",
        "base_url": "https://api.z.ai/api/paas/v4",
        "default_model": "glm-4.5",
        "rate_limit_rpm": 30,
        "key_prefix": "",
        "env_var": "ZHIPU_API_KEY",
        "signup_url": "https://z.ai/manage-apikey/apikey-list",
        "free_models": {
            "glm-4.5": "GLM-4.5 (free tier, bilingual, stable)",
            "glm-4.5-air": "GLM-4.5 Air (lightweight, efficient)",
            "glm-4.6": "GLM-4.6 (advanced reasoning)",
        },
    },
    "pollinations": {
        "name": "Pollinations.ai",
        "description": "Keyless free inference. OpenAI-compatible. Verified live 2026-10-14 (incl. with dummy bearer). ~1 req/15s anonymous, higher with free key.",
        "base_url": "https://text.pollinations.ai/openai",
        "default_model": "openai",
        "rate_limit_rpm": 4,
        "key_prefix": "",
        "env_var": "POLLINATIONS_API_KEY",
        "signup_url": "https://pollinations.ai",
        "requires_auth": False,
        "free_models": {
            "openai": "Pollinations default (auto-routed, keyless)",
        },
    },
    "ollama_native": {
        "name": "Ollama Native (Local)",
        "description": "Local inference via native API. Zero cost. No API key. No rate limits. NOT OpenAI-compatible.",
        "base_url": "http://localhost:11434/api/generate",
        "default_model": "llama3.2:1b",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "OLLAMA_NATIVE_API_KEY",
        "signup_url": "https://ollama.com",
        "requires_auth": False,
        "free_models": {
            "llama3.2": "Llama 3.2 (Meta, lightweight, fast, local)",
            "llama3.2:1b": "Llama 3.2 1B (Meta, ultra-light, local)",
            "gemma2:2b": "Gemma 2 2B (Google, efficient, local)",
            "gemma3:4b": "Gemma 3 4B (Google, latest, local)",
            "llava:7b": "LLaVA 7B (vision-language, local)",
            "minicpm-v": "MiniCPM-V (vision-language, local)",
            "moondream": "Moondream (vision-language, local)",
            "nomic-embed-text": "Nomic Embed Text (embeddings, local)",
            "eve-unleashed": "Eve Unleashed (local)",
        },
    },
    "gemini_native": {
        "name": "Gemini Native (Google)",
        "description": "Google Gemini native API via generateContent. NOT OpenAI-compatible. Free tier, generous. Uses own RPC protocol. IDs from live /v1beta/models list (50 models) Oct 14 2026.",
        "base_url": "https://generativelanguage.googleapis.com",
        "default_model": "gemini-3.8-flash",
        "rate_limit_rpm": 15,
        "key_prefix": "",
        "env_var": "GEMINI_API_KEY",
        "signup_url": "https://aistudio.google.com/apikey",
        "requires_auth": True,
        "free_models": {
            "gemini-3.8-flash": "Gemini 3.8 Flash (current)",
            "gemini-2.5-flash": "Gemini 2.5 Flash (stable)",
            "gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite (fastest)",
        },
    },
    "huggingface_native": {
        "name": "HuggingFace Native",
        "description": "HuggingFace native inference API. NOT OpenAI-compatible. Task-specific endpoints. Free tier, rate-limited.",
        "base_url": "https://api-inference.huggingface.co",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct",
        "rate_limit_rpm": 10,
        "key_prefix": "hf_",
        "env_var": "HF_TOKEN",
        "signup_url": "https://huggingface.co/settings/tokens",
        "requires_auth": True,
        "free_models": {
            "meta-llama/Llama-3.3-70B-Instruct": "Llama 3.3 70B (HuggingFace)",
            "Qwen/Qwen2.5-72B-Instruct": "Qwen 2.5 72B (HuggingFace)",
            "deepseek-ai/DeepSeek-R1": "DeepSeek R1 (HuggingFace)",
        },
    },
    "llama_cpp_direct": {
        "name": "LlamaCpp Direct (Local)",
        "description": "In-process GGUF inference via llama-cpp-python. No HTTP, no SDK, no API. NOT OpenAI-compatible.",
        "base_url": "",
        "default_model": "llama3.2-1b",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "LLAMA_CPP_MODEL_PATH",
        "signup_url": "https://github.com/ggml-org/llama.cpp",
        "requires_auth": False,
        "free_models": {
            "llama3.2-1b": "Llama 3.2 1B (Meta, GGUF, local)",
            "gemma2-2b": "Gemma 2 2B (Google, GGUF, local)",
            "gemma3-4b": "Gemma 3 4B (Google, GGUF, local)",
            "llava-7b": "LLaVA 7B (vision-language, GGUF, local)",
        },
    },
    "huggingface_direct": {
        "name": "HuggingFace Direct (Local)",
        "description": "In-process model inference via transformers. No HTTP, no SDK, no API. NOT OpenAI-compatible.",
        "base_url": "",
        "default_model": "meta-llama/Llama-3.2-1B",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "HF_DIRECT_MODEL",
        "signup_url": "https://huggingface.co/models",
        "requires_auth": False,
        "free_models": {
            "meta-llama/Llama-3.2-1B": "Llama 3.2 1B (Meta, via transformers)",
            "meta-llama/Llama-3.2-3B": "Llama 3.2 3B (Meta, via transformers)",
            "google/gemma-2-2b": "Gemma 2 2B (Google, via transformers)",
            "google/gemma-3-4b-it": "Gemma 3 4B (Google, via transformers)",
        },
    },
    "mistral_fim_native": {
        "name": "Mistral FIM Native",
        "description": "Mistral Fill-in-the-Middle via /v1/fim/completions. NOT OpenAI-compatible. Free experiment plan, ~1B tokens/mo.",
        "base_url": "https://api.mistral.ai",
        "default_model": "codestral-latest",
        "rate_limit_rpm": 15,
        "key_prefix": "",
        "env_var": "MISTRAL_API_KEY",
        "signup_url": "https://console.mistral.ai/api-keys",
        "requires_auth": True,
        "free_models": {
            "codestral-latest": "Codestral (Mistral, code generation, FIM)",
            "codestral-2505": "Codestral 2505 (Mistral, FIM, legacy)",
        },
    },    "cohere_native": {
        "name": "Cohere Native",
        "description": "Cohere native v2/chat API. NOT OpenAI-compatible. Free trial, 1K calls/mo, no CC.",
        "base_url": "https://api.cohere.com",
        "default_model": "command-r-plus-08-2024",
        "rate_limit_rpm": 20,
        "key_prefix": "",
        "env_var": "COHERE_API_KEY",
        "signup_url": "https://dashboard.cohere.com/api-keys",
        "requires_auth": True,
        "free_models": {
            "command-r-plus-08-2024": "Command R+ 08-2024 (best, RAG-optimized, 128K context)",
            "command-r-08-2024": "Command R 08-2024 (efficient, multilingual, 128K context)",
            "command-a-plus-05-2026": "Command A+ 05-2026 (newest, strongest reasoning)",
        },
    },
    "lmstudio": {
        "name": "LM Studio (Local)",
        "description": "Local inference via LM Studio server. OpenAI-compatible API on port 1234. No API key needed. Runs on local GPU with llama.cpp backend. Models at ~/.lmstudio/models/",
        "base_url": "http://localhost:1234/v1",
        "default_model": "local-model",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "LMSTUDIO_API_KEY",
        "signup_url": "https://lmstudio.ai",
        "requires_auth": False,
        "free_models": {
            "local-model": "Currently loaded model in LM Studio",
        },
    },
    # --- Local llama-server endpoints (START_LOCAL_MODEL.bat) ----------------
    #
    # Each local model is served by its OWN llama-server process on its own
    # port, so each gets its own registry entry (default_model must equal the
    # --alias the launcher passes, or the request 404s).
    #
    # requires_auth: False makes _load_provider_config() inject the anonymous
    # placeholder key and has_valid_key() short-circuit True, so no env var is
    # needed -- same pattern as `lmstudio` above.
    #
    # --reasoning off MUST be set SERVER-SIDE (the launcher does it). These are
    # reasoning models: chain-of-thought streams to `reasoning_content` and
    # `content` stays EMPTY until thinking ends, so any client that reads only
    # `content` sees a blank turn. Reproduced as a 4096-token runaway with zero
    # output. If these return empty content, check for "chat template,
    # thinking = 0" in the server log.
    # `llamaserver` is the id the failover chains already reference:
    #   CORE/WORMS/expansion/servemesh.py  CHAIN[0]  = "llamaserver"
    #   CORE/WORMS/expansion/federated.py  PROVIDERS = (..., "llamaserver", ...)
    # It had no registry entry, and spine.py documents that consequence:
    # "A CHAIN entry with no registry entry (e.g. `llamaserver`) is omitted
    #  entirely". So the local model was silently skipped by the mesh rather
    # than being tried. This entry makes the name real.
    #
    # Default port 8080 matches the Rust supervisor's own config.toml. Point it
    # elsewhere without editing code by setting LLAMASERVER_BASE_URL.
    "llamaserver": {
        "name": "llama-server (local, primary)",
        "description": "The primary local llama-server endpoint, by the id the failover chains already use. Defaults to the Rust supervisor on :8080. Override with LLAMASERVER_BASE_URL (e.g. http://localhost:18080/v1). Must run with --reasoning off server-side if it serves a thinking model.",
        "base_url": "http://localhost:8080/v1",
        "base_url_env": "LLAMASERVER_BASE_URL",
        "default_model": "local-model",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "LLAMASERVER_API_KEY",
        "signup_url": "https://github.com/ggml-org/llama.cpp",
        "requires_auth": False,
        "local": True,
        "free_models": {
            "local-model": "Whatever the local llama-server has loaded",
        },
    },
"edge0": {
        "name": "Edge0 Intel (local, :8091 Spark / :8089-92)",
        "description": "edge0-intel project on this box — Intel Arc 140T via Level Zero/torch.xpu (MoE streaming backend) plus llama.cpp SYCL servers on 8089-8092. OpenAI-compatible. Spark fast bench on 8091. Start via edge0-intel scripts. Requires EDGE0_BACKEND=intel.",
        "base_url": "http://localhost:8091/v1",
        "base_url_env": "EDGE0_BASE_URL",
        "default_model": "spark",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "EDGE0_API_KEY",
        "signup_url": "https://github.com/Edge0-AI/Edge0",
        "requires_auth": False,
        "local": True,
        "free_models": {
            "spark": "Spark fast bench (edge0-intel, port 8091)",
            "gemma-4-e4b-it": "Gemma 4 E4B (edge0-intel, port 8090)",
            "ling-abl-s05": "Ling abliterated (edge0-intel, port 8089)",
        },
    },
    "accelerator_sidecar": {
        "name": "Accelerator sidecar (local, :18180)",
        "description": "tools/accelerator_sidecar.py — binds the best available accelerator in failover order (llama.cpp Vulkan on the Intel Arc 140T -> CPU -> adopted live server) and proxies OpenAI-compatible calls; /health reports the ACTUALLY BOUND device. Start with: python tools/accelerator_sidecar.py --port 18180. Override host/port with ACCEL_SIDECAR_BASE_URL.",
        "base_url": "http://localhost:18180/v1",
        "base_url_env": "ACCEL_SIDECAR_BASE_URL",
        "default_model": "local-model",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "ACCEL_SIDECAR_API_KEY",
        "signup_url": "https://github.com/ggml-org/llama.cpp",
        "requires_auth": False,
        "local": True,
        "free_models": {
            "local-model": "Whatever model the sidecar's bound backend has loaded (Qwen2.5-1.5B default)",
        },
    },
    "llama_mythos": {
        "name": "Qwythos-9B (local, llama-server :8081)",
        "description": "Qwythos-9B + MTP speculative decoding on llama-server. Port 8081. Measured 16.35 t/s vs 8.30 t/s plain CPU (draft acceptance 0.9275). Start with START_LOCAL_MODEL.bat mythos. Requires --reasoning off server-side.",
        "base_url": "http://localhost:8081/v1",
        "default_model": "qwythos-9b-mtp",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "LLAMA_MYTHOS_API_KEY",
        "signup_url": "https://github.com/ggml-org/llama.cpp",
        "requires_auth": False,
        "local": True,
        "reasoning_overhead": _LOCAL_REASONING_OVERHEAD,
        "free_models": {
            "qwythos-9b-mtp": "Qwythos-9B (9B, MTP speculative decoding, local)",
        },
    },
    "llama_mythos_v2": {
        "name": "Qwythos-9B-v2 (local, llama-server :8082)",
        "description": "Qwythos-9B-v2 on llama-server. Port 8082. FTPO-trained to cut repetition looping; observed clean 100-175 token turns where v1 ran away to the 4096 cap. No MTP file ships with v2. Start with START_LOCAL_MODEL.bat mythos-v2.",
        "base_url": "http://localhost:8082/v1",
        "default_model": "qwythos-9b-v2",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "LLAMA_MYTHOS_V2_API_KEY",
        "signup_url": "https://github.com/ggml-org/llama.cpp",
        "requires_auth": False,
        "local": True,
        "free_models": {
            "qwythos-9b-v2": "Qwythos-9B v2 (9B, anti-loop FTPO, local)",
        },
    },
    "llama_qwopus": {
        "name": "Qwopus3.5-9B-v3 (local, llama-server :8083)",
        "description": "Qwopus3.5-9B-v3 on llama-server. Port 8083. No MTP file, so no speculative speedup. Start with START_LOCAL_MODEL.bat qwopus. Requires --reasoning off server-side.",
        "base_url": "http://localhost:8083/v1",
        "default_model": "qwopus3.5-9b-v3",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "LLAMA_QWOPUS_API_KEY",
        "signup_url": "https://github.com/ggml-org/llama.cpp",
        "requires_auth": False,
        "local": True,
        # Reasoning overhead. This model streams chain-of-thought to
        # `reasoning_content` and leaves `content` EMPTY until thinking ends (see
        # the note on the local llama-server entries above), so a cap that does
        # not budget for the thinking is spent entirely on it and the turn reads
        # as a blank answer. This entry's measurement is what
        # `_LOCAL_REASONING_OVERHEAD` is set from; it is NOT a fix for runaway
        # thinking, which is what the server-side `--reasoning off` is for.
        # `OpenAIProvider._default_max_tokens()` applies it, and only when the
        # caller did not set an explicit cap.
        "reasoning_overhead": _LOCAL_REASONING_OVERHEAD,
        "free_models": {
            "qwopus3.5-9b-v3": "Qwopus3.5-9B v3 (9B, Q4_K_M, local)",
        },
    },
    "llama_ornith": {
        "name": "Ornith-1.5-9B (local, llama-server :8084)",
        "description": "Ornith-1.5-9B on llama-server. Port 8084. No MTP file, so no speculative speedup. Start with START_LOCAL_MODEL.bat ornith. Requires --reasoning off server-side.",
        "base_url": "http://localhost:8084/v1",
        "default_model": "ornith-1.5-9b",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "LLAMA_ORNITH_API_KEY",
        "signup_url": "https://github.com/ggml-org/llama.cpp",
        "requires_auth": False,
        "local": True,
        "reasoning_overhead": _LOCAL_REASONING_OVERHEAD,
        "free_models": {
            "ornith-1.5-9b": "Ornith-1.5-9B (9B, Q4_K_M, local)",
        },
    },
    "openvino": {
        "name": "OpenVINO (Intel Local)",
        "description": "Local inference via Intel OpenVINO runtime. Uses Intel Arc GPU, AI Boost NPU, or CPU. Runs on your Intel hardware with zero API cost. Accelerated via oneAPI Level Zero.",
        "base_url": "",
        "default_model": "auto",
        "rate_limit_rpm": 9999,
        "key_prefix": "",
        "env_var": "OPENVINO_MODEL_PATH",
        "signup_url": "https://docs.openvino.ai/",
        "requires_auth": False,
        "devices": ["AUTO", "GPU", "NPU", "CPU"],
        "free_models": {
            "auto": "Auto-select best model (set OPENVINO_MODEL_PATH)",
        },
        "device_support": {
            "CPU": "Intel Core Ultra 9 285H (AVX-512, VNNI)",
            "GPU": "Intel Arc 140T (8GB, 128 EUs, Level Zero)",
            "NPU": "Intel AI Boost (lightweight inference accelerator)",
            "AUTO": "Automatic device selection (GPU > NPU > CPU)",
        },
    },
}


# --- Generic OpenAI-Compatible Provider -----------------------------

class OpenAIProvider:
    """Generic inference client for any OpenAI-compatible API.

    Thread-safe. Works with Groq, OpenRouter, Gemini, Cloudflare, Cerebras,
    NVIDIA NIM, Mistral, GitHub, and any other /v1/chat/completions endpoint.

    Features:
    - Chat completions (sync + streaming)
    - Model listing and selection
    - API key from env var or explicit config
    - Rate limit tracking
    - Self-test mode
    """

    VERSION = "1.0.0"

    def __init__(self, provider: Optional[str] = None, config: Optional[ProviderConfig] = None):
        self._lock = threading.Lock()
        self._request_times: List[float] = []
        self._total_requests: int = 0
        self._total_tokens: int = 0
        self._last_error: Optional[str] = None
        self._openai_client: Any = None

        if config is not None:
            self.config = config
            self.provider_name = config.provider
        elif provider and provider in PROVIDER_REGISTRY:
            self.provider_name = provider
            self.config = self._load_provider_config(provider)
        else:
            # Default: try groq, fall back to generic
            self.provider_name = provider or "generic"
            self.config = self._load_provider_config("groq") if provider is None else ProviderConfig(
                provider="generic", base_url="", model=""
            )

    def _default_max_tokens(self) -> int:
        """Config cap, padded by the entry's declared reasoning overhead.

        A reasoning model spends generated tokens on chain-of-thought before it
        emits any `content`, so the usable cap is the declared one minus the
        thinking. `reasoning_overhead` is that declared budget; entries without
        it (every hosted API) are unchanged. A lookup miss is +0 on purpose: an
        unregistered provider must not silently gain a larger cap.

        An EXPLICIT caller-supplied cap is never padded -- it is the caller's
        own budget, and padding it would break any caller that sized the cap
        deliberately (e.g. a bounded probe).
        """
        info: Dict[str, Any] = PROVIDER_REGISTRY.get(self.provider_name) or {}
        return int(self.config.max_tokens) + int(
            info.get("reasoning_overhead", 0) or 0)

    def _load_provider_config(self, name: str) -> ProviderConfig:
        """Load provider config from registry + env vars."""
        if name not in PROVIDER_REGISTRY:
            raise ValueError(
                f"Unknown provider '{name}'. Available: {list(PROVIDER_REGISTRY)}"
            )

        info = PROVIDER_REGISTRY[name]
        env_var = info["env_var"]
        api_key = os.environ.get(env_var, "")

        # Fallback: read from ~/.azazael/{name}_config.json
        if not api_key:
            api_key = _read_config_key(name)

        base_url = info["base_url"]
        # Optional endpoint override: local servers move port (a llama-server
        # on 8080 today may be 18080 tomorrow). Declared per entry so the
        # entry still works unconfigured.
        _base_url_env = info.get("base_url_env")
        if _base_url_env:
            base_url = (os.environ.get(_base_url_env, "") or "").strip() or base_url
        # Substitute Cloudflare account_id placeholder
        if "{account_id}" in base_url:
            account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
            if not account_id:
                try:
                    _cf = json.loads((Path.home() / ".azazael" / "cloudflare_config.json").read_text(encoding="utf-8"))
                    account_id = _cf.get("account_id", "") or ""
                except (OSError, ValueError):
                    account_id = ""
            if not account_id:
                # Don't produce a malformed URL — will be caught by has_valid_key() check
                base_url = base_url.replace("{account_id}", "MISSING_ACCOUNT_ID")
            else:
                base_url = base_url.replace("{account_id}", account_id)

        requires_auth = info.get("requires_auth", True)
        # Anonymous providers: use a dummy key for OpenAI client compatibility
        if not requires_auth and not api_key:
            api_key = "anonymous"

        return ProviderConfig(
            provider=name,
            api_key=api_key,
            base_url=base_url,
            model=os.environ.get(f"{name.upper()}_MODEL", str(info["default_model"])),
            rate_limit_rpm=info.get("rate_limit_rpm", 30),
            key_prefix=info.get("key_prefix", ""),
            signup_url=info.get("signup_url", ""),
            extra_headers=info.get("extra_headers", {}),
            requires_auth=requires_auth,
        )

    # -- Configuration -----------------------------------------------

    def save_config(self) -> bool:
        """Save current config to ~/.azazael/{provider}_config.json."""
        config_file = Path.home() / ".azazael" / f"{self.provider_name}_config.json"
        try:
            config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(config_file, "w", encoding="utf-8") as fh:
                json.dump({
                    "api_key": self.config.api_key,
                    "base_url": self.config.base_url,
                    "model": self.config.model,
                }, fh, indent=2)
            return True
        except OSError as exc:                    # noqa: BLE001 - config save failed
            note_failure("providers:config-save-failed", exc)
            return False

    def set_api_key(self, key: str) -> None:
        """Set the API key at runtime. Saves to config file."""
        self.config.api_key = key
        self.save_config()

    def has_valid_key(self) -> bool:
        """Check if the API key looks valid (or not required).

        Also validates provider-specific requirements:
        - Cloudflare: requires CLOUDFLARE_ACCOUNT_ID env var (previously caused 404)
        """
        if not getattr(self.config, "requires_auth", True):
            return True  # Anonymous provider, no key needed
        key = self.config.api_key
        if not key:
            return False
        if self.config.key_prefix and not key.startswith(self.config.key_prefix):
            return False
        # Cloudflare-specific: account_id must be set (fixes 404 routing error)
        if self.provider_name == "cloudflare" and "MISSING_ACCOUNT_ID" in self.config.base_url:
            return False
        return True

    # -- Client factory ----------------------------------------------

    def _get_client(self) -> Any:
        """Get or lazily create the cached OpenAI client."""
        if OpenAI is None:
            raise RuntimeError(
                "openai package unavailable on this interpreter "
                "(pydantic_core has no 3.14t build); use a stdlib HTTP "
                "client or plain CPython for live OpenAI-SDK calls")
        if self._openai_client is None:
            kwargs: Dict[str, Any] = dict(
                base_url=self.config.base_url,
                api_key=self.config.api_key,
                timeout=self.config.timeout_seconds,
                max_retries=self.config.max_retries,
            )
            # Add extra headers (OpenRouter needs HTTP-Referer)
            if self.config.extra_headers:
                kwargs["default_headers"] = self.config.extra_headers

            self._openai_client = OpenAI(**kwargs)
        return self._openai_client

    # -- Rate limiting -----------------------------------------------

    def _check_rate_limit(self) -> None:
        """Enforce rate limits. Sleeps OUTSIDE the lock."""
        now = time.time()
        window = 60.0
        wait = 0.0

        with self._lock:
            self._request_times = [t for t in self._request_times if now - t < window]

            if len(self._request_times) >= self.config.rate_limit_rpm:
                oldest = self._request_times[0]
                wait = window - (now - oldest) + 0.5

            self._request_times.append(now)

        if wait > 0:
            time.sleep(wait)

    # -- Model listing -----------------------------------------------

    def list_models(self) -> List[ModelInfo]:
        """List free-tier models for this provider."""
        if self.provider_name in PROVIDER_REGISTRY:
            models = PROVIDER_REGISTRY[self.provider_name].get("free_models", {})
            return [ModelInfo(id=mid, description=desc) for mid, desc in models.items()]
        return []

    # -- Chat completions --------------------------------------------

    def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send a chat completion request.

        Checks circuit breaker before making the request and records
        success/failure afterward for provider health monitoring.

        Returns:
            Dict with 'content', 'model', 'usage', 'finish_reason', 'elapsed_ms'.
            On error: 'error' key with error message.
"""
        if not self.has_valid_key():
            return {
                "error": (
                    f"No valid {self.provider_name.upper()} API key. "
                    f"Set {self.provider_name.upper()}_API_KEY env var. "
                    f"Get a free key at: {self.config.signup_url}"
                )
            }
        
        # Check if provider is retired
        provider_info = PROVIDER_REGISTRY.get(self.provider_name, {})
        if provider_info.get("retired", False):
            retired_date = provider_info.get("retired_date", "unknown")
            retired_reason = provider_info.get("retired_reason", "Provider retired")
            return {
                "error": (
                    f"Provider '{self.provider_name}' is retired as of {retired_date}: "
                    f"{retired_reason}. This provider is no longer available."
                ),
                "elapsed_ms": 0,
                "provider": self.provider_name,
                "retired": True,
            }
        
        # -- Circuit breaker check --
        breaker = _global_breaker
        if not breaker.allow_request(self.provider_name):
            return {
                "error": (
                    f"Circuit breaker OPEN for {self.provider_name}. "
                    f"Provider is temporarily disabled due to repeated failures."
                ),
                "elapsed_ms": 0,
                "provider": self.provider_name,
                "circuit_open": True,
            }

        self._check_rate_limit()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        start = time.time()
        try:
            client = self._get_client()
            # Build kwargs dynamically — some providers (Cohere) reject `top_p` or `n`
            kwargs = dict(
                model=model or self.config.model,
                messages=messages,
                max_tokens=max_tokens or self._default_max_tokens(),
                temperature=temperature if temperature is not None else self.config.temperature,
            )
            # Only send OpenAI-standard params for providers that support them
            if self.provider_name != "cohere":
                kwargs["top_p"] = self.config.top_p
                kwargs["n"] = 1
            response = client.chat.completions.create(**kwargs)

            elapsed_ms = int((time.time() - start) * 1000)

            # Validate response structure — some providers (e.g., GitHub Models)
            # may return error strings instead of proper response objects
            if not hasattr(response, 'choices') or not response.choices:
                error_msg = f"Invalid response from {self.provider_name}: {response}"
                note_failure(f"providers:invalid-response:{self.provider_name}", 
                             Exception(error_msg))
                breaker.record_failure(self.provider_name, error_msg)
                return {
                    "error": error_msg,
                    "elapsed_ms": elapsed_ms,
                    "provider": self.provider_name,
                }

            choice = response.choices[0]

            result: Dict[str, Any] = {
                "content": choice.message.content or "",
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                },
                "finish_reason": choice.finish_reason or "unknown",
                "elapsed_ms": elapsed_ms,
                "provider": self.provider_name,
            }

            with self._lock:
                self._total_requests += 1
                self._total_tokens += result["usage"]["total_tokens"]
                self._last_error = None

            # Record success in circuit breaker
            breaker.record_success(self.provider_name)

            # Record to inference cache for file-backed citations (RULE 0Q-A)
            try:
                if _cache_record_inference is not None:
                        _cache_record_inference(
                        self.provider_name,
                        result.get("model", "unknown"),
                        prompt,
                        result.get("content", ""),
                        {
                            "total_tokens": result.get("usage", {}).get("total_tokens", 0),
                            "elapsed_ms": result.get("elapsed_ms", 0),
                        },
                    )
            except Exception as exc:  # noqa: BLE001 — graceful degradation, never crash chat()
                note_failure("providers:cache-record", exc)
                pass

            # Record the turn to PAMS persistent memory (same store the worms
            # write to). Never raises; a memory failure is not a call failure.
            if _memory_record_turn is not None:
                try:
                    _memory_record_turn(
                        provider=self.provider_name,
                        model=result.get("model", "unknown"),
                        prompt=prompt,
                        content=result.get("content", "") or "",
                        system_prompt=system_prompt,
                        usage=result.get("usage", {}),
                        elapsed_ms=result.get("elapsed_ms", 0),
                        finish_reason=result.get("finish_reason"),
                    )
                except Exception as exc:  # noqa: BLE001 — memory must never break chat()
                    note_failure("providers:memory-record-turn", exc)
                    pass

            return result

        except Exception as e:  # noqa: BLE001 — provider call must degrade, never crash
            note_failure("providers:chat-degraded", e)
            elapsed_ms = int((time.time() - start) * 1000)
            self._last_error = str(e)

            # Record failure in circuit breaker
            breaker.record_failure(self.provider_name, str(e))

            if _memory_record_failure is not None:
                try:
                    _memory_record_failure(
                        provider=self.provider_name,
                        prompt=prompt,
                        error=str(e),
                        elapsed_ms=elapsed_ms,
                    )
                except Exception as exc:  # noqa: BLE001 — memory must never break chat()
                    note_failure("providers:memory-record-failure", exc)
                    pass

            return {
                "error": str(e),
                "elapsed_ms": elapsed_ms,
                "provider": self.provider_name,
            }

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Remove <think>...</think> preamble and markdown fences.

        Reasoning models stream chain-of-thought first (sometimes into
        `reasoning_content`, sometimes inline); either way the payload
        parser must never see it. Returns the remainder stripped.
        """
        out = text or ""
        while True:
            start = out.find("<think>")
            end = out.find("</think>", start + 7) if start >= 0 else -1
            if start < 0 or end < 0:
                break
            out = out[:start] + out[end + 8:]
        out = out.strip()
        if out.startswith("```"):
            out = out[3:]
            nl = out.find("\n")
            out = out[nl + 1:] if nl >= 0 else ""
            if out.rstrip().endswith("```"):
                out = out.rstrip()[:-3]
        return out.strip()

    @staticmethod
    def _parse_structured(text: str) -> tuple[Any, str]:
        """Parse a schema-bound payload. Returns (object|None, path).

        Paths: `direct` (clean json.loads), `extracted` (outermost {...}
        after thinking/fence stripping), `failed`. Never raises.
        """
        cleaned = OpenAIProvider._strip_thinking(text)
        try:
            return json.loads(cleaned), "direct"
        except (ValueError, TypeError) as exc:
            note_failure("providers:structured-parse-direct", exc)
        try:
            start = cleaned.find("{")
            if start >= 0:
                obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
                return obj, "extracted"
        except (ValueError, TypeError) as exc:
            note_failure("providers:structured-parse-extract", exc)
        return None, "failed"

    def structured_chat(
        self,
        prompt: str,
        schema: Dict[str, Any],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        force_tool_fallback: bool = True,
    ) -> Dict[str, Any]:
        """Schema-bound completion with graceful provider degradation.

        Ladder, first success wins (each step recorded in `path`):
          1. `response_format` json_schema + thinking disabled + temp 0.0.
          2. Same call without `response_format` (providers that reject it,
             e.g. GLM-through-litellm which silently drops it).
          3. Forced single tool call carrying the schema (schema-valid args
             back even where response_format is unsupported).
        A thinking preamble is stripped before parsing because reasoning
        traces otherwise eat the token budget and truncate the payload.
        On total failure returns an `error` dict -- prose is never
        returned as success.
        """
        if not self.has_valid_key():
            return {
                "error": (
                    f"No valid {self.provider_name.upper()} API key. "
                    f"Set {self.provider_name.upper()}_API_KEY env var."
                ),
                "parsed": False,
                "path": "no-key",
                "provider": self.provider_name,
            }
        breaker = _global_breaker
        if not breaker.allow_request(self.provider_name):
            return {
                "error": (
                    f"Circuit breaker OPEN for {self.provider_name}. "
                    f"Provider is temporarily disabled due to repeated failures."
                ),
                "elapsed_ms": 0,
                "parsed": False,
                "path": "circuit-open",
                "provider": self.provider_name,
                "circuit_open": True,
            }
        self._check_rate_limit()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        cap = max_tokens or self._default_max_tokens()
        start = time.time()

        def _once(extra: Dict[str, Any]) -> Dict[str, Any]:
            kwargs: Dict[str, Any] = dict(
                model=model or self.config.model,
                messages=messages,
                max_tokens=cap,
                temperature=0.0,
            )
            kwargs.update(extra)
            if self.provider_name != "cohere":
                kwargs["top_p"] = 1.0
                kwargs["n"] = 1
            client = self._get_client()
            response = client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            message = choice.message
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                args = getattr(tool_calls[0].function, "arguments", "") or ""
                return {"raw": args, "via": "tool",
                        "model": response.model,
                        "usage": response.usage, "choice": choice}
            return {"raw": message.content or "", "via": "content",
                    "model": response.model,
                    "usage": response.usage, "choice": choice}

        def _usage(usage: Any) -> Dict[str, int]:
            try:
                return {
                    "prompt_tokens": usage.prompt_tokens if usage else 0,
                    "completion_tokens": usage.completion_tokens if usage else 0,
                    "total_tokens": usage.total_tokens if usage else 0,
                }
            except AttributeError as exc:
                note_failure("providers:structured-usage-shape", exc)
                return {"prompt_tokens": 0, "completion_tokens": 0,
                        "total_tokens": 0}

        def _succeed(raw: str, path: str, out: Dict[str, Any]) -> Dict[str, Any]:
            obj, parse_path = self._parse_structured(raw)
            elapsed_ms = int((time.time() - start) * 1000)
            full_path = path if parse_path == "direct" else path + "+" + parse_path
            if obj is None:
                breaker.record_failure(
                    self.provider_name, "structured parse failed: " + full_path)
                self._last_error = "structured parse failed: " + full_path
                return {
                    "error": "response was not valid JSON",
                    "raw": (raw or "")[:500],
                    "parsed": False,
                    "path": full_path,
                    "elapsed_ms": elapsed_ms,
                    "provider": self.provider_name,
                }
            with self._lock:
                self._total_requests += 1
                usage = _usage(out.get("usage"))
                self._total_tokens += usage["total_tokens"]
                self._last_error = None
            breaker.record_success(self.provider_name)
            choice = out.get("choice")
            return {
                "content": obj,
                "parsed": True,
                "path": full_path,
                "model": out.get("model"),
                "usage": usage,
                "finish_reason": getattr(choice, "finish_reason", "unknown")
                or "unknown",
                "elapsed_ms": elapsed_ms,
                "provider": self.provider_name,
            }

        schema_block = {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "azazael_structured",
                                "strict": True, "schema": schema},
            },
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
        try:
            out = _once(schema_block)
            return _succeed(out["raw"], "schema", out)
        except Exception as exc:  # noqa: BLE001 -- degrade through the ladder
            note_failure("providers:structured-schema-step", exc)
        try:
            out = _once({})
            return _succeed(out["raw"], "plain", out)
        except Exception as exc:  # noqa: BLE001 -- degrade through the ladder
            note_failure("providers:structured-plain-step", exc)
        if force_tool_fallback:
            try:
                out = _once({
                    "tools": [{"type": "function",
                               "function": {"name": "azazael_structured",
                                            "description": "Return the answer "
                                            "as JSON matching the schema.",
                                            "parameters": schema}}],
                    "tool_choice": {"type": "function",
                                    "function": {"name": "azazael_structured"}},
                })
                if out.get("via") == "tool":
                    return _succeed(out["raw"], "tool", out)
                note_failure("providers:structured-tool-no-call", None)
                return _succeed(out["raw"], "tool-no-call", out)
            except Exception as exc:  # noqa: BLE001 -- final rung, then honest error
                note_failure("providers:structured-tool-step", exc)
        elapsed_ms = int((time.time() - start) * 1000)
        self._last_error = "all structured rungs failed"
        breaker.record_failure(self.provider_name, "all structured rungs failed")
        return {
            "error": "all structured rungs failed (schema, plain, tool)",
            "parsed": False,
            "path": "failed",
            "elapsed_ms": elapsed_ms,
            "provider": self.provider_name,
        }

    def chat_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Iterator[str]:
        """Stream a chat completion, yielding content chunks."""
        if not self.has_valid_key():
            yield f"[ERROR] No {self.provider_name} API key. Sign up: {self.config.signup_url}"
            return

        self._check_rate_limit()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            client = self._get_client()
            stream = client.chat.completions.create(
                model=model or self.config.model,
                messages=messages,
                max_tokens=self._default_max_tokens(),
                temperature=self.config.temperature,
                stream=True,
            )

            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

            with self._lock:
                self._total_requests += 1

        except Exception as e:  # noqa: BLE001 — stream must degrade, never crash
            note_failure("providers:stream-degraded", e)
            self._last_error = str(e)
            yield f"\n[ERROR] {e}"

    # -- Stats -------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get client usage statistics."""
        with self._lock:
            recent = len([t for t in self._request_times if time.time() - t < 60])
        return {
            "provider": self.provider_name,
            "total_requests": self._total_requests,
            "total_tokens": self._total_tokens,
            "requests_last_minute": recent,
            "rate_limit_per_minute": self.config.rate_limit_rpm,
            "last_error": self._last_error,
            "model": self.config.model,
        }

    # -- Self-test ---------------------------------------------------

    def self_test(self, verbose: bool = True) -> Dict[str, Any]:
        """Run self-test (no API call, no credit consumption)."""
        results: Dict[str, Any] = {
            "provider": self.provider_name,
            "config_valid": False,
            "key_format_ok": False,
            "models_available": 0,
            "import_ok": False,
            "issues": [],
        }

        issues = self.config.validate()
        results["config_valid"] = len(issues) == 0
        results["issues"].extend(issues)
        results["key_format_ok"] = self.has_valid_key()

        models = self.list_models()
        results["models_available"] = len(models)

        try:
            from openai import OpenAI  # noqa: F401
            results["import_ok"] = True
        except ImportError as exc:
            note_failure("providers:self-test-import", exc)
            results["issues"].append("openai package not installed")
            results["import_ok"] = False

        if verbose:
            self._print_test_results(results)

        return results

    def _print_test_results(self, results: Dict[str, Any]) -> None:
        """Print self-test results."""
        name = PROVIDER_REGISTRY.get(self.provider_name, {}).get("name", self.provider_name)
        print(f"\n{'='*60}")
        print(f"  {name} — Self-Test")
        print(f"{'='*60}")

        checks = [
            ("openai import", results["import_ok"]),
            ("Config valid", results["config_valid"]),
            ("API key format", results["key_format_ok"]),
            (f"Models ({results['models_available']})", results["models_available"] > 0),
        ]
        for check_name, passed in checks:
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {check_name}")

        if results["issues"]:
            print(f"\n  Issues:")
            for issue in results["issues"]:
                print(f"    - {issue}")

        if not results["key_format_ok"]:
            info: Dict[str, Any] = PROVIDER_REGISTRY.get(self.provider_name, {})
            if info.get("signup_url"):
                print(f"\n  Get a free API key: {info['signup_url']}")
                print(f"  Then: export {info.get('env_var', 'API_KEY')}=your-key")

        all_pass = all(p for _, p in checks)
        print(f"\n  {'ALL CHECKS PASSED' if all_pass else 'CHECKS FAILED (expected without API key)'}")
        print(f"{'='*60}\n")

    def __repr__(self) -> str:
        name = PROVIDER_REGISTRY.get(self.provider_name, {}).get("name", self.provider_name)
        key_status = "configured" if self.has_valid_key() else "missing"
        return f"OpenAIProvider({name}, model={self.config.model}, key={key_status})"



# === NON-OPENAI-COMPATIBLE FRAMEWORK ============================
# Framework for integrating ANY free internet API endpoint that
# does NOT use the OpenAI /v1/chat/completions format.
#
# To add a new provider:
#   1. Subclass NativeAPIProvider
#   2. Set framework_config with your API details
#   3. Implement _parse_response()
#   4. Register in PROVIDER_REGISTRY with native_framework=True
#
# Example providers using this framework:
#   - See 'ollama_native_framework' in PROVIDER_REGISTRY
# ================================================================

from abc import ABC, abstractmethod
import subprocess


@dataclass
class NativeFrameworkConfig:
    """Configuration template for any non-OpenAI-compatible internet API.
    
    Fields:
        api_format: How to structure the API request
            'raw_prompt' - send prompt as-is (default)
            'json_body' - send JSON with custom fields
            'form_data' - send as form data
        endpoint: API endpoint path (e.g., '/api/generate')
        method: HTTP method ('POST', 'GET')
        auth_header: Format of auth header (e.g., 'Bearer {key}', 'X-API-Key: {key}')
        error_path: JSON path to error message in response (e.g., 'error.message')
        default_model: Default model name
    """
    api_format: str = 'json_body'
    endpoint: str = '/api/generate'
    method: str = 'POST'
    auth_header: str = ''
    auth_scheme: str = 'header'
    auth_param_name: str = 'key'          # Query param name for 'query_param' auth
    error_path: str = 'error'
    response_path: str = ''
    model_field: str = 'model'
    prompt_field: str = 'prompt'
    max_tokens_field: str = 'max_tokens'
    temperature_field: str = 'temperature'
    timeout_seconds: float = 30.0
    requires_auth: bool = False
    rate_limit_rpm: int = 9999


class NativeAPIProvider(ABC):
    """Abstract framework for ANY non-OpenAI-compatible free internet API.
    
    Subclass this to integrate any internet API endpoint that uses
    its own protocol instead of OpenAI's /v1/chat/completions.
    
    The framework handles:
    - HTTP communication (urllib.request)
    - Auth headers
    - Rate limiting
    - Error handling
    - Lock-protected stats
    
    Subclasses must implement:
    - _build_request(prompt, **kwargs) -> dict: Build the request body
    - _parse_response(response_data: dict) -> dict: Parse the API response
    
    Optional overrides:
    - _get_headers() -> dict: Custom HTTP headers
    - _get_endpoint() -> str: Custom API endpoint URL
    """
    VERSION = "1.0.0"
    
    def __init__(self, provider: str = ''):
        self._lock = threading.Lock()
        self._total_requests = 0
        self._total_tokens = 0
        self._last_error = None
        self.provider_name = provider
        self.config = self._load_config()
        self.framework_config = self._get_framework_config()
    
    def _load_config(self):
        info: Dict[str, Any] = PROVIDER_REGISTRY.get(self.provider_name, {})
        api_key = os.environ.get(info.get('env_var', ''), '')
        if not api_key:
            api_key = _read_config_key(self.provider_name)
        if not api_key:
            base = {'gemini_native': 'gemini', 'huggingface_native': 'huggingface',
                    'mistral_fim_native': 'mistral', 'cohere_native': 'cohere'}.get(self.provider_name)
            if base:
                api_key = _read_config_key(base)
        return ProviderConfig(
            provider=self.provider_name,
            api_key=api_key,
            base_url=info.get('base_url', ''),
            model=os.environ.get(
                f'{self.provider_name.upper()}_MODEL',
                str(info.get('default_model', ''))
            ),
            rate_limit_rpm=info.get('rate_limit_rpm', 9999),
            key_prefix=info.get('key_prefix', ''),
            signup_url=info.get('signup_url', ''),
            requires_auth=info.get('requires_auth', False),
        )
    
    @abstractmethod
    def _get_framework_config(self) -> NativeFrameworkConfig:
        """Return framework config for this provider."""
        pass
    
    @abstractmethod
    def _build_request(self, prompt: str, **kwargs) -> dict:
        """Build the request body for the specific API format."""
        pass
    
    @abstractmethod
    def _parse_response(self, response_data: dict) -> dict:
        """Parse the API response into standardized format.
        
        Must return dict with keys:
            'content': str - generated text
            'model': str - model used
            'usage': dict with prompt_tokens, completion_tokens, total_tokens
            Optionally: 'finish_reason'
        """
        pass
    
    def _get_headers(self) -> dict:
        """Get HTTP headers. Override for custom auth."""
        headers = {'Content-Type': 'application/json'}
        if self.config.requires_auth and self.config.api_key:
            if self.framework_config.auth_scheme == 'header':
                headers['Authorization'] = f'Bearer {self.config.api_key}'
            elif self.framework_config.auth_scheme == 'x-api-key':
                headers['X-API-Key'] = self.config.api_key
        return headers
    
    def _get_endpoint(self, model: Optional[str] = None) -> str:
        """Get the full API endpoint URL. Substitutes {model} placeholder and appends auth key if query_param auth."""
        endpoint = self.framework_config.endpoint
        if model is None:
            model = self.config.model
        # Substitute {model} placeholder in the endpoint path
        endpoint = endpoint.replace('{model}', model)
        base = self.config.base_url.rstrip('/') + endpoint
        fc = self.framework_config
        if fc.requires_auth and self.config.api_key and fc.auth_scheme == 'query_param':
            import urllib.parse
            separator = '&' if '?' in base else '?'
            base += separator + urllib.parse.urlencode({fc.auth_param_name: self.config.api_key})
        return base
    
    def has_valid_key(self) -> bool:
        if not self.framework_config.requires_auth:
            return True
        return bool(self.config.api_key)
    
    def _check_rate_limit(self):
        """No-op for native providers (9999 RPM default)."""
        pass
    
    def chat(self, prompt: str, **kwargs) -> dict:
        """Send a request to the API using the framework config."""
        if not self.has_valid_key():
            return {'error': 'No valid API key', 'elapsed_ms': 0, 'provider': self.provider_name}
        
        import urllib.request
        
        start = time.time()
        try:
            # Resolve model before building request and endpoint
            model = kwargs.pop('model', self.config.model)
            # Build request using the framework template
            body = self._build_request(prompt, model=model, **kwargs)
            data = json.dumps(body).encode('utf-8')
            headers = self._get_headers()
            url = self._get_endpoint(model=model)
            
            req = urllib.request.Request(
                url, data=data, headers=headers,
                method=self.framework_config.method
            )
            
            timeout = kwargs.get('timeout', self.framework_config.timeout_seconds)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            
            elapsed_ms = int((time.time() - start) * 1000)
            
            # Parse response using provider-specific logic
            parsed = self._parse_response(result)
            parsed['elapsed_ms'] = elapsed_ms
            parsed['provider'] = self.provider_name
            parsed['native_api'] = True
            parsed['framework'] = 'NativeAPIProvider'
            
            # Update stats
            with self._lock:
                self._total_requests += 1
                usage = parsed.get('usage', {})
                self._total_tokens += usage.get('total_tokens', 0)
                self._last_error = None
            
            return parsed
            
        except Exception as e:  # noqa: BLE001 — provider call degrades to signed error
            note_failure("providers:call-degraded", e)
            elapsed_ms = int((time.time() - start) * 1000)
            self._last_error = str(e)
            return {'error': str(e), 'elapsed_ms': elapsed_ms, 'provider': self.provider_name}
    
    def _parse_stream_chunk(self, chunk: dict) -> str:
        """Extract text from a streaming SSE data chunk.
        
        Override this method for APIs with non-standard SSE formats.
        Returns empty string for chunks that don't contain text content.
        """
        return chunk.get('text', '')
    
    def chat_stream(self, prompt: str, **kwargs):
        """Stream a response from the API, yielding content chunks.
        
        Handles SSE (Server-Sent Events) streaming. Reads line by line
        from the HTTP response and parses 'data:' lines as JSON.
        Calls _parse_stream_chunk() to extract text from each chunk.
        """
        if not self.has_valid_key():
            yield 'ERROR: No valid API key for ' + self.provider_name
            return
        
        import urllib.request
        
        model = kwargs.pop('model', self.config.model)
        body = self._build_request(prompt, model=model, stream=True, **kwargs)
        data = json.dumps(body).encode('utf-8')
        headers = self._get_headers()
        url = self._get_endpoint(model=model)
        
        try:
            req = urllib.request.Request(
                url, data=data, headers=headers,
                method=self.framework_config.method
            )
            timeout = kwargs.get('timeout', self.framework_config.timeout_seconds)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for raw_line in resp:
                    line = raw_line.decode('utf-8').strip()
                    if line.startswith('data:'):
                        content = line[5:].strip()
                        if content == '[DONE]':
                            break
                        try:
                            chunk = json.loads(content)
                            text = self._parse_stream_chunk(chunk)
                            if text:
                                yield text
                        except json.JSONDecodeError as exc:
                            note_failure("providers:sse-chunk-skip", exc)
                            continue
            
            with self._lock:
                self._total_requests += 1
                
        except Exception as e:  # noqa: BLE001 — stream must degrade, never crash
            note_failure("providers:stream-degraded", e)
            self._last_error = str(e)
            yield '\n[ERROR] ' + str(e)
    
    def list_models(self):
        if self.provider_name in PROVIDER_REGISTRY:
            models = PROVIDER_REGISTRY[self.provider_name].get('free_models', {})
            return [ModelInfo(id=mid, description=desc) for mid, desc in models.items()]
        return []
    
    def get_stats(self):
        with self._lock:
            return {
                'provider': self.provider_name,
                'total_requests': self._total_requests,
                'total_tokens': self._total_tokens,
                'last_error': self._last_error,
                'model': self.config.model,
                'framework': 'NativeAPIProvider',
            }
    
    def __repr__(self):
        return f'NativeAPIProvider({self.provider_name}, model={self.config.model})'


# Example: Ollama via framework (internet API pattern, but localhost)
# This shows how to use NativeAPIProvider for any HTTP-based API

class OllamaNativeFrameworkProvider(NativeAPIProvider):
    """Ollama native API using the framework pattern.
    
    Demonstrates how to integrate a non-OpenAI-compatible endpoint
    using the NativeAPIProvider framework. This pattern works for
    ANY free internet API endpoint that uses its own protocol.
    """
    def _get_framework_config(self) -> NativeFrameworkConfig:
        return NativeFrameworkConfig(
            endpoint='/api/generate',
            method='POST',
            prompt_field='prompt',
            model_field='model',
            max_tokens_field='options.num_predict',
            temperature_field='options.temperature',
            requires_auth=False,
        )
    
    def _build_request(self, prompt: str, **kwargs) -> dict:
        model = kwargs.get('model', self.config.model)
        max_tokens = kwargs.get('max_tokens', self.config.max_tokens)
        temperature = kwargs.get('temperature', self.config.temperature)
        return {
            'model': model,
            'prompt': prompt,
            'stream': False,
            'options': {
                'temperature': temperature,
                'num_predict': max_tokens,
            },
        }
    
    def _parse_response(self, data: dict) -> dict:
        return {
            'content': data.get('response', ''),
            'model': data.get('model', self.config.model),
            'usage': {
                'prompt_tokens': data.get('prompt_eval_count', 0),
                'completion_tokens': data.get('eval_count', 0),
                'total_tokens': data.get('prompt_eval_count', 0) + data.get('eval_count', 0),
            },
            'finish_reason': data.get('done_reason', 'stop'),
        }


# Remove old individual providers
# The old OllamaNativeProvider, LlamaCppDirectProvider, and
# HuggingFaceDirectProvider are replaced by this framework pattern.
# To add a new non-OpenAI internet API:
#   1. Subclass NativeAPIProvider
#   2. Implement _build_request() and _parse_response()
#   3. Register in PROVIDER_REGISTRY

# --- Native Provider: Google Gemini (Native API) -------------------

class GeminiNativeProvider(NativeAPIProvider):
    """Google Gemini native API via generateContent endpoint.
    
    Uses Gemini's own RPC-style protocol (contents/parts) instead of
    the OpenAI-compatible /v1/chat/completions format.
    
    Endpoint: https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
    Auth: API key as query parameter (?key=...)
    
    Free tier: Generous, includes Gemini 2.0 Flash and Pro models.
    """
    def _get_framework_config(self) -> NativeFrameworkConfig:
        return NativeFrameworkConfig(
            endpoint='/v1beta/models/{model}:generateContent',
            method='POST',
            prompt_field='contents',
            model_field='model',
            requires_auth=True,
            auth_scheme='query_param',
            auth_param_name='key',
        )
    
    def _build_request(self, prompt: str, **kwargs) -> dict:
        model = kwargs.get('model', self.config.model)
        max_tokens = kwargs.get('max_tokens', self.config.max_tokens)
        temperature = kwargs.get('temperature', self.config.temperature)
        return {
            'contents': [{
                'role': 'user',
                'parts': [{'text': prompt}]
            }],
            'generationConfig': {
                'temperature': temperature,
                'maxOutputTokens': max_tokens,
            },
        }
    
    def _parse_response(self, data: dict) -> dict:
        candidates = data.get('candidates', [])
        if not candidates:
            return {'content': '', 'model': self.config.model, 'usage': {
                'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}
        parts = candidates[0].get('content', {}).get('parts', [])
        text = parts[0].get('text', '') if parts else ''
        usage = data.get('usageMetadata', {})
        finish = candidates[0].get('finishReason', 'STOP')
        return {
            'content': text,
            'model': data.get('modelVersion', self.config.model),
            'usage': {
                'prompt_tokens': usage.get('promptTokenCount', 0),
                'completion_tokens': usage.get('candidatesTokenCount', 0),
                'total_tokens': usage.get('totalTokenCount', 0),
            },
            'finish_reason': finish,
        }


# --- Native Provider: Hugging Face (Native Inference API) -----------

class HuggingFaceNativeProvider(NativeAPIProvider):
    """Hugging Face native inference API.
    
    Uses HuggingFace's task-specific /models/{model_id} endpoint
    instead of the OpenAI-compatible router.
    
    Endpoint: https://api-inference.huggingface.co/models/{model_id}
    Auth: Bearer token (hf_...)
    
    Free tier: Rate-limited, shared CPU/GPU runners.
    """
    def _get_framework_config(self) -> NativeFrameworkConfig:
        return NativeFrameworkConfig(
            endpoint='/models/{model}',
            method='POST',
            prompt_field='inputs',
            model_field='model',
            requires_auth=True,
            auth_scheme='header',
            error_path='error',
        )
    
    def _build_request(self, prompt: str, **kwargs) -> dict:
        max_tokens = kwargs.get('max_tokens', self.config.max_tokens)
        temperature = kwargs.get('temperature', self.config.temperature)
        return {
            'inputs': prompt,
            'parameters': {
                'max_new_tokens': max_tokens,
                'temperature': temperature,
                'return_full_text': False,
            },
            'options': {
                'wait_for_model': True,
            },
        }
    
    def _parse_response(self, data: dict) -> dict:
        # Response is a list: [{"generated_text": "..."}]
        if isinstance(data, list) and len(data) > 0:
            text = data[0].get('generated_text', '')
        elif isinstance(data, dict):
            text = data.get('generated_text', '')
        else:
            text = ''
        return {
            'content': text,
            'model': self.config.model,
            'usage': {
                'prompt_tokens': 0,
                'completion_tokens': 0,
                'total_tokens': 0,
            },
            'finish_reason': 'stop',
        }



# --- Native Provider: Mistral FIM (Fill-in-the-Middle) ---------------

class MistralFIMNativeProvider(NativeAPIProvider):
    """Mistral Fill-in-the-Middle via /v1/fim/completions.
    
    Uses Mistral's FIM endpoint for code completion between a prompt
    and suffix. NOT OpenAI-compatible. Uses codestral model.
    
    Endpoint: https://api.mistral.ai/v1/fim/completions
    Auth: Bearer token (MISTRAL_API_KEY)
    
    Free tier: Experiment plan, ~1B tokens/month.
    """
    def _get_framework_config(self) -> NativeFrameworkConfig:
        return NativeFrameworkConfig(
            endpoint='/v1/fim/completions',
            method='POST',
            prompt_field='prompt',
            model_field='model',
            requires_auth=True,
            auth_scheme='header',
        )
    
    def _build_request(self, prompt: str, **kwargs) -> dict:
        model = kwargs.get('model', self.config.model)
        max_tokens = kwargs.get('max_tokens', self.config.max_tokens)
        temperature = kwargs.get('temperature', self.config.temperature)
        suffix = kwargs.get('suffix', '')
        stream = kwargs.get('stream', False)
        body = {
            'model': model,
            'prompt': prompt,
            'max_tokens': max_tokens,
            'temperature': temperature,
        }
        if suffix:
            body['suffix'] = suffix
        if stream:
            body['stream'] = True
        return body

    def _parse_stream_chunk(self, chunk: dict) -> str:
        """Parse Mistral FIM SSE streaming chunk.

        Mistral sends choices[0].delta.content in streaming responses.
        """
        choices = chunk.get('choices', [])
        if choices:
            delta = choices[0].get('delta', {})
            return delta.get('content', '')
        return ""
    
    def _parse_response(self, data: dict) -> dict:
        choices = data.get('choices', [])
        if not choices:
            return {'content': '', 'model': self.config.model, 'usage': {
                'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}
        usage = data.get('usage', {})
        return {
            'content': choices[0].get('text', ''),
            'model': data.get('model', self.config.model),
            'usage': {
                'prompt_tokens': usage.get('prompt_tokens', 0),
                'completion_tokens': usage.get('completion_tokens', 0),
                'total_tokens': usage.get('total_tokens', 0),
            },
            'finish_reason': choices[0].get('finish_reason', 'stop'),
        }


# --- Native Provider: Cohere (v2/chat) -----------------------------

class CohereNativeProvider(NativeAPIProvider):
    """Cohere native v2/chat API.

    Uses Cohere's v2/chat endpoint with messages array format.
    NOT OpenAI-compatible. Response format differs from OpenAI
    (message.content is array of objects, not string).

    Endpoint: https://api.cohere.com/v2/chat
    Auth: Bearer token (COHERE_API_KEY)
    """
    def _get_framework_config(self) -> NativeFrameworkConfig:
        return NativeFrameworkConfig(
            endpoint='/v2/chat',
            method='POST',
            prompt_field='messages',
            model_field='model',
            requires_auth=True,
            auth_scheme='header',
        )

    def _build_request(self, prompt: str, **kwargs) -> dict:
        model = kwargs.get('model', self.config.model)
        max_tokens = kwargs.get('max_tokens', self.config.max_tokens)
        temperature = kwargs.get('temperature', self.config.temperature)
        stream = kwargs.get('stream', False)
        body = {
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens,
        }
        if temperature is not None:
            body['temperature'] = temperature
        if stream:
            body['stream'] = True
        return body

    def _parse_response(self, data: dict) -> dict:
        msg = data.get('message', {})
        content_blocks = msg.get('content', [])
        text = ''
        for block in content_blocks:
            if block.get('type') == 'text':
                text += block.get('text', '')
        usage = data.get('usage', {})
        token_usage = usage.get('tokens', {})
        return {
            'content': text,
            'model': data.get('model', self.config.model),
            'usage': {
                'prompt_tokens': token_usage.get('input_tokens', 0),
                'completion_tokens': token_usage.get('output_tokens', 0),
                'total_tokens': 0,
            },
            'finish_reason': data.get('finish_reason', 'complete'),
        }

    def _parse_stream_chunk(self, chunk: dict) -> str:
        """Parse Cohere SSE streaming chunk.

        Cohere sends content-delta events with delta.message.content.text.
        """
        if chunk.get('type') == 'content-delta':
            delta = chunk.get('delta', {})
            msg = delta.get('message', {})
            c = msg.get('content', {})
            return c.get('text', '')
        return ''


# --- Native Provider: LlamaCpp Direct (in-process, no HTTP) ----------

class LlamaCppDirectProvider:
    """In-process GGUF inference via llama-cpp-python (NOT OpenAI-compatible)."""
    VERSION = "1.0.0"
    def __init__(self, provider: str = "llama_cpp_direct"):
        self._lock = threading.Lock()
        self._total_requests = 0
        self._total_tokens = 0
        self._last_error = None
        self._model = None
        self._model_path = None
        self.provider_name = provider
        self.config = self._load_config()
    def _load_config(self):
        info: Dict[str, Any] = PROVIDER_REGISTRY.get(self.provider_name, {})
        return ProviderConfig(provider=self.provider_name, api_key="",
            base_url=info.get("base_url", ""),
            model=os.environ.get("LLAMA_CPP_MODEL", str(info.get("default_model", "llama3.2-1b"))),
            rate_limit_rpm=9999, key_prefix="",
            signup_url=info.get("signup_url", ""), requires_auth=False, max_tokens=512)
    def has_valid_key(self) -> bool:
        return True
    def _get_model_path(self, model_name: Optional[str] = None) -> str:
        name = model_name or self.config.model
        env_path = os.environ.get("LLAMA_CPP_MODEL_PATH", "")
        if env_path:
            return env_path
        KNOWN_BLOBS = {
            "llama3.2:1b": "C:/Users/clint/.ollama/models/blobs/sha256-74701a8c35f6c8d9a4b91f3f3497643001d63e0c7a84e085bed452548fa88d45",
            "llama3.2-1b": "C:/Users/clint/.ollama/models/blobs/sha256-74701a8c35f6c8d9a4b91f3f3497643001d63e0c7a84e085bed452548fa88d45",
            "gemma2:2b": "C:/Users/clint/.ollama/models/blobs/sha256-7462734796d67c40ecec2ca98eddf970e171dbb6b370e43fd633ee75b69abe1b",
            "gemma2-2b": "C:/Users/clint/.ollama/models/blobs/sha256-7462734796d67c40ecec2ca98eddf970e171dbb6b370e43fd633ee75b69abe1b",
        }
        if name in KNOWN_BLOBS:
            return KNOWN_BLOBS[name]
        p = Path(name)
        if p.exists():
            return str(p)
        raise FileNotFoundError("No GGUF model for '" + name + "'. Set LLAMA_CPP_MODEL_PATH env var.")
    def chat(self, prompt: str, **kwargs) -> dict:
        model_name = kwargs.get("model", self.config.model)
        max_tokens = kwargs.get("max_tokens", self.config.max_tokens)
        temperature = kwargs.get("temperature", self.config.temperature)
        start = time.time()
        try:
            if self._model is None or model_name != self._model_path:
                with self._lock:
                    # Double-check under lock against concurrent model loads (P0 fix Jun 2026)
                    if self._model is None or model_name != self._model_path:
                        mp = self._get_model_path(model_name)
                        from llama_cpp import Llama
                        self._model = Llama(model_path=mp, n_ctx=2048, verbose=False)
                        self._model_path = model_name
            result = self._model.create_completion(
                prompt=prompt, max_tokens=max_tokens,
                temperature=temperature, stop=None, echo=False)
            elapsed_ms = int((time.time() - start) * 1000)
            choice = result.get("choices", [dict()])[0]
            ud = result.get("usage", dict())
            usage = {"prompt_tokens": ud.get("prompt_tokens", 0),
                     "completion_tokens": ud.get("completion_tokens", 0),
                     "total_tokens": ud.get("total_tokens", 0)}
            with self._lock:
                self._total_requests += 1
                self._total_tokens += usage["total_tokens"]
                self._last_error = None
            return {"content": choice.get("text", ""), "model": model_name,
                    "usage": usage, "finish_reason": choice.get("finish_reason", "stop"),
                    "elapsed_ms": elapsed_ms, "provider": self.provider_name,
                    "native_api": True, "backend": "llama-cpp-python"}
        except Exception as e:  # noqa: BLE001 — provider call degrades to signed error
            note_failure("providers:call-degraded", e)
            elapsed_ms = int((time.time() - start) * 1000)
            self._last_error = str(e)
            return {"error": str(e), "elapsed_ms": elapsed_ms, "provider": self.provider_name}
    def _check_rate_limit(self):
        pass
    def list_models(self):
        if self.provider_name in PROVIDER_REGISTRY:
            return [ModelInfo(id=mid, description=desc) for mid, desc in
                    PROVIDER_REGISTRY[self.provider_name].get("free_models", dict()).items()]
        return []
    def get_stats(self):
        with self._lock:
            return {"provider": self.provider_name, "total_requests": self._total_requests,
                    "total_tokens": self._total_tokens, "last_error": self._last_error,
                    "model": self.config.model, "backend": "llama-cpp-python"}
    def __repr__(self):
        return "LlamaCppDirectProvider(model=" + str(self.config.model) + ", backend=llama-cpp-python)"


# --- Native Provider: HuggingFace Direct (in-process, no HTTP) ------

class HuggingFaceDirectProvider:
    """In-process model inference via transformers (NOT OpenAI-compatible)."""
    VERSION = "1.0.0"
    def __init__(self, provider: str = "huggingface_direct"):
        self._lock = threading.Lock()
        self._total_requests = 0
        self._total_tokens = 0
        self._last_error = None
        self._model: Any = None
        self._tokenizer: Any = None
        self._loaded_model_name = None
        self.provider_name = provider
        self.config = self._load_config()
    def _load_config(self):
        info: Dict[str, Any] = PROVIDER_REGISTRY.get(self.provider_name, {})
        return ProviderConfig(provider=self.provider_name, api_key="",
            base_url=info.get("base_url", ""),
            model=os.environ.get("HF_DIRECT_MODEL", str(info.get("default_model", "meta-llama/Llama-3.2-1B"))),
            rate_limit_rpm=9999, key_prefix="",
            signup_url=info.get("signup_url", ""), requires_auth=False, max_tokens=512)
    def has_valid_key(self) -> bool:
        return True
    def chat(self, prompt: str, **kwargs) -> dict:
        model_name = kwargs.get("model", self.config.model)
        max_tokens = kwargs.get("max_tokens", self.config.max_tokens)
        temperature = kwargs.get("temperature", self.config.temperature)
        start = time.time()
        try:
            if self._model is None or model_name != self._loaded_model_name:
                with self._lock:
                    # Double-check under lock against concurrent model loads (P0 fix Jun 2026)
                    if self._model is None or model_name != self._loaded_model_name:
                        from transformers import AutoModelForCausalLM, AutoTokenizer
                        self._model = AutoModelForCausalLM.from_pretrained(
                            model_name, device_map="auto", torch_dtype="auto")
                        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
                        self._loaded_model_name = model_name
            import torch
            inputs = self._tokenizer(prompt, return_tensors="pt")
            with torch.no_grad():
                outputs = self._model.generate(
                    **inputs, max_new_tokens=max_tokens,
                    temperature=temperature, do_sample=(temperature > 0),
                    pad_token_id=self._tokenizer.eos_token_id)
            response_text = self._tokenizer.decode(
                outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
            elapsed_ms = int((time.time() - start) * 1000)
            in_tok = inputs.input_ids.shape[1]
            out_tok = outputs.shape[1] - in_tok
            usage = {"prompt_tokens": in_tok, "completion_tokens": out_tok,
                     "total_tokens": in_tok + out_tok}
            with self._lock:
                self._total_requests += 1
                self._total_tokens += usage["total_tokens"]
                self._last_error = None
            return {"content": response_text, "model": model_name, "usage": usage,
                    "finish_reason": "stop", "elapsed_ms": elapsed_ms,
                    "provider": self.provider_name, "native_api": True, "backend": "transformers"}
        except Exception as e:  # noqa: BLE001 — provider call degrades to signed error
            note_failure("providers:call-degraded", e)
            elapsed_ms = int((time.time() - start) * 1000)
            self._last_error = str(e)
            return {"error": str(e), "elapsed_ms": elapsed_ms, "provider": self.provider_name}
    def _check_rate_limit(self):
        pass
    def list_models(self):
        if self.provider_name in PROVIDER_REGISTRY:
            return [ModelInfo(id=mid, description=desc) for mid, desc in
                    PROVIDER_REGISTRY[self.provider_name].get("free_models", dict()).items()]
        return []
    def get_stats(self):
        with self._lock:
            return {"provider": self.provider_name, "total_requests": self._total_requests,
                    "total_tokens": self._total_tokens, "last_error": self._last_error,
                    "model": self.config.model, "backend": "transformers"}
    def __repr__(self):
        return "HuggingFaceDirectProvider(model=" + str(self.config.model) + ", backend=transformers)"


# --- Native Provider: OpenVINO (Intel Local) -------------------------

class OpenVINOProvider:
    """Local inference provider using Intel OpenVINO runtime.

    Uses Intel hardware acceleration via OpenVINO for inference on
    CPU, Intel Arc GPU, and Intel AI Boost NPU. Zero API cost.
    Optimized for Intel Core Ultra (Meteor Lake) architecture.

    Supports:
    - openvino_genai.LLMPipeline for text generation (preferred)
    - HuggingFace models via optimum-intel auto-conversion
    - OpenVINO IR models (.xml + .bin)
    - Auto device selection: GPU > NPU > CPU

    Thread-safe for Python 3.14t free-threading.

    Device selection (set via OPENVINO_DEVICE env var):
        AUTO  - Let OpenVINO decide (GPU > NPU > CPU, default)
        GPU   - Intel Arc GPU via Level Zero
        NPU   - Intel AI Boost NPU
        CPU   - Intel Core CPU with AVX-512/VNNI

    Environment variables:
        OPENVINO_MODEL_PATH - Path to model directory or HuggingFace model ID
        OPENVINO_DEVICE     - Device to use (AUTO, GPU, NPU, CPU)
    """

    VERSION = "1.0.0"

    # Class-level backend probe cache (shared across instances, avoids repeated subprocess calls)
    _BACKEND_PROBE = {}

    @classmethod
    def _probe_backend(cls, module_name: str, import_stmt: Optional[str] = None):
        """Test if a module can be imported without crashing.

        Uses a subprocess to safely check because some C extensions
        (like openvino_genai, optimum.intel) segfault on Python 3.14t
        free-threading builds - and segfaults can't be caught in-process.

        Results are cached at the class level so we only probe once.

        Returns:
            True  - module imports successfully
            False - module not installed
            None  - module exists but crashes (e.g. segfault on 3.14t)
        """
        if module_name in cls._BACKEND_PROBE:
            return cls._BACKEND_PROBE[module_name]

        import sys

        # Quick check: is it even installed?
        try:
            import importlib.util
            if importlib.util.find_spec(module_name) is None:
                cls._BACKEND_PROBE[module_name] = False
                return False
        except Exception as exc:  # noqa: BLE001 — backend probe must never crash discovery
            note_failure("providers:probe-unavailable", exc)
            cls._BACKEND_PROBE[module_name] = False
            return False

        # Subprocess probe: try importing in a child process
        # If it segfaults, the child exits non-zero and we get None
        script = import_stmt or f"import {module_name}"
        try:
            # On 3.14t, set OPENVINO_DISABLE_FRONTENDS to avoid segfault
            # in TensorFlow/PyTorch C extension frontends that aren't
            # compatible with free-threading builds.
            env = None
            if module_name == "openvino" and cls._running_on_314t_without_gil():
                import os
                env = {**os.environ, "OPENVINO_DISABLE_FRONTENDS": "tensorflow,pytorch,mxnet,onnx"}

            r = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                env=env,
            )
            if r.returncode == 0:
                cls._BACKEND_PROBE[module_name] = True
                return True
            else:
                # Crashed (segfault, access violation, etc.)
                cls._BACKEND_PROBE[module_name] = None
                return None
        except subprocess.TimeoutExpired as exc:
            note_failure("providers:probe-timeout", exc)
            cls._BACKEND_PROBE[module_name] = None
            return None
        except Exception as exc:  # noqa: BLE001 — child-process probe crashed; cache as unavailable
            note_failure("providers:probe-crashed", exc)
            cls._BACKEND_PROBE[module_name] = None
            return None

    @staticmethod
    def _running_on_314t_without_gil():
        """Check if on Python 3.14t free-threading with GIL disabled."""
        try:
            import sysconfig
            return sysconfig.get_config_var("Py_GIL_DISABLED") == 1
        except Exception as exc:  # noqa: BLE001 — 314t detection is best-effort
            note_failure("providers:314t-detect", exc)
            return False

    def __init__(self, provider: str = "openvino"):
        self._lock = threading.Lock()
        self._total_requests = 0
        self._total_tokens = 0
        self._last_error = None
        self._llm_pipeline = None
        self._core = None
        self._compiled_model = None
        self._loaded_model_id = None
        self.provider_name = provider
        # On 3.14t free-threading, disable C frontends that cause segfaults
        if self._running_on_314t_without_gil():
            import os
            os.environ.setdefault(
                "OPENVINO_DISABLE_FRONTENDS",
                "tensorflow,pytorch,mxnet,onnx"
            )
        self.config = self._load_config()
        # Warm the probe cache during init (safe - subprocess, not in-process)
        self._probe_backend("openvino_genai")
        self._probe_backend("optimum.intel", "from optimum.intel import OVModelForCausalLM")

    def _load_config(self):
        info: Dict[str, Any] = PROVIDER_REGISTRY.get(self.provider_name, {})
        return ProviderConfig(
            provider=self.provider_name,
            api_key="",
            base_url="",
            model=os.environ.get("OPENVINO_MODEL", str(info.get("default_model", "auto"))),
            rate_limit_rpm=9999, key_prefix="",
            signup_url=info.get("signup_url", "https://docs.openvino.ai/"),
            requires_auth=False,
        )

    def has_valid_key(self) -> bool:
        return True

    # -- Device detection -----------------------------------------------

    def get_device(self) -> str:
        """Get the device to use for inference.

        Resolution order:
        1. OPENVINO_DEVICE env var
        2. AUTO (let OpenVINO decide)

        Returns: Device string (AUTO, GPU, NPU, CPU)
        """
        return os.environ.get("OPENVINO_DEVICE", "AUTO")

    def list_devices(self) -> List[Dict[str, str]]:
        """List available Intel hardware devices detected by OpenVINO.

        Safe on Python 3.14t: checks subprocess probe before importing.
        Returns empty list if OpenVINO is unsafe or not installed.
        """
        # Probe openvino core first
        ov_probe = self._probe_backend("openvino")
        if ov_probe is not True:
            return []
        try:
            import openvino as ov
            core = ov.Core()
            devices = core.available_devices
            result = []
            for d in devices:
                try:
                    name = core.get_property(d, "FULL_DEVICE_NAME")
                except Exception as exc:  # noqa: BLE001 — per-device name probe best-effort
                    note_failure("providers:device-name", exc)
                    name = d
                result.append({"id": d, "name": name})
            return result
        except ImportError as exc:
            note_failure("providers:device-list", exc)
            return []

    def get_device_info(self) -> Dict[str, Any]:
        """Get detailed info about available OpenVINO devices."""
        info: Dict[str, Any] = {
            "available": False,
            "devices": [],
            "openvino_safe": False,
            "openvino_version": "",
            "has_genai": False,
            "has_optimum_intel": False,
            "genai_available": False,
            "genai_safe": False,
            "selected_device": self.get_device(),
        }

        # Safe probe: openvino core
        ov_probe = self._probe_backend("openvino")
        info["openvino_safe"] = ov_probe is True
        if ov_probe is True:
            try:
                import openvino as ov
                info["openvino_version"] = ov.__version__
                core = ov.Core()
                devices = core.available_devices
                for d in devices:
                    dev_info = {"id": d}
                    for prop in ["FULL_DEVICE_NAME", "DEVICE_TYPE",
                                 "OPTIMAL_NUMBER_OF_INFER_REQUESTS"]:
                        try:
                            dev_info[prop.lower()] = core.get_property(d, prop)
                        except Exception as exc:  # noqa: BLE001 — per-device prop probe best-effort
                            note_failure("providers:device-prop", exc)
                            pass
                    info["devices"].append(dev_info)
                info["available"] = len(devices) > 0
            except ImportError as exc:
                note_failure("providers:device-info", exc)
                pass

        # Safe probe: openvino_genai
        genai_probe = self._probe_backend("openvino_genai")
        info["has_genai"] = genai_probe is True or genai_probe is None
        info["genai_safe"] = genai_probe is True
        info["genai_available"] = genai_probe is True

        # Safe probe: optimum.intel
        optimum_probe = self._probe_backend("optimum.intel", "from optimum.intel import OVModelForCausalLM")
        info["has_optimum_intel"] = optimum_probe is True or optimum_probe is None

        return info

    # -- Model management ----------------------------------------------

    def _get_model_path_or_id(self) -> str:
        """Get model path or HuggingFace model ID.

        Resolution order:
        1. OPENVINO_MODEL_PATH env var
        2. config.model
        3. Default: 'TinyLlama/TinyLlama-1.1B-Chat-v1.0'
        """
        env_path = os.environ.get("OPENVINO_MODEL_PATH", "")
        if env_path:
            return env_path
        if self.config.model and self.config.model != "auto":
            return self.config.model
        # Default small model for demo/testing
        return "TinyLlama/TinyLlama-1.1B-Chat-v1.0"

    # -- Inference -----------------------------------------------------

    def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Run inference using OpenVINO on Intel hardware.

        Uses openvino_genai.LLMPipeline for text generation when available.
        Falls back to optimum-intel + transformers for HuggingFace models.

        Returns dict with 'content', 'model', 'usage', 'elapsed_ms', 'device'.
        """
        start = time.time()
        device = self.get_device()
        model_id = model or self._get_model_path_or_id()
        max_tok = max_tokens or self.config.max_tokens
        temp = temperature if temperature is not None else self.config.temperature

        try:
            # Strategy 1: Use openvino_genai.LLMPipeline (preferred for LLMs)
            result = self._chat_genai(prompt, system_prompt, model_id, max_tok, temp, device)
            elapsed_ms = int((time.time() - start) * 1000)
            result["elapsed_ms"] = elapsed_ms
            result["provider"] = self.provider_name
            result["device"] = device
            result["backend"] = "openvino_genai"

            with self._lock:
                self._total_requests += 1
                usage = result.get("usage", {})
                self._total_tokens += usage.get("total_tokens", 0)
                self._last_error = None
            return result

        except ImportError as e:
            # openvino_genai not available — fall through
            note_failure("providers:genai-missing", e)
            pass
        except Exception as e:  # noqa: BLE001 — provider call degrades to signed error
            note_failure("providers:call-degraded", e)
            elapsed_ms = int((time.time() - start) * 1000)
            self._last_error = str(e)
            return {
                "error": str(e),
                "elapsed_ms": elapsed_ms,
                "provider": self.provider_name,
                "device": device,
                "backend": "openvino_genai",
            }

        try:
            # Strategy 2: Use optimum-intel for HuggingFace model conversion
            result = self._chat_optimum(prompt, system_prompt, model_id, max_tok, temp, device)
            elapsed_ms = int((time.time() - start) * 1000)
            result["elapsed_ms"] = elapsed_ms
            result["provider"] = self.provider_name
            result["device"] = device
            result["backend"] = "optimum-intel"

            with self._lock:
                self._total_requests += 1
                usage = result.get("usage", {})
                self._total_tokens += usage.get("total_tokens", 0)
                self._last_error = None
            return result

        except ImportError as e:
            note_failure("providers:genai-import-error", e)
            elapsed_ms = int((time.time() - start) * 1000)
            error_msg = (
                "OpenVINO inference requires either:\n"
                "  1. openvino_genai (for LLM text generation)\n"
                "  2. optimum-intel (for HuggingFace model conversion)\n"
                f"Install: pip install openvino-genai optimum-intel\n"
                f"Import error: {e}"
            )
            self._last_error = error_msg
            return {"error": error_msg, "elapsed_ms": elapsed_ms,
                    "provider": self.provider_name, "device": device}
        except Exception as e:  # noqa: BLE001 — openvino_genai call degrades to signed error
            note_failure("providers:call-degraded", e)
            elapsed_ms = int((time.time() - start) * 1000)
            self._last_error = str(e)
            return {"error": str(e), "elapsed_ms": elapsed_ms,
                    "provider": self.provider_name, "device": device,
                    "backend": "optimum-intel"}

    def _chat_genai(
        self, prompt: str, system_prompt: Optional[str],
        model_id: str, max_tokens: int, temperature: float, device: str
    ) -> Dict[str, Any]:
        """Inference via openvino_genai.LLMPipeline.

        LLMPipeline provides optimized text generation for LLMs.
        Automatically downloads HuggingFace model and converts to
        OpenVINO IR format on first use.
        """
        # Check probe before importing (avoids segfault on 3.14t)
        probe = self._probe_backend("openvino_genai")
        if probe is None:
            raise ImportError(
                "openvino_genai crashes on this Python version "
                "(segfault in C extension). Try with PYTHON_GIL=1."
            )
        if not probe:
            raise ImportError("openvino_genai not installed")

        import openvino_genai

        # Lazy-load pipeline with double-checked locking (P0 fix Jun 2026)
        if self._llm_pipeline is None or model_id != self._loaded_model_id:
            with self._lock:
                # Re-check: another thread may have loaded while we waited
                if self._llm_pipeline is None or model_id != self._loaded_model_id:
                    if self._llm_pipeline is not None:
                        del self._llm_pipeline
                        self._llm_pipeline = None
                    config: Dict[str, Any] = {"PERFORMANCE_HINT": "LATENCY"}
                    self._llm_pipeline = openvino_genai.LLMPipeline(
                        model_id, device=device, **config
                    )
                    self._loaded_model_id = model_id

        # Build full prompt with system message if provided
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        result = self._llm_pipeline.generate(
            full_prompt,
            max_new_tokens=max_tokens,
            temperature=temperature,
            do_sample=(temperature > 0),
        )

        # Extract generated text
        generated_text = str(result) if hasattr(result, "__str__") else ""

        tokens_info = {}
        try:
            if hasattr(result, "perf_metrics"):
                pm = result.perf_metrics
                if hasattr(pm, "get_throughput"):
                    tokens_info["throughput"] = pm.get_throughput()
        except Exception as exc:  # noqa: BLE001 — throughput metric best-effort
            note_failure("providers:throughput-metric", exc)
            pass

        return {
            "content": generated_text,
            "model": model_id,
            "usage": {
                "prompt_tokens": tokens_info.get("prompt_tokens", 0),
                "completion_tokens": tokens_info.get("completion_tokens", 0),
                "total_tokens": tokens_info.get("total_tokens", 0),
            },
            "finish_reason": "stop",
            "native_api": True,
        }

    def _chat_optimum(
        self, prompt: str, system_prompt: Optional[str],
        model_id: str, max_tokens: int, temperature: float, device: str
    ) -> Dict[str, Any]:
        """Inference via optimum-intel + transformers.

        Converts HuggingFace models to OpenVINO format using OVModelForCausalLM.
        Runs on the specified Intel device (CPU/GPU/NPU).
        """
        # Check probe before importing (avoids segfault on 3.14t)
        probe = self._probe_backend("optimum.intel", "from optimum.intel import OVModelForCausalLM")
        if probe is None:
            raise ImportError(
                "optimum-intel crashes on this Python version "
                "(segfault in C extension). Try with PYTHON_GIL=1."
            )
        if not probe:
            raise ImportError("optimum-intel not installed")

        from optimum.intel import OVModelForCausalLM
        from transformers import AutoTokenizer, pipeline as hf_pipeline

        # Map device string to HuggingFace device_map
        hf_device = "cpu" if device == "CPU" else device.lower()

        model_kwargs: Dict[str, Any] = {
            "export": True,  # Auto-convert to OpenVINO IR
            "device_map": hf_device,
        }

        self._model: Any = OVModelForCausalLM.from_pretrained(
            model_id, **model_kwargs
        )
        self._tokenizer: Any = AutoTokenizer.from_pretrained(model_id)

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Use HF pipeline for chat template
        pipe = hf_pipeline(
            "text-generation",
            model=self._model,
            tokenizer=self._tokenizer,
            device=hf_device,
            max_new_tokens=max_tokens,
            temperature=temperature,
            do_sample=(temperature > 0),
        )

        output = pipe(messages)
        generated_text = output[0]["generated_text"][-1]["content"] if output else ""

        # Token count estimation
        in_toks = len(self._tokenizer.encode(prompt))
        out_toks = len(self._tokenizer.encode(generated_text)) if generated_text else 0

        return {
            "content": generated_text,
            "model": model_id,
            "usage": {
                "prompt_tokens": in_toks,
                "completion_tokens": out_toks,
                "total_tokens": in_toks + out_toks,
            },
            "finish_reason": "stop",
            "native_api": True,
        }

    # -- Streaming -----------------------------------------------------

    def chat_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Iterator[str]:
        """Stream text generation via OpenVINO.

        Uses openvino_genai.LLMPipeline streaming when available.
        Falls back to optimum-intel streaming.
        """
        device = self.get_device()
        model_id = model or self._get_model_path_or_id()

        # Check probe before importing (avoids segfault on 3.14t)
        genai_probe = self._probe_backend("openvino_genai")
        if genai_probe is not True:
            if genai_probe is None:
                yield "[ERROR] openvino_genai crashes on this Python version (3.14t segfault). Use PYTHON_GIL=1"
            else:
                yield "[ERROR] openvino_genai not installed. pip install openvino-genai"
            return

        try:
            import openvino_genai

            # Double-checked locking (P0 fix Jun 2026) - match _chat_genai pattern
            if self._llm_pipeline is None or model_id != self._loaded_model_id:
                with self._lock:
                    if self._llm_pipeline is None or model_id != self._loaded_model_id:
                        config: Dict[str, Any] = {"PERFORMANCE_HINT": "LATENCY"}
                        self._llm_pipeline = openvino_genai.LLMPipeline(
                            model_id, device=device, **config
                        )
                        self._loaded_model_id = model_id

            full_prompt = prompt
            if system_prompt:
                full_prompt = f"{system_prompt}\n\n{prompt}"

            stream = self._llm_pipeline.generate(
                full_prompt,
                max_new_tokens=self.config.max_tokens,
                streamer=True,
            )

            for chunk in stream:
                if chunk:
                    yield str(chunk)

            with self._lock:
                self._total_requests += 1

        except Exception as e:  # noqa: BLE001 — stream must degrade, never crash
            note_failure("providers:stream-degraded", e)
            self._last_error = str(e)
            yield f"\n[ERROR] {e}"

    # -- Self-test -----------------------------------------------------

    def self_test(self, verbose: bool = True) -> Dict[str, Any]:
        """Run self-test — checks device detection and imports via safe subprocess probes.

        Does NOT load or run any model (no inference).
        ALL imports go through subprocess probes to avoid segfaults on Python 3.14t.
        """
        results: Dict[str, Any] = {
            "provider": self.provider_name,
            "openvino_import": False,
            "openvino_safe": False,
            "openvino_version": "",
            "devices_found": 0,
            "device_list": [],
            "has_genai": False,
            "genai_safe": False,
            "has_optimum_intel": False,
            "optimum_safe": False,
            "selected_device": self.get_device(),
            "issues": [],
        }

        # Safe probe: openvino core (via subprocess, avoids 3.14t segfault)
        ov_probe = self._probe_backend("openvino")
        results["openvino_safe"] = ov_probe is True
        if ov_probe is True:
            try:
                import openvino as ov
                results["openvino_import"] = True
                results["openvino_version"] = ov.__version__
                core = ov.Core()
                results["device_list"] = core.available_devices
                results["devices_found"] = len(results["device_list"])
            except ImportError as exc:
                note_failure("providers:openvino-missing", exc)
                results["issues"].append("openvino package not installed")
            except Exception as e:  # noqa: BLE001 — diagnose, don't fail the audit
                note_failure("providers:openvino-init", e)
                results["issues"].append(f"OpenVINO init error: {e}")
        elif ov_probe is None:
            results["issues"].append("openvino CRASHES on this Python version (3.14t segfault). Use PYTHON_GIL=1 or standard Python")
        else:
            results["issues"].append("openvino package not installed")

        # Safe probe: openvino_genai (crash-prone on 3.14t)
        genai_probe = self._probe_backend("openvino_genai")
        results["has_genai"] = genai_probe is True or genai_probe is None
        results["genai_safe"] = genai_probe is True
        if genai_probe is False:
            results["issues"].append("openvino_genai not installed — install with: pip install openvino-genai")
        elif genai_probe is None:
            results["issues"].append("openvino_genai CRASHES on this Python version (3.14t segfault). Use PYTHON_GIL=1 or standard Python")

        # Safe probe: optimum.intel (crash-prone on 3.14t)
        optimum_probe = self._probe_backend("optimum.intel", "from optimum.intel import OVModelForCausalLM")
        results["has_optimum_intel"] = optimum_probe is True or optimum_probe is None
        results["optimum_safe"] = optimum_probe is True
        if optimum_probe is False:
            results["issues"].append("optimum-intel not installed — install with: pip install optimum-intel")
        elif optimum_probe is None:
            results["issues"].append("optimum-intel CRASHES on this Python version (3.14t segfault). Use PYTHON_GIL=1 or standard Python")

        if verbose:
            self._print_test_results(results)

        return results

    def _print_test_results(self, results: Dict[str, Any]) -> None:
        """Print self-test results."""
        print(f"\n{'='*60}")
        print(f"  OpenVINO (Intel Local) — Self-Test")
        print(f"{'='*60}")

        checks = [
            ("openvino safe", results.get("openvino_safe", results["openvino_import"])),
            ("openvino import", results["openvino_import"]),
            ("Devices detected", results["devices_found"] > 0),
            ("openvino_genai", results["has_genai"]),
            ("  safe on this Python", results.get("genai_safe", results["has_genai"]) if results["has_genai"] else True),
            ("optimum-intel", results["has_optimum_intel"]),
            ("  safe on this Python", results.get("optimum_safe", results["has_optimum_intel"]) if results["has_optimum_intel"] else True),
        ]
        for check_name, passed in checks:
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {check_name}")

        if results["device_list"]:
            print(f"\n  Intel Hardware Detected:")
            try:
                import openvino as ov
                core = ov.Core()
                for d in results["device_list"]:
                    try:
                        name = core.get_property(d, "FULL_DEVICE_NAME")
                        print(f"    {d}: {name}")
                    except Exception:  # noqa: BLE001 — per-device name best-effort
                        print(f"    {d}")
            except Exception:  # noqa: BLE001 — device-name listing best-effort
                for d in results["device_list"]:
                    print(f"    {d}")

        print(f"\n  Selected device: {results['selected_device']}")
        print(f"  OpenVINO version: {results['openvino_version'] or 'N/A'}")

        if results["issues"]:
            print(f"\n  Issues:")
            for issue in results["issues"]:
                print(f"    - {issue}")
            print(f"\n  Install: pip install openvino-genai optimum-intel")

        if results["devices_found"] > 0:
            print(f"\n  Usage:")
            print(f"    from CORE.inference.providers import get_provider")
            print(f"    ov = get_provider('openvino')")
            print(f"    result = ov.chat('Hello!')")
            print(f"    Device info: {results['device_list']}")
            print(f"  Set OPENVINO_DEVICE=GPU for Intel Arc GPU offload")
            print(f"  Set OPENVINO_DEVICE=NPU for Intel AI Boost NPU")

        all_pass = results["openvino_import"] and results["devices_found"] > 0
        print(f"\n  {'ALL CHECKS PASSED' if all_pass else 'CHECKS FAILED'}")
        print(f"{'='*60}\n")

    # -- Stats ---------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "provider": self.provider_name,
                "total_requests": self._total_requests,
                "total_tokens": self._total_tokens,
                "last_error": self._last_error,
                "model": self.config.model,
                "device": self.get_device(),
                "backend": "OpenVINO",
                "devices_available": self.list_devices(),
            }

    def list_models(self) -> List[ModelInfo]:
        if self.provider_name in PROVIDER_REGISTRY:
            models = PROVIDER_REGISTRY[self.provider_name].get("free_models", {})
            return [ModelInfo(id=mid, description=desc) for mid, desc in models.items()]
        return [ModelInfo(id="auto", description="Auto-detect model from OPENVINO_MODEL_PATH")]

    def _check_rate_limit(self):
        pass  # Local inference, no rate limits

    def __repr__(self) -> str:
        device = self.get_device()
        genai_ok = self._probe_backend("openvino_genai") is True
        extra = ", genai=ok" if genai_ok else ""
        return f"OpenVINOProvider(device={device}{extra})"


# --- Native Provider: Ollama (/api/generate) -------------------------

class OllamaNativeProvider:
    """Native Ollama provider using /api/generate (NOT OpenAI-compatible).

    Uses Ollama's native HTTP API at /api/generate instead of the
    OpenAI-compatible /v1/chat/completions endpoint. Demonstrates how to
    build custom providers for non-OpenAI-compatible free inference backends.

    Completely free: runs locally on your hardware, no API keys,
    no rate limits, no internet required, no credit card.
    """

    VERSION = "1.0.0"

    def __init__(self, provider: str = "ollama_native"):
        self._lock = threading.Lock()
        self._total_requests = 0
        self._total_tokens = 0
        self._last_error = None
        self.provider_name = provider
        self.config = self._load_config()

    def _load_config(self):
        info: Dict[str, Any] = PROVIDER_REGISTRY.get(self.provider_name, {})
        return ProviderConfig(
            provider=self.provider_name,
            api_key="",
            base_url=info.get("base_url", "http://localhost:11434/api/generate"),
            model=os.environ.get("OLLAMA_NATIVE_MODEL", str(info.get("default_model", "llama3.2:1b"))),
            rate_limit_rpm=9999, key_prefix="",
            signup_url=info.get("signup_url", "https://ollama.com"),
            requires_auth=False,
        )

    def has_valid_key(self) -> bool:
        return True

    def chat(self, prompt: str, **kwargs) -> dict:
        import json, urllib.request
        model = kwargs.get("model", self.config.model)
        max_tokens = kwargs.get("max_tokens", self.config.max_tokens)
        temperature = kwargs.get("temperature", self.config.temperature)
        payload = {
            "model": model, "prompt": prompt, "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        start = time.time()
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.config.base_url, data=data,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            elapsed_ms = int((time.time() - start) * 1000)
            usage = {
                "prompt_tokens": result.get("prompt_eval_count", 0),
                "completion_tokens": result.get("eval_count", 0),
                "total_tokens": result.get("prompt_eval_count", 0) + result.get("eval_count", 0),
            }
            with self._lock:
                self._total_requests += 1
                self._total_tokens += usage["total_tokens"]
                self._last_error = None
            return {
                "content": result.get("response", ""), "model": result.get("model", model),
                "usage": usage, "finish_reason": result.get("done_reason", "stop"),
                "elapsed_ms": elapsed_ms, "provider": self.provider_name, "native_api": True,
            }
        except Exception as e:  # noqa: BLE001 — provider call degrades to signed error
            note_failure("providers:call-degraded", e)
            elapsed_ms = int((time.time() - start) * 1000)
            self._last_error = str(e)
            return {"error": str(e), "elapsed_ms": elapsed_ms, "provider": self.provider_name}

    def _check_rate_limit(self):
        pass

    def list_models(self):
        if self.provider_name in PROVIDER_REGISTRY:
            models = PROVIDER_REGISTRY[self.provider_name].get("free_models", {})
            return [ModelInfo(id=mid, description=desc) for mid, desc in models.items()]
        return []

    def get_stats(self):
        with self._lock:
            return {"provider": self.provider_name, "total_requests": self._total_requests,
                    "total_tokens": self._total_tokens, "last_error": self._last_error,
                    "model": self.config.model, "native_api": True}

    def __repr__(self):
        return f"OllamaNativeProvider(model={self.config.model}, native_api=True)"


# --- Provider Factory -----------------------------------------------

_provider_cache: Dict[str, Any] = {}
_cache_lock = threading.Lock()


# --- Circuit Breaker + Provider Health Monitor ----------------------

@dataclass
class ProviderHealth:
    """Health status for a single inference provider."""
    provider: str
    consecutive_failures: int = 0
    total_requests: int = 0
    total_failures: int = 0
    last_success: float = 0.0
    last_failure: float = 0.0
    last_error: str = ""
    circuit_open: bool = False
    circuit_opened_at: float = 0.0
    failure_rate: float = 0.0

    @property
    def is_healthy(self) -> bool:
        """Provider is healthy if circuit is closed and failure rate < 50%."""
        return not self.circuit_open and self.failure_rate < 0.5


class CircuitBreaker:
    """Circuit breaker for inference providers.

    Opens when consecutive failures exceed threshold, preventing cascading
    timeouts. Half-opens after cooldown to test recovery. Implements the
    standard circuit breaker pattern with 3 states: CLOSED, OPEN, HALF_OPEN.

    Thresholds:
    - failure_threshold: Consecutive failures before opening (default: 5)
    - cooldown_seconds: Time before attempting half-open (default: 30)
    - half_open_max: Max requests in half-open state (default: 1)
    """

    VERSION = "1.0.0"

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 30.0,
                 half_open_max: int = 1):
        self._lock = threading.Lock()
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._half_open_max = half_open_max
        self._health: Dict[str, ProviderHealth] = {}
        self._half_open_counters: Dict[str, int] = {}

    def _get_health(self, provider_name: str) -> ProviderHealth:
        """Get or create health record for a provider."""
        if provider_name not in self._health:
            self._health[provider_name] = ProviderHealth(provider=provider_name)
        return self._health[provider_name]

    def allow_request(self, provider_name: str) -> bool:
        """Check if a request to this provider should be allowed.

        Returns True if the circuit is CLOSED or HALF_OPEN (and within limit).
        In HALF_OPEN state, only `half_open_max` probe requests are allowed.
        """
        with self._lock:
            h = self._get_health(provider_name)

            if not h.circuit_open:
                return True  # CLOSED

            # Circuit is OPEN — check cooldown for half-open transition
            elapsed = time.time() - h.circuit_opened_at
            if elapsed >= self._cooldown_seconds:
                # HALF_OPEN: allow up to half_open_max probe requests
                count = self._half_open_counters.get(provider_name, 0)
                if count < self._half_open_max:
                    self._half_open_counters[provider_name] = count + 1
                    return True
                # Probe limit exceeded — re-open circuit
                return False

            return False  # Still OPEN (cooldown not elapsed)

    def record_success(self, provider_name: str) -> None:
        """Record a successful request — resets failure count."""
        with self._lock:
            h = self._get_health(provider_name)
            h.consecutive_failures = 0
            h.total_requests += 1
            h.last_success = time.time()
            h.circuit_open = False  # CLOSED
            h.failure_rate = (
                h.total_failures / max(h.total_requests, 1)
            )

    def record_failure(self, provider_name: str, error: str = "") -> None:
        """Record a failed request — may open circuit."""
        with self._lock:
            h = self._get_health(provider_name)
            h.consecutive_failures += 1
            h.total_requests += 1
            h.total_failures += 1
            h.last_failure = time.time()
            h.last_error = error[:200]
            h.failure_rate = (
                h.total_failures / max(h.total_requests, 1)
            )

            # Open circuit if threshold exceeded
            if h.consecutive_failures >= self._failure_threshold and not h.circuit_open:
                h.circuit_open = True
                h.circuit_opened_at = time.time()

    def get_health_report(self) -> Dict[str, Any]:
        """Get health report for all monitored providers."""
        with self._lock:
            report = {}
            for name, h in self._health.items():
                report[name] = {
                    "healthy": h.is_healthy,
                    "circuit": "OPEN" if h.circuit_open else "CLOSED",
                    "consecutive_failures": h.consecutive_failures,
                    "failure_rate": round(h.failure_rate, 3),
                    "total_requests": h.total_requests,
                    "last_error": h.last_error[:800] if h.last_error else None,
                }
            return {
                "version": self.VERSION,
                "providers": report,
                "healthy_count": sum(1 for h in self._health.values() if h.is_healthy),
                "total_providers": len(self._health),
            }

    def is_healthy(self, provider_name: str) -> bool:
        """Check if a provider is healthy WITHOUT mutating circuit state.

        Unlike allow_request(), this is read-only — it doesn't consume
        half-open probes or transition states. Safe for health checks
        and provider selection.
        """
        with self._lock:
            h = self._get_health(provider_name)
            return h.is_healthy

    def reset(self, provider_name: Optional[str] = None) -> None:
        """Reset circuit breaker state for one or all providers."""
        with self._lock:
            if provider_name:
                if provider_name in self._health:
                    self._health[provider_name] = ProviderHealth(provider=provider_name)
            else:
                self._health.clear()


# Global circuit breaker singleton
_global_breaker = CircuitBreaker()


def get_circuit_breaker() -> CircuitBreaker:
    """Get the global circuit breaker singleton."""
    return _global_breaker


def health_check_providers() -> Dict[str, Any]:
    """Run a health check on all configured providers (no API calls).

    Returns a report of which providers are healthy, have valid keys,
    and have open/closed circuits.
    """
    breaker = get_circuit_breaker()
    report = breaker.get_health_report()

    # Add key status for all registered providers
    for p in list_providers():
        name = p["id"]
        if name not in report["providers"]:
            report["providers"][name] = {
                "healthy": True,
                "circuit": "CLOSED",
                "key_configured": p["has_api_key"],
            }
        else:
            report["providers"][name]["key_configured"] = p["has_api_key"]

    return report


def get_provider(name):
    """Get or create a provider by name (e.g., 'groq', 'openrouter', 'gemini').

    Uses a singleton cache with full lock coverage for 3.14t thread safety.
    Subsequent calls return the same instance. The cache read is protected
    by _cache_lock to prevent torn reads under free-threading.
    """
    name = name.lower()
    if name not in PROVIDER_REGISTRY:
        available = list(PROVIDER_REGISTRY)
        raise ValueError(f"Unknown provider '{name}'. Available: {available}")

    # Fast path: read from cache under lock for 3.14t safety
    with _cache_lock:
        if name in _provider_cache:
            return _provider_cache[name]

    # Slow path: create provider outside lock to avoid blocking other readers
    if name == "ollama_native":
        provider = OllamaNativeProvider(provider=name)
    elif name == "gemini_native":
        provider = GeminiNativeProvider(provider=name)
    elif name == "huggingface_native":
        provider = HuggingFaceNativeProvider(provider=name)
    elif name == "mistral_fim_native":
        provider = MistralFIMNativeProvider(provider=name)
    elif name == "cohere_native":
        provider = CohereNativeProvider(provider=name)
    elif name == "llama_cpp_direct":
        provider = LlamaCppDirectProvider(provider=name)
    elif name == "huggingface_direct":
        provider = HuggingFaceDirectProvider(provider=name)
    elif name == "openvino":
        provider = OpenVINOProvider(provider=name)
    else:
        provider = OpenAIProvider(provider=name)
    with _cache_lock:
        # Double-check: another thread may have created it while we were
        if name not in _provider_cache:
            _provider_cache[name] = provider
        return _provider_cache[name]


def _read_config_key(provider_name: str) -> str:
    """Read API key from ~/.azazael/{provider}_config.json if it exists.

    Returns the key string, or empty string if no config file found.
    """
    config_files = [Path.home() / ".azazael" / f"{provider_name}_config.json"]
    if provider_name == "nvidia_nim":
        config_files.append(Path.home() / ".azazael" / "nim_config.json")
    for cf in config_files:
        if cf.exists():
            try:
                with open(cf, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                key = data.get("api_key", "")
                if key:
                    return key
            except (json.JSONDecodeError, OSError) as exc:
                note_failure("providers:config-read", exc)
                pass
    return ""


def list_providers() -> List[Dict[str, Any]]:
    """List all available providers with their status."""
    result = []
    _native_base = {'gemini_native': 'gemini', 'huggingface_native': 'huggingface',
                    'mistral_fim_native': 'mistral', 'cohere_native': 'cohere'}
    for key, info in PROVIDER_REGISTRY.items():
        env_var = info["env_var"]
        has_key = bool(os.environ.get(env_var, ""))
        # Also check config files
        if not has_key:
            has_key = bool(_read_config_key(key))
        if not has_key and key in _native_base:
            has_key = bool(_read_config_key(_native_base[key]))
        result.append({
            "id": key,
            "name": info["name"],
            "description": info["description"],
            "models": len(info.get("free_models", {})),
            "has_api_key": has_key,
            "env_var": env_var,
            "signup_url": info.get("signup_url", ""),
        })
    return result


# --- Local-provider liveness (failover ordering) ----------------------
#
# `has_valid_key()` CANNOT be trusted for anonymous providers. Their key is not
# required, so it returns True unconditionally -- which means the old
# get_best_provider() would happily hand back a dead endpoint whose position in
# the list happened to come first. Measured before this fix, on this machine:
#
#     get_best_provider() -> openvino | base_url: '' | has_valid_key: True
#
# i.e. it returned a provider with an EMPTY base URL whose runtime is not even
# installed. Three entries near the top of the list (openvino, llama_cpp_direct,
# huggingface_direct) are in-process providers with hardcoded
# `has_valid_key() -> True`, and two more (ollama, ollama_native) point at a
# daemon that may not be running.
#
# So for anonymous providers the key proves nothing and we verify the thing
# actually EXISTS instead:
#   * HTTP provider (non-empty base_url)   -> TCP connect to host:port
#   * in-process provider (empty base_url) -> is its runtime importable
#
# Providers that require a real key are left exactly as before: key presence
# stays the criterion, so selection gains NO new network dependency and the
# existing cloud ordering is untouched.
_LOCAL_PROVIDER_NAMES = (
    "llamaserver", "llama_mythos", "llama_mythos_v2", "llama_qwopus",
    "llama_ornith", "lmstudio", "accelerator_sidecar", "edge0",
    "openvino", "huggingface_direct", "llama_cpp_direct",
)

# Runtime module each in-process provider needs. A name absent from this map is
# assumed present, so a future in-process provider is never silently dropped.
_INPROCESS_RUNTIME = {
    "llama_cpp_direct": "llama_cpp",
    "huggingface_direct": "transformers",
    "openvino": "openvino",
}

# TCP probes are cheap on localhost but still cached, because get_best_provider()
# can be called in a loop by the inference worms.
_PROBE_TTL_SECONDS = 5.0
_probe_cache: Dict[str, tuple] = {}
_probe_lock = threading.Lock()


def _tcp_alive(host: str, port: int, timeout: float = 0.35) -> bool:
    """True if something is listening on host:port. Path-agnostic, so it works
    for both OpenAI-style (/v1) and native (/api/generate) base URLs."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError) as exc:
        note_failure("providers:tcp-probe", exc)
        return False


def _runtime_present(module_name: str) -> bool:
    """True if an in-process provider's runtime is importable."""
    import importlib.util
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError, AttributeError) as exc:
        note_failure("providers:runtime-probe", exc)
        return False


def provider_credential_configured(name: str) -> bool:
    """True if this provider has a usable credential, resolved EXACTLY the way
    live requests resolve it: env var first, then ~/.azazael/{name}_config.json.

    Why this exists: callers that check `os.environ` alone disagree with this
    module and declare a working provider unusable. Measured on this machine,
    the NVIDIA NIM key lives in ~/.azazael/nim_config.json and NOT in
    NVIDIA_NIM_API_KEY, so an env-only check reported `nim: False` and dropped a
    perfectly usable provider out of the failover chain
    (spine._provider_key_state).

    Anonymous providers return True: they have no credential to be missing.
    """
    info = PROVIDER_REGISTRY.get(name) or {}
    if not info.get("requires_auth", True):
        return True
    var = str(info.get("env_var") or "")
    if var and os.environ.get(var):
        return True
    return bool(_read_config_key(name))


def provider_is_live(provider) -> bool:
    """True if this provider can plausibly serve a request right now.

    Named providers that require a key are always considered live (unchanged
    behaviour). Anonymous providers are probed, because for them a 'valid key'
    is not evidence of anything.
    """
    cfg = provider.config
    if getattr(cfg, "requires_auth", True):
        return True

    name = getattr(provider, "provider_name", None) or getattr(cfg, "provider", "")
    base = (cfg.base_url or "").strip()
    cache_key = f"{name}|{base}"

    now = time.monotonic()
    with _probe_lock:
        hit = _probe_cache.get(cache_key)
        if hit is not None and now - hit[0] < _PROBE_TTL_SECONDS:
            return hit[1]

    if base:
        parsed = urlparse(base)
        host, port = parsed.hostname, parsed.port
        live = bool(host and port and _tcp_alive(host, port))
    else:
        module = _INPROCESS_RUNTIME.get(name)
        live = True if module is None else _runtime_present(module)

    with _probe_lock:
        _probe_cache[cache_key] = (now, live)
    return live


def get_best_provider(skip_unhealthy: bool = True) -> "OpenAIProvider":
    """Get the best available provider.

    Skips providers with open circuits when skip_unhealthy=True.

    Base priority order (unchanged): openvino > nvidia_nim > ollama >
    ollama_native > gemini_native > huggingface_native > mistral_fim_native >
    cohere_native > llama_cpp_direct > huggingface_direct > groq > cerebras >
    openrouter > gemini > github > mistral > huggingface > cloudflare >
    siliconflow > cohere > zhipu > ovhcloud

    Local llama-server models are inserted according to env var
    AZAZAEL_LOCAL_PRIORITY:
        "last" (default) -- only use local when nothing hosted is available
        "first"          -- prefer a LIVE local model over any hosted endpoint
        "off"            -- ignore local providers entirely

    "last" is the default because **every caller of this function is internal
    bulk work** -- the inference worms (`INFERENCE_WORM`, `chorus`), the GUI
    inference tab, and the analysis engines. The INTERACTIVE path does NOT come
    through here: Open Interpreter talks to llama-server directly via its own
    profile and `--api_base`, so local-first would buy that path nothing while
    taxing the bulk one. Measured: a 9B on CPU does 8.3 t/s (16.35 with MTP)
    against a hosted endpoint that is roughly 60x faster, and the mesh's own
    event log shows the scale this serves (`nature_cluster_discovery` 43,507,
    `category_cluster_executed` 22,255, `training_complete` 20,095).

    Set "first" when you deliberately want local: offline operation, privacy
    (prompts never leave the machine), or when hosted keys are gone. A local
    provider is only ever chosen when it is actually answering, so it can never
    displace a hosted endpoint by merely existing.
    """
    breaker = _global_breaker
    priority = ["openvino", "nvidia_nim", "ollama", "ollama_native", "gemini_native", "huggingface_native", "mistral_fim_native", "cohere_native", "llama_cpp_direct", "huggingface_direct", "groq", "cerebras", "openrouter", "gemini",
                "github", "mistral", "huggingface", "cloudflare", "siliconflow", "cohere", "zhipu", "ovhcloud"]

    mode = (os.environ.get("AZAZAEL_LOCAL_PRIORITY", "last") or "last").strip().lower()
    local = [n for n in _LOCAL_PROVIDER_NAMES if n not in priority]
    if mode == "off":
        ordered = priority
    elif mode == "last":
        ordered = [n for n in priority if n not in local] + local
    else:
        ordered = local + [n for n in priority if n not in local]

    for name in ordered:
        try:
            provider = get_provider(name)
            if not provider.has_valid_key():
                continue
            # Anonymous providers report a valid key unconditionally, so ask
            # the stronger question: does it actually exist right now?
            if not provider_is_live(provider):
                continue
            # Skip providers with open circuits (if health checking enabled)
            # Uses is_healthy() instead of allow_request() to avoid
            # consuming half-open probes during provider selection.
            if skip_unhealthy and not breaker.is_healthy(name):
                continue
            return provider
        except ValueError as exc:
            note_failure("providers:provider-select", exc)
            continue
    # Fallback: return groq even without a key (will show helpful error)
    return get_provider("groq")


# --- Self-test ------------------------------------------------------

if __name__ == "__main__":
    print("Multi-Provider Inference Registry")
    print(f"Providers: {len(PROVIDER_REGISTRY)} available\n")

    for prov in list_providers():
        key_status = "KEY FOUND" if prov["has_api_key"] else "no key"
        print(f"  {prov['id']:12s}  {prov['models']} models  [{key_status}]  {prov['signup_url']}")

    print(f"\n{'='*60}")
    print("  Testing each provider (self-test, no API calls):")
    print(f"{'='*60}")

    for name in PROVIDER_REGISTRY:
        try:
            provider = get_provider(name)
            provider.self_test(verbose=True)
        except Exception as e:  # noqa: BLE001 — self-test best-effort, continue
            print(f"  [{name}] ERROR: {e}")

    # Try best available
    print(f"\n{'='*60}")
    print("  Best available provider:")
    best = get_best_provider()
    print(f"  {best}")
    print(f"{'='*60}")
