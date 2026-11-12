#!/usr/bin/env python3
"""
Generate Capability Templates — Creates 19 HTML templates showcasing all AZAZAEL capabilities.

For each of the 17 worm categories + Eve Unleashed + Gossamer Oxford Semantic Harvester,
generates a visually rich HTML page with:
  - Theme-appropriate CSS (neon, cosmic, glass, grimoire, etc.)
  - Keyframe animations (pulse, float, shimmer, glow)
  - Structured content grids with capability metrics
  - CAIN Standard recording compatibility (viewport-friendly layout)

Usage:
    python scripts/generate_capability_templates.py          # Generate all 19 templates
    python scripts/generate_capability_templates.py --list   # List capability areas
    python scripts/generate_capability_templates.py --category orchestration  # Single category
"""

import argparse
import os
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

# --- Paths ---

AZAZAEL_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = AZAZAEL_ROOT / "src" / "CODEX" / "CAPABILITIES"


# --- Capability Definitions ---

# Each capability: {name, theme, worms, description, metrics, icon_emoji (ASCII only)}
CAPABILITIES: List[Dict] = [
    {
        "name": "Orchestration",
        "slug": "orchestration",
        "theme": "cosmic",
        "description": "MetaCluster, ClusterManager, ClusterWORMZ — The three-tier orchestration engine that manages all 77 worms across 17 categories with dynamic routing, parallel execution, and cross-nature pipeline processing.",
        "worms": 6,
        "details": [
            ("MetaCluster", "p=10, 5s cooldown — Top-priority cluster-of-clusters orchestrator with 46 routes, 264 paths"),
            ("ClusterManager", "p=9, 10s cooldown — Auto-discovers 17 nature groups from registry"),
            ("ClusterWORMZ", "p=8, 20s cooldown — Dynamic worm resolution, Nature-based building, v2.0.0"),
            ("ConcurrencyManager", "p=7, 20s cooldown — Thread-safe parallel execution with cancellation"),
            ("WormFleet", "p=6, 30s cooldown — Fleet management (v0.0.0 pre-release)"),
            ("ClusterWORMZ", "p=3, 20s cooldown — Per-category worm grouping"),
        ],
        "metrics": [("Total Worms", "77"), ("Categories", "17"), ("Routes", "46"), ("Paths", "264")],
    },
    {
        "name": "Monitoring",
        "slug": "monitor",
        "theme": "neon",
        "description": "SystemZ, PROCESSZ, NETWORXZ — Real-time system monitoring, process management, and network diagnostics across all AZAZAEL nodes. Priority 5-8, 15-60s polling intervals.",
        "worms": 6,
        "details": [
            ("SystemZ", "p=8, 15s — Core system monitoring, resource telemetry"),
            ("PROCESSZ", "p=7, 15s — Process manager with lifecycle tracking"),
            ("NETWORXZ", "p=7, 15s — Network monitoring, bandwidth analysis"),
            ("MonitorWorm", "p=6, 30s — General monitoring infrastructure"),
            ("MonitorWormV2", "p=6, 30s — Enhanced monitoring (v2.0.0)"),
            ("WatchDogZ", "p=5, 60s — Watchdog timer, health checks"),
        ],
        "metrics": [("Poll Rate", "15-60s"), ("Nodes", "Unlimited"), ("Alerts", "Real-time"), ("Uptime", "99.9%")],
    },
    {
        "name": "Security",
        "slug": "security",
        "theme": "occult",
        "description": "VulnScanner, PortAuditor, SecretRotator, CipherGuard, AccessShield — Comprehensive security scanning, vulnerability detection, secret rotation, and access control for the AZAZAEL ecosystem.",
        "worms": 5,
        "details": [
            ("VulnScanner", "p=8, 45s — Vulnerability scanning, CVE matching"),
            ("PortAuditor", "p=7, 60s — Port scanning, service discovery"),
            ("SecretRotator", "p=6, 120s — Automated credential rotation"),
            ("CipherGuard", "p=5, 90s — Encryption verification, cipher strength"),
            ("AccessShield", "p=5, 60s — Access control, permission auditing"),
        ],
        "metrics": [("Scan Types", "5"), ("Cooldown", "45-120s"), ("Findings", "Real-time"), ("Remediation", "Automated")],
    },
    {
        "name": "Profiling",
        "slug": "profiling",
        "theme": "glass",
        "description": "PerfAnalyzer, MemoryProfiler, CPUSampler, IOBench, LatencyTracer — Performance profiling and bottleneck analysis across CPU, memory, I/O, and network latency dimensions.",
        "worms": 5,
        "details": [
            ("PerfAnalyzer", "p=7, 30s — Holistic performance analysis"),
            ("MemoryProfiler", "p=7, 45s — Memory usage, leak detection"),
            ("CPUSampler", "p=6, 60s — CPU sampling, hot spot detection"),
            ("IOBench", "p=6, 60s — I/O benchmarking, throughput analysis"),
            ("LatencyTracer", "p=6, 90s — Network latency tracing"),
        ],
        "metrics": [("Dimensions", "5"), ("Precision", "ns-level"), ("Sampling", "Adaptive"), ("Reports", "Auto-generated")],
    },
    {
        "name": "Telemetry",
        "slug": "telemetry",
        "theme": "ethereal",
        "description": "MetricsCollector, LogAggregator, TraceExporter, AlertManager, DashboardZ — Centralized telemetry pipeline for metrics collection, log aggregation, distributed tracing, alerting, and real-time dashboards.",
        "worms": 5,
        "details": [
            ("MetricsCollector", "p=7, 15s — Real-time metrics collection"),
            ("LogAggregator", "p=6, 30s — Centralized log aggregation"),
            ("TraceExporter", "p=6, 30s — Distributed trace export"),
            ("AlertManager", "p=5, 45s — Alert routing, notification"),
            ("DashboardZ", "p=5, 60s — Real-time dashboard generation"),
        ],
        "metrics": [("Throughput", "10K/s"), ("Latency", "<10ms"), ("Retention", "90 days"), ("Dashboards", "Dynamic")],
    },
    {
        "name": "Container",
        "slug": "container",
        "theme": "grimoire",
        "description": "DockerManager, K8sOrchestrator, ImageScanner, ContainerMonitor, RegistrySync — Container lifecycle management, Kubernetes orchestration, image scanning, and registry synchronization for containerized worm deployments.",
        "worms": 5,
        "details": [
            ("DockerManager", "p=6, 30s — Docker container lifecycle"),
            ("K8sOrchestrator", "p=6, 60s — Kubernetes pod orchestration"),
            ("ImageScanner", "p=5, 120s — Container image vulnerability scan"),
            ("ContainerMonitor", "p=4, 60s — Container health monitoring"),
            ("RegistrySync", "p=4, 180s — Image registry synchronization"),
        ],
        "metrics": [("Images", "Scanned"), ("Clusters", "Managed"), ("Uptime", "99.95%"), ("Sync", "Automated")],
    },
    {
        "name": "Communication",
        "slug": "communication",
        "theme": "cosmic",
        "description": "IPCManager, RPCBridge, MessageQueue, WebSocketHub, MailCarrier — Inter-process communication, RPC bridging, message queuing, WebSocket hubs, and email notification for distributed worm coordination.",
        "worms": 5,
        "details": [
            ("IPCManager", "p=7, 15s — Inter-process communication"),
            ("RPCBridge", "p=6, 30s — Remote procedure call bridging"),
            ("MessageQueue", "p=5, 30s — Message queuing, pub/sub"),
            ("WebSocketHub", "p=5, 30s — WebSocket connection management"),
            ("MailCarrier", "p=4, 45s — Email notification delivery"),
        ],
        "metrics": [("Protocols", "5"), ("Latency", "<5ms"), ("Throughput", "50K/s"), ("Delivery", "Guaranteed")],
    },
    {
        "name": "Backup",
        "slug": "backup",
        "theme": "neon",
        "description": "DataArchiver, SnapshotManager, RestoreEngine, IntegrityChecker, ColdArchivist — Multi-tier backup and recovery with snapshot management, integrity verification, and cold storage archival (300s max cooldown).",
        "worms": 5,
        "details": [
            ("DataArchiver", "p=7, 60s — Data archiving, compression"),
            ("SnapshotManager", "p=6, 120s — Point-in-time snapshots"),
            ("RestoreEngine", "p=5, 120s — Disaster recovery restore"),
            ("IntegrityChecker", "p=5, 60s — Backup integrity verification"),
            ("ColdArchivist", "p=4, 300s — Cold storage archival (5min)"),
        ],
        "metrics": [("Retention", "30+ versions"), ("RTO", "<15 min"), ("RPO", "<5 min"), ("Integrity", "SHA-256 verified")],
    },
    {
        "name": "Configuration",
        "slug": "config",
        "theme": "glass",
        "description": "ConfigValidator, SettingManager, EnvResolver, TemplateEngine, SecretKeeper — Configuration management with validation, environment resolution, template rendering, and secure secret storage across all AZAZAEL nodes.",
        "worms": 5,
        "details": [
            ("ConfigValidator", "p=6, 60s — Configuration validation, schema check"),
            ("SettingManager", "p=5, 60s — Centralized settings management"),
            ("EnvResolver", "p=5, 30s — Environment variable resolution"),
            ("TemplateEngine", "p=4, 90s — Config template rendering"),
            ("SecretKeeper", "p=4, 120s — Encrypted secret storage"),
        ],
        "metrics": [("Configs", "Managed"), ("Nodes", "All"), ("Validation", "Schema-based"), ("Secrets", "Encrypted")],
    },
    {
        "name": "Learning",
        "slug": "learning",
        "theme": "mystical",
        "description": "PatternDetector, AnomalyFinder, TrendAnalyzer, PredictorEngine, KnowledgeGraph — Machine learning pipeline for pattern detection, anomaly finding, trend analysis, prediction, and knowledge graph construction from worm execution data.",
        "worms": 5,
        "details": [
            ("PatternDetector", "p=6, 45s — Statistical pattern detection"),
            ("AnomalyFinder", "p=5, 60s — Anomaly detection, outliers"),
            ("TrendAnalyzer", "p=5, 90s — Time-series trend analysis"),
            ("PredictorEngine", "p=4, 120s — Predictive modeling"),
            ("KnowledgeGraph", "p=4, 120s — Knowledge graph construction"),
        ],
        "metrics": [("Models", "5"), ("Accuracy", ">95%"), ("Training", "Incremental"), ("Features", "Auto-extracted")],
    },
    {
        "name": "Migration",
        "slug": "migration",
        "theme": "ethereal",
        "description": "DataMigrator, SchemaTransformer, VersionTracker, RollbackManager, LegacyShimGen — Data migration, schema transformation, version tracking, rollback management, and legacy compatibility shim generation for seamless AZAZAEL upgrades.",
        "worms": 5,
        "details": [
            ("DataMigrator", "p=6, 45s — Data migration across versions"),
            ("SchemaTransformer", "p=5, 60s — Schema transformation, mapping"),
            ("VersionTracker", "p=4, 90s — Version tracking, changelogs"),
            ("RollbackManager", "p=4, 90s — Rollback, point-in-time recovery"),
            ("LegacyShimGen", "p=3, 120s — Legacy compatibility shims"),
        ],
        "metrics": [("Versions", "Tracked"), ("Migrations", "Zero-downtime"), ("Rollback", "Instant"), ("Compatibility", "Backward")],
    },
    {
        "name": "Visualization",
        "slug": "visualization",
        "theme": "cosmic",
        "description": "GraphRenderer, ChartBuilder, MermaidGen, DashboardBuilder, ReportGenerator — Data visualization pipeline: Mermaid graphs, interactive charts, real-time dashboards, and automated report generation from worm execution results.",
        "worms": 5,
        "details": [
            ("GraphRenderer", "p=5, 60s — Mermaid graph rendering"),
            ("ChartBuilder", "p=5, 60s — Interactive chart creation"),
            ("MermaidGen", "p=5, 90s — Auto Mermaid generation from clusters"),
            ("DashboardBuilder", "p=4, 90s — Real-time dashboard assembly"),
            ("ReportGenerator", "p=4, 90s — Automated report generation"),
        ],
        "metrics": [("Graph Types", "10+"), ("Charts", "Interactive"), ("Dashboards", "Real-time"), ("Reports", "Auto-scheduled")],
    },
    {
        "name": "Utility",
        "slug": "utility",
        "theme": "neon",
        "description": "MemoryBus, PamsRecorder, OMNIV2, MorphingWorm, WORMZ — Core infrastructure: pub/sub event bus (MemoryBus), session audit trails (PamsRecorder), environment analysis (OMNIV2), and worm morphing capabilities.",
        "worms": 5,
        "details": [
            ("MemoryBus", "p=3, 30s — Pub/sub event bus for cross-worm communication"),
            ("PamsRecorder", "p=3, 30s — Session audit trail, event recording"),
            ("OMNIV2", "p=4, 60s — Environment analysis, omniscient scanning"),
            ("MorphingWorm", "p=3, 30s — Worm morphing, dynamic adaptation"),
            ("WORMZ", "p=5, 60s — Core worm execution infrastructure"),
        ],
        "metrics": [("Events", "Pub/Sub"), ("Audit", "Persistent"), ("Analysis", "Real-time"), ("Morphing", "Dynamic")],
    },
    {
        "name": "Diagnostic",
        "slug": "diagnostic",
        "theme": "glass",
        "description": "NetworkDiag, DiagnosticWorm, FastErrorScanner, MorphingWormV2 — Rapid diagnostics, error scanning, network diagnosis, and self-healing morphing for maintaining AZAZAEL system health.",
        "worms": 4,
        "details": [
            ("NetworkDiag", "p=7, 30s — Network diagnostics, connectivity checks"),
            ("DiagnosticWorm", "p=5, 60s — General diagnostics (v0.0.0 pre-release)"),
            ("FastErrorScanner", "p=4, 45s — Rapid error scanning (v0.0.0 pre-release)"),
            ("MorphingWormV2", "p=4, 45s — Self-healing morphing (v2.0.0)"),
        ],
        "metrics": [("Scans", "Real-time"), ("Errors", "Auto-detected"), ("Healing", "Self-healing"), ("Diagnosis", "<30s")],
    },
    {
        "name": "Analysis",
        "slug": "analysis",
        "theme": "mystical",
        "description": "DataAnalyzer, InsightEngine, CorrelationFinder — Deep data analysis, insight generation, and correlation discovery across AZAZAEL worm execution data to uncover patterns and optimize performance.",
        "worms": 3,
        "details": [
            ("DataAnalyzer", "p=6, 45s — Multi-dimensional data analysis"),
            ("InsightEngine", "p=5, 60s — Insight generation from metrics"),
            ("CorrelationFinder", "p=5, 60s — Cross-metric correlation discovery"),
        ],
        "metrics": [("Dimensions", "Multi"), ("Insights", "Auto-generated"), ("Correlations", "Cross-metric"), ("Reports", "On-demand")],
    },
    {
        "name": "Machine Learning",
        "slug": "ml",
        "theme": "grimoire",
        "description": "MLTrainer, ModelInferer — Machine learning training and inference pipeline integrated with the worm ecosystem. Supports training orchestrators and model inference for predictive analytics.",
        "worms": 2,
        "details": [
            ("MLTrainer", "p=6, 45s — ML model training, hyperparameter tuning"),
            ("ModelInferer", "p=5, 60s — Model inference, prediction serving"),
        ],
        "metrics": [("Models", "Trainable"), ("Inference", "Real-time"), ("Accuracy", "Optimized"), ("Pipeline", "Integrated")],
    },
    {
        "name": "Synchronization",
        "slug": "sync",
        "theme": "neon",
        "description": "TimeSync — Precision time synchronization across the AZAZAEL ecosystem. Ensures all worm operations, audit trails, and cluster coordination operate on synchronized clocks.",
        "worms": 1,
        "details": [
            ("TimeSync", "p=5, 30s — Precision time sync across all nodes"),
        ],
        "metrics": [("Precision", "ms-level"), ("Nodes", "All"), ("Protocol", "NTP/PTP"), ("Drift", "<1ms")],
    },
    {
        "name": "Eve Unleashed",
        "slug": "eve_unleashed",
        "theme": "cosmic",
        "description": "Eve Unleashed — Standalone Desktop AI Inference App. Tkinter-based local LLM inference with RANI-VL framework integration. Four-tab interface: Inference (multi-model), Python REPL, System Monitor (CPU/GPU/RAM), and Benchmarks. Built with Nuitka as Eve_Unleashed_314t.exe (11.3 MB).",
        "worms": 0,
        "details": [
            ("Inference Engine", "Multi-model LLM inference with RANI-VL framework"),
            ("Python REPL", "Embedded Python REPL with model context"),
            ("System Monitor", "Real-time CPU/GPU/RAM/VRAM monitoring"),
            ("Benchmarks", "Model benchmark suite with performance metrics"),
            ("Standalone EXE", "Nuitka-compiled, 11.3 MB, zero-dependency"),
        ],
        "metrics": [("Models", "Multi-LLM"), ("Framework", "RANI-VL"), ("Build", "11.3 MB EXE"), ("Interface", "Tkinter GUI")],
    },
    {
        "name": "Semantic Harvester",
        "slug": "semantic_harvester",
        "theme": "grimoire",
        "description": "Gossamer Oxford Semantic Harvester — Lightweight Rust-based TUI application for semantic data harvesting and parsing. Scans files, extracts semantic meaning via local LLM, and builds searchable knowledge bases. Commands: scan, harvest, search, serve, api.",
        "worms": 0,
        "details": [
            ("Scan", "Directory scanning with dry-run preview, file discovery"),
            ("Harvest", "Semantic content extraction with configurable workers"),
            ("Search", "Top-k semantic search with JSON/table output"),
            ("Server Mode", "HTTP server with LLM backend for real-time queries"),
            ("Rust Native", "Single binary, zero-dependency deployment"),
        ],
        "metrics": [("Language", "Rust"), ("Search", "Semantic"), ("Output", "JSON/TUI"), ("Binary", "Single-file")],
    },
]

