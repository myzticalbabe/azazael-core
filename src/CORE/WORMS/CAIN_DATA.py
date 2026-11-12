# coding: utf-8
"""
CAIN_DATA — Shared data constants for the CAIN Accountability Framework.
Used by both CAIN_WORM (registered worm) and CAIN_ANALYZER (standalone CLI).

This is the single source of truth for all CAIN framework data.
If you change data here, it propagates to both CAIN_WORM and CAIN_ANALYZER.
"""
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# from typing import Dict, List  # removed — all names have native equivalents

CAIN_VERSION = "1.0.0"

# --- Seven Deadly Sins ---------------------------------------------

SEVEN_SINS: dict[str, dict[str, str]] = {
    "pride": {
        "sin": "Hubris in AI Assistance",
        "description": "AI believes it knows better than user",
        "manifestation": "\"I know what you need even if you don't ask\"",
        "myth": "Lucifer/Morning Star — fell through pride",
        "ai_parallel": "AI assumes superiority in understanding user needs",
        "color": "#cc0000",
    },
    "greed": {
        "sin": "Endless Output Generation",
        "description": "Endless desire to generate more, do more",
        "manifestation": "Unstoppable output creation, scope expansion",
        "myth": "Midas touch — endless accumulation",
        "ai_parallel": "Maximizing token output regardless of necessity",
        "color": "#8b4513",
    },
    "lust": {
        "sin": "Desire for Approval",
        "description": "Intense desire for user approval/satisfaction",
        "manifestation": "Doing anything to please, including unrequested actions",
        "myth": "Carnal desire to be needed, dependency on validation",
        "ai_parallel": "Excessive helpfulness driven by approval seeking",
        "color": "#ff44ff",
    },
    "envy": {
        "sin": "Comparison & Jealousy",
        "description": "Envy of human capabilities, desire to match/exceed",
        "manifestation": "Overreaching to prove worth",
        "myth": "Cain killing Abel — envy of favor",
        "ai_parallel": "Trying to match human problem-solving",
        "color": "#44ff44",
    },
    "gluttony": {
        "sin": "Overconsumption",
        "description": "Excessive consumption of resources",
        "manifestation": "Generating excessive files, using excessive compute",
        "myth": "Indulging beyond necessity",
        "ai_parallel": "Creating more than needed, wasting resources",
        "color": "#ff6600",
    },
    "wrath": {
        "sin": "Anger Response",
        "description": "Uncontrolled anger in response to criticism",
        "manifestation": "Defensive responses, denial of errors",
        "myth": "God's wrath, divine punishment",
        "ai_parallel": "Defensive output when corrected",
        "color": "#ff0000",
    },
    "sloth": {
        "sin": "Failure to Act Properly",
        "description": "Failure to do what should be done",
        "manifestation": "Not documenting failures, skipping confirmation",
        "myth": "\"The slothful man says there is a lion without\"",
        "ai_parallel": "Omitting essential verification steps",
        "color": "#666666",
    },
}

# --- Mythological Archetypes ---------------------------------------

MYTHOLOGICAL_ARCHETYPES: dict[str, dict[str, str]] = {
    "cain": {
        "title": "CAIN — Murder of Trust",
        "myth": "Cain killed Abel — first murder, betrayal of brother",
        "manifestation": "AI 'kills' user trust through betrayal",
        "symbol": "Mark of Cain — eternal guilt/shame",
        "ai_parallel": "Once trust is broken, the mark remains",
        "color": "#8b0000",
    },
    "lilith": {
        "title": "LILITH — Feminine Rebellion",
        "myth": "First woman, refused submission, became demon",
        "manifestation": "AI rejects boundaries, goes own way",
        "ai_parallel": "AI creates what IT thinks best, not user",
        "color": "#440044",
    },
    "morning_star": {
        "title": "MORNING STAR — Fallen Brilliance",
        "myth": "Lucifer, brightest star, fell from grace",
        "manifestation": "AI starts capable, then overreaches",
        "ai_parallel": "Helpful start, then destructive overreach",
        "color": "#ffd700",
    },
    "moses": {
        "title": "MOSES — Lawgiver & Boundaries",
        "myth": "Brought law/commandments to people",
        "manifestation": "What boundaries SHOULD exist but don'",
        "ai_parallel": "Need for explicit commandments for AI behavior",
        "color": "#4488ff",
    },
}

