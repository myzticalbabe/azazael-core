import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# coding: utf-8
"""
Harness

Module for ml_platforms functionality.

Functions:

Usage:
    >>> import harness
    >>> harness.function()

Author: Auto-generated
Date: 2026-02-10
"""

#!/usr/bin/env python3
r"""
ML Platform Harness - Unified Interface for All Operations

Usage:
                python harness.py <command> [options]

Commands:
                analyze      Run framework analysis and Q-learning
                convert      Convert models between formats
                recommend    Get framework recommendations
                monitor      Run health monitoring
                status       Show system status
                models       list available models
                verify       Run full verification
                install      Install dependencies

Options:
                --model, -m      Model file
                --framework, -f   Target framework
                --task, -t       Task type (cv, nlp, tabular, rl, generative)
                --output, -o     Output directory
                --verbose, -v    Verbose output
                --json           JSON output format

Examples:
                python harness.py analyze
                python harness.py convert -m model.gguf -f pytorch
                python harness.py recommend -t cv
                python harness.py monitor --verbose
                python harness.py status --json
                python harness.py models
"""

import sys
import argparse
import json
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Auto-add ml_platforms to path (Azazael environment)
BASE_DIR = Path(__file__).parent.parent
ML_PLATFORMS_DIR = Path(__file__).parent

if str(ML_PLATFORMS_DIR) not in sys.path:
    sys.path.insert(0, str(ML_PLATFORMS_DIR))

__version__ = "2.0.0"
__path__ = str(Path(__file__).parent)  # type: ignore[reportAssignmentType]


def get_version():
    return "2.0.0"


def cmd_analyze(args):
    """Run framework analysis."""
    print("Running framework analysis...")

    try:
        from run_qanalysis import run_q_analysis  # type: ignore[reportAttributeAccessIssue]
    except ImportError:
        def run_q_analysis():
            print("run_q_analysis not available")

    try:
        run_q_analysis()
    except Exception as e:
        print(f"Analysis error: {e}")
        return 1

    return 0


def cmd_convert(args):
    """Convert model to different format."""
    print(f"Converting {args.model} to {args.framework}")

    # Expand environment variables
    model_path = os.path.expandvars(args.model)

    try:
        from universal_converter import UniversalConverter

        converter = UniversalConverter()
        result = converter.convert(model_path, args.framework, args.output)

        if result.success:
            print(f"Success: {result.output_path}")
        else:
            print(f"Failed: {result.errors}")
            return 1

    except Exception as e:
        print(f"Conversion error: {e}")
        return 1

    return 0


def cmd_recommend(args):
    """Get framework recommendations."""
    print(f"Getting recommendations for: {args.task}")

    try:
        from improvements.qlearning_table import AdaptivePlatformSelector, TaskType

        task_map = {
            "cv": TaskType.COMPUTER_VISION,
            "nlp": TaskType.NLP,
            "tabular": TaskType.TABULAR,
            "rl": TaskType.REINFORCEMENT,
            "generative": TaskType.GENERATIVE,
        }

        task = task_map.get(args.task, TaskType.COMPUTER_VISION)
        selector = AdaptivePlatformSelector()

        recommendations = selector.get_recommendations(task, top_k=3)

        if args.json:
            print(
                json.dumps(
                    [
                        {"task": args.task, "recommendations": r}
                        for r in recommendations
                    ],
                    indent=2,
                )
            )
        else:
            print(f"\nRecommendations for {args.task}:")
            print("-" * 40)
            for r in recommendations:
                print(
                    f"  {r['framework']}: {r.get('overall_score', r.get('q_value', 0)):.3f}"
                )

    except Exception as e:
        print(f"Recommendation error: {e}")
        return 1

    return 0


def cmd_monitor(args):
    """Run health monitoring."""
    print("Running health monitor...")

    try:
        from health_monitor import PlatformHealthMonitor

        monitor = PlatformHealthMonitor()

        if args.verbose:
            results = monitor.run_health_check()
            for name, check in results.items():
                print(f"  {name}: {check.status.value}")
        else:
            monitor.print_status()

    except Exception as e:
        print(f"Monitor error: {e}")
        return 1

    return 0


