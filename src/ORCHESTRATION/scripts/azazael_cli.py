import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))

# coding: utf-8
#!/usr/bin/env python3
"""
Azazael CLI Assistant v2.1
Unified command-line interface for all Azazael operations.

Features:
- Command aliases
- Configuration file support (~/.azazaelrc)
- Plugin system for custom commands
- Colored output
- Progress bars
- Tab auto-completion support

Usage:
    python azazael_cli.py              # Interactive mode
    python azazael_cli.py command      # Single command
    python azazael_cli.py --help       # Show help
"""

import sys
import subprocess
import json
import importlib.util
from pathlib import Path
from typing import Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    # optional env dependency, not code: guarded below; installed ad hoc, ships no stubs
    from colorama import Fore, Style, init  # pyright: ignore[reportMissingModuleSource] -- optional dependency without stubs

    COLORAMA_AVAILABLE = True
except ImportError as exc:
    note_failure("azazael_cli:L37", exc)
    COLORAMA_AVAILABLE = False

try:
    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
    )
    from rich.console import Console
    from rich.table import Table

    RICH_AVAILABLE = True
except ImportError as exc:
    note_failure("azazael_cli:L50", exc)
    RICH_AVAILABLE = False


class OutputStyle:
    """Colored output utilities."""

    def __init__(self):
        init(autoreset=True) if COLORAMA_AVAILABLE else None
        self.console = Console() if RICH_AVAILABLE else None

    def ok(self, msg: str):
        prefix = f"{Fore.GREEN}[OK]{Style.RESET_ALL}" if COLORAMA_AVAILABLE else "[OK]"
        print(f"{prefix} {msg}")

    def warn(self, msg: str):
        prefix = (
            f"{Fore.YELLOW}[WARN]{Style.RESET_ALL}" if COLORAMA_AVAILABLE else "[WARN]"
        )
        print(f"{prefix} {msg}")

    def error(self, msg: str):
        prefix = (
            f"{Fore.RED}[ERROR]{Style.RESET_ALL}" if COLORAMA_AVAILABLE else "[ERROR]"
        )
        print(f"{prefix} {msg}")

    def info(self, msg: str):
        prefix = (
            f"{Fore.CYAN}[INFO]{Style.RESET_ALL}" if COLORAMA_AVAILABLE else "[INFO]"
        )
        print(f"{prefix} {msg}")

    def header(self, msg: str):
        sep = "=" * 60
        if COLORAMA_AVAILABLE:
            print(f"{Fore.BLUE}{sep}{Style.RESET_ALL}")
            print(f"{Fore.BLUE}{msg}{Style.RESET_ALL}")
            print(f"{Fore.BLUE}{sep}{Style.RESET_ALL}")
        else:
            print(sep)
            print(msg)
            print(sep)


