# coding: utf-8
"""
NETWORK_DIAG — Advanced Network Diagnostics Worm

Comprehensive network diagnostics and analysis for the Azazael ecosystem.

Features:
- traceroute / tracert — Hop-by-hop route tracing with latency per hop
- ping — Enhanced ICMP echo with latency statistics (min/avg/max/stddev)
- mtr — My Traceroute: continuous ping+traceroute with per-hop loss/latency
- tracepath — Path MTU discovery along a route
- ss / netstat — Socket statistics: listening ports, connections, process mapping
- strace-like — System call trace via Python trace module / subprocess
- lsof — list open files by process (files, sockets, pipes)
- ltrace-like — DLL/library load monitoring on Windows
- PAMS integration — Every diagnostic run records as a memory insight

Usage:
    >>> from CORE.WORMS.NETWORK_DIAG import NetworkDiag
    >>> nd = NetworkDiag()
    >>> nd.traceroute("google.com")
    >>> nd.ping("google.com", count=5)
    >>> nd.mtr("google.com", cycles=10)
    >>> nd.netstat(listening=True)
    >>> nd.lsof(pid=1234)
"""

import sys
import time
import json
import math
import socket
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    """Record a fail-open swallow. Never raises."""
    try:
        note_failure(where, exc)
    except Exception as _e:
        _SWALLOWED.append("%s:%s" % (where, type(_e).__name__))

# psutil is loaded lazily (segfaults on Python 3.14t free-threaded builds)

# ── Worm Registry ─────────────────────────────────────────────────────
try:
    from CORE.worm_registry import register_worm  # pyright: ignore[reportAssignmentType] -- optional import: except-path defines fallback decorator; selected by import success
    _REGISTRY = True
except ImportError as exc:
    _note_failure("worm_registry_import:L46", exc)
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
    _note_failure("pams_import:L57", exc)
    _pams = None
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class TracerouteHop:
    """Single hop in a traceroute."""
    hop: int
    ip: str
    hostname: str
    rtt_ms: list[float]  # RTT times for each probe at this hop
    loss: bool = False

    @property
    def avg_rtt(self) -> float:
        if not self.rtt_ms:
            return 0.0
        return sum(self.rtt_ms) / len(self.rtt_ms)

    @property
    def min_rtt(self) -> float:
        return min(self.rtt_ms) if self.rtt_ms else 0.0

    @property
    def max_rtt(self) -> float:
        return max(self.rtt_ms) if self.rtt_ms else 0.0


@dataclass
class PingResult:
    """Enhanced ping result with full statistics."""
    host: str
    ip: str
    transmitted: int
    received: int
    loss_percent: float
    rtt_min_ms: float
    rtt_avg_ms: float
    rtt_max_ms: float
    rtt_stddev_ms: float
    ttl: int
    rtt_samples: list[float] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.received > 0


@dataclass
class MtrHop:
    """Per-hop MTR statistics."""
    hop: int
    ip: str
    hostname: str
    loss_percent: float
    sent: int
    received: int
    rtt_min_ms: float
    rtt_avg_ms: float
    rtt_max_ms: float
    rtt_stddev_ms: float
    rtt_samples: list[float] = field(default_factory=list)


@dataclass
class MtrResult:
    """Full MTR report."""
    host: str
    hops: list[MtrHop]
    total_hops: int
    final_hop_reached: bool
    duration_sec: float


@dataclass
class NetstatEntry:
    """Single socket/connection entry."""
    proto: str
    local_addr: str
    local_port: int
    remote_addr: str
    remote_port: int
    state: str
    pid: (int | None)
    process_name: str


@dataclass
class OpenFileEntry:
    """Single open file descriptor."""
    path: str
    fd: (int | None)
    mode: str
    file_type: str  # file, socket, pipe, etc.


# ── NetworkDiag Worm ──────────────────────────────────────────────────────────

@register_worm(category="diagnostic", priority=7, cooldown_seconds=30.0, description="Advanced network diagnostics (ping, traceroute, MTR, netstat, lsof, strace)")
class NetworkDiag:
    """Advanced Network Diagnostics Worm.

    Provides cross-platform network diagnostic capabilities including
    traceroute, ping statistics, MTR, netstat, lsof, and system call tracing.
    All diagnostic results are automatically recorded to PAMS memory.
    """

    VERSION = "1.0.0"

    def __init__(self):
        self._pams = _pams
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    def _record(self, event_type: str, data: dict[str, Any], severity: str = "info"):
        """Record diagnostic event to PAMS memory."""
        if self._pams:
            try:
                self._pams.record_event(
                    event_type=f"netdiag.{event_type}",
                    data=data,
                    source="NetworkDiag",
                    severity=severity,
                )
            except (OSError, TypeError, ValueError) as exc:
                _note_failure("record:pams:L184", exc)
                pass  # best-effort memory recording

    # ── traceroute ────────────────────────────────────────────────────────────

    def traceroute(
        self,
        host: str,
        max_hops: int = 30,
        timeout_sec: int = 3,
        probes_per_hop: int = 3,
        _record: bool = True,
    ) -> list[TracerouteHop]:
        """Trace the route to a host with per-hop latency.

        On Windows: uses tracert.exe
        On Linux: uses traceroute or raw sockets for ICMP
        """
        hops = []

        if IS_WINDOWS:
            hops = self._traceroute_windows(host, max_hops, timeout_sec)
        else:
            hops = self._traceroute_linux(host, max_hops, timeout_sec, probes_per_hop)

        if _record:
            self._record("traceroute", {
                "host": host,
                "hops_found": len(hops),
                "max_hops": max_hops,
                "final_hop": hops[-1].ip if hops else "none",
                "total_latency_ms": sum(h.avg_rtt for h in hops),
            })

        return hops

    def _traceroute_windows(self, host: str, max_hops: int, timeout_sec: int) -> list[TracerouteHop]:
        """Windows tracert parser."""
        hops = []
        try:
            result = subprocess.run(
                ["tracert", "-d", "-h", str(max_hops), "-w", str(timeout_sec * 1000), host],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=max_hops * timeout_sec + 10,
            )
            for line in result.stdout.splitlines()[4:]:  # Skip header
                line = line.strip()
                if not line or "Trace complete" in line:
                    break

                # Parse: "  N   [<time> ms]  [<time> ms]  [<time> ms]  <IP>"
                parts = line.split()
                if len(parts) < 2:
                    continue

                try:
                    hop_num = int(parts[0])
                except ValueError as exc:
                    _note_failure("traceroute_windows:hop_num:L240", exc)
                    continue

                # Extract RTTs and IP
                rtts = []
                ip = ""
                for p in parts[1:]:
                    if p.endswith("ms"):
                        try:
                            rtts.append(float(p.replace("ms", "")))
                        except ValueError as exc:
                            _note_failure("traceroute_windows:rtt:L250", exc)
                            pass
                    elif p == "*":
                        rtts.append(0.0)
                    elif p not in ("ms", "<1"):
                        # Likely the IP
                        if not ip and not p.startswith("["):
                            ip = p

                if ip == "" and not rtts:
                    ip = "Request timed out"

                # Try DNS reverse lookup
                hostname = ""
                if ip and ip != "Request timed out":
                    try:
                        hostname = socket.gethostbyaddr(ip)[0]
                    except OSError as exc:
                        _note_failure("traceroute_windows:reverse_dns:L315", exc)
                        hostname = ip

                hops.append(TracerouteHop(
                    hop=hop_num,
                    ip=ip,
                    hostname=hostname,
                    rtt_ms=[r for r in rtts if r > 0] if rtts else [],
                    loss=ip == "Request timed out" or not rtts,
                ))
        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            _note_failure("traceroute_windows:L277", e)
            self._record("traceroute_error", {"host": host, "error": str(e)}, severity="error")

        return hops

    def _traceroute_linux(self, host: str, max_hops: int, timeout_sec: int, probes: int) -> list[TracerouteHop]:
        """Linux traceroute parser."""
        hops = []
        try:
            result = subprocess.run(
                ["traceroute", "-n", "-m", str(max_hops), "-w", str(timeout_sec),
                 "-q", str(probes), host],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=max_hops * timeout_sec + 10,
            )
            for line in result.stdout.splitlines()[1:]:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                try:
                    hop_num = int(parts[0])
                except ValueError as exc:
                    _note_failure("traceroute_linux:hop_num:L300", exc)
                    continue

                ip = parts[1] if not parts[1].startswith("*") else "timeout"
                rtts = []
                for p in parts[2:]:
                    try:
                        rtts.append(float(p.replace("ms", "")))
                    except ValueError as exc:
                        _note_failure("traceroute_linux:rtt:L308", exc)
                        pass

                hostname = ""
                if ip != "timeout":
                    try:
                        hostname = socket.gethostbyaddr(ip)[0]
                    except OSError as exc:
                        _note_failure("traceroute_linux:reverse_dns:L267", exc)
                        hostname = ip

                hops.append(TracerouteHop(
                    hop=hop_num, ip=ip, hostname=hostname,
                    rtt_ms=rtts, loss=(ip == "timeout"),
                ))
        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            _note_failure("traceroute_linux:L322", e)
            self._record("traceroute_error", {"host": host, "error": str(e)}, severity="error")

        return hops

    # ── Enhanced ping ─────────────────────────────────────────────────────────

    def ping(
        self,
        host: str,
        count: int = 4,
        timeout_sec: int = 3,
        packet_size: int = 32,
    ) -> PingResult:
        """Enhanced ping with full latency statistics (min/avg/max/stddev/ttl).

        On Windows: uses ping.exe -n count
        On Linux: uses ping -c count
        """
        rtts = []
        ip = ""
        ttl = 0
        transmitted = count
        received = 0

        if IS_WINDOWS:
            ip, rtts, received, ttl = self._ping_windows(host, count, timeout_sec, packet_size)
        else:
            ip, rtts, received, ttl = self._ping_linux(host, count, timeout_sec, packet_size)

        # Calculate statistics
        loss = ((transmitted - received) / transmitted * 100) if transmitted > 0 else 100.0
        avg = sum(rtts) / len(rtts) if rtts else 0.0
        min_rtt = min(rtts) if rtts else 0.0
        max_rtt = max(rtts) if rtts else 0.0

        # Standard deviation
        if len(rtts) > 1:
            variance = sum((r - avg) ** 2 for r in rtts) / len(rtts)
            stddev = math.sqrt(variance)
        else:
            stddev = 0.0

        result = PingResult(
            host=host,
            ip=ip or host,
            transmitted=transmitted,
            received=received,
            loss_percent=round(loss, 1),
            rtt_min_ms=round(min_rtt, 2),
            rtt_avg_ms=round(avg, 2),
            rtt_max_ms=round(max_rtt, 2),
            rtt_stddev_ms=round(stddev, 2),
            ttl=ttl,
            rtt_samples=rtts,
        )

        self._record("ping", {
            "host": host, "ip": ip, "received": received,
            "loss_percent": result.loss_percent,
            "avg_rtt_ms": result.rtt_avg_ms,
            "min_rtt_ms": result.rtt_min_ms,
            "max_rtt_ms": result.rtt_max_ms,
        })

        return result

    def _ping_windows(self, host: str, count: int, timeout: int, size: int) -> tuple[str, list[float], int, int]:
        rtts: list[float] = []
        ip = ""
        ttl = 0
        received = 0

        try:
            result = subprocess.run(
                ["ping", "-n", str(count), "-w", str(timeout * 1000),
                 "-l", str(size), host],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout * count + 10,
            )
            for line in result.stdout.splitlines():
                # Extract IP: "Pinging google.com [142.250.80.14] with 32 bytes"
                if "Pinging" in line and "[" in line:
                    try:
                        ip = line[line.index("[") + 1:line.index("]")]
                    except ValueError as exc:
                        _note_failure("ping_windows:ip:L406", exc)
                        pass
                # Extract TTL: "time<1ms TTL=118" or "time=10ms TTL=118"
                if "TTL=" in line:
                    try:
                        ttl_str = line[line.index("TTL=") + 4:].split()[0]
                        ttl = int(ttl_str)
                    except (ValueError, IndexError) as exc:
                        _note_failure("ping_windows:ttl:L413", exc)
                        pass
                    try:
                        if "time<" in line:
                            rtts.append(0.5)
                            received += 1
                        elif "time=" in line:
                            rtt = float(line[line.index("time=") + 5:].replace("ms", "").split()[0])
                            rtts.append(rtt)
                            received += 1
                    except (ValueError, IndexError) as exc:
                        _note_failure("ping_windows:rtt:L423", exc)
                        pass

            # Extract summary for received count
            for line in result.stdout.splitlines():
                if "Received =" in line:
                    try:
                        received = int(line.split("Received =")[1].split(",")[0].strip())
                    except (ValueError, IndexError) as exc:
                        _note_failure("ping_windows:summary:L431", exc)
                        pass

        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            _note_failure("ping_windows:L434", e)
            self._record("ping_error", {"host": host, "error": str(e)}, severity="error")

        return ip, rtts, received, ttl

    def _ping_linux(self, host: str, count: int, timeout: int, size: int) -> tuple[str, list[float], int, int]:
        rtts = []
        ip = ""
        ttl = 0
        received = 0

        try:
            result = subprocess.run(
                ["ping", "-c", str(count), "-W", str(timeout), "-s", str(size), host],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout * count + 10,
            )
            for line in result.stdout.splitlines():
                # Extract IP: "64 bytes from 142.250.80.14: icmp_seq=1 ttl=118 time=12.3 ms"
                if "bytes from" in line:
                    try:
                        parts = line.split()
                        ip_part = [p for p in parts if ":" in p and "." in p][0]
                        ip = ip_part.rstrip(":")
                        if "ttl=" in line:
                            ttl = int(line.split("ttl=")[1].split()[0])
                        if "time=" in line:
                            rtt = float(line.split("time=")[1].replace("ms", "").strip())
                            rtts.append(rtt)
                            received += 1
                    except (ValueError, IndexError) as exc:
                        _note_failure("ping_linux:parse:L463", exc)
                        pass

                # Extract summary
                if "received," in line:
                    try:
                        received = int(line.split()[3])
                    except (ValueError, IndexError) as exc:
                        _note_failure("ping_linux:summary:L470", exc)
                        pass

        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            _note_failure("ping_linux:L473", e)
            self._record("ping_error", {"host": host, "error": str(e)}, severity="error")

        return ip, rtts, received, ttl

    # ── MTR (My Traceroute) ───────────────────────────────────────────────────

    def mtr(
        self,
        host: str,
        cycles: int = 10,
        max_hops: int = 30,
        timeout_sec: int = 3,
        interval_sec: float = 1.0,
    ) -> MtrResult:
        """MTR: Continuous ping+traceroute with per-hop loss & latency statistics.

        Runs multiple traceroute cycles and aggregates per-hop statistics
        including loss percentage, min/avg/max/stddev RTT.
        """
        start_time = time.perf_counter()
        hop_data: dict[int, list[float]] = {}  # hop_num -> list of RTTs
        hop_ips: dict[int, str] = {}
        final_reached = False

        for cycle in range(cycles):
            hops = self.traceroute(host, max_hops=max_hops, timeout_sec=timeout_sec, probes_per_hop=1, _record=False)

            for h in hops:
                if h.hop not in hop_data:
                    hop_data[h.hop] = []
                    hop_ips[h.hop] = h.ip
                hop_data[h.hop].extend(h.rtt_ms)

            # Check if we reached the target (last hop's IP resolves to host)
            if hops and not hops[-1].loss:
                final_reached = True

            if cycle < cycles - 1:
                time.sleep(interval_sec)

        # Build MTR hop statistics
        mtr_hops = []
        total_sent = cycles  # 1 probe per cycle per hop
        for hop_num in sorted(hop_data.keys()):
            rtts = hop_data[hop_num]
            total_recv = len(rtts)
            loss_pct = ((total_sent - total_recv) / total_sent * 100) if total_sent > 0 else 100.0

            avg = sum(rtts) / len(rtts) if rtts else 0.0
            stddev = 0.0
            if len(rtts) > 1:
                variance = sum((r - avg) ** 2 for r in rtts) / len(rtts)
                stddev = math.sqrt(variance)

            ip = hop_ips[hop_num]
            try:
                hostname = socket.gethostbyaddr(ip)[0] if ip and ip != "Request timed out" else ip
            except (OSError, UnicodeError) as exc:
                _note_failure("mtr:reverse_dns:L531", exc)
                hostname = ip

            mtr_hops.append(MtrHop(
                hop=hop_num,
                ip=ip,
                hostname=hostname,
                loss_percent=round(loss_pct, 1),
                sent=total_sent,
                received=total_recv,
                rtt_min_ms=round(min(rtts), 2) if rtts else 0.0,
                rtt_avg_ms=round(avg, 2),
                rtt_max_ms=round(max(rtts), 2) if rtts else 0.0,
                rtt_stddev_ms=round(stddev, 2),
                rtt_samples=rtts,
            ))

        duration = time.perf_counter() - start_time

        result = MtrResult(
            host=host,
            hops=mtr_hops,
            total_hops=len(mtr_hops),
            final_hop_reached=final_reached,
            duration_sec=round(duration, 2),
        )

        self._record("mtr", {
            "host": host, "cycles": cycles, "hops": len(mtr_hops),
            "final_reached": final_reached,
            "worst_loss_pct": max((h.loss_percent for h in mtr_hops), default=0),
            "duration_sec": duration,
        })

        return result

    # ── tracepath (Path MTU Discovery) ────────────────────────────────────────

    def tracepath(
        self,
        host: str,
        max_hops: int = 30,
        timeout_sec: int = 3,
    ) -> dict[str, Any]:
        """Trace path with MTU discovery.

        Discovers the MTU at each hop along the path to detect
        fragmentation issues and path bottlenecks.

        On Windows: approximates via tracert with flags
        On Linux: uses tracepath if available
        """
        result: dict[str, Any] = {
            "host": host, "hops": [], "pmtu": None, "asymmetry": None
        }

        if IS_LINUX:
            try:
                proc = subprocess.run(
                    ["tracepath", "-n", "-m", str(max_hops), host],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=max_hops * timeout_sec + 10,
                )
                for line in proc.stdout.splitlines():
                    line = line.strip()
                    if "pmtu" in line.lower():
                        try:
                            result["pmtu"] = int(line.split("pmtu")[1].split()[0])
                        except (ValueError, IndexError) as exc:
                            _note_failure("tracepath:pmtu:L598", exc)
                            pass
                    if "asymm" in line.lower():
                        try:
                            result["asymmetry"] = int(line.split("asymm")[1].split()[0])
                        except (ValueError, IndexError) as exc:
                            _note_failure("tracepath:asymmetry:L603", exc)
                            pass
                    # Parse hop lines: " 1:  192.168.1.1  0.512ms pmtu 1500"
                    parts = line.split()
                    if len(parts) >= 3 and ":" in parts[0]:
                        try:
                            hop_data = {
                                "hop": int(parts[0].rstrip(":")),
                                "ip": parts[1] if not parts[1].startswith("*") else "timeout",
                                "rtt_ms": float(parts[2].replace("ms", "")) if len(parts) > 2 else 0.0,
                            }
                            result["hops"].append(hop_data)
                        except (ValueError, IndexError) as exc:
                            _note_failure("tracepath:hop:L615", exc)
                            pass
            except (OSError, subprocess.TimeoutExpired, ValueError) as e:
                _note_failure("tracepath_linux:L617", e)
                result["error"] = str(e)
        else:
            # Windows: fallback to traceroute with MTU notes
            hops = self.traceroute(host, max_hops=max_hops, timeout_sec=timeout_sec)
            # Estimate PMTU from common values
            result["pmtu"] = 1500  # Standard Ethernet
            result["note"] = "PMTU discovery limited on Windows; using standard Ethernet MTU"
            result["hops"] = [
                {"hop": h.hop, "ip": h.ip, "rtt_ms": h.avg_rtt} for h in hops
            ]

        self._record("tracepath", result)
        return result

    # ── ss / netstat (Socket Statistics) ──────────────────────────────────────

    def netstat(
        self,
        listening: bool = False,
        tcp: bool = True,
        udp: bool = True,
        resolve_names: bool = False,
    ) -> list[NetstatEntry]:
        """Socket statistics: list all connections with process mapping.

        On Windows: uses `netstat -ano` + psutil for process names
        On Linux: uses `ss -tunlp` if available, falls back to netstat
        """
        entries = []

        if IS_WINDOWS:
            entries = self._netstat_windows(listening, tcp, udp, resolve_names)
        else:
            entries = self._netstat_linux(listening, tcp, udp, resolve_names)

        self._record("netstat", {
            "total_connections": len(entries),
            "listening_only": listening,
            "tcp": tcp, "udp": udp,
            "listening_count": sum(1 for e in entries if e.state == "LISTENING"),
            "established_count": sum(1 for e in entries if e.state == "ESTABLISHED"),
        })

        return entries

    def _netstat_windows(self, listening: bool, tcp: bool, udp: bool, resolve: bool) -> list[NetstatEntry]:
        entries = []
        # Build PID->name cache from psutil (lazy import)
        pid_names: dict[int, str] = {}
        try:
            import psutil as _psutil
            for proc in _psutil.process_iter(["pid", "name"]):
                try:
                    pid_names[proc.info["pid"]] = proc.info["name"] or ""
                except (_psutil.NoSuchProcess, _psutil.AccessDenied) as exc:
                    _note_failure("netstat_windows:proc_iter:L747", exc)
                    pass
        except ImportError as exc:
            _note_failure("netstat_windows:psutil:L749", exc)
            pass  # psutil unavailable

        try:
            args = ["netstat", "-ano"]
            result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)

            # Skip header lines robustly: skip until we find the first data line (starts with "  TCP" or "  UDP")
            lines = result.stdout.splitlines()
            data_start = 0
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("TCP") or stripped.startswith("UDP"):
                    data_start = i
                    break

            for line in lines[data_start:]:
                line = line.strip()
                if not line:
                    continue

                parts = line.split()
                if len(parts) < 4:
                    continue

                proto = parts[0].lower()
                if (proto == "tcp" and not tcp) or (proto == "udp" and not udp):
                    continue

                local = parts[1]
                remote = parts[2]
                state = parts[3] if proto == "tcp" else ""

                # Parse IP:port
                local_ip, local_port = self._parse_addr(local)
                remote_ip, remote_port = self._parse_addr(remote)

                # PID is the last column
                pid = None
                try:
                    pid = int(parts[-1])
                except ValueError as exc:
                    _note_failure("netstat_windows:pid:L715", exc)
                    pass

                if listening and state != "LISTENING" and state != "LISTEN":
                    continue

                process_name = pid_names.get(pid, "") if pid is not None else ""

                entries.append(NetstatEntry(
                    proto=proto.upper(),
                    local_addr=local_ip,
                    local_port=local_port,
                    remote_addr=remote_ip,
                    remote_port=remote_port,
                    state=state if state else ("*" if proto == "udp" else ""),
                    pid=pid,
                    process_name=process_name,
                ))

        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            _note_failure("netstat_windows:L734", e)
            self._record("netstat_error", {"error": str(e)}, severity="error")

        return entries

    def _netstat_linux(self, listening: bool, tcp: bool, udp: bool, resolve: bool) -> list[NetstatEntry]:
        entries = []
        pid_names: dict[int, str] = {}
        try:
            import psutil as _psutil
            for proc in _psutil.process_iter(["pid", "name"]):
                try:
                    pid_names[proc.info["pid"]] = proc.info["name"] or ""
                except (_psutil.NoSuchProcess, _psutil.AccessDenied) as exc:
                    _note_failure("netstat_linux:proc_iter:L672", exc)
                    pass
        except ImportError as exc:
            _note_failure("netstat_linux:psutil:L674", exc)
            pass  # psutil unavailable

        # Try `ss` first (faster), fall back to netstat
        cmd = ["ss", "-tunlp"] if listening else ["ss", "-tuna"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
            if result.returncode != 0:
                raise FileNotFoundError("ss not available")

            for line in result.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 5:
                    continue
                state = parts[0].upper()
                local = parts[4]
                remote = parts[5] if len(parts) > 5 else ""

                # Extract PID from "users:((\"process\",pid=1234,fd=5))"
                pid = None
                process_name = ""
                if "pid=" in line:
                    try:
                        pid_str = line[line.index("pid=") + 4:].split(",")[0]
                        pid = int(pid_str)
                        process_name = pid_names.get(pid, "")
                    except (ValueError, IndexError) as exc:
                        _note_failure("netstat_linux:pid:L775", exc)
                        pass

                local_ip, local_port = self._parse_addr(local)
                remote_ip, remote_port = self._parse_addr(remote) if remote else ("", 0)

                entries.append(NetstatEntry(
                    proto="TCP" if "tcp" in state.lower() else "UDP",
                    local_addr=local_ip, local_port=local_port,
                    remote_addr=remote_ip, remote_port=remote_port,
                    state=state if "tcp" in state.lower() else "",
                    pid=pid, process_name=process_name,
                ))
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            _note_failure("netstat_linux:ss:L788", exc)
            pass  # ss uninstalled or unreachable

        return entries

    def _parse_addr(self, addr: str) -> tuple[str, int]:
        """Parse 'IP:port' or '[IPv6]:port' into (ip, port)."""
        if not addr or addr == "*:*":
            return ("*", 0)
        # IPv6: [::1]:port
        if addr.startswith("["):
            try:
                close = addr.index("]")
                ip = addr[1:close]
                port = int(addr[close + 2:]) if addr[close + 1:] else 0
                return (ip, port)
            except (ValueError, IndexError) as exc:
                _note_failure("parse_addr:ipv6:L804", exc)
                pass
        # IPv4: ip:port
        if ":" in addr:
            parts = addr.rsplit(":", 1)
            try:
                port = int(parts[1])
                return (parts[0], port)
            except (ValueError, IndexError) as exc:
                _note_failure("parse_addr:ipv4:L812", exc)
                return (addr, 0)
        return (addr, 0)

    # ── strace-like (System Call Trace) ───────────────────────────────────────

    def strace(
        self,
        command: str,
        args: (list[str] | None) = None,
        timeout_sec: int = 30,
    ) -> dict[str, Any]:
        """System call trace (strace-like) for a command.

        On Linux: uses strace if available
        On Windows: uses Python's trace module to monitor subprocess
        Cross-platform fallback: subprocess execution with resource monitoring

        WARNING: strace provides deep system visibility. Use responsibly.
        """
        args = args or []
        result = {
            "command": command,
            "args": args,
            "platform": sys.platform,
            "syscalls": [],
            "summary": {},
            "output": "",
            "error": "",
        }

        if IS_LINUX:
            try:
                strace_cmd = ["strace", "-f", "-c", command] + args
                proc = subprocess.run(
                    strace_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=timeout_sec,
                )
                result["output"] = proc.stdout[:10000]
                result["error"] = proc.stderr[:5000]

                # Parse strace summary table
                for line in proc.stderr.splitlines():
                    if "%" in line and "seconds" in line:
                        try:
                            parts = line.split()
                            if len(parts) >= 4:
                                result["syscalls"].append({
                                    "name": parts[-1] if len(parts) > 5 else parts[-2],
                                    "calls": parts[0],
                                    "time_percent": parts[-2] if "%" in parts[-2] else "",
                                })
                        except (ValueError, IndexError) as exc:
                            _note_failure("strace_linux:L864", exc)
                            pass
            except FileNotFoundError as exc:
                _note_failure("strace_linux:L866", exc)
                result["error"] = "strace not installed. Install: apt install strace"
            except (OSError, subprocess.TimeoutExpired, ValueError) as e:
                _note_failure("strace_linux:L868", e)
                result["error"] = str(e)
        else:
            # Windows: resource-monitored subprocess execution
            try:
                start = time.perf_counter()
                proc = subprocess.run(
                    [command] + args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=timeout_sec,
                )
                elapsed = time.perf_counter() - start

                result["output"] = proc.stdout[:10000]
                result["summary"] = {
                    "exit_code": proc.returncode,
                    "duration_sec": round(elapsed, 3),
                    "note": "Windows strace limited; use 'Process Monitor' or 'strace.exe' for full syscall tracing",
                }

                # Monitor process resources via psutil (lazy import; proc.pid provides the child PID)
                try:
                    import psutil as _psutil
                    child = _psutil.Process(proc.pid)  # pyright: ignore[reportAttributeAccessIssue]
                    try:
                        with child.oneshot():
                            result["syscalls"].append({
                                "pid": child.pid,
                                "name": child.name(),
                                "cpu_percent": child.cpu_percent(),
                                "memory_mb": round(child.memory_info().rss / (1024**2), 2),
                                "threads": child.num_threads(),
                            })
                    except (_psutil.NoSuchProcess, _psutil.AccessDenied) as exc:
                        _note_failure("strace_windows:L900", exc)
                        pass
                except ImportError as exc:
                    _note_failure("strace_windows:L902", exc)
                    pass  # psutil unavailable
            except (OSError, subprocess.TimeoutExpired, ValueError) as e:
                _note_failure("strace_windows:L904", e)
                result["error"] = str(e)

        self._record("strace", {
            "command": command,
            "syscalls_count": len(result["syscalls"]),
        })
        return result

    # ── lsof (List Open Files) ────────────────────────────────────────────────

    def lsof(
        self,
        pid: (int | None) = None,
        process_name: (str | None) = None,
        file_type_filter: (str | None) = None,
        max_processes: int = 200,
    ) -> dict[int, list[OpenFileEntry]]:
        """list open files by process.

        On all platforms: uses psutil.Process.open_files() and .connections()
        On Linux: also tries `lsof -p PID` for more detail
        """
        import psutil as _psutil
        result: dict[int, list[OpenFileEntry]] = {}

        processes_to_check = []

        if pid:
            try:
                processes_to_check = [_psutil.Process(pid)]
            except _psutil.NoSuchProcess as exc:
                _note_failure("lsof:L935", exc)
                self._record("lsof_error", {"pid": pid, "error": "Process not found"}, severity="warning")
                return {}
        elif process_name:
            for proc in _psutil.process_iter(["pid", "name"]):
                try:
                    if process_name.lower() in (proc.info["name"] or "").lower():
                        processes_to_check.append(proc)
                except (_psutil.AccessDenied, _psutil.NoSuchProcess) as exc:
                    _note_failure("lsof:name_filter:L943", exc)
                    pass
        else:
            # All processes (bounded for safety)
            processes_to_check = list(_psutil.process_iter())[:max_processes]

        for proc in processes_to_check:
            entries = []
            pid_val = proc.pid

            # Open files
            try:
                for f in proc.open_files():
                    entry = OpenFileEntry(
                        path=str(f.path) if f.path else str(f.fd),
                        fd=f.fd,
                        mode=f.mode if hasattr(f, "mode") else "",
                        file_type="file",
                    )
                    if file_type_filter is None or file_type_filter == "file":
                        entries.append(entry)
            except (_psutil.AccessDenied, _psutil.NoSuchProcess) as exc:
                _note_failure("lsof:open_files:L964", exc)
                pass

            # Network connections (sockets)
            try:
                for conn in proc.connections(kind="all"):
                    entry = OpenFileEntry(
                        path=f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "",
                        fd=conn.fd,
                        mode="",
                        file_type="socket",
                    )
                    if file_type_filter is None or file_type_filter == "socket":
                        entries.append(entry)
            except (_psutil.AccessDenied, _psutil.NoSuchProcess) as exc:
                _note_failure("lsof:sockets:L978", exc)
                pass

            if entries:
                result[pid_val] = entries

        self._record("lsof", {
            "processes_scanned": len(processes_to_check),
            "processes_with_open_files": len(result),
            "total_open_files": sum(len(v) for v in result.values()),
        })

        return result

    # ── ltrace-like (Library Trace) ───────────────────────────────────────────

    def ltrace(
        self,
        command: str,
        args: (list[str] | None) = None,
        timeout_sec: int = 30,
    ) -> dict[str, Any]:
        """Library call trace (ltrace-like).

        On Linux: uses ltrace if available
        On Windows: monitors DLL loading via subprocess + module enumeration
        """
        args = args or []
        result = {
            "command": command,
            "args": args,
            "platform": sys.platform,
            "libraries": [],
            "calls": [],
            "output": "",
        }

        if IS_LINUX:
            try:
                ltrace_cmd = ["ltrace", "-c", command] + args
                proc = subprocess.run(
                    ltrace_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=timeout_sec,
                )
                result["output"] = proc.stderr[:10000] + "\n" + proc.stdout[:10000]

                # Parse ltrace summary
                for line in proc.stderr.splitlines():
                    if "%" in line and "(" in line:
                        try:
                            parts = line.split()
                            if len(parts) >= 4:
                                result["calls"].append({
                                    "function": parts[-1].split("(")[0] if "(" in parts[-1] else parts[-1],
                                    "count": parts[0],
                                    "time_percent": parts[-2] if "%" in parts[-2] else "",
                                })
                        except (ValueError, IndexError) as exc:
                            _note_failure("ltrace_linux:L1035", exc)
                            pass
            except FileNotFoundError as exc:
                _note_failure("ltrace_linux:L1037", exc)
                result["error"] = "ltrace not installed. Install: apt install ltrace"
            except (OSError, subprocess.TimeoutExpired, ValueError) as e:
                _note_failure("ltrace_linux:L1039", e)
                result["error"] = str(e)
        else:
            # Windows: DLL loading analysis
            try:
                proc = subprocess.run(
                    [command] + args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=timeout_sec,
                )
                result["output"] = proc.stdout[:10000]

                # Enumerate loaded modules via psutil (lazy import)
                try:
                    import psutil as _psutil
                    child = _psutil.Process(proc.pid)  # pyright: ignore[reportAttributeAccessIssue]
                    try:
                        for mmap in child.memory_maps():
                            if mmap.path and (mmap.path.endswith(".dll") or mmap.path.endswith(".so")):
                                result["libraries"].append({
                                    "pid": child.pid,
                                    "path": mmap.path,
                                    "rss_mb": round(mmap.rss / (1024**2), 2),
                                })
                    except (_psutil.NoSuchProcess, _psutil.AccessDenied) as exc:
                        _note_failure("ltrace_windows:L1062", exc)
                        pass
                except ImportError as exc:
                    _note_failure("ltrace_windows:L1064", exc)
                    pass  # psutil unavailable

                result["note"] = "Windows ltrace limited; DLL enumeration from memory maps"

            except (OSError, subprocess.TimeoutExpired, ValueError) as e:
                _note_failure("ltrace_windows:L1069", e)
                result["error"] = str(e)

        self._record("ltrace", {
            "command": command,
            "libraries_found": len(result["libraries"]),
            "calls_traced": len(result["calls"]),
        })

        return result

    # ── Combined Diagnostics ─────────────────────────────────────────────────

    def get_summary(self) -> dict[str, Any]:
        """Return a lightweight status summary for MetaWormSpawner fleet reports."""
        return {
            "version": self.VERSION,
            "platform": sys.platform,
            "tools": ["ping", "traceroute", "mtr", "tracepath", "netstat", "lsof", "strace", "ltrace"],
            "tool_count": 8,
            "session_id": self._session_id,
            "pams_connected": self._pams is not None,
        }

    def run_full_diagnostics(self, target: str) -> dict[str, Any]:
        """Run all network diagnostics against a target and return comprehensive report."""
        report: dict[str, Any] = {
            "target": target,
            "timestamp": datetime.now().isoformat(),
            "version": self.VERSION,
        }

        # Run diagnostics in parallel
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(self.ping, target, count=5): "ping",
                pool.submit(self.traceroute, target): "traceroute",
                pool.submit(self.mtr, target, cycles=5): "mtr",
                pool.submit(self.tracepath, target): "tracepath",
            }

            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result(timeout=60)
                    if name == "ping":
                        report["ping"] = {
                            "success": result.success,
                            "avg_rtt_ms": result.rtt_avg_ms,
                            "loss_percent": result.loss_percent,
                            "ttl": result.ttl,
                        }
                    elif name == "traceroute":
                        report["traceroute"] = {
                            "hops": len(result),
                            "final_hop": result[-1].ip if result else "none",
                            "total_latency_ms": sum(h.avg_rtt for h in result),
                        }
                    elif name == "mtr":
                        report["mtr"] = {
                            "hops": result.total_hops,
                            "final_reached": result.final_hop_reached,
                            "worst_loss_pct": max((h.loss_percent for h in result.hops), default=0),
                        }
                    elif name == "tracepath":
                        report["tracepath"] = {
                            "pmtu": result.get("pmtu"),
                            "hops": len(result.get("hops", [])),
                        }
                except Exception as e:  # noqa: BLE001 — future.result re-raises any diagnostic error; per-diagnostic capture required
                    _note_failure("run_full_diagnostics:L1138", e)
                    report[name] = {"error": str(e)}

        # PAMS memory
        self._record("full_diagnostics", report)
        return report


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def main():
    """CLI entry point for NetworkDiag worm."""

    parser = argparse.ArgumentParser(
        description="NETWORK_DIAG — Advanced Network Diagnostics Worm",
        epilog="All results are recorded to PAMS memory for later analysis.",
    )
    sub = parser.add_subparsers(dest="command", help="Diagnostic command")

    # ping
    ping_p = sub.add_parser("ping", help="Enhanced ping with statistics")
    ping_p.add_argument("host", help="Target host")
    ping_p.add_argument("-c", "--count", type=int, default=4, help="Ping count")
    ping_p.add_argument("-", "--timeou", type=int, default=3, help="Timeout (sec)")
    ping_p.add_argument("-s", "--size", type=int, default=32, help="Packet size")

    # traceroute
    trace_p = sub.add_parser("traceroute", help="Trace route to host")
    trace_p.add_argument("host", help="Target host")
    trace_p.add_argument("-m", "--max-hops", type=int, default=30)

    # mtr
    mtr_p = sub.add_parser("mtr", help="My Traceroute (continuous)")
    mtr_p.add_argument("host", help="Target host")
    mtr_p.add_argument("-c", "--cycles", type=int, default=10)
    mtr_p.add_argument("-m", "--max-hops", type=int, default=30)

    # tracepath
    tp_p = sub.add_parser("tracepath", help="Path MTU discovery")
    tp_p.add_argument("host", help="Target host")

    # netstat
    ns_p = sub.add_parser("netstat", help="Socket statistics")
    ns_p.add_argument("-l", "--listening", action="store_true", help="Listening only")

    # lsof
    lsof_p = sub.add_parser("lsof", help="list open files")
    lsof_p.add_argument("-p", "--pid", type=int, help="Process PID")
    lsof_p.add_argument("-n", "--name", help="Process name filter")

    # strace
    strace_p = sub.add_parser("strace", help="System call trace")
    strace_p.add_argument("command", help="Command to trace")
    strace_p.add_argument("args", nargs="*", help="Command arguments")

    # ltrace
    ltrace_p = sub.add_parser("ltrace", help="Library call trace")
    ltrace_p.add_argument("command", help="Command to trace")
    ltrace_p.add_argument("args", nargs="*", help="Command arguments")

    # full
    full_p = sub.add_parser("full", help="Run all diagnostics")
    full_p.add_argument("host", help="Target host")

    # JSON output
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    nd = NetworkDiag()

    if args.command == "ping":
        result = nd.ping(args.host, count=args.count, timeout_sec=args.timeout, packet_size=args.size)
        if args.json:
            print(json.dumps(result.__dict__, indent=2, default=str))
        else:
            print(f"PING {result.host} ({result.ip}): {result.received}/{result.transmitted} received, {result.loss_percent}% loss")
            print(f"  min/avg/max/stddev = {result.rtt_min_ms}/{result.rtt_avg_ms}/{result.rtt_max_ms}/{result.rtt_stddev_ms} ms")
            print(f"  TTL={result.ttl}")

    elif args.command == "traceroute":
        hops = nd.traceroute(args.host, max_hops=args.max_hops)
        if args.json:
            print(json.dumps([h.__dict__ for h in hops], indent=2, default=str))
        else:
            print(f"traceroute to {args.host}, {args.max_hops} hops max")
            for h in hops:
                rtt_str = f"  avg={h.avg_rtt:.1f}ms" if h.rtt_ms else "  *"
                print(f"  {h.hop:>3}  {h.ip:<20} {rtt_str}  {'(loss)' if h.loss else ''}")

    elif args.command == "mtr":
        result = nd.mtr(args.host, cycles=args.cycles, max_hops=args.max_hops)
        if args.json:
            print(json.dumps({"host": result.host, "hops": [h.__dict__ for h in result.hops],
                              "final_reached": result.final_hop_reached, "duration_sec": result.duration_sec}, indent=2, default=str))
        else:
            print(f"MTR to {result.host} — {result.total_hops} hops, {result.duration_sec}s")
            print(f"{'Hop':>4} {'IP':<20} {'Loss%':>6} {'Snt':>4} {'Avg':>7} {'Best':>7} {'Wrst':>7} {'StDev':>7}")
            for h in result.hops:
                print(f"{h.hop:>4} {h.ip:<20} {h.loss_percent:>5.1f}% {h.sent:>4} {h.rtt_avg_ms:>6.1f}ms "
                      f"{h.rtt_min_ms:>6.1f}ms {h.rtt_max_ms:>6.1f}ms {h.rtt_stddev_ms:>6.1f}ms")

    elif args.command == "tracepath":
        result = nd.tracepath(args.host)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"tracepath to {result['host']}")
            print(f"  PMTU: {result.get('pmtu', 'unknown')}")
            for h in result.get("hops", []):
                print(f"  {h.get('hop', '?'):>3}  {h.get('ip', '???'):<20}  {h.get('rtt_ms', 0):.1f}ms")

    elif args.command == "netstat":
        entries = nd.netstat(listening=args.listening)
        if args.json:
            print(json.dumps([e.__dict__ for e in entries], indent=2, default=str))
        else:
            "LISTENING " if args.listening else ""
            print(f"{'Proto':<6} {'Local Address':<24} {'Remote Address':<24} {'State':<12} {'PID':>6} {'Process':<20}")
            for e in entries[:50]:
                print(f"{e.proto:<6} {e.local_addr}:{e.local_port:<24} {e.remote_addr}:{e.remote_port:<24} {e.state:<12} {e.pid or '':>6} {e.process_name:<20}")

    elif args.command == "lsof":
        result = nd.lsof(pid=args.pid, process_name=args.name)
        if args.json:
            print(json.dumps({str(k): [e.__dict__ for e in v] for k, v in result.items()}, indent=2, default=str))
        else:
            for pid, entries in result.items():
                print(f"PID {pid}: {len(entries)} open files/sockets")
                for e in entries[:10]:
                    print(f"  [{e.file_type}] {e.path}")

    elif args.command == "strace":
        result = nd.strace(args.command, args.args)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"strace: {args.command} {' '.join(args.args)}")
            for s in result.get("syscalls", []):
                print(f"  {s}")

    elif args.command == "ltrace":
        result = nd.ltrace(args.command, args.args)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"ltrace: {args.command} {' '.join(args.args)}")
            for lib in result.get("libraries", [])[:20]:
                print(f"  PID {lib['pid']}: {lib['path']}")

    elif args.command == "full":
        report = nd.run_full_diagnostics(args.host)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print(f"=== Full Network Diagnostics for {args.host} ===")
            for diag, data in report.items():
                if diag in ("target", "timestamp", "version"):
                    continue
                print(f"\n  [{diag.upper()}]")
                for k, v in data.items():
                    print(f"    {k}: {v}")

    else:
        # Default: show diagnostic summary
        print(f"NetworkDiag v{nd.VERSION}")
        print(f"  Platform: {sys.platform}")
        print("  Available commands: ping, traceroute, mtr, tracepath, netstat, lsof, strace, ltrace, full")


if __name__ == "__main__":
    main()
