# coding: utf-8
"""
INFERENCE_WORM.py — Provider-Agnostic Inference Worm for Azazael

Auto-selects the best available provider (Groq, NIM, Gemini, etc.) and
executes inference requests. All results published to MemoryBus.

Usage:
    from CORE.WORMS.INFERENCE_WORM import InferenceWorm
    worm = InferenceWorm()
    result = worm.execute(prompt="Analyze this log...")

    # Force a specific provider
    result = worm.execute(prompt="Hello", provider="groq")
"""

from typing import Any, List

from CORE.worm_registry import register_worm
from CORE.inference.providers import get_provider, get_best_provider, list_providers as _list_providers
from CORE.inference.bus_utils import InferenceBusPublisher

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__version__ = "1.0.0"


@register_worm(
    category="analysis",
    priority=7,
    cooldown_seconds=15.0,
    description="Provider-agnostic inference — auto-selects best available LLM API"
)
class InferenceWorm:
    """Multi-provider inference worm.

    Auto-selects the best available provider (Groq > NIM > Gemini > ...)
    and executes chat completions. All results published to MemoryBus
    on topic 'inference.result' for other worms to consume.

    Features:
    - Auto-provider selection via get_best_provider()
    - Manual provider override via 'provider' parameter
    - MemoryBus publish/subscribe integration
    - Rate limit awareness (inherited from providers)
    - All 11 providers supported
    """

    VERSION = "1.0.0"

    def __init__(self):
        self._exec_count: int = 0
        self._total_tokens: int = 0
        self._bus_pub = InferenceBusPublisher(source="inference_worm")

    def execute(
        self,
        prompt: str = "",
        system_prompt: (str | None) = None,
        provider: (str | None) = None,
        model: (str | None) = None,
        max_tokens: (int | None) = None,
        temperature: (float | None) = None,
    ) -> dict[str, Any]:
        """Execute an inference request using the best available provider.

        Args:
            prompt: The user prompt to send.
            system_prompt: Optional system instructions.
            provider: Override provider name (e.g., 'groq', 'nvidia_nim').
                      If None, auto-selects the best available.
            model: Override default model.
            max_tokens: Override default max tokens.
            temperature: Override default temperature.

        Returns:
            dict with 'success', 'content', 'model', 'provider', 'usage', 'elapsed_ms'.
        """
        if not prompt:
            return {"success": False, "error": "No prompt provided"}

        # Select provider
        if provider:
            client = get_provider(provider)
        else:
            client = get_best_provider()

        if not client.has_valid_key():
            return {
                "success": False,
                "error": (
                    f"No valid API key for {client.provider_name}. "
                    f"set {client.provider_name.upper()}_API_KEY env var. "
                    f"Get a key at: {client.config.signup_url}"
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
                "provider": result.get("provider", client.provider_name),
            })
            return {"success": False, "error": result["error"]}

        self._exec_count += 1
        self._total_tokens += result.get("usage", {}).get("total_tokens", 0)

        inference_result = {
            "success": True,
            "content": result["content"],
            "model": result["model"],
            "provider": result.get("provider", client.provider_name),
            "usage": result["usage"],
            "elapsed_ms": result["elapsed_ms"],
            "finish_reason": result.get("finish_reason", "unknown"),
        }

        self._bus_pub.publish({
            "event": "inference.complete",
            "provider": inference_result["provider"],
            "model": result["model"],
            "tokens": result["usage"]["total_tokens"],
            "elapsed_ms": result["elapsed_ms"],
            "content_preview": result["content"][:200] if result["content"] else "",
        })

        return inference_result

    def analyze(self, content: str, instruction: str = "Analyze and provide key insights.") -> dict[str, Any]:
        """Analyze content with a system prompt."""
        return self.execute(prompt=content, system_prompt=instruction)

    def summarize(self, content: str, max_words: int = 100) -> dict[str, Any]:
        """Summarize content to a word limit."""
        return self.execute(
            prompt=content,
            system_prompt=f"Summarize in no more than {max_words} words.",
            max_tokens=max_words * 2,
        )

    def list_providers(self) -> list[dict[str, Any]]:
        """list all available providers with status."""
        return _list_providers()

    def get_stats(self) -> dict[str, Any]:
        """Get inference worm statistics."""
        return {
            "version": InferenceWorm.VERSION,
            "exec_count": self._exec_count,
            "total_tokens": self._total_tokens,
        }

    def get_status(self) -> dict[str, Any]:
        """Side-effect free status probe (safe for cluster dispatch).

        Returned by ClusterWORMZ dispatcher instead of falling through to
        an argument-less execute() call, which would error on an empty prompt.
        """
        return {
            "success": True,
            "exec_count": self._exec_count,
            "total_tokens": self._total_tokens,
        }

    def __repr__(self) -> str:
        return f"InferenceWorm(v{InferenceWorm.VERSION}, execs={self._exec_count}, tokens={self._total_tokens})"


# --- Standalone test ------------------------------------------------

if __name__ == "__main__":
    print(f"InferenceWorm v{InferenceWorm.VERSION}")
    print("=" * 60)

    worm = InferenceWorm()

    # Show all providers
    print("Available providers:")
    for p in worm.list_providers():
        status = "LIVE" if p["has_api_key"] else "no key"
        print(f"  {p['id']:15s} [{status}]")

    # Run test with best provider
    print("\nTesting with best available provider...")
    result = worm.execute(
        prompt="Say hello from Azazael in exactly 5 words.",
        max_tokens=50,
    )
    if result["success"]:
        print(f"  Provider: {result['provider']}")
        print(f"  Model: {result['model']}")
        print(f"  Response: {result['content']}")
        print(f"  Tokens: {result['usage']['total_tokens']}")
        print(f"  Time: {result['elapsed_ms']}ms")
    else:
        print(f"  ERROR: {result['error']}")

    # Test Groq specifically
    print("\nTesting Groq specifically...")
    result2 = worm.execute(
        prompt="Say hello from Azazael in exactly 5 words.",
        provider="groq",
        max_tokens=50,
    )
    if result2["success"]:
        print(f"  Provider: {result2['provider']}")
        print(f"  Response: {result2['content']}")
        print(f"  Time: {result2['elapsed_ms']}ms")
    else:
        print(f"  ERROR: {result2['error']}")

    print("\nDone.")
