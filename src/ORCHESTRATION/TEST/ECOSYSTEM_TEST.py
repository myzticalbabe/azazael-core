import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))

#!/usr/bin/env python3
"""
ECOSYSTEM TEST SCRIPT
Tests TERENCE error resolution and activates HYDRA cluster
"""

import sys
from datetime import datetime
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

DESKTOP_PATH = "/c/Users/clint/Desktop"

sys.path.insert(0, f"{DESKTOP_PATH}/WORMZ")
sys.path.insert(0, f"{DESKTOP_PATH}/METAWORMS")
sys.path.insert(0, f"{DESKTOP_PATH}/TERENCE")


def print_header(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_section(title):
    print(f"\n--- {title} ---")


def test_terence():
    """Test TERENCE error resolution on ecosystem."""
    print_header("TERENCE ERROR RESOLUTION TEST")

    try:
        import terence

        print("TERENCE loaded successfully")

        t = terence.TERENCE()
        print(f"TERENCE version: {t.VERSION}")

        print_section("Scanning for errors...")
        folders = [
            f"{DESKTOP_PATH}/WORMZ",
            f"{DESKTOP_PATH}/METAWORMS",
            f"{DESKTOP_PATH}/TERENCE",
            f"{DESKTOP_PATH}/Azazael",
        ]

        errors_found = []
        for folder in folders:
            if os.path.exists(folder):
                for root, dirs, files in os.walk(folder):
                    for file in files:
                        if file.endswith(".py"):
                            filepath = os.path.join(root, file)
                            try:
                                with open(
                                    filepath, "r", encoding="utf-8", errors="ignore"
                                ) as f:
                                    content = f.read()

                                lines = content.split("\n")
                                for i, line in enumerate(lines, 1):
                                    for fix in terence.TERENCE.FIXES[:10]:
                                        if fix["match"] in line:
                                            errors_found.append(
                                                {
                                                    "file": filepath,
                                                    "line": i,
                                                    "error": fix["match"],
                                                    "fix": fix["fix"],
                                                    "desc": fix["desc"],
                                                }
                                            )
                            except Exception as exc:
                                note_failure("ECOSYSTEM_TEST:L79", exc)

        print(f"Found {len(errors_found)} potential issues")

        unique_issues = {}
        for e in errors_found:
            key = f"{e['file']}:{e['line']}"
            if key not in unique_issues:
                unique_issues[key] = e

        print(f"Unique issues: {len(unique_issues)}")

        if unique_issues:
            print("\nSample issues found:")
            for i, (key, issue) in enumerate(list(unique_issues.items())[:5]):
                print(f"  {i + 1}. {key}")
                print(f"     Issue: {issue['error']}")
                print(f"     Fix: {issue['fix']}")

        print_section("TERENCE Stats")
        print(f"  Fix patterns available: {len(terence.TERENCE.FIXES)}")
        print(f"  Files would be scanned: {len(folders)}")

        return True

    except Exception as e:
        print(f"TERENCE test failed: {e}")
        return False


def test_systemz():
    """Test SYSTEMZ worm."""
    print_header("SYSTEMZ WORM TEST")

    try:
        import SYSTEMZ

        sz = SYSTEMZ.SYSTEMZ()
        status = sz.get_status()

        print(f"  CPU: {status.cpu_percent}% ({status.cpu_count} cores)")
        print(
            f"  Memory: {status.memory_used_gb:.1f}GB / {status.memory_total_gb:.1f}GB"
        )
        print(f"  Disk: {status.disk_used_gb:.1f}GB / {status.disk_total_gb:.1f}GB")
        print(f"  Processes: {status.process_count}")
        print(f"  Uptime: {status.uptime_seconds / 3600:.1f} hours")

        return True
    except Exception as e:
        print(f"SYSTEMZ test failed: {e}")
        return False


def test_processz():
    """Test PROCESSZ worm."""
    print_header("PROCESSZ WORM TEST")

    try:
        import PROCESSZ

        pz = PROCESSZ.PROCESSZ()
        summary = pz.get_summary()

        print(f"  Total processes: {summary['total_processes']}")
        print(f"  CPU usage: {summary['total_cpu_percent']:.1f}%")
        print(f"  Memory usage: {summary['total_memory_percent']:.1f}%")
        print(f"  Running: {summary['by_status']['running']}")
        print(f"  Sleeping: {summary['by_status']['sleeping']}")

        return True
    except Exception as e:
        print(f"PROCESSZ test failed: {e}")
        return False


def test_diskz():
    """Test DISKZ worm."""
    print_header("DISKZ WORM TEST")

    try:
        import DISKZ

        dz = DISKZ.DISKZ()
        usage = dz.get_usage()

        print(f"  Disk: {usage.mountpoint}")
        print(f"  Total: {usage.total_gb}GB")
        print(f"  Used: {usage.used_gb}GB ({usage.percent}%)")
        print(f"  Free: {usage.free_gb}GB")

        io = dz.get_io_stats()
        print(f"  I/O Read: {io['read_mb']}MB")
        print(f"  I/O Write: {io['write_mb']}MB")

        return True
    except Exception as e:
        print(f"DISKZ test failed: {e}")
        return False


def test_networxz():
    """Test NETWORXZ worm."""
    print_header("NETWORXZ WORM TEST")

    try:
        import NETWORXZ

        nz = NETWORXZ.NETWORXZ()

        interfaces = nz.get_interfaces()
        print(f"  Interfaces found: {len(interfaces)}")

        for iface in interfaces[:3]:
            print(f"    {iface['Name']}: {len(iface['addresses'])} addresses")

        stats = nz.get_stats()
        print(f"  Bytes sent: {stats['bytes_sent_mb']}MB")
        print(f"  Bytes recv: {stats['bytes_recv_mb']}MB")

        return True
    except Exception as e:
        print(f"NETWORXZ test failed: {e}")
        return False


def test_clusterworms():
    """Test CLUSTERWORMS."""
    print_header("CLUSTERWORMS TEST")

    try:
        sys.path.insert(0, f"{DESKTOP_PATH}/WORMZ")
        import CLUSTERWORMS

        print("  Available clusters:")
        for name, info in CLUSTERWORMS.CLUSTER_REGISTRY.items():
            print(f"    {name}: {info['description']}")
            print(f"      Worms: {', '.join(info['worms'])}")

        return True
    except Exception as e:
        print(f"CLUSTERWORMS test failed: {e}")
        return False


def test_metaworms():
    """Test METAWORMS."""
    print_header("METAWORMS TEST")

    try:
        sys.path.insert(0, f"{DESKTOP_PATH}/METAWORMS")
        import METAWORMS

        print(f"  Version: {METAWORMS.METAWORMS.VERSION}")
        print("  Available metaworms:")
        for name in METAWORMS.METAWORMS.WORMS:
            print(f"    - {name}")

        return True
    except Exception as e:
        print(f"METAWORMS test failed: {e}")
        return False


def test_hydrometa():
    """Test HYDRAMETA."""
    print_header("HYDRATIC METAWORMS (HYDRAMETA) TEST")

    try:
        sys.path.insert(0, f"{DESKTOP_PATH}/METAWORMS")
        import HYDRAMETA

        hm = HYDRAMETA.HYDRAMETA()
        status = hm.get_status()

        print(f"  Version: {status['version']}")
        print(f"  Trial count: {status['trial_count']}")
        print(f"  Total runs: {status['total_runs']}")
        print(f"  Last run: {status['last_run']}")

        print_section("HYDRA ML Parameters")
        params = hm.optimizer.get_best_params()
        print(f"  Interval: {params.get('interval', 'N/A')}")
        print(f"  Timeout: {params.get('timeout', 'N/A')}")
        print(f"  Parallel: {params.get('parallel', 'N/A')}")

        return True
    except Exception as e:
        print(f"HYDRAMETA test failed: {e}")
        return False


def activate_hydra_cluster():
    """Activate HYDRA cluster with ML optimization."""
    print_header("HYDRA CLUSTER ACTIVATION")

    try:
        sys.path.insert(0, f"{DESKTOP_PATH}/METAWORMS")
        import HYDRAMETA

        hm = HYDRAMETA.HYDRAMETA()

        print_section("Initial Status")
        status = hm.get_status()
        print(f"  Total runs: {status['total_runs']}")
        print(f"  Trial count: {status['trial_count']}")

        print_section("Running Optimization Trials")
        print("  Running 3 optimization trials...")

        trials = hm.run_trials(3)
        print(f"  Completed {len(trials)} trials")

        best_trial = max(trials, key=lambda x: x.efficiency_score)
        print(f"  Best efficiency: {best_trial.efficiency_score:.1f}%")
        print(f"  Best worm sequence: {best_trial.worm_sequence}")

        print_section("Best Parameters")
        params = hm.optimizer.get_best_params()
        for k, v in params.items():
            print(f"    {k}: {v}")

        print_section("Analysis")
        analysis = hm.analyze_performance()
        print(f"  Total runs: {analysis['total_runs']}")
        print(f"  Total trials: {analysis['total_trials']}")
        print(f"  Best efficiency: {analysis['best_efficiency']:.1f}%")

        return True, best_trial, params

    except Exception as e:
        print(f"HYDRA activation failed: {e}")
        return False, None, None


def main():
    """Run all tests."""
    print("\n" + "#" * 70)
    print("#  Azazael ECOSYSTEM TEST SUITE")
    print(f"#  {datetime.now().isoformat()}")
    print("#" * 70)

    results = {}

    results["terence"] = test_terence()

    results["systemz"] = test_systemz()
    results["processz"] = test_processz()
    results["diskz"] = test_diskz()
    results["networxz"] = test_networxz()

    results["clusterworms"] = test_clusterworms()
    results["metaworms"] = test_metaworms()

    hydra_success = test_hydrometa()
    results["hydrometa"] = hydra_success

    if hydra_success:
        results["hydra_cluster"] = activate_hydra_cluster()[0]

        print_header("TEST SUMMARY")

        worm_tests = ["systemz", "processz", "diskz", "networxz"]
        passed_worms = sum(1 for t in worm_tests if results.get(t, False))
        print(f"  WORM Tests: {passed_worms}/{len(worm_tests)} passed")

        cluster_tests = ["clusterworms", "metaworms", "hydrometa"]
        passed_clusters = sum(1 for t in cluster_tests if results.get(t, False))
        print(f"  Cluster/Metaworm Tests: {passed_clusters}/{len(cluster_tests)} passed")

        print(f"  TERENCE: {'PASS' if results.get('terence') else 'FAIL'}")

        if results.get("hydra_cluster"):
            print("  HYDRA Cluster: ACTIVATED")

            print("\n" + "#" * 70)
            print("#  TEST SUITE COMPLETE")
            print("#" * 70)

            return results


if __name__ == "__main__":
    main()