class AzazaelConfig:
    """Configuration file management."""

    DEFAULT_CONFIG = {
        "theme": "dark",
        "show_timestamps": False,
        "history_size": 1000,
        "plugins_enabled": True,
        "aliases": {
            "q": "analyze",
            "h": "help",
            "s": "status",
            "v": "validate",
            "r": "run",
            "ll": "ls -l",
            "cls": "clear",
        },
        "favorite_commands": [],
        "custom_paths": [],
    }

    def __init__(self):
        self.config_path = Path.home() / ".azazaelrc"
        self.config = self.load()

    def load(self) -> dict:
        """Load configuration from file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    loaded = json.load(f)
                    config = self.DEFAULT_CONFIG.copy()
                    config.update(loaded)
                    return config
            except Exception as exc:
                note_failure("azazael_cli:L129", exc)
                return self.DEFAULT_CONFIG.copy()
        return self.DEFAULT_CONFIG.copy()

    def save(self):
        """Save configuration to file."""
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)

    def get_aliases(self) -> dict[str, str]:
        """Get command aliases."""
        return self.config.get("aliases", {})

    def add_alias(self, alias: str, command: str):
        """Add a command alias."""
        self.config["aliases"][alias] = command
        self.save()

    def remove_alias(self, alias: str):
        """Remove a command alias."""
        if alias in self.config["aliases"]:
            del self.config["aliases"][alias]
            self.save()


class PluginManager:
    """Plugin system for custom commands."""

    def __init__(self, plugins_dir: Path | None = None):
        self.plugins_dir = plugins_dir or Path.home() / ".azazael" / "plugins"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.commands: dict[str, Callable] = {}
        self.load_plugins()

    def load_plugins(self):
        """Load all plugins from plugins directory."""
        for py_file in self.plugins_dir.glob("*.py"):
            try:
                spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
                if spec is None or spec.loader is None:
                    raise TypeError(f"no usable import spec/loader for plugin {py_file}")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                if hasattr(module, "register_command"):
                    cmd_name = module.register_command()
                    self.commands[cmd_name] = getattr(module, cmd_name)
            except Exception as e:
                print(f"Plugin {py_file.stem} failed to load: {e}")

    def register_command(self, name: str, func: Callable):
        """Register a custom command."""
        self.commands[name] = func

    def get_commands(self) -> dict[str, Callable]:
        """Get all registered commands."""
        return self.commands


class AzazaelCLI:
    """Unified CLI Assistant for Azazael."""

    VERSION = "2.1.0"
    ROOT = Path(__file__).resolve().parent.parent  # scripts/ -> ORCHESTRATION/
    SHORTY = Path.home() / "Desktop" / "SHORTY"

    def __init__(self):
        self.output = OutputStyle()
        self.config = AzazaelConfig()
        self.plugins = PluginManager()
        self.setup_environment()
        self.load_modules()
        self.history_file = self.ROOT / ".azazael_history"
        self.load_history()

    def setup_environment(self):
        """Setup Python environment."""
        paths = [
            str(self.ROOT / "ml_platforms"),
            str(self.ROOT / "scripts"),
        ]
        for p in paths:
            if p not in sys.path:
                sys.path.insert(0, p)

        os.chdir(self.ROOT)
        self.output.ok("Environment configured")

    def load_modules(self):
        """Load core modules."""
        self.modules = {}
        try:
            from harness import main as harness_main
            from launcher import main as launcher_main

            self.modules["harness"] = harness_main
            self.modules["launcher"] = launcher_main
            self.output.ok("Core modules loaded")
        except Exception as e:
            self.output.warn(f"Module load warning: {e}")

    def load_history(self):
        """Load command history."""
        self.history = []
        if self.history_file.exists():
            self.history = self.history_file.read_text(encoding="utf-8").split("\n")

    def save_history(self):
        """Save command history."""
        max_size = self.config.config.get("history_size", 1000)
        self.history_file.write_text("\n".join(self.history[-max_size:]), encoding="utf-8")

    def resolve_alias(self, cmd: str) -> str:
        """Resolve command aliases."""
        parts = cmd.split()
        if not parts:
            return cmd

        aliases = self.config.get_aliases()
        if parts[0] in aliases:
            parts[0] = aliases[parts[0]]

        if parts[0] in ["ll", "la"]:
            parts[0] = "ls"
            parts.append("-" + parts[0][-1])

        return " ".join(parts)

    def run_interactive(self):
        """Run interactive mode."""
        self.output.header(f"Azazael CLI v{self.VERSION}")
        print("Type 'help' for commands, 'exit' to quit\n")

        if RICH_AVAILABLE:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                progress.add_task("Ready", total=None)

        while True:
            try:
                cwd = os.getcwd().replace(str(self.ROOT), "")
                prompt = f"azazael{cwd}> " if cwd else "azazael> "
                cmd = input(prompt).strip()
                if cmd:
                    self.history.append(cmd)
                    resolved = self.resolve_alias(cmd)
                    self.execute(resolved)

            except KeyboardInterrupt:
                print("\nUse 'exit' to quit")
            except Exception as e:
                self.output.error(str(e))

    def execute(self, cmd: str):
        """Execute a single command."""
        parts = cmd.split()
        if not parts:
            return

        action = parts[0].lower()
        args = parts[1:]

        commands = self._get_commands()

        if action in commands:
            commands[action](args)
        else:
            self.output.error(f"Unknown command: '{action}'")
            print("Type 'help' for available commands")

    def _get_commands(self) -> dict[str, Callable]:
        """Get all available commands."""
        commands = {
            "help": self.cmd_help,
            "exit": self.cmd_exit,
            "quit": self.cmd_exit,
            "status": self.cmd_status,
            "cd": lambda a: self.cmd_cd(a[0] if a else str(self.ROOT)),
            "ls": lambda a: self.cmd_ls(a[0] if a else "."),
            "dir": lambda a: self.cmd_ls(a[0] if a else "."),
            "cat": lambda a: self.cmd_cat(a[0] if a else None),
            "type": lambda a: self.cmd_cat(a[0] if a else None),
            "run": lambda a: self.cmd_run(a[0] if a else None),
            "exec": lambda a: self.cmd_exec(" ".join(a)) if a else None,
            "python": lambda a: self.cmd_python(" ".join(a)) if a else None,
            "shortcuts": self.cmd_shortcuts,
            "scripts": self.cmd_scripts,
            "modules": self.cmd_modules,
            "docs": self.cmd_docs,
            "quickref": self.cmd_quickref,
            "analyze": self.cmd_analyze,
            "test": self.cmd_test,
            "validate": self.cmd_validate,
            "fix": self.cmd_fix,
            "history": self.cmd_history,
            "clear": self.cmd_clear,
            "harness": self.cmd_harness,
            "launcher": self.cmd_launcher,
            "alias": self.cmd_alias,
            "aliases": self.cmd_aliases,
            "config": self.cmd_config,
            "horus": self.cmd_horus,
            "hydra": self.cmd_hydra,
            "metrics": self.cmd_metrics,
            "tarbaby": self.cmd_tarbaby,
            "cluster": self.cmd_cluster,
            "poindexter": self.cmd_poindexter,
            "wormz": self.cmd_wormz,
            "metaworms": self.cmd_metaworms,
            "errorz": self.cmd_errorz,
        }

        commands.update(self.plugins.get_commands())
        return commands

    def cmd_help(self, args: list[str]):
        """Show help."""
        aliases = self.config.get_aliases()
        alias_str = "\n  ".join([f"{k} -> {v}" for k, v in aliases.items()])

        print(f"""
