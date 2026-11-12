"""Regression test: verify_head_hash normalizes Windows separator paths.

The anchor ledger stores `audit_file` paths that may use Windows backslashes
(``.audit\\session_...jsonl``) while callers may query with forward slashes
(``.audit/session_...jsonl``) — or vice versa. verify_head_hash() must treat
them as identical via os.path.normpath() so Layer 14 (truncation defense)
passes regardless of the separator in either side.

Works standalone (python tests/test_verify_head_hash_normalization.py) and
under pytest.
"""

import os
import sys
import tempfile
import json
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import CORE.audit_trail as afr  # noqa: E402


def _genesis_entry(session_file: str) -> dict:
    """Build a minimal session_start genesis entry with a valid hash."""
    header = {
        "uuid": "00000000-0000-7001-0000-0000000000aa",
        "ts": "2026-08-29T00:00:00",
        "type": "session_start",
        "audit_trail_version": "3.0.0",
        "session_file": session_file,
        "session_uuid": "aa",
        "prev_hash": "0" * 64,
    }
    header["hash"] = afr._compute_hash(header, header["prev_hash"])
    return header


def _build_session_file(dir_path: Path, name: str) -> Path:
    """Create a 1-entry session file; return its path using backslashes."""
    fp = dir_path / name
    session_file_str = fp.as_posix()
    entry = _genesis_entry(session_file_str)
    with fp.open("w", encoding="utf-8") as fh:
        fh.write(afr._json_dumps(entry) + "\n")
    return fp


class TestVerifyHeadHashPathNormalization(unittest.TestCase):
    def test_anchor_backslash_query_forwardslash(self):
        """Anchor recorded with backslashes; query with forward slashes."""
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            session = _build_session_file(tdir, "session_20260829_000000_aa.jsonl")
            head = afr.get_head_hash(str(session))
            assert head

            # Anchor stores a BACKSLASH path (as the ledger historically did).
            anchor = {
                "ts": "2026-08-29T00:00:00",
                "audit_file": session.as_posix().replace("/", os.sep),  # backslashes on nt
                "head_hash": head,
                "entry_count": 1,
            }
            anchors_file = tdir / "anchors_back.jsonl"
            anchors_file.write_text(afr._json_dumps(anchor) + "\n", encoding="utf-8")

            # Point module at our temp anchors file, query with FORWARD slashes.
            old = afr.ANCHORS_FILE
            try:
                afr.ANCHORS_FILE = anchors_file
                valid, stored, current = afr.verify_head_hash(session.as_posix())
                assert valid, (stored, current)
                assert stored == head == current
            finally:
                afr.ANCHORS_FILE = old

    def test_anchor_forwardslash_query_backslash(self):
        """Anchor recorded with forward slashes; query with backslashes."""
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            session = _build_session_file(tdir, "session_20260829_000001_bb.jsonl")
            head = afr.get_head_hash(str(session))
            assert head

            anchor = {
                "ts": "2026-08-29T00:00:00",
                "audit_file": session.as_posix(),  # forward slashes
                "head_hash": head,
                "entry_count": 1,
            }
            anchors_file = tdir / "anchors_fwd.jsonl"
            anchors_file.write_text(afr._json_dumps(anchor) + "\n", encoding="utf-8")

            old = afr.ANCHORS_FILE
            try:
                afr.ANCHORS_FILE = anchors_file
                # Query with os-native (backslash) separators.
                native = os.path.normpath(session)
                valid, stored, current = afr.verify_head_hash(native)
                assert valid, (stored, current)
                assert stored == head == current
            finally:
                afr.ANCHORS_FILE = old

    def test_unmatched_path_returns_false(self):
        """A path that normalizes differently must NOT match the anchor."""
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            session = _build_session_file(tdir, "session_20260829_000002_cc.jsonl")
            head = afr.get_head_hash(str(session))
            anchor = {
                "ts": "2026-08-29T00:00:00",
                "audit_file": session.as_posix(),  # anchors THIS file
                "head_hash": head,
                "entry_count": 1,
            }
            anchors_file = tdir / "anchors_match.jsonl"
            anchors_file.write_text(afr._json_dumps(anchor) + "\n", encoding="utf-8")

            old = afr.ANCHORS_FILE
            try:
                afr.ANCHORS_FILE = anchors_file
                # Different file entirely -> no matching anchor -> invalid.
                other = _build_session_file(tdir, "session_other_de.jsonl")
                valid, stored, _cur = afr.verify_head_hash(other.as_posix())
                assert stored == ""
                assert not valid
            finally:
                afr.ANCHORS_FILE = old


if __name__ == "__main__":
    unittest.main()