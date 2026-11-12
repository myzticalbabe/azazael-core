#!/usr/bin/env python3
"""
Azazael Go Interpreter Runner
===============================
Subprocess-based Go code runner with:
    - Run .go files via 'go run'
    - Build binaries via 'go build'
    - Module-aware execution (go.mod detection)
    - Test runner integration
    - Formatting via gofmt
    - Cross-compilation support
"""

import os
import shutil
import hashlib
import subprocess
import time
from pathlib import Path
# from typing import Dict, List, Optional, Tuple  # removed — all names have native equivalents
from dataclasses import dataclass, field
import argparse
from typing import List, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# --- Configuration -----------------------------------------------------------

try:
    from CORE.paths import get_project_root_safe as _get_project_root_safe
except ImportError as exc:                               # never break the host
    note_failure("go_runner:core-paths-import", exc)
    _get_project_root_safe = None

#: Repo root for the env fallback below. MEASURED 2026-10-11: the fallback used to be
#: `Path.home() / "azazael"` -- a DIFFERENT, stale checkout from this repo -- so with
#: AZAZAEL_ROOT unset this runner resolved the WRONG tree and forwarded it to the child
#: process it spawns. `parents[2]` (this file's own tree) answers only the case where
#: CORE.paths is unimportable.
_DEFAULT_ROOT = (str(_get_project_root_safe()) if _get_project_root_safe is not None
                 else str(Path(__file__).resolve().parents[2]))

AZAZAEL_ROOT = os.environ.get(
    "AZAZAEL_ROOT", os.environ.get("Azazael_ROOT", _DEFAULT_ROOT)
)