Azazael CLI v{self.VERSION}
{"=" * 60}

NAVIGATION:
  cd <dir>       Change directory
  ls / dir       list files
  cat / type     Show file content

FILE OPERATIONS:
  run <file>     Execute file
  exec <code>    Execute Python code
  python <code>  Execute Python code

Azazael:
  shortcuts      list BAT shortcuts
  scripts        list Python scripts
  modules        list ML modules
  docs           Show documentation
  quickref       Quick reference

ANALYSIS:
  analyze [file] Analyze a file
  test <file>   Test a file
  validate      Run validation
  fix <file>    Fix issues

SYSTEM:
  status        System status
  history       Command history
  clear         Clear screen
  help          This help
  exit          Quit

CONFIG:
   alias         Add command alias
   aliases       list aliases
   config        Show configuration

ADVANCED:
   horus         Launch HORUS ML results viewer
   hydra         Run HYDRA adaptive ML cluster
   metrics       Show metrics dashboard
   tarbaby       Run TARBABY binary builder
   cluster       Run full cluster test with popup
   poindexter    Run mathematical analysis
   wormz         Run WORMZ BIBL crawler
   metaworms     Run METAWORMS management
   errorz        Run ERRORZ code error crawler

ALIASES:
  {alias_str}

EXAMPLES:
  azazael> cd ml_platforms
  azazael> ls *.py
  azazael> shortcuts
  azazael> validate
