# coding: utf-8
"""
NETWORXZ - Network Management Worm

Network monitoring and management for Azazael ecosystem.

Features:
- Network interface listing
- Connection monitoring
- Bandwidth statistics
- Ping monitoring
- Port checking
- DNS resolution

Usage:
    >>> import networxz
    >>> networxz.get_interfaces()     # List interfaces
    >>> networxz.get_connections()     # List connections
    >>> networxz.get_stats()          # Get bandwidth stats
    >>> networxz.ping("google.com")   # Ping host
"""

import json
import socket
from typing import Any, List
from dataclasses import dataclass
import argparse
import subprocess

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("NETWORXZ:L30", exc)
    register_worm = lambda **kw: (lambda cls: cls)  # no-op fallback


def _format_socket_address(addr: Any) -> str:
    """Format a psutil connection address for the connections table.

    INET sockets carry an (ip, port) tuple; UNIX sockets a path string; an
    empty value means the kernel reported nothing. Same hasattr/str/empty
    ladder previously inlined at both call sites.
    """
    if not addr:
        return ""
    if hasattr(addr, "ip"):
        return f"{addr.ip}:{addr.port}"
    return str(addr)


@dataclass
class NetworkInterface:
    """Network interface information."""

    name: str
    address: str
    netmask: str
    broadcast: str
    is_up: bool
    speed: (int | None)


@register_worm(
    category="monitor",
    priority=7,
    cooldown_seconds=15.0,
    description="Network interface and traffic monitoring worm",
    tags={"network", "traffic", "connectivity"},
)
class NETWORXZ:
    """Network Management Worm."""

    VERSION = "1.0.0"

    def __init__(self):
        pass

    def get_interfaces(self) -> list[dict[str, Any]]:
        """Get network interfaces."""
        import psutil
        interfaces = []
        for name, addrs in psutil.net_if_addrs().items():
            info = {
                "name": name,
                "addresses": [],
                "is_up": name in psutil.net_if_stats(),
            }
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    info["addresses"].append(
                        {
                            "address": addr.address,
                            "netmask": addr.netmask,
                            "broadcast": addr.broadcast,
                        }
                    )
            if info["addresses"]:
                interfaces.append(info)
        return interfaces

    def get_connections(self, kind: str = "all") -> list[dict[str, Any]]:
        """Get network connections."""
        import psutil
        conns = []
        for conn in psutil.net_connections(kind=kind):
            conns.append(
                {
                    "fd": conn.fd,
                    "family": conn.family.name
                    if hasattr(conn.family, "name")
                    else str(conn.family),
                    "type": conn.type.name
                    if hasattr(conn.type, "name")
                    else str(conn.type),
                    "local_addr": _format_socket_address(conn.laddr),
                    "remote_addr": _format_socket_address(conn.raddr),
                    "status": conn.status,
                    "pid": conn.pid,
                }
            )
        return conns

    def get_stats(self) -> dict[str, Any]:
        """Get network statistics."""
        import psutil
        counters = psutil.net_io_counters()
        return {
            "bytes_sent_mb": round(counters.bytes_sent / (1024**2), 2),
            "bytes_recv_mb": round(counters.bytes_recv / (1024**2), 2),
            "packets_sent": counters.packets_sent,
            "packets_recv": counters.packets_recv,
            "errin": counters.errin,
            "errout": counters.errout,
            "dropin": counters.dropin,
            "dropout": counters.dropout,
        }

    def ping(self, host: str, count: int = 1, timeout: int = 2) -> dict[str, Any]:
        """Ping a host."""

        try:
            result = subprocess.run(
                ["ping", "-n", str(count), "-w", str(timeout * 1000), host],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout * count + 5,
            )
            return {
                "host": host,
                "success": result.returncode == 0,
                "output": result.stdout[:500],
                "error": result.stderr[:500] if result.stderr else None,
            }
        except Exception as e:
            note_failure("NETWORXZ:L144", e)
            return {"host": host, "success": False, "error": str(e)}

    def check_port(self, host: str, port: int, timeout: int = 2) -> dict[str, Any]:
        """Check if port is open."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            result = sock.connect_ex((host, port))
            return {"host": host, "port": port, "open": result == 0}
        except Exception as e:
            note_failure("NETWORXZ:L154", e)
            return {"host": host, "port": port, "open": False, "error": str(e)}
        finally:
            sock.close()

    def resolve(self, hostname: str) -> dict[str, Any]:
        """Resolve hostname to IP."""
        try:
            ip = socket.gethostbyname(hostname)
            return {"hostname": hostname, "ip": ip, "success": True}
        except socket.gaierror as e:
            note_failure("NETWORXZ:L164", e)
            return {"hostname": hostname, "success": False, "error": str(e)}

    def get_summary(self) -> dict[str, Any]:
        """Get network summary."""
        return {
            "interfaces": self.get_interfaces(),
            "connections_count": len(self.get_connections()),
            "stats": self.get_stats(),
        }


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(description="NETWORXZ - Network Management Worm")
    parser.add_argument("--interfaces", action="store_true", help="list interfaces")
    parser.add_argument("--connections", action="store_true", help="list connections")
    parser.add_argument("--stats", action="store_true", help="Get bandwidth stats")
    parser.add_argument("--ping", type=str, help="Ping host")
    parser.add_argument("--port", type=int, help="Check port (requires --host)")
    parser.add_argument("--resolve", type=str, help="Resolve hostname")
    parser.add_argument("--summary", action="store_true", help="Summary")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    nz = NETWORXZ()

    if args.json:
        if args.interfaces:
            print(json.dumps(nz.get_interfaces(), indent=2))
        elif args.connections:
            print(json.dumps(nz.get_connections(), indent=2))
        elif args.stats:
            print(json.dumps(nz.get_stats(), indent=2))
        elif args.ping:
            print(json.dumps(nz.ping(args.ping), indent=2))
        elif args.port and args.host:
            print(json.dumps(nz.check_port(args.host, args.port), indent=2))
        elif args.resolve:
            print(json.dumps(nz.resolve(args.resolve), indent=2))
        elif args.summary:
            print(json.dumps(nz.get_summary(), indent=2))
        else:
            print(json.dumps(nz.get_summary(), indent=2))
    else:
        if args.interfaces:
            for iface in nz.get_interfaces():
                print(
                    f"{iface['name']}: {', '.join(a['address'] for a in iface['addresses'])}"
                )
        elif args.connections:
            for c in nz.get_connections()[:20]:
                print(f"{c['local_addr']} -> {c['remote_addr']} [{c['status']}]")
        elif args.stats:
            s = nz.get_stats()
            print(f"Sent: {s['bytes_sent_mb']}MB | Recv: {s['bytes_recv_mb']}MB")
        elif args.ping:
            r = nz.ping(args.ping)
            print(f"{args.ping}: {'OK' if r['success'] else 'FAILED'}")
        elif args.port and args.host:
            r = nz.check_port(args.host, args.port)
            print(f"{args.host}:{args.port}: {'OPEN' if r['open'] else 'CLOSED'}")
        elif args.resolve:
            r = nz.resolve(args.resolve)
            print(f"{args.resolve}: {r.get('ip', r.get('error', 'UNKNOWN'))}")
        else:
            s = nz.get_stats()
            print(
                f"Connections: {s['connections_count']} | Sent: {s['stats']['bytes_sent_mb']}MB"
            )


if __name__ == "__main__":
    main()
