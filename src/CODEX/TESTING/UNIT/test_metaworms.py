import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))

#!/usr/bin/env python3
"""
Unit Tests for METAWORMS
"""

import pytest
from pathlib import Path
import sys
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

_src_meta = Path(__file__).resolve().parent.parent.parent.parent  # UNIT/ -> TESTING/ -> CODEX/ -> src/
if str(_src_meta) not in sys.path:
    sys.path.insert(0, str(_src_meta))


class TestMetaWormResult:
    """Unit tests for MLEntry dataclass."""

    def test_worm_result_creation(self):
        """Test MLEntry can be created."""
        from CORE.WORMS.WORMZ_ML import MLEntry

        entry = MLEntry(
            term="test_term",
            category="test",
            confidence=0.95,
            occurrences=5,
            files=["file1.py", "file2.py"],
            last_found="2026-02-12",
            pattern_score=0.8,
        )
        assert entry.term == "test_term"
        assert entry.category == "test"
        assert entry.confidence == 0.95


class TestMetaWormConfig:
    """Unit tests for WORMZ_MLConfig."""

    def test_config_defaults(self):
        """Test WORMZ_MLConfig has expected defaults."""
        from CORE.WORMS.WORMZ_ML import WORMZ_MLConfig

        config = WORMZ_MLConfig()
        assert any(
            "ORCHESTRATION" in str(folder)
            for folder in config.TARGET_FOLDERS
        )
        assert "__pycache__" in config.SKIP_PATTERNS


class TestHYDRAMETA:
    """Unit tests for HYDRAMETA genetic algorithm."""

    def test_hydrometa_initialization(self):
        """Test HYDRAMETA initializes with defaults."""
        try:
            from HYDRAMETA import HYDRAMETA

            optimizer = HYDRAMETA()
            # The GA knobs are per-call (evolve(num_generations=...)), not
            # constructor attributes: population_size/generations were never
            # real attributes, so the old assertions could only ever skip.
            ga = optimizer.optimizer
            assert type(ga).__name__ == "HYDRAOptimizer"
            assert callable(ga.initialize_population)
            assert callable(ga.evolve)
            assert callable(ga.run_trial)
        except ImportError as exc:
            note_failure("test_metaworms:L67", exc)
            pytest.skip("HYDRAMETA not importable")

    def test_hydrometa_has_optimize_method(self):
        """Test HYDRAMETA has optimize method."""
        try:
            from HYDRAMETA import HYDRAMETA

            optimizer = HYDRAMETA()
            # The optimization entry point is run_optimized (and run_trials),
            # not optimize(); asserting the retired name guaranteed a skip.
            assert hasattr(optimizer, "run_optimized")
            assert callable(optimizer.run_optimized)
            assert callable(optimizer.run_trials)
        except ImportError as exc:
            note_failure("test_metaworms:L78", exc)
            pytest.skip("HYDRAMETA not importable")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
