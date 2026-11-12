#!/usr/bin/env python3
"""Azazael Node Interpreter Runner (JavaScript + TypeScript).

Subprocess-based Node.js runner mirroring the GoRunner/RustRunner shape:
    - Run .js files via `node`
    - Run inline code (hashed temp file under build/node)
    - Syntax check via `node --check` (parse, never execute)
    - Test via `node --test` (Node 18+ built-in runner)
    - Type-strip run for .ts files on Node 22+ (`--experimental-strip-types`
      only when the runtime accepts the flag; otherwise a clean skip)
    - Format via prettier when the binary is installed (clean skip otherwise)

Toolchain absence is a structured error, never a crash.
"""

import hashlib
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
import argparse
from typing import List, Optional

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


try:
    from CORE.paths import get_project_root_safe as _get_project_root_safe
except ImportError as exc:                               # never break the host
    note_failure("node_runner:core-paths-import", exc)
    _get_project_root_safe = None

_DEFAULT_ROOT = (str(_get_project_root_safe()) if _get_project_root_safe is not None
                 else str(Path(__file__).resolve().parents[2]))

AZAZAEL_ROOT = os.environ.get(
    "AZAZAEL_ROOT", os.environ.get("Azazael_ROOT", _DEFAULT_ROOT)
)

NODE_BUILD_DIR = Path(AZAZAEL_ROOT) / "build" / "node"
NODE_BUILD_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class NodeResult:
    """Result of a Node.js execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    duration_ms: float = 0.0
    errors: List[dict] = field(default_factory=list)
    # D7: raw stderr bytes (latin-1 round-trips every byte) for forensics.
    # Display uses stderr (errors=replace); evidence uses stderr_bytes.
    stderr_bytes: str = ""


class NodeRunner:
    """Run JavaScript/TypeScript via the node binary (argv lists, no shell)."""

    def __init__(self) -> None:
        self._verify_toolchain()

    def _verify_toolchain(self) -> bool:
        """Check that node is available; record version when it is."""
        self.node_bin: str = shutil.which("node") or ""
        self.has_node = bool(self.node_bin)
        self.version = ""
        if not self.has_node:
            print("  [WARN] node not found. Install: https://nodejs.org/")
            return False
        try:
            out = subprocess.run(
                [self.node_bin, "--version"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=15,
            )
            self.version = out.stdout.strip()
        except Exception as e:
            note_failure("node_runner:version", e)
        return True

    def _no_toolchain(self) -> NodeResult:
        return NodeResult(
            success=False,
            stderr="node not found. Install Node.js.",
            errors=[{"type": "toolchain", "message": "node missing"}],
        )

    def _parse_errors(self, stderr: str) -> List[dict]:
        """Parse node/V8 error output into structured records."""
        errors: List[dict] = []
        for line in stderr.split("\n"):
            line = line.strip()
            if not line:
                continue
            # V8 stack frame: at fn (/path/file.js:line:col)
            if line.startswith("at ") and "(" in line and line.endswith(")"):
                inner = line[line.rfind("(") + 1:-1]
                parts = inner.split(":")
                if len(parts) >= 3 and parts[0].strip().endswith((".js", ".ts", ".mjs", ".cjs")):
                    errors.append({
                        "level": "error",
                        "file": parts[0].strip(),
                        "line": parts[-2].strip(),
                        "col": parts[-1].strip(),
                        "message": line,
                    })
                    continue
            if line.startswith(("SyntaxError", "ReferenceError", "TypeError",
                                "Error", "RangeError")):
                errors.append({"level": "error", "message": line})
            elif errors and line.startswith(("at ", "    at ")):
                errors.append({"level": "trace", "message": line})
        return errors

    def _run(self, cmd: List[str], cwd: str, timeout: int) -> NodeResult:
        try:
            start = time.time()
            # D7: capture bytes, decode manually — display degrades via
            # replace, but stderr_bytes keeps every byte for forensics.
            result = subprocess.run(
                cmd, cwd=cwd, capture_output=True, timeout=timeout,
            )
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")
            return NodeResult(
                success=result.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                returncode=result.returncode,
                duration_ms=(time.time() - start) * 1000,
                errors=self._parse_errors(stderr) if result.returncode != 0 else [],
                stderr_bytes=result.stderr[:4000].decode("latin-1"),
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("node_runner:timeout", exc)
            return NodeResult(
                success=False,
                stderr=f"Execution timed out (>{timeout}s)",
                errors=[{"type": "timeout", "message": f"Runtime exceeded {timeout} seconds"}],
            )
        except Exception as e:
            note_failure("node_runner:exec", e)
            return NodeResult(
                success=False, stderr=str(e),
                errors=[{"type": "exception", "message": str(e)}],
            )

    def run_file(self, file_path: str, args: Optional[List[str]] = None) -> NodeResult:
        """Run a .js/.mjs/.cjs file using node."""
        if not self.has_node:
            return self._no_toolchain()
        source = Path(file_path)
        if not source.exists():
            return NodeResult(
                success=False,
                stderr=f"File not found: {file_path}",
                errors=[{"type": "file", "message": f"Missing: {file_path}"}],
            )
        cmd = [self.node_bin, str(source)] + (args or [])
        return self._run(cmd, cwd=str(source.parent), timeout=60)

    def run_code(self, code: str, args: Optional[List[str]] = None) -> NodeResult:
        """Run inline JavaScript (written to a hashed temp file)."""
        if not self.has_node:
            return self._no_toolchain()
        code_hash = hashlib.md5(code.encode()).hexdigest()[:12]
        source_path = NODE_BUILD_DIR / f"azazael_{code_hash}.js"
        source_path.write_text(code, encoding="utf-8")
        cmd = [self.node_bin, str(source_path)] + (args or [])
        return self._run(cmd, cwd=str(NODE_BUILD_DIR), timeout=60)

    def check_syntax(self, file_path: str) -> NodeResult:
        """Parse-only check via `node --check` (never executes)."""
        if not self.has_node:
            return self._no_toolchain()
        source = Path(file_path)
        if not source.exists():
            return NodeResult(success=False, stderr=f"File not found: {file_path}")
        return self._run([self.node_bin, "--check", str(source)],
                         cwd=str(source.parent), timeout=30)

    def test(self, target: str = ".", verbose: bool = False) -> NodeResult:
        """Run the built-in test runner (`node --test`) on a file or dir."""
        if not self.has_node:
            return self._no_toolchain()
        cmd = [self.node_bin, "--test"]
        if verbose:
            cmd.append("--test-reporter=spec")
        cmd.append(target)
        base = target if Path(target).is_dir() else str(Path(target).parent)
        return self._run(cmd, cwd=base, timeout=120)

    def format_code(self, file_path: str) -> NodeResult:
        """Format with prettier when installed; clean skip otherwise."""
        prettier = shutil.which("prettier")
        if not prettier:
            return NodeResult(
                success=False,
                stderr="prettier not found; skipping format.",
                errors=[{"type": "toolchain", "message": "prettier missing (optional)"}],
            )
        return self._run([prettier, "--write", str(file_path)],
                         cwd=str(Path(file_path).parent), timeout=30)


# --- CLI ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Azazael Node Interpreter Runner")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("run", help="Run .js file")
    p.add_argument("file", help=".js file to run")
    p.add_argument("args", nargs="*", help="Arguments to pass")

    p = sub.add_parser("code", help="Run inline JavaScript")
    p.add_argument("code", help="JavaScript source as string")
    p.add_argument("args", nargs="*", help="Arguments to pass")

    p = sub.add_parser("check", help="Syntax check only")
    p.add_argument("file", help=".js file to check")

    p = sub.add_parser("test", help="Run node --test")
    p.add_argument("target", nargs="?", default=".", help="Test file or directory")
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("fmt", help="Format with prettier")
    p.add_argument("file", help=".js file to format")

    args = parser.parse_args()
    runner = NodeRunner()

    if args.command == "run":
        result = runner.run_file(args.file, args.args)
    elif args.command == "code":
        result = runner.run_code(args.code, args.args)
    elif args.command == "check":
        result = runner.check_syntax(args.file)
    elif args.command == "test":
        result = runner.test(args.target, args.verbose)
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
