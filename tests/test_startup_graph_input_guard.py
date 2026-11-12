#!/usr/bin/env python3
# coding: utf-8
"""Session startup must check graph inputs BEFORE building the graph.

Why this file exists. `scripts/check_tooling.py` is the mandatory first step of
a session. The 2026-09-21 lesson was that a single mislabelled file (an HTML
block page named `.pdf`) aborts a ~40-minute `graphify update .`, and the only
symptom is a stalled, un-explainable refresh. So the mislabel audit now runs at
startup, where it is cheap. These tests pin the two properties that make it
worth wiring in: it delegates to the audit tool, and it can NEVER hard-fail the
session -- a guard that can itself stop the session is worse than no guard.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import sys
import types
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_CHECK_TOOLING = os.path.join(_ROOT, "scripts", "check_tooling.py")
_spec = importlib.util.spec_from_file_location("_check_tooling", _CHECK_TOOLING)
check_tooling = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_tooling)  # type: ignore[union-attr]


class TestStartupGraphInputGuard(unittest.TestCase):

    def test_guard_reports_a_result_without_raising(self):
        res = check_tooling.check_graph_input_hygiene()
        self.assertIn("available", res)
        self.assertIn("ok", res)
        self.assertIn("note", res)

    def test_guard_delegates_to_the_audit_in_check_mode(self):
        """`--check` is the regression semantics: only NEW mislabels fail."""
        seen = {}

        class _Done:
            returncode = 0
            stdout = "checked 500 parseable-binary file(s)\nOK: no new mislabelled files\n"
            stderr = ""

        def _run(cmd, **kw):
            seen["cmd"] = cmd
            return _Done()

        with mock.patch.object(check_tooling.subprocess, "run", _run):
            res = check_tooling.check_graph_input_hygiene()
        self.assertTrue(res["ok"])
        self.assertIn("--check", seen["cmd"])
        self.assertTrue(any("audit_parseable_files" in str(a) for a in seen["cmd"]))

    def test_guard_requests_the_deep_pdf_check(self):
        """Magic bytes alone miss a TRUNCATED PDF -- only a real parse rejects it.

        A truncated PDF still starts with `%PDF`, so the shallow signature check
        passes it, yet pypdf (the parser graphify picks for `.pdf`) aborts on it
        -- exactly the 2026-09-21 failure. The startup guard must therefore ask
        for `--deep`, not just `--check`.
        """
        seen = {}

        class _Done:
            returncode = 0
            stdout = "OK: no new mislabelled files\n"
            stderr = ""

        with mock.patch.object(check_tooling.subprocess, "run",
                               lambda cmd, **kw: (seen.__setitem__("cmd", cmd), _Done())[1]):
            check_tooling.check_graph_input_hygiene()
        self.assertIn("--deep", seen["cmd"],
                      "startup must deep-parse PDFs, not just check magic bytes")

    def test_guard_surfaces_the_deep_pdf_count(self):
        """The note must say how many PDFs the deep pass read.

        A deep scan over a PDF-heavy tree is legitimately slow; reporting only
        pass/fail would make a slow startup look like a stalled one.
        """
        class _Done:
            returncode = 0
            stdout = ("checked 500 parseable-binary file(s)\n"
                      "deep-parsed pdfs: 3\n"
                      "OK: no new mislabelled files\n")
            stderr = ""

        with mock.patch.object(check_tooling.subprocess, "run",
                               lambda *a, **k: _Done()):
            res = check_tooling.check_graph_input_hygiene()
        self.assertIn("deep-parsed pdfs: 3", res["note"])
        self.assertTrue(res["ok"])

    def test_missing_pdf_engine_is_degraded_but_not_fatal(self):
        """A missing pypdf must be visible, not a silent OK.

        The deep pass is the part that catches truncated PDFs; when its engine
        is absent the check did nothing, yet the old code reported success. It
        is reported as degraded -- and must NOT fail startup, since it rests on
        an optional third-party dependency.
        """
        class _Degraded:
            returncode = 0
            stdout = ("checked 500 parseable-binary file(s)\n"
                      "deep-unavailable: pypdf is not importable\n"
                      "deep-parsed pdfs: 0\n"
                      "OK: no new mislabelled files\n")
            stderr = ""

        with mock.patch.object(check_tooling.subprocess, "run",
                               lambda *a, **k: _Degraded()):
            res = check_tooling.check_graph_input_hygiene()
        self.assertTrue(res["ok"], "a missing optional dep must not fail startup")
        self.assertTrue(res["degraded"], "but it must be reported as degraded")
        self.assertIn("deep-unavailable", res["note"])

    def test_a_new_mislabel_is_reported_as_not_ok(self):
        class _Fail:
            returncode = 1
            stdout = ("checked 500 file(s)\nNEW mislabels: 1\n"
                      "  archive/x.pdf  (.pdf -> text/<!doctype html)\n")
            stderr = ""

        with mock.patch.object(check_tooling.subprocess, "run",
                               lambda *a, **k: _Fail()):
            res = check_tooling.check_graph_input_hygiene()
        self.assertFalse(res["ok"])

    def test_guard_never_hard_fails_the_session(self):
        """An unavailable/failing audit must not stop startup -- it is insurance."""
        with mock.patch.object(check_tooling.subprocess, "run",
                               side_effect=OSError("no interpreter")):
            res = check_tooling.check_graph_input_hygiene()
        self.assertTrue(res["ok"], "a broken guard must fail SAFE, not fail startup")
        self.assertFalse(res["available"])


class TestStartupSequenceOrder(unittest.TestCase):
    """Section [8] must run DURING startup, before the graph is ever built.

    The guard is worthless if it lands after `graphify update .` has already
    aborted -- the whole point is to catch the bad input before the ~40-minute
    run starts. Nothing in the startup path builds the graph itself (that is
    the later maintenance chain), so the property worth pinning is that the
    check is invoked by `main()` at all, and that it fires before the session's
    later steps. If a future refactor moves the check out of startup (e.g. into
    the refresh chain, i.e. after the graph), these tests fail.
    """

    def _run_main_with_order(self) -> list:
        order: list = []

        class _Store:
            def integrity_check(self):
                return {"ok": True}

            def query_memories(self, **kw):
                return []

        fake_mem = types.ModuleType("CORE.local_memory")
        fake_mem.get_memory_store = lambda: _Store()

        class _Proc:
            returncode = 0
            stdout = "3.14.0\n"
            stderr = ""

        def _hygiene(*a, **k):
            order.append("hygiene")
            return {"available": True, "ok": True, "note": "ok"}

        def _record(*a, **k):
            order.append("record")
            return True

        with mock.patch.dict(sys.modules, {"CORE.local_memory": fake_mem}), \
                mock.patch.object(check_tooling, "check_cli",
                                  lambda *a, **k: {"name": "x", "available": True,
                                                   "version": "1"}), \
                mock.patch.object(check_tooling, "check_pip_package",
                                  lambda *a, **k: {"name": "x", "available": True}), \
                mock.patch.object(check_tooling, "check_npm_package",
                                  lambda *a, **k: {"name": "x", "available": True}), \
                mock.patch.object(check_tooling.subprocess, "run",
                                  lambda *a, **k: _Proc()), \
                mock.patch.object(check_tooling, "check_graph_input_hygiene", _hygiene), \
                mock.patch.object(check_tooling, "_record_with_timeout", _record), \
                contextlib.redirect_stdout(io.StringIO()):
            check_tooling.main()
        return order

    def test_main_invokes_the_graph_input_guard(self):
        order = self._run_main_with_order()
        self.assertIn("hygiene", order,
                      "startup must run the graph-input guard, not just define it")

    def test_guard_runs_before_the_later_session_steps(self):
        order = self._run_main_with_order()
        self.assertEqual(order, ["hygiene", "record"],
                         "the graph-input check must precede session recording; "
                         "anything that builds a graph comes after both")

    def test_guard_call_site_lives_inside_main(self):
        """Cheap structural backstop: exactly one call site, and it is in main."""
        src = open(_CHECK_TOOLING, encoding="utf-8").read()
        self.assertEqual(src.count("check_graph_input_hygiene()"), 1,
                         "the guard should have exactly one call site (in main)")
        main_body = src[src.index("def main()"):]
        self.assertRegex(main_body, re.compile(r"check_graph_input_hygiene\(\)"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
