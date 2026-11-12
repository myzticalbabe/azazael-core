# coding: utf-8
"""
Unit tests for ML.trace_consolidator - offline trace consolidation.
"""

import json
import sys
import unittest
from pathlib import Path

# Ensure azazael/src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ML.trace_consolidator import consolidate, dream, render_markdown, summarize_snapshot
from typing import List
import tempfile

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _write_snapshot(directory: Path, sid: str, title: str, parts: list[dict]) -> Path:
    lines = [{"type": "session", "session": {"id": sid, "title": title}}]
    lines += parts
    path = directory / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    return path


def _tool(tool: str, status: str = "completed") -> dict:
    return {"type": "part", "kind": "tool", "tool": tool, "status": status}


class TestSummarize(unittest.TestCase):
    def test_counts(self):

        with tempfile.TemporaryDirectory() as tmp:
            path = _write_snapshot(
                Path(tmp), "s1", "T1",
                [
                    {"type": "message", "role": "user"},
                    _tool("read"), _tool("bash"), _tool("bash", "error"),
                    {"type": "part", "kind": "compaction"},
                ],
            )
            sig = summarize_snapshot(path)
            self.assertEqual(sig.events, 3)
            self.assertEqual(sig.errors, 1)
            self.assertEqual(sig.compactions, 1)
            self.assertEqual(sig.user_turns, 1)
            self.assertEqual(sig.tools["bash"], 2)

    def test_bad_lines_skipped(self):

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s9.jsonl"
            path.write_text(
                '{"type": "session", "session": {"id": "s9"}}\nnot json\n{"type": "part"}\n',
                encoding="utf-8",
            )
            sig = summarize_snapshot(path)
            self.assertEqual(sig.session_id, "s9")


class TestConsolidate(unittest.TestCase):
    def test_failure_split_and_render(self):

        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "snaps"
            reps = Path(tmp) / "reps"
            snap.mkdir()
            reps.mkdir()
            _write_snapshot(snap, "bad", "Bad run", [_tool("bash", "error"), _tool("bash", "error")])
            _write_snapshot(snap, "good", "Good run", [_tool("read")])
            (reps / "bad.json").write_text(
                json.dumps({"dimensions": [{"name": "scope", "verdict": "problem"}]}),
                encoding="utf-8",
            )
            data = consolidate(snap, reps)
            self.assertEqual(data["n_sessions"], 2)
            self.assertEqual(data["tool_total"], {"bash": 2, "read": 1})
            md = render_markdown(data)
            self.assertIn("1 failure, 1 clean", md)
            self.assertIn("Bad run", md)
            self.assertIn("problem dimension: scope", md)

    def test_dream_writes_file(self):

        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "snaps"
            snap.mkdir()
            _write_snapshot(snap, "s1", "T", [_tool("read")])
            out = dream(snap, None, Path(tmp) / "out" / "PATTERNS.md")
            self.assertTrue(out.exists())
            self.assertIn("Consolidated session patterns", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
