#!/usr/bin/env python3
"""
Azazael Interpreters Package
=============================
Unified dispatcher for multi-language code execution.
Built to mirror the openmono-rebuild pattern: each language gets a dedicated
runner class with compile, run, test, and lint capabilities.

Supported: Python, Rust, Go, Bash, JavaScript (Node), PowerShell (pwsh).

Usage:
    from INTERPRETERS import run_code
    result = run_code("rust", 'fn main() { println!("Hello"); }')

OpenMono Integration:
    The InterpreterRouter mirrors the openmono OpenMonoDispatcher pattern.
    Each language runner (RustRunner, GoRunner) follows the same lifecycle:
        parse -> compile -> run -> test -> lint -> report.
        See: openmono_1click_commands.html on Desktop for CLI reference.
"""

import argparse
import os
import sys
# from typing import List  # removed — all names have native equivalents
from dataclasses import dataclass
from typing import Callable, List
import io
import subprocess

from INTERPRETERS.node_runner import NodeRunner
from INTERPRETERS.pwsh_runner import PwshRunner
from INTERPRETERS.sh_runner import ShRunner

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class RunResult:
    """Unified result from any language runner."""
    language: str
    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    duration_ms: float = 0.0
    binary_path: str = ""
    stderr_bytes: str = ""  # D7: raw stderr (latin-1) for forensics


def run_python(code: str, args: list[str] | None = None) -> RunResult:
    """Run Python code via exec.

    D6 contract: args are literal strings exposed as sys.argv[1:] with NO
    shell evaluation; args containing spaces/quotes arrive intact."""
    import time
    from contextlib import redirect_stdout, redirect_stderr

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    start = time.time()

    try:
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            _old_argv = sys.argv
            sys.argv = ["<inline>"] + list(args or [])
            try:
                exec(code, {"__name__": "__main__"})
            finally:
                sys.argv = _old_argv
        success = True
        returncode = 0
    except Exception as e:
        note_failure("__init__:L55", e)
        stderr_capture.write(f"{type(e).__name__}: {e}\n")
        success = False
        returncode = 1

    return RunResult(
        language="python",
        success=success,
        stdout=stdout_capture.getvalue(),
        stderr=stderr_capture.getvalue(),
        returncode=returncode,
        duration_ms=(time.time() - start) * 1000,
    )


def run_rust(code: str, args: list[str] | None = None) -> RunResult:
    """Run Rust code via rust_runner.

    D6 contract: args are literal strings forwarded as process argv with
    NO shell evaluation; args containing spaces/quotes arrive intact."""
    from INTERPRETERS.rust_runner import RustRunner

    runner = RustRunner()
    result = runner.run_code(code, args or [])
    return RunResult(
        language="rust",
        success=result.success,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        duration_ms=result.duration_ms,
        binary_path=result.binary_path or "",
        stderr_bytes=getattr(result, "stderr_bytes", ""),
    )


def run_go(code: str, args: list[str] | None = None) -> RunResult:
    """Run Go code via go_runner.

    D6 contract: args are literal strings forwarded as process argv with
    NO shell evaluation; args containing spaces/quotes arrive intact."""
    from INTERPRETERS.go_runner import GoRunner

    runner = GoRunner()
    result = runner.run_code(code, args or [])
    return RunResult(
        language="go",
        success=result.success,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        duration_ms=result.duration_ms,
        binary_path=result.binary_path or "",
        stderr_bytes=getattr(result, "stderr_bytes", ""),
    )


