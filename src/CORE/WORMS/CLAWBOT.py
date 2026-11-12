#!/usr/bin/env python3
"""
CLAWBOT.py — Azazael worm wrapper around Desktop/Libra/claw_bot.

Registers the CLAW-BOT outreach agent in the worm fleet (communication
category) so ClusterWORMZ can discover and surface it. The worm itself is
READ-ONLY: it reports outbox status and Moltbook agent state but NEVER
sends — the human-approval queue in claw_bot.outbox is the only gate.

The core module lives in Desktop/Libra (the investigation codebase); this
wrapper imports it on demand so the worm is safe even if that path is
unavailable (returns a status dict instead of crashing the fleet).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:  # pragma: no cover - standalone/import-time fallback
    note_failure("CLAWBOT:L22", exc)
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

# Desktop/Libra is the CLAW-BOT code home; make it importable from the worm.
# The worm file lives at <root>/azazael/src/CORE/WORMS/, so Desktop/Libra is
# <root>/Desktop/Libra — search upward from the worm file to find it.
_LIBRA_CANDIDATES = [
    Path(__file__).resolve().parents[4] / "Desktop" / "Libra",  # <root>/Desktop/Libra
    Path(__file__).resolve().parents[3] / "Desktop" / "Libra",  # azazael/Desktop/Libra
    Path("C:/Users/clint/Desktop/Libra"),
]
_LIBRA_DIR = next((p for p in _LIBRA_CANDIDATES if (p / "claw_bot").is_dir()), _LIBRA_CANDIDATES[0])
if str(_LIBRA_DIR) not in sys.path:
    sys.path.insert(0, str(_LIBRA_DIR))


@register_worm(
    category="communication",
    priority=6,
    cooldown_seconds=120.0,
    description=(
        "CLAW-BOT outreach agent — human-approval email/form queue "
        "(Desktop/Libra/claw_bot) + Moltbook investigative connector"
    ),
)
class CLAWBOT:
    """Worm wrapper: reports outbox + Moltbook status; never sends."""

    def execute(self) -> dict:
        """Surface CLAW-BOT state to the fleet (read-only)."""
        try:
            from claw_bot import config, contacts, moltbook, outbox
        except ImportError as e:
            note_failure("CLAWBOT:L55", e)
            return {
                "success": False,
                "error": f"claw_bot import failed: {e} (run from Desktop/Libra)",
                "hint": "cd C:/Users/clint/Desktop/Libra && python clawbot.py --help",
            }

        ox = outbox.Outbox()
        m = moltbook.status()
        return {
            "success": True,
            "agent": "CLAW-BOT",
            "outbox_counts": ox.counts(),
            "pending_count": len(ox.list("pending")),
            "approved_count": len(ox.list("approved")),
            "contact_count": len(contacts.load_contacts()),
            "moltbook": m,
            "smtp_configured": config.smtp_configured(),
            "note": "Read-only worm — sending requires human approval via "
                    "Desktop/Libra/clawbot.py",
        }
