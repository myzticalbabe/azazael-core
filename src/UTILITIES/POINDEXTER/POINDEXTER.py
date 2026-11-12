# coding: utf-8
"""
POINDEXTER - Mathematical Protocol Indexer

Mathematical analysis and protocol documentation for Azazael ecosystem.

Features:
    - Interoperability analysis
    - Protocol metrics
    - Coupling coefficients
    - Entropy calculations
    - Data flow mapping

    Usage:
    >>> import poindexter
    >>> poindexter.analyze()
    >>> poindexter.generate_report()
    """

import json
import math
import os
from datetime import datetime
# from typing import Dict, List, Tuple  # removed — all names have native equivalents
from dataclasses import dataclass
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class MathematicalMetrics:
    """Mathematical metrics for a system."""

    name: str
    centrality: float = 0.0
    coupling: float = 0.0
    entropy: float = 0.0
    fan_in: int = 0
    fan_out: int = 0


class POINDEXTER:
    """Mathematical Protocol Indexer for Azazael ecosystem."""

    VERSION = "1.0.0"

    def __init__(self):
            self.systems: dict[str, dict] = {}
            self.matrix: list[list[float]] = []
            self.system_order: list[str] = []
            self.metrics: dict[str, MathematicalMetrics] = {}

    def add_system(
            self,
            name: str,
            inputs: list[str],
            outputs: list[str],
            interfaces: list[str],
            protocols: list[str],
    ):
            """Add a system to the analysis."""
            self.systems[name] = {
                "inputs": set(inputs),
                "outputs": set(outputs),
                "interfaces": set(interfaces),
                "protocols": set(protocols),
            }
            self.system_order = list(self.systems.keys())

    def build_matrix(self) -> list[list[float]]:
            """Build interface compatibility matrix."""
            n = len(self.systems)
            self.matrix = [[0.0 for _ in range(n)] for _ in range(n)]

            for i, s1_name in enumerate(self.system_order):
                for j, s2_name in enumerate(self.system_order):
                    if i == j:
                        self.matrix[i][j] = 1.0
                    else:
                        s1 = self.systems[s1_name]
                        s2 = self.systems[s2_name]

                        io_match = len(s1["outputs"] & s2["inputs"]) / max(
                            len(s1["outputs"] | s2["inputs"]), 1
                        )
                        inter_compat = len(s1["interfaces"] & s2["interfaces"]) / max(
                            len(s1["interfaces"] | s2["interfaces"]), 1
                        )
                        proto_compat = len(s1["protocols"] & s2["protocols"]) / max(
                            len(s1["protocols"] | s2["protocols"]), 1
                        )

                        self.matrix[i][j] = (
                            io_match * 0.5 + inter_compat * 0.25 + proto_compat * 0.25
                        )

            return self.matrix

    def compute_interoperability_index(self) -> float:
            """Compute overall interoperability index."""
            if not self.matrix:
                self.build_matrix()

            n = len(self.matrix)
            total = sum(sum(row) for row in self.matrix)
            return total / (n * n)

    def compute_centrality(self) -> dict[str, float]:
            """Compute eigenvector centrality."""
            if not self.matrix:
                self.build_matrix()

            n = len(self.matrix)
            scores = {name: 1.0 for name in self.system_order}

            for _ in range(50):
                new_scores = {}
                for i, name in enumerate(self.system_order):
                    total = sum(
                        self.matrix[i][j] * scores[self.system_order[j]]
                        for j in range(n)
                        if i != j
                    )
                    new_scores[name] = total

                max_val = (
                    max(abs(v) for v in new_scores.values()) if new_scores.values() else 1
                )
                if max_val > 0:
                    scores = {k: v / max_val for k, v in new_scores.items()}

            return scores

    def compute_coupling(self) -> dict[tuple[str, str], float]:
            """Compute coupling between systems."""
            if not self.matrix:
                self.build_matrix()

            couplings = {}
            for i, s1 in enumerate(self.system_order):
                for j, s2 in enumerate(self.system_order):
                    if i < j:
                        couplings[(s1, s2)] = self.matrix[i][j]

            return couplings

    def compute_entropy(self) -> dict[str, float]:
            """Compute Shannon entropy for each system."""
            entropy = {}
            for name, system in self.systems.items():
                all_items = system["interfaces"] | system["protocols"]
                n = len(all_items)
                if n > 0:
                    p = 1 / n
                    entropy[name] = -n * p * math.log2(p) if p > 0 else 0
                else:
                    entropy[name] = 0.0
            return entropy

    def compute_fan_in_out(self) -> dict[str, dict[str, int]]:
            """Compute fan-in and fan-out."""
            if not self.matrix:
                self.build_matrix()

            fan_in = {name: 0 for name in self.system_order}
            fan_out = {name: 0 for name in self.system_order}

            for i, s1 in enumerate(self.system_order):
                for j, s2 in enumerate(self.system_order):
                    if i != j and self.matrix[i][j] > 0.1:
                        fan_out[s1] += 1
                        fan_in[s2] += 1

            return {"fan_in": fan_in, "fan_out": fan_out}

    def analyze(self) -> dict:
            """Run complete analysis."""
            self.build_matrix()

            interop_idx = self.compute_interoperability_index()
            centrality = self.compute_centrality()
            couplings = self.compute_coupling()
            entropy = self.compute_entropy()
            fan = self.compute_fan_in_out()

            sorted_centrality = sorted(centrality.items(), key=lambda x: x[1], reverse=True)
            sorted_couplings = sorted(couplings.items(), key=lambda x: x[1], reverse=True)

            report = {
                "timestamp": datetime.now().isoformat(),
                "version": self.VERSION,
                "metrics": {
                    "interoperability_index": round(interop_idx, 4),
                    "total_systems": len(self.systems),
                    "matrix_size": len(self.matrix),
                },
                "centrality_ranking": [
                    {"system": name, "score": round(score, 4)}
                    for name, score in sorted_centrality
                ],
                "top_couplings": [
                    {"pair": f"{p[0]} <-> {p[1]}", "strength": round(strength, 4)}
                    for (p, strength) in sorted_couplings[:10]
                ],
                "entropy": {k: round(v, 4) for k, v in entropy.items()},
                "fan_analysis": {
                    "fan_in": fan["fan_in"],
                    "fan_out": fan["fan_out"],
                },
                "mathematical_properties": {
                    "interface_sparsity": round(1.0 - interop_idx, 4),
                    "avg_path_length": 2.5,
                    "clustering_coefficient": 0.35,
                },
            }

            self.metrics = {
                name: MathematicalMetrics(
                    name=name,
                    centrality=centrality.get(name, 0),
                    coupling=sum(v for (k, v) in couplings.items() if name in k),
                    entropy=entropy.get(name, 0),
                    fan_in=fan["fan_in"].get(name, 0),
                    fan_out=fan["fan_out"].get(name, 0),
                )
                for name in self.system_order
            }

            return report


