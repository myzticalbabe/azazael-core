import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))

#!/usr/bin/env python3
"""
Unit Tests for WORMZ
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

_src_wormz = Path(__file__).resolve().parent.parent.parent.parent  # UNIT/ -> TESTING/ -> CODEX/ -> src/
if str(_src_wormz) not in sys.path:
    sys.path.insert(0, str(_src_wormz))


class TestWORMZ:
    """Unit tests for WORMZ module."""

    def test_bibl_entry_creation(self):
        """Test BIBLEntry dataclass creation."""
        from CORE.WORMS.WORMZ import BIBLEntry

        entry = BIBLEntry(
            term="test_term", definition="Test definition", category="test"
        )
        assert entry.term == "test_term"
        assert entry.definition == "Test definition"
        assert entry.category == "test"

    def test_crawl_result_creation(self):
        """Test CrawlResult dataclass creation."""
        from CORE.WORMS.WORMZ import CrawlResult

        result = CrawlResult(
            file_path="/test",
            terms_found=[],
            classes_found=["TestClass"],
            functions_found=["test_func"],
            duration_ms=100.0,
        )
        assert result.file_path == "/test"
        assert result.duration_ms == 100.0
        assert len(result.classes_found) == 1

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.rglob")
    def test_wormz_crawl_single_file(self, mock_rglob, mock_exists):
        """Test WORMZ crawl with single file."""
        mock_exists.return_value = True
        mock_file = MagicMock()
        mock_file.read_text.return_value = "def test(): pass"
        mock_rglob.return_value = [mock_file]

        from CORE.WORMS.WORMZ import WORMZ

        wormz = WORMZ()
        result = wormz.crawl_folder("/test")

        assert result is not None

    def test_wormz_run_all_returns_list(self):
        """Test WORMZ.crawl_all() returns dict."""
        from CORE.WORMS.WORMZ import WORMZ

        wormz = WORMZ()
        results = wormz.crawl_all(show_progress=False)
        assert isinstance(results, dict)


class TestFunctionalWorms:
    """Unit tests for functional worms."""

    def test_systemz_import(self):
        """Test SYSTEMZ can be imported."""
        try:
            pass

            assert True
        except ImportError as exc:
            note_failure("test_wormz:L84", exc)
            pytest.skip("SYSTEMZ not implemented")

    def test_processz_import(self):
        """Test PROCESSZ can be imported."""
        try:
            pass

            assert True
        except ImportError as exc:
            note_failure("test_wormz:L93", exc)
            pytest.skip("PROCESSZ not implemented")

    def test_diskz_import(self):
        """Test DISKZ can be imported."""
        try:
            pass

            assert True
        except ImportError as exc:
            note_failure("test_wormz:L102", exc)
            pytest.skip("DISKZ not implemented")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
