"""Live therapy + voice-assistance logic for Eve.

Pure stdlib so the gate can test it without network, models, or audio
hardware. The live path is: browser Web Speech API (mic + speakers) <->
Eve serve /therapy (swarm LLM with NSP judgment, static grounding
fallback). This module holds the pieces both sides share:

- build_prompt: therapy system prompt from mood + note.
- fallback_response: offline grounding script, always available.
- parse_voice_command: transcript -> allowlisted dev/therapy action.
"""
GROUNDING_STEPS = [
    "Name 5 things you can see right now.",
    "Name 4 things you can touch.",
    "Name 3 things you can hear.",
    "Name 2 things you can smell.",
    "Name 1 thing you can taste.",
]

BREATHING = ("Breathe in 4 counts, hold 4, out 6. "
             "We will do two rounds together.")


def build_prompt(mood, note):
    """Therapy prompt sent to the swarm. Never a diagnosis."""
    mood = (mood or "stressed").strip()[:60]
    note = (note or "").strip()[:500]
    return (
        "You are Eve, a warm supportive companion, not a clinician. "
        "The user feels %s. They shared: %s. "
        "Give a short supportive reply (under 120 words): validate the "
        "feeling, one 60-second grounding step, one breathing cue, and "
        "one tiny next action. Reply directly to the user; do not narrate "
        "your reasoning process. No diagnosis, no medical advice, no "
        "emergency claims. End by asking what they need next."
        % (mood, note or "nothing more yet"))


def fallback_response(mood, note):
    """Static grounding response when the swarm is unreachable."""
    mood = (mood or "stressed").strip()
    lines = [
        "I hear you. Feeling %s makes sense, and you showed up anyway."
        % mood,
        "",
        BREATHING,
        "",
        "Grounding (pick one step): %s" % GROUNDING_STEPS[0],
        "",
        "Tiny next action: drink water, unclench your jaw, "
        "look 20 feet away for 20 seconds.",
        "",
        "I am here with you. What do you need next?",
    ]
    if note:
        lines.insert(1, "You shared: %s" % note[:200])
    return "\n".join(lines)


def parse_voice_command(transcript):
    """Map a voice transcript to an allowlisted action dict.

    Returns {"action": ...} where action is one of: therapy, breathe,
    apply-best, revert, next, help, unknown. Never executes anything.
    """
    t = (transcript or "").lower()
    if any(k in t for k in ("therap", "calm", "anxious", "stressed",
                            "ground me", "feel")):
        return {"action": "therapy"}
    if "breath" in t or "breathe" in t:
        return {"action": "breathe"}
    if "apply" in t and "best" in t:
        return {"action": "apply-best"}
    if "revert" in t or "undo" in t:
        return {"action": "revert"}
    if "next" in t or "continue" in t or "forward" in t:
        return {"action": "next"}
    if "help" in t or "what can you" in t:
        return {"action": "help"}
    return {"action": "unknown", "heard": (transcript or "")[:120]}
