# coding: utf-8
"""
Unit tests for CORE/WORMS/expansion/nuclei.py - NucleiBridge worm.

All subprocess calls are mocked: no live WSL, no live scans.
"""

import json
import sys
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

# Ensure azazael/src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from CORE.WORMS.expansion.nuclei import NucleiBridge
from typing import List
import tempfile

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class _Proc:
    def __init__(self, returncode=0, stdout: str | bytes = "", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _finding_line(**kw):
    event = {
        "template-id": kw.get("template_id", "CVE-2024-1234"),
        "info": {"name": kw.get("name", "Test Vuln"), "severity": kw.get("severity", "high")},
        "host": kw.get("host", "http://example.com"),
        "matched-at": kw.get("matched", "2026-01-01T00:00:00Z"),
    }
    return json.dumps(event)


class TestAvailability(unittest.TestCase):
    @patch("CORE.WORMS.expansion.nuclei.subprocess.run")
    def test_available_when_present(self, mock_run):
        mock_run.side_effect = [
            _Proc(0, "kali-linux\nUbuntu-26.04\n".encode("utf-16")),
            _Proc(0, "/usr/bin/nuclei\n"),
        ]
        self.assertTrue(NucleiBridge.available())

    @patch("CORE.WORMS.expansion.nuclei.subprocess.run")
    def test_unavailable_without_distro(self, mock_run):
        mock_run.return_value = _Proc(0, "Ubuntu-26.04\n".encode("utf-16"))
        self.assertFalse(NucleiBridge.available())


class TestScanParsing(unittest.TestCase):
    @patch("CORE.WORMS.expansion.nuclei.subprocess.run")
    def test_findings_parsed(self, mock_run):
        mock_run.side_effect = lambda *a, **k: _Proc(
            0, _finding_line() + "\nnot-json-noise\n" + _finding_line(severity="critical") + "\n"
        )
        with unittest.mock.patch.object(NucleiBridge, "templates_version", return_value="v1"):
            result = NucleiBridge().scan(["http://example.com"], severity="high,critical")
        self.assertEqual(len(result["findings"]), 2)
        self.assertEqual(result["findings"][0]["template_id"], "CVE-2024-1234")
        self.assertEqual(result["findings"][1]["severity"], "critical")
        self.assertEqual(result["run"]["findings"], 2)

    def test_empty_targets_rejected(self):
        with self.assertRaises(ValueError):
            NucleiBridge().scan([])
        with self.assertRaises(ValueError):
            NucleiBridge().scan(["  "])


class TestFeedState(unittest.TestCase):
    def test_status_missing_file(self):

        worm = NucleiBridge(state_path=Path(tempfile.mkdtemp()) / "state.json")
        self.assertEqual(worm.templates_status(), {})

    @patch("CORE.WORMS.expansion.nuclei.subprocess.run")
    def test_update_templates_records_state(self, mock_run):

        mock_run.side_effect = [_Proc(0, ""), _Proc(0, "3.4.0\n")]
        path = Path(tempfile.mkdtemp()) / "state.json"
        result = NucleiBridge(state_path=path).update_templates()
        self.assertTrue(result["ok"])
        self.assertEqual(result["templates_version"], "3.4.0")
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["templates_version"], "3.4.0")
        self.assertIn("updated_at", saved)


if __name__ == "__main__":
    unittest.main(verbosity=2)
