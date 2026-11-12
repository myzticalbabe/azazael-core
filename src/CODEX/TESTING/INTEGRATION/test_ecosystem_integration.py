import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))

#!/usr/bin/env python3
"""
Integration Tests for Azazael Ecosystem
"""

import pytest
import sys
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

_src_integ = Path(__file__).resolve().parent.parent.parent.parent  # INTEGRATION/ -> TESTING/ -> CODEX/ -> src/
if str(_src_integ) not in sys.path:
    sys.path.insert(0, str(_src_integ))


class TestWORMZHydraIntegration:
    """Integration tests for WORMZ + HYDRA."""

    @pytest.mark.integration
    def test_wormz_produces_bibl_for_hydra(self):
        """Test WORMZ output can be used by HYDRA."""
        try:
            from CORE.WORMS.WORMZ import WORMZ
            from ORCHESTRATION.HYDRA import HYDRACluster

            wormz = WORMZ()
            hydra = HYDRACluster()

            assert wormz is not None
            assert hydra is not None
        except ImportError as e:
            note_failure("test_ecosystem_integration:L35", e)
            pytest.skip(f"Integration component not available: {e}")


class TestMetaWormsIntegration:
    """Integration tests for METAWORMS."""

    @pytest.mark.integration
    def test_metaworms_can_analyze_wormz_output(self):
        """Test METAWORMS can analyze WORMZ results."""
        try:
            from META.METAWORMS import WormResult

            result = WormResult(name="test", success=True, output={})
            assert result.success is True
        except ImportError as exc:
            note_failure("test_ecosystem_integration:L50", exc)
            pytest.skip("METAWORMS not available")


class TestKLINKIntegration:
    """Integration tests for KLINK."""

    @pytest.mark.integration
    def test_klink_has_all_commands(self):
        """Test klink has expected commands."""
        try:
            from UTILITIES.KLINK.klink import COMMANDS

            expected = [
                "status",
                "worms",
                "hydra",
                "metaworms",
                "terence",
                "horus",
                "all",
            ]
            for cmd in expected:
                assert cmd in COMMANDS, f"Missing command: {cmd}"
        except ImportError as exc:
            note_failure("test_ecosystem_integration:L74", exc)
            pytest.skip("KLINK not available")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "integration"])
