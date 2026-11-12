"""Hash Verification — Compare file hashes between Ubuntu and G: archive.

Usage:  python3 verify_hashes.py
"""

import hashlib
import sys
from pathlib import Path

# This script prints U+2705/U+274C/U+26A0/U+1F4E6/U+1F3AF status glyphs. On a Windows console stdout defaults to
# cp1252, which cannot encode any of them, so the first emoji reached raised
# UnicodeEncodeError and killed the run before a verdict was ever produced --
# leaving an exit code of 1 that is indistinguishable from "hashes mismatched".
# Reconfiguring degrades an unencodable glyph to '?' instead of losing the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - exotic stdout
        pass

SRC = Path.home() / "Desktop" / "azazael-project-e"
DST = Path("/mnt/windows/g/AZAZAEL/azazael-project-e_backup")

EXCLUDE_DIRS = {"__pycache__", ".git", ".appdata", ".mypy_cache", ".pytest_cache", ".venv"}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_files(root):
    files = {}
    for f in root.rglob("*"):
        if f.is_file() and not any(e in str(f) for e in EXCLUDE_DIRS):
            rel = f.relative_to(root)
            files[str(rel)] = f
    return files


print("=" * 65)
print("  FILE HASH VERIFICATION — Ubuntu vs G: Archive")
print("=" * 65)

if not SRC.exists():
    print(f"\n[X] Source not found: {SRC}")
    sys.exit(1)

if not DST.exists():
    print(f"\n[X] Destination not found: {DST}")
    print("  Run: python3 backup_to_g.py")
    sys.exit(1)

src_files = scan_files(SRC)
dst_files = scan_files(DST)

matched = 0
mismatched = 0
missing_in_dst = 0
missing_in_src = 0

common = set(src_files.keys()) & set(dst_files.keys())
only_src = set(src_files.keys()) - set(dst_files.keys())
only_dst = set(dst_files.keys()) - set(src_files.keys())

print(f"\n  Source files:      {len(src_files)}")
print(f"  Destination files: {len(dst_files)}")
print(f"  Common:            {len(common)}")
print(f"  Only in source:    {len(only_src)}")
print(f"  Only in archive:   {len(only_dst)}")
print()

if only_src:
    print(f"  [!]  {len(only_src)} files in source not yet in archive:")
    for f in sorted(only_src)[:5]:
        print(f"      - {f}")
    if len(only_src) > 5:
        print(f"      ... and {len(only_src) - 5} more")

print("\n  Verifying hashes of common files...")
for rel in sorted(common):
    h1 = sha256_of(src_files[rel])
    h2 = sha256_of(dst_files[rel])
    if h1 == h2:
        matched += 1
    else:
        mismatched += 1
        if mismatched <= 3:
            print(f"    [X] MISMATCH: {rel}")

print(f"\n  {'='*50}")
print(f"  RESULTS")
print(f"  {'='*50}")
print(f"  [OK] Matched:      {matched}")
if mismatched:
    print(f"  [X] Mismatched:   {mismatched}")
if only_src:
    print(f"  [!]  Not archived: {len(only_src)}")
if only_dst:
    print(f"  [+] Extra in arc: {len(only_dst)}")

if mismatched == 0 and len(only_src) == 0:
    print(f"\n  [*] ARCHIVE IS COMPLETE AND INTACT")
elif mismatched == 0:
    print(f"\n  [!]  Archive intact but missing {len(only_src)} files — run backup_to_g.py")
else:
    print(f"\n  [!]  {mismatched} files have different content — check for corruption")
print()
