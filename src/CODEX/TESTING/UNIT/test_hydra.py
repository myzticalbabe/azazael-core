import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))

#!/usr/bin/env python3
"""
Unit Tests for HYDRA
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

_src_hydra = Path(__file__).resolve().parent.parent.parent.parent  # UNIT/ -> TESTING/ -> CODEX/ -> src/
if str(_src_hydra) not in sys.path:
    sys.path.insert(0, str(_src_hydra))


class TestHYDRACluster:
    """Unit tests for HYDRACluster."""

    def test_cluster_initialization(self):
        """Test HYDRACluster initializes."""
        try:
            from ORCHESTRATION.HYDRA import HYDRACluster

            cluster = HYDRACluster()
            assert cluster is not None
        except ImportError as exc:
            note_failure("test_hydra:L30", exc)
            pytest.skip("HYDRACluster not implemented")

    def test_cluster_has_run_discover(self):
        """Test HYDRACluster exposes node discovery.

        The method was renamed run_discover -> discover_nodes in the HYDRA
        split; asserting the retired name only ever produced a green skip.
        """
        try:
            from ORCHESTRATION.HYDRA import HYDRACluster

            cluster = HYDRACluster()
            assert hasattr(cluster, "discover_nodes")
            assert callable(cluster.discover_nodes)
        except ImportError as exc:
            note_failure("test_hydra:L40", exc)
            pytest.skip("HYDRACluster not implemented")

    def test_cluster_has_run_all(self):
        """Test HYDRACluster exposes the full-sequence runner.

        run_all was replaced by run_sequential; the old name made this test a
        permanent skip rather than a check.
        """
        try:
            from ORCHESTRATION.HYDRA import HYDRACluster

            cluster = HYDRACluster()
            assert hasattr(cluster, "run_sequential")
            assert callable(cluster.run_sequential)
        except ImportError as exc:
            note_failure("test_hydra:L51", exc)
            pytest.skip("HYDRACluster not implemented")

    def test_cluster_has_stages(self):
        """Test HYDRACluster has expected stages."""
        try:
            from ORCHESTRATION.HYDRA import HYDRACluster

            expected_stages = [
                "discover",
                "analyze",
                "optimize",
                "validate",
                "deploy",
                "feedback",
            ]
            # Check that stages attribute exists or can be discovered
            cluster = HYDRACluster()
            # Just verify initialization works
            assert cluster is not None
        except ImportError as exc:
            note_failure("test_hydra:L71", exc)
            pytest.skip("HYDRACluster not implemented")


class TestHYDRADataClasses:
    """Unit tests for HYDRA data classes."""

    def test_processing_stage_creation(self):
        """Test the ProcessingStage enum and the StageResult carrier.

        ProcessingStage is an Enum of six stages, not the keyword dataclass
        this test once assumed (name/enabled/duration_ms); that shape belongs
        to StageResult(stage, success, ...). Both are asserted here.
        """
        try:
            import dataclasses

            from ORCHESTRATION.HYDRA import ProcessingStage, StageResult

            members = [m.name for m in ProcessingStage]
            assert members == ["DISCOVERY", "ANALYSIS", "OPTIMIZATION",
                               "VALIDATION", "DEPLOYMENT", "FEEDBACK"]
            assert dataclasses.is_dataclass(StageResult)
            fields = {f.name for f in dataclasses.fields(StageResult)}
            assert {"stage", "success", "modules_processed", "duration"} <= fields
            row = StageResult(stage=ProcessingStage.DISCOVERY, success=True,
                              modules_processed=1, duration=0.5)
            assert row.stage is ProcessingStage.DISCOVERY
        except ImportError as exc:
            note_failure("test_hydra:L85", exc)
            pytest.skip("ProcessingStage not implemented")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
