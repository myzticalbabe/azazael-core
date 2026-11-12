"""
Eve Unleashed — Multi-Model Router (Phase 2, V8.0).

Analyzes incoming query type and routes to the optimal local model:
  gemma-4-E2B  -> quick analysis / fleet health summaries
  Qwen2.5-1.5B -> code generation, fix proposals
  InternVL3-8B -> multimodal (screenshots, diagrams, plots)

With an automatic fallback chain if the preferred model is unavailable.

Fail-open: routes to the first available model; degrades gracefully.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
import os

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: Any = None) -> None:
        pass


#: Default model route table: capability -> ordered model list (preferred first).
DEFAULT_ROUTES: Dict[str, List[str]] = {
    "quick": ["gemma-4-e2b-it-Q8_0.gguf", "Qwen2.5-1.5B-instruct-Q8_0.gguf",
              "InternVL3-8B-Q8_0.gguf"],
    "code": ["Qwen2.5-1.5B-instruct-Q8_0.gguf", "gemma-4-e2b-it-Q8_0.gguf",
             "InternVL3-8B-Q8_0.gguf"],
    "vision": ["InternVL3-8B-Q8_0.gguf", "Qwen2.5-1.5B-instruct-Q8_0.gguf",
               "gemma-4-e2b-it-Q8_0.gguf"],
    "default": ["gemma-4-e2b-it-Q8_0.gguf", "Qwen2.5-1.5B-instruct-Q8_0.gguf",
                "InternVL3-8B-Q8_0.gguf"],
}

#: Keyword hints used to classify a query when no explicit type is given.
_CODE_HINTS = re.compile(r"\b(def |class |import |function|bug|fix|refactor|"
                         r"python|javascript|typescript|code|snippet)\b", re.I)
_VISION_HINTS = re.compile(r"\b(image|screenshot|diagram|plot|chart|vision|"
                           r"multimodal|photo|picture|figure)\b", re.I)
_QUICK_HINTS = re.compile(r"\b(health|status|summary|quick|ping|fleet|"
                          r"overview|brief)\b", re.I)


@dataclass
class RouteDecision:
    query_type: str
    model: str
    fallback_chain: List[str]
    reason: str
    confidence: float = 0.5


class ModelRouter:
    """Routes queries to the optimal local model with fallback chain."""

    def __init__(self, routes: Optional[Dict[str, List[str]]] = None,
                 available_models: Optional[List[str]] = None):
        self.routes = dict(routes) if routes else dict(DEFAULT_ROUTES)
        # available_models: models whose GGUF files actually exist on disk.
        self.available_models = set(available_models or [])
        self._route_counts: Dict[str, int] = {}

    def set_available_models(self, models: List[str]) -> None:
        self.available_models = set(models)

    def classify(self, query: str) -> str:
        """Classify a raw query into quick/code/vision/default."""
        if _VISION_HINTS.search(query):
            return "vision"
        if _CODE_HINTS.search(query):
            return "code"
        if _QUICK_HINTS.search(query):
            return "quick"
        return "default"

    def route(self, query: str, query_type: Optional[str] = None) -> RouteDecision:
        """Pick the best available model for the query."""
        qtype = query_type or self.classify(query)
        chain = self.routes.get(qtype, self.routes["default"])
        # Filter to available models, preserving preference order.
        available = [m for m in chain if m in self.available_models] or list(chain)
        chosen = available[0] if available else (chain[0] if chain else "")
        self._route_counts[qtype] = self._route_counts.get(qtype, 0) + 1
        reason = (f"classified '{qtype}'; preferred '{chain[0]}'"
                  if chain else "no routes configured")
        if chosen != (chain[0] if chain else ""):
            reason += f"; falling back to '{chosen}'"
        return RouteDecision(
            query_type=qtype, model=chosen, fallback_chain=list(available),
            reason=reason, confidence=0.8 if chosen else 0.0,
        )

    def route_for_type(self, query_type: str) -> RouteDecision:
        """Route by an explicit query type (no classification)."""
        return self.route("", query_type=query_type)

    def stats(self) -> Dict[str, Any]:
        return {"routes": dict(self.routes),
                "available_models": sorted(self.available_models),
                "route_counts": dict(self._route_counts)}


#: Convenience module-level router (lazy available_models discovery).
_router: Optional[ModelRouter] = None


def get_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
        _discover_available(_router)
    return _router


def _discover_available(router: ModelRouter) -> None:
    from pathlib import Path
    base = Path(os.environ.get("AZAZAEL_ROOT", r"C:\Azazael\azazael")) / "models"
    models: List[str] = []
    if base.exists():
        for p in base.iterdir():
            if p.suffix.lower() == ".gguf":
                models.append(p.name)
    router.set_available_models(models)


def route_query(query: str, query_type: Optional[str] = None) -> RouteDecision:
    """Module-level helper: route a query through the shared router."""
    return get_router().route(query, query_type=query_type)


__all__ = ["ModelRouter", "RouteDecision", "DEFAULT_ROUTES",
           "get_router", "route_query"]
