#!/usr/bin/env python3
"""Azazael Shell Runner (bash/sh).

Subprocess-based runner mirroring the GoRunner/RustRunner shape:
    - Run .sh files via the bash binary
    - Run inline scripts (hashed temp file under build/sh)
    - Syntax check via `bash -n` (parse, never execute)
    - Lint via shellcheck when installed (clean skip otherwise)

argv lists throughout -- never shell=True. The run_shell_untrusted()
helper in __init__.py is the designated host-shell path; this module is
the structured replacement for file/code execution.
"""

import hashlib
import os
import re
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
    note_failure("sh_runner:core-paths-import", exc)
    _get_project_root_safe = None

def _bash_flavor(bash_bin=""):
    """Which POSIX mapping the bash binary expects: 'wsl' or 'msys'.

    WSL bash wants ``/mnt/c/...``; Git-Bash / MSYS2 / Cygwin bash want
    ``/c/...``. The old code assumed WSL always, which broke every bash
    run on boxes (like this one) where ``bash`` resolves to Git-Bash.
    WSL is detected by the binary name or the WSL distro marker; the
    MSYS family is the default because it is what ``which bash`` finds
    on a stock Windows dev box.
    """
    b = (bash_bin or "").lower().replace(chr(92), "/")
    if b.endswith("wsl.exe") or os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    return "msys"


def _to_bash_path(path, bash_bin=""):
    r"""Convert a Windows absolute path to the POSIX form WSL/bash expects.

    ``bash`` rejects ``C:\Azazael\...`` and silently drops the
    backslashes, so a script path passed as a Windows path arrives
    mangled (``C:Azazaelazazael...``). Convert to the flavored form:
    ``/c/...`` for MSYS/Git-Bash, ``/mnt/c/...`` for WSL bash.
    Both ``C:\`` and ``C:/`` prefixes are recognised.
    """
    if os.name != "nt":
        return path
    p = str(path)
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
    if not m:
        return p
    rest = m.group(2).replace(chr(92), "/")
    if _bash_flavor(bash_bin) == "wsl":
        return "/mnt/%s/%s" % (m.group(1).lower(), rest)
    return "/%s/%s" % (m.group(1).lower(), rest)


_DEFAULT_ROOT = (str(_get_project_root_safe()) if _get_project_root_safe is not None
                 else str(Path(__file__).resolve().parents[2]))

