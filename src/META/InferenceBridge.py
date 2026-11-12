#!/usr/bin/env python3
"""InferenceBridge — llama-server bridge for worm-fleet intelligence (Tier P0).

Implements the bridge proposed in ORCHESTRATION/INCORPORATION_PLAN.md §5 and
consumed by ORCHESTRATION/hydra_bridge.py: connects worm-fleet telemetry to a
local llama-server (default http://127.0.0.1:18080) so fleet operations can be
analyzed by an LLM instead of merely logged.

FAIL-OPEN CONTRACT: llama-server is a *service*, not a dependency. It may be
down, and requiring it would make everything importing this module import-fail.
So the module imports cleanly without the server; callers that need inference
check ``get_bridge_status()["llama_available"]`` first (the tests do exactly
that), and ``send_to_llama`` returns a structured no-op when unavailable.

Stdlib-only by design (urllib, not requests) — the test tree imports this
module on bare interpreters.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: Any = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: Where llama-server listens. Same default as the unified launcher scripts.
LLAMA_HOST = os.environ.get("LLAMA_SERVER_HOST", "127.0.0.1")
LLAMA_PORT = int(os.environ.get("LLAMA_SERVER_PORT", "18080"))
LLAMA_BASE = f"http://{LLAMA_HOST}:{LLAMA_PORT}"
#: Bounded so a hung server can't wedge a worm's loop.
TIMEOUT_SECONDS = float(os.environ.get("INFERENCE_BRIDGE_TIMEOUT", "30"))


class InferenceBridge:
    """Connects MetaWormSpawner telemetry to llama-server inference."""

    VERSION = "1.0.0"

    def __init__(self):
        #: Kept non-None always (tests assert ``bridge.llama is not None``);
        #: lazily connects and reports availability via get_bridge_status().
        self.llama = LlamaClient(LLAMA_BASE, TIMEOUT_SECONDS)
        #: Last inference responses, newest last, bounded ring.
        self._intelligence: List[Dict[str, Any]] = []

    # -- Public API (the INCORPORATION_PLAN contract) -----------------------

    def collect_worm_telemetry(self) -> Dict[str, Any]:
        """Gather structured data from the active worm fleet.

        Reads the process-local registry when importable; produces a stable
        structure with at least ``total_worms``/``categories`` either way.
        """
        telemetry: Dict[str, Any] = {"total_worms": 0, "categories": {}}
        try:
            from CORE.worm_registry import WORM_REGISTRY  # process-local
            for name, entry in WORM_REGISTRY.items():
                telemetry["total_worms"] += 1
                category = entry.get("category", "utility")
                telemetry["categories"][category] = (
                    telemetry["categories"].get(category, 0) + 1)
        except Exception as exc:                      # telemetry must never raise
            note_failure("inference_bridge:collect_worm_telemetry", exc)
        return telemetry

    def format_inference_prompt(self, telemetry: Dict[str, Any]) -> str:
        """Format telemetry as a llama-server prompt."""
        cats = ", ".join(f"{k}:{v}" for k, v in sorted(
            telemetry.get("categories", {}).items()))
        return (
            "Analyze this autonomous-worm-fleet telemetry and report anomalies, "
            "the top 2 risks, and one recommended action.\n"
            f"total_worms: {telemetry.get('total_worms', 0)}\n"
            f"categories: {cats}\n"
        )

    def send_to_llama(self, prompt: str) -> str:
        """Send prompt to llama-server, return response ('' if unavailable)."""
        return self.llama.complete(prompt)

    def record_intelligence(self, response: str) -> Dict[str, Any]:
        """Store an inference result (in-memory ring; PAMS when importable)."""
        entry: Dict[str, Any] = {"response": response, "length": len(response)}
        self._intelligence.append(entry)
        del self._intelligence[:-50]  # bounded
        try:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
            append = getattr(store, "append_event", None)
            if append is not None:
                append(
                    {"type": "inference_bridge_intelligence",
                     "content": response[:4000]})
        except Exception as exc:                      # recording must never raise
            note_failure("inference_bridge:record_intelligence", exc)
        return entry

    # -- Status (the shape hydra_bridge + tests read) ------------------------

    def get_bridge_status(self) -> Dict[str, Any]:
        """Status dict; the two keys tests assert are llama_available/version."""
        available = self.llama.ping()
        return {
            "llama_available": available,
            "version": self.VERSION,
            "base_url": self.llama.base_url,
            "models_loaded": self.llama.models_loaded() if available else [],
            "intelligence_recorded": len(self._intelligence),
        }


class LlamaClient:
    """Thin OpenAI-compatible client for llama-server. Never raises."""

    def __init__(self, base_url: str = LLAMA_BASE, timeout: float = TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def ping(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/health")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError, ValueError) as exc:
            note_failure("inference_bridge:ping", exc)
            return False

    def models_loaded(self) -> List[str]:
        try:
            req = urllib.request.Request(f"{self.base_url}/v1/models")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
            return [m.get("id", "?") for m in body.get("data", [])]
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            note_failure("inference_bridge:models_loaded", exc)
            return []

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        """One completion; returns '' (never raises) when server unreachable."""
        payload = json.dumps({
            "prompt": prompt,
            "max_tokens": max_tokens,
            "stream": False,
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{self.base_url}/completion", data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
            return str(body.get("content", ""))
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            note_failure("inference_bridge:complete", exc)
            return ""
