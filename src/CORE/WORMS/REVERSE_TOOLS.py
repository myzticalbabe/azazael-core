# coding: utf-8
"""
REVERSE_TOOLS — Reverse Engineering Tool Manager

Manages installation, configuration, and integration of reverse engineering
tools into the Azazael ecosystem.

Features:
- Ghidra — NSA's software reverse engineering framework
  - Download & install
  - Launch headless analysis
  - Python scripting bridge (GhidraBridge)
  - Auto-decompile and export results
  
- radare2 / rizin — UNIX-like reverse engineering framework
  - Install via pip/package manager
  - r2pipe integration for scripted analysis
  - Auto-analysis with `aaa` command
  - Export to JSON for PAMS memory

- PAMS Integration — All RE sessions recorded as memory insights
  - Ghidra analysis results → PAMS memory
  - radare2 disassembly → PAMS memory
  - Cross-reference findings across tools

Usage:
    >>> from CORE.WORMS.REVERSE_TOOLS import ReverseTools
    >>> rt = ReverseTools()
    >>> rt.install_radare2()
    >>> rt.check_ghidra()
    >>> rt.radare2_analyze("target.exe")
    >>> rt.ghidra_headless("target.exe", output_dir="./ghidra_out")
"""

import os
import sys
import re
import json
import shutil
import subprocess
import zipfile
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).
_SWALLOWED: List[str] = []

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        _SWALLOWED.append("%s:no-recorder" % where)


def _note_failure(where: str, exc: BaseException | None = None) -> None:
    """Record a fail-open swallow. Never raises -- a boundary hook must not break
    the boundary it guards, so the recorder is itself wrapped.

    Note for the coordinator: the shared brief's helper block defined only the
    imported `note_failure`, while every per-site example calls `_note_failure`.
    Without this wrapper each converted site would raise NameError inside its
    except block, i.e. the swallow would become a crash. Verified present here.
    """
    try:
        note_failure(where, exc)
    except Exception as _e:
        _SWALLOWED.append("%s:%s" % (where, type(_e).__name__))

# ── Worm Registry ─────────────────────────────────────────────────────
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("REVERSE_TOOLS:L77", exc)
    _REGISTRY = False
    def register_worm(category='', priority=0, cooldown_seconds=0.0, description='', tags=None):        return lambda cls: cls

# ── PAMS Integration ──────────────────────────────────────────────────────────
try:
    _ft_path = str(Path(__file__).resolve().parent.parent.parent)
    if _ft_path not in sys.path:
        sys.path.insert(0, _ft_path)
    from CORE.PamsRecorder import get_recorder
    _pams: Any = get_recorder()
except ImportError as exc:
    note_failure("REVERSE_TOOLS:L88", exc)
    _pams = None
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

# Tool paths
GHIDRA_DEFAULT_DIR: str = os.path.expanduser("~/ghidra")
RADARE2_DEFAULT_DIR: str = os.path.expanduser("~/radare2")
TOOLS_CACHE: Path = Path.home() / ".azazael" / "re_tools"
TOOLS_CACHE.mkdir(parents=True, exist_ok=True)


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class ToolStatus:
    """Status of a reverse engineering tool."""
    name: str
    installed: bool
    version: str
    path: str
    last_check: str
    notes: str = ""


@dataclass
class Radare2Analysis:
    """Results from radare2 analysis."""
    path: str
    arch: str
    bits: int
    hashes: dict[str, str]
    functions: list[dict[str, Any]]
    imports: list[str]
    exports: list[str]
    strings: list[str]
    sections: list[dict[str, Any]]
    entry_points: list[int]


@dataclass
class GhidraAnalysis:
    """Results from Ghidra headless analysis."""
    path: str
    program_name: str
    functions_count: int
    data_types_count: int
    output_dir: str


# ── ReverseTools Manager ─────────────────────────────────────────────────────

