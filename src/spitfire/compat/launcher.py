#!/usr/bin/env python3
# coding: utf-8
"""SpitFire Launcher — OS version detection and dispatch.

Selects the appropriate implementation based on runtime OS:
  - Windows 7/Server 2008R2  -> Py3.8 GIL implementation
  - Windows 8.1/Server 2012R2 -> Py3.12 GIL implementation
  - Windows 10 1607+         -> Py3.15t free-threaded implementation
  - macOS 10.15+             -> Py3.15t universal2 implementation
  - Linux glibc 2.17+        -> Py3.15t manylinux2014 implementation
"""

from __future__ import annotations

import argparse
import importlib
import platform
import sys
from dataclasses import dataclass
from enum import Enum


class Target(Enum):
    WIN7 = "win7"
    WIN81 = "win81"
    WIN10 = "win10"
    MACOS = "macos"
    LINUX = "linux"


@dataclass(frozen=True)
class TargetInfo:
    target: Target
    python_version: str
    free_threaded: bool
    description: str


TARGET_MATRIX = {
    Target.WIN7: TargetInfo(
        Target.WIN7, "3.8", False, "Windows 7 / Server 2008R2 (Python 3.8 + GIL)"
    ),
    Target.WIN81: TargetInfo(
        Target.WIN81, "3.12", False, "Windows 8.1 / Server 2012R2 (Python 3.12 + GIL)"
    ),
    Target.WIN10: TargetInfo(
        Target.WIN10, "3.15t", True, "Windows 10 1607+ (Python 3.15t free-threaded)"
    ),
    Target.MACOS: TargetInfo(
        Target.MACOS, "3.15t", True, "macOS 10.15+ (Python 3.15t universal2)"
    ),
    Target.LINUX: TargetInfo(
        Target.LINUX, "3.15t", True, "Linux glibc 2.17+ (Python 3.15t manylinux2014)"
    ),
}


def detect_target() -> Target:
    """Detect the appropriate build target for the current OS."""
    system = platform.system().lower()

    if system == "windows":
        release = platform.release()
        version_info = sys.getwindowsversion()

        # Windows 7: 6.1, Windows 8: 6.2, Windows 8.1: 6.3, Windows 10/11: 10.0
        if version_info.major == 6 and version_info.minor == 1:
            return Target.WIN7
        if version_info.major == 6 and version_info.minor in (2, 3):
            return Target.WIN81
        # Windows 10+ (10.0)
        return Target.WIN10

    if system == "darwin":
        # macOS: check version
        mac_ver = platform.mac_ver()[0]
        major_minor = tuple(map(int, mac_ver.split(".")[:2]))
        if major_minor >= (10, 15):
            return Target.MACOS
        # Older macOS not supported by 3.15t
        return Target.MACOS

    if system == "linux":
        return Target.LINUX

    # Default to modern target
    return Target.WIN10 if system == "windows" else Target.LINUX


def get_target_info(target: Target) -> TargetInfo:
    return TARGET_MATRIX[target]


# Lazy-loaded implementation modules (avoids import_inside_function)
_IMPL_FT = None
_IMPL_GIL = None


def _get_impl_free_threaded():
    global _IMPL_FT
    if _IMPL_FT is None:
        _IMPL_FT = importlib.import_module("spitfire.compat.impl_free_threaded")
    return _IMPL_FT


def _get_impl_gil():
    global _IMPL_GIL
    if _IMPL_GIL is None:
        _IMPL_GIL = importlib.import_module("spitfire.compat.impl_gil")
    return _IMPL_GIL


def dispatch(args: argparse.Namespace) -> int:
    """Dispatch to the appropriate implementation module."""
    target = detect_target()
    info = get_target_info(target)

    print(
        f"[SpitFire] Target: {info.description}",
        file=sys.stderr,
    )
    print(
        f"[SpitFire] Running on Python {platform.python_version()} "
        f"({'free-threaded' if info.free_threaded else 'GIL'})",
        file=sys.stderr,
    )

    # Lazy-load the appropriate implementation
    if info.free_threaded:
        impl = _get_impl_free_threaded()
    else:
        impl = _get_impl_gil()

    return impl.run(args, target)


def main() -> int:
    """Standalone launcher entry point for testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=[t.value for t in Target])
    args, _ = parser.parse_known_args()

    if args.target:
        target = Target(args.target)
    else:
        target = detect_target()

    info = get_target_info(target)
    print(f"Target: {info.description}")
    return 0


if __name__ == "__main__":
    sys.exit(main())