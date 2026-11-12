# coding: utf-8
"""
NIM_INFERENCE.py — NVIDIA NIM Inference Worm for Azazael

Spawns inference tasks using NVIDIA NIM, Groq, Gemini, or any provider.
Registered as a worm so ClusterWORMZ can dispatch inference workloads.

Results are published to MemoryBus on topic "inference.result".

Usage:
    from CORE.WORMS.NIM_INFERENCE import NimInferenceWorm
    worm = NimInferenceWorm()
    result = worm.execute(prompt="Analyze this log...")
    result = worm.execute(prompt="Hello", provider="groq")
"""

from typing import Any, List

from CORE.worm_registry import register_worm
from CORE.inference.nim_client import NimClient, get_client, NIM_FREE_MODELS
from CORE.inference.providers import get_provider, list_providers
from CORE.inference.bus_utils import InferenceBusPublisher

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__version__ = "1.2.0"


@register_worm(
    category="analysis",
    priority=6,
    cooldown_seconds=30.0,
    description="Multi-provider inference — NIM, Groq, Gemini via OpenAI-compatible API"
)
class NimInferenceWorm:
    """Worm that performs AI inference using NVIDIA NIM.

    Features:
    - Chat completions with configurable model
    - Streaming support
    - Rate limit awareness
    - Session recording integration
    - Multiple model support (Llama, Nemotron, Mistral, DeepSeek)

    Requires: NVIDIA_NIM_API_KEY env var or saved config.
    Get a free key at: https://build.nvidia.com
    """

    VERSION = "1.2.0"

    def __init__(self):
        self._client: (NimClient | None) = None
        self._exec_count: int = 0
        self._total_tokens: int = 0
        self._bus_pub = InferenceBusPublisher(source="nim_inference")

    def execute(
        self,
        prompt: str = "",
        system_prompt: (str | None) = None,
        provider: (str | None) = None,
        model: (str | None) = None,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
    ) -> dict[str, Any]:
        """Execute an inference request.

        Args:
            prompt: The user prompt to send to the model.
            system_prompt: Optional system instructions.
            provider: Provider name ('nvidia_nim', 'groq', 'gemini').
                      Default: 'nvidia_nim' for backward compatibility.
            model: Override default model.
            max_tokens: Override default max tokens.
            temperature: Override default temperature (0.0-2.0).

        Returns:
            dict with 'success', 'content', 'model', 'provider', 'usage', 'elapsed_ms'.
        """
        if not prompt:
            return {"success": False, "error": "No prompt provided"}

        # Use specified provider or default to NIM
        if provider and provider != "nvidia_nim":
            client = get_provider(provider)
            provider_name = provider
        else:
            client = self._get_client()
            provider_name = "nvidia_nim"

        if not client.has_valid_key():
            return {
                "success": False,
                "error": (
                    f"No API key configured for {provider_name}. "
                    f"Get a free key at: {client.config.signup_url}"
                ),
            }

        result = client.chat(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        if "error" in result:
            self._bus_pub.publish({
                "event": "inference.error",
                "error": result["error"],
                "provider": provider_name,
            })
            return {"success": False, "error": result["error"]}

        self._exec_count += 1
        self._total_tokens += result.get("usage", {}).get("total_tokens", 0)

        inference_result = {
            "success": True,
            "content": result["content"],
            "model": result["model"],
            "provider": provider_name,
            "usage": result["usage"],
            "elapsed_ms": result["elapsed_ms"],
            "finish_reason": result.get("finish_reason", "unknown"),
        }

        self._bus_pub.publish({
            "event": "inference.complete",
            "provider": provider_name,
            "model": result["model"],
            "tokens": result["usage"]["total_tokens"],
            "elapsed_ms": result["elapsed_ms"],
            "content_preview": result["content"][:200] if result["content"] else "",
        })

        return inference_result

    def analyze(
        self,
        content: str,
        instruction: str = "Analyze the following content and provide key insights.",
    ) -> dict[str, Any]:
        """Analyze content with a system prompt.

        Convenience method for common analysis workflows.
        """
        return self.execute(prompt=content, system_prompt=instruction)

    def summarize(self, content: str, max_words: int = 100) -> dict[str, Any]:
        """Summarize content to a specified word limit."""
        return self.execute(
            prompt=content,
            system_prompt=f"Summarize the following in no more than {max_words} words.",
            max_tokens=max_words * 2,
        )

    def list_available_models(self) -> list[dict[str, str]]:
        """list models available on the free tier."""
        client = self._get_client()
        return [
            {"id": model.id, "description": model.description}
            for model in client.list_models()
        ]

    def get_stats(self) -> dict[str, Any]:
        """Get inference worm statistics."""
        client_stats = self._get_client().get_stats() if self._client else {}
        return {
            "exec_count": self._exec_count,
            "total_tokens": self._total_tokens,
            "client": client_stats,
            "model": self._client.config.model if self._client else "not initialized",
        }

    def get_status(self) -> dict[str, Any]:
        """Side-effect free status probe (safe for cluster dispatch).

        Returned by ClusterWORMZ dispatcher instead of falling through to
        an argument-less execute() call, which would error on an empty prompt.
        Avoids lazy client creation so probing never triggers network.
        """
        return {
            "success": True,
            "exec_count": self._exec_count,
            "total_tokens": self._total_tokens,
        }

    def list_all_providers(self) -> list[dict[str, Any]]:
        """list all available inference providers (not just NIM)."""
        return list_providers()

    def _get_client(self) -> NimClient:
        """Get or lazily create the NimClient."""
        if self._client is None:
            self._client = get_client()
        return self._client

    def __repr__(self) -> str:
        return f"NimInferenceWorm(v{NimInferenceWorm.VERSION}, execs={self._exec_count}, tokens={self._total_tokens})"


# --- Standalone test ------------------------------------------------

if __name__ == "__main__":
    print(f"NimInferenceWorm v{NimInferenceWorm.VERSION}")
    print("=" * 60)

    worm = NimInferenceWorm()

    # Show config status
    client = worm._get_client()
    print(f"Client: {client}")
    print(f"Has API key: {client.has_valid_key()}")

    # List models
    print(f"\nAvailable models ({len(NIM_FREE_MODELS)}):")
    for mid, desc in NIM_FREE_MODELS.items():
        print(f"  {mid}")
        print(f"    {desc}")

    # Stats
    print(f"\nWorm stats: {worm.get_stats()}")

    # If key is available, run a test inference
    if client.has_valid_key():
        print("\nRunning test inference...")
        result = worm.execute(
            prompt="Say 'Hello from Azazael!' in exactly 5 words.",
            max_tokens=50,
        )
        if result["success"]:
            print(f"  Response: {result['content']}")
            print(f"  Model: {result['model']}")
            print(f"  Tokens: {result['usage']['total_tokens']}")
            print(f"  Time: {result['elapsed_ms']}ms")
        else:
            print(f"  ERROR: {result['error']}")
    else:
        print("\nNo API key configured. Skipping live test.")
        print("To test: set NVIDIA_NIM_API_KEY=nvapi-... and re-run.")
        print("Get a free key at: https://build.nvidia.com")

    print("\nDone.")
