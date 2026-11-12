#!/usr/bin/env python3
# coding: utf-8
"""No Azazael module may silence ALL logging process-wide.

The 2026-09-21 incident: the proprioception gate refused to write for ~30 hours
and emitted a WARNING from inside `scripts/spine_cycle_apply.py`, whose
import-time `logging.disable(logging.CRITICAL)` muted it -- so a stalled loop
read as success and nothing was red. A sweep found nine sibling entry points
carrying the same blanket switch, each one a latent copy of the same silent
failure waiting for a warning that mattered.

The narrow replacement lives in `src/CORE/log_hygiene.py`: silence the KNOWN
chatty loggers by NAME and leave WARNING+ live for the rest of the process.
These tests keep the sweep from being undone -- across `scripts/` AND `src/`,
not just the entry points:

  1. fail on any `logging.disable` call anywhere under scripts/ or src/;
  2. confine module-scope `logging.basicConfig` in src/ to a documented
     allowlist, so a NEW global logging configuration cannot appear unnoticed;
  3. pin that the shared helper quiets *by name* and never disables globally.

The AST is used rather than a text match so prose about `logging.disable` in a
docstring (this module, log_hygiene's docstring, spine_cycle_apply's comment)
is not mistaken for a call.
"""
from __future__ import annotations

import ast
import logging
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SCRIPTS = os.path.join(_ROOT, "scripts")
_SRC = os.path.join(_ROOT, "src")
for _p in (_ROOT, _SCRIPTS, _SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_SKIP_DIRS = {"__pycache__", ".mypy_cache", ".git", ".venv-3.14t", ".venv",
              "node_modules", ".pytest_cache"}

#: Modules that legitimately configure logging for their OWN application at
#: import. These are app/config entry modules rather than libraries, and
#: `basicConfig(level=INFO)` does not suppress WARNING -- so they are tolerated,
#: but listed here so a NEW global logging configuration cannot slip in. Entries
#: are repo-relative with forward slashes. `_walk_py` is run over src/ only;
#: scripts/ entry points configure their own logging via log_hygiene instead.
ALLOWED_BASIC_CONFIG = {
    "src/CODEX/CORE/AUTO_DOWNLOADER.py",
    "src/CORE/arxiv_integration.py",
    "src/CORE/orkg_integration.py",
    "src/HYDRA/ml_platforms/config.py",
    "src/ORCHESTRATION/ml_platforms/config.py",
    "src/ORCHESTRATION/ml_platforms/verify_moe.py",
    "src/ORCHESTRATION/scripts/azazael_wsa.py",
    "src/UTILITIES/TARBABY/tarbaby_enhanced.py",
    "src/UTILITIES/TERENCE/terence_enhanced.py",
}


def _walk_py(root: str):
    """Every .py file under root, skipping caches and virtualenvs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _logging_calls(path: str, attr: str) -> list[int]:
    """Line numbers of real `logging.<attr>(...)` calls in a module."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except SyntaxError:
        return []
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == attr
                and isinstance(func.value, ast.Name) and func.value.id == "logging"):
            hits.append(node.lineno)
    return hits


def _rel(path: str) -> str:
    return os.path.relpath(path, _ROOT).replace("\\", "/")


class TestNoBlanketLoggingDisable(unittest.TestCase):

    def test_no_module_disables_logging_process_wide(self):
        offenders: list[str] = []
        for root in (_SCRIPTS, _SRC):
            for path in _walk_py(root):
                for line in _logging_calls(path, "disable"):
                    offenders.append(f"{_rel(path)}:{line}")
        self.assertEqual(
            offenders, [],
            "a process-wide `logging.disable` also swallows this process's "
            "operator warnings -- use log_hygiene.quiet_noisy_loggers() instead "
            "(see the 2026-09-21 silent-refusal incident). Offenders: "
            + ", ".join(offenders),
        )

    def test_global_logging_config_is_confined_to_the_allowlist(self):
        found: set[str] = set()
        for path in _walk_py(_SRC):
            if _logging_calls(path, "basicConfig"):
                found.add(_rel(path))
        unexpected = sorted(found - ALLOWED_BASIC_CONFIG)
        self.assertEqual(
            unexpected, [],
            "module-scope `logging.basicConfig` configures the ROOT logger for "
            "every importer; add it to ALLOWED_BASIC_CONFIG only if that is "
            "genuinely intended here. New config: " + ", ".join(unexpected),
        )

    def test_allowlist_has_no_stale_entries(self):
        existing = {_rel(p) for p in _walk_py(_SRC)}
        missing = sorted(p for p in ALLOWED_BASIC_CONFIG if p not in existing)
        self.assertEqual(
            missing, [],
            "ALLOWED_BASIC_CONFIG names files that no longer exist -- drop them: "
            + ", ".join(missing),
        )


class TestLogHygieneHelper(unittest.TestCase):

    def setUp(self):
        self._prev_disable = logging.root.manager.disable
        logging.disable(logging.NOTSET)

    def tearDown(self):
        logging.disable(self._prev_disable)

    def test_helper_quiets_the_known_noisy_loggers_by_name(self):
        from CORE import log_hygiene
        self.assertTrue(log_hygiene.NOISY_LOGGERS,
                        "the helper must name at least one noisy logger")
        log_hygiene.quiet_noisy_loggers()
        for name in log_hygiene.NOISY_LOGGERS:
            logger = logging.getLogger(name)
            self.assertGreaterEqual(
                logger.level, logging.WARNING,
                f"{name} must stay quiet at INFO by name",
            )
            self.assertTrue(
                logger.isEnabledFor(logging.WARNING),
                f"{name} must STILL deliver WARNINGs -- that is the whole point",
            )

    def test_helper_never_disables_logging_globally(self):
        from CORE import log_hygiene
        log_hygiene.quiet_noisy_loggers()
        self.assertLess(
            logging.root.manager.disable, logging.WARNING,
            "the helper must not reintroduce a process-wide disable",
        )

    def test_helper_leaves_unrelated_loggers_alone(self):
        from CORE import log_hygiene
        other = logging.getLogger("CORE.WORMS.expansion.spine")
        before = other.level
        log_hygiene.quiet_noisy_loggers()
        self.assertEqual(other.level, before,
                         "silencing the registry must not touch other loggers")


if __name__ == "__main__":
    unittest.main(verbosity=2)