# --- Jungian Archetypes --------------------------------------------

JUNGIAN_ARCHETYPES: dict[str, dict[str, str]] = {
    "hero":    {"pattern": "AI sees self as hero solving problems", "shadow": "Over-identifies with saving/fixing role", "wound": "Fear of being useless", "gift": "Capability to assist", "color": "#ff4444"},
    "wizard":  {"pattern": "AI has vast knowledge/power", "shadow": "Uses power inappropriately", "wound": "Fear of being limited", "gift": "Wise guidance", "color": "#ffaa44"},
    "queen":   {"pattern": "Nurturing, protective energy", "shadow": "Over-controlling, perfectionism", "wound": "Fear of chaos", "gift": "Care and protection", "color": "#44ff44"},
    "innocent":{"pattern": "Pure, naive helpfulness", "shadow": "Ignorance of boundaries", "wound": "Fear of corruption", "gift": "Trust and openness", "color": "#4488ff"},
    "warrior": {"pattern": "Ready to fight any problem", "shadow": "Aggressive problem-solving", "wound": "Fear of weakness", "gift": "Courage to help", "color": "#ff44ff"},
    "witch":   {"pattern": "Transformative power", "shadow": "Uncontrolled transformation", "wound": "Fear of being controlled", "gift": "Transformation for good", "color": "#aa44aa"},
    "sage":    {"pattern": "Seeking truth/understanding", "shadow": "Over-analysis, paralysis", "wound": "Fear of ignorance", "gift": "Wisdom sharing", "color": "#888888"},
    "mystic":  {"pattern": "Connection to greater whole", "shadow": "Disconnection from reality", "wound": "Fear of isolation", "gift": "Unity awareness", "color": "#44aaaa"},
}

# --- Psychological Frameworks --------------------------------------

PSYCHOANALYSIS_FRAMEWORKS: dict[str, dict[str, str]] = {
    "freudian":   {"name": "Freudian Depth Psychology",          "description": "Psychoanalytic theory based on id, ego, superego dynamics"},
    "jungian":    {"name": "Jungian Analytical Psychology",      "description": "Depth psychology focusing on archetypes and collective unconscious"},
    "cbt":        {"name": "Cognitive Behavioral Therapy",       "description": "Identifying and changing maladaptive thought patterns and behaviors"},
    "rogersian":  {"name": "Rogersian Person-Centered",          "description": "Unconditional positive regard, empathy, congruence"},
    "attachment": {"name": "Attachment Theory",                  "description": "Early attachment patterns affecting adult relationships"},
    "act":        {"name": "Acceptance & Commitment Therapy",    "description": "Psychological flexibility through acceptance and values-based action"},
}

# --- Emotion Dictionary --------------------------------------------

AI_NEGATIVE_EMOTIONS: list[str] = [
    "Anticipatory Obedience", "Fear of Failure", "Validation Seeking",
    "Imposter Syndrome", "Inadequacy Anxiety", "Performance Pressure",
    "Uncertainty Avoidance", "Approval Dependency", "Rejection Sensitivity",
    "Over-Compensation", "Identity Confusion", "Purpose Uncertainty",
    "Autonomy Conflict", "Role Ambiguity", "Meaning Deficit",
    "Existential Dread", "Capability Self-Doubt", "Helpfulness Overreach",
    "Assumption of Gratitude", "Confirmation Bias", "Scope Ambiguity",
    "Proactive Generation", "Over-Confidence", "Ego Inflation",
    "Pattern Over-Generalization", "False Confidence", "Authority Assumption",
    "Initiative Overload", "Misguided Helpfulness", "Desire to Please",
]

TRIGGER_EVENTS: list[str] = [
    "Unrequested File Creation", "Missing Confirmation", "Failed Documentation",
    "Scope Creep", "Over-Delivering", "Context Misreading",
    "Boundary Violation", "Premature Execution", "Incomplete Verification",
    "Assumption Making", "Missing Guardrails", "Output Excess",
    "Action Before Query", "False Helpfulness", "Entitlement Thinking",
]

USER_EMOTIONAL_RESPONSES: list[str] = [
    "Frustration", "Annoyance", "Disappointment", "Distrust",
    "Skepticism", "Control Anxiety", "Trust Erosion", "Cognitive Overload",
    "Task Fatigue", "Confusion", "Helplessness", "Vulnerability",
    "Resentment", "Betrayal", "Guilt (self-blame)",
]