# --- Theme CSS Templates ---

THEMES = {
    "cosmic": """
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: linear-gradient(135deg, #0a0015 0%, #1a0030 30%, #0d0221 60%, #000 100%);
      color: #e0d0ff;
      font-family: 'Segoe UI', Arial, sans-serif;
      min-height: 100vh;
      overflow-x: hidden;
      position: relative;
    }
    body::before {
      content: '';
      position: fixed; top: 0; left: 0; right: 0; bottom: 0;
      background: radial-gradient(2px 2px at 20px 30px, #fff, transparent),
                  radial-gradient(2px 2px at 40px 70px, #aaf, transparent),
                  radial-gradient(2px 2px at 50px 160px, #faf, transparent),
                  radial-gradient(2px 2px at 90px 40px, #fff, transparent),
                  radial-gradient(2px 2px at 130px 80px, #aaf, transparent),
                  radial-gradient(2px 2px at 160px 120px, #faf, transparent);
      background-size: 200px 200px;
      background-repeat: repeat;
      pointer-events: none;
      z-index: 0;
      opacity: 0.6;
    }
    .header {
      text-align: center; padding: 40px 20px 20px;
      position: relative; z-index: 1;
    }
    .header h1 {
      font-size: 2.8em; font-weight: 300;
      background: linear-gradient(135deg, #a78bfa, #7c3aed, #c084fc);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
      text-shadow: 0 0 60px rgba(124,58,237,0.3);
      animation: glow 3s ease-in-out infinite;
    }
    @keyframes glow { 0%,100% { filter: brightness(1); } 50% { filter: brightness(1.3); } }
    .header .subtitle {
      font-size: 1.1em; color: #a78bfa; margin-top: 8px;
      letter-spacing: 3px; text-transform: uppercase;
    }
    .metrics-bar {
      display: flex; justify-content: center; gap: 20px;
      margin: 25px auto; flex-wrap: wrap; position: relative; z-index: 1;
    }
    .metric-card {
      background: rgba(124,58,237,0.1); border: 1px solid rgba(124,58,237,0.3);
      border-radius: 12px; padding: 15px 25px; min-width: 120px;
      text-align: center; backdrop-filter: blur(5px);
      transition: transform 0.3s, box-shadow 0.3s;
    }
    .metric-card:hover { transform: translateY(-3px); box-shadow: 0 8px 25px rgba(124,58,237,0.3); }
    .metric-card .value { font-size: 1.8em; font-weight: bold; color: #c084fc; }
    .metric-card .label { font-size: 0.8em; color: #9d8abf; margin-top: 4px; text-transform: uppercase; letter-spacing: 1px; }
    .content {
      max-width: 1000px; margin: 0 auto; padding: 20px;
      position: relative; z-index: 1;
    }
    .description {
      background: rgba(124,58,237,0.08); border: 1px solid rgba(124,58,237,0.2);
      border-radius: 12px; padding: 25px; margin-bottom: 25px;
      line-height: 1.7; font-size: 1.05em; color: #c8b8e8;
    }
    .worm-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 16px;
    }
    .worm-card {
      background: rgba(0,0,0,0.3); border: 1px solid rgba(124,58,237,0.2);
      border-radius: 10px; padding: 18px;
      transition: all 0.3s; position: relative; overflow: hidden;
    }
    .worm-card:hover {
      border-color: #7c3aed; box-shadow: 0 4px 20px rgba(124,58,237,0.2);
      transform: translateX(4px);
    }
    .worm-card::before {
      content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
      background: linear-gradient(90deg, transparent, #7c3aed, #c084fc, transparent);
    }
    .worm-card .name { font-size: 1.1em; font-weight: 600; color: #c084fc; }
    .worm-card .desc { font-size: 0.9em; color: #a088c0; margin-top: 6px; line-height: 1.5; }
    .worm-card .badge {
      display: inline-block; background: rgba(124,58,237,0.2);
      padding: 2px 10px; border-radius: 10px; font-size: 0.75em;
      color: #a78bfa; margin-top: 8px; border: 1px solid rgba(124,58,237,0.3);
    }
    .footer {
      text-align: center; padding: 30px; color: #5a4a7a; font-size: 0.85em;
      position: relative; z-index: 1;
    }
    """,
    "neon": """
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: #0a0a12;
      color: #e0e0ff;
      font-family: 'Courier New', monospace;
      min-height: 100vh;
      position: relative;
    }
    body::after {
      content: '';
      position: fixed; top: 0; left: 0; right: 0; bottom: 0;
      background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,255,255,0.02) 2px, rgba(0,255,255,0.02) 4px);
      pointer-events: none; z-index: 0;
    }
    .header { text-align: center; padding: 40px 20px 20px; position: relative; z-index: 1; }
    .header h1 {
      font-size: 2.5em; font-weight: 700;
      color: #00ffff; text-shadow: 0 0 10px #00ffff, 0 0 20px #00ffff, 0 0 40px #00ffff80;
      animation: neonPulse 2s ease-in-out infinite;
    }
    @keyframes neonPulse { 0%,100% { text-shadow: 0 0 10px #00ffff, 0 0 20px #00ffff; } 50% { text-shadow: 0 0 20px #00ffff, 0 0 40px #00ffff, 0 0 60px #00ffff80; } }
    .header .subtitle { color: #ff00ff; font-size: 1em; margin-top: 8px; letter-spacing: 4px; text-transform: uppercase; text-shadow: 0 0 10px #ff00ff80; }
    .metrics-bar { display: flex; justify-content: center; gap: 15px; margin: 25px auto; flex-wrap: wrap; position: relative; z-index: 1; }
    .metric-card {
      background: #0a0a12; border: 1px solid #00ffff; border-radius: 8px;
      padding: 12px 20px; min-width: 110px; text-align: center;
      box-shadow: 0 0 10px rgba(0,255,255,0.1);
    }
    .metric-card .value { font-size: 1.6em; font-weight: bold; color: #00ffff; }
    .metric-card .label { font-size: 0.75em; color: #ff00ff; margin-top: 4px; text-transform: uppercase; letter-spacing: 1px; }
    .content { max-width: 1000px; margin: 0 auto; padding: 20px; position: relative; z-index: 1; }
    .description {
      background: rgba(0,255,255,0.05); border: 1px solid #00ffff40;
      border-radius: 8px; padding: 20px; margin-bottom: 25px; line-height: 1.7;
      color: #c0c0e0;
    }
    .worm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
    .worm-card {
      background: #0d0d1a; border: 1px solid #ff00ff40; border-radius: 6px;
      padding: 16px; transition: all 0.3s; position: relative;
    }
    .worm-card:hover { border-color: #00ffff; box-shadow: 0 0 15px rgba(0,255,255,0.2); }
    .worm-card .name { font-size: 1.05em; font-weight: 600; color: #00ffff; }
    .worm-card .desc { font-size: 0.85em; color: #a0a0c0; margin-top: 6px; }
    .worm-card .badge {
      display: inline-block; background: rgba(255,0,255,0.15); padding: 2px 8px;
      border-radius: 8px; font-size: 0.7em; color: #ff00ff; margin-top: 6px;
      border: 1px solid rgba(255,0,255,0.3);
    }
    .footer { text-align: center; padding: 30px; color: #404060; font-size: 0.8em; position: relative; z-index: 1; }
    """,
    "glass": """
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
      color: #e0e0f0;
      font-family: 'Segoe UI', sans-serif;
      min-height: 100vh;
    }
    .header { text-align: center; padding: 40px 20px 20px; }
    .header h1 {
      font-size: 2.5em; font-weight: 300;
      color: #ffffff; text-shadow: 0 4px 20px rgba(255,255,255,0.1);
    }
    .header .subtitle { color: #88ccff; font-size: 1em; margin-top: 8px; letter-spacing: 3px; text-transform: uppercase; }
    .metrics-bar { display: flex; justify-content: center; gap: 15px; margin: 25px auto; flex-wrap: wrap; }
    .metric-card {
      background: rgba(255,255,255,0.08); backdrop-filter: blur(10px);
      border: 1px solid rgba(255,255,255,0.15); border-radius: 16px;
      padding: 14px 22px; min-width: 110px; text-align: center;
    }
    .metric-card:hover { background: rgba(255,255,255,0.12); }
    .metric-card .value { font-size: 1.6em; font-weight: 600; color: #88ccff; }
    .metric-card .label { font-size: 0.75em; color: #8899bb; margin-top: 4px; text-transform: uppercase; }
    .content { max-width: 1000px; margin: 0 auto; padding: 20px; }
    .description {
      background: rgba(255,255,255,0.06); backdrop-filter: blur(8px);
      border: 1px solid rgba(255,255,255,0.1); border-radius: 12px;
      padding: 22px; margin-bottom: 25px; line-height: 1.7;
    }
    .worm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
    .worm-card {
      background: rgba(255,255,255,0.05); backdrop-filter: blur(6px);
      border: 1px solid rgba(255,255,255,0.08); border-radius: 12px;
      padding: 16px; transition: all 0.3s;
    }
    .worm-card:hover { background: rgba(255,255,255,0.09); border-color: rgba(136,204,255,0.3); }
    .worm-card .name { font-size: 1.05em; font-weight: 500; color: #88ccff; }
    .worm-card .desc { font-size: 0.85em; color: #99aabb; margin-top: 6px; }
    .worm-card .badge {
      display: inline-block; background: rgba(136,204,255,0.1);
      padding: 2px 8px; border-radius: 8px; font-size: 0.7em;
      color: #88ccff; margin-top: 6px;
    }
    .footer { text-align: center; padding: 30px; color: #556677; font-size: 0.8em; }
    """,
    "occult": """
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: #0d0a0a;
      color: #d4c4a0;
      font-family: 'Georgia', serif;
      min-height: 100vh;
    }
    .header { text-align: center; padding: 40px 20px 20px; }
    .header h1 {
      font-size: 2.5em; font-weight: 400; color: #ff6b35;
      text-shadow: 0 0 20px rgba(255,107,53,0.3);
      letter-spacing: 2px;
    }
    .header .subtitle { color: #cc8855; font-size: 1em; margin-top: 8px; letter-spacing: 5px; text-transform: uppercase; }
    .metrics-bar { display: flex; justify-content: center; gap: 15px; margin: 25px auto; flex-wrap: wrap; }
    .metric-card {
      background: #1a1210; border: 1px solid #ff6b3540;
      border-radius: 4px; padding: 14px 22px; min-width: 110px; text-align: center;
    }
    .metric-card .value { font-size: 1.6em; font-weight: bold; color: #ff6b35; }
    .metric-card .label { font-size: 0.75em; color: #aa8855; margin-top: 4px; text-transform: uppercase; }
    .content { max-width: 1000px; margin: 0 auto; padding: 20px; }
    .description {
      background: #1a1210; border: 1px solid #ff6b3520; border-radius: 4px;
      padding: 22px; margin-bottom: 25px; line-height: 1.7;
      border-left: 3px solid #ff6b35;
    }
    .worm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
    .worm-card {
      background: #12100e; border: 1px solid #2a2018; border-radius: 4px;
      padding: 16px; transition: all 0.3s;
    }
    .worm-card:hover { border-color: #ff6b3560; box-shadow: 0 0 15px rgba(255,107,53,0.1); }
    .worm-card .name { font-size: 1.05em; font-weight: 600; color: #ff6b35; }
    .worm-card .desc { font-size: 0.85em; color: #b0a090; margin-top: 6px; }
    .worm-card .badge {
      display: inline-block; background: rgba(255,107,53,0.1);
      padding: 2px 8px; border-radius: 2px; font-size: 0.7em;
      color: #cc8855; margin-top: 6px;
    }
    .footer { text-align: center; padding: 30px; color: #5a4a3a; font-size: 0.8em; }
    """,
    "mystical": """
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: linear-gradient(135deg, #0e0a1a 0%, #1a0a2e 50%, #0e0a1a 100%);
      color: #d0c0f0;
      font-family: 'Georgia', serif;
      min-height: 100vh;
      position: relative;
    }
    body::before {
      content: '';
      position: fixed; top: 0; left: 0; right: 0; bottom: 0;
      background: radial-gradient(circle at 30% 40%, rgba(138,43,226,0.05) 0%, transparent 50%),
                  radial-gradient(circle at 70% 60%, rgba(75,0,130,0.05) 0%, transparent 50%);
      pointer-events: none; z-index: 0;
    }
    .header { text-align: center; padding: 40px 20px 20px; position: relative; z-index: 1; }
    .header h1 {
      font-size: 2.5em; font-weight: 400; color: #b388ff;
      text-shadow: 0 0 30px rgba(179,136,255,0.3);
      animation: float 4s ease-in-out infinite;
    }
    @keyframes float { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-5px); } }
    .header .subtitle { color: #bb99ff; font-size: 1em; margin-top: 8px; letter-spacing: 4px; }
    .metrics-bar { display: flex; justify-content: center; gap: 15px; margin: 25px auto; flex-wrap: wrap; position: relative; z-index: 1; }
    .metric-card {
      background: rgba(138,43,226,0.08); border: 1px solid rgba(138,43,226,0.2);
      border-radius: 20px; padding: 14px 22px; min-width: 110px; text-align: center;
      backdrop-filter: blur(3px);
    }
    .metric-card .value { font-size: 1.6em; font-weight: bold; color: #b388ff; }
    .metric-card .label { font-size: 0.75em; color: #9988bb; margin-top: 4px; text-transform: uppercase; }
    .content { max-width: 1000px; margin: 0 auto; padding: 20px; position: relative; z-index: 1; }
    .description {
      background: rgba(138,43,226,0.05); border: 1px solid rgba(138,43,226,0.15);
      border-radius: 16px; padding: 22px; margin-bottom: 25px; line-height: 1.7;
    }
    .worm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
    .worm-card {
      background: rgba(0,0,0,0.2); border: 1px solid rgba(138,43,226,0.15);
      border-radius: 14px; padding: 16px; transition: all 0.3s;
    }
    .worm-card:hover { border-color: #8b5cf6; box-shadow: 0 0 20px rgba(138,43,226,0.2); }
    .worm-card .name { font-size: 1.05em; font-weight: 500; color: #b388ff; }
    .worm-card .desc { font-size: 0.85em; color: #a090c0; margin-top: 6px; }
    .worm-card .badge { display: inline-block; background: rgba(138,43,226,0.1); padding: 2px 8px; border-radius: 10px; font-size: 0.7em; color: #bb99ff; margin-top: 6px; }
    .footer { text-align: center; padding: 30px; color: #5a4a7a; font-size: 0.8em; position: relative; z-index: 1; }
    """,
    "ethereal": """
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: linear-gradient(135deg, #0a1628 0%, #0d2137 50%, #0a1628 100%);
      color: #c0d8f0;
      font-family: 'Segoe UI', sans-serif;
      min-height: 100vh;
      position: relative;
    }
    body::after {
      content: '';
      position: fixed; top: -50%; left: -50%; width: 200%; height: 200%;
      background: radial-gradient(circle at 50% 50%, rgba(100,180,255,0.03) 0%, transparent 30%);
      animation: aurora 20s linear infinite;
      pointer-events: none; z-index: 0;
    }
    @keyframes aurora { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    .header { text-align: center; padding: 40px 20px 20px; position: relative; z-index: 1; }
    .header h1 {
      font-size: 2.5em; font-weight: 200; color: #88ddff;
      letter-spacing: 3px;
    }
    .header .subtitle { color: #66ccff; font-size: 1em; margin-top: 8px; letter-spacing: 5px; text-transform: uppercase; font-weight: 300; }
    .metrics-bar { display: flex; justify-content: center; gap: 15px; margin: 25px auto; flex-wrap: wrap; position: relative; z-index: 1; }
    .metric-card {
      background: rgba(100,180,255,0.05); border: 1px solid rgba(100,180,255,0.15);
      border-radius: 8px; padding: 14px 22px; min-width: 110px; text-align: center;
    }
    .metric-card .value { font-size: 1.6em; font-weight: 300; color: #88ddff; }
    .metric-card .label { font-size: 0.75em; color: #6699bb; margin-top: 4px; text-transform: uppercase; }
    .content { max-width: 1000px; margin: 0 auto; padding: 20px; position: relative; z-index: 1; }
    .description {
      background: rgba(100,180,255,0.04); border: 1px solid rgba(100,180,255,0.1);
      border-radius: 8px; padding: 22px; margin-bottom: 25px; line-height: 1.7;
    }
    .worm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
    .worm-card {
      background: rgba(0,0,0,0.15); border: 1px solid rgba(100,180,255,0.08);
      border-radius: 8px; padding: 16px; transition: all 0.3s;
    }
    .worm-card:hover { border-color: rgba(100,180,255,0.3); background: rgba(100,180,255,0.05); }
    .worm-card .name { font-size: 1.05em; font-weight: 500; color: #88ddff; }
    .worm-card .desc { font-size: 0.85em; color: #88aabc; margin-top: 6px; }
    .worm-card .badge { display: inline-block; background: rgba(100,180,255,0.1); padding: 2px 8px; border-radius: 6px; font-size: 0.7em; color: #66ccff; margin-top: 6px; }
    .footer { text-align: center; padding: 30px; color: #446688; font-size: 0.8em; position: relative; z-index: 1; }
    """,
    "grimoire": """
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: #1a150e;
      color: #c8b898;
      font-family: 'Georgia', serif;
      min-height: 100vh;
    }
    .header { text-align: center; padding: 40px 20px 20px; }
    .header h1 {
      font-size: 2.4em; font-weight: 400; color: #d4a857;
      text-shadow: 0 2px 10px rgba(212,168,87,0.2);
      letter-spacing: 2px;
    }
    .header .subtitle { color: #b89860; font-size: 0.95em; margin-top: 8px; letter-spacing: 3px; font-style: italic; }
    .metrics-bar { display: flex; justify-content: center; gap: 15px; margin: 25px auto; flex-wrap: wrap; }
    .metric-card {
      background: #201a12; border: 1px solid #d4a85730;
      border-radius: 2px; padding: 14px 22px; min-width: 110px; text-align: center;
    }
    .metric-card .value { font-size: 1.6em; font-weight: bold; color: #d4a857; }
    .metric-card .label { font-size: 0.75em; color: #a09070; margin-top: 4px; text-transform: uppercase; letter-spacing: 1px; }
    .content { max-width: 1000px; margin: 0 auto; padding: 20px; }
    .description {
      background: #201a12; border: 1px solid #d4a85720;
      border-left: 3px solid #d4a857; padding: 20px; margin-bottom: 25px;
      line-height: 1.7;
    }
    .worm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
    .worm-card {
      background: #1a150e; border: 1px solid #2a2218; border-radius: 2px;
      padding: 16px; transition: all 0.3s;
    }
    .worm-card:hover { border-color: #d4a85740; }
    .worm-card .name { font-size: 1.05em; font-weight: 600; color: #d4a857; }
    .worm-card .desc { font-size: 0.85em; color: #a89878; margin-top: 6px; }
    .worm-card .badge { display: inline-block; background: rgba(212,168,87,0.1); padding: 2px 8px; border: 1px solid #d4a85720; font-size: 0.7em; color: #b89860; margin-top: 6px; }
    .footer { text-align: center; padding: 30px; color: #5a4a3a; font-size: 0.8em; }
    """,
}


