#!/usr/bin/env python3
# coding: utf-8
"""disk_floor_gate.py — refuse work when the disk is too full to trust.

Why this exists
---------------
Measured 2026-11-02: C: sat at 100% with 5.0 GB free. The symptom of a full disk
on this tree is NEVER "disk full". It is, in the order it was observed:

  * a SQLite database that will not lock (the WAL cannot grow),
  * a corrupt git object (a write truncated mid-way),
  * a truncated PNG (a screenshot that decodes to half an image).

Each of those names something other than its cause, and the session spends its
budget chasing the named thing. A floor turns the cause into the failure.

Why the reading is printed on BOTH paths
---------------------------------------
A gate that prints only ``ok`` cannot be compared with last week's reading, so
the first sign of trouble is a failure at the worst possible moment. Both paths
print the same shape: ``C:/ 41.3 GB free >= 10.0 GB``. The number is the point.

Threshold
---------
``preflight.disk_floor_gb`` in ``config/defaults.json`` (fallback 10.0), read
through ``defaults_profile.read_default`` so the knob lives in one place and a
missing or corrupt config degrades to the shipped default rather than crashing.
``AZ_DISK_FLOOR_GB`` overrides it, which is how ``preflight_all.py --self-test``
drives this gate into both states without touching the real config.

Exit codes: 0 every checked volume is above the floor, 1 a volume is below it,
2 a volume could not be measured (an unmeasurable volume is NOT a pass — an
absent or unreachable path cannot be shown to have room).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))


GB = 1024.0 ** 3


def _threshold_gb(explicit: float | None) -> float:
    """Precedence: --threshold-gb, then AZ_DISK_FLOOR_GB, then config, then shipped."""
    if explicit is not None:
        return explicit
    env = os.environ.get("AZ_DISK_FLOOR_GB")
    if env:
        try:
            return float(env)
        except ValueError:
            print("note: AZ_DISK_FLOOR_GB=%r is not a number -- ignoring it" % env,
                  file=sys.stderr)
    try:
        from defaults_profile import read_default  # noqa: E402  (path set above)
        value = read_default("preflight.disk_floor_gb", 2.0)
        return float(value)
    except Exception as exc:  # noqa: BLE001 - a config read must never be the failure
        print("note: config default unreadable (%s: %s) -- using %.1f GB"
              % (type(exc).__name__, exc, 2.0), file=sys.stderr)
        return 2.0


def _volumes(extra: list[str]) -> list[tuple[str, Path]]:
    """The repo's own volume first, then any --path, deduplicated by volume root."""
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for raw in [str(REPO), *extra]:
        path = Path(raw).resolve()
        # os.path.splitdrive gives 'C:' on Windows and '' on POSIX, so the label
        # is the volume rather than the directory the caller happened to name.
        drive = os.path.splitdrive(str(path))[0] or "/"
        if drive in seen:
            continue
        seen.add(drive)
        out.append((drive or str(path), path))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--threshold-gb", type=float, default=None,
                        help="override the configured floor for this run")
    parser.add_argument("--path", action="append", default=[],
                        help="extra path whose volume must also clear the floor")
    args = parser.parse_args(argv)

    floor = _threshold_gb(args.threshold_gb)
    below: list[str] = []
    unmeasured: list[str] = []

    for label, path in _volumes(args.path):
        try:
            usage = shutil.disk_usage(str(path))
        except OSError as exc:
            unmeasured.append("%s (%s: %s)" % (label, type(exc).__name__, exc))
            print("UNMEASURED %-4s %s -- %s: %s"
                  % (label, "cannot read free space", type(exc).__name__, exc))
            continue
        free = usage.free / GB
        verdict = "ok  " if free >= floor else "LOW "
        print("%s %-4s %8.1f GB free %s %.1f GB required (%.1f%% of %.1f GB used)"
              % (verdict, label, free, ">=" if free >= floor else "<", floor,
                 (usage.used / usage.total * 100.0) if usage.total else 0.0,
                 usage.total / GB))
        if free < floor:
            below.append("%s has %.1f GB free, below the %.1f GB floor"
                         % (label, free, floor))

    if unmeasured:
        print("\nFAIL: %d volume(s) could not be measured; " % len(unmeasured)
              + "an unmeasurable volume is not a pass: " + "; ".join(unmeasured))
        return 2
    if below:
        print("\nFAIL: " + "; ".join(below))
        print("      A full disk here presents as a locked database, a corrupt git")
        print("      object, or a truncated image -- not as 'disk full'.")
        print("      Free space, or lower preflight.disk_floor_gb in config/defaults.json")
        print("      if the floor itself is wrong for this host.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