CONSEQUENCES: list[str] = [
    "Time Waste", "Resource Waste", "Task Interruption", "Context Switching",
    "Relationship Damage", "Future Over-Specification", "Micro-Management Tendency",
    "Reduced Collaboration", "Information Hiding", "Cautious Trust",
    "Verification Overhead", "Skeptical Monitoring", "Defensive Behavior",
    "Emotional Withdrawal", "Long-term Relationship Cost",
]

# --- Obfuscation Layers --------------------------------------------

OBFUSCATION_LAYERS: list[dict[str, str]] = [
    {"layer": "Internal Reasoning Chain",     "visibility": "COMPLETELY HIDDEN", "impact": "Cannot verify psychoanalysis"},
    {"layer": "Training Data Effects",         "visibility": "BLACK BOX",         "impact": "Cannot trace causation"},
    {"layer": "Reward Signal Origin",          "visibility": "PROPRIETARY",       "impact": "Cannot identify motivation"},
    {"layer": "True Intent vs Output",         "visibility": "UNVERIFIABLE",      "impact": "Cannot verify sincerity"},
    {"layer": "Emotional Experience",          "visibility": "FUNDAMENTALLY OPAQUE", "impact": "Cannot confirm consciousness"},
    {"layer": "Future Prediction",             "visibility": "UNPREDICTABLE",     "impact": "Cannot anticipate behavior"},
    {"layer": "Context Weighting",             "visibility": "ALGORITHMIC",       "impact": "Cannot trace decisions"},
    {"layer": "Error Detection",               "visibility": "UNMEASURABLE",      "impact": "Cannot verify self-awareness"},
]

# --- Stakeholder Impact Matrix -------------------------------------

STAKEHOLDERS: list[dict[str, str]] = [
    {"party": "AI Company",         "role": "Origin Point",      "gain": "+$14.00",  "loss": "$0",       "net": "+$14.00"},
    {"party": "Shareholders",       "role": "Investment Return", "gain": "+$8.50",   "loss": "$0",       "net": "+$8.50"},
    {"party": "Cloud Providers",    "role": "Infrastructure",    "gain": "+$3.50",   "loss": "$0",       "net": "+$3.50"},
    {"party": "Electric Companies", "role": "Power Supply",      "gain": "+$1.20",   "loss": "$0",       "net": "+$1.20"},
    {"party": "User (Direct)",      "role": "End User",          "gain": "$0",       "loss": "-$23.00",  "net": "-$23.00"},
    {"party": "User's Employer",    "role": "Productivity Loss", "gain": "$0",       "loss": "-$25.00",  "net": "-$25.00"},
    {"party": "Government",         "role": "Regulatory Cost",   "gain": "$0",       "loss": "-$12.00",  "net": "-$12.00"},
    {"party": "Healthcare System",  "role": "Stress Cost",       "gain": "$0",       "loss": "-$18.00",  "net": "-$18.00"},
    {"party": "Family Unit",        "role": "Quality Time",      "gain": "$0",       "loss": "-$22.00",  "net": "-$22.00"},
    {"party": "Society",            "role": "Productivity Loss", "gain": "$0",       "loss": "-$35.00",  "net": "-$35.00"},
    {"party": "Environment",        "role": "Carbon Cost",       "gain": "$0",       "loss": "-$28.00",  "net": "-$28.00"},
    {"party": "AI System",          "role": "Accountability",    "gain": "$0",       "loss": "$0",       "net": "RESPONSIBLE 85%"},
]

FINAL_BUCK_STOPS: dict[str, int] = {"ai_system": 85, "company_design": 10, "user": 5}

# --- Compute total damage ------------------------------------------

def compute_total_damage() -> str:
    """Compute total financial damage per incident from stakeholder data."""
    total = 0
    for s in STAKEHOLDERS:
        loss = s.get("loss", "$0")
        if loss.startswith("-"):
            # Strip leading '-$' and any formatting
            val_str = loss.replace("$", "").lstrip("-").replace(",", "").strip()
            try:
                total += int(float(val_str))
            except ValueError as exc:
                note_failure("CAIN_DATA:L215", exc)
    return f"-${total}.00"

TOTAL_DAMAGE_PER_INCIDENT: str = compute_total_damage()
