# coding: utf-8
"""
ECOSYSTEM_EXPANSION — 50 New Worm Classes (split into 10 category modules)

Categories:
  security    (5):  VulnScanner, PortAuditor, CertMonitor, FirewallAudit, PermissionScanner
  profiling   (5):  CodeProfiler, MemoryLeakDetector, IOBottleneckFinder, GILContentionProbe, CPUFlameProfiler
  telemetry   (5):  SpanTracer, MetricAggregator, LogStreamer, EventRouter, AlertEscalator
  container   (5):  ContainerOrch, ComposeValidator, ImageAuditor, RegistryPoller, ResourceQuotaWatch
  config      (5):  ConfigLinter, EnvValidator, DotfileSync, SecretRotator, FeatureFlagCtrl
  backup      (5):  StateSnapshotter, IncrementalBackupWorm, RecoveryTester, ColdArchivist, CheckpointWorm
  migration   (5):  CodeMigrator, DepUpgrader, SchemaVersioner, ImportPathFixWorm, LegacyShimGen
  communication (5): MessageBroker, RPCEndpoint, WebhookRelay, IPCBridge, ProtocolNegotiator
  learning    (5):  RLTrainLoop, ModelVersioner, DataPipelineWorm, ABTestRunner, FeatureEngineer
  scheduling  (2):  ScheduledTrainingWorm — automated ML pipeline on configurable interval
                    ScheduledDbMonitorWorm — scheduled database health monitor
  db_monitor  (1):  DatabaseMonitorWorm — database health monitor with alerts
  visualization (5): DashboardGen, GraphRenderer, ReportCompiler, HeatmapBuilder, TimelineViz
"""

from .security import VulnScanner, PortAuditor, CertMonitor, FirewallAudit, PermissionScanner
from .profiling import CodeProfiler, MemoryLeakDetector, IOBottleneckFinder, GILContentionProbe, CPUFlameProfiler
from .telemetry import SpanTracer, MetricAggregator, LogStreamer, EventRouter, AlertEscalator
from .container import ContainerOrch, ComposeValidator, ImageAuditor, RegistryPoller, ResourceQuotaWatch
from .config import ConfigLinter, EnvValidator, DotfileSync, SecretRotator, FeatureFlagCtrl
from .backup import StateSnapshotter, IncrementalBackupWorm, RecoveryTester, ColdArchivist, CheckpointWorm
from .migration import CodeMigrator, DepUpgrader, SchemaVersioner, ImportPathFixWorm, LegacyShimGen
from .communication import MessageBroker, RPCEndpoint, WebhookRelay, IPCBridge, ProtocolNegotiator
from .learning import RLTrainLoop, ModelVersioner, DataPipelineWorm, ABTestRunner, FeatureEngineer
from .scheduling import ScheduledTrainingWorm, ScheduledDbMonitorWorm, TrainingOrchestrator
from .db_monitor import DatabaseMonitorWorm
from .visualization import DashboardGen, GraphRenderer, ReportCompiler, HeatmapBuilder, TimelineViz
from .meta_cognition import MetaCognitionWorm
from .evolution import EvolutionEngine, GeneticOptimizer
from .dreaming import DreamingModule
from .race_detector import RaceConditionDetector
from .error_recovery import ErrorRecoveryOrchestrator
from .mesh import MeshRouterWorm, GossipSyncWorm, PartitionHealerWorm, EnvBeaconWorm
from .synapse import SynapseWeightWorm, MoEDispatcherWorm, SSMSequenceWorm, ActivationRouterWorm
from .consensus import QuorumVoterWorm, LeaderElectorWorm, SplitBrainResolverWorm
from .engram import VectorShardWorm, RecallRouterWorm, CoherenceValidatorWorm, DreamConsolidatorWorm
from .federated import FederatedAggregatorWorm, NASMeshSearcherWorm, RewardShaperMeshWorm
from .servemesh import InferenceLoadBalancerWorm, ProviderFailoverMeshWorm, ModelShardServerWorm
from .immune import ImmuneSentinelWorm, QuarantineWorm, AttestationWorm, SelfHealWorm
from .xenomorph import (OvomorphWorm, FacehuggerWorm, ChestbursterWorm, DroneWorm,
                        PraetorianWorm, Lilith, ResinWorm)
