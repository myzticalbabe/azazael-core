#!/usr/bin/env python3
# coding: utf-8
"""One rule, shared by every gate that writes evidence: the path follows the scope.

Why this is a module and not two copies
---------------------------------------
`tools/aggregate_suite_gate.py` and `tools/type_check_gate.py` both pin their
baseline and report to a fixed path, and both accept a flag (`--only`) that
narrows what is measured. That combination let a one-file smoke test write
`1 passed in 1s` over a 301-test profile run, under a filename that still claimed
to describe the profile (Error #195, ADR-012).

Two copies of the rule would drift the moment one of them was "improved", so the
rule lives here and each gate imports it. The rule is two lines long; the shared
residence is the point.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Sequence


def is_narrowed(expected: Sequence[str], select: Optional[Iterable[str]]) -> bool:
    """True when an explicit selection is smaller than the profile's own set.

    Membership rather than equality: naming the profile's files in another order
    is still a full run of the profile, so it keeps the profile's evidence path.
    """
    if not select:
        return False
    return not set(expected) <= set(select)


def partial(path: Path) -> Path:
    """The sibling a narrowed run is allowed to write.

    A `.partial` marker rather than a new directory: it sits beside the artifact
    it is not, so a reader who finds one finds the other and sees which is which.
    """
    return path.with_name(path.stem + ".partial" + path.suffix)


def narrowed_note(scope: str, report: Path, baseline: Path) -> str:
    """The line a narrowed run prints, so the redirect is never silent."""
    return ("NOTE: the selection names a subset of %s, so this run's report and "
            "baseline go to %s / %s -- a narrowed run is not the scope's "
            "evidence" % (scope, report.name, baseline.name))
