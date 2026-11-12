# coding: utf-8
"""


__all__ = [
    "EXTRA_ENV_VARS",
    "PROVIDER_DEFS",
    "SecretsManager",
    "VERSION",
    "audit_secrets",
    "get_secret",
]

secrets_manager.py -- Unified Secrets Manager for Azazael
=========================================================
Single source of truth for ALL API keys across the project.
Consolidates .env, config JSONs, and environment variables.

Load order:
  1. Environment variables (highest priority)
  2. ~/.env file
  3. ~/.azazael/{provider}_config.json (fallback)

Usage:
    from CORE.secrets_manager import SecretsManager
    sm = SecretsManager()
    key = sm.get("MISTRAL_API_KEY")
    sm.audit()  # List all providers and their status
"""

import json
import os
import threading
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Dict, List, Optional, Tuple  # removed — all names have native equivalents

# Provider definitions: {provider_id: {env_var, config_file, signup_url, description}}
PROVIDER_DEFS = {
    "groq": {
        "env_var": "GROQ_API_KEY",
        "config_file": "groq_config.json",
        "signup_url": "https://console.groq.com",
        "description": "Groq Cloud -- LPU-powered, 30 RPM free",
    },
    "openrouter": {
        "env_var": "OPENROUTER_API_KEY",
        "config_file": "openrouter_config.json",
        "signup_url": "https://openrouter.ai/keys",
        "description": "OpenRouter -- free model hub, 20 RPM",
    },
    "gemini": {
        "env_var": "GEMINI_API_KEY",
        "config_file": "gemini_config.json",
        "signup_url": "https://aistudio.google.com/apikey",
        "description": "Google Gemini -- 15 RPM, 1M TPM free",
    },
    "cloudflare": {
        "env_var": "CLOUDFLARE_API_TOKEN",
        "config_file": "cloudflare_config.json",
        "signup_url": "https://dash.cloudflare.com/ai",
        "description": "Cloudflare Workers AI -- free neurons",
    },
    "cohere": {
        "env_var": "COHERE_API_KEY",
        "config_file": "cohere_config.json",
        "signup_url": "https://dashboard.cohere.com/api-keys",
        "description": "Cohere -- trial API, 1K calls/month free, Command R+",
    },
    "cerebras": {
        "env_var": "CEREBRAS_API_KEY",
        "config_file": "cerebras_config.json",
        "signup_url": "https://cloud.cerebras.ai",
        "description": "Cerebras Cloud -- 1M tokens/day free, 3000 tok/s",
    },
    "nvidia_nim": {
        "env_var": "NVIDIA_NIM_API_KEY",
        "config_file": "nim_config.json",
        "signup_url": "https://build.nvidia.com",
        "description": "NVIDIA NIM -- 40 RPM permanent free",
    },
    "mistral": {
        "env_var": "MISTRAL_API_KEY",
        "config_file": "mistral_config.json",
        "signup_url": "https://console.mistral.ai/api-keys",
        "description": "Mistral AI -- 1B tokens/month free",
    },
    "github": {
        "env_var": "GITHUB_TOKEN",
        "config_file": "github_config.json",
        "signup_url": "https://github.com/settings/tokens",
        "description": "GitHub Models -- free for users, GPT-4o included",
    },
    "huggingface": {
        "env_var": "HF_TOKEN",
        "config_file": "huggingface_config.json",
        "signup_url": "https://huggingface.co/settings/tokens",
        "description": "Hugging Face Inference -- free, no credit card, serverless",
    },
    "ovhcloud": {
        "env_var": "OVHCLOUD_API_KEY",
        "config_file": "ovhcloud_config.json",
        "signup_url": "https://www.ovhcloud.com/en/public-cloud/ai/endpoints/",
        "description": "OVHcloud AI Endpoints -- 2 RPM, anonymous, no key needed",
    },
    "ollama": {
        "env_var": "OLLAMA_API_KEY",
        "config_file": "ollama_config.json",
        "signup_url": "https://ollama.com",
        "description": "Ollama (Local) -- zero cost, no key, unlimited local inference",
    },
    "siliconflow": {
        "env_var": "SILICONFLOW_API_KEY",
        "config_file": "siliconflow_config.json",
        "signup_url": "https://siliconflow.cn",
        "description": "SiliconFlow -- 200+ models, generous free tier",
    },
    "together": {
        "env_var": "TOGETHER_API_KEY",
        "config_file": "together_config.json",
        "signup_url": "https://api.together.xyz/settings/api-keys",
        "description": "Together AI -- requires payment, large model catalog",
    },
    "zhipu": {
        "env_var": "ZHIPU_API_KEY",
        "config_file": "zhipu_config.json",
        "signup_url": "https://z.ai/manage-apikey/apikey-list",
        "description": "Zhipu AI (Z.ai) -- GLM series, free tier, bilingual",
    },
}