from .spine import (SpineMapperWorm, HomeostatWorm, RedQueenWorm,
                    SpineCycleWorm, SpineGossipWorm, MorphogenesisWorm,
                    ConsolidationWorm, MeshArchitectWorm)
from typing import List
from collections import defaultdict

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    # security
    "VulnScanner", "PortAuditor", "CertMonitor", "FirewallAudit", "PermissionScanner",
    # profiling
    "CodeProfiler", "MemoryLeakDetector", "IOBottleneckFinder", "GILContentionProbe", "CPUFlameProfiler",
    # telemetry
    "SpanTracer", "MetricAggregator", "LogStreamer", "EventRouter", "AlertEscalator",
    # container
    "ContainerOrch", "ComposeValidator", "ImageAuditor", "RegistryPoller", "ResourceQuotaWatch",
    # config
    "ConfigLinter", "EnvValidator", "DotfileSync", "SecretRotator", "FeatureFlagCtrl",
    # backup
    "StateSnapshotter", "IncrementalBackupWorm", "RecoveryTester", "ColdArchivist", "CheckpointWorm",
    # migration
    "CodeMigrator", "DepUpgrader", "SchemaVersioner", "ImportPathFixWorm", "LegacyShimGen",
    # communication
    "MessageBroker", "RPCEndpoint", "WebhookRelay", "IPCBridge", "ProtocolNegotiator",
    # learning
    "RLTrainLoop", "ModelVersioner", "DataPipelineWorm", "ABTestRunner", "FeatureEngineer",
    # scheduling
    "ScheduledTrainingWorm",
    "ScheduledDbMonitorWorm",
    "TrainingOrchestrator",
    # db_monitor
    "DatabaseMonitorWorm",
    # visualization
    "DashboardGen", "GraphRenderer", "ReportCompiler", "HeatmapBuilder", "TimelineViz",
    # neural_advancement (meta-cognition, evolution, dreaming)
    "MetaCognitionWorm", "EvolutionEngine", "GeneticOptimizer",
    "DreamingModule",
    # infrastructure (race detection, error recovery)
    "RaceConditionDetector", "ErrorRecoveryOrchestrator",
    # neural mesh fabric (25 worms, 7 natures)
    "MeshRouterWorm", "GossipSyncWorm", "PartitionHealerWorm", "EnvBeaconWorm",
    "SynapseWeightWorm", "MoEDispatcherWorm", "SSMSequenceWorm", "ActivationRouterWorm",
    "QuorumVoterWorm", "LeaderElectorWorm", "SplitBrainResolverWorm",
    "VectorShardWorm", "RecallRouterWorm", "CoherenceValidatorWorm", "DreamConsolidatorWorm",
    "FederatedAggregatorWorm", "NASMeshSearcherWorm", "RewardShaperMeshWorm",
    "InferenceLoadBalancerWorm", "ProviderFailoverMeshWorm", "ModelShardServerWorm",
    "ImmuneSentinelWorm", "QuarantineWorm", "AttestationWorm", "SelfHealWorm",
    # xenomorph hive (7 worms)
    "OvomorphWorm", "FacehuggerWorm", "ChestbursterWorm", "DroneWorm",
    "PraetorianWorm", "Lilith", "ResinWorm",
    # spine (7 worms): self-mapping + homeostat feedback + red-team driver
    # + gossip sync + one-entry self-improvement cycle + morphogenesis
    # (grows specialists from chronic failures) + consolidation (sleep-phase
    # dreaming wired to the Q-table)
    "SpineMapperWorm", "HomeostatWorm", "RedQueenWorm",
    "MeshArchitectWorm",
]


def get_all_expansion_worms():
    """Return all worm class names defined in this package."""
    return list(__all__)


def main():
    """Print expansion summary."""
    cats = defaultdict(list)
    for name in __all__:
        cls = globals().get(name)
        if cls and hasattr(cls, "VERSION"):
            cats[cls.__module__].append(name)
            print("=" * 70)
            print("  ECOSYSTEM EXPANSION — 50 New Worm Classes")
            print("=" * 70)
            for cat in sorted(cats):
                print(f"  [{cat}] ({len(cats[cat])} worms):")
                for name in sorted(cats[cat]):
                    print(f"    {name}")
                    print(f"  Total: {len(__all__)} worm classes")
                    print("=" * 70)


if __name__ == "__main__":
    main()
