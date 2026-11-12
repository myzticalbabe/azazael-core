"""grantforge_native — the executable's stdlib-only core.

Kept separate from `grantforge/` on purpose: that package imports pydantic,
typer, rich, httpx and lxml before any command runs, which is ~1 GB of optional
dependency and an import side effect a binary cannot afford. This package
imports nothing outside the standard library, so `grantforge.exe` starts on a
machine that has no Python at all.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
