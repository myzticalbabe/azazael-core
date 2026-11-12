"""
Eve Unleashed — Natural Language Interface (Phase 4, V10.0).

Intent parsing: understand what the user wants, not just match commands.
Proactive suggestions: "I noticed X, would you like me to Y?"
Narrative reports: human-readable ecosystem activity summaries.
Conversational KLINK: chat-style wrapper over the decision engine / KLINK eve.

Fail-open: degrades to keyword matching if NLP/bridge unavailable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: Any = None) -> None:
        pass


@dataclass
class Intent:
    """Parsed user intent."""
    action: str            # status | health | spawn | approve | reject | veto | suggest | help | unknown
    confidence: float      # 0..1
    entities: Dict[str, Any] = field(default_factory=dict)
    raw: str = ""


#: Keyword -> intent mapping (fallback when no LLM).
_INTENT_KEYWORDS: Dict[str, List[str]] = {
    "status": ["status", "state", "health", "how is", "summary"],
    "health": ["health", "failing", "unhealthy", "broken", "down"],
    "spawn": ["spawn", "start", "launch", "create worm"],
    "approve": ["approve", "ok", "yes", "go ahead"],
    "reject": ["reject", "no", "deny", "stop"],
    "veto": ["veto", "block", "override", "forbid"],
    "suggest": ["suggest", "recommend", "what should", "idea"],
    "help": ["help", "commands", "what can you do"],
}


class IntentParser:
    """Classify free-text input into an intent + entities."""

    def __init__(self, bridge=None):
        self._bridge = bridge

    def _get_bridge(self):
        if self._bridge is not None:
            return self._bridge
        try:
            from META.InferenceBridge import InferenceBridge
            self._bridge = InferenceBridge()
            return self._bridge
        except Exception as exc:
            note_failure("nlp:bridge", exc)
            return None

    def parse(self, text: str) -> Intent:
        lowered = text.lower().strip()
        # Try LLM-based classification when available.
        bridge = self._get_bridge()
        if bridge is not None:
            try:
                status = bridge.get_bridge_status()
                if status.get("llama_available", False):
                    prompt = ("Classify the user's request into ONE of: "
                              "status, health, spawn, approve, reject, veto, suggest, help, unknown. "
                              "Reply with ONLY the intent word.\n" + text)
                    verdict = bridge.send_to_llama(prompt).strip().lower()
                    if verdict in _INTENT_KEYWORDS:
                        return Intent(action=verdict, confidence=0.9, raw=text)
            except Exception as exc:
                note_failure("nlp:llm_classify", exc)

        # Fallback: keyword matching (whole-word to avoid "start" inside "restart").
        best_action = "unknown"
        best_score = 0.0
        for action, kws in _INTENT_KEYWORDS.items():
            hits = 0
            for kw in kws:
                if " " in kw:
                    if kw in lowered:
                        hits += 1
                else:
                    if re.search(r"\b" + re.escape(kw) + r"\b", lowered):
                        hits += 1
            score = hits / max(1, len(kws))
            if score > best_score:
                best_score = score
                best_action = action
        confidence = min(0.8, best_score + 0.2) if best_action != "unknown" else 0.15
        entities = self._extract_entities(lowered, best_action)
        return Intent(action=best_action, confidence=confidence,
                      entities=entities, raw=text)

    def _extract_entities(self, lowered: str, action: str) -> Dict[str, Any]:
        ent: Dict[str, Any] = {}
        if action in ("approve", "reject"):
            m = re.search(r"\b([0-9a-f]{8,})\b", lowered)
            if m:
                ent["decision_id"] = m.group(1)
        if action == "veto":
            m = re.search(r"veto\s+(\w+)", lowered)
            if m:
                ent["action_name"] = m.group(1)
        if action == "spawn":
            m = re.search(r"spawn\s+(?:a\s+)?(\w+)", lowered)
            if m:
                ent["worm_type"] = m.group(1)
        return ent


@dataclass
class Suggestion:
    """A proactive suggestion to the user."""
    id: str
    title: str
    body: str
    action: str          # KLINK eve subcommand to act on
    confidence: float
    source: str          # "introspection" | "reflection" | "health"


class ProactiveSuggestions:
    """Generate 'I noticed X, would you like me to Y?' suggestions."""

    def __init__(self, decision_engine=None, introspection=None,
                 reflection=None):
        self._engine = decision_engine
        self._introspection = introspection
        self._reflection = reflection

    def generate(self, limit: int = 5) -> List[Suggestion]:
        out: List[Suggestion] = []
        # Health-based: pending decisions need approval.
        if self._engine is not None:
            pending = self._engine.pending()
            for d in pending[: limit - len(out)]:
                out.append(Suggestion(
                    id=f"pending_{d['id'][:8]}",
                    title=f"Approve pending action: {d['action']}",
                    body=(f"Eve proposes '{d['action']}' (confidence "
                          f"{d['confidence']:.2f}, risk {d['risk']}). "
                          f"Rationale: {d['rationale']}"),
                    action=f"eve approve {d['id'][:8]}",
                    confidence=d['confidence'],
                    source="health",
                ))
        # Introspection-based: flagged risks.
        if self._introspection is not None:
            for ins in self._introspection.recent_insights(limit - len(out)):
                analysis = str(ins.get("bridge_analysis", ""))
                if "risk" in analysis.lower() or "gap" in analysis.lower():
                    out.append(Suggestion(
                        id=f"introspect_{len(out)}",
                        title="Review introspection finding",
                        body=analysis[:200],
                        action="eve status",
                        confidence=0.5,
                        source="introspection",
                    ))
        # Reflection-based: no improvement -> suggest goal.
        if self._reflection is not None and len(out) < limit:
            cycles = self._reflection.snapshot()
            if cycles and not cycles[-1].get("improved", False):
                out.append(Suggestion(
                    id="reflect_stall",
                    title="Fitness stalled — propose a new goal",
                    body="Last morph cycle showed no measurable improvement. "
                         "Would you like me to propose a new improvement goal?",
                    action="eve status",
                    confidence=0.6,
                    source="reflection",
                ))
        return out[:limit]


class NarrativeReport:
    """Human-readable ecosystem activity summary."""

    def __init__(self, decision_engine=None, introspection=None,
                 reflection=None, self_model=None):
        self._engine = decision_engine
        self._introspection = introspection
        self._reflection = reflection
        self._self_model = self_model

    def generate(self) -> str:
        lines: List[str] = []
        lines.append("Eve Unleashed — Ecosystem Narrative Report")
        lines.append("=" * 50)

        if self._self_model is not None:
            snap = self._self_model.snapshot()
            lines.append(f"Architecture version: {snap['version']}")
            lines.append(f"Components tracked: {len(snap['components'])}")
            lines.append(f"Autonomy level: {snap['autonomy_level']}")
            lines.append(f"Confidence threshold: {snap['confidence_threshold']}")
            unhealthy = [n for n, c in snap['components'].items()
                         if c.get("health", 1) < 0.7]
            if unhealthy:
                lines.append(f"Unhealthy components: {', '.join(unhealthy)}")
        lines.append("")

        if self._engine is not None:
            stats = self._engine.stats()
            lines.append(f"Autonomous decisions: {stats['total']} total, "
                         f"{stats['by_status'].get('executed', 0)} executed, "
                         f"{stats['by_status'].get('pending', 0)} pending")
            lines.append(f"Vetoed actions: {', '.join(stats['vetoed_actions']) or 'none'}")
            lines.append(f"Executed rate: {stats['executed_rate']:.2f}")
        lines.append("")

        if self._reflection is not None:
            cycles = self._reflection.snapshot()
            if cycles:
                last = cycles[-1]
                lines.append(f"Last reflection: generation delta "
                             f"{last.get('generation_delta', 0)}, "
                             f"{last.get('note', 'unknown')}")
        lines.append("")
        lines.append("End of report.")
        return "\n".join(lines)


class ConversationalKLINK:
    """Chat-style entry point: parses intent, acts via decision engine / KLINK
    eve, returns a natural-language response."""

    def __init__(self, decision_engine=None, intent_parser=None,
                 suggestions=None, report=None):
        self._engine = decision_engine
        self._parser = intent_parser or IntentParser()
        self._suggestions = suggestions
        self._report = report

    def respond(self, text: str) -> str:
        intent = self._parser.parse(text)
        if intent.action == "unknown":
            return ("I'm not sure what you'd like me to do. Try: "
                    "'eve status', 'approve <id>', 'veto <action>', or 'help'.")
        if intent.action == "help":
            return ("I can help with Eve Unleashed autonomous control: "
                    "check status, approve/reject pending decisions, veto "
                    "actions, and view reports. Try 'eve status' or 'help'.")
        if intent.action == "status":
            if self._engine is None:
                return "Decision engine unavailable."
            stats = self._engine.stats()
            pend = self._engine.pending()
            parts = [f"{stats['total']} decisions tracked "
                     f"({stats['by_status'].get('executed', 0)} executed, "
                     f"{stats['by_status'].get('pending', 0)} pending)."]
            if pend:
                parts.append("Pending your approval: " +
                             "; ".join(f"{d['action']} (conf {d['confidence']:.2f})"
                                       for d in pend[:5]))
            return " ".join(parts)
        if intent.action == "approve":
            did = intent.entities.get("decision_id")
            if not did:
                return "Please specify a decision id to approve (e.g. 'approve abc123')."
            if self._engine is None:
                return "Decision engine unavailable."
            full = self._engine.resolve_id(did)
            if full is None:
                return f"Could not find a decision matching '{did}'."
            ok = self._engine.approve(full, decided_by="human")
            return ("Approved." if ok else
                    f"Could not approve '{full}' — already decided.")
        if intent.action == "reject":
            did = intent.entities.get("decision_id")
            if not did:
                return "Please specify a decision id to reject."
            if self._engine is None:
                return "Decision engine unavailable."
            full = self._engine.resolve_id(did)
            if full is None:
                return f"Could not find a decision matching '{did}'."
            ok = self._engine.reject(full, decided_by="human")
            return ("Rejected." if ok else
                    f"Could not reject '{full}' — already decided.")
        if intent.action == "veto":
            aname = intent.entities.get("action_name")
            if not aname:
                return "Please specify an action to veto (e.g. 'veto restart_service')."
            from eve_unleashed.decision_engine import DecisionStatus  # noqa: WPS433
            vetoed = False
            if self._engine is not None:
                for d in list(self._engine._decisions.values()):
                    if d.action == aname and d.status == DecisionStatus.PENDING:
                        self._engine.veto(d.id, decided_by="human",
                                          reason="user chat veto")
                        vetoed = True
                self._engine._vetoed_actions.add(aname)
            return (f"Vetoed '{aname}' (pending decision(s) blocked)."
                    if vetoed else f"Vetoed '{aname}' going forward.")
        if intent.action == "suggest":
            if self._suggestions is None:
                return "Proactive suggestions unavailable."
            sugs = self._suggestions.generate(3)
            if not sugs:
                return "No proactive suggestions right now — everything looks stable."
            lines = ["Here's what I noticed:"]
            for s in sugs:
                lines.append(f"• {s.title} — {s.body} (say: '{s.action}')")
            return "\n".join(lines)
        if intent.action == "health":
            return self._report.generate() if self._report else \
                "Health reporting unavailable."
        return "I understood that, but haven't wired that action yet."


__all__ = ["Intent", "IntentParser", "Suggestion", "ProactiveSuggestions",
           "NarrativeReport", "ConversationalKLINK"]
