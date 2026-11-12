# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: SECURITY (nuclei bridge)

NucleiBridge drives Kali's ProjectDiscovery nuclei over the WSL bridge and
tracks template-feed freshness. Nuclei itself (plus naabu/httpx/subfinder)
lives in kali-linux; this worm is the Windows-side driver plus state.

AUTHORIZATION: only scan systems you own or are explicitly permitted to
test. Every run is recorded with timestamp, targets, and template version.
"""

import json
import re
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _optional_import(module: str, attr: str) -> Any:
    """Import ``attr`` from ``module``, returning None when unavailable.

    importlib-based so missing optional deps degrade to ``None`` instead of
    crashing the module import (and stay mypy-clean, unlike try/except
    import rebinding).
    """
    import importlib

    try:
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as exc:
        note_failure("nuclei:L34", exc)
        return None


def _noop_register(**kw: Any) -> Callable[[type], type]:
    return lambda cls: cls


# -- Worm Registry Import ---------------------------------------------
_imported_register = _optional_import("CORE.worm_registry", "register_worm")
_REGISTRY = _imported_register is not None
register_worm: Callable[..., Callable[[type], type]] = (
    _imported_register if _REGISTRY else _noop_register
)

# -- Python 3.14t Free-Threading Awareness ----------------------------
_imported_tsd = _optional_import("CORE.freethreading", "ThreadSafeDict")
_FREETHREAD = _imported_tsd is not None
ThreadSafeDict: Any = _imported_tsd if _FREETHREAD else dict

DISTRO = "kali-linux"
STATE_PATH = Path.home() / ".config" / "unified-llm" / "nuclei_state.json"


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove ANSI color codes (nuclei colorizes version output)."""
    return _ANSI_RE.sub("", text)


def _decode_wsl_output(data: bytes) -> str:
    """wsl.exe lists distros as UTF-16LE; decode accordingly when detected."""
    if b"\x00" in data:
        try:
            return data.decode("utf-16")
        except (UnicodeDecodeError, ValueError) as exc:
            note_failure("nuclei:L71", exc)
    return data.decode("utf-8", errors="replace")


def _distro_present(distros_stdout: str, distro: str) -> bool:
    names = {
        line.replace("*", "").strip().lower()
        for line in distros_stdout.splitlines()
    }
    return distro.lower() in names


def _run_wsl(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run a kali command without a shell (no injection surface)."""
    return subprocess.run(
        ["wsl", "-d", DISTRO, "--", *args],
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


@register_worm(category="security", priority=7, cooldown_seconds=30.0,
    description="Nuclei bridge — template CVE scanning via Kali WSL plus feed freshness tracking")
class NucleiBridge:
    """Template-driven vulnerability scanning through Kali's nuclei."""

    VERSION = "1.0.0"

    def __init__(self, state_path: Path | None = None):
        self._lock = threading.Lock()
        self._state_path = Path(state_path) if state_path else STATE_PATH
        self._runs: list[dict[str, Any]] = []

    # -- availability -------------------------------------------------
    @staticmethod
    def available() -> bool:
        """True when the kali distro and nuclei are both reachable."""
        try:
            distros = subprocess.run(
                ["wsl", "--list", "--quiet"],
                capture_output=True, timeout=30,
            )
            if not _distro_present(_decode_wsl_output(distros.stdout), DISTRO):
                return False
            probe = _run_wsl(["which", "nuclei"], timeout=60)
            return probe.returncode == 0 and bool(probe.stdout.strip())
        except Exception as exc:
            note_failure("nuclei:L119", exc)
            return False

    # -- template feed ------------------------------------------------
    def templates_version(self) -> str:
        """Installed nuclei version string (empty when unreachable)."""
        try:
            proc = _run_wsl(["nuclei", "-version"], timeout=60)
            for line in _strip_ansi(proc.stdout + proc.stderr).splitlines():
                line = line.strip()
                if line:
                    return line
        except Exception as exc:
            note_failure("nuclei:L131", exc)
        return ""

    def update_templates(self, timeout: int = 600) -> dict[str, Any]:
        """Refresh the nuclei template feed; records version + timestamp."""
        with self._lock:
            try:
                proc = _run_wsl(["nuclei", "-update-templates"], timeout=timeout)
                ok = proc.returncode == 0
            except Exception as exc:
                note_failure("nuclei:L141", exc)
                return {"ok": False, "error": str(exc)}
            state = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "templates_version": self.templates_version(),
                "ok": ok,
            }
            try:
                self._state_path.parent.mkdir(parents=True, exist_ok=True)
                self._state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            except Exception as exc:
                note_failure("nuclei:L151", exc)
            return state

    def templates_status(self) -> dict[str, Any]:
        """Last known feed state (empty dict when never updated)."""
        try:
            if self._state_path.exists():
                return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            note_failure("nuclei:L160", exc)
        return {}

    # -- scanning -----------------------------------------------------
    def scan(
        self,
        targets: list[str],
        severity: str | None = None,
        templates: list[str] | None = None,
        timeout: int = 900,
    ) -> dict[str, Any]:
        """Scan explicit targets; returns findings + run record.

        ``targets`` must be a non-empty list of hosts/URLs you are
        authorized to test. Findings come from nuclei ``-jsonl`` output.
        """
        if not targets or not all(isinstance(t, str) and t.strip() for t in targets):
            raise ValueError("targets must be a non-empty list of host/URL strings")
        args = ["nuclei", "-l", "@TARGETS@", "-jsonl", "-silent"]
        if severity:
            args += ["-severity", severity]
        for template in templates or []:
            args += ["-t", template]
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(t.strip() for t in targets) + "\n")
            targets_file = fh.name
        args = [targets_file if a == "@TARGETS@" else a for a in args]
        try:
            proc = _run_wsl(args, timeout=timeout)
        finally:
            try:
                Path(targets_file).unlink()
            except Exception as exc:
                note_failure("nuclei:L193", exc)
        findings: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError) as exc:
                note_failure("nuclei:L202", exc)
                continue
            info = event.get("info", {}) if isinstance(event.get("info"), dict) else {}
            findings.append({
                "template_id": event.get("template-id", event.get("templateID", "")),
                "severity": str(info.get("severity", "")).lower(),
                "host": event.get("host", ""),
                "matched_at": event.get("matched-at", event.get("matched_at", "")),
                "name": info.get("name", ""),
            })
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "targets": list(targets),
            "severity": severity,
            "templates": list(templates or []),
            "returncode": proc.returncode,
            "findings": len(findings),
            "templates_version": self.templates_version(),
        }
        with self._lock:
            self._runs.append(record)
        return {"findings": findings, "run": record}

    def run_history(self) -> list[dict[str, Any]]:
        """Audit trail of scans performed through this instance."""
        with self._lock:
            return list(self._runs)
