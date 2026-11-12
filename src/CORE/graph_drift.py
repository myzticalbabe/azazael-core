"""CI-friendly graph snapshot drift checking."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, List

from CORE.codebase_graph import build_graph

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def compare_snapshot(root: Path | str, snapshot_path: Path | str, include_tests: bool = False) -> dict[str, Any]:
    """Build a current graph and compare it with a saved JSON snapshot."""
    previous = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    graph = build_graph(root, include_tests=include_tests)
    diff = graph.snapshot_diff(previous)
    return {"drift": bool(any(diff.values())), "diff": diff, "current": graph.to_dict()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check codebase graph snapshot drift")
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--include-tests", action="store_true")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    result = compare_snapshot(args.root, args.snapshot, args.include_tests)
    if args.update:
        Path(args.snapshot).write_text(json.dumps(result["current"], indent=2, sort_keys=True), encoding="utf-8")
        print("graph snapshot updated")
        return 0
    print(json.dumps(result["diff"], indent=2, sort_keys=True))
    return 1 if result["drift"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
