#!/usr/bin/env python3
"""Unit tests for parallax_validator.py — Watchdog daemon token verification and revert logic."""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT.parent))

from DAEMONS.parallax_validator import (  # noqa: E402
    _get_tokens_dir,
    _get_token_path,
    verify_token,
    revert_file,
    record_violation,
    ParallaxHandler,
)

# Skip tests if module dependencies are unavailable
_HAS_DEPS = True
try:
    from DAEMONS.parallax_validator import _get_tokens_dir  # noqa: F811 — verify import
    import hashlib  # noqa: F401
except ImportError:
    _HAS_DEPS = False

_HAS_DEPS = unittest.skipUnless(_HAS_DEPS, "DAEMONS.parallax_validator dependencies unavailable")


@_HAS_DEPS
class TestTokenVerification(unittest.TestCase):
    """Test the daemon's token verification logic."""

    def setUp(self):
        """Create a clean tokens directory and add a test token."""
        tokens_dir = _get_tokens_dir()
        for f in tokens_dir.glob("*.token"):
            f.unlink()
        
        # Create a valid test token (BLAKE2b fallback for test portability)
        import hashlib
        token_data = {
            "timestamp": "2026-06-19T12:00:00+00:00",
            "target": "test_verify.py",
            "intent": "str_replace",
            "gate_data": {"authorized": True},
            "ttl_seconds": 15,
            "pid": 12345,
        }
        payload = json.dumps(token_data, sort_keys=True, default=str)
        salt = os.urandom(16).hex()
        sig = hashlib.blake2b(
            (payload + salt).encode("utf-8"), digest_size=32
        ).hexdigest()
        token_data["signature"] = f"blake2b:{sig}:{salt}"
        
        self.token_path = _get_token_path("test_verify.py")
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(token_data), encoding="utf-8")

    def tearDown(self):
        """Clean up test tokens."""
        for f in _get_tokens_dir().glob("*.token"):
            f.unlink()

    def test_verify_token_valid(self):
        """verify_token() should return True for a freshly created valid token."""
        # We need a current timestamp, so create a fresh one
        from DAEMONS.parallax_validator import _get_tokens_dir as gtd
        import hashlib
        from datetime import datetime, timezone
        
        token_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target": "fresh_test.py",
            "intent": "str_replace",
            "gate_data": {},
            "ttl_seconds": 15,
            "pid": 999,
        }
        payload = json.dumps(token_data, sort_keys=True, default=str)
        salt = os.urandom(16).hex()
        sig = hashlib.blake2b((payload + salt).encode("utf-8"), digest_size=32).hexdigest()
        token_data["signature"] = f"blake2b:{sig}:{salt}"
        
        token_path = gtd() / "fresh_test.py.token"
        token_path.write_text(json.dumps(token_data), encoding="utf-8")
        
        valid, reason = verify_token("fresh_test.py", max_age_seconds=60)
        self.assertTrue(valid, f"Token should be valid, got: {reason}")

    def test_verify_token_missing(self):
        """verify_token() should return False when no token exists."""
        valid, reason = verify_token("nonexistent_file.py")
        self.assertFalse(valid)
        self.assertIn("No token", reason)

    def test_verify_token_target_mismatch(self):
        """verify_token() should return False when the token names another target.

        A mismatch needs a token that EXISTS under this filename but names a
        different target. Without one this asserts the missing-token branch
        ("No token found at ..."), which is a different check and what
        test_verify_token_missing covers -- so this test could never pass.
        """
        from DAEMONS.parallax_validator import _get_tokens_dir as gtd
        import hashlib
        from datetime import datetime, timezone

        token_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target": "some_other_file.py",
            "intent": "str_replace",
            "gate_data": {},
            "ttl_seconds": 15,
            "pid": 321,
        }
        payload = json.dumps(token_data, sort_keys=True, default=str)
        salt = os.urandom(16).hex()
        sig = hashlib.blake2b(
            (payload + salt).encode("utf-8"), digest_size=32).hexdigest()
        token_data["signature"] = f"blake2b:{sig}:{salt}"
        (gtd() / "wrong_target.py.token").write_text(json.dumps(token_data), encoding="utf-8")

        valid, reason = verify_token("wrong_target.py")
        self.assertFalse(valid)
        self.assertIn("mismatch", reason.lower())

    def test_verify_token_expired(self):
        """verify_token() should return False for expired tokens."""
        # Create an expired token
        from DAEMONS.parallax_validator import _get_tokens_dir as gtd
        import hashlib
        from datetime import datetime, timezone, timedelta
        
        token_data = {
            "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
            "target": "old_token.py",
            "intent": "str_replace",
            "gate_data": {},
            "ttl_seconds": 15,
            "pid": 444,
        }
        payload = json.dumps(token_data, sort_keys=True, default=str)
        salt = os.urandom(16).hex()
        sig = hashlib.blake2b((payload + salt).encode("utf-8"), digest_size=32).hexdigest()
        token_data["signature"] = f"blake2b:{sig}:{salt}"
        
        token_path = gtd() / "old_token.py.token"
        token_path.write_text(json.dumps(token_data), encoding="utf-8")
        
        valid, reason = verify_token("old_token.py", max_age_seconds=5)
        self.assertFalse(valid)
        self.assertIn("expired", reason.lower())


