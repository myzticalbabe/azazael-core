# coding: utf-8
"""
nim_client.py — NVIDIA NIM Inference Client for Azazael

Extends OpenAIProvider with NVIDIA NIM-specific configuration and models.
Uses the OpenAI-compatible NVIDIA NIM API for chat completions.

Free tier: 1,000-5,000 API credits, no credit card required.
Sign up at: https://build.nvidia.com

Usage:
    from CORE.inference.nim_client import NimClient, NimConfig

    # From env var
    client = NimClient()  # reads NVIDIA_NIM_API_KEY

    # Explicit config (use env var in production)
    config = NimConfig(api_key=os.environ.get("NVIDIA_NIM_API_KEY", "nvapi-YOUR-KEY-HERE"), model="meta/llama-3.3-70b")
    client = NimClient(config)

    # Chat
    response = client.chat("Hello, world!")
    print(response)

    # Self-test
    python -m CORE.inference.nim_client
"""

import os
import json
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, List, TYPE_CHECKING

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# 3.14t port: openai SDK needs pydantic_core (no free-threaded build).
if TYPE_CHECKING:                 # annotation-only: never evaluated at run time
    from openai import OpenAI as _OpenAIClientType

try:
    from openai import OpenAI
except ImportError as exc:
    note_failure("nim_client:L39", exc)
    OpenAI = None

from CORE.inference.providers import OpenAIProvider


# --- Constants ------------------------------------------------------

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NIM_DEFAULT_MODEL = "meta/llama-3.3-70b-instruct"
NIM_CONFIG_DIR = Path.home() / ".azazael"
NIM_CONFIG_FILE = NIM_CONFIG_DIR / "nim_config.json"

# Available free-tier models (updated June 2026)
NIM_FREE_MODELS = {
    "meta/llama-3.3-70b-instruct": "Llama 3.3 70B Instruct (fast, general-purpose)",
    "meta/llama-3.1-8b-instruct": "Llama 3.1 8B Instruct (fastest, lightweight)",
    "nvidia/nemotron-4-340b-instruct": "Nemotron 4 340B (NVIDIA-optimized)",
    "mistralai/mistral-large-2-instruct": "Mistral Large 2 (strong reasoning)",
    "deepseek-ai/deepseek-r1": "DeepSeek R1 (chain-of-thought reasoning)",
}

# --- Configuration --------------------------------------------------

@dataclass
class NimConfig:
    """Configuration for the NVIDIA NIM client. Compatible with ProviderConfig."""
    api_key: str = ""
    base_url: str = NIM_BASE_URL
    model: str = NIM_DEFAULT_MODEL
    max_tokens: int = 1024
    temperature: float = 0.7
    top_p: float = 1.0
    timeout_seconds: float = 60.0
    max_retries: int = 3
    rate_limit_rpm: int = 40
    signup_url: str = "https://build.nvidia.com"
    extra_headers: dict[str, str] = field(default_factory=dict)
    key_prefix: str = "nvapi-"

    def validate(self) -> list[str]:
        """Validate configuration. Returns list of issues (empty = valid)."""
        issues = []
        if not self.api_key or not self.api_key.startswith("nvapi-"):
            issues.append("API key must start with 'nvapi-' (set NVIDIA_NIM_API_KEY)")
        if self.temperature < 0 or self.temperature > 2:
            issues.append("temperature must be 0.0-2.0")
        if self.max_tokens < 1 or self.max_tokens > 131072:
            issues.append("max_tokens must be 1-131072")
        return issues


@dataclass
class NimModel:
    """Metadata for an available NIM model."""
    id: str
    description: str = ""
    max_tokens: int = 131072
    free_tier: bool = True


# --- Client (extends OpenAIProvider) --------------------------------

