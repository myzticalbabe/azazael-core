#!/usr/bin/env python3
"""


__all__ = [
    "cmd_backup",
    "cmd_config",
    "cmd_inference",
    "cmd_graph",
    "cmd_atlas",
    "cmd_stats",
    "cmd_status",
    "cmd_status_json",
    "cmd_registry",
    "cmd_cluster",
    "main",
]

azazael_ctl.py — Unified Azazael CLI (crossref, audit, memory, inference).

Usage:
    azazael-ctl crossref <subcommand> [args]
    azazael-ctl audit <subcommand> [args]
    azazael-ctl memory <subcommand> [args]
    azazael-ctl inference <subcommand> [args]
    azazael-ctl status
    azazael-ctl help
"""

import sys
import os
import json
import time
import shutil
from pathlib import Path
from typing import Any, Dict, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Lock file — prevent concurrent writes
_LOCK_FILE = Path.home() / ".azazael" / "ctl.lock"

# Color support (disabled if not a TTY)
_COLORS = sys.stdout.isatty()


def _acquire_lock():
    """Acquire a lock file to prevent concurrent azazael-ctl processes."""
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
        _lock_fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_RDWR)
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (ImportError, IOError, OSError):
        # Windows or lock contention
        if _LOCK_FILE.exists():
            age = time.time() - _LOCK_FILE.stat().st_mtime
            if age < 30:  # Lock is fresh (<30s old)
                print(_color("Another azazael-ctl process may be running. Use --force to override.", "red"))
                return False
            # Stale lock — override
        _LOCK_FILE.touch()
        return True


def _release_lock():
    """Release the lock file."""
    try:
        if _LOCK_FILE.exists():
            _LOCK_FILE.unlink()
    except Exception as exc:
        note_failure("azazael_ctl:L70", exc)

def _color(text: str, code: str) -> str:
    if not _COLORS:
        return text
    codes = {
        "red": "\033[91m", "green": "\033[92m", "yellow": "\033[93m",
        "blue": "\033[94m", "magenta": "\033[95m", "cyan": "\033[96m",
        "bold": "\033[1m", "dim": "\033[2m", "reset": "\033[0m",
    }
    return f"{codes.get(code, '')}{text}{codes.get('reset', '')}"


def _progress_bar(current: int, total: int, label: str = "") -> str:
    """Simple progress bar for CLI."""
    if not _COLORS:
        return f"{label} {current}/{total}"
    width = min(40, shutil.get_terminal_size().columns - 30)
    filled = int(width * current / max(total, 1))
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * current / max(total, 1))
    return f"{label} [{bar}] {pct}% ({current}/{total})"


def cmd_status(json_output: bool = False):
    cols, _ = shutil.get_terminal_size()
    header = _color("  AZAZAEL System Status", "bold")
    print("=" * cols)
    print(header)
    print("=" * cols)
    from CORE.direct_memory import DirectMemory
    from CORE.audit_trail import verify_on_startup
    try:
        s = DirectMemory.get_mapping_summary()
        print(f"\n  {_color('Cross-Reference:', 'cyan')}")
        print(f"    Total mappings:   {_color(str(s.get('total_mappings', '?')), 'green')}")
        print(f"    Linked DM:        {s.get('unique_dm_sessions', '?')}")
        print(f"    Linked AJ:        {s.get('unique_aj_sessions', '?')}")
    except Exception as e:
        print(f"  DM error: {e}")

    try:
        broken = verify_on_startup()
        print(f"\n  {_color('Audit Trail:', 'magenta')}")
        broken_count = len(broken)
        clr = "red" if broken_count > 0 else "green"
        print(f"    Broken files:     {_color(str(broken_count), clr)}")
        for b in broken:
            print(f"      {b.get('file', '?')[-40:]}")
    except Exception as e:
        print(f"  Audit error: {e}")

    print(f"\n  {_color('Inference Pool:', 'yellow')}")
    try:
        from CORE.inference_pool import InferencePool
        pool = InferencePool(max_concurrent=1)
        health = pool.health()
        status_text = _color("Online", "green") if health.get("healthy") else _color("Offline", "red")
        print(f"    Status:           {status_text}")
        print(f"    Max concurrent:   {pool.stats['max_concurrent']}")
        print(f"    VRAM:             {pool.stats['vram_gb']}GB")
    except Exception as e:
        print(f"    Not available: {e}")

    from CORE.freethreading import print_thread_info
    print(f"\n  {_color('Python:', 'blue')}")
    print_thread_info()
    open_cnt = DirectMemory.get_open_sessions_count()
    open_clr = "yellow" if open_cnt > 0 else "green"
    print(f"  Open sessions:    {_color(str(open_cnt), open_clr)}")

    print(f"\n  {_color('Worm Registry:', 'green')}")
    try:
        from CORE.worm_registry import validate_registry_importable
        reg = validate_registry_importable()
        reg_clr = "green" if reg.get("valid") else "red"
        print(f"    Import valid:    {_color(str(reg.get('valid')), reg_clr)}")
        print(f"    Total entries:   {reg.get('total', 0)}")
        print(f"    Confirmed:       {reg.get('checked', 0)}")
        broken = reg.get("broken") or []
        print(f"    Broken imports:  {_color(str(len(broken)), 'red' if broken else 'green')}")
        for b in broken[:20]:
            print(f"      {b.get('name', '?')} ({b.get('module', '?')})")
            print(f"        {b.get('error', '?')}")
        if len(broken) > 20:
            print(f"      ... and {len(broken) - 20} more")
    except Exception as e:
        print(f"    Not available: {e}")