""")

    def cmd_exit(self, args: list[str]):
        """Exit the CLI."""
        self.save_history()
        print("Goodbye!")
        sys.exit(0)

    def cmd_cd(self, args: str | list[str]):
        """Change directory."""
        if not args:
            args = [str(self.ROOT)]

        path = args[0]
        try:
            if path == "..":
                os.chdir("..")
            elif path == "~":
                os.chdir(Path.home())
            elif path == "-":
                os.chdir(self.ROOT)
            else:
                new_path = Path(path)
                if not new_path.is_absolute():
                    new_path = self.ROOT / path
                if new_path.exists():
                    os.chdir(new_path)
                else:
                    self.output.error(f"Directory not found: {path}")
        except Exception as e:
            self.output.error(str(e))

    def cmd_ls(self, args: str | list[str]):
        """list files."""
        pattern = args[0] if args else "*"

        if RICH_AVAILABLE:
            table = Table(title="Files")
            table.add_column("Name", style="cyan")
            table.add_column("Type", style="green")
            table.add_column("Size", style="yellow")

            search_path = Path(pattern)
            base = search_path.parent if "." in str(search_path) else Path.cwd()
            glob_pattern = search_path.name if search_path.name != "." else "*"

            items = list(base.glob(glob_pattern))[:50]

            for item in sorted(items):
                if item.is_dir():
                    table.add_row(f"{item.name}/", "DIR", "-")
                elif item.suffix == ".py":
                    table.add_row(f"{item.name}*", "PY", f"{item.stat().st_size} B")
                else:
                    table.add_row(item.name, "FILE", f"{item.stat().st_size} B")

            # console lives on OutputStyle (self.output), not on AzazaelCLI;
            # direct self.console raised AttributeError in the rich branch
            console = self.output.console
            if console is not None:
                console.print(table)
        else:
            try:
                search_path = Path(pattern)
                base = search_path.parent if "." in str(search_path) else Path.cwd()
                glob_pattern = search_path.name if search_path.name != "." else "*"

                items = list(base.glob(glob_pattern))[:50]

                for item in sorted(items):
                    marker = (
                        "/" if item.is_dir() else "*" if item.suffix == ".py" else ""
                    )
                    print(f"{item.name}{marker}")
            except Exception as e:
                self.output.error(str(e))

    def cmd_cat(self, args: str | list[str] | None):
        """Show file content."""
        if not args:
            print("Usage: cat <file>")
            return

        file_path = args[0]
        try:
            path = Path(file_path)
            if not path.is_absolute():
                path = self.ROOT / file_path

            if path.exists():
                content = path.read_text(encoding="utf-8")[:5000]
                print(content)
                if len(path.read_text(encoding="utf-8")) > 5000:
                    print(f"\n... ({len(path.read_text(encoding='utf-8'))} bytes total)")
            else:
                self.output.error(f"File not found: {file_path}")
        except Exception as e:
            self.output.error(str(e))

    def cmd_run(self, args: str | list[str] | None):
        """Run a file."""
        if not args:
            print("Usage: run <file>")
            return

        file_path = args[0]
        try:
            path = Path(file_path)
            if not path.is_absolute():
                path = self.ROOT / file_path

            if path.suffix == ".py":
                subprocess.run([sys.executable, str(path)], timeout=1800)
            elif path.suffix == ".bat":
                # Run .bat files through cmd.exe explicitly
                subprocess.run(["cmd", "/c", str(path)], timeout=1800)
            elif path.is_file():
                self.output.error(f"Cannot run: {path.suffix} files")
            else:
                self.output.error(f"File not found: {file_path}")
        except Exception as e:
            self.output.error(str(e))

    def cmd_exec(self, args: str | list[str]):
        """Execute Python code."""
        code = " ".join(args)
        try:
            exec(code)
        except Exception as e:
            self.output.error(str(e))

    def cmd_python(self, args: str | list[str]):
        """Execute Python code (alias)."""
        self.cmd_exec(args)

    def cmd_shortcuts(self, args: list[str]):
        """list BAT shortcuts."""
        print("Available Shortcuts (SHORTY):")
        for bat in sorted(self.SHORTY.glob("*.bat")):
            print(f"  {bat.stem}")

    def cmd_scripts(self, args: list[str]):
        """list Python scripts."""
        print("Available Scripts:")
        for py in sorted((self.ROOT / "scripts").glob("*.py")):
            print(f"  {py.stem}")

    def cmd_modules(self, args: list[str]):
        """list ML modules."""
        print("Available Modules:")
        for py in sorted((self.ROOT / "ml_platforms").glob("*.py")):
            print(f"  {py.stem}")

    def cmd_docs(self, args: list[str]):
        """Show documentation."""
        print("Documentation:")
        print(f"  README: {self.ROOT / 'README.md'}")
        print(f"  Protocol: {self.ROOT / 'DOCUMENTATION_PROTOCOL.md'}")
        print(f"  Instructions: {self.ROOT / 'INSTRUCTIONS.md'}")
        print(f"  Enhancement Plan: {self.ROOT / 'Azazael_ENHANCEMENT_PLAN.md'}")

    def cmd_quickref(self, args: list[str]):
        """Quick reference."""
        print("Quick Reference:")
        print("  Commands: See 'help'")
        print("  API: ml_platforms/docs/")
        print("  Source: ml_platforms/*.py")

    def cmd_analyze(self, args: list[str]):
        """Analyze a file."""
        if args:
            self.output.info(f"Analyzing: {args[0]}")

        self.output.info("Running full analysis...")

        if RICH_AVAILABLE:
            with Progress(transient=True) as progress:
                task = progress.add_task("Analyzing...", total=100)
                subprocess.run(
                    [
                        sys.executable,
                        str(self.ROOT / "scripts" / "comprehensive_analysis.py"),
                    ],
                timeout=1800)
                progress.update(task, advance=100)
        else:
            subprocess.run(
                [
                    sys.executable,
                    str(self.ROOT / "scripts" / "comprehensive_analysis.py"),
                ],
            timeout=1800)

    def cmd_test(self, args: list[str]):
        """Test a file."""
        if args:
            self.output.info(f"Testing: {args[0]}")
            path = Path(args[0])
            if path.suffix == ".py":
                subprocess.run(
                    [sys.executable, "-m", "pytest", str(self.ROOT / args[0])],
                timeout=1800)
        else:
            self.output.info("Running full test suite...")
            subprocess.run(
                [sys.executable, str(self.ROOT / "scripts" / "run_tests.py")],
            timeout=1800)

    def cmd_validate(self, args: list[str]):
        """Run validation."""
        self.output.info("Running validation suite...")
        subprocess.run(
            [sys.executable, str(self.ROOT / "scripts" / "validate_docs.py")],
        timeout=1800)

    def cmd_fix(self, args: list[str]):
        """Fix file issues."""
        if args:
            self.output.info(f"Fixing: {args[0]}")
        else:
            print("Available fixes:")
            print("  syntax     - Fix syntax errors")
            print("  docstrings - Generate docstrings")
            print("  bats       - Improve BAT files")

    def cmd_history(self, args: list[str]):
        """Show command history."""
        print("Command History:")
        for i, cmd in enumerate(self.history[-50:], 1):
            if cmd.strip():
                print(f"  {i:3d}: {cmd}")

    def cmd_clear(self, args: list[str]):
        """Clear screen."""
        print("\033[H\033[J", end="")

    def cmd_harness(self, args: list[str]):
        """Run harness."""
        cmd = [sys.executable, str(self.ROOT / "ml_platforms" / "harness.py")]
        cmd.extend(args)
        subprocess.run(cmd, timeout=1800)

    def cmd_launcher(self, args: list[str]):
        """Run launcher."""
        cmd = [sys.executable, str(self.ROOT / "ml_platforms" / "launcher.py")]
        cmd.extend(args)
        subprocess.run(cmd, timeout=1800)

    def cmd_alias(self, args: list[str]):
        """Add a command alias."""
        if len(args) >= 2:
            alias = args[0]
            command = " ".join(args[1:])
            self.config.add_alias(alias, command)
            self.output.ok(f"Alias added: {alias} -> {command}")
        else:
            print("Usage: alias <alias_name> <command>")
            print("Example: alias ll ls -l")

    def cmd_aliases(self, args: list[str]):
        """list all aliases."""
        aliases = self.config.get_aliases()
        if aliases:
            print("Command Aliases:")
            for alias, command in aliases.items():
                print(f"  {alias} -> {command}")
        else:
            print("No aliases defined.")

    def cmd_config(self, args: list[str]):
        """Show configuration."""
        print("Configuration:")
        print(f"  Config file: {self.config.config_path}")
        print(f"  Theme: {self.config.config.get('theme', 'dark')}")
        print(f"  History size: {self.config.config.get('history_size', 1000)}")
        print(f"  Plugins enabled: {self.config.config.get('plugins_enabled', True)}")
        print(f"  Plugins loaded: {len(self.plugins.commands)}")

    def cmd_horus(self, args: list[str]):
        """Run HORUS ML results viewer."""
        self.output.info("Launching HORUS...")
        cmd = [sys.executable, str(self.ROOT / "HORUS.py")]
        cmd.extend(args)
        subprocess.run(cmd, timeout=1800)

    def cmd_hydra(self, args: list[str]):
        """Run HYDRA adaptive ML cluster."""
        self.output.info("Launching HYDRA...")
        cmd = [sys.executable, str(self.ROOT / "HYDRA.py")]
        cmd.extend(args)
        subprocess.run(cmd, timeout=1800)

    def cmd_metrics(self, args: list[str]):
        """Show metrics dashboard."""
        self.output.info("Loading metrics...")
        try:
            from ml_platforms.improvements.metrics_tracker import (
                generate_quality_report,
            )

            report = generate_quality_report()
            print(json.dumps(report, indent=2))
        except Exception as e:
            self.output.error(f"Metrics error: {e}")

    def cmd_tarbaby(self, args: list[str]):
        """Run TARBABY binary builder."""
        self.output.info("Launching TARBABY binary builder...")
        cmd = [sys.executable, str(self.ROOT / "TARBABY.py")]
        cmd.extend(args)
        subprocess.run(cmd, timeout=1800)

    def cmd_cluster(self, args: list[str]):
        """Run full cluster test with popup monitoring."""
        self.output.info("Starting cluster test with popup...")
        cmd = [sys.executable, str(self.ROOT / "HYDRA.py"), "--popup"]
        cmd.extend(args)
        subprocess.run(cmd, timeout=1800)

    def cmd_poindexter(self, args: list[str]):
        """Run POINDEXTER mathematical analysis."""
        self.output.info("Launching POINDEXTER...")
        cmd = [sys.executable, str(Path.home() / "Desktop" / "Azazael_UNIFIED" / "ORCHESTRATION_UNIFIED" / "UTILITIES" / "POINDEXTER" / "POINDEXTER.py")]
        cmd.extend(args)
        subprocess.run(cmd, timeout=1800)

    def cmd_wormz(self, args: list[str]):
        """Run WORMZ BIBL crawler."""
        self.output.info("Launching WORMZ BIBL crawler...")
        cmd = [sys.executable, str(Path(__file__).resolve().parent.parent.parent / "CORE" / "WORMS" / "WORMZ_POPUP.py")]
        cmd.extend(args)
        subprocess.run(cmd, timeout=1800)

    def cmd_metaworms(self, args: list[str]):
        """Run METAWORMS management system."""
        self.output.info("Launching METAWORMS...")
        cmd = [sys.executable, str(Path(__file__).resolve().parent.parent.parent / "META" / "METAWORMS_ENRICHED.py")]
        cmd.extend(args)
        subprocess.run(cmd, timeout=1800)

    def cmd_errorz(self, args: list[str]):
        """Run ERRORZ code error crawler."""
        self.output.info("Launching ERRORZ...")
        cmd = [sys.executable, str(Path(__file__).resolve().parent.parent.parent / "ERRORZ.py"), "--scan"]
        cmd.extend(args)
        subprocess.run(cmd, timeout=1800)

    def cmd_status(self, args: list[str]):
        """Show status."""
        cwd = Path.cwd()
        modules_count = len(self.modules)
        history_count = len(self.history)
        shortcuts_count = len(list(self.SHORTY.glob("*.bat")))
        scripts_count = len(list((self.ROOT / "scripts").glob("*.py")))
        len(list((self.ROOT / "ml_platforms").glob("*.py")))

        if RICH_AVAILABLE:
            table = Table(title="Azazael Status")
            table.add_row("Root", str(self.ROOT))
            table.add_row("Current", str(cwd))
            table.add_row("Python", sys.version.split()[0])
            table.add_row("Modules", str(modules_count))
            table.add_row("History", str(history_count))
            table.add_row("Shortcuts", str(shortcuts_count))
            table.add_row("Scripts", str(scripts_count))
            table.add_row("Version", self.VERSION)
            # console lives on OutputStyle (self.output), not on AzazaelCLI;
            # direct self.console raised AttributeError in the rich branch
            console = self.output.console
            if console is not None:
                console.print(table)
        else:
            print(f"""
Azazael Status
{"-" * 50}
Root:     {self.ROOT}
Current:  {cwd}
Python:   {sys.version.split()[0]}
Modules:   {modules_count} loaded
History:   {history_count} commands
Shortcuts: {shortcuts_count} files
Scripts:  {scripts_count} files
Version:   {self.VERSION}
{"-" * 50}
""")


def main():
    """Main entry point."""
    cli = AzazaelCLI()

    if len(sys.argv) > 1:
        if sys.argv[1] in ["--help", "-h"]:
            cli.cmd_help([])
        else:
            cli.execute(" ".join(sys.argv[1:]))
    else:
        cli.run_interactive()


if __name__ == "__main__":
    main()