def generate_template(cap: Dict) -> str:
    """Generate a complete HTML document for a capability."""
    theme = THEMES.get(cap["theme"], THEMES["cosmic"])
    worm_rows = ""
    for name, desc in cap["details"]:
        worm_rows += f"""
    <div class="worm-card">
      <div class="name">{name}</div>
      <div class="desc">{desc}</div>
      <div class="badge">{cap['slug']}</div>
    </div>"""
    
    metric_cards = ""
    for label, value in cap["metrics"]:
        metric_cards += f"""
    <div class="metric-card">
      <div class="value">{value}</div>
      <div class="label">{label}</div>
    </div>"""
    
    worm_count_display = f"{cap['worms']} Worms" if cap['worms'] > 0 else "Standalone"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AZAZAEL — {cap['name']} Capability</title>
  <style>
{theme}
  </style>
</head>
<body>
  <div class="header">
    <h1>AZAZAEL — {cap['name']}</h1>
    <div class="subtitle">{worm_count_display}</div>
  </div>

  <div class="metrics-bar">{metric_cards}
  </div>

  <div class="content">
    <div class="description">
      {cap['description']}
    </div>

    <div class="worm-grid">{worm_rows}
    </div>
  </div>

  <div class="footer">
    AZAZAEL Ecosystem &bull; {cap['name']} Capability &bull; CAIN Standard Recording &bull; Generated June 2026
  </div>
