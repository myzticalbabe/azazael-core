# coding: utf-8
"""
POINDEXTER_WORM - Mathematical Protocol Indexer worm.

Registered analysis worm over UTILITIES/POINDEXTER: seeds the shared default
systems (or caller-supplied ones) and returns the interoperability report as
a dict, so discovery, scheduling and the mesh can run POINDEXTER like any
other worm. The analysis itself lives in the utility module; this file is
only the registry entry point (production wiring for the POINDEXTER goal).

Usage:
    from CORE.WORMS.POINDEXTER_WORM import POINDEXTERWORM
    result = POINDEXTERWORM().execute()
"""

from typing import Any, Dict, List, Optional

from CORE.worm_registry import register_worm
# Hoisted 2026-10-30. The old comment claimed a CORE <-> UTILITIES cycle;
# MEASURED otherwise: `import UTILITIES.POINDEXTER.POINDEXTER` pulls ZERO
# CORE modules, so this module creates no cycle. (The package-wide count
# of CORE imports elsewhere in UTILITIES is a different claim.)
from UTILITIES.POINDEXTER.POINDEXTER import POINDEXTER, get_default_systems


@register_worm(
    category="analysis", priority=4, cooldown_seconds=300.0,
    description="Mathematical protocol indexer — interface compatibility matrix, centrality, coupling, entropy over ecosistema systems",
)
class POINDEXTERWORM:
    """Worm entry point for the POINDEXTER utility (see UTILITIES/POINDEXTER)."""

    VERSION = "1.0.0"

    def __init__(self, systems: Optional[List[Dict[str, Any]]] = None):
        self.systems = systems

    def execute(self) -> Dict[str, Any]:
        """Seed systems (defaults shared with the CLI) and return the report."""
        indexer = POINDEXTER()
        specs = self.systems if self.systems else get_default_systems()
        for spec in specs:
            indexer.add_system(
                spec["name"],
                inputs=spec["inputs"],
                outputs=spec["outputs"],
                interfaces=spec["interfaces"],
                protocols=spec["protocols"],
            )
        report = indexer.analyze()
        report["worm"] = type(self).__name__
        report["version"] = self.VERSION
        return report

    def __repr__(self) -> str:
        return f"POINDEXTERWORM(v{self.VERSION})"


if __name__ == "__main__":
    result = POINDEXTERWORM().execute()
    print(f"interoperability_index: {result['metrics']['interoperability_index']}")
    print(f"total_systems: {result['metrics']['total_systems']}")
