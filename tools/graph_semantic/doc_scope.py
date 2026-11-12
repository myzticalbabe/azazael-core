"""Full doc-file scope: what exists, what we exclude, and why."""
from __future__ import annotations

import collections
import json
import os
from pathlib import Path

# Classes we exclude from the doc graph, each with a reason. Archive/backup/vendor
# content is not the project's documentation; generated output is not source.
EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "graphify-out", "dist", "build", ".agents",
    "skills_staging", "output", "graphs", ".ruff_cache", ".tox", "site-packages",
}
ARCHIVE_DIRS = {"archived", "archive", "archived_unified", "backup", "skills.win-backup", "target"}

included: list[tuple[str, int]] = []
archive: list[str] = []
generated_skill_mirror = 0
total = 0

for dirpath, dirnames, filenames in os.walk("."):
    rel_dir = Path(dirpath).as_posix().lstrip("./")
    parts = [p for p in rel_dir.split("/") if p and p != "."]
    dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
    hit_archive = any(p in ARCHIVE_DIRS for p in parts)
    for fn in filenames:
        if not fn.endswith(".md"):
            continue
        total += 1
        p = (Path(dirpath) / fn).as_posix().lstrip("./")
        if hit_archive:
            archive.append(p)
            continue
        try:
            words = len((Path(dirpath) / fn).read_text(encoding="utf-8", errors="ignore").split())
        except OSError:
            words = 0
        included.append((p, words))

print(f"total .md seen (excl. build/cache dirs): {total}")
print(f"excluded as archive/backup/vendor/target: {len(archive)}")
print(f"INCLUDED project docs: {len(included)}  words={sum(w for _, w in included)}")

buckets = collections.Counter()
for p, _ in included:
    parts = p.split("/")
    if len(parts) == 1:
        b = "(root)"
    elif parts[0] == "skills":
        # family by prefix of the skill dir name
        fam = parts[1].split("-")[0] if len(parts) > 1 else "?"
        b = f"skills/{fam}"
    elif parts[0] == "src":
        b = "src/" + (parts[1] if len(parts) > 2 else "(file)")
    else:
        b = parts[0]
    buckets[b] += 1
print("\n=== bucket distribution ===")
for k, v in buckets.most_common(40):
    print(f"{v:5d}  {k}")

# How many included docs currently have NO node in graph.json?
g = json.loads(Path("graphify-out/graph.json").read_text(encoding="utf-8"))
have_sf = {n.get("source_file") for n in g["nodes"]}
missing = [p for p, _ in included if p not in have_sf]
print(f"\nproject docs with NO node in graph now: {len(missing)} of {len(included)}")
print("sample:", missing[:10])
Path("graphify-out/_doc_scope.json").write_text(json.dumps({
    "included": [p for p, _ in included],
    "excluded_archive": archive,
}, indent=0), encoding="utf-8")