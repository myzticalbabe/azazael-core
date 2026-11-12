#!/usr/bin/env python3
"""
KLINK - Azazael Ecosystem Unified Command Launcher

Cross-platform CLI for all Azazael ecosystem commands.
Supports: Windows (CMD/PowerShell), Linux, Debian, macOS, WSL

Usage:
    python klink.py --help
    python klink.py status
    python klink.py worms
    python klink.py hydra all
    python klink.py optimize

Installation:
    chmod +x klink.py
    sudo cp klink.py /usr/local/bin/klink
"""

import os
import sys
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List
import urllib.request

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Configuration
Azazael_ROOT = os.environ.get("AZAZAEL_ROOT", os.environ.get("Azazael_ROOT", str(Path(__file__).resolve().parents[3])))

# Command mappings
COMMANDS = {
    # Core
    "status": {"func": "cmd_status", "help": "Show ecosystem status"},
    "version": {"func": "cmd_version", "help": "Show version info"},
    "list": {"func": "cmd_list", "help": "list all commands"},
    # WORMZ
    "worms": {"func": "cmd_worms", "help": "Run all WORMZ worms"},
    "systemz": {"func": "cmd_systemz", "help": "System information worm"},
    "processz": {"func": "cmd_processz", "help": "Process management worm"},
    "diskz": {"func": "cmd_diskz", "help": "Disk analysis worm"},
    "networkz": {"func": "cmd_networkz", "help": "Network analysis worm"},
    "clockz": {"func": "cmd_clockz", "help": "Time synchronization worm"},
    # METAWORMS
    "metaworms": {"func": "cmd_metaworms", "help": "Run all METAWORMS"},
    "errorz": {"func": "cmd_errorz", "help": "Error tracking metaworm"},
    "trimz": {"func": "cmd_trimz", "help": "Optimization metaworm"},
    "healthz": {"func": "cmd_healthz", "help": "Health monitoring metaworm"},
    "hydrometa": {"func": "cmd_hydrometa", "help": "HYDRA metaworm"},
    # HYDRA
    "hydra": {"func": "cmd_hydra", "help": "Run HYDRA cluster", "has_args": True},
    # Ecosystem
    "terence": {"func": "cmd_terence", "help": "TERENCE error resolver"},
    "horus": {"func": "cmd_horus", "help": "HORUS output formatter"},
    "tarbaby": {"func": "cmd_tarbaby", "help": "Binary builder"},
    "poindexter": {"func": "cmd_poindexter", "help": "Mathematical indexer"},
    "persephone": {"func": "cmd_persephone", "help": "Cross-platform utilities"},
    # Optimization
    "optimize": {"func": "cmd_optimize", "help": "Run ecosystem optimizer"},
    "monitor": {"func": "cmd_monitor", "help": "Launch live monitoring popup"},
    "analyze": {"func": "cmd_analyze", "help": "Analyze batch files"},
    # Full runs
    "all": {"func": "cmd_all", "help": "Run full ecosystem"},
    "full": {"func": "cmd_all", "help": "Run full ecosystem"},
    # EVE UNLEASHED
    "eve": {"func": "cmd_eve", "help": "Eve Unleashed autonomous control", "has_args": True},
}


def log(msg, level="INFO"):
    """Print colored log message."""
    colors = {
        "INFO": "\033[94m",  # Blue
        "OK": "\033[92m",  # Green
        "WARN": "\033[93m",  # Yellow
        "ERROR": "\033[91m",  # Red
        "NC": "\033[0m",  # No Color
    }
    if sys.platform == "win32":
        colors = {k: "" for k in colors}

    print(f"{colors.get(level, colors['INFO'])}[{level}] {msg}{colors['NC']}")