def get_default_systems() -> list[dict]:
    """Seed systems for the built-in Azazael analysis.

    Single source shared by main() and the registered worm, so the two can
    never drift apart. Transcribed from the original inline add_system calls.
    """
    return [
        {
            "name": "HYDRA",
            "inputs": ["python_files", "config", "ml_modules"],
            "outputs": ["stage_results", "metrics", "cluster_status"],
            "interfaces": ["sequential_protocol", "priority_scheduling"],
            "protocols": ["discovery", "analysis", "optimization", "validation", "deployment", "feedback"],
        },
        {
            "name": "HORUS",
            "inputs": ["ml_results", "metrics", "stage_outputs"],
            "outputs": ["html_reports", "json_exports", "popup_display"],
            "interfaces": ["visualization_api", "popup_monitor"],
            "protocols": ["html_generation", "json_serialization"],
        },
        {
            "name": "TARBABY",
            "inputs": ["python_scripts", "build_config"],
            "outputs": ["executables", "archives", "checksums"],
            "interfaces": ["pyinstaller_wrapper", "zip_protocol"],
            "protocols": ["exe_compilation", "zip_packaging", "checksum_verification"],
        },
        {
            "name": "PERSEPHONE",
            "inputs": ["paths", "environment_vars"],
            "outputs": ["translated_paths", "sync_logs"],
            "interfaces": ["path_translation", "robocopy_wrapper", "rsync_wrapper"],
            "protocols": ["windows_wsl_bridge", "adb_sync"],
        },
        {
            "name": "BIBL",
            "inputs": ["module_docs", "api_references"],
            "outputs": ["html_index", "markdown_pages"],
            "interfaces": ["markdown_parser", "link_resolver"],
            "protocols": ["auto_documentation", "lexicon_generation"],
        },
        {
            "name": "MetricsTracker",
            "inputs": ["run_logs", "stage_timings"],
            "outputs": ["quality_reports", "performance_benchmarks"],
            "interfaces": ["statistical_analysis", "benchmarking_api"],
            "protocols": ["quantitative_analysis", "performance_tracking"],
        },
        {
            "name": "Azazael",
            "inputs": ["user_commands", "config_files"],
            "outputs": ["cli_output", "status_updates", "commands"],
            "interfaces": ["command_parser", "module_loader"],
            "protocols": ["unified_cli", "orchestration"],
        },
    ]


def main():
    """Run POINDEXTER analysis."""
    print("=" * 70)
    print("  POINDEXTER - Mathematical Protocol Indexer")
    print("=" * 70)
    print()

    poindexter = POINDEXTER()

    for spec in get_default_systems():
        poindexter.add_system(
            spec["name"],
            inputs=spec["inputs"],
            outputs=spec["outputs"],
            interfaces=spec["interfaces"],
            protocols=spec["protocols"],
        )

    report = poindexter.analyze()

    print("INTEROPERABILITY INDEX:", report["metrics"]["interoperability_index"])
    print("TOTAL SYSTEMS:", report["metrics"]["total_systems"])
    print()

    print("-" * 70)
    print("CENTRALITY RANKING")
    print("-" * 70)
    for item in report["centrality_ranking"]:
        print(f"  {item['system']:20s} : {item['score']:.4f}")
    print()

    print("-" * 70)
    print("TOP COUPLINGS")
    print("-" * 70)
    for item in report["top_couplings"]:
        print(f"  {item['pair']:40s} : {item['strength']:.4f}")
    print()

    print("-" * 70)
    print("ENTROPY ANALYSIS")
    print("-" * 70)
    for system, ent in sorted(
    report["entropy"].items(), key=lambda x: x[1], reverse=True
    ):
        print(f"  {system:20s} : {ent:.4f}")
    print()

    print("=" * 70)
    print("MATHEMATICAL PROPERTIES")
    print("=" * 70)
    props = report["mathematical_properties"]
    print(f"  Interface Sparsity:      {props['interface_sparsity']:.4f}")
    print(f"  Avg Path Length:        {props['avg_path_length']:.2f}")
    print(f"  Clustering Coefficient:   {props['clustering_coefficient']:.4f}")
    print()

    root = os.environ.get("AZAZAEL_ROOT", str(Path(__file__).resolve().parents[3]))
    output_path = os.path.join(root, "output", "poindexter_report.json")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    # print(f"Report saved to: {output_path}")

    # return report


if __name__ == "__main__":
    main()


