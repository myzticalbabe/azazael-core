# coding: utf-8
"""
AZAZAEL CLI v1 - Terminal Interface for Azazael Ecosystem
Integrates bootstrap, worms, open-interpreter, and local LLMs
"""

import sys
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from CORE.paths import get_src_dir
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class AzazaelCLI:
    VERSION = "1.0.0"

    def __init__(self):
        self.root = get_src_dir()  # azazael/src/

    def log(self, msg: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        symbols = {"INFO": "[+]", "WARN": "[!]", "ERROR": "[-]", "OK": "[*]"}
        print(f"{symbols.get(level, '[?]')} {timestamp} {msg}")

    def run_bootstrap(self):
        self.log("Initializing bootstrap...", "INFO")
        try:
            bootstrap_path = self.root / "SCRIPTS" / "bootstrap.py"
            result = subprocess.run(
                [sys.executable, str(bootstrap_path)],
                capture_output=True,
                timeout=30,
                cwd=str(self.root),
            )
            if result.returncode == 0:
                self.log("Bootstrap initialized", "OK")
                return True
            else:
                self.log(f"Bootstrap failed: {result.stderr.decode('utf-8', errors='replace')}", "ERROR")
                return False
        except Exception as e:
            self.log(f"Bootstrap error: {e}", "ERROR")
            return False

    def run_worm(self, worm_name: str):
        worms = {
            "omni": "CORE/WORMS/OMNI_WORM.py",
            "error": "CORE/WORMS/ERROR_SCAN_WORM.py",
            "morph": "CORE/WORMS/MORPHING_WORM_V2.py",
            "cluster": "CORE/WORMS/CLUSTER_INSPECTOR.py",
            "stress": "CORE/STRESS_TEST.py",
        }
        worm_path = worms.get(worm_name.lower())
        if not worm_path:
            self.log(f"Unknown worm: {worm_name}", "ERROR")
            return False

        self.log(f"Running {worm_name} worm...", "INFO")
        try:
            full_path = self.root / worm_path
            result = subprocess.run(
                [sys.executable, str(full_path)],
                capture_output=True,
                timeout=120,
                cwd=str(self.root),
            )
            if result.returncode == 0:
                self.log(f"{worm_name} completed", "OK")
                return True
            else:
                self.log(f"{worm_name} failed", "ERROR")
                return False
        except Exception as e:
            self.log(f"{worm_name} error: {e}", "ERROR")
            return False

    def run_open_interpreter(self):
        self.log("Starting Open Interpreter...", "INFO")
        try:
            subprocess.run(
                [sys.executable, "-m", "interpreter", "--local"],
                capture_output=True,
                timeout=30,
            )
            return True
        except Exception as e:
            self.log(f"Open Interpreter error: {e}", "ERROR")
            return False

    def run_kilo_local(self):
        self.log("Running KILO-LOCAL...", "INFO")
        try:
            result = subprocess.run(
                [sys.executable, str(self.root / "kilo-local.py"), "list"],
                capture_output=True,
                timeout=60,
            )
            print(result.stdout)
            return result.returncode == 0
        except Exception as e:
            self.log(f"KILO-LOCAL error: {e}", "ERROR")
            return False

    def run_python_manager(self):
        self.log("Running Python Manager...", "INFO")
        try:
            python_manager_path = self.root / "CORE" / "PYTHON_MANAGER.py"
            result = subprocess.run(
                [sys.executable, str(python_manager_path)],
                capture_output=True,
                timeout=60,
                cwd=str(self.root),
            )
            print(result.stdout.decode('utf-8', errors='replace') if result.stdout else result.stderr.decode('utf-8', errors='replace'))
            return result.returncode == 0
        except Exception as e:
            self.log(f"Python Manager error: {e}", "ERROR")
            return False

    def run_syntax_check(self):
        self.log("Validating syntax...", "INFO")
        files = list(self.root.rglob("*.py"))
        errors = 0
        for fp in files:
            if "__pycache__" in str(fp) or ".venv" in str(fp):
                continue
            try:
                subprocess.run(
                    [sys.executable, "-m", "py_compile", str(fp)],
                    capture_output=True,
                    timeout=5,
                )
            except Exception as exc:
                note_failure("azazael_cli:L135", exc)
                errors += 1
        if errors == 0:
            self.log(f"All {len(files)} files valid", "OK")
            return True
        else:
            self.log(f"{errors} files have errors", "WARN")
            return False

    def print_menu(self):
        print()
        print("=" * 60)
        print(f"AZAZAEL CLI v{self.VERSION}")
        print("=" * 60)
        print()
        print("Commands:")
        print("  bootstrap       - Initialize Azazael bootstrap")
        print("  worm <name>    - Run a worm (systemz, processz, diskz, networkz, clockz)")
        print("  interpreter    - Start Open Interpreter (local mode)")
        print("  kilo           - list local LLM models")
        print("  python         - Show Python installations")
        print("  syntax         - Validate Python syntax")
        print("  health         - System health check")
        print("  all            - Run full system check")
        print("  help           - Show this menu")
        print("  quit           - Exit")
        print()

    def run_health_check(self):
        self.log("Running health check...", "INFO")
        self.run_bootstrap()
        self.run_syntax_check()
        self.run_python_manager()

    def run_all_checks(self):
        print()
        print("=" * 60)
        print("AZAZAEL FULL SYSTEM CHECK")
        print("=" * 60)
        print()

        self.run_bootstrap()
        self.run_syntax_check()
        self.run_python_manager()
        self.run_kilo_local()

        print()
        self.log("Full check complete", "OK")

    def main(self):
        print()
        print("=" * 60)
        print(f"AZAZAEL CLI v{self.VERSION}")
        print("=" * 60)
        print()
        self.print_menu()

        while True:
            try:
                cmd = input("Azazael> ").strip().lower()
            except KeyboardInterrupt:
                print("\nExiting...")
                break

            if not cmd:
                continue

            if cmd in ["quit", "exit", "q"]:
                print("Goodbye!")
                break

            elif cmd == "help" or cmd == "?":
                self.print_menu()

            elif cmd == "bootstrap":
                self.run_bootstrap()

            elif cmd.startswith("worm "):
                worm_name = cmd.split(" ")[1]
                self.run_worm(worm_name)

            elif cmd == "interpreter":
                self.run_open_interpreter()

            elif cmd in ["kilo", "local"]:
                self.run_kilo_local()

            elif cmd == "python":
                self.run_python_manager()

            elif cmd == "syntax":
                self.run_syntax_check()

            elif cmd == "health":
                self.run_health_check()

            elif cmd == "all":
                self.run_all_checks()

            else:
                print(f"Unknown command: {cmd}")
                print("Type 'help' for available commands.")


if __name__ == "__main__":
    cli = AzazaelCLI()
    cli.main()