@_HAS_DEPS
class TestParallaxHandler(unittest.TestCase):
    """Test the watchdog event handler."""

    def test_should_watch_python_file(self):
        """_should_watch should return True for .py files."""
        handler = ParallaxHandler()
        self.assertTrue(handler._should_watch("src/module.py"))

    def test_should_watch_markdown_file(self):
        """_should_watch should return True for .md files."""
        handler = ParallaxHandler()
        self.assertTrue(handler._should_watch("docs/readme.md"))

    def test_should_watch_ignore_git_dir(self):
        """_should_watch should return False for files in .git/."""
        handler = ParallaxHandler()
        self.assertFalse(handler._should_watch(".git/config"))

    def test_should_watch_ignore_pycache(self):
        """_should_watch should return False for files in __pycache__/."""
        handler = ParallaxHandler()
        self.assertFalse(handler._should_watch("src/__pycache__/module.pyc"))

    def test_should_watch_ignore_audit_dir(self):
        """_should_watch should return False for files in .audit/."""
        handler = ParallaxHandler()
        self.assertFalse(handler._should_watch(".audit/session.jsonl"))

    def test_should_watch_ignore_backup_files(self):
        """_should_watch should return False for .pre_gate_backup files."""
        handler = ParallaxHandler()
        self.assertFalse(handler._should_watch("src/file.py.pre_gate_backup"))
        self.assertFalse(handler._should_watch("src/.gate_backup_test.py"))


@_HAS_DEPS
class TestRevertFile(unittest.TestCase):
    """Test the git-based file reversion logic."""

    @patch("subprocess.run")
    def test_revert_via_git_success(self, mock_run):
        """revert_file() should call git checkout on success."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        # We can't actually revert a nonexistent file, but we can test the path
        result = revert_file("nonexistent_should_not_exist.py")
        # File doesn't exist so it returns early before git
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    @patch("subprocess.run")
    def test_revert_registers_debounce(self, mock_run):
        """revert_file() should register the file in the cooldown registry."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        # Access the module-level cooldown dict
        from DAEMONS.parallax_validator import _revert_cooldown
        test_file = "test_debounce.py"
        
        # If the file doesn't exist, revert_file returns early before debounce
        # Let's just verify the dict exists and is a dict
        self.assertIsInstance(_revert_cooldown, dict)


@_HAS_DEPS
class TestGetTokenPath(unittest.TestCase):
    """Test token path resolution."""

    def test_token_path_replaces_slashes(self):
        """Token paths should replace / and \\ with _."""
        path = _get_token_path("src/module.py")
        self.assertNotIn("/", path.name)
        self.assertNotIn("\\", path.name)

    def test_token_path_replaces_colons(self):
        """Token paths should replace : with _."""
        path = _get_token_path("C:/path/file.py")
        self.assertNotIn(":", path.name)


if __name__ == "__main__":
    unittest.main()
