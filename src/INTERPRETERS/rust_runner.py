#!/usr/bin/env python3
"""
Azazael Rust Interpreter Runner
=================================
Subprocess-based Rust code runner with:
    - Compile & run .rs files
    - Cargo project detection & build
    - Syntax checking via rustc --check
    - Test runner integration
    - Error parsing and formatting
"""

import os
import shutil
import hashlib
import subprocess
import time
from pathlib import Path
# from typing import Dict, List, Optional  # removed — all names have native equivalents
from dataclasses import dataclass, field
from enum import Enum
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
    note_failure("rust_runner:core-paths-import", exc)
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

RUST_BUILD_DIR = Path(AZAZAEL_ROOT) / "build" / "rust"
RUST_BUILD_DIR.mkdir(parents=True, exist_ok=True)


class RustEdition(Enum):
    E2015 = "2015"
    E2018 = "2018"
    E2021 = "2021"
    E2024 = "2024"


@dataclass
class RustResult:
    """Result of a Rust code execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    duration_ms: float = 0.0
    binary_path: (str | None) = None
    errors: list[dict] = field(default_factory=list)
    stderr_bytes: str = ""  # D7: raw stderr (latin-1) for forensics


class RustRunner:
    """Compile and run Rust code via subprocess."""

    def __init__(self, edition: RustEdition = RustEdition.E2021,
                 opt_level: int = 0, debug: bool = True):
        self.edition = edition
        self.opt_level = opt_level
        self.debug = debug
        self._verify_toolchain()

    def _verify_toolchain(self) -> bool:
        """Check that rustc and cargo are available."""
        self.has_rustc = shutil.which("rustc") is not None
        self.has_cargo = shutil.which("cargo") is not None

        if not self.has_rustc:
            print("  [WARN] rustc not found. Install: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh")
        return self.has_rustc

    def _compile(self, source_path: Path, output_path: Path) -> RustResult:
        """Compile a single .rs file to a binary."""
        if not self.has_rustc:
            return RustResult(
                success=False,
                stderr="rustc not found. Install Rust toolchain.",
                errors=[{"type": "toolchain", "message": "rustc missing"}],
            )

        cmd = [
            "rustc",
            str(source_path),
            "-o", str(output_path),
            "--edition", self.edition.value,
            "-C", f"opt-level={self.opt_level}",
        ]
        if self.debug:
            cmd.append("-g")

        try:
            start = time.time()
            # D7: bytes capture; display via replace, evidence via stderr_bytes.
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            duration = (time.time() - start) * 1000
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")

            return RustResult(
                success=result.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                returncode=result.returncode,
                duration_ms=duration,
                binary_path=str(output_path) if result.returncode == 0 else None,
                errors=self._parse_errors(stderr) if result.returncode != 0 else [],
                stderr_bytes=result.stderr[:4000].decode("latin-1"),
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("rust_runner:L106", exc)
            return RustResult(
                success=False,
                stderr="Compilation timed out (>60s)",
                errors=[{"type": "timeout", "message": "Compilation exceeded 60 seconds"}],
            )
        except Exception as e:
            note_failure("rust_runner:L112", e)
            return RustResult(
                success=False,
                stderr=str(e),
                errors=[{"type": "exception", "message": str(e)}],
            )

    def _parse_errors(self, stderr: str) -> list[dict]:
        """Parse rustc error output into structured format."""
        errors: list[dict] = []
        for line in stderr.split("\n"):
            line = line.strip()
            if not line or line.startswith("  =") or line.startswith("  |"):
                continue
            if line.startswith("error") or line.startswith("warning"):
                parts = line.split(":", 3)
                if len(parts) >= 4:
                    errors.append({
                        "level": parts[0].strip(),
                        "file": parts[1].strip() if len(parts) > 1 else "",
                        "line": parts[2].strip() if len(parts) > 2 else "",
                        "message": parts[3].strip() if len(parts) > 3 else line,
                    })
                else:
                    errors.append({"level": "error", "message": line})
        return errors

    def _run_binary(self, binary_path: Path, args: Optional[list[str]] = None) -> RustResult:
        """Execute a compiled Rust binary."""
        cmd = [str(binary_path)] + (args or [])
        try:
            start = time.time()
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            duration = (time.time() - start) * 1000
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")

            return RustResult(
                success=result.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                returncode=result.returncode,
                duration_ms=duration,
                binary_path=str(binary_path),
                stderr_bytes=result.stderr[:4000].decode("latin-1"),
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("rust_runner:L155", exc)
            return RustResult(
                success=False,
                stderr="Execution timed out (>30s)",
                errors=[{"type": "timeout", "message": "Runtime exceeded 30 seconds"}],
            )

    def run_code(self, code: str, args: Optional[list[str]] = None) -> RustResult:
        """Compile and run Rust source code from a string."""
        # Hash the code to create a unique filename
        code_hash = hashlib.md5(code.encode()).hexdigest()[:12]
        source_path = RUST_BUILD_DIR / f"azazael_{code_hash}.rs"
        binary_path = RUST_BUILD_DIR / f"azazael_{code_hash}"

        # Write source
        source_path.write_text(code, encoding="utf-8")

        # Compile
        compile_result = self._compile(source_path, binary_path)
        if not compile_result.success:
            return compile_result

        # Run (cold recompute: caller pays compile + run, honestly summed)
        run_result = self._run_binary(binary_path, args)
        run_result.duration_ms += compile_result.duration_ms
        return run_result

    def run_file(self, file_path: str, args: Optional[list[str]] = None) -> RustResult:
        """Compile and run a .rs file."""
        source = Path(file_path)
        if not source.exists():
            return RustResult(
                success=False,
                stderr=f"File not found: {file_path}",
                errors=[{"type": "file", "message": f"Missing: {file_path}"}],
            )

        binary = RUST_BUILD_DIR / f"{source.stem}"
        compile_result = self._compile(source, binary)
        if not compile_result.success:
            return compile_result

        return self._run_binary(binary, args)

    def check_syntax(self, file_path: str) -> RustResult:
        """Check syntax without producing a binary (--check only)."""
        source = Path(file_path)
        if not source.exists():
            return RustResult(
                success=False,
                stderr=f"File not found: {file_path}",
            )

        cmd = ["rustc", "--edition", self.edition.value, "--check", str(source)]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")
            return RustResult(
                success=result.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                returncode=result.returncode,
                errors=self._parse_errors(stderr) if result.returncode != 0 else [],
                stderr_bytes=result.stderr[:4000].decode("latin-1"),
            )
        except Exception as e:
            note_failure("rust_runner:L216", e)
            return RustResult(success=False, stderr=str(e))

    def cargo_build(self, project_dir: str, release: bool = False) -> RustResult:
        """Build a Cargo project."""
        if not self.has_cargo:
            return RustResult(
                success=False,
                stderr="cargo not found. Install Rust toolchain.",
            )

        cmd = ["cargo", "build"]
        if release:
            cmd.append("--release")

        try:
            result = subprocess.run(
                cmd, cwd=project_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
            )
            return RustResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
                errors=self._parse_errors(result.stderr) if result.returncode != 0 else [],
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("rust_runner:L242", exc)
            return RustResult(success=False, stderr="Cargo build timed out (>120s)")
        except Exception as e:
            note_failure("rust_runner:L244", e)
            return RustResult(success=False, stderr=str(e))

    def cargo_test(self, project_dir: str) -> RustResult:
        """Run cargo test for a project."""
        if not self.has_cargo:
            return RustResult(success=False, stderr="cargo not found.")

        cmd = ["cargo", "test"]
        try:
            result = subprocess.run(
                cmd, cwd=project_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
            )
            return RustResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
            )
        except Exception as e:
            note_failure("rust_runner:L263", e)
            return RustResult(success=False, stderr=str(e))

    def new_project(self, name: str, directory: Optional[str] = None) -> RustResult:
        """Create a new Cargo project."""
        if not self.has_cargo:
            return RustResult(success=False, stderr="cargo not found.")

        target = Path(directory or RUST_BUILD_DIR)
        cmd = ["cargo", "new", name]
        try:
            result = subprocess.run(
                cmd, cwd=str(target), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
            )
            return RustResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                binary_path=str(target / name),
            )
        except Exception as e:
            note_failure("rust_runner:L283", e)
            return RustResult(success=False, stderr=str(e))

    def format_code(self, file_path: str) -> RustResult:
        """Format Rust code with rustfmt."""
        if not shutil.which("rustfmt"):
            return RustResult(success=False, stderr="rustfmt not found.")

        try:
            result = subprocess.run(
                ["rustfmt", str(file_path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
            )
            return RustResult(
                success=result.returncode == 0,
                stdout="Formatted" if result.returncode == 0 else result.stderr,
            )
        except Exception as e:
            note_failure("rust_runner:L299", e)
            return RustResult(success=False, stderr=str(e))


# --- CLI ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Azazael Rust Interpreter Runner")
    sub = parser.add_subparsers(dest="command")

    # Run
    p = sub.add_parser("run", help="Compile and run Rust code")
    p.add_argument("file", help=".rs file to run")
    p.add_argument("args", nargs="*", help="Arguments to pass to binary")

    # Code
    p = sub.add_parser("code", help="Run inline Rust code")
    p.add_argument("code", help="Rust source code as string")
    p.add_argument("args", nargs="*", help="Arguments to pass to binary")

    # Check
    p = sub.add_parser("check", help="Syntax check only")
    p.add_argument("file", help=".rs file to check")

    # Build
    p = sub.add_parser("build", help="Build Cargo project")
    p.add_argument("project", nargs="?", default=".", help="Cargo project directory")
    p.add_argument("--release", action="store_true", help="Release build")

    # Test
    p = sub.add_parser("test", help="Run cargo test")
    p.add_argument("project", nargs="?", default=".", help="Cargo project directory")

    # New
    p = sub.add_parser("new", help="Create new Cargo project")
    p.add_argument("name", help="Project name")

    # Format
    p = sub.add_parser("fmt", help="Format Rust file")
    p.add_argument("file", help=".rs file to format")

    args = parser.parse_args()
    runner = RustRunner()

    if args.command == "run":
        result = runner.run_file(args.file, args.args)
    elif args.command == "code":
        result = runner.run_code(args.code, args.args)
    elif args.command == "check":
        result = runner.check_syntax(args.file)
    elif args.command == "build":
        result = runner.cargo_build(args.project, args.release)
    elif args.command == "test":
        result = runner.cargo_test(args.project)
    elif args.command == "new":
        result = runner.new_project(args.name)
    elif args.command == "fmt":
        result = runner.format_code(args.file)
    else:
        parser.print_help()
        return

    if result.success:
        print(f"  [OK] Success ({result.duration_ms:.0f}ms)")
        if result.stdout:
            print(result.stdout)
    else:
        print(f"  [X] Failed ({result.returncode})")
        if result.stderr:
            print(result.stderr)
    for err in result.errors:
        print(f"    [{err.get('level', 'error')}] {err.get('message', '')}")


if __name__ == "__main__":
    main()
