"""Resumable read-only filesystem inventory scanner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


DEFAULT_EXCLUDED = {".git", "node_modules", "__pycache__", ".cache", ".mypy_cache", ".pytest_cache", ".venv", "venv", "build", "dist", "target", "site-packages"}


def _record(path: Path, root: Path, hash_limit: int) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    try:
        stat = path.stat()
        result: dict[str, Any] = {"path": relative, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "suffix": path.suffix.lower()}
        if stat.st_size <= hash_limit:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            result["sha256"] = digest.hexdigest()
        else:
            result["hash_status"] = "skipped_large"
        return result
    except (OSError, UnicodeError) as exc:
        note_failure("full_scan:L30", exc)
        return {"path": relative, "error": str(exc)}


def scan(root: Path | str, output_dir: Path | str, batch_size: int = 50000, batch_index: int | None = None, hash_limit: int = 16 * 1024 * 1024, excluded: set[str] | None = None) -> dict[str, Any]:
    """Scan a root in deterministic batches, preserving checkpoints."""
    root_path = Path(root).expanduser().resolve()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    excluded = excluded or DEFAULT_EXCLUDED
    index_path = output / "paths.jsonl"
    manifest_path = output / "manifest.jsonl"
    state_path = output / "state.json"
    if not index_path.exists():
        paths = []
        for path in root_path.rglob("*"):
            try:
                relative = path.relative_to(root_path)
                if any(part in excluded for part in relative.parts):
                    continue
                if path.is_file():
                    paths.append(relative.as_posix())
            except OSError as exc:
                note_failure("full_scan:L52", exc)
                continue
        paths.sort()
        index_path.write_text("\n".join(paths) + ("\n" if paths else ""), encoding="utf-8")
    all_paths = index_path.read_text(encoding="utf-8").splitlines()
    total_batches = (len(all_paths) + batch_size - 1) // batch_size
    state: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"completed_batches": [], "errors": []}
    pending = range(total_batches) if batch_index is None else [batch_index]
    with manifest_path.open("a", encoding="utf-8") as manifest:
        for current_batch in pending:
            if current_batch in state["completed_batches"]:
                continue
            start = current_batch * batch_size
            records = []
            for relative in all_paths[start : start + batch_size]:
                path = root_path / relative
                if path.is_file():
                    records.append(_record(path, root_path, hash_limit))
            for record in records:
                manifest.write(json.dumps(record, separators=(",", ":")) + "\n")
            state["completed_batches"].append(current_batch)
            state["completed_batches"] = sorted(set(state["completed_batches"]))
            state["root"] = str(root_path)
            state["total_paths"] = len(all_paths)
            state["total_batches"] = total_batches
            state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    complete = len(state["completed_batches"]) == total_batches
    if complete:
        (output / "COMPLETE.json").write_text(json.dumps({"root": str(root_path), "total_paths": len(all_paths), "total_batches": total_batches}, indent=2), encoding="utf-8")
    return {"root": str(root_path), "total_paths": len(all_paths), "total_batches": total_batches, "completed_batches": len(state["completed_batches"]), "complete": complete, "output": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Resumable read-only filesystem scanner")
    parser.add_argument("root")
    parser.add_argument("output")
    parser.add_argument("--batch-size", type=int, default=50000)
    parser.add_argument("--batch", type=int)
    args = parser.parse_args()
    print(json.dumps(scan(args.root, args.output, args.batch_size, args.batch), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
