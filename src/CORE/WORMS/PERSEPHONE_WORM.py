# coding: utf-8
"""
PERSEPHONE_WORM - Cross-platform path/env sync worm.

Registered sync worm over UTILITIES/PERSEPHONE: proves the Windows<->WSL
translation layer and Azazael root discovery, returning the result as a
dict so discovery, scheduling and the mesh can run PERSEPHONE like any
other worm. The translation itself lives in the utility module; this file
is only the registry entry point (production wiring for the PERSEPHONE
orphan goal).

Usage:
    from CORE.WORMS.PERSEPHONE_WORM import PERSEPHONEWORM
    result = PERSEPHONEWORM().execute()
"""

from pathlib import Path
from typing import Any, Dict, Optional

from CORE.worm_registry import register_worm
from UTILITIES.PERSEPHONE.persephone import PathTranslator, detect_platform


@register_worm(
    category="sync", priority=4, cooldown_seconds=300.0,
    description="Cross-platform path/env sync — Windows/WSL translation proof and Azazael root discovery",
)
class PERSEPHONEWORM:
    """Worm entry point for the PERSEPHONE utility (see UTILITIES/PERSEPHONE)."""

    VERSION = "1.0.0"

    def __init__(self, probe_path: Optional[str] = None):
        # Default probe is the live repo root, resolved at runtime — never a
        # hardcoded drive path (anti-pattern gate: hardcoded_win_path).
        self.probe_path = probe_path or str(Path(__file__).resolve().parents[3])

    def execute(self) -> Dict[str, Any]:
        """Prove translation round-trips and report platform + roots."""
        platform = detect_platform().value
        to_wsl = PathTranslator.windows_to_wsl(self.probe_path)
        round_tripped = PathTranslator.wsl_to_windows(to_wsl)
        azazael = PathTranslator.detect_azazael()
        return {
            "worm": type(self).__name__,
            "version": self.VERSION,
            "platform": platform,
            "probe_path": self.probe_path,
            "wsl_path": to_wsl,
            "round_trip_ok": round_tripped == self.probe_path,
            "azazael_windows": azazael.windows_path,
            "azazael_wsl": azazael.wsl_path,
            "passed": round_tripped == self.probe_path,
        }

    def __repr__(self) -> str:
        return f"PERSEPHONEWORM(v{self.VERSION})"


if __name__ == "__main__":
    result = PERSEPHONEWORM().execute()
    print(f"platform: {result['platform']}")
    print(f"round_trip_ok: {result['round_trip_ok']}")
