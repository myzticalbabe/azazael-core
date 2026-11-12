"""HYPER_EDGE -- the next-generation meta-evolution release candidate.

Target line: Python 3.16t (free-threaded). The 3.16t interpreter does not exist
yet, so this package is written against **capabilities**, never against
``sys.version_info``: it runs unchanged on today's 3.15t production interpreter,
on a GIL build, and on 3.16t the day one is installed. See ``compat`` for the
detection layer and ``docs/HYPER_EDGE_ENGINEERING_RC_2026-11-05.md`` for the
adoption contract.

The three layers, bottom up:

``compat``      capability detection, bounded parallel map, a report a caller can
                quote without reading this file.
``microcode``   the admissibility ISA: twelve ops distilled from measurements this
                tree paid for, each with an explicit legacy behaviour so a pre-RC
                consumer can be replayed and *proved* different, not assumed fixed.
``evolution``   meta-evolution over recorded evidence: genomes are admissibility
                parameters, fitness is the reading a genome would produce on rows
                that already exist, selection is Pareto plus maximin, and the
                output is a proposed per-environment overlay.
"""

from __future__ import annotations

from HYPER_EDGE.compat import capabilities, parallel_map, report as compat_report
from HYPER_EDGE.evolution import evolve, load_evidence, pin_shares
from HYPER_EDGE.microcode import ISA, evaluate, isa_table

__version__ = "0.1.0rc1"
TARGET = "Python 3.16t (free-threaded) -- capability-detected, runs on 3.15t today"

__all__ = [
    "__version__",
    "TARGET",
    "ISA",
    "capabilities",
    "compat_report",
    "evaluate",
    "evolve",
    "isa_table",
    "load_evidence",
    "parallel_map",
    "pin_shares",
]
