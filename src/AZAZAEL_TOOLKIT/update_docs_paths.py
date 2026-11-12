#!/usr/bin/env python3
"""
Documentation Updater - Updates all paths for C:\\Azazael migration
"""

from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

root = Path.home() / "Desktop" / "Azazael_UNIFIED"

patterns = [
    (Path.home() / "Desktop" / "Azazael_UNIFIED", "C:/Azazael"),
    ("/c/Users/clint/Desktop/Azazael_UNIFIED", "/c/Azazael"),
    ("~/Desktop/Azazael_UNIFIED", "~/Azazael"),
]

updated = []
errors = []

for md in list(root.rglob("*.md")) + list(root.rglob("*.txt")):
    if ".venv" in str(md) or "site-packages" in str(md):
        continue
    try:
        content = md.read_text(encoding="utf-8", errors="ignore")
        original = content
        for old, new in patterns:
            content = content.replace(old, new)
        if content != original:
            md.write_text(content, encoding="utf-8")
            updated.append(str(md.relative_to(root)))
    except Exception as e:
        note_failure("update_docs_paths:L30", e)
        errors.append((md.name, str(e)[:50]))

print(f"Updated {len(updated)} files:")
for f in updated:
    print(f"  {f}")

if errors:
    print(f"\nErrors ({len(errors)}):")
    for name, err in errors:
        print(f"  {name}: {err}")
