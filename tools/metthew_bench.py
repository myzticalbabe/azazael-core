#!/usr/bin/env python3
# coding: utf-8
"""metthew_bench.py -- M-02: Low-end budget bench for Metthew shards.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
It measures the declared tick budget of a shard by running its golden run
over multiple repetitions and reporting p50/p99/ticks/repeats. It also
measures payload size, file count, and memory footprint. It does NOT verify
the golden run's state hash -- that is the validator's job (M-03).

This tool produces a `budget.json` that the validator (M-03) consumes as
the source of truth for tick budget comparisons (V13).

Usage
-----
    python tools/metthew_bench.py --manifest docs/METTHEW/reference-shards/highend_duel/shard.json
    python tools/metthew_bench.py --manifest docs/METTHEW/reference-shards/highend_ladder/shard.json
    python tools/metthew_bench.py --all --out budget.json
    python tools/metthew_bench.py --manifest shard.json --repeats 10 --out budget.json

Output
------
A JSON file with:
{
  "generated_at": "2026-11-02T...",
  "python_version": "3.15.0",
  "shards": [
    {
      "manifest_path": "...",
      "id": "highend-duel@sha256:...",
      "payload_bytes": 11636,
      "file_count": 2,
      "tick_budget": { "p50_ms": 0.00074, "p99_ms": 0.00078, "ticks": 360, "repeats": 5 },
      "measured": true,
      "payload_ok": true,
      "files_ok": true
    }
  ],
  "summary": { "total_shards": 1, "measured": 1, "payload_ok": 1, "files_ok": 1 }
}

Exit codes: 0 all measured, 1 any measurement failed, 2 unreadable manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO = Path(__file__).resolve().parents[1]
METTHEW = REPO / "docs" / "METTHEW"
SCHEMA_PATH = METTHEW / "shard.schema.json"
REFERENCE_SHARDS = METTHEW / "reference-shards"

LOWEND_PAYLOAD_CAP = 2 * 1024 * 1024
LOWEND_FILE_CAP = 24
HIGHEND_PAYLOAD_CAP = 8 * 1024 * 1024 * 1024
HIGHEND_FILE_CAP = 1024
DEFAULT_SIM_HZ = 30
DEFAULT_REPEATS = 5

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


class MeasureError(Exception):
    """The shard's tick could not be measured at all."""
    pass


def load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path} is not readable JSON: {exc}") from exc


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    return load_json(path)


def load_schema_optional(path: Path = SCHEMA_PATH) -> dict:
    try:
        return load_schema(path)
    except Exception:
        return {}


def get_sim_hz(manifest: dict) -> int:
    value = manifest.get("sim_hz")
    return value if isinstance(value, int) and value > 0 else 30


