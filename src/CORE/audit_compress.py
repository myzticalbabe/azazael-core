#!/usr/bin/env python3
"""audit_compress.py - utility functions (compress_session, archive_sessions).

Audit file compression and archival.
"""


__all__ = [
    "archive_sessions",
    "compress_session",
]

import gzip
import shutil
from pathlib import Path
from CORE.audit_trail import AUDIT_DIR
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

def compress_session(fp: Path) -> Path:
    gz_path = fp.with_suffix(".jsonl.gz")
    with fp.open("rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return gz_path

def archive_sessions(age_days: int = 30, dry_run: bool = True) -> list:
    archived = []
    for fp in sorted(AUDIT_DIR.glob("session_*.jsonl")):
        age = (__import__("time").time() - fp.stat().st_mtime) / 86400
        if age > age_days:
            if not dry_run:
                gz = compress_session(fp)
                fp.unlink()
                archived.append((str(fp), str(gz)))
            else:
                archived.append((str(fp), "would compress"))
    return archived

if __name__ == "__main__":
    import sys
    dry = "--execute" not in sys.argv
    results = archive_sessions(age_days=int(sys.argv[1]) if len(sys.argv)>1 and sys.argv[1].isdigit() else 30, dry_run=dry)
    for orig, dest in results:
        print(f"  {orig} -> {dest}")
    print(f"  Total: {len(results)} files {'(dry run)' if dry else 'compressed'}")
