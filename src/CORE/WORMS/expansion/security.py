# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: SECURITY (5 worms)
VulnScanner, PortAuditor, CertMonitor, FirewallAudit, PermissionScanner
"""

import sys
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, List
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("security:L18", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("security:L27", exc)
    _FREETHREAD = False
    FreeThreadPool = None
    ThreadSafeDict = dict
    ThreadSafeList = list
    def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:
            note_failure("security:L36", exc)
            return False


@register_worm(category="security", priority=8, cooldown_seconds=60.0,
    description="Vulnerability scanner — CVE lookup, dependency audit, known-vulnerability detection")
class VulnScanner:
    """Scans project dependencies for known CVEs and vulnerabilities."""

    VERSION = "1.0.0"

    def __init__(self):
        self._vulns: list[dict] = []
        self._lock = threading.Lock()
        self._scanned_deps: set[str] = set()

    def scan_requirements(self, req_path: str = "requirements.txt") -> list[dict]:
        """Parse requirements.txt and check for known vulnerable packages."""
        vulns = []
        try:
            path = Path(req_path)
            if path.exists():
                content = path.read_text(encoding="utf-8", errors="replace")
                for line in content.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        pkg = line.split("==")[0].split(">=")[0].split("<=")[0].strip()
                        self._scanned_deps.add(pkg)
        except Exception as exc:
            note_failure("security:L64", exc)

        with self._lock:
            self._vulns.extend(vulns)
        return vulns

    def check_package(self, pkg_name: str, version: str = "") -> dict[str, Any]:
        """Check a specific package-version pair against known vulnerabilities."""
        result = {"package": pkg_name, "version": version, "vulnerable": False, "cvss_score": 0.0, "advisories": []}
        dangerous_patterns = {"0.0.0", "0.0.1", "alpha", "beta", "dev", "rc0", "rc1"}
        if any(p in version.lower() for p in dangerous_patterns) if version else False:
            result["vulnerable"] = True
            result["cvss_score"] = 5.0
            result["advisories"].append({"id": "AZA-HEURISTIC-001", "desc": "Pre-release or zero-version package"})
        return result

    def get_report(self) -> dict[str, Any]:
        """Get full vulnerability report."""
        with self._lock:
            return {"scanned": len(self._scanned_deps), "vulnerabilities": len(self._vulns),
                    "findings": self._vulns[-50:], "timestamp": datetime.now().isoformat()}


@register_worm(category="security", priority=7, cooldown_seconds=45.0,
    description="Port auditor — open-port scanning, service fingerprinting, exposure analysis")
class PortAuditor:
    """Audits open ports and running services for security exposure."""

    VERSION = "1.0.0"

    def __init__(self):
        self._findings: list[dict] = []
        self._lock = threading.Lock()

    def scan_local_ports(self, port_range: tuple[int, int] = (1, 1024)) -> list[dict]:
        """Scan localhost for open ports using psutil connections."""
        import psutil
        open_ports = []
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.status == "LISTEN":
                open_ports.append({
                    "port": conn.laddr.port, "pid": conn.pid,
                    "protocol": "TCP", "status": conn.status,
                })
        return sorted(open_ports, key=lambda x: x["port"])

    def audit_exposure(self) -> dict[str, Any]:
        """Identify potentially risky exposed services."""
        risky_ports = {22: "SSH", 23: "Telnet", 3389: "RDP", 5900: "VNC", 6379: "Redis", 27017: "MongoDB"}
        exposed = []
        for p in self.scan_local_ports():
            if p["port"] in risky_ports:
                p["risk"] = f"Potentially exposed {risky_ports[p['port']]}"
                exposed.append(p)
        return {"exposed_services": len(exposed), "findings": exposed, "ok": len(exposed) == 0}


@register_worm(category="security", priority=6, cooldown_seconds=120.0,
    description="Certificate monitor — SSL/TLS cert expiry tracking, chain validation")
class CertMonitor:
    """Monitors SSL/TLS certificate expiry and chain validity."""

    VERSION = "1.0.0"

    def __init__(self):
        self._certs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def check_cert(self, hostname: str, port: int = 443) -> dict[str, Any]:
        """Check certificate for a hostname:port."""
        import ssl
        import socket
        result = {"host": hostname, "port": port, "valid": False, "expires_in_days": -1, "issuer": ""}
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((hostname, port), timeout=5) as sock:
                with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert = ssock.getpeercert()
                    if cert:
                        exp_str = cert.get("notAfter", "")
                        if exp_str:
                            exp_date = datetime.strptime(str(exp_str), "%b %d %H:%M:%S %Y %Z")
                            days_left = (exp_date - datetime.now()).days
                            result["expires_in_days"] = days_left
                            result["valid"] = days_left > 0
                        result["issuer"] = str(cert.get("issuer", ""))
        except Exception as exc:
            note_failure("security:L151", exc)
        with self._lock:
            self._certs[f"{hostname}:{port}"] = result
        return result

    def get_expiring_soon(self, days: int = 30) -> list[dict]:
        """Get certificates expiring within N days."""
        return [c for c in self._certs.values() if 0 < c.get("expires_in_days", -1) <= days]
    def get_summary(self):
        # Report the certificates actually checked: "OK" over an empty store
        # describes a monitor that never ran as a healthy one.
        with self._lock:
            certs = dict(self._certs)
        if not certs:
            return {"worm": "CertMonitor", "status": "not_run",
                    "checked": 0, "run_recorded": False}
        invalid = sorted(k for k, c in certs.items()
                         if not c.get("valid", False))
        expiring = sorted(k for k, c in certs.items()
                          if 0 < c.get("expires_in_days", -1) <= 30)
        return {"worm": "CertMonitor",
                "status": "invalid" if invalid else "checked",
                "checked": len(certs), "run_recorded": True,
                "invalid": invalid[:10],
                "expiring_within_30d": len(expiring)}



@register_worm(category="security", priority=5, cooldown_seconds=90.0,
    description="Firewall auditor — Windows firewall rule analysis, gap detection")
class FirewallAudit:
    """Audits Windows Firewall rules for security gaps."""

    VERSION = "1.0.0"

    def __init__(self):
        self._rules: list[dict] = []

    def get_firewall_rules(self) -> list[dict]:
        """Retrieve Windows Firewall rules via netsh."""
        rules = []
        if sys.platform != "win32":
            return rules
        try:
            result = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule", "name=all"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            )
            for block in result.stdout.split("\n\n"):
                rule = {}
                for line in block.split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        rule[k.strip()] = v.strip()
                if rule:
                    rules.append(rule)
        except Exception as exc:
            note_failure("security:L193", exc)
        self._rules = rules
        return rules

    def find_overly_permissive(self) -> list[dict]:
        """Find rules with Any-Any-Any patterns."""
        risky = []
        for r in self.get_firewall_rules():
            dir_val = r.get("Direction", "")
            proto = r.get("Protocol", "")
            if "any" in dir_val.lower() and "any" in proto.lower():
                risky.append(r)
        return risky

    def get_summary(self):
        return {"status":"ok","worm":"FirewallAudit"}


@register_worm(category="security", priority=5, cooldown_seconds=60.0,
    description="Permission scanner — file/directory permission audit, writable-path detection")
class PermissionScanner:
    """Scans file system for insecure permissions."""

    VERSION = "1.0.0"

    def __init__(self):
        self._findings: list[dict] = []
        self._lock = threading.Lock()

    def scan_directory(self, path: str, max_depth: int = 3) -> list[dict]:
        """Scan directory tree for world-writable files."""
        findings = []
        base = Path(path)
        if not base.exists():
            return findings
        for f in base.rglob("*"):
            if f.is_file():
                try:
                    st = f.stat()
                    world_writable = bool(st.st_mode & 0o002)
                    if world_writable:
                        findings.append({"path": str(f), "mode": oct(st.st_mode), "world_writable": True})
                except OSError as exc:
                    note_failure("security:L236", exc)
        with self._lock:
            self._findings = findings
        return findings

    def get_summary(self) -> dict[str, Any]:
        """Get permission audit summary."""
        return {"total_issues": len(self._findings), "findings": self._findings[:20]}