def cmd_status_json():
    """JSON status output for programmatic consumption."""
    from CORE.direct_memory import DirectMemory
    from CORE.audit_trail import verify_on_startup
    broken = verify_on_startup()
    report: Dict[str, Any] = {
        "audit": {"broken": len(broken), "files": [b.get("file","") for b in broken]},
        "sessions": {"open": DirectMemory.get_open_sessions_count()},
    }
    try:
        s = DirectMemory.get_mapping_summary()
        report["crossref"] = {
            "total": s.get("total_mappings", 0),
            "dm": s.get("unique_dm_sessions", 0),
            "aj": s.get("unique_aj_sessions", 0),
        }
    except Exception as e:
        note_failure("azazael_ctl:L177", e)
        report["crossref"] = {"error": str(e)}
    try:
        from CORE.worm_registry import validate_registry_importable
        reg = validate_registry_importable()
        report["registry"] = {
            "valid": reg.get("valid"),
            "total": reg.get("total", 0),
            "checked": reg.get("checked", 0),
            "broken": reg.get("broken") or [],
            "validated_at": reg.get("validated_at"),
        }
    except Exception as e:
        note_failure("azazael_ctl:L189", e)
        report["registry"] = {"valid": False, "error": str(e)}
    print(json.dumps(report, indent=2))