def run_python(script_path, args=None):
    """Run a Python script - searches multiple locations."""
    search_paths = [
        Path(Azazael_ROOT) / script_path,
        Path(Azazael_ROOT) / "src" / script_path,
        Path(Azazael_ROOT) / "src" / "CORE" / "WORMS" / script_path,
        Path(Azazael_ROOT) / "src" / "HYDRA" / script_path,
        Path(Azazael_ROOT) / "src" / "ORCHESTRATION" / script_path,
        Path(Azazael_ROOT) / "src" / "UTILITIES" / script_path,
        Path(Azazael_ROOT) / "src" / "META" / script_path,
        Path(Azazael_ROOT) / "src" / "CODEX" / script_path,
    ]
    full_path = None
    for p in search_paths:
        if p.exists():
            full_path = p
            break
    # Fallback: strip directory prefix and search by basename (handles WORMZ/SYSTEMZ.py vs WORMS/SYSTEMZ.py)
    if not full_path and "/" in script_path:
        basename = script_path.rsplit("/", 1)[-1]
        base_dirs = [
            Path(Azazael_ROOT),
            Path(Azazael_ROOT) / "src",
            Path(Azazael_ROOT) / "src" / "CORE" / "WORMS",
            Path(Azazael_ROOT) / "src" / "HYDRA",
            Path(Azazael_ROOT) / "src" / "ORCHESTRATION",
            Path(Azazael_ROOT) / "src" / "UTILITIES",
            Path(Azazael_ROOT) / "src" / "META",
            Path(Azazael_ROOT) / "src" / "CODEX",
        ]
        for base in base_dirs:
            candidate = base / basename
            if candidate.exists():
                full_path = candidate
                break
    if not full_path:
        return False, f"Script not found: {script_path} (searched {len(search_paths)} locations)"

    cmd = [sys.executable, str(full_path)] + (args or [])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        ok = result.returncode == 0
        msg = "Success" if ok else f"Exit {result.returncode}: {result.stderr[:200].strip()}"
        log(f"{script_path}: {msg}", "OK" if ok else "ERROR")
        return ok, msg
    except Exception as e:
        note_failure("klink:L126", e)
        return False, f"Error: {e}"


def cmd_status():
    """Show ecosystem status."""
    print("\n" + "=" * 60)
    print("  Azazael ECOSYSTEM STATUS")
    print("=" * 60)
    print(f"\nPlatform: {sys.platform}")
    print(f"Azazael_ROOT: {Azazael_ROOT}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Commands available: {len(COMMANDS)}")
    print("\nComponents:")
    component_paths = {
        "WORMZ": [Path(Azazael_ROOT) / "src" / "CORE" / "WORMS"],
        "METAWORMS": [Path(Azazael_ROOT) / "src" / "META"],
        "HYDRA": [Path(Azazael_ROOT) / "src" / "HYDRA"],
        "TERENCE": [Path(Azazael_ROOT) / "src" / "UTILITIES" / "TERENCE"],
        "HORUS": [Path(Azazael_ROOT) / "src" / "ORCHESTRATION" / "HORUS.py"],
        "TARBABY": [Path(Azazael_ROOT) / "src" / "UTILITIES" / "TARBABY"],
        "POINDEXTER": [Path(Azazael_ROOT) / "src" / "UTILITIES" / "POINDEXTER"],
        "PERSEPHONE": [Path(Azazael_ROOT) / "src" / "UTILITIES" / "PERSEPHONE"],
        "BIBL": [Path(Azazael_ROOT) / "src" / "ORCHESTRATION" / "BIBL",
                 Path(Azazael_ROOT) / "src" / "UTILITIES" / "BIBL"],
    }
    for name, paths in component_paths.items():
        exists = "+" if any(p.exists() for p in paths) else "-"
        print(f"  [{exists}] {name}")
    print("=" * 60)


def cmd_version():
    """Show version."""
    print("Azazael Ecosystem v5.0.0")
    print(f"Platform: {sys.platform}")
    print(f"Python: {sys.version.split()[0]}")


def cmd_list():
    """list all commands."""
    print("\nAvailable Commands:")
    print("-" * 40)
    for cmd, info in COMMANDS.items():
        print(f"  {cmd:<15} - {info['help']}")
    print("-" * 40)


# WORMZ Commands
def cmd_worms():
    run_python("WORMZ/CONCURRENCY.py", ["--scan"])


def cmd_systemz():
    run_python("WORMZ/SYSTEMZ.py", ["--status"])


def cmd_processz():
    run_python("WORMZ/PROCESSZ.py", ["--status"])


def cmd_diskz():
    run_python("WORMZ/DISKZ.py", ["--status"])


def cmd_networkz():
    run_python("WORMZ/NETWORXZ.py", ["--status"])


def cmd_clockz():
    run_python("WORMZ/CLOCKZ.py", ["--status"])


# METAWORMS Commands
def cmd_metaworms():
    run_python("METAWORMS/HYDRAMETA.py", ["--all"])


def cmd_errorz():
    run_python("METAWORMS/ERRORZ.py", ["--all"])