GO_BUILD_DIR = Path(AZAZAEL_ROOT) / "build" / "go"
GO_BUILD_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class GoResult:
    """Result of a Go code execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    duration_ms: float = 0.0
    binary_path: (str | None) = None
    errors: list[dict] = field(default_factory=list)
    stderr_bytes: str = ""  # D7: raw stderr (latin-1) for forensics


class GoRunner:
    """Compile and run Go code via subprocess."""

    def __init__(self, goos: str = "", goarch: str = ""):
        self.goos = goos  # Cross-compile target OS
        self.goarch = goarch  # Cross-compile target arch
        self._verify_toolchain()

    def _verify_toolchain(self) -> bool:
        """Check that go is available."""
        self.has_go = shutil.which("go") is not None
        if not self.has_go:
            print("  [WARN] go not found. Install: https://go.dev/dl/")
        return self.has_go

    def _go_env(self) -> dict:
        """Build environment dict with optional cross-compilation."""
        env = os.environ.copy()
        if self.goos:
            env["GOOS"] = self.goos
        if self.goarch:
            env["GOARCH"] = self.goarch
        return env

    def _find_module_root(self, file_path: Path) -> (Path | None):
        """Walk up directories to find go.mod."""
        current = file_path.parent
        for _ in range(10):
            if (current / "go.mod").exists():
                return current
            if current.parent == current:
                break
            current = current.parent
        return None

    def run_file(self, file_path: str, args: Optional[list[str]] = None) -> GoResult:
        """Run a .go file using 'go run'."""
        if not self.has_go:
            return GoResult(
                success=False,
                stderr="go not found. Install Go toolchain.",
                errors=[{"type": "toolchain", "message": "go missing"}],
            )

        source = Path(file_path)
        if not source.exists():
            return GoResult(
                success=False,
                stderr=f"File not found: {file_path}",
                errors=[{"type": "file", "message": f"Missing: {file_path}"}],
            )

        cmd = ["go", "run", str(source)] + (args or [])
        module_root = self._find_module_root(source)

        try:
            start = time.time()
            # D7: bytes capture; display via replace, evidence via stderr_bytes.
            result = subprocess.run(
                cmd,
                cwd=str(module_root or source.parent),
                env=self._go_env(),
                capture_output=True,
                timeout=60,
            )
            duration = (time.time() - start) * 1000
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")

            return GoResult(
                success=result.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                returncode=result.returncode,
                duration_ms=duration,
                errors=self._parse_errors(stderr) if result.returncode != 0 else [],
                stderr_bytes=result.stderr[:4000].decode("latin-1"),
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("go_runner:L122", exc)
            return GoResult(
                success=False,
                stderr="Execution timed out (>60s)",
                errors=[{"type": "timeout", "message": "Runtime exceeded 60 seconds"}],
            )
        except Exception as e:
            note_failure("go_runner:L128", e)
            return GoResult(
                success=False, stderr=str(e),
                errors=[{"type": "exception", "message": str(e)}],
            )

    def run_code(self, code: str, args: Optional[list[str]] = None) -> GoResult:
        """Run Go source code from a string (honest timing, P1-item-1).

        `go run` compiles then executes in one process: the reported
        duration covers both, matching RustRunner.run_code's compile+run
        sum. Comparable numbers or no comparison."""
        code_hash = hashlib.md5(code.encode()).hexdigest()[:12]
        source_path = GO_BUILD_DIR / f"azazael_{code_hash}.go"
        t_write = time.time()
        source_path.write_text(code, encoding="utf-8")
        write_ms = (time.time() - t_write) * 1000
        result = self.run_file(str(source_path), args)
        # run_file already includes compile+run; add only the write cost
        # so cold-recompute totals stay honest across languages.
        result.duration_ms += write_ms
        return result

    def build_binary(self, file_path: str, output_name: str = "",
                     release: bool = False) -> GoResult:
        """Build a .go file into a standalone binary."""
        source = Path(file_path)
        if not source.exists():
            return GoResult(
                success=False,
                stderr=f"File not found: {file_path}",
            )

        output = GO_BUILD_DIR / (output_name or source.stem)
        if self.goos == "windows":
            output = output.with_suffix(".exe")

        cmd = ["go", "build", "-o", str(output)]
        if release:
            cmd.insert(2, "-ldflags=-s -w")  # Strip debug info

        cmd.append(str(source))
        module_root = self._find_module_root(source)

        try:
            start = time.time()
            result = subprocess.run(
                cmd,
                cwd=str(module_root or source.parent),
                env=self._go_env(),
                capture_output=True,
                timeout=120,
            )
            duration = (time.time() - start) * 1000
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")

            binary_path = str(output) if result.returncode == 0 and output.exists() else None
            return GoResult(
                success=result.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                returncode=result.returncode,
                duration_ms=duration,
                binary_path=binary_path,
                errors=self._parse_errors(stderr) if result.returncode != 0 else [],
                stderr_bytes=result.stderr[:4000].decode("latin-1"),
            )
        except Exception as e:
            note_failure("go_runner:L184", e)
            return GoResult(success=False, stderr=str(e))

    def _parse_errors(self, stderr: str) -> list[dict]:
        """Parse Go compiler error output."""
        errors: list[dict] = []
        for line in stderr.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Go errors: file:line:col: message
            parts = line.split(":", 3)
            if len(parts) >= 4 and parts[0].endswith(".go"):
                errors.append({
                    "level": "error",
                    "file": parts[0].strip(),
                    "line": parts[1].strip(),
                    "col": parts[2].strip(),
                    "message": parts[3].strip(),
                })
            else:
                errors.append({"level": "error", "message": line})
        return errors

    def test(self, project_dir: str = ".", verbose: bool = False) -> GoResult:
        """Run 'go test' in a project directory."""
        if not self.has_go:
            return GoResult(success=False, stderr="go not found.")

        cmd = ["go", "test", "./..."]
        if verbose:
            cmd.insert(2, "-v")

        try:
            result = subprocess.run(
                cmd, cwd=project_dir, env=self._go_env(),
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
            return GoResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
            )
        except Exception as e:
            note_failure("go_runner:L228", e)
            return GoResult(success=False, stderr=str(e))

    def format_code(self, file_path: str) -> GoResult:
        """Format Go code with gofmt."""
        if not self.has_go:
            return GoResult(success=False, stderr="go not found.")

        try:
            result = subprocess.run(
                ["gofmt", "-w", str(file_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            return GoResult(
                success=result.returncode == 0,
                stdout="Formatted" if result.returncode == 0 else result.stderr,
            )
        except Exception as e:
            note_failure("go_runner:L245", e)
            return GoResult(success=False, stderr=str(e))

    def vet(self, project_dir: str = ".") -> GoResult:
        """Run 'go vet' for static analysis."""
        if not self.has_go:
            return GoResult(success=False, stderr="go not found.")

        try:
            result = subprocess.run(
                ["go", "vet", "./..."],
                cwd=project_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            return GoResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        except Exception as e:
            note_failure("go_runner:L263", e)
            return GoResult(success=False, stderr=str(e))

    def mod_init(self, module_name: str, directory: Optional[str] = None) -> GoResult:
        """Initialize a new Go module with 'go mod init'."""
        if not self.has_go:
            return GoResult(success=False, stderr="go not found.")

        target = Path(directory or GO_BUILD_DIR)
        target.mkdir(parents=True, exist_ok=True)

        try:
            result = subprocess.run(
                ["go", "mod", "init", module_name],
                cwd=str(target), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            return GoResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                binary_path=str(target / "go.mod"),
            )
        except Exception as e:
            note_failure("go_runner:L285", e)
            return GoResult(success=False, stderr=str(e))

    def cross_compile(self, file_path: str, targets: Optional[list[tuple[str, str]]] = None
                      ) -> dict[str, GoResult]:
        """Build for multiple OS/arch targets."""
        if targets is None:
            targets = [
                ("linux", "amd64"),
                ("windows", "amd64"),
                ("darwin", "amd64"),
            ]

        results: dict[str, GoResult] = {}
        for goos, goarch in targets:
            key = f"{goos}/{goarch}"
            original_goos, original_goarch = self.goos, self.goarch
            self.goos, self.goarch = goos, goarch

            source = Path(file_path)
            output_name = f"{source.stem}_{goos}_{goarch}"
            results[key] = self.build_binary(file_path, output_name)

            self.goos, self.goarch = original_goos, original_goarch

        return results


# --- CLI ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Azazael Go Interpreter Runner")
    sub = parser.add_subparsers(dest="command")

    # Run
    p = sub.add_parser("run", help="Run .go file")
    p.add_argument("file", help=".go file to run")
    p.add_argument("args", nargs="*", help="Arguments to pass")

    # Code
    p = sub.add_parser("code", help="Run inline Go code")
    p.add_argument("code", help="Go source code as string")
    p.add_argument("args", nargs="*", help="Arguments to pass")

    # Build
    p = sub.add_parser("build", help="Build Go binary")
    p.add_argument("file", help=".go file to build")
    p.add_argument("--output", help="Output binary name")
    p.add_argument("--release", action="store_true", help="Stripped release build")
    p.add_argument("--goos", help="Target OS (cross-compile)")
    p.add_argument("--goarch", help="Target arch (cross-compile)")

    # Test
    p = sub.add_parser("test", help="Run go test")
    p.add_argument("project", nargs="?", default=".", help="Project directory")
    p.add_argument("-v", "--verbose", action="store_true")

    # Vet
    p = sub.add_parser("vet", help="Run go vet")
    p.add_argument("project", nargs="?", default=".", help="Project directory")

    # Init
    p = sub.add_parser("init", help="Initialize Go module")
    p.add_argument("module", help="Module name (e.g., github.com/user/project)")

    # Format
    p = sub.add_parser("fmt", help="Format Go file")
    p.add_argument("file", help=".go file to format")

    args = parser.parse_args()

    goos = getattr(args, "goos", "")
    goarch = getattr(args, "goarch", "")
    runner = GoRunner(goos=goos, goarch=goarch)

    if args.command == "run":
        result = runner.run_file(args.file, args.args)
    elif args.command == "code":
        result = runner.run_code(args.code, args.args)
    elif args.command == "build":
        result = runner.build_binary(args.file, args.output, args.release)
    elif args.command == "test":
        result = runner.test(args.project, args.verbose)
    elif args.command == "vet":
        result = runner.vet(args.project)
    elif args.command == "init":
        result = runner.mod_init(args.module)
    elif args.command == "fmt":
        result = runner.format_code(args.file)
    else:
        parser.print_help()
        return

    if result.success:
        print(f"  [OK] Success ({result.duration_ms:.0f}ms)")
        if result.stdout:
            print(result.stdout)
        if result.binary_path:
            print(f"  Binary: {result.binary_path}")
    else:
        print(f"  [X] Failed ({result.returncode})")
        if result.stderr:
            print(result.stderr)
    for err in result.errors:
        msg = err.get("message", "")
        loc = f"{err.get('file', '')}:{err.get('line', '')}" if err.get("file") else ""
        print(f"    [{err.get('level', 'error')}] {loc} {msg}")


if __name__ == "__main__":
    main()
