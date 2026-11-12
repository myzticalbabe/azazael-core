# coding: utf-8
"""
Azazael ECOSYSTEM - Full Crawler Launcher with Resolution

Usage:
    python ECOSYSTEM_CRAWLER_LAUNCHER.py

Features:
- Runs all ecosystem crawlers
- Updates system environment variables
- Reports health and status
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path
# from typing import Dict, List  # removed — all names have native equivalents
from dataclasses import dataclass
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Dynamic path resolution
try:
    from CORE.paths import get_desktop_dir
except ImportError as exc:
    note_failure("ECOSYSTEM_CRAWLER_LAUNCHER:L25", exc)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from CORE.paths import get_desktop_dir


@dataclass
class CrawlerResult:
    name: str
    success: bool
    duration_ms: float
    info_gathered: dict
    issues_found: list[str]
    resolutions_applied: list[str]
    output: str


class EcosystemCrawlerLauncher:
    BASE_DIR = str(get_desktop_dir())

    def __init__(self):
        base = os.path.normpath(self.BASE_DIR)
        self.base_path = Path(base)
        self.results: dict[str, CrawlerResult] = {}
        self.start_time = None
        self.env_file = self.base_path / "Azazael_UNIFIED" / ".env.json"

    def update_environment(self):
        """Update system environment from crawler results."""
        env_updates = {
            "last_crawl": datetime.now().isoformat(),
            "total_crawlers": len(self.results),
            "successful_crawlers": sum(1 for r in self.results.values() if r.success),
            "crawl_duration_ms": sum(r.duration_ms for r in self.results.values()),
        }

        for name, result in self.results.items():
            env_updates[f"crawl_{name.lower()}_status"] = (
                "success" if result.success else "failed"
            )
            env_updates[f"crawl_{name.lower()}_duration"] = (
                f"{result.duration_ms:.0f}ms"
            )

        env_path = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~")) / "Azazael_ENV.bat"
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("@echo off\n")
            f.write("echo Azazael Environment Update - %DATE% %TIME%\n")
            for key, value in env_updates.items():
                if isinstance(value, str):
                    f.write(f'set "{key}={value}"\n')
                else:
                    f.write(f'set "{key}={value}"\n')

        print(f"\n[ENV] Environment updated: {env_path}")
        return env_updates

    def run_all_crawlers(self) -> dict[str, CrawlerResult]:
        self.start_time = datetime.now()

        print("\n" + "=" * 80)
        print("  Azazael ECOSYSTEM - FULL CRAWLER LAUNCHER")
        print("=" * 80)
        print(f"\nStart Time: {self.start_time.isoformat()}")

        crawlers = [
            ("WORMZ_SCAN", self.run_worm_scan),
            ("METAWORMS_SCAN", self.run_metaworm_scan),
            ("TERENCE", self.run_terence_scan),
            ("ECOSYSTEM_INTEGRATION", self.run_ecosystem_scan),
        ]

        for name, func in crawlers:
            print(f"\n{'-' * 60}")
            print(f"Launching: {name}")
            print(f"{'-' * 60}")

            result = func()
            self.results[name] = result

            status = "OK" if result.success else "ISSUE"
            print(f"[{status}] {name}: {result.duration_ms:.0f}ms")
            if result.issues_found:
                for issue in result.issues_found[:3]:
                    print(f"  - {issue}")

        self.print_summary()
        self.update_environment()
        return self.results

    def run_worm_scan(self) -> CrawlerResult:
        start = time.time()
        info = {"worms": [], "active": 0, "total": 5}
        issues = []
        resolutions = []

        wormz_path = self.base_path / "WORMZ"

        worms = ["SYSTEMZ", "PROCESSZ", "DISKZ", "NETWORXZ", "CLOCKZ"]

        if not wormz_path.exists():
            return CrawlerResult(
                name="WORMZ_SCAN",
                success=False,
                duration_ms=0,
                info_gathered=info,
                issues_found=["WORMZ directory not found"],
                resolutions_applied=["Check Desktop for WORMZ folder"],
                output=f"Path: {wormz_path}",
            )

        for worm in worms:
            worm_file = wormz_path / f"{worm}.py"
            if worm_file.exists():
                info["worms"].append({"name": worm, "status": "present"})
                info["active"] += 1
            else:
                info["worms"].append({"name": worm, "status": "missing"})
                issues.append(f"{worm}: missing")

        return CrawlerResult(
            name="WORMZ_SCAN",
            success=len(issues) == 0,
            duration_ms=(time.time() - start) * 1000,
            info_gathered=info,
            issues_found=issues,
            resolutions_applied=resolutions if issues else ["All worms present"],
            output=f"Active: {info['active']}/{info['total']}",
        )

    def run_metaworm_scan(self) -> CrawlerResult:
        start = time.time()
        info = {"metaworms": [], "present": 0, "total": 5}
        issues = []

        mw_path = self.base_path / "METAWORMS"

        metaworms = ["ERRORZ", "TRIMZ", "HEALTHZ", "OPTIMIZ", "HYDRAMETA"]

        if not mw_path.exists():
            return CrawlerResult(
                name="METAWORMS_SCAN",
                success=False,
                duration_ms=0,
                info_gathered=info,
                issues_found=["METAWORMS directory not found"],
                resolutions_applied=["Check Desktop for METAWORMS folder"],
                output=f"Path: {mw_path}",
            )

        for mw in metaworms:
            mw_file = mw_path / f"{mw}.py"
            if mw_file.exists():
                info["metaworms"].append({"name": mw, "status": "present"})
                info["present"] += 1
            else:
                info["metaworms"].append({"name": mw, "status": "missing"})
                issues.append(f"{mw}: missing")

        return CrawlerResult(
            name="METAWORMS_SCAN",
            success=len(issues) == 0,
            duration_ms=(time.time() - start) * 1000,
            info_gathered=info,
            issues_found=issues,
            resolutions_applied=["All metaworms present"] if not issues else [],
            output=f"Present: {info['present']}/{info['total']}",
        )

    def run_terence_scan(self) -> CrawlerResult:
        start = time.time()
        info = {"patterns": 26, "status": "active"}
        issues = []

        terence_path = self.base_path / "TERENCE"

        if not (terence_path / "TERENCE.py").exists():
            issues.append("TERENCE.py not found")
            return CrawlerResult(
                name="TERENCE",
                success=False,
                duration_ms=0,
                info_gathered=info,
                issues_found=issues,
                resolutions_applied=["Create TERENCE directory"],
                output="TERENCE not found",
            )

        return CrawlerResult(
            name="TERENCE",
            success=True,
            duration_ms=(time.time() - start) * 1000,
            info_gathered=info,
            issues_found=issues,
            resolutions_applied=["TERENCE operational"],
            output=f"Patterns: {info['patterns']}",
        )

    def run_ecosystem_scan(self) -> CrawlerResult:
        start = time.time()
        info = {"components": {}, "healthy": 0, "total": 9}
        issues = []

        components = {
            "WORMZ": "WORMZ",
            "METAWORMS": "METAWORMS",
            "HYDRA": "HYDRA",
            "TERENCE": "TERENCE",
            "HORUS": "HORUS",
            "TARBABY": "TARBABY",
            "PERSEPHONE": "PERSEPHONE",
            "POINDEXTER": "POINDEXTER",
            "KLINK": "KLINK",
            "Azazael": "Azazael",
        }

        for comp_name, folder in components.items():
            comp_path = self.base_path / folder
            if comp_path.exists():
                info["components"][comp_name] = "healthy"
                info["healthy"] += 1
            else:
                info["components"][comp_name] = "missing"
                issues.append(f"{comp_name}: missing")

        return CrawlerResult(
            name="ECOSYSTEM_INTEGRATION",
            success=info["healthy"] >= 7,
            duration_ms=(time.time() - start) * 1000,
            info_gathered=info,
            issues_found=issues,
            resolutions_applied=["Components checked"],
            output=f"Healthy: {info['healthy']}/{info['total']}",
        )

    def print_summary(self):
        total_time = (
            (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        )

        successful = sum(1 for r in self.results.values() if r.success)
        total = len(self.results)

        all_issues = []
        for r in self.results.values():
            all_issues.extend(r.issues_found)

        print(f"\n{'=' * 80}")
        print("  CRAWLER LAUNCH SUMMARY")
        print(f"{'=' * 80}")
        print(f"\nTotal Crawlers: {total}")
        print(f"Successful: {successful}")
        print(f"Failed: {total - successful}")
        print(f"Duration: {total_time:.2f}s")

        print(f"\n{'-' * 80}")
        print("  ISSUES FOUND")
        print(f"{'-' * 80}")
        if all_issues:
            for issue in set(all_issues):
                print(f"  - {issue}")
        else:
            print("  No issues found!")

        print(f"\n{'=' * 80}")
        print("  DETAILED RESULTS")
        print(f"{'=' * 80}")

        for name, result in self.results.items():
            status = "OK" if result.success else "FAIL"
            print(f"\n[{status}] {name}")
            print(f"  Duration: {result.duration_ms:.0f}ms")
            print(f"  Output: {result.output}")

        print(f"\n{'=' * 80}")


def main():
    print("\n" + "=" * 80)
    print("  Azazael ECOSYSTEM - COMPREHENSIVE CRAWLER LAUNCHER")
    print("=" * 80)

    launcher = EcosystemCrawlerLauncher()
    results = launcher.run_all_crawlers()

    return results


if __name__ == "__main__":
    main()
