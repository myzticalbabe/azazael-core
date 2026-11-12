# coding: utf-8
"""Project SpitFire — TswArm standalone executable with multi-OS support.

Multi-binary strategy:
  - Win7:      Python 3.8  + MSVC 14.1  (GIL)
  - Win8.1:    Python 3.12 + MSVC 14.2  (GIL)
  - Win10+:    Python 3.15t+ MSVC 14.3  (free-threaded)
  - macOS10.15+: Python 3.15t  universal2
  - Linux:     Python 3.15t  manylinux2014

GIL-sidecar pattern: core logic compiled once, interpreter swapped at runtime.
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("spitfire")
except PackageNotFoundError:
    __version__ = "1.0.0-dev"

__all__ = ["__version__"]