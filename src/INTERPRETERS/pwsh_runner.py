#!/usr/bin/env python3
"""Azazael PowerShell Runner (pwsh 7+, Windows PowerShell fallback).

Subprocess-based runner mirroring the GoRunner/RustRunner shape:
    - Run .ps1 files via `pwsh -NoProfile -NonInteractive -File`
    - Run inline code (hashed temp file under build/pwsh)
    - Syntax check via the PowerShell parser (no execution)
    - Lint via PSScriptAnalyzer when the module is installed (clean skip)
    - Test via Pester when the module is installed (clean skip)

argv lists throughout -- never shell=True. Toolchain/module absence is a
structured error, never a crash.
"""

import hashlib
import json
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
    note_failure("pwsh_runner:core-paths-import", exc)
    _get_project_root_safe = None

_DEFAULT_ROOT = (str(_get_project_root_safe()) if _get_project_root_safe is not None
                 else str(Path(__file__).resolve().parents[2]))

AZAZAEL_ROOT = os.environ.get(
    "AZAZAEL_ROOT", os.environ.get("Azazael_ROOT", _DEFAULT_ROOT)
)

PWSH_BUILD_DIR = Path(AZAZAEL_ROOT) / "build" / "pwsh"
PWSH_BUILD_DIR.mkdir(parents=True, exist_ok=True)

# Parser-only syntax check: returns "OK" or throws; exit code carries it.
_PARSER_CHECK = (
    "$__f=$args[0];"
    "$__e=$null;$__t=$null;"
    "[void][System.Management.Automation.Language.Parser]::ParseFile("
    "$__f,[ref]$__t,[ref]$__e);"
    "if($__e.Count -gt 0){$__e|ForEach-Object{$_.Message};exit 1}"
    "else{'OK'}"
)


@dataclass
class PwshResult:
    """Result of a PowerShell execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    duration_ms: float = 0.0
    errors: List[dict] = field(default_factory=list)
    stderr_bytes: str = ""  # D7: raw stderr (latin-1) for forensics


class PwshRunner:
    """Run PowerShell via pwsh (argv lists, no shell)."""

    def __init__(self) -> None:
        self._verify_toolchain()

    def _verify_toolchain(self) -> bool:
        """Prefer pwsh 7; fall back to Windows PowerShell when present."""
        self.pwsh_bin: str = shutil.which("pwsh") or shutil.which("powershell") or ""
        self.has_pwsh = bool(self.pwsh_bin)
        self.version = ""
        if not self.has_pwsh:
            print("  [WARN] no PowerShell found. Install: https://aka.ms/powershell")
            return False
        try:
            out = subprocess.run(
                [self.pwsh_bin, "-NoProfile", "-NonInteractive", "-Command",
                 "$PSVersionTable.PSVersion.ToString()"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20,
            )
            self.version = out.stdout.strip()
        except Exception as e:
            note_failure("pwsh_runner:version", e)
        return True

    def _no_toolchain(self) -> PwshResult:
        # Lowercase "not found" phrasing matches the node/go/bash/rust
        # family: run_shard's skip contract keys on that substring.
        return PwshResult(
            success=False,
            stderr="pwsh not found. Install PowerShell 7.",
            errors=[{"type": "toolchain", "message": "pwsh missing"}],
        )

    def _parse_errors(self, stderr: str) -> List[dict]:
        errors: List[dict] = []
        for line in stderr.split("\n"):
            s = line.strip()
            if not s:
                continue
            # Parser/runtime errors carry file:line info; keep the raw text.
            errors.append({"level": "error", "message": s})
        return errors

    def _run(self, cmd: List[str], cwd: str, timeout: int) -> PwshResult:
        # D7: capture bytes, decode manually — display via replace,
        # evidence via stderr_bytes (latin-1 round-trips every byte).
        try:
            start = time.time()
            result = subprocess.run(
                cmd, cwd=cwd, capture_output=True, timeout=timeout,
            )
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")
            return PwshResult(
                success=result.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                returncode=result.returncode,
                duration_ms=(time.time() - start) * 1000,
                errors=self._parse_errors(stderr) if result.returncode != 0 else [],
                stderr_bytes=result.stderr[:4000].decode("latin-1"),
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("pwsh_runner:timeout", exc)
            return PwshResult(
                success=False,
                stderr=f"Execution timed out (>{timeout}s)",
                errors=[{"type": "timeout", "message": f"Runtime exceeded {timeout} seconds"}],
            )
        except Exception as e:
            note_failure("pwsh_runner:exec", e)
            return PwshResult(
                success=False, stderr=str(e),
                errors=[{"type": "exception", "message": str(e)}],
            )

    def _base(self, extra: List[str]) -> List[str]:
        return [self.pwsh_bin, "-NoProfile", "-NonInteractive"] + extra

    def run_file(self, file_path: str, args: Optional[List[str]] = None) -> PwshResult:
        """Run a .ps1 file.

        D6 contract: args are literal strings passed to `pwsh -File` with NO
        shell evaluation. PowerShell `-File` arg passing has legacy quoting
        quirks (quotes stripped, `$` untouched, unlike `-Command`); callers
        must not pre-quote — pass the raw string and let argv carry it."""
        if not self.has_pwsh:
            return self._no_toolchain()
        source = Path(file_path)
        if not source.exists():
            return PwshResult(
                success=False,
                stderr=f"File not found: {file_path}",
                errors=[{"type": "file", "message": f"Missing: {file_path}"}],
            )
        return self._run(self._base(["-File", str(source)] + (args or [])),
                         cwd=str(source.parent), timeout=60)

    def run_code(self, code: str, args: Optional[List[str]] = None) -> PwshResult:
        """Run inline PowerShell (written to a hashed temp file)."""
        if not self.has_pwsh:
            return self._no_toolchain()
        code_hash = hashlib.md5(code.encode()).hexdigest()[:12]
        source_path = PWSH_BUILD_DIR / f"azazael_{code_hash}.ps1"
        source_path.write_text(code, encoding="utf-8")
        return self._run(self._base(["-File", str(source_path)] + (args or [])),
                         cwd=str(PWSH_BUILD_DIR), timeout=60)

    def check_syntax(self, file_path: str) -> PwshResult:
        """Parser-only check (never executes the script)."""
        if not self.has_pwsh:
            return self._no_toolchain()
        source = Path(file_path)
        if not source.exists():
            return PwshResult(success=False, stderr=f"File not found: {file_path}")
        return self._run(self._base(["-Command", _PARSER_CHECK, str(source)]),
                         cwd=str(source.parent), timeout=30)

    _module_cache: dict = {}  # P2: process-level probe cache (pwsh spawn ~200ms)

    def _module_present(self, name: str) -> bool:
        """True when a PowerShell module is importable (fast probe).

        P2: result cached per process — lint+test previously shelled out
        twice (probe + run); repeated calls now pay one spawn per module."""
        if not self.has_pwsh:
            return False
        if name in PwshRunner._module_cache:
            return PwshRunner._module_cache[name]
        try:
            probe = subprocess.run(
                self._base(["-Command", f"if(Get-Module -ListAvailable -Name {name}){{'YES'}}"]),
                capture_output=True, timeout=30,
            )
            present = b"YES" in (probe.stdout or b"")
            PwshRunner._module_cache[name] = present
            return present
        except Exception as e:
            note_failure("pwsh_runner:module-probe", e)
            return False

    def lint(self, file_path: str) -> PwshResult:
        """PSScriptAnalyzer when installed; clean skip otherwise."""
        if not self.has_pwsh:
            return self._no_toolchain()
        if not self._module_present("PSScriptAnalyzer"):
            return PwshResult(
                success=False,
                stderr="PSScriptAnalyzer not installed; skipping lint.",
                errors=[{"type": "toolchain", "message": "PSScriptAnalyzer missing (optional)"}],
            )
        cmd = (
            "$__r=Invoke-ScriptAnalyzer -Path $args[0];"
            "$__r|ConvertTo-Json -Depth 3 -Compress;"
            "if($null -eq $__r){'[]'}"
        )
        res = self._run(self._base(["-Command", cmd, str(file_path)]),
                        cwd=str(Path(file_path).parent), timeout=60)
        if not res.success:
            return res
        try:
            findings = json.loads(res.stdout.strip() or "[]")
            if isinstance(findings, dict):
                findings = [findings]
            errors = [{"level": str(f.get("Severity", "error")).lower(),
                       "message": f"{f.get('ScriptName', '')}:{f.get('Line', '')} "
                                  f"{f.get('RuleName', '')} {f.get('Message', '')}".strip()}
                      for f in findings]
            bad = [e for e in errors if e["level"] == "error"]
            return PwshResult(success=not bad, stdout=res.stdout,
                              stderr="" if not bad else f"{len(bad)} error(s)",
                              returncode=0 if not bad else 1,
                              duration_ms=res.duration_ms, errors=errors)
        except ValueError as e:
            note_failure("pwsh_runner:lint-parse", e)
            return PwshResult(success=False, stderr=f"lint output unparseable: {e}")

    def test(self, target: str = ".") -> PwshResult:
        """Pester when installed; clean skip otherwise."""
        if not self.has_pwsh:
            return self._no_toolchain()
        if not self._module_present("Pester"):
            return PwshResult(
                success=False,
                stderr="Pester not installed; skipping tests.",
                errors=[{"type": "toolchain", "message": "Pester missing (optional)"}],
            )
        cmd = ("$__c=Invoke-Pester -Path $args[0] -PassThru;"
               "'FAILED='+$__c.FailedCount+',PASSED='+$__c.PassedCount")
        base = target if Path(target).is_dir() else str(Path(target).parent)
        return self._run(self._base(["-Command", cmd, target]),
                         cwd=base, timeout=180)


# --- CLI ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Azazael PowerShell Runner")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("run", help="Run .ps1 file")
    p.add_argument("file", help=".ps1 file to run")
    p.add_argument("args", nargs="*", help="Arguments to pass")

    p = sub.add_parser("code", help="Run inline PowerShell")
    p.add_argument("code", help="PowerShell source as string")
    p.add_argument("args", nargs="*", help="Arguments to pass")

    p = sub.add_parser("check", help="Parser-only syntax check")
    p.add_argument("file", help=".ps1 file to check")

    p = sub.add_parser("lint", help="PSScriptAnalyzer lint")
    p.add_argument("file", help=".ps1 file to lint")

    p = sub.add_parser("test", help="Pester tests")
    p.add_argument("target", nargs="?", default=".", help="Test path")

    args = parser.parse_args()
    runner = PwshRunner()

    if args.command == "run":
        result = runner.run_file(args.file, args.args)
    elif args.command == "code":
        result = runner.run_code(args.code, args.args)
    elif args.command == "check":
        result = runner.check_syntax(args.file)
    elif args.command == "lint":
        result = runner.lint(args.file)
    elif args.command == "test":
        result = runner.test(args.target)
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