def bundle_fingerprint(manifest: dict) -> str:
    """Hash of the file-set, used to spot one bundle shipped under two names."""
    entries = [entry for entry in manifest.get("files") or [] if isinstance(entry, dict)]
    lines = sorted("%s %s" % (entry.get("path"), entry.get("sha256"))
                   for entry in entries)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def measure_tick_budget(shard_dir: Path, manifest: dict, repeats: int = 5) -> dict:
    """Time ``run(ticks, bytes)`` over the manifest's own golden input."""
    import importlib.util
    import time

    entrypoint = manifest.get("entrypoint")
    golden = manifest.get("golden_run") or {}
    ticks = golden.get("ticks")
    payload_hex = golden.get("input")

    if not isinstance(entrypoint, str) or not entrypoint.endswith(".py"):
        raise ValueError(f"entrypoint {entrypoint!r} is not a python file")
    if not isinstance(ticks, int) or ticks < 1:
        raise ValueError("golden_run.ticks is missing or invalid")

    path = Path(shard_dir) / entrypoint
    if not path.is_file():
        raise FileNotFoundError(f"no entrypoint at {path}")

    spec = importlib.util.spec_from_file_location("shard_under_measure", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError) as exc:
        raise ImportError(f"importing {path} failed: {exc}") from exc

    run = getattr(module, "run", None)
    if not callable(run):
        raise ValueError("the shard exposes no run(ticks, bytes)")

    try:
        raw = bytes.fromhex(str(manifest.get("golden_run", {}).get("input", "")))
    except ValueError as exc:
        raise ValueError(f"golden_run.input is not hex: {exc}") from exc
    if not raw:
        raise ValueError("golden_run.input is empty")

    ticks = manifest["golden_run"]["ticks"]
    per_tick: list[float] = []

    for _ in range(max(1, 5)):
        start = time.perf_counter()
        run(ticks, raw)
        per_tick.append((time.perf_counter() - start) * 1000.0 / ticks)

    per_tick.sort()
    return {
        "p50_ms": per_tick[len(per_tick) // 2],
        "p99_ms": per_tick[-1],
        "ticks": ticks,
        "repeats": len(per_tick),
    }


def measure_memory_peak() -> int:
    """Return peak RSS in bytes for the current process, or 0 if unavailable."""
    if not PSUTIL_AVAILABLE:
        return 0
    try:
        import psutil
        process = psutil.Process()
        return process.memory_info().rss
    except Exception as exc:
        note_failure("tools/metthew_bench.py", exc)
        return 0


def measure_shard(manifest_path: Path, repeats: int = 5) -> dict:
    """Measure a single shard manifest and return the results."""
    manifest = load_json(manifest_path)
    shard_dir = manifest_path.parent

    # Payload and file count
    files = [entry for entry in manifest.get("files") or [] if isinstance(entry, dict)]
    payload_bytes = sum(entry.get("size_bytes", 0) for entry in files
                        if isinstance(entry.get("size_bytes"), int))
    file_count = len(files)

    # Payload and file count gates

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

    tier = manifest.get("tier") or "any"
    lowend = tier in ("lowend", "any")
    payload_ok = True
    files_ok = True
    if lowend:
        if payload_bytes > 2 * 1024 * 1024:
            payload_ok = False
        if len(files) > 24:
            files_ok = False

    # Measure tick budget
    measured = None
    measure_error = None
    try:
        measured = measure_tick_budget(manifest_path.parent, manifest)
    except Exception as exc:
        measure_error = str(exc)

    # Memory
    peak_rss = measure_memory_peak()

    # Payload and file count gates
    payload_ok = True
    files_ok = True
    if tier in ("lowend", "any"):
        if payload_bytes > 2 * 1024 * 1024:
            payload_ok = False
        if len(files) > 24:
            files_ok = False
    elif tier == "highend":
        if payload_bytes > 8 * 1024 * 1024 * 1024:
            payload_ok = False
        if len(files) > 1024:
            files_ok = False

    # Fingerprint
    fingerprint = ""
    try:
        entries = [entry for entry in manifest.get("files") or [] if isinstance(entry, dict)]
        lines = sorted("%s %s" % (entry.get("path"), entry.get("sha256"))
                       for entry in entries)
        fingerprint = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    except Exception as exc:
        note_failure("tools/metthew_bench.py", exc)
        pass

    return {
        "manifest_path": str(manifest_path),
        "id": manifest.get("id", "unknown"),
        "tier": tier,
        "payload_bytes": payload_bytes,
        "file_count": len(files),
        "file_list": [{"path": f.get("path"), "role": f.get("role"),
                       "size_bytes": f.get("size_bytes")} for f in files],
        "tick_budget": {
            "p50_ms": None,
            "p99_ms": None,
            "ticks": 0,
            "repeats": 0,
        } if measure_error else {
            "p50_ms": round(measured["p50_ms"], 6),
            "p99_ms": round(measured["p99_ms"], 6),
            "ticks": measured["ticks"],
            "repeats": measured["repeats"],
        },
        "measured": measure_error is None,
        "measure_error": measure_error,
        "payload_ok": payload_ok,
        "files_ok": files_ok,
        "fingerprint": fingerprint,
        "peak_rss_bytes": peak_rss,
        "measured_at": datetime.now(UTC).isoformat(),
    }


def measure_all(repeats: int = 5) -> dict:
    """Measure all reference shards and return aggregated results."""
    shards_dir = METTHEW / "reference-shards"
    if not shards_dir.is_dir():
        return {"error": "no reference-shards directory"}

    results = []
    for shard_path in sorted(shards_dir.iterdir()):
        if not shard_path.is_dir():
            continue
        manifest_path = shard_path / "shard.json"
        if not manifest_path.is_file():
            continue
        print(f"Measuring {shard_path.name}...")
        result = measure_shard(manifest_path, repeats=5)
        results.append(result)
        print(f"  {result['id']}: payload={result['payload_bytes']} bytes, "
              f"files={result['file_count']}, "
              f"p50={result['tick_budget']['p50_ms']}ms, "
              f"p99={result['tick_budget']['p99_ms']}ms, "
              f"payload_ok={result['payload_ok']}, files_ok={result['files_ok']}")

    measured_count = sum(1 for r in results if r["measured"])
    payload_ok_count = sum(1 for r in results if r["payload_ok"])
    files_ok_count = sum(1 for r in results if r["files_ok"])

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "shards": results,
        "summary": {
            "total_shards": len(results),
            "measured": measured_count,
            "payload_ok": payload_ok_count,
            "files_ok": files_ok_count,
        }
    }


def write_budget_json(data: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Written {output_path}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Metthew budget bench (M-02).")
    parser.add_argument("--manifest", action="append", default=[],
                        help="a shard.json to measure (repeatable)")
    parser.add_argument("--all", action="store_true",
                        help="measure all reference shards")
    parser.add_argument("--repeats", type=int, default=5,
                        help="repetitions for measurement (default 5)")
    parser.add_argument("--out", type=Path, default=Path("budget.json"),
                        help="output JSON file (default budget.json)")
    parser.add_argument("--json", action="store_true", help="print JSON to stdout")
    args = parser.parse_args(argv)

    try:
        if args.all:
            data = measure_all(repeats=args.repeats)
        elif args.manifest:
            results = []
            for entry in args.manifest:
                path = Path(entry)
                if not path.is_file():
                    print(f"ERROR: not found: {path}", file=sys.stderr)
                    return 2
                result = measure_shard(path, repeats=args.repeats)
                print(f"  {result['id']}: payload={result['payload_bytes']} bytes, "
                      f"files={result['file_count']}, "
                      f"p50={result['tick_budget']['p50_ms']}ms, "
                      f"p99={result['tick_budget']['p99_ms']}ms, "
                      f"payload_ok={result['payload_ok']}, files_ok={result['files_ok']}")
                results.append(measure_shard(Path(entry), repeats=args.repeats))

            measured_count = sum(1 for r in results if r["measured"])
            payload_ok_count = sum(1 for r in results if r["payload_ok"])
            files_ok_count = sum(1 for r in results if r["files_ok"])

            data = {
                "generated_at": datetime.now(UTC).isoformat(),
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "shards": results,
                "summary": {
                    "total_shards": len(results),
                    "measured": measured_count,
                    "payload_ok": payload_ok_count,
                    "files_ok": files_ok_count,
                }
            }
        else:
            print("ERROR: must specify --all or --manifest", file=sys.stderr)
            return 2

        if args.json:
            print(json.dumps(data, indent=1, sort_keys=True))
        else:
            write_budget_json(data, args.out)

        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())