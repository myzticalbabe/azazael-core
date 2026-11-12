#!/usr/bin/env python3
# coding: utf-8
"""Shared logging hygiene for Azazael entry points.

Why this exists (2026-09-21). Ten entry points silenced the worm registry's
one-INFO-per-worm import chatter with a process-wide
`logging.disable(logging.CRITICAL)`. That switch does not only mute chatter: it
mutes WARNING and ERROR across the entire process. When the proprioception gate
refused to write for ~30 hours it emitted a WARNING from inside exactly such a
process, so the refusal read as success in the scheduler's log and nothing was
red.

The narrow fix -- silence the KNOWN-noisy loggers by NAME and leave WARNING+
live -- is what `scripts/spine_cycle_apply.py` adopted first. This module is
that fix's single source of truth, so the whole family adopts it without
re-deriving the logger list (and so a future noisy logger is muted in one place
instead of a dozen).

It lives in CORE rather than scripts/ so every consumer resolves it the standard
way, without depending on the scripts/ directory happening to be on sys.path:

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from CORE.log_hygiene import quiet_noisy_loggers
    quiet_noisy_loggers()

It imports only stdlib `logging`, so it is safe to import before the rest of
CORE (which it must be -- it exists to quiet the chatter those imports emit).
"""
from __future__ import annotations

import logging
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: Loggers that emit per-item chatter on import/discovery. They are silenced to
#: WARNING (not CRITICAL) by name; WARNING and above on these loggers is still
#: delivered, and no other logger is affected.
NOISY_LOGGERS: tuple[str, ...] = ("worm_registry",)


def quiet_noisy_loggers(level: int = logging.WARNING) -> None:
    """Quiet the known-chatty loggers BY NAME -- never process-wide.

    Deliberately does NOT call `logging.disable()`: a process-wide switch would
    also swallow this process's operator warnings, which is the exact defect
    this helper exists to prevent (see the module docstring for the ~30h
    silent-refusal incident).
    """
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(level)
