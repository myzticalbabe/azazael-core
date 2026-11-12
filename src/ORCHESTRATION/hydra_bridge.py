# coding: utf-8
"""
HYDRA Pipeline Bridge — End-to-End Wiring for the 6-Stage ML Cluster

Connects HYDRA's processing stages to InferenceBridge (LLM analysis),
PAMS recording, and HORUS output formatting. Completes the full
Discovery→Analysis→Optimization→Validation→Deployment→Feedback loop
with LLM-powered insights at the FEEDBACK stage.

Usage:
    from hydra_bridge import HydraBridge
    bridge = HydraBridge()
    results = bridge.run_full_pipeline()

Architecture:
    HYDRA (6 stages) ←→ InferenceBridge (LLM)
                    ←→ PamsRecorder (memory)
                    ←→ HORUS (output formatting)
"""

import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from dataclasses import dataclass, field

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).
#: Declared BEFORE the root resolver below, because that resolver's ImportError
#: handler records through it: a recorder bound after its first use raises NameError
#: from INSIDE the handler, turning a caught import failure into an uncaught one.

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: Canonical root resolver, shared with HORUS / HYDRA / BIBL. The old fallback was
#: `Path.home() / "azazael"` -- a DIFFERENT, STALE checkout from this repo
#: (C:/Azazael/azazael) -- so with AZAZAEL_ROOT unset this module pushed the wrong
#: tree's ORCHESTRATION / META / UTILITIES onto sys.path.

try:
    from CORE.paths import get_project_root_safe as _get_project_root_safe
except ImportError as exc:                               # src/ not on sys.path
    note_failure("hydra_bridge:core-paths-import", exc)
    _get_project_root_safe = None

#: This file is src/ORCHESTRATION/hydra_bridge.py, so parents[2] is the repo root.
_ROOT_FALLBACK = Path(__file__).resolve().parents[2]

_AZAZAEL_ROOT = os.environ.get(
    "AZAZAEL_ROOT",
    os.environ.get(
        "Azazael_ROOT",
        str(_get_project_root_safe() if _get_project_root_safe is not None
            else _ROOT_FALLBACK),
    ),
)
_ORCH_DIR = os.path.join(_AZAZAEL_ROOT, "src", "ORCHESTRATION")
_META_DIR = os.path.join(_AZAZAEL_ROOT, "src", "META")
_UTIL_DIR = os.path.join(_AZAZAEL_ROOT, "src", "UTILITIES")

sys.path.insert(0, _ORCH_DIR) if _ORCH_DIR not in sys.path else None
sys.path.insert(0, _META_DIR) if _META_DIR not in sys.path else None
sys.path.insert(0, _UTIL_DIR) if _UTIL_DIR not in sys.path else None

# Import core components
from ORCHESTRATION.HYDRA import HYDRACluster, ClusterConfig, ProcessingStage, StageResult
import argparse

# PAMS recording
try:
    from CORE.PamsRecorder import get_recorder
    PAMS_REC = get_recorder()
except ImportError as exc:
    note_failure("hydra_bridge:L46", exc)
    PAMS_REC = None

# InferenceBridge (may fail if llama-server not running)
try:
    from InferenceBridge import InferenceBridge
    BRIDGE_AVAILABLE = True
except ImportError as exc:
    note_failure("hydra_bridge:L53", exc)
    BRIDGE_AVAILABLE = False


@dataclass
class PipelineReport:
    """Complete pipeline execution report."""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    stages_completed: int = 0
    stages: list[dict[str, Any]] = field(default_factory=list)
    total_duration: float = 0.0
    llm_insights: (str | None) = None
    recommendations: list[str] = field(default_factory=list)
    success: bool = False