class NimClient(OpenAIProvider):
    """NVIDIA NIM inference client. Extends OpenAIProvider with NIM specifics.

    Inherits chat(), chat_stream(), _check_rate_limit(), _get_client(),
    and get_stats() from OpenAIProvider. Only overrides NIM-specific
    config loading, key validation, model listing, and self-test display.

    Thread-safe. All free-tier features included.
    """

    VERSION = "1.1.0"

    def __init__(self, config: (NimConfig | None) = None):
        # Set up state manually (don't call super().__init__ which loads from registry)
        self._lock = threading.Lock()
        self._request_times: list[float] = []
        self._total_requests: int = 0
        self._total_tokens: int = 0
        self._last_error: (str | None) = None
        self._openai_client: "_OpenAIClientType | None" = None
        self.provider_name = "nvidia_nim"

        if config is not None:
            self.config = config
        else:
            self.config = self._load_config()

    # -- Configuration (NIM-specific) -------------------------------

    def _load_config(self) -> NimConfig:
        """Load NIM configuration from env vars, config file, or defaults."""
        api_key = os.environ.get("NVIDIA_NIM_API_KEY", "")

        if not api_key and NIM_CONFIG_FILE.exists():
            try:
                with open(NIM_CONFIG_FILE, "r", encoding="utf-8") as fh:
                    file_config = json.load(fh)
                api_key = file_config.get("api_key", "")
            except (json.JSONDecodeError, OSError) as exc:
                note_failure("nim_client:L139", exc)

        return NimConfig(
            api_key=api_key,
            base_url=os.environ.get("NVIDIA_NIM_BASE_URL", NIM_BASE_URL),
            model=os.environ.get("NVIDIA_NIM_MODEL", NIM_DEFAULT_MODEL),
        )

    def save_config(self) -> bool:
        """Save current config to ~/.azazael/nim_config.json."""
        try:
            NIM_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(NIM_CONFIG_FILE, "w", encoding="utf-8") as fh:
                json.dump({
                    "api_key": self.config.api_key,
                    "base_url": self.config.base_url,
                    "model": self.config.model,
                }, fh, indent=2)
            return True
        except OSError as exc:
            note_failure("nim_client:L159", exc)
            return False

    # -- Key validation (NIM-specific: nvapi- prefix) ---------------

    def set_api_key(self, key: str) -> None:
        """set the API key at runtime. Saves to config file."""
        self.config.api_key = key
        self.save_config()

    def has_valid_key(self) -> bool:
        """Check for valid NIM API key (nvapi- prefix)."""
        key = self.config.api_key
        return bool(key and key.startswith("nvapi-"))

    # -- Model listing (NIM-specific models) ------------------------

    def list_models(self) -> list[NimModel]:
        """list NVIDIA NIM free-tier models."""
        return [
            NimModel(id=model_id, description=desc)
            for model_id, desc in NIM_FREE_MODELS.items()
        ]

    def list_remote_models(self) -> list[NimModel]:
        """Fetch available models from the NIM API."""
        if not self.has_valid_key():
            return self.list_models()

        try:
            client = self._get_client()
            response = client.models.list()
            return [
                NimModel(
                    id=m.id,
                    description=getattr(m, "description", "") or "",
                    max_tokens=getattr(m, "max_tokens", 131072),
                )
                for m in response.data
            ]
        except Exception as e:
            err_str = str(e).lower()
            if "auth" in err_str or "401" in err_str or "403" in err_str:
                return self.list_models()
            raise

    # -- Self-test (NIM-specific display) ---------------------------

    def self_test(self, verbose: bool = True) -> dict[str, Any]:
        """Run self-test with NIM-specific display."""
        results = {
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
        results["models_available"] = len(self.list_models())

        try:
            from openai import OpenAI  # noqa: F401
            results["import_ok"] = True
        except ImportError as exc:
            note_failure("nim_client:L226", exc)
            results["issues"].append("openai package not installed")
            results["import_ok"] = False

        if verbose:
            self._print_test_results(results)

        return results

    def _print_test_results(self, results: dict[str, Any]) -> None:
        """Print NIM-specific self-test results."""
        print(f"\n{'='*60}")
        print(f"  NVIDIA NIM Client v{NimClient.VERSION} — Self-Test")
        print(f"{'='*60}")

        checks = [
            ("openai import", results["import_ok"]),
            ("Config valid", results["config_valid"]),
            ("API key format", results["key_format_ok"]),
            (f"Models ({results['models_available']})", results["models_available"] > 0),
        ]
        for name, passed in checks:
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {name}")

        if results["issues"]:
            print("\n  Issues:")
            for issue in results["issues"]:
                print(f"    - {issue}")

        if not results["key_format_ok"]:
            print("\n  To get a free API key:")
            print("    1. Visit: https://build.nvidia.com")
            print("    2. Join NVIDIA Developer Program (free)")
            print("    3. Generate an API key")
            print("    4. set: export NVIDIA_NIM_API_KEY=nvapi-...")
            print("    OR:   client.set_api_key('nvapi-...')")

        all_pass = all(p for _, p in checks)
        print(f"\n  {'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED (expected without API key)'}")
        print(f"{'='*60}\n")

    # -- Stats (NIM-specific: adds "provider" key) -----------------

    def get_stats(self) -> dict[str, Any]:
        """Get NIM client usage statistics."""
        stats = super().get_stats()
        stats["provider"] = "nvidia_nim"
        return stats

    def __repr__(self) -> str:
        key_status = "configured" if self.has_valid_key() else "missing"
        return f"NimClient(v{NimClient.VERSION}, model={self.config.model}, key={key_status})"


# --- Module-level convenience --------------------------------------

_default_client: (NimClient | None) = None
_client_lock = threading.Lock()


def get_client() -> NimClient:
    """Get or create the default NimClient singleton."""
    global _default_client
    if _default_client is None:
        with _client_lock:
            if _default_client is None:
                _default_client = NimClient()
    return _default_client


def chat(prompt: str, system_prompt: (str | None) = None) -> dict[str, Any]:
    """Quick one-shot chat using the default client."""
    return get_client().chat(prompt, system_prompt=system_prompt)


# --- CLI / Self-test entry point ------------------------------------

if __name__ == "__main__":
    print(f"NVIDIA NIM Client v{NimClient.VERSION}")
    print(f"Config directory: {NIM_CONFIG_DIR}")
    print(f"Config file: {NIM_CONFIG_FILE}")

    client = NimClient()
    client.self_test(verbose=True)

    # Show available models
    print("Available models (free tier):")
    for model in client.list_models():
        print(f"  {model.id}")
        print(f"    {model.description}")

    # Check if we can make a real call
    if client.has_valid_key():
        print("\nAPI key found. Testing chat completion...")
        result = client.chat("Say 'Hello from Azazael!' in exactly 5 words.")
        if "error" in result:
            print(f"  ERROR: {result['error']}")
        else:
            print(f"  Response: {result['content']}")
            print(f"  Model: {result['model']}")
            print(f"  Tokens: {result['usage']['total_tokens']}")
            print(f"  Time: {result['elapsed_ms']}ms")
    else:
        print("\nNo API key configured. set NVIDIA_NIM_API_KEY to test live inference.")
        print("Get a free key at: https://build.nvidia.com")
