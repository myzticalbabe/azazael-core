# coding: utf-8
"""
DISKZ - Disk Management Worm

Disk monitoring and management for Azazael ecosystem.

Features:
- Disk usage monitoring
- Partition analysis
- I/O statistics
- Directory size analysis
- Large file finder
- Disk health check

Usage:
    >>> import diskz
    >>> diskz.get_usage()          # Get disk usage
    >>> diskz.get_partitions()      # Get partition info
    >>> diskz.get_io_stats()        # Get I/O statistics
    >>> diskz.analyze_directory("/path") # Analyze directory
    >>> diskz.find_large_files(100) # Find files > 100MB
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional
from dataclasses import dataclass
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm  # pyright: ignore[reportAssignmentType] -- optional import: except-path defines fallback decorator; selected by import success
except ImportError as exc:
    note_failure("DISKZ:L32", exc)
    register_worm = lambda **kw: (lambda cls: cls)  # no-op fallback


@dataclass
class DiskUsage:
    """Disk usage information."""

    device: str
    mountpoint: str
    total_gb: float
    used_gb: float
    free_gb: float
    percent: float


@register_worm(
    category="monitor",
    priority=5,
    cooldown_seconds=30.0,
    description="Disk I/O and space monitoring worm",
    tags={"system", "disk", "storage"},
)
class DISKZ:
    """Disk Management Worm."""

    VERSION = "1.0.0"

    def __init__(self):
        pass

    def get_usage(self, path: str = "/") -> DiskUsage:
        """Get disk usage for path."""
        import psutil
        usage = psutil.disk_usage(path)
        return DiskUsage(
            device="/",
            mountpoint=path,
            total_gb=round(usage.total / (1024**3), 2),
            used_gb=round(usage.used / (1024**3), 2),
            free_gb=round(usage.free / (1024**3), 2),
            percent=usage.percent,
        )

    def get_partitions(self) -> list[dict[str, Any]]:
        """Get all partitions."""
        import psutil
        partitions = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                partitions.append(
                    {
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "fstype": part.fstype,
                        "total_gb": round(usage.total / (1024**3), 2),
                        "used_gb": round(usage.used / (1024**3), 2),
                        "free_gb": round(usage.free / (1024**3), 2),
                        "percent": usage.percent,
                    }
                )
            except (PermissionError, OSError) as exc:
                note_failure("DISKZ:L94", exc)
                partitions.append(
                    {
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "fstype": part.fstype,
                        "error": "Unable to get usage",
                    }
                )
        return partitions

    def get_io_stats(self) -> dict[str, Any]:
        """Get disk I/O statistics."""
        import psutil
        io = psutil.disk_io_counters()
        if io is None:
            return {}
        return {
            "read_mb": round(io.read_bytes / (1024**2), 2),
            "write_mb": round(io.write_bytes / (1024**2), 2),
            "read_count": io.read_count,
            "write_count": io.write_count,
            "read_time_ms": io.read_time,
            "write_time_ms": io.write_time,
        }

    def get_io_counters(self, perdisk: bool = True) -> dict[str, dict[str, Any]]:
        """Get per-disk I/O counters."""
        import psutil
        counters = psutil.disk_io_counters(perdisk=perdisk)
        if not counters:
            return {}
        result = {}
        for disk, io in counters.items():  # pyright: ignore[reportAttributeAccessIssue]
            if io is None:
                continue
            result[disk] = {
                "read_mb": round(io.read_bytes / (1024**2), 2),
                "write_mb": round(io.write_bytes / (1024**2), 2),
                "read_count": io.read_count,
                "write_count": io.write_count,
            }
        return result

    def analyze_directory(
        self, path: str, max_depth: int = 3, min_size_mb: int = 1
    ) -> dict[str, Any]:
        """Analyze directory for space usage."""
        base = Path(path)
        if not base.exists():
            return {"error": "Path does not exist"}

        directories = {}
        files = []

        def scan_directory(p: Path, depth: int):
            if depth > max_depth:
                return
            try:
                for item in p.iterdir():
                    if item.is_file():
                        size = item.stat().st_size
                        if size >= min_size_mb * 1024 * 1024:
                            files.append(
                                {
                                    "path": str(item),
                                    "size_mb": round(size / (1024**2), 2),
                                    "modified": datetime.fromtimestamp(
                                        item.stat().st_mtime
                                    ).isoformat(),
                                }
                            )
                    elif item.is_dir():
                        try:
                            total = sum(
                                f.stat().st_size for f in item.rglob("*") if f.is_file()
                            )
                            directories[str(item)] = round(total / (1024**2), 2)
                            scan_directory(item, depth + 1)
                        except (PermissionError, OSError) as exc:
                            note_failure("DISKZ:L167", exc)
            except PermissionError as exc:
                note_failure("DISKZ:L169", exc)

        scan_directory(base, 0)

        return {
            "path": str(base),
            "total_directories": len(directories),
            "large_files": sorted(files, key=lambda x: x["size_mb"], reverse=True)[:50],
            "directories": dict(
                sorted(directories.items(), key=lambda x: x[1], reverse=True)[:20]
            ),
        }

    def find_large_files(
        self, min_size_mb: int = 100, paths: Optional[list[str]] = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Find large files across paths."""
        if paths is None:
            paths = ["/c/Users/clint/Desktop", "/c"]

        large_files = []
        for base_path in paths:
            base = Path(base_path)
            if not base.exists():
                continue
            try:
                for item in base.rglob("*"):
                    if item.is_file():
                        try:
                            size = item.stat().st_size
                            if size >= min_size_mb * 1024 * 1024:
                                large_files.append(
                                    {
                                        "path": str(item),
                                        "size_mb": round(size / (1024**2), 2),
                                        "modified": datetime.fromtimestamp(
                                            item.stat().st_mtime
                                        ).isoformat(),
                                    }
                                )
                        except (OSError, PermissionError) as exc:
                            note_failure("DISKZ:L210", exc)
            except PermissionError as exc:
                note_failure("DISKZ:L212", exc)
                continue

        large_files.sort(key=lambda x: x["size_mb"], reverse=True)
        return large_files[:limit]

    def get_summary(self) -> dict[str, Any]:
        """Get disk summary."""
        usage = self.get_usage()
        return {
            "root": {
                "total_gb": usage.total_gb,
                "used_gb": usage.used_gb,
                "free_gb": usage.free_gb,
                "percent": usage.percent,
            },
            "io_stats": self.get_io_stats(),
            "partitions": self.get_partitions(),
        }


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(description="DISKZ - Disk Management Worm")
    parser.add_argument("--usage", action="store_true", help="Disk usage")
    parser.add_argument("--partitions", action="store_true", help="list partitions")
    parser.add_argument("--io", action="store_true", help="I/O statistics")
    parser.add_argument("--analyze", type=str, help="Analyze directory")
    parser.add_argument("--find-large", type=int, help="Find files > N MB", metavar="N")
    parser.add_argument("--summary", action="store_true", help="Summary")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    dz = DISKZ()

    if args.json:
        if args.usage:
            usage = dz.get_usage()
            print(json.dumps(usage.__dict__, indent=2))
        elif args.partitions:
            print(json.dumps(dz.get_partitions(), indent=2))
        elif args.io:
            print(json.dumps(dz.get_io_stats(), indent=2))
        elif args.analyze:
            print(json.dumps(dz.analyze_directory(args.analyze), indent=2, default=str))
        elif args.find_large:
            print(
                json.dumps(dz.find_large_files(args.find_large), indent=2, default=str)
            )
        elif args.summary:
            print(json.dumps(dz.get_summary(), indent=2))
        else:
            print(json.dumps(dz.get_usage().__dict__, indent=2))
    else:
        if args.usage:
            u = dz.get_usage()
            print(f"Disk Usage: {u.mountpoint}")
            print(f"  Total: {u.total_gb}GB")
            print(f"  Used:  {u.used_gb}GB ({u.percent}%)")
            print(f"  Free:  {u.free_gb}GB")
        elif args.partitions:
            print("Partitions:")
            for p in dz.get_partitions():
                print(
                    f"  {p['device']} -> {p['mountpoint']} ({p.get('percent', 'N/A')}%)"
                )
        elif args.io:
            io = dz.get_io_stats()
            print("I/O Statistics")
            print(f"  Read:  {io['read_mb']}MB ({io['read_count']} ops)")
            print(f"  Write: {io['write_mb']}MB ({io['write_count']} ops)")
        elif args.analyze:
            result = dz.analyze_directory(args.analyze)
            print(f"Directory Analysis: {result['path']}")
            print(f"  Directories: {result['total_directories']}")
            print(f"  Large files found: {len(result['large_files'])}")
        elif args.find_large:
            files = dz.find_large_files(args.find_large)
            print(f"Files > {args.find_large}MB:")
            for f in files[:20]:
                print(f"  {f['size_mb']:>8}MB - {f['path'][:60]}...")
        else:
            u = dz.get_usage()
            print(f"Disk: {u.used_gb}GB / {u.total_gb}GB ({u.percent}%)")


if __name__ == "__main__":
    main()
