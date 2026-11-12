"""Sync CODEX/ to G: Archive — Copy then SHA-256 verify.

Usage: python tools/sync_codex_to_g.py
"""

import hashlib
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "CODEX"
DST = Path("G:/AZAZAEL/AZAZAEL_ROOT/src/CODEX")

EXCLUDE_DIRS = {
    "__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".venv",
    "dist", "build", "OUTPUT", "videos", ".pytest_cache",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".prof"}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_files(root):
    files = {}
    for f in root.rglob("*"):
        if f.is_file():
            if any(e in f.parts for e in EXCLUDE_DIRS):
                continue
            if f.suffix in EXCLUDE_SUFFIXES:
                continue
            rel = f.relative_to(root)
            files[str(rel)] = f
    return files


# --- MAIN ---
print("=" * 65)
print("  CODEX/ SYNC TO G: ARCHIVE")
print(f"  Source:      {SRC}")
print(f"  Destination: {DST}")
print("=" * 65)

if not SRC.exists():
    print(f"\nERROR: Source not found: {SRC}")
    sys.exit(1)

DST.mkdir(parents=True, exist_ok=True)

src_files = scan_files(SRC)
print(f"\n  Source files: {len(src_files)}")

# --- COPY ---
print("\n  Copying files to G: archive...")
copied = 0
skipped = 0
errors = 0

for rel, src_path in sorted(src_files.items()):
    dst_path = DST / rel
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dst_path.exists():
            # Compare hashes before copying
            try:
                src_hash = sha256_of(src_path)
                dst_hash = sha256_of(dst_path)
                if src_hash == dst_hash:
                    skipped += 1
                    if skipped % 50 == 1 and skipped <= 51:
                        print(f"    [skip] {rel[:70]}")
                    continue
            except Exception:
                pass
        shutil.copy2(src_path, dst_path)
        copied += 1
        if copied % 20 == 1 and copied <= 21:
            print(f"    [copy] {rel[:70]}")
    except Exception as e:
        errors += 1
        print(f"    [ERROR] {rel[:70]}: {e}")

print(f"\n  Copied:  {copied}")
print(f"  Skipped: {skipped} (unchanged)")
if errors:
    print(f"  Errors:  {errors}")

# --- VERIFY ---
print("\n" + "=" * 65)
print("  SHA-256 VERIFICATION")
print("=" * 65)

dst_files = scan_files(DST)
print(f"\n  Archive files: {len(dst_files)}")

common = set(src_files.keys()) & set(dst_files.keys())
only_src = set(src_files.keys()) - set(dst_files.keys())
only_dst = set(dst_files.keys()) - set(src_files.keys())

print(f"  Common:         {len(common)}")
print(f"  Only in source: {len(only_src)}")
print(f"  Only in archive:{len(only_dst)}")

if only_src:
    print(f"\n  NOT ARCHIVED ({len(only_src)} files):")
    for f in sorted(only_src)[:10]:
        print(f"    - {f}")
    if len(only_src) > 10:
        print(f"    ... and {len(only_src) - 10} more")

print(f"\n  Verifying {len(common)} hashes...")
matched = 0
mismatched = 0

for rel in sorted(common):
    h1 = sha256_of(src_files[rel])
    h2 = sha256_of(dst_files[rel])
    if h1 == h2:
        matched += 1
    else:
        mismatched += 1
        if mismatched <= 5:
            print(f"    MISMATCH: {rel}")

print(f"\n  {'=' * 50}")
print(f"  VERIFICATION RESULTS")
print(f"  {'=' * 50}")
print(f"  Matched:    {matched}")
if mismatched:
    print(f"  Mismatched: {mismatched}")
if only_src:
    print(f"  Not synced: {len(only_src)}")
if only_dst:
    print(f"  Extra:      {len(only_dst)}")

if mismatched == 0 and len(only_src) == 0:
    print(f"\n  ARCHIVE COMPLETE — SHA-256 VERIFIED")
elif mismatched == 0:
    print(f"\n  Archive intact — {len(only_src)} files need syncing (re-run)")
else:
    print(f"\n  {mismatched} FILES CORRUPTED OR OUT OF DATE — re-sync needed")

print()