class HydraBridge:
    """
    Wraps HYDRACluster with InferenceBridge + PAMS + HORUS integration.

    Wires the full 6-stage pipeline end-to-end with LLM-powered
    feedback analysis and memory recording of all stages.
    """

    VERSION = "1.0.0"

    def __init__(self):
        self.cluster = HYDRACluster(ClusterConfig())
        self.bridge = None
        if BRIDGE_AVAILABLE:
            try:
                self.bridge = InferenceBridge()
            except Exception as exc:
                note_failure("hydra_bridge:L85", exc)
        self._recorder = PAMS_REC

    def run_full_pipeline(self, use_llm: bool = True) -> PipelineReport:
        """
        Run the complete HYDRA pipeline with LLM feedback analysis.

        Args:
            use_llm: Whether to use InferenceBridge for FEEDBACK analysis

        Returns:
            PipelineReport with full execution details
        """
        report = PipelineReport()
        start = time.time()

        print("=" * 70)
        print(f"  HYDRA Pipeline Bridge v{self.VERSION}")
        print(f"  LLM available: {self.bridge is not None and self.bridge.llama.is_available() if self.bridge else False}")
        print("=" * 70)

        stages = [
            ProcessingStage.DISCOVERY,
            ProcessingStage.ANALYSIS,
            ProcessingStage.OPTIMIZATION,
            ProcessingStage.VALIDATION,
            ProcessingStage.DEPLOYMENT,
            ProcessingStage.FEEDBACK,
        ]

        for stage in stages:
            stage_start = time.time()
            print(f"\n  [{stage.value}] Running...")

            try:
                result = self.cluster.run_stage(stage)
                stage_duration = time.time() - stage_start

                stage_info = {
                    "stage": stage.value,
                    "success": result.success,
                    "modules_processed": result.modules_processed,
                    "duration": round(stage_duration, 3),
                    "errors": result.errors,
                    "warnings": result.warnings,
                }

                # LLM-powered analysis for FEEDBACK stage
                if stage == ProcessingStage.FEEDBACK and use_llm and self.bridge:
                    stage_info["llm_analysis"] = self._run_feedback_analysis(result)

                report.stages.append(stage_info)

                status = "OK" if result.success else "FAIL"
                print(f"  [{status}] {stage.value}: {result.modules_processed} modules in {stage_duration:.2f}s")

                # Record to PAMS
                self._record_stage(stage.value, stage_info)

            except Exception as e:
                report.stages.append({
                    "stage": stage.value,
                    "success": False,
                    "error": str(e),
                })
                print(f"  [FAIL] {stage.value}: {e}")
                self._record_stage(stage.value, {"error": str(e)}, severity="error")

        report.stages_completed = sum(1 for s in report.stages if s.get("success"))
        report.total_duration = time.time() - start
        report.success = report.stages_completed >= 4  # At least 4/6 stages succeed

        # Generate recommendations
        report.recommendations = self._generate_recommendations(report)

        # Record pipeline completion
        self._record_event("hydra_pipeline_complete", {
            "stages_completed": report.stages_completed,
            "duration": report.total_duration,
            "success": report.success,
        })

        # Print summary
        self._print_summary(report)

        return report

    def _run_feedback_analysis(self, result: StageResult) -> (str | None):
        """Run LLM-powered analysis of pipeline feedback."""
        if not self.bridge or not self.bridge.llama.is_available():
            return None

        # Collect pipeline history for analysis
        stage_history = []
        for s in self.cluster.stage_history[-5:]:
            stage_history.append(f"{s.stage.value}: {'OK' if s.success else 'FAIL'} ({s.modules_processed} modules, {s.duration:.2f}s)")

        prompt = f"""Analyze this HYDRA pipeline execution:

Nodes: {len(self.cluster.nodes)} total, {len([n for n in self.cluster.nodes.values() if n.enabled])} enabled

Stage History:
{chr(10).join(stage_history)}

What went well? What can be improved? Respond concisely (2-3 sentences)."""

        try:
            resp = self.bridge.llama.chat([
                {"role": "system", "content": "You are a system analysis AI. Be concise."},
                {"role": "user", "content": prompt},
            ])

            if resp.success:
                return resp.response
        except Exception as exc:
            note_failure("hydra_bridge:L200", exc)

        return None

    def _generate_recommendations(self, report: PipelineReport) -> list[str]:
        """Generate actionable recommendations from pipeline results."""
        recs = []

        failed_stages = [s for s in report.stages if not s.get("success")]
        if failed_stages:
            recs.append(f"Retry failed stage(s): {', '.join(s['stage'] for s in failed_stages)}")

        # Check node health
        enabled = len([n for n in self.cluster.nodes.values() if n.enabled])
        total = len(self.cluster.nodes)
        if total > 0 and enabled / total < 0.5:
            recs.append(f"Only {enabled}/{total} nodes enabled — re-enable disabled nodes for fuller coverage")

        # LLM recommendation
        llm_stages = [s for s in report.stages if s.get("llm_analysis")]
        if not llm_stages and self.bridge is None:
            recs.append("Start llama-server (port 18080) for LLM-powered feedback analysis")
        elif not llm_stages:
            recs.append("llama-server is running but InferenceBridge not imported — check paths")

        if report.stages_completed == len(report.stages):
            recs.append("All stages complete — run DistillZ to synthesize pipeline knowledge")

        return recs

    def _record_stage(self, stage_name: str, data: Dict, severity: str = "info"):
        """Record a pipeline stage to PAMS memory."""
        if self._recorder:
            self._recorder.record_event(
                "hydra_stage",
                data,
                source=f"HYDRA.{stage_name}",
                severity=severity,
            )

    def _record_event(self, event_type: str, data: Dict):
        """Record a pipeline event to PAMS memory."""
        if self._recorder:
            self._recorder.record_event(
                event_type,
                data,
                source="HydraBridge",
            )

    def _print_summary(self, report: PipelineReport):
        """Print pipeline execution summary."""
        print()
        print("=" * 70)
        print(f"  HYDRA Pipeline Complete ({report.total_duration:.1f}s)")
        print("=" * 70)
        for s in report.stages:
            status = "OK" if s.get("success") else "FAIL"
            errors = f" — {', '.join(s.get('errors', []))}" if s.get('errors') else ""
            llm = f" [LLM: {s['llm_analysis'][:60]}...]" if s.get('llm_analysis') else ""
            print(f"  [{status}] {s['stage']}: {s.get('modules_processed', 0)} modules in {s.get('duration', 0):.2f}s{errors}{llm}")

        if report.recommendations:
            print()
            print("  Recommendations:")
            for r in report.recommendations:
                print(f"  - {r}")
        print("=" * 70)

    def get_status(self) -> dict[str, Any]:
        """Get bridge status."""
        cluster_status = self.cluster.get_cluster_status()
        return {
            "version": self.VERSION,
            "cluster": cluster_status,
            "llm_available": self.bridge is not None and self.bridge.llama.is_available() if self.bridge else False,
            "recorder_active": self._recorder is not None,
        }


