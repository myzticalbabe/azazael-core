# coding: utf-8
"""
OPTIMIZ - Optimization Meta-Worm

Optimization worm that analyzes and improves system performance.

Features:
- Performance analysis
- Resource optimization recommendations
- Bottleneck detection
- Efficiency scoring
- Auto-tuning suggestions

Usage:
    >>> import optimiz
    >>> optimiz.analyze()           # Full analysis
    >>> optimiz.get_score()          # Get efficiency score
    >>> optimiz.recommend()         # Get recommendations
"""

import json
from datetime import datetime
# from typing import List  # removed — all names have native equivalents
from dataclasses import dataclass, field
from typing import List
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ── Worm Registry ─────────────────────────────────────────────────────
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("OPTIMIZ:L30", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


@dataclass
class OptimizationResult:
    """Optimization analysis result."""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    overall_score: float = 0.0
    cpu_efficiency: float = 0.0
    memory_efficiency: float = 0.0
    disk_efficiency: float = 0.0
    network_efficiency: float = 0.0
    bottlenecks: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


@register_worm(category="optimization", priority=5, cooldown_seconds=45.0, description="System optimization meta-worm — bottleneck detection, efficiency scoring, tuning recommendations")
class OPTIMIZ:
    """Optimization Meta-Worm."""

    VERSION = "1.0.0"

    def __init__(self):
        pass

    def _get_cpu_efficiency(self) -> float:
        """Calculate CPU efficiency."""
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        return max(0, 100 - cpu_percent)

    def _get_memory_efficiency(self) -> float:
        """Calculate memory efficiency."""
        import psutil
        mem = psutil.virtual_memory()
        return max(0, 100 - mem.percent)

    def _get_disk_efficiency(self) -> float:
        """Calculate disk efficiency."""
        import psutil
        disk = psutil.disk_usage("/")
        return max(0, 100 - disk.percent)

    def _get_network_efficiency(self) -> float:
        """Calculate network efficiency."""
        import psutil
        counters = psutil.net_io_counters()
        return min(
            100, counters.bytes_recv / (1024**2) + counters.bytes_sent / (1024**2)
        )

    def _detect_bottlenecks(
        self, cpu_e: float, mem_e: float, disk_e: float
    ) -> list[str]:
        """Detect system bottlenecks."""
        bottlenecks = []
        if cpu_e < 30:
            bottlenecks.append("High CPU usage detected")
        if mem_e < 30:
            bottlenecks.append("High memory usage detected")
        if disk_e < 30:
            bottlenecks.append("High disk usage detected")
        return bottlenecks

    def _generate_recommendations(
        self, cpu_e: float, mem_e: float, disk_e: float
    ) -> list[str]:
        """Generate optimization recommendations."""
        recs = []
        if cpu_e < 50:
            recs.append("Consider reducing CPU-intensive processes")
        if mem_e < 50:
            recs.append("Free up memory or add more RAM")
        if disk_e < 50:
            recs.append("Clean up disk space or extend storage")
        if cpu_e > 80 and mem_e > 80:
            recs.append("System is well-optimized!")
        return recs

    def analyze(self) -> OptimizationResult:
        """Perform full optimization analysis."""
        cpu_eff = self._get_cpu_efficiency()
        mem_eff = self._get_memory_efficiency()
        disk_eff = self._get_disk_efficiency()
        net_eff = self._get_network_efficiency()

        overall = (cpu_eff + mem_eff + disk_eff + net_eff) / 4

        bottlenecks = self._detect_bottlenecks(cpu_eff, mem_eff, disk_eff)
        recommendations = self._generate_recommendations(cpu_eff, mem_eff, disk_eff)

        return OptimizationResult(
            overall_score=round(overall, 2),
            cpu_efficiency=round(cpu_eff, 2),
            memory_efficiency=round(mem_eff, 2),
            disk_efficiency=round(disk_eff, 2),
            network_efficiency=round(net_eff, 2),
            bottlenecks=bottlenecks,
            recommendations=recommendations,
        )

    def get_score(self) -> float:
        """Get overall optimization score."""
        return self.analyze().overall_score

    def recommend(self) -> list[str]:
        """Get optimization recommendations."""
        return self.analyze().recommendations


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(description="OPTIMIZ - Optimization Meta-Worm")
    parser.add_argument("--analyze", action="store_true", help="Full analysis")
    parser.add_argument("--score", action="store_true", help="Get efficiency score")
    parser.add_argument("--recommend", action="store_true", help="Get recommendations")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    opt = OPTIMIZ()

    if args.json:
        if args.analyze:
            result = opt.analyze()
            print(json.dumps(result.__dict__, indent=2))
        elif args.score:
            print(json.dumps({"score": opt.get_score()}, indent=2))
        elif args.recommend:
            print(json.dumps(opt.recommend(), indent=2))
        else:
            print(json.dumps(opt.analyze().__dict__, indent=2))
    else:
        if args.analyze:
            r = opt.analyze()
            print("Optimization Analysis")
            print(f"  Overall Score: {r.overall_score}/100")
            print(f"  CPU Efficiency: {r.cpu_efficiency}/100")
            print(f"  Memory Efficiency: {r.memory_efficiency}/100")
            print(f"  Disk Efficiency: {r.disk_efficiency}/100")
            for b in r.bottlenecks:
                print(f"  [!] {b}")
            for rec in r.recommendations:
                print(f"  [>] {rec}")
        elif args.score:
            print(f"Efficiency Score: {opt.get_score()}/100")
        elif args.recommend:
            for rec in opt.recommend():
                print(f"  [>] {rec}")
        else:
            print(f"Score: {opt.get_score()}/100")


if __name__ == "__main__":
    main()