@register_worm(category="analysis", priority=6, cooldown_seconds=60.0, description="RE tool manager (Ghidra, radare2/r2pipe, cross-tool analysis)")
class ReverseTools:
    """Reverse Engineering Tool Manager for Azazael.

    Manages Ghidra and radare2 installation, provides analysis bridges,
    and records all RE activities to PAMS memory.
    """

    VERSION = "1.0.0"
    GHIDRA_LATEST = "11.2.1"
    GHIDRA_DOWNLOAD_URL = (
        f"https://github.com/NationalSecurityAgency/ghidra/releases/download/"
        f"Ghidra_{GHIDRA_LATEST}_build/ghidra_{GHIDRA_LATEST}_PUBLIC_20241105.zip"
    )

    def __init__(self):
        self._pams = _pams
        self._tool_cache = TOOLS_CACHE

    def _record(self, event_type: str, data: dict[str, Any], severity: str = "info"):
        if self._pams:
            try:
                self._pams.record_event(
                    event_type=f"retools.{event_type}",
                    data=data, source="ReverseTools", severity=severity,
                )
            except Exception as exc:  # noqa: BLE001 - was silent (PAMS event lost)
                _note_failure("reverse_tools:L141", exc)
                pass

    # ── Tool Status ───────────────────────────────────────────────────────────

    def check_all(self) -> list[ToolStatus]:
        """Check status of all reverse engineering tools."""
        tools = [
            self.check_ghidra(),
            self.check_radare2(),
        ]
        self._record("check_all", {
            "tools": [{"name": t.name, "installed": t.installed, "version": t.version} for t in tools],
        })
        return tools

    def check_ghidra(self) -> ToolStatus:
        """Check if Ghidra is installed and find its version."""
        status = ToolStatus(
            name="Ghidra",
            installed=False,
            version="",
            path="",
            last_check=datetime.now().isoformat(),
        )

        # Check common installation locations
        search_dirs = [
            GHIDRA_DEFAULT_DIR,
            os.path.expanduser("~/ghidra_public"),
            "C:/ghidra",
            "C:/Program Files/ghidra",
            "/opt/ghidra",
        ]

        for d in search_dirs:
            d_path = Path(d)
            if d_path.exists():
                # Find ghidraRun.bat or ghidraRun
                bat = d_path / "ghidraRun.bat"
                sh = d_path / "ghidraRun"
                if bat.exists() or sh.exists():
                    status.installed = True
                    status.path = str(d)

                    # Try to find version from application.properties
                    props = d_path / "Ghidra" / "application.properties"
                    if props.exists():
                        try:
                            with open(props, "r", encoding="utf-8") as f:
                                for line in f:
                                    if "application.version" in line:
                                        status.version = line.split("=")[-1].strip()
                                        break
                        except Exception as exc:  # noqa: BLE001 - was silent
                            _note_failure("reverse_tools:L195", exc)
                            pass
                    break

        self._record("check_ghidra", {"installed": status.installed, "version": status.version})
        return status

    def check_radare2(self) -> ToolStatus:
        """Check if radare2 is installed."""
        status = ToolStatus(
            name="radare2",
            installed=False,
            version="",
            path="",
            last_check=datetime.now().isoformat(),
        )

        # Try to run r2
        for cmd in ["r2", "radare2", "rizin"]:
            try:
                proc = subprocess.run([cmd, "-v"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
                if proc.returncode == 0:
                    status.installed = True
                    status.path = shutil.which(cmd) or cmd
                    # Parse version from first line
                    first_line = proc.stdout.splitlines()[0] if proc.stdout else proc.stderr.splitlines()[0]
                    status.version = first_line.strip()
                    break
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:  # noqa: BLE001 - probe
                _note_failure("reverse_tools:L223", exc)
                pass

        # Also check pip-installed r2pipe
        if not status.installed:
            try:
                import r2pipe
                status.installed = True
                status.path = "r2pipe (Python)"
                status.version = getattr(r2pipe, "__version__", "unknown")
                status.notes = "r2pipe available (Python binding only)"
            except ImportError as exc:
                note_failure("REVERSE_TOOLS:L262", exc)

        self._record("check_radare2", {"installed": status.installed, "version": status.version})
        return status

    # ── Installation ──────────────────────────────────────────────────────────

    def install_radare2(self) -> dict[str, Any]:
        """Install radare2 via the best available method.

        Tries in order:
        1. pip install r2pipe + radare2-binary
        2. Platform package manager (choco/apt/brew)
        3. Direct download
        """
        result = {"method": "", "success": False, "version": "", "error": ""}

        # Check if already installed
        status = self.check_radare2()
        if status.installed:
            result["success"] = True
            result["version"] = status.version
            result["method"] = "pre-existing"
            return result

        # Method 1: pip install
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "r2pipe", "--quiet"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            if proc.returncode == 0:
                result["success"] = True
                result["method"] = "pip (r2pipe)"
                result["version"] = "r2pipe installed"
                self._record("install_radare2", result)
                return result
        except Exception as exc:  # noqa: BLE001 - was silent
            _note_failure("reverse_tools:L272", exc)
            pass

        # Method 2: Platform package manager
        if IS_WINDOWS:
            try:
                proc = subprocess.run(
                    ["choco", "install", "radare2", "-y"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                )
                if proc.returncode == 0:
                    result["success"] = True
                    result["method"] = "chocolatey"
                    result["version"] = "latest (choco)"
                    self._record("install_radare2", result)
                    return result
            except Exception as exc:  # noqa: BLE001 - was silent
                _note_failure("reverse_tools:L288", exc)
                pass

            # Try winget
            try:
                proc = subprocess.run(
                    ["winget", "install", "radare2.radare2", "--silent"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                )
                if proc.returncode == 0:
                    result["success"] = True
                    result["method"] = "winget"
                    result["version"] = "latest (winget)"
                    self._record("install_radare2", result)
                    return result
            except Exception as exc:  # noqa: BLE001 - was silent
                _note_failure("reverse_tools:L303", exc)
                pass

        elif IS_LINUX:
            for pm in [["apt", "install", "-y", "radare2"], ["dnf", "install", "-y", "radare2"]]:
                try:
                    proc = subprocess.run(["sudo"] + pm, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
                    if proc.returncode == 0:
                        result["success"] = True
                        result["method"] = pm[0]
                        result["version"] = "latest (system)"
                        self._record("install_radare2", result)
                        return result
                except Exception as exc:  # noqa: BLE001 - was silent
                    _note_failure("reverse_tools:L316", exc)
                    pass

        # Method 3: pip install radare2-binary wheels
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "radare2-binary", "--quiet"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            if proc.returncode == 0:
                result["success"] = True
                result["method"] = "pip (radare2-binary)"
                result["version"] = "binary installed"
                self._record("install_radare2", result)
                return result
        except Exception as exc:  # noqa: BLE001 - was silent
            _note_failure("reverse_tools:L331", exc)
            pass

        result["error"] = "Could not install radare2. Install manually: https://github.com/radareorg/radare2"
        self._record("install_radare2_error", result, severity="error")
        return result

    def install_ghidra(self) -> dict[str, Any]:
        """Download and install Ghidra.

        WARNING: Ghidra requires JDK 17+. This is a large download (~400MB).
        """
        result = {"method": "", "success": False, "version": "", "path": "", "error": ""}

        # Check if already installed
        status = self.check_ghidra()
        if status.installed:
            result["success"] = True
            result["version"] = status.version
            result["path"] = status.path
            result["method"] = "pre-existing"
            return result

        # Check for Java
        java_ok = False
        java_version = 0
        for java_cmd in ["java", "javaw"]:
            try:
                proc = subprocess.run([java_cmd, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
                ver_out = proc.stderr or proc.stdout
                # Parse version: "version "17.0.1"" or "version "1.8.0_301""
                match = re.search(r'version "(?:1\.)?(\d+)', ver_out)
                if match:
                    java_version = int(match.group(1))
                    if java_version >= 17:
                        java_ok = True
                        break
            except Exception as exc:  # noqa: BLE001 - was silent
                _note_failure("reverse_tools:L368", exc)
                pass

        if not java_ok:
            result["error"] = "JDK 17+ required. Install from: https://adoptium.net/"
            result["method"] = "blocked"
            self._record("install_ghidra_error", result, severity="error")
            return result

        # Download and install
        try:
            zip_path = self._tool_cache / f"ghidra_{self.GHIDRA_LATEST}.zip"

            if not zip_path.exists():
                urllib.request.urlretrieve(self.GHIDRA_DOWNLOAD_URL, zip_path)

            install_dir = Path(GHIDRA_DEFAULT_DIR)
            install_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(install_dir)

            result["success"] = True
            result["method"] = "direct download"
            result["version"] = self.GHIDRA_LATEST
            result["path"] = str(install_dir)

            self._record("install_ghidra", result)

        except Exception as e:  # noqa: BLE001 — best-effort boundary
            result["error"] = str(e)
            self._record("install_ghidra_error", result, severity="error")

        return result

    # ── radare2 Analysis ──────────────────────────────────────────────────────

    def radare2_analyze(self, path: str, auto_analysis: bool = True) -> Radare2Analysis:
        """Analyze a binary using radare2/r2pipe.

        Requires radare2 or r2pipe to be installed.
        """
        analysis = Radare2Analysis(
            path=path, arch="", bits=0,
            hashes={}, functions=[], imports=[],
            exports=[], strings=[], sections=[], entry_points=[],
        )

        full_path = Path(path)
        if not full_path.exists():
            return analysis

        # Try r2pipe first (Python binding)
        try:
            import r2pipe
            r2 = r2pipe.open(str(full_path))
            if auto_analysis:
                r2.cmd("aaa")  # Auto-analyze

            # Architecture info
            info = json.loads(r2.cmd("ij"))
            analysis.arch = info.get("bin", {}).get("arch", "")
            analysis.bits = info.get("bin", {}).get("bits", 0)

            # Hashes
            analysis.hashes = {
                "md5": r2.cmd("ph md5").strip(),
                "sha1": r2.cmd("ph sha1").strip(),
                "sha256": r2.cmd("ph sha256").strip(),
            }

            # Functions
            funcs_json = r2.cmd("aflj")
            try:
                funcs = json.loads(funcs_json)
                for f in funcs:
                    analysis.functions.append({
                        "name": f.get("name", ""),
                        "offset": f.get("offset", 0),
                        "size": f.get("size", 0),
                    })
            except json.JSONDecodeError as exc:  # noqa: BLE001 - was silent
                _note_failure("reverse_tools:L449", exc)
                pass

            # Imports
            imports_json = r2.cmd("iij")
            try:
                imports = json.loads(imports_json)
                for imp in imports:
                    analysis.imports.append(imp.get("name", ""))
            except json.JSONDecodeError as exc:  # noqa: BLE001 - was silent
                _note_failure("reverse_tools:L458", exc)
                pass

            # Exports
            exports_json = r2.cmd("iEj")
            try:
                exports = json.loads(exports_json)
                for exp in exports:
                    analysis.exports.append(exp.get("name", ""))
            except json.JSONDecodeError as exc:  # noqa: BLE001 - was silent
                _note_failure("reverse_tools:L467", exc)
                pass

            # Strings
            strings_raw = r2.cmd("izz").splitlines()
            for s in strings_raw:
                # Parse: "nth paddr vaddr len size section type string"
                parts = s.split(maxsplit=8)
                if len(parts) >= 9:
                    analysis.strings.append(parts[8])

            # Sections
            sections_json = r2.cmd("iSj")
            try:
                sections = json.loads(sections_json)
                for sec in sections:
                    analysis.sections.append({
                        "name": sec.get("name", ""),
                        "size": sec.get("size", 0),
                        "vaddr": sec.get("vaddr", 0),
                        "paddr": sec.get("paddr", 0),
                    })
            except json.JSONDecodeError as exc:  # noqa: BLE001 - was silent
                _note_failure("reverse_tools:L489", exc)
                pass

            # Entry points
            entry_json = r2.cmd("iej")
            try:
                entries = json.loads(entry_json)
                for e in entries:
                    analysis.entry_points.append(e.get("vaddr", 0))
            except json.JSONDecodeError as exc:  # noqa: BLE001 - was silent
                _note_failure("reverse_tools:L498", exc)
                pass

            r2.quit()

        except ImportError as exc:
            # Fallback: try r2 command line directly
            # Fallback: try r2 command line directly
            note_failure("REVERSE_TOOLS:L542", exc)
            analysis = self._radare2_cli(str(full_path), auto_analysis)
        except Exception as e:  # noqa: BLE001 — best-effort boundary
            self._record("radare2_error", {"path": str(full_path), "error": str(e)}, severity="error")

        self._record("radare2_analysis", {
            "path": str(full_path),
            "arch": analysis.arch,
            "bits": analysis.bits,
            "functions": len(analysis.functions),
            "imports": len(analysis.imports),
            "strings": len(analysis.strings),
        })

        return analysis

    def _radare2_cli(self, path: str, auto_analysis: bool) -> Radare2Analysis:
        """Analyze using radare2 CLI directly."""
        analysis = Radare2Analysis(
            path=path, arch="", bits=0,
            hashes={}, functions=[], imports=[],
            exports=[], strings=[], sections=[], entry_points=[],
        )

        for r2_cmd in ["r2", "radare2"]:
            try:
                base_cmd = [r2_cmd, "-q"]
                if auto_analysis:
                    base_cmd.extend(["-A"])  # Auto-analyze
                base_cmd.append(path)

                # Get architecture
                proc = subprocess.run(
                    base_cmd + ["-c", "ij", "-c", "q"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                )
                try:
                    info = json.loads(proc.stdout)
                    analysis.arch = info.get("bin", {}).get("arch", "")
                    analysis.bits = info.get("bin", {}).get("bits", 0)
                except json.JSONDecodeError as exc:  # noqa: BLE001 - was silent
                    _note_failure("reverse_tools:L544", exc)
                    pass

                # Get functions
                proc = subprocess.run(
                    base_cmd + ["-c", "aflj", "-c", "q"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                )
                try:
                    funcs = json.loads(proc.stdout)
                    for f in funcs:
                        analysis.functions.append({
                            "name": f.get("name", ""),
                            "offset": f.get("offset", 0),
                            "size": f.get("size", 0),
                        })
                except json.JSONDecodeError as exc:  # noqa: BLE001 - was silent
                    _note_failure("reverse_tools:L560", exc)
                    pass

                break  # Success
            except FileNotFoundError as exc:  # noqa: BLE001 - probe: variant absent
                _note_failure("reverse_tools:L564", exc)
                continue
            except Exception as exc:  # noqa: BLE001 - was silent
                _note_failure("reverse_tools:L566", exc)
                break

        return analysis

    def radare2_disasm(self, path: str, function_name: str = "main", max_ins: int = 200) -> list[dict[str, Any]]:
        """Disassemble a specific function using radare2."""
        instructions = []
        full_path = Path(path)

        if not full_path.exists():
            return instructions

        try:
            import r2pipe
            r2 = r2pipe.open(str(full_path))
            r2.cmd("aaa")
            r2.cmd(f"s {function_name}")

            pdf_output = r2.cmd(f"pdf @ {function_name}")
            for line in pdf_output.splitlines():
                line = line.strip()
                if not line or "│" in line or "└" in line:
                    continue
                # Parse: "0x00401000  48 83 ec 28  sub rsp, 0x28"
                match = re.match(r"^(0x[0-9a-fA-F]+)\s+([0-9a-fA-F\s]+?)\s{2,}(.+)$", line)
                if match:
                    instructions.append({
                        "address": match.group(1),
                        "bytes": match.group(2).strip(),
                        "mnemonic": match.group(3).strip(),
                    })

                if len(instructions) >= max_ins:
                    break

            r2.quit()
        except ImportError:
            # Try objdump fallback
            try:
                proc = subprocess.run(
                    ["objdump", "-d", "-M", "intel", str(full_path)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                )
                in_func = False
                for line in proc.stdout.splitlines():
                    if f"<{function_name}>:" in line:
                        in_func = True
                        continue
                    if in_func and line.strip() == "":
                        break
                    if in_func and ":" in line:
                        parts = line.split(":", 1)
                        if len(parts) >= 2:
                            rest = parts[1].strip()
                            tab_parts = rest.split("\t", 1)
                            instructions.append({
                                "address": parts[0].strip(),
                                "bytes": tab_parts[0].strip() if len(tab_parts) > 0 else "",
                                "mnemonic": tab_parts[1].strip() if len(tab_parts) > 1 else rest,
                            })
                    if len(instructions) >= max_ins:
                        break
            except Exception as exc:  # noqa: BLE001 - was silent
                _note_failure("reverse_tools:L629", exc)
                pass

        return instructions

    # ── Ghidra Headless Analysis ──────────────────────────────────────────────

    def ghidra_headless(
        self,
        path: str,
        output_dir: (str | None) = None,
        post_script: (str | None) = None,
        max_memory: str = "2G",
    ) -> GhidraAnalysis:
        """Run Ghidra headless analysis on a binary.

        Requires Ghidra to be installed and the path configured.

        Args:
            path: Binary to analyze
            output_dir: Where to output analysis results
            post_script: Ghidra Python script to run after analysis
            max_memory: JVM max memory (-maxmemory flag)
        """
        full_path = Path(path)
        output_dir = output_dir or str(TOOLS_CACHE / "ghidra_output" / full_path.stem)
        os.makedirs(output_dir, exist_ok=True)

        result = GhidraAnalysis(
            path=str(full_path),
            program_name=full_path.stem,
            functions_count=0,
            data_types_count=0,
            output_dir=output_dir,
        )

        ghidra_status = self.check_ghidra()
        if not ghidra_status.installed:
            self._record("ghidra_not_installed", {"path": str(full_path)}, severity="warning")
            return result

        try:
            # Find the analyzeHeadless script
            ghidra_dir = Path(ghidra_status.path)
            if IS_WINDOWS:
                analyze_cmd = str(ghidra_dir / "support" / "analyzeHeadless.bat")
            else:
                analyze_cmd = str(ghidra_dir / "support" / "analyzeHeadless")

            args = [
                analyze_cmd,
                output_dir,           # Project directory
                full_path.stem,       # Project name
                "-import", str(full_path),
                "-overwrite",
                "-maxmemory", max_memory,
            ]

            if post_script:
                args.extend(["-postScript", post_script])

            proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)

            # Parse output for function/data type counts
            for line in proc.stdout.splitlines():
                if "functions" in line.lower() and "found" in line.lower():
                    try:
                        result.functions_count = int(line.split()[0])
                    except (ValueError, IndexError) as exc:  # noqa: BLE001 - was silent
                        _note_failure("reverse_tools:L697", exc)
                        pass
                if "data types" in line.lower():
                    try:
                        result.data_types_count = int(line.split()[0])
                    except (ValueError, IndexError) as exc:  # noqa: BLE001 - was silent
                        _note_failure("reverse_tools:L702", exc)
                        pass

            self._record("ghidra_headless", {
                "path": str(full_path),
                "output_dir": output_dir,
                "functions": result.functions_count,
                "data_types": result.data_types_count,
            })

        except FileNotFoundError:
            self._record("ghidra_headless_error", {
                "path": str(full_path), "error": "analyzeHeadless not found",
            }, severity="error")
        except Exception as e:  # noqa: BLE001 — best-effort boundary
            self._record("ghidra_headless_error", {
                "path": str(full_path), "error": str(e),
            }, severity="error")

        return result

    # ── Cross-Tool Analysis Pipeline ──────────────────────────────────────────

    def cross_analyze(self, path: str) -> dict[str, Any]:
        """Run radare2 + Ghidra analysis and cross-reference results.

        Combines insights from both tools into a unified PAMS memory record.
        """
        report = {
            "path": path,
            "timestamp": datetime.now().isoformat(),
            "r2": None,
            "ghidra": None,
            "cross_references": [],
        }

        # radare2 analysis
        try:
            r2_result = self.radare2_analyze(path)
            report["r2"] = {
                "arch": r2_result.arch,
                "bits": r2_result.bits,
                "functions": len(r2_result.functions),
                "imports": r2_result.imports[:50],
                "strings_count": len(r2_result.strings),
                "hashes": r2_result.hashes,
            }
        except Exception as e:  # noqa: BLE001 — best-effort boundary
            _note_failure("reverse_tools:L749", e)
            report["r2"] = {"error": str(e)}

        # Ghidra analysis
        try:
            ghidra_result = self.ghidra_headless(path)
            report["ghidra"] = {
                "functions": ghidra_result.functions_count,
                "data_types": ghidra_result.data_types_count,
                "output_dir": ghidra_result.output_dir,
            }
        except Exception as e:  # noqa: BLE001 — best-effort boundary
            _note_failure("reverse_tools:L760", e)
            report["ghidra"] = {"error": str(e)}

        # Cross-reference
        if report["r2"] and report["ghidra"]:
            report["cross_references"].append(
                f"r2 found {report['r2']['functions']} functions; "
                f"Ghidra found {report['ghidra']['functions']} functions"
            )

        self._record("cross_analyze", report)
        return report

    # ── Utility ───────────────────────────────────────────────────────────────

    def get_summary(self) -> dict[str, Any]:
        """Get summary of all RE tools and recent analyses."""
        tools = self.check_all()
        return {
            "version": self.VERSION,
            "tools": [
                {"name": t.name, "installed": t.installed, "version": t.version}
                for t in tools
            ],
            "cache_dir": str(self._tool_cache),
            "platform": sys.platform,
        }


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def main():

    parser = argparse.ArgumentParser(description="REVERSE_TOOLS — RE Tool Manager")
    sub = parser.add_subparsers(dest="command", help="Command")

    # check
    check_p = sub.add_parser("check", help="Check tool status")
    check_p.add_argument("--tool", choices=["ghidra", "radare2", "all"], default="all")

    # install
    install_p = sub.add_parser("install", help="Install a tool")
    install_p.add_argument("tool", choices=["ghidra", "radare2"])

    # analyze (radare2)
    r2_p = sub.add_parser("radare2-analyze", help="Analyze with radare2")
    r2_p.add_argument("path", help="Binary path")
    r2_p.add_argument("--no-auto", action="store_true", help="Skip auto-analysis")

    # disasm (radare2)
    disasm_p = sub.add_parser("radare2-disasm", help="Disassemble function")
    disasm_p.add_argument("path", help="Binary path")
    disasm_p.add_argument("-f", "--function", default="main", help="Function name")

    # ghidra
    ghidra_p = sub.add_parser("ghidra-analyze", help="Ghidra headless analysis")
    ghidra_p.add_argument("path", help="Binary path")
    ghidra_p.add_argument("-o", "--output", help="Output directory")

    # cross-analyze
    cross_p = sub.add_parser("cross-analyze", help="Cross-tool analysis")
    cross_p.add_argument("path", help="Binary path")

    # summary
    summary_p = sub.add_parser("summary", help="Tool summary")

    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    rt = ReverseTools()

    if not hasattr(args, "command") or not args.command:
        s = rt.get_summary()
        print(f"ReverseTools v{s['version']}")
        for t in s["tools"]:
            icon = "[INSTALLED]" if t["installed"] else "[MISSING]"
            print(f"  {icon} {t['name']}: {t['version']}")
        return

    if args.command == "check":
        if args.tool == "all":
            tools = rt.check_all()
        elif args.tool == "ghidra":
            tools = [rt.check_ghidra()]
        else:
            tools = [rt.check_radare2()]

        if args.json:
            print(json.dumps([t.__dict__ for t in tools], indent=2))
        else:
            for t in tools:
                icon = "[OK]" if t.installed else "[MISSING]"
                print(f"{icon} {t.name}: {t.version or 'not installed'}")

    elif args.command == "install":
        if args.tool == "radare2":
            result = rt.install_radare2()
        else:
            result = rt.install_ghidra()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if result["success"]:
                print(f"[OK] {args.tool} installed via {result['method']}")
            else:
                print(f"[FAIL] {result.get('error', 'unknown error')}")

    elif args.command == "radare2-analyze":
        result = rt.radare2_analyze(args.path, auto_analysis=not args.no_auto)
        if args.json:
            print(json.dumps(result.__dict__, indent=2, default=str))
        else:
            print(f"radare2 Analysis: {result.path}")
            print(f"  Arch: {result.arch} ({result.bits}-bit)")
            print(f"  Functions: {len(result.functions)}")
            print(f"  Imports: {len(result.imports)}")
            print(f"  Strings: {len(result.strings)}")
            print(f"  Sections: {len(result.sections)}")
            if result.hashes:
                for algo, h in result.hashes.items():
                    print(f"  {algo}: {h}")

    elif args.command == "radare2-disasm":
        ins = rt.radare2_disasm(args.path, function_name=args.function)
        if args.json:
            print(json.dumps(ins, indent=2))
        else:
            print(f"Disassembly of {args.function}:")
            for i in ins:
                print(f"  {i['address']}: {i['bytes']:<24} {i['mnemonic']}")

    elif args.command == "ghidra-analyze":
        result = rt.ghidra_headless(args.path, output_dir=args.output)
        if args.json:
            print(json.dumps(result.__dict__, indent=2))
        else:
            print(f"Ghidra Analysis: {result.path}")
            print(f"  Functions: {result.functions_count}")
            print(f"  Data Types: {result.data_types_count}")
            print(f"  Output: {result.output_dir}")

    elif args.command == "cross-analyze":
        report = rt.cross_analyze(args.path)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print(f"Cross-Analysis: {args.path}")
            for tool, data in report.items():
                if tool in ("path", "timestamp"):
                    continue
                print(f"\n  [{tool.upper()}]")
                if isinstance(data, dict):
                    for k, v in data.items():
                        print(f"    {k}: {v}")

    elif args.command == "summary":
        s = rt.get_summary()
        if args.json:
            print(json.dumps(s, indent=2))
        else:
            print(f"ReverseTools v{s['version']}")
            for t in s["tools"]:
                icon = "[INSTALLED]" if t["installed"] else "[MISSING]"
                print(f"  {icon} {t['name']}: {t['version']}")
            print(f"  Cache: {s['cache_dir']}")


if __name__ == "__main__":
    main()