def main():
    """Run HydraBridge as standalone."""

    parser = argparse.ArgumentParser(description="HYDRA Pipeline Bridge")
    parser.add_argument("--run", action="store_true", help="Run full 6-stage pipeline")
    parser.add_argument("--status", action="store_true", help="Show bridge status")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM analysis")
    parser.add_argument("--stage", type=str, help="Run a single stage (discovery|analysis|optimization|validation|deployment|feedback)")

    args = parser.parse_args()
    bridge = HydraBridge()

    if args.status:
        status = bridge.get_status()
        print(json.dumps(status, indent=2, default=str))

    elif args.stage:
        stage_map = {s.value.lower(): s for s in ProcessingStage}
        stage = stage_map.get(args.stage.lower())
        if stage:
            bridge.cluster.run_stage(stage)
            print(f"Stage {stage.value} complete")
        else:
            print(f"Unknown stage: {args.stage}")
            print(f"Valid: {', '.join(stage_map.keys())}")

    elif args.run:
        bridge.run_full_pipeline(use_llm=not args.no_llm)

    else:
        print(f"HydraBridge v{HydraBridge.VERSION}")
        print()
        print("Usage:")
        print("  --run         Run full 6-stage pipeline")
        print("  --status      Show bridge status")
        print("  --stage NAME  Run a single stage")
        print("  --no-llm      Skip LLM feedback analysis")


if __name__ == "__main__":
    main()