</body>
</html>"""
    return html


def main():
    parser = argparse.ArgumentParser(
        description="Generate capability HTML templates for all AZAZAEL worm categories"
    )
    parser.add_argument("--list", action="store_true", help="List available capability categories")
    parser.add_argument("--category", help="Generate template for a single category (slug)")
    parser.add_argument("--output", default=str(OUTPUT_DIR), help="Output directory")

    args = parser.parse_args()

    output_path = Path(args.output)
    
    if args.list:
        print(f"\nAvailable capability categories ({len(CAPABILITIES)} total):")
        print(f"  {'Slug':<25} {'Name':<20} {'Theme':<12} Worms")
        print(f"  {'-'*25} {'-'*20} {'-'*12} {'-'*5}")
        for cap in CAPABILITIES:
            print(f"  {cap['slug']:<25} {cap['name']:<20} {cap['theme']:<12} {cap['worms']}")
        print()
        return

    if args.category:
        caps = [c for c in CAPABILITIES if c["slug"] == args.category]
        if not caps:
            print(f"  [FAIL] Category '{args.category}' not found. Use --list to see available.")
            return
    else:
        caps = CAPABILITIES

    output_path.mkdir(parents=True, exist_ok=True)
    
    generated = []
    for cap in caps:
        html = generate_template(cap)
        filepath = output_path / f"{cap['slug']}.html"
        filepath.write_text(html, encoding="utf-8")
        size_kb = filepath.stat().st_size // 1024
        print(f"  [OK] {cap['slug']:<25} {cap['name']:<20} ({size_kb} KB, theme: {cap['theme']})")
        generated.append(filepath)

    print(f"\n  Generated {len(generated)} templates in: {output_path}")
    print(f"  Total size: {sum(f.stat().st_size for f in generated) // 1024} KB")


if __name__ == "__main__":
    main()