def cmd_backup(output_dir: str | None = None):
    """Backup memory_store.db and .audit/ to a directory."""
    from datetime import datetime
    from pathlib import Path
    from CORE.direct_memory import DB_PATH
    out = Path(output_dir or f"azazael_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)
    # Copy DB
    db_path = Path(str(DB_PATH))
    items = []
    if db_path.exists():
        items.append(db_path)
    from CORE.audit_trail import AUDIT_DIR
    if AUDIT_DIR.exists():
        items.extend(sorted(AUDIT_DIR.glob("*.jsonl")))
    total = len(items)
    for idx, src in enumerate(items, 1):
        dest = out / src.name
        shutil.copy2(str(src), str(dest))
        print(f"\r{_progress_bar(idx, total, 'Backup:')}")
        if _COLORS:
            print(f"  {src.name} -> {dest.name}", end="\033[K\n")
        else:
            print(f"  {src.name} -> {dest.name}")
    print()
    print(f"\n  {_color('Backup saved to:', 'green')} {out.resolve()}")


def cmd_config():
    """Print current configuration."""
    from CORE.direct_memory import DB_PATH
    from CORE.audit_trail import AUDIT_DIR
    from CORE.inference_pool import OLLAMA_BASE
    print("=== Azazael Configuration ===")
    print(f"  DB path:          {DB_PATH}")
    print(f"  Audit dir:        {AUDIT_DIR.resolve()}")
    print(f"  Ollama base:      {OLLAMA_BASE}")
    print(f"  Python:           {sys.version.split()[0]}")
    import platform
    print(f"  Platform:         {platform.system()} {platform.release()}")
    print(f"  Free-threading:   {'yes' if __import__('CORE.freethreading', fromlist=['is_free_threaded']).is_free_threaded() else 'no'}")
    print(f"  AZAZAEL_MEMORY_DIR: {os.environ.get('AZAZAEL_MEMORY_DIR', '(not set)')}")


def cmd_graph(args: list[str]):
    """Extract and export a static or runtime-enriched codebase graph."""
    from CORE.codebase_graph import build_graph

    commands = {"artifacts", "gaps"}
    root_arg = next((arg for arg in args if not arg.startswith("--") and arg not in commands), None)
    root = Path(root_arg).resolve() if root_arg else Path(__file__).resolve().parent.parent
    output = None
    fmt = "json"
    include_tests = "--no-tests" not in args
    previous = None
    runtime = "--runtime" in args
    if "artifacts" in args or "gaps" in args:
        from CORE.graph_artifacts import generate_artifacts, determine_gaps
        artifact_dir = Path(args[args.index("--output") + 1]) if "--output" in args else root / "output" / "graph"
        if "artifacts" in args:
            result = generate_artifacts(root, artifact_dir, include_runtime=runtime, include_tests=include_tests)
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            graph = build_graph(root, include_tests=include_tests)
            if runtime:
                from CORE.graph_artifacts import runtime_overlay
                nodes, edges, warnings = runtime_overlay()
                graph.add_overlay(nodes, edges)
            print(json.dumps(determine_gaps(graph), indent=2, sort_keys=True))
        return
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg == "--format" and idx + 1 < len(args):
            fmt = args[idx + 1]
            idx += 1
        elif arg == "--output" and idx + 1 < len(args):
            output = Path(args[idx + 1])
            idx += 1
        elif arg == "--diff" and idx + 1 < len(args):
            previous = json.loads(Path(args[idx + 1]).read_text(encoding="utf-8"))
            idx += 1
        idx += 1

    graph = build_graph(root, include_tests=include_tests)
    if runtime:
        from CORE.graph_artifacts import runtime_overlay
        nodes, edges, _warnings = runtime_overlay()
        graph.add_overlay(nodes, edges)
    if previous is not None:
        rendered = json.dumps(graph.snapshot_diff(previous), indent=2, sort_keys=True)
    elif fmt == "json":
        rendered = graph.to_json()
    elif fmt == "mermaid":
        rendered = graph.to_mermaid()
    elif fmt == "dot":
        rendered = graph.to_dot()
    elif fmt == "html":
        rendered = graph.to_html()
    elif fmt == "mindmap":
        rendered = graph.to_mermaid_mindmap()
    elif fmt == "graphml":
        rendered = graph.to_graphml()
    elif fmt == "gexf":
        rendered = graph.to_gexf()
    elif fmt in ("cytoscape", "cytoscape-json"):
        rendered = graph.to_cytoscape_json()
    elif fmt == "csv":
        rendered = graph.to_csv()
    elif fmt == "svg":
        rendered = graph.to_svg()
    else:
        raise ValueError(f"Unsupported graph format: {fmt}")

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(f"  Graph written to: {output}")
    else:
        print(rendered)


def cmd_atlas(args: list[str]):
    """Inventory explicitly supplied project roots and export provenance atlas."""
    from CORE.project_atlas import scan_projects
    roots: dict[str, Path | str] = {
        "azazael": Path("C:/Azazael/azazael"),
        "azazael_archive": Path("G:/azazael.archived-20261021"),  # archived 2026-10-21; was azazael_secondary
        # Relocated 2026-09-30: vermillion_lightfury is Kali-native and
        # deprecated on Windows (SYNC.md), so it no longer sits in the
        # Windows pillar. Staged here until the Kali pillar absorbs it.
        "vermillion_lightfury": Path("C:/Users/clint/.kali_staging/vermillion_lightfury"),
        "tigger": Path("C:/Users/clint/tigger"),
        "aoa_g_archive": Path("G:/AZAZAEL/AOA"),
        "aoa_source_truth": Path("G:/AZAZAEL_SOURCE_OF_TRUTH/AOA"),
        "g_source_truth_azazael": Path("G:/source-of-truth/azazael"),
        "gosp": Path("G:/source-of-truth/GOSP"),
        "pams": Path("G:/source-of-truth/PAMS"),
    }
    output = Path("azazael/output/project_atlas")
    if "--output" in args:
        index = args.index("--output")
        if index + 1 < len(args):
            output = Path(args[index + 1])
    atlas = scan_projects(roots)
    output.mkdir(parents=True, exist_ok=True)
    (output / "project_atlas.json").write_text(atlas.to_json(), encoding="utf-8")
    (output / "project_atlas.csv").write_text(atlas.to_csv(), encoding="utf-8")
    (output / "project_atlas.html").write_text(atlas.to_html(), encoding="utf-8")
    print(json.dumps(atlas.to_dict()["coverage"], indent=2, sort_keys=True))


def cmd_stats():
    """Print system statistics."""
    from CORE.direct_memory import DirectMemory
    print("=== Azazael Statistics ===")
    s = DirectMemory.summary_stats()
    print(f"  Total events:     {s.get('total_events', '?')}")
    print(f"  Sessions:         {s.get('sessions', '?')}")
    print(f"  Event types:      {len(s.get('by_type', {}))}")
    print(f"  Open sessions:    {DirectMemory.get_open_sessions_count()}")
    print(f"  DB size:          {DirectMemory.db_size().get('total_mb', '?')} MB")
    by_type = s.get('by_type', {})
    print("\n  Top event types:")
    for et, cnt in sorted(by_type.items(), key=lambda x: -x[1])[:10]:
        print(f"    {et:30s} {cnt}")


def cmd_inference(args: list):
    """InferencePool subcommands."""
    from CORE.inference_pool import InferencePool
    pool = InferencePool()
    if not args or args[0] == "benchmark":
        model = args[1] if len(args) > 1 else "llama3.2:1b"
        iterations = int(args[2]) if len(args) > 2 and args[2].isdigit() else 5
        result = pool.benchmark(model=model, iterations=iterations)
        for k, v in result.items():
            print(f"  {k}: {v}")
    elif args[0] == "models":
        models = pool.list_models()
        if models:
            for m in models:
                print(f"  {m.get('name', '?')}")
        else:
            print("  No models found (Ollama not running?)")
    elif args[0] == "classify":
        text = " ".join(args[1:])
        result = pool.infer(f"Classify: {text}")
        print(result.get("response", result.get("error", "?")))
    elif args[0] == "stats":
        for k, v in pool.stats.items():
            print(f"  {k}: {v}")


def cmd_registry(args: list) -> int:
    """`azazael-ctl registry` — worm registry inspection.

    Implements the `registry validate|health|stats` subcommands that the
    `azazael-ctl` wrapper help advertises. Before 2026-10-03 the router in
    `main()` never learned the `registry` section, so the advertised command
    answered "Unknown section: registry" while the same help listed it.

    Every subcommand discovers first: the registry is process-global and empty
    until discovery runs, and validating an empty registry is a vacuous pass
    (measured 2026-10-03: `valid=True` with `total=0`).
    """
    import CORE.worm_registry as reg

    sub = args[0] if args else "health"
    if sub not in ("validate", "health", "stats"):
        print(f"  Unknown registry subcommand: {sub}")
        print("  Usage: azazael-ctl registry validate|health|stats")
        return 1

    try:
        reg.discover_worms()
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        print(f"  registry discovery failed: {type(exc).__name__}: {exc}")
        return 1

    if sub == "validate":
        result = reg.validate_registry_importable()
        clr = "green" if result["valid"] else "red"
        print(f"  Registry valid:   {_color(str(result['valid']), clr)}")
        print(f"  Total entries:    {result['total']}")
        print(f"  Confirmed:        {result['checked']}")
        broken = result.get("broken") or []
        print(f"  Broken imports:   "
              f"{_color(str(len(broken)), 'red' if broken else 'green')}")
        for b in broken[:20]:
            print(f"    {b.get('name', '?')} ({b.get('module', '?')}): "
                  f"{b.get('error', '?')}")
        return 0 if result["valid"] else 1

    if sub == "health":
        cats = reg.get_all_categories()
        print(f"  Worms registered: {len(reg.WORM_REGISTRY)}")
        print(f"  Categories:       {len(cats)}")
        print(f"  Discovery done:   {reg.discovery_completed()}")
        return 0

    # stats
    cats = sorted(reg.get_all_categories())
    rows = [(c, len(reg.get_worms_by_category(c))) for c in cats]
    total = sum(n for _, n in rows)
    print(f"  {len(cats)} categories, {total} worms")
    for cat, n in sorted(rows, key=lambda kv: kv[1], reverse=True):
        print(f"    {cat:<24} {n}")
    return 0


def cmd_cluster(args: list) -> int:
    """`azazael-ctl cluster` — cluster ecosystem inspection.

    Implements `cluster status|list|natures|dead-letter`, documented in
    `docs/CLUSTER_API_REFERENCE.md` and advertised by the wrapper help - both
    of which the router did not honour before 2026-10-03.
    """
    from CORE.WORMS import CLUSTERWORMS as cw

    sub = args[0] if args else "status"

    if sub == "list":
        print("  Available Clusters:")
        for name, info in cw.CLUSTER_REGISTRY.items():
            worms = ", ".join(info.get("worms", []))
            print(f"    {name:<12} {info.get('description', '')}")
            print(f"                 worms: {worms}")
        return 0

    if sub == "natures":
        # D1/U3: the nature taxonomy was removed.
        print("  Nature categories: 0 (taxonomy removed - MIGRATION_LEDGER D1/U3)")
        print("    Execution is by category: ClusterWORMZ.build_from_registry(worms)")
        return 0

    if sub == "dead-letter":
        lookback = 3600.0
        if "--lookback" in args:
            idx = args.index("--lookback")
            if idx + 1 < len(args):
                try:
                    lookback = float(args[idx + 1]) * 3600.0
                except ValueError:
                    print("  --lookback expects hours (e.g. --lookback 24)")
                    return 1
        registry = cw._get_breaker_registry()
        if not registry:
            print("  Circuit breaker registry unavailable")
            return 0
        summary = registry.get_dead_letter_summary(lookback_seconds=lookback)
        n = summary.get("total_dead_letters", 0)
        clr = "red" if n else "green"
        print(f"  Dead letters: {_color(str(n), clr)}")
        for name, count in summary.get("per_breaker", {}).items():
            print(f"    {name}: {count}")
        for err in summary.get("recent_errors", [])[:10]:
            print(f"    [{err.get('breaker', '?')}] {err.get('error', '?')}")
        return 0

    if sub == "status":
        try:
            cluster = cw.ClusterWORMZ("system")
            status = cluster.get_status()
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            print(f"  cluster status unavailable: {type(exc).__name__}: {exc}")
            return 1
        print(f"  Cluster:          {status.get('cluster_type')} "
              f"({status.get('cluster_id')})")
        print(f"  Worms:            {status.get('worm_count')} "
              f"({status.get('enabled_count')} enabled)")
        print(f"  Free-threaded:    {status.get('free_threaded')}")
        print(f"  Registry/PAMS/Bus:{status.get('registry_ok')}/"
              f"{status.get('pams_ok')}/{status.get('bus_ok')}")
        print(f"  Circuit breaker:  {status.get('breaker_ok')}")
        print(f"  Predefined:       {len(cw.CLUSTER_REGISTRY)} clusters")
        return 0

    print(f"  Unknown cluster subcommand: {sub}")
    print("  Usage: azazael-ctl cluster "
          "status|list|natures|dead-letter")
    return 1


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    # Handle --json flag (must be before section)
    json_output = "--json" in args
    if json_output:
        args.remove("--json")

    section = args[0]
    rest_args = args[1:]

    if section == "doctor":
        from CORE.doctor import cmd_doctor
        cmd_doctor()
    elif section == "verify":
        from CORE.doctor import cmd_verify
        report = cmd_verify()
        sys.exit(0 if report.get("healthy") else 1)
    elif section == "config":
        cmd_config()
    elif section == "stats":
        cmd_stats()
    elif section == "maintain":
        dry_run = "--dry-run" in rest_args
        if dry_run:
            print("  DRY RUN — no changes will be made")
        else:
            from CORE.data_retention import cmd_maintain
            cmd_maintain()
    elif section == "backup":
        cmd_backup(rest_args[0] if rest_args else None)
    elif section == "dashboard":
        from CORE.xref_dashboard import generate_html
        result = generate_html()
        print(f"  Dashboard: {result}")
    elif section == "status":
        if "--watch" in rest_args:
            # Watch mode: poll periodically
            interval = 2
            try:
                idx = rest_args.index("--interval")
                if idx + 1 < len(rest_args):
                    interval = int(rest_args[idx + 1])
            except (ValueError, IndexError) as exc:
                note_failure("azazael_ctl:L569", exc)
            try:
                while True:
                    print("\033[2J\033[H", end="")
                    if json_output:
                        cmd_status_json()
                    else:
                        cmd_status()
                    print(f"\n{_color('Watching... Ctrl+C to stop', 'dim')} (every {interval}s)")
                    time.sleep(interval)
            except KeyboardInterrupt:
                print("\nStopped.")
        elif json_output:
            cmd_status_json()
        else:
            cmd_status()
    elif section == "crossref":
        from CORE.cli_crossref import main as xref_main
        sys.argv = [sys.argv[0]] + rest_args + (["--json"] if json_output else [])
        xref_main()
    elif section == "audit":
        from CORE.cli_audit import main as audit_main
        sys.argv = [sys.argv[0]] + rest_args
        audit_main()
    elif section == "memory":
        from CORE.cli_memory import main as mem_main
        sys.argv = [sys.argv[0]] + rest_args
        mem_main()
    elif section == "inference":
        cmd_inference(rest_args)
    elif section == "registry":
        sys.exit(cmd_registry(rest_args))
    elif section == "cluster":
        sys.exit(cmd_cluster(rest_args))
    elif section == "graph":
        cmd_graph(rest_args)
    elif section == "atlas":
        cmd_atlas(rest_args)
    elif section in ("help", "--help", "-h"):
        print(__doc__)
    else:
        print(f"Unknown section: {section}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    try:
        locked = _acquire_lock()
        if locked:
            main()
        else:
            sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    finally:
        _release_lock()