def cmd_trimz():
    run_python("METAWORMS/TRIMZ.py", ["--all"])


def cmd_healthz():
    run_python("METAWORMS/HEALTHZ.py", ["--all"])


def cmd_hydrometa():
    run_python("METAWORMS/HYDRAMETA.py", ["--all"])


# HYDRA Command
def cmd_hydra(args):
    if not args or args[0] in ["all", None]:
        run_python("HYDRA/HYDRA.py", ["--all"])
    else:
        stage = args[0]
        valid_stages = [
            "discovery",
            "analysis",
            "optimization",
            "validation",
            "deployment",
            "feedback",
        ]
        if stage in valid_stages:
            run_python("HYDRA/HYDRA.py", ["--stage", stage])
        else:
            log(f"Invalid stage: {stage}", "ERROR")
            print(f"Valid stages: {', '.join(valid_stages)}")


# Ecosystem Commands
def cmd_terence():
    run_python("TERENCE/TERENCE.py", ["--scan"])


def cmd_horus():
    run_python("HORUS/HORUS.py")


def cmd_tarbaby():
    run_python("TARBABY/TARBABY.py", ["--test"])


def cmd_poindexter():
    run_python("POINDEXTER/POINDEXTER.py")


def cmd_persephone():
    run_python("PERSEPHONE/persephone.py")


# Optimization Commands
def cmd_optimize():
    run_python("ECOSYSTEM_OPTIMIZER.py", ["-o"])


def cmd_monitor():
    """Launch live monitoring popup."""
    try:

        monitor_path = str(Path(Azazael_ROOT) / "Azazael_LIVE_MONITOR.py")
        if not os.path.exists(monitor_path):
            monitor_path = str(Path(Azazael_ROOT) / "src" / "ORCHESTRATION" / "HORUS_LIVE_MONITOR.py")
        if not os.path.exists(monitor_path):
            log(f"Monitor script not found at {monitor_path}", "WARN")
            return

        result = subprocess.run(
            [sys.executable, monitor_path],
            capture_output=True,
            text=True,
            timeout=5, encoding="utf-8", errors="replace"
        )
        if result.returncode == 0:
            log("Live monitor launched", "OK")
        else:
            log(f"Monitor launch issue: {result.stderr[:800]}", "WARN")
    except Exception as e:
        log(f"Could not launch monitor: {e}", "ERROR")


def cmd_analyze():
    run_python("BAT_OPTIMIZATION_ANALYSIS.py")


# Full Ecosystem
def cmd_all():
    run_python("Azazael_INTEGRATION.py")


