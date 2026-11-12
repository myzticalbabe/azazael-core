"""Integration tests for generate_telemetry.py — RULE 0Z-F telemetry export."""
import sys
import os
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import List
import shutil

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

TEST_DIR = os.path.dirname(__file__)
SCRIPTS_DIR = os.path.join(TEST_DIR, '..', 'scripts')
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))


class TestGenerateTelemetry(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: self._cleanup(self.temp_dir))

    def _cleanup(self, path: Path):
        shutil.rmtree(path, ignore_errors=True)

    def test_generate_output_format(self):
        """telemetry.json contains required fields with correct types."""
        from generate_telemetry import main as gen_main

        output = self.temp_dir / "telemetry.json"
        # Save and restore argv
        saved = sys.argv
        sys.argv = ["generate_telemetry.py", "--output", str(output)]
        try:
            exit_code = gen_main()
            self.assertEqual(exit_code, 0)
            self.assertTrue(output.exists())
            data = json.loads(output.read_text(encoding="utf-8"))
            # Required fields
            self.assertIn("parallel_batches", data)
            self.assertIn("sequential_violations", data)
            self.assertIn("edit_count", data)
            self.assertIn("compliance_ratio", data)
            self.assertIn("total_spawns", data)
            self.assertIn("timestamp", data)
            self.assertIn("status", data)
            # Types
            self.assertIsInstance(data["parallel_batches"], int)
            self.assertIsInstance(data["sequential_violations"], int)
            self.assertIsInstance(data["edit_count"], int)
            self.assertIsInstance(data["compliance_ratio"], (int, float))
            self.assertIsInstance(data["status"], str)
        finally:
            sys.argv = saved

    def test_generate_custom_output_path(self):
        """--output flag writes to custom path."""
        from generate_telemetry import main as gen_main

        custom_dir = self.temp_dir / "nested" / "dir"
        custom_file = custom_dir / "custom_telemetry.json"

        saved = sys.argv
        sys.argv = ["generate_telemetry.py", "--output", str(custom_file)]
        try:
            exit_code = gen_main()
            self.assertEqual(exit_code, 0)
            self.assertTrue(custom_file.exists())
            data = json.loads(custom_file.read_text(encoding="utf-8"))
            self.assertIn("parallel_batches", data)
        finally:
            sys.argv = saved

    @unittest.mock.patch('CORE.compliance.pre_action_gate.get_pre_action_gate', side_effect=ImportError("Simulated missing gate"))
    def test_generate_runs_without_gate(self, mock_gate):
        """generate_telemetry handles missing PreActionGate gracefully via ImportError."""
        from generate_telemetry import main as gen_main

        output = self.temp_dir / "fallback.json"
        saved = sys.argv
        sys.argv = ["generate_telemetry.py", "--output", str(output)]
        try:
            exit_code = gen_main()
            self.assertEqual(exit_code, 0)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("status", data)
            self.assertEqual(data["status"], "gate_unavailable")
            self.assertIn("error", data)
        finally:
            sys.argv = saved

    def test_generate_output_is_valid_json(self):
        """Always produces valid JSON."""
        from generate_telemetry import main as gen_main

        output = self.temp_dir / "valid.json"
        saved = sys.argv
        sys.argv = ["generate_telemetry.py", "--output", str(output)]
        try:
            exit_code = gen_main()
            self.assertEqual(exit_code, 0)
            # Verify it's valid JSON by parsing
            json.loads(output.read_text(encoding="utf-8"))
        finally:
            sys.argv = saved


if __name__ == "__main__":
    unittest.main()