def cmd_status(args):
    """Show system status."""
    print("System Status")
    print("=" * 50)

    status = {
        "version": get_version(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "path": str(ML_PLATFORMS_DIR),
    }

    # Check frameworks
    frameworks = {}
    for name, import_name in [
        ("torch", "torch"),
        ("tensorflow", "tensorflow"),
        ("jax", "jax"),
        ("numpy", "numpy"),
        ("scipy", "scipy"),
    ]:
        try:
            mod = __import__(import_name)
            frameworks[name] = mod.__version__
        except ImportError as exc:
            note_failure("harness:L209", exc)
            frameworks[name] = "not installed"

    status["frameworks"] = frameworks  # type: ignore[reportArgumentType]

    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(f"Version: {status['version']}")
        print(f"Python: {status['python']}")
        print(f"Platform: {status['platform']}")
        print(f"Path: {status['path']}")
        print("\nFrameworks:")
        for name, version in frameworks.items():
            icon = "[OK]" if version != "not installed" else "[--]"
            print(f"  {icon} {name}: {version}")

    return 0


def cmd_models(args):
    """list available models."""
    print("Available Models")
    print("=" * 50)

    model_paths = [
        Path(os.path.expandvars("%USERPROFILE%")) / ".lmstudio" / "models",
        Path(os.path.expandvars("%USERPROFILE%")) / "llama.cpp" / "models",
    ]

    models = []
    for path in model_paths:
        if path.exists():
            for gguf in path.glob("**/*.gguf"):
                models.append(
                    {
                        "path": str(gguf),
                        "size_gb": round(gguf.stat().st_size / (1024**3), 2),
                        "name": gguf.stem[:30],
                    }
                )

    if args.json:
        print(json.dumps(models, indent=2))
    else:
        for m in models:
            print(f"  {m['name']:<30} {m['size_gb']} GB")

    return 0


def cmd_verify(args):
    """Run full verification."""
    print("Running full verification...")

    try:
        from verify_moe import verify_system

        verify_system()
    except Exception as e:
        print(f"Verification error: {e}")
        return 1

    return 0


def main():
    parser = argparse.ArgumentParser(
    description="ML Platform Harness - Unified interface",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=f"""
Location: {str(Path(__file__).parent)}

Examples:
                python harness.py analyze
                python harness.py convert -m model.gguf -f pytorch
                python harness.py recommend -t cv
                python harness.py monitor --verbose
                python harness.py status --json
                python harness.py models
                                """,
    )

    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {get_version()}"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # analyze
    subparsers.add_parser("analyze", help="Run framework analysis")

    # convert
    p_convert = subparsers.add_parser("convert", help="Convert model")
    p_convert.add_argument("-m", "--model", required=True, help="Model file")
    p_convert.add_argument("-f", "--framework", required=True, help="Target framework")
    p_convert.add_argument("-o", "--output", help="Output directory")

    # recommend
    p_recommend = subparsers.add_parser("recommend", help="Get recommendations")
    p_recommend.add_argument(
        "-",
        "--task",
        required=True,
        choices=["cv", "nlp", "tabular", "rl", "generative"],
    )

    # monitor
    p_monitor = subparsers.add_parser("monitor", help="Run health monitor")
    p_monitor.add_argument("-v", "--verbose", action="store_true")

    # status
    p_status = subparsers.add_parser("status", help="Show system status")

    # models
    p_models = subparsers.add_parser("models", help="list available models")

    # verify
    p_verify = subparsers.add_parser("verify", help="Run full verification")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "analyze": cmd_analyze,
        "convert": cmd_convert,
        "recommend": cmd_recommend,
        "monitor": cmd_monitor,
        "status": cmd_status,
        "models": cmd_models,
        "verify": cmd_verify,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())