AZAZAEL_ROOT = os.environ.get(
    "AZAZAEL_ROOT", os.environ.get("Azazael_ROOT", _DEFAULT_ROOT)
)
SH_BUILD_DIR = Path(AZAZAEL_ROOT) / "build" / "sh"
SH_BUILD_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ShResult:
    """Result of a shell execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    duration_ms: float = 0.0
    errors: List[dict] = field(default_factory=list)
    stderr_bytes: str = ""  # D7: raw stderr (latin-1) for forensics


class ShRunner:
    """Run shell scripts via the bash binary (argv lists, no shell)."""

    def __init__(self) -> None:
        self._verify_toolchain()

    def _verify_toolchain(self) -> bool:
        """Check that a bash binary is available."""
        self.bash_bin: str = shutil.which("bash") or ""
        self.has_bash = bool(self.bash_bin)
        self.version = ""
        if not self.has_bash:
            print("  [WARN] bash not found. Install Git Bash or WSL.")
            return False
        try:
            out = subprocess.run(
                [self.bash_bin, "--version"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=15,
            )
            self.version = out.stdout.split("\n")[0].strip() if out.stdout else ""
        except Exception as e:
            note_failure("sh_runner:version", e)
        return True

    def _no_toolchain(self) -> ShResult:
        return ShResult(
            success=False,
            stderr="bash not found. Install Git Bash or WSL.",
            errors=[{"type": "toolchain", "message": "bash missing"}],
        )

    def _run(self, cmd: List[str], cwd: str, timeout: int) -> ShResult:
        # D7: capture raw bytes; display decodes with replace, but on
        # failure the raw hex is kept in stderr_raw_hex for forensics.
        try:
            start = time.time()
            result = subprocess.run(
                cmd, cwd=cwd, capture_output=True, timeout=timeout,
            )
            stdout = result.stdout.decode("utf-8", errors="replace") if isinstance(result.stdout, (bytes, bytearray)) else (result.stdout or "")
            stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, (bytes, bytearray)) else (result.stderr or "")
            errors: List[dict] = []
            raw = ""
            if result.returncode != 0:
                if isinstance(result.stderr, (bytes, bytearray)) and result.stderr:
                    raw = bytes(result.stderr[:4000]).decode("latin-1")
                if stderr.strip():
                    errors = [{"level": "error", "message": stderr.strip().split("\n")[0]}]
            return ShResult(
                success=result.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                returncode=result.returncode,
                duration_ms=(time.time() - start) * 1000,
                errors=errors,
                stderr_bytes=raw,
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("sh_runner:timeout", exc)
            return ShResult(
                success=False,
                stderr=f"Execution timed out (>{timeout}s)",
                errors=[{"type": "timeout", "message": f"Runtime exceeded {timeout} seconds"}],
            )
        except Exception as e:
            note_failure("sh_runner:exec", e)
            return ShResult(
                success=False, stderr=str(e),
                errors=[{"type": "exception", "message": str(e)}],
            )

    def run_file(self, file_path: str, args: Optional[List[str]] = None) -> ShResult:
        """Run a .sh file via bash."""
        if not self.has_bash:
            return self._no_toolchain()
        source = Path(file_path)
        if not source.exists():
            return ShResult(
                success=False,
                stderr=f"File not found: {file_path}",
                errors=[{"type": "file", "message": f"Missing: {file_path}"}],
            )
        return self._run([self.bash_bin,
                          _to_bash_path(str(source), self.bash_bin)]
                         + (args or []),
                         cwd=str(source.parent), timeout=60)

    def run_code(self, code: str, args: Optional[List[str]] = None) -> ShResult:
        """Run an inline script (written to a hashed temp file).

        D6 contract: args are literal argv strings, NO shell evaluation."""
        if not self.has_bash:
            return self._no_toolchain()
        code_hash = hashlib.md5(code.encode()).hexdigest()[:12]
        source_path = SH_BUILD_DIR / f"azazael_{code_hash}.sh"
        source_path.write_text(code, encoding="utf-8")
        return self._run([self.bash_bin,
                          _to_bash_path(str(source_path), self.bash_bin)]
                         + (args or []),
                         cwd=str(SH_BUILD_DIR), timeout=60)

    def run_command(self, argv: List[str], cwd: str = ".") -> ShResult:
        """Run an explicit argv command (no shell interpretation)."""
        if not self.has_bash:
            return self._no_toolchain()
        if not argv:
            return ShResult(success=False, stderr="Empty command.",
                            errors=[{"type": "args", "message": "empty argv"}])
        return self._run(list(argv), cwd=cwd, timeout=60)

    def check_syntax(self, file_path: str) -> ShResult:
        """Parse-only check via `bash -n` (never executes)."""
        if not self.has_bash:
            return self._no_toolchain()
        source = Path(file_path)
        if not source.exists():
            return ShResult(success=False, stderr=f"File not found: {file_path}")
        return self._run([self.bash_bin, "-n",
                          _to_bash_path(str(source), self.bash_bin)],
                         cwd=str(source.parent), timeout=30)

    def lint(self, file_path: str) -> ShResult:
        """shellcheck when installed; clean skip otherwise."""
        if not self.has_bash:
            return self._no_toolchain()
        shellcheck = shutil.which("shellcheck")
        if not shellcheck:
            return ShResult(
                success=False,
                stderr="shellcheck not found; skipping lint.",
                errors=[{"type": "toolchain", "message": "shellcheck missing (optional)"}],
            )
        return self._run([shellcheck, "-f", "gcc", _to_bash_path(str(file_path))],
                         cwd=str(Path(file_path).parent), timeout=60)


# --- CLI ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Azazael Shell Runner")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("run", help="Run .sh file")
    p.add_argument("file", help=".sh file to run")
    p.add_argument("args", nargs="*", help="Arguments to pass")

    p = sub.add_parser("code", help="Run inline shell script")
    p.add_argument("code", help="Shell source as string")
    p.add_argument("args", nargs="*", help="Arguments to pass")

    p = sub.add_parser("check", help="Syntax check only (bash -n)")
    p.add_argument("file", help=".sh file to check")

    p = sub.add_parser("lint", help="shellcheck lint")
    p.add_argument("file", help=".sh file to lint")

    args = parser.parse_args()
    runner = ShRunner()

    if args.command == "run":
        result = runner.run_file(args.file, args.args)
    elif args.command == "code":
        result = runner.run_code(args.code, args.args)
    elif args.command == "check":
        result = runner.check_syntax(args.file)
    elif args.command == "lint":
        result = runner.lint(args.file)
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