# Additional config env vars not tied to a provider
EXTRA_ENV_VARS = {
    "CLOUDFLARE_ACCOUNT_ID": "Cloudflare Workers AI account ID (required for base URL)",
}

VERSION = "1.0.0"


class SecretsManager:
    """Unified secrets manager for all Azazael API keys.

    Singleton pattern -- use SecretsManager() to get the global instance.
    Thread-safe via double-checked locking for Python 3.14t free-threading.
    """

    _instance: "SecretsManager | None" = None
    _instance_lock = threading.Lock()
    _initialized = False

    def __new__(cls, *args, **kwargs):
        """Return the global instance, as this class's own docstring promises.

        2026-09-21: `_instance` and `_instance_lock` were DECLARED and the
        docstring said "Thread-safe via double-checked locking for Python 3.14t
        free-threading", but no `__new__` existed, so nothing ever read either
        field and every `SecretsManager()` built a new object. Measured by
        `tests/test_secrets_manager_thread_safety.py`, which until today was not
        collected by pytest at all: `sm1 is sm2` was False, and 20 threads
        produced **20 distinct instances**. The declared lock was inert.

        This matters beyond identity: `_refresh()` reads the process environment
        into a per-instance cache, so N instances meant N divergent views of the
        same secrets.
        """
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # `__new__` returns a cached instance, but Python still calls `__init__`
        # after it, so without this guard every `SecretsManager()` would re-run
        # `_refresh()` and wipe the shared cache. Guarded under the same lock for
        # the same reason the instance cache is: two threads constructing at once.
        if type(self)._initialized:
            return
        with type(self)._instance_lock:
            if type(self)._initialized:
                return
            type(self)._initialized = True
        self._cache: dict[str, (str | None)] = {}
        self._cache_lock = threading.RLock()
        self._env_path = Path.home() / ".env"
        self._config_dir = Path.home() / ".azazael"
        self._refresh()

    def _refresh(self) -> None:
        """Reload all secrets from all sources (thread-safe)."""
        # Snapshot env outside lock to avoid deadlock on os.environ
        env_snapshot = dict(os.environ)

        # Load .env file (no lock needed — file reads are safe)
        env_vars_from_file: dict[str, str] = {}
        if self._env_path.exists():
            try:
                for line in self._env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and value:
                        env_vars_from_file[key] = value
            except OSError as exc:
                note_failure("secrets_manager:L206", exc)

        # Load config JSONs (no lock needed — file reads are safe)
        config_vars: dict[str, str] = {}
        if self._config_dir.exists():
            for cf in self._config_dir.glob("*_config.json"):
                try:
                    data = json.loads(cf.read_text(encoding="utf-8"))
                    api_key = data.get("api_key", "").strip()
                    if api_key:
                        stem = cf.stem.replace("_config", "")
                        config_vars[stem] = api_key
                except (json.JSONDecodeError, OSError) as exc:
                    note_failure("secrets_manager:L219", exc)

        # Build cache under lock — single atomic update
        with self._cache_lock:
            self._cache.clear()
            for provider_id, info in PROVIDER_DEFS.items():
                env_var = info["env_var"]
                key = env_snapshot.get(env_var, "")
                if not key:
                    key = env_vars_from_file.get(env_var, "")
                if not key:
                    key = config_vars.get(provider_id, "")
                    if not key:
                        cf_name = info["config_file"].replace("_config.json", "")
                        key = config_vars.get(cf_name, "")
                self._cache[env_var] = key if key else None

            for extra_var in EXTRA_ENV_VARS:
                val = env_snapshot.get(extra_var, "")
                if not val:
                    val = env_vars_from_file.get(extra_var, "")
                self._cache[extra_var] = val if val else None

    def get(self, key: str) -> (str | None):
        """Get a secret by its environment variable name or provider ID."""
        with self._cache_lock:
            if key in self._cache:
                return self._cache[key]
            if key in PROVIDER_DEFS:
                env_var = PROVIDER_DEFS[key]["env_var"]
                return self._cache.get(env_var)
            return None

    def has(self, key: str) -> bool:
        """Check if a secret exists (non-empty)."""
        val = self.get(key)
        return val is not None and len(val) > 0

    def audit(self) -> list[dict[str, str]]:
        """Audit all providers and return their status. Keys are masked for security.

        Thread-safe: takes a snapshot of _cache under lock, then builds results.
        """
        with self._cache_lock:
            cache_snapshot = dict(self._cache)
        results = []
        for provider_id, info in PROVIDER_DEFS.items():
            env_var = info["env_var"]
            key = cache_snapshot.get(env_var)
            status = "CONFIGURED" if key else "MISSING"
            masked = self._mask_key(key) if key else ""
            results.append({
                "provider": provider_id,
                "env_var": env_var,
                "status": status,
                "key_preview": masked,
                "signup_url": info.get("signup_url", ""),
                "description": info.get("description", ""),
            })
        for extra_var, desc in EXTRA_ENV_VARS.items():
            val = cache_snapshot.get(extra_var)
            status = "CONFIGURED" if val else "MISSING"
            results.append({
                "provider": extra_var.lower(),
                "env_var": extra_var,
                "status": status,
                "key_preview": "***" if val else "",
                "signup_url": "",
                "description": desc,
            })
        return results

    def status_summary(self) -> tuple[int, int]:
        """Return (configured_count, total_count)."""
        configured = sum(1 for r in self.audit() if r["status"] == "CONFIGURED")
        total = len(self.audit())
        return configured, total

    @staticmethod
    def _mask_key(key: str) -> str:
        """Mask an API key for safe display: 'nvapi-vM...CfmQ'."""
        if not key:
            return ""
        if len(key) <= 12:
            return "***"
        return key[:8] + "..." + key[-4:]

    def add_to_env(self, env_var: str, value: str, comment: str = "") -> bool:
        """Add or update a key in ~/.env. Returns True on success."""
        try:
            env = ""
            if self._env_path.exists():
                env = self._env_path.read_text(encoding="utf-8", errors="replace").rstrip()

            # Check if env_var is already defined (exact match at line start, not in comments)
            found = False
            lines = env.splitlines() if env else []
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(f"{env_var}=") and not stripped.startswith("#"):
                    new_lines.append(f"{env_var}={value}")
                    found = True
                else:
                    new_lines.append(line)

            if not found:
                if comment:
                    new_lines.append(f"# {comment}")
                new_lines.append(f"{env_var}={value}")

            # Atomic write: temp file first, then rename
            new_content = "\n".join(new_lines).strip() + "\n"
            tmp_path = self._env_path.with_name(".env.tmp")
            tmp_path.write_text(new_content, encoding="utf-8")
            tmp_path.replace(self._env_path)
            self._refresh()
            return True
        except OSError as exc:
            note_failure("secrets_manager:L338", exc)
            return False

    def reload(self) -> None:
        """Force reload all secrets from sources."""
        self._refresh()

    def __repr__(self) -> str:
        configured, total = self.status_summary()
        return f"SecretsManager({configured}/{total} configured)"


# Module-level convenience
_secrets = SecretsManager()

def get_secret(key: str) -> (str | None):
    """Get a secret by env var or provider ID."""
    return _secrets.get(key)

def audit_secrets() -> list[dict[str, str]]:
    """Quick audit of all secrets."""
    return _secrets.audit()
