#!/usr/bin/env python3
# coding: utf-8
"""The silent-failure census must classify handlers correctly, both directions.

Why this file exists. `tools/silent_failure_audit.py` reports where a failure
leaves no signal. It is only worth having if its classifier is right in both
directions: it must FLAG a swallow-to-pass handler, and it must NOT flag a
handler that logs, re-raises, or returns (a census that cries wolf gets
switched off). These tests drive it against synthetic source, and pin the one
thing `--check` fails on: a BARE `except:`.
"""
from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools import silent_failure_audit as sfa  # noqa: E402


def _scan_source(src: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as d:
        pkg = Path(d) / "src"
        pkg.mkdir()
        (pkg / "sample.py").write_text(textwrap.dedent(src), encoding="utf-8")
        # Point the scanner at the temp tree by running it in that root.
        old = sfa.ROOT
        sfa.ROOT = Path(d)
        try:
            return sfa.scan(roots=("src",))["findings"]
        finally:
            sfa.ROOT = old


class TestClassification(unittest.TestCase):

    def test_flags_a_pass_only_handler(self):
        found = _scan_source("""
            def f():
                try:
                    g()
                except Exception:
                    pass
        """)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "Exception")

    def test_flags_a_bare_except(self):
        found = _scan_source("""
            def f():
                try:
                    g()
                except:
                    pass
        """)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "bare")

    def test_does_not_flag_a_logging_handler(self):
        found = _scan_source("""
            def f():
                try:
                    g()
                except Exception as e:
                    log.warning("failed: %s", e)
        """)
        self.assertEqual(found, [], "a handler that logs is not silent")

    def test_does_not_flag_a_re_raise_or_return(self):
        found = _scan_source("""
            def f():
                try:
                    g()
                except ValueError:
                    raise
            def h():
                try:
                    g()
                except KeyError:
                    return None
        """)
        self.assertEqual(found, [])

    def test_names_when_a_write_like_call_is_swallowed(self):
        found = _scan_source("""
            def f():
                try:
                    handle.write(payload)
                except Exception:
                    pass
        """)
        self.assertEqual(len(found), 1)
        self.assertIn("write", found[0]["swallows_write"])

    def test_a_plain_pass_handler_without_a_write_is_not_write_flagged(self):
        found = _scan_source("""
            def f():
                try:
                    g()
                except Exception:
                    pass
        """)
        self.assertEqual(found[0]["swallows_write"], [])


class TestCheckGate(unittest.TestCase):

    def test_check_fails_only_on_a_bare_except(self):
        with tempfile.TemporaryDirectory() as d:
            pkg = Path(d) / "src"
            pkg.mkdir()
            (pkg / "sample.py").write_text(
                "def f():\n    try:\n        g()\n    except:\n        pass\n",
                encoding="utf-8")
            old = sfa.ROOT
            sfa.ROOT = Path(d)
            try:
                self.assertEqual(sfa.main(["--check"]), 1)
                # Rewrites to a typed handler: the gate opens.
                (pkg / "sample.py").write_text(
                    "def f():\n    try:\n        g()\n    except Exception:\n"
                    "        pass\n", encoding="utf-8")
                self.assertEqual(sfa.main(["--check"]), 0)
            finally:
                sfa.ROOT = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