def run_shell_untrusted(command: str) -> RunResult:
    """Run a bash command string (P2: explicit untrusted-shell name).

    NOTE: shell=True is INTENTIONAL here — this is a bash command runner.
    The `command` parameter is user-supplied and is meant to be arbitrary shell.
    This function is the designated shell execution path for the interpreter.
    Do NOT pass untrusted input through this function without sanitization.
    D6 contract: argv-based runners (ShRunner/run_code) take literal string
    args with NO shell evaluation; this function is the ONLY shell=True path.
    """
    import time

    start = time.time()
    try:
        # D7: bytes capture; display via replace, evidence via stderr_bytes.
        result = subprocess.run(
            command, shell=True,  # scanner:ignore -- run_shell_untrusted IS the designated shell path; input is user shell by contract
            capture_output=True, timeout=30
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        return RunResult(
            language="bash",
            success=result.returncode == 0,
            stdout=stdout,
            stderr=stderr,
            returncode=result.returncode,
            duration_ms=(time.time() - start) * 1000,
            stderr_bytes=result.stderr[:4000].decode("latin-1"),
        )
    except Exception as e:
        note_failure("__init__:L128", e)
        return RunResult(
            language="bash",
            success=False,
            stderr=str(e),
            returncode=1,
        )


def run_bash(code: str, args: list[str] | None = None) -> RunResult:
    """Run bash via ShRunner (E3: the real bash binary, argv lists).

    Previously this name was a shell=True alias (which on Windows is
    cmd.exe, not bash) and the dispatcher silently dropped args. Now
    bash means bash: ShRunner finds the bash binary, runs the script
    file, and forwards args as literal argv. For an explicit host-shell
    string (pipes, redirection, cmd.exe-isms), call
    run_shell_untrusted instead.

    D6 contract: args are literal strings, NO shell evaluation."""
    runner = ShRunner()
    result = runner.run_code(code, args or [])
    return RunResult(
        language="bash",
        success=result.success,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        duration_ms=result.duration_ms,
        stderr_bytes=getattr(result, "stderr_bytes", ""),
    )


def run_javascript(code: str, args: list[str] | None = None) -> RunResult:
    """Run JavaScript code via NodeRunner (toolchain-checked)."""
    runner = NodeRunner()
    result = runner.run_code(code, args or [])
    return RunResult(
        language="javascript",
        success=result.success,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        duration_ms=result.duration_ms,
        stderr_bytes=getattr(result, "stderr_bytes", ""),
    )


def run_powershell(code: str, args: list[str] | None = None) -> RunResult:
    """Run PowerShell code via PwshRunner."""
    runner = PwshRunner()
    result = runner.run_code(code, args or [])
    return RunResult(
        language="powershell",
        success=result.success,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        duration_ms=result.duration_ms,
        stderr_bytes=getattr(result, "stderr_bytes", ""),
    )


# --- Unified Dispatcher -----------------------------------------------------

LANGUAGE_RUNNERS: dict[str, Callable[..., RunResult]] = {
    "python": run_python,
    "py": run_python,
    "rust": run_rust,
    "rs": run_rust,
    "go": run_go,
    "golang": run_go,
    "bash": run_bash,
    "sh": run_bash,
    "shell": run_bash,
    "javascript": run_javascript,
    "js": run_javascript,
    "node": run_javascript,
    "powershell": run_powershell,
    "ps1": run_powershell,
    "pwsh": run_powershell,
}


def run_code(language: str, code: str, args: list[str] | None = None) -> RunResult:
    """Dispatch code execution to the appropriate language runner.

    Every runner takes (code, args): args are literal argv strings with
    NO shell evaluation (D6). The only shell=True path is an explicit
    run_shell_untrusted call — never the dispatcher."""
    runner = LANGUAGE_RUNNERS.get(language.lower())
    if not runner:
        return RunResult(
            language=language,
            success=False,
            stderr=f"Unsupported language: {language}. "
                    f"Supported: {sorted(set(LANGUAGE_RUNNERS.keys()))}",
            returncode=1,
        )
    return runner(code, args)


def supported_languages() -> list[str]:
    """Return list of supported languages."""
    return sorted(set(
        k for k in LANGUAGE_RUNNERS
        if len(k) > 2  # Skip short aliases
    ))


# --- CLI ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Azazael Multi-Language Interpreter")
    parser.add_argument("language", help="Language: python, rust, go, bash, javascript")
    parser.add_argument("code", help="Code to execute (or '-' for stdin)")
    parser.add_argument("args", nargs="*", help="Arguments to pass to the program")
    args = parser.parse_args()

    code = args.code
    if code == "-":
        code = sys.stdin.read()

    result = run_code(args.language, code, args.args)

    if result.success:
        print(result.stdout, end="")
    else:
        print(f"[{result.language}] Error:", file=sys.stderr)
        print(result.stderr, end="", file=sys.stderr)
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