def main():
    parser = argparse.ArgumentParser(
    description="KLINK - Azazael Ecosystem Unified Command Launcher",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
Examples:
  klink.py status              Show ecosystem status
  klink.py worms               Run all WORMZ worms
  klink.py hydra all           Run full HYDRA cycle
  klink.py hydra discovery     Run HYDRA discovery stage
  klink.py optimize            Run ecosystem optimizer
  klink.py all                 Run full ecosystem

Platforms: Windows, Linux, Debian, macOS, WSL
        """,
    )
    parser.add_argument("command", nargs="?", help="Command to run")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments for command")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--version", action="store_true", help="Show version")

    args = parser.parse_args()

    if args.version:
        cmd_version()
        return

    if not args.command or args.command in ["help", "--help", "-h"]:
        cmd_list()
        print("\nUse 'klink.py --help' for full usage")
        return

    cmd_name = args.command.lower()

    if cmd_name not in COMMANDS:
        log(f"Unknown command: {cmd_name}", "ERROR")
        print(f"\nAvailable commands: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    cmd_info = COMMANDS[cmd_name]
    func_name = cmd_info["func"]

    if args.json:
        result = {
            "command": cmd_name,
            "timestamp": datetime.now().isoformat(),
            "status": "running",
        }
        print(json.dumps(result, indent=2))

    # Call the appropriate function
    func = globals().get(func_name)
    if func:
        if cmd_info.get("has_args"):
            func(args.args)
        else:
            func()
    else:
        log(f"Function not found: {func_name}", "ERROR")


if __name__ == "__main__":
    main()


def cmd_bridge(args):
    """InferenceBridge — LLM-powered fleet analysis."""
    if not args or args[0] in ["status"]:
        run_python("META/InferenceBridge.py", ["--status"])
    elif args[0] == "test":
        run_python("META/InferenceBridge.py", ["--test"])
    elif args[0] == "chat":
        msg = " ".join(args[1:]) if len(args) > 1 else "Hello"
        run_python("META/InferenceBridge.py", ["--chat", msg])
    elif args[0] == "models":
        run_python("META/InferenceBridge.py", ["--models"])
    else:
        log(f"Unknown bridge subcommand: {args[0]}", "ERROR")
        print("Subcommands: status, test, chat <msg>, models")


def cmd_fleet(args):
    """FleetZ — Continuous fleet daemon."""
    if not args or args[0] in ["status"]:
        run_python("META/FleetZ.py", ["--status"])
    elif args[0] == "export":
        run_python("META/FleetZ.py", ["--export"])
    elif args[0] in ["continuous", "run"]:
        interval = args[1] if len(args) > 1 else "30"
        duration = args[2] if len(args) > 2 else "0"
        run_python("META/FleetZ.py", ["--continuous", "--interval", interval, "--duration", duration])
    else:
        log(f"Unknown fleet subcommand: {args[0]}", "ERROR")
        print("Subcommands: status, export, continuous [interval] [duration]")


def cmd_distill(args):
    """DistillZ — Tier 5 knowledge distillation."""
    if not args or args[0] in ["run", "distill"]:
        run_python("META/DistillZ.py", ["--distill"])
    elif args[0] == "export":
        filepath = args[1] if len(args) > 1 else None
        cmd_args = ["--distill", "--export"]
        if filepath:
            cmd_args.append(filepath)
        run_python("META/DistillZ.py", cmd_args)
    elif args[0] == "no-llm":
        run_python("META/DistillZ.py", ["--distill", "--no-llm"])
    else:
        log(f"Unknown distill subcommand: {args[0]}", "ERROR")
        print("Subcommands: distill, export [filepath], no-llm")


# === NEW COMMANDS (added 2026-05-27) ===

COMMANDS["debug"] = {"func": "cmd_debug", "help": "Run full DebugZ diagnostic suite"}
COMMANDS["diag"] = {"func": "cmd_diag", "help": "Quick diagnostic overview"}
COMMANDS["test"] = {"func": "cmd_test", "help": "Run ecosystem test suite"}
COMMANDS["pams"] = {"func": "cmd_pams", "help": "PAMS memory server status"}
COMMANDS["llama"] = {"func": "cmd_llama", "help": "Llama server status and models"}
COMMANDS["metaworm"] = {"func": "cmd_metaworm", "help": "MetaWormSpawner fleet control", "has_args": True}
COMMANDS["insights"] = {"func": "cmd_insights", "help": "Show ecosystem insights and metrics"}
COMMANDS["bridge"] = {"func": "cmd_bridge", "help": "InferenceBridge — LLM analysis of fleet", "has_args": True}
COMMANDS["fleet"] = {"func": "cmd_fleet", "help": "FleetZ — Continuous fleet daemon", "has_args": True}
COMMANDS["distill"] = {"func": "cmd_distill", "help": "DistillZ — Knowledge distillation", "has_args": True}


def cmd_debug():
    """Run full DebugZ diagnostic suite."""
    log("Running DebugZ diagnostic suite...", "INFO")
    run_python("META/DebugZ.py", ["--full", "--save"])


def cmd_diag():
    """Quick diagnostic overview."""
    log("Azazael Quick Diagnostic", "INFO")
    print()
    
    # Check PAMS
    try:
        import urllib.request
        import json
        req = urllib.request.urlopen("http://127.0.0.1:21434/api/health", timeout=3)
        data = json.loads(req.read())
        log(f"PAMS: {data.get('status', 'unknown')}", "OK" if data.get('status') == 'healthy' else "WARN")
    except Exception as e:
        log(f"PAMS: not reachable ({e})", "WARN")
    
    # Check components
    component_paths = {
        "WORMZ": Path(Azazael_ROOT) / "src" / "CORE" / "WORMS" / "WORMZ.py",
        "MetaWormSpawner": Path(Azazael_ROOT) / "src" / "META" / "MetaWormSpawner.py",
        "DebugZ": Path(Azazael_ROOT) / "src" / "META" / "DebugZ.py",
        "HYDRA": Path(Azazael_ROOT) / "src" / "ORCHESTRATION" / "HYDRA.py",
        "TERENCE": Path(Azazael_ROOT) / "src" / "UTILITIES" / "TERENCE" / "TERENCE.py",
        "KLINK": Path(__file__),
    }
    for name, path in component_paths.items():
        if path.exists():
            size = path.stat().st_size
            log(f"{name}: {size/1024:.0f}KB", "OK")
        else:
            log(f"{name}: MISSING", "ERROR")
    
    # Check PYTHON
    log(f"Python: {sys.version.split()[0]}", "INFO")
    
    # Quick path audit
    key_folders = [
        "src/CORE/WORMS", "src/META", "src/ORCHESTRATION", "src/UTILITIES/KLINK",
        "src/HYDRA", "src/CODEX"
    ]
    for folder in key_folders:
        fp = Path(Azazael_ROOT) / folder
        if fp.exists():
            log(f"{folder}: OK", "OK")
        else:
            log(f"{folder}: MISSING", "ERROR")


def cmd_test():
    """Run ecosystem test suite."""
    log("Running ecosystem test suite...", "INFO")
    run_python("DebugZ.py", ["--health"])
    run_python("DebugZ.py", ["--syntax"])


def cmd_pams():
    """PAMS memory server status and control."""
    print()
    print("=" * 60)
    print("  PAMS — Persistent AI Memory Server")
    print("=" * 60)
    
    import json
    
    # Health check
    try:
        req = urllib.request.urlopen("http://127.0.0.1:21434/api/health", timeout=3)
        data = json.loads(req.read())
        print(f"  Status:      {data.get('status', 'unknown')}")
        print(f"  Server:      {data.get('server', 'unknown')}")
    except Exception as e:
        print(f"  Status:      NOT REACHABLE ({e})")
    
    # Diagnostics
    try:
        req = urllib.request.urlopen("http://127.0.0.1:21434/api/diagnostics", timeout=3)
        data = json.loads(req.read())
        if "memory_usage_mb" in data:
            print(f"  Memory:      {data['memory_usage_mb']:.1f} MB")
        if "cmdline" in data:
            print(f"  Process:     {data.get('cmdline', 'N/A')}")
    except Exception as exc:
        note_failure("klink:L517", exc)
    
    # DB files (configurable via PAMS_DATA_DIR env var)
    db_dir = os.environ.get(
        "PAMS_DATA_DIR",
        os.path.join(sys.prefix, "Lib", "site-packages", "memory_data")
    )
    dbs = ["conversations.db", "ai_memories.db", "mcp_tool_calls.db", "schedule.db"]
    for db in dbs:
        dbpath = os.path.join(db_dir, db)
        if os.path.exists(dbpath):
            size = os.path.getsize(dbpath) / 1024
            print(f"  {db}: {size:.0f} KB")
    
    print("=" * 60)


def cmd_llama():
    """Llama server status and model listing."""
    print()
    print("=" * 60)
    print("  LLAMA — Local LLM Inference Server")
    print("=" * 60)
    
    # Check server binary (configurable via LLAMA_SERVER_EXE env var)
    llama_exe = os.environ.get(
        "LLAMA_SERVER_EXE",
        "C:/Users/clint/temp_llama_cpp_build/llama.cpp/build_server_multi/bin/Release/llama-server.exe",
    )
    print(f"  Binary:      {'FOUND' if os.path.exists(llama_exe) else 'MISSING'}")
    
    # Check port
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    result = s.connect_ex(("127.0.0.1", 18080))
    s.close()
    print(f"  Port 18080:  {'OPEN' if result == 0 else 'CLOSED'}")
    
    # List models (configurable via LLAMA_MODELS_DIR env var)
    models_dir = os.environ.get(
        "LLAMA_MODELS_DIR",
        "C:/Users/clint/llama-server/models",
    )
    if os.path.exists(models_dir):
        print("  Models:")
        for d in sorted(os.listdir(models_dir)):
            dpath = os.path.join(models_dir, d)
            if os.path.isdir(dpath):
                print(f"    - {d}")
    
    # Check launch script (configurable via LLAMA_LAUNCH_SCRIPT env var)
    launch = os.environ.get(
        "LLAMA_LAUNCH_SCRIPT",
        os.path.join(str(Path.home()), "Desktop", "Llama Server", "launch_chat.bat"),
    )
    print(f"  Launcher:    {'FOUND' if os.path.exists(launch) else 'MISSING'}")
    
    print("=" * 60)


def cmd_metaworm(args):
    """MetaWormSpawner fleet control."""
    if not args or args[0] in ["status"]:
        run_python("META/MetaWormSpawner.py", ["--status"])
    elif args[0] == "start":
        run_python("META/MetaWormSpawner.py", ["--start"])
    elif args[0] == "run":
        run_python("META/MetaWormSpawner.py", ["--run"])
    elif args[0] == "morph":
        run_python("META/MetaWormSpawner.py", ["--morph"])
    elif args[0] == "demo":
        duration = args[1] if len(args) > 1 else "30"
        run_python("META/MetaWormSpawner.py", ["--demo", "--duration", duration])
    elif args[0] == "export":
        filepath = args[1] if len(args) > 1 else None
        cmd_args = ["--export"]
        if filepath:
            cmd_args.append(filepath)
        run_python("META/MetaWormSpawner.py", cmd_args)
    else:
        log(f"Unknown metaworm subcommand: {args[0]}", "ERROR")
        print("Subcommands: status, start, run, morph, demo [duration], export [filepath]")


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        try:
            from eve_unleashed.decision_engine import (
                AutonomousDecisionEngine, AutonomyLevel,
            )
            _engine = AutonomousDecisionEngine()
        except Exception as exc:  # fail-open
            log(f"Eve decision engine unavailable: {exc}", "WARN")
            _engine = False
    return _engine


def cmd_eve(args):
    """Eve Unleashed — autonomous decision control (V9.0)."""
    engine = _get_engine()
    if not engine:
        print("Eve decision engine is not available.")
        return

    if not args or args[0] in ["help", "--help", "-h"]:
        print()
        print("=" * 60)
        print("  EVE UNLEASHED — Autonomous Decision-Making (V9.0)")
        print("=" * 60)
        print()
        print("  Subcommands:")
        print("    status              Show engine stats and pending decisions")
        print("    pending             List decisions awaiting human approval")
        print("    approve <id>        Approve a pending decision")
        print("    reject <id>         Reject a pending decision")
        print("    veto <action> [reason]  Human override: block an action name")
        print("    unveto <action>     Remove a human veto on an action name")
        print("    stats               Print decision statistics (JSON)")
        print("    autonomy <level>    Set autonomy level (recommend_only|supervised|autonomous)")
        print("    threshold <value>   Set confidence threshold (0.0-1.0)")
        print()
        return

    sub = args[0].lower()

    # Type-only narrowing: _get_engine() also returns falsy sentinels
    # (None before first try, False after a failed import); the `not engine`
    # gate above already handled both, so this isinstance never changes flow.
    from eve_unleashed.decision_engine import (
        AutonomousDecisionEngine, DecisionStatus, AutonomyLevel,  # noqa: WPS433
    )
    if not isinstance(engine, AutonomousDecisionEngine):
        return

    if sub == "status":
        s = engine.stats()
        print()
        print("=" * 60)
        print("  EVE — Autonomous Decision Engine Status")
        print("=" * 60)
        print(f"  Total decisions:   {s['total']}")
        print(f"  By status:         {s['by_status']}")
        print(f"  Autonomy level:    {s['autonomy_level']}")
        print(f"  Confidence thresh: {s['confidence_threshold']}")
        print(f"  Vetoed actions:    {s['vetoed_actions']}")
        print(f"  Executed rate:     {s['executed_rate']:.2f}")
        pend = engine.pending()
        print(f"  Pending approval:  {len(pend)}")
        for d in pend[:10]:
            print(f"    - {d['id'][:8]}  {d['action']}  (conf={d['confidence']:.2f}, risk={d['risk']})")
        print()

    elif sub == "pending":
        pend = engine.pending()
        if not pend:
            print("No pending decisions.")
            return
        print()
        print(f"Pending decisions ({len(pend)}):")
        for d in pend:
            print(f"  {d['id'][:8]}  {d['action']}  conf={d['confidence']:.2f}  risk={d['risk']}")
            print(f"       rationale: {d['rationale']}")
        print()

    elif sub == "approve":
        if len(args) < 2:
            print("Usage: eve approve <id>")
            return
        full = engine.resolve_id(args[1])
        if full is None:
            print(f"Could not find a decision matching '{args[1]}'.")
            return
        ok = engine.approve(full, decided_by="human")
        print("Approved." if ok else "Already decided.")

    elif sub == "reject":
        if len(args) < 2:
            print("Usage: eve reject <id>")
            return
        full = engine.resolve_id(args[1])
        if full is None:
            print(f"Could not find a decision matching '{args[1]}'.")
            return
        ok = engine.reject(full, decided_by="human")
        print("Rejected." if ok else "Already decided.")

    elif sub == "veto":
        if len(args) < 2:
            print("Usage: eve veto <action> [reason]")
            return
        reason = args[2] if len(args) > 2 else "human override"
        # Veto applies to the action name; find a pending decision with that action or record standalone.
        vetoed = False
        for d in list(engine._decisions.values()):
            if d.action == args[1] and d.status == DecisionStatus.PENDING:
                engine.veto(d.id, decided_by="human", reason=reason)
                vetoed = True
        engine._vetoed_actions.add(args[1])
        print(f"Vetoed action '{args[1]}' (reason: {reason})." + (" Pending decision(s) vetoed." if vetoed else ""))

    elif sub == "unveto":
        if len(args) < 2:
            print("Usage: eve unveto <action>")
            return
        engine._vetoed_actions.discard(args[1])
        print(f"Unvetoed action '{args[1]}'.")

    elif sub == "stats":
        print(json.dumps(engine.stats(), indent=2))

    elif sub == "autonomy":
        if len(args) < 2:
            print(f"Current autonomy: {engine.autonomy_level.name}")
            return
        mapping = {"recommend_only": 0, "supervised": 1, "autonomous": 2}
        lvl = mapping.get(args[1].lower())
        if lvl is None:
            print("Valid levels: recommend_only, supervised, autonomous")
            return
        engine.set_autonomy_level(AutonomyLevel(lvl))
        print(f"Autonomy set to {args[1].lower()}.")

    elif sub == "threshold":
        if len(args) < 2:
            print(f"Current threshold: {engine.confidence_threshold}")
            return
        try:
            val = float(args[1])
            engine.set_confidence_threshold(val)
            print(f"Confidence threshold set to {val}.")
        except ValueError:
            print("Invalid threshold value.")

    else:
        log(f"Unknown eve subcommand: {sub}", "ERROR")
        print("Run 'eve help' for subcommands.")


def cmd_insights():
    """Show ecosystem insights and metrics."""
    print()
    print("=" * 60)
    print("  AZAZAEL — Ecosystem Insights")
    print("=" * 60)
    print()
    print("  Architecture: 7-Tier Worm Hierarchy")
    print("  Worms:        5 core (SYSTEMZ, PROCESSZ, DISKZ, NETWORXZ, CLOCKZ)")
    print("  Metaworms:    5 (ERRORZ, TRIMZ, HEALTHZ, HYDRAMETA, MetaWormSpawner)")
    print("  Clusters:     8 (System, Network, Security, Analysis, Validation, Health, BIBL, Full)")
    print("  Utilities:    6 (KLINK, TERENCE, POINDEXTER, PERSEPHONE, TARBABY, DebugZ)")
    print("  Orchestration: HYDRA (6-stage) + HORUS + ECOSYSTEM_OPTIMIZER")
    print()
    print("  LLM:          llama-server (8 models, gemma-4-E2B, Qwen2.5, InternVL3)")
    print("  Memory:       pams-server (port 21434)")
    print("  Python:       3.14.5 (standard) + 3.14.3t (free-threaded, no-GIL)")
    print("  CLI:          KLINK (35 commands)")
    print()
    print("  Recent Enhancements (2026-05-27):")
    print("    - MetaWormSpawner v2.0.0 (dynamic morphing sub-agents)")
    print("    - DebugZ v1.0.0 (comprehensive debugging suite)")
    print("    - Ecosystem config paths corrected to ~/azazael/src/")
    print("    - PAMS daemon fixed (Python wrapper, lifespan=off)")
    print("    - llama launch_chat.bat fixed")
    print("    - G:/ archive of obsolete Desktop files")
    print("    - 14 __init__.py files for clean package imports")
    print("    - InferenceBridge v1.0.0 (llama-server ↔ MetaWormSpawner)")
    print("    - FleetZ v1.0.0 (continuous fleet daemon)")
    print("    - DistillZ v1.0.0 (Tier 5 knowledge distillation)")
    print()
    print("  Key Metrics:")
    print("    - BIBL: 421K entries indexed across 451 files")
    print("    - TERENCE: 26 fix patterns for auto-resolution")
    print("    - Free-threaded: true parallel execution (no GIL)")
    print("=" * 60)
