"""Build a compact digest of the markdown corpus for the semantic extraction pass.

Mechanical only: reads each doc and emits path, title, headings, and a short lead
so the LLM (the agent) can author nodes/edges/rationale without the raw corpus
being dumped into context verbatim.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Doc titles can contain non-ASCII and leading BOMs; on a cp1252 Windows console
# printing them raises UnicodeEncodeError. Make this script safe to run standalone.
if hasattr(sys.stdout, "reconfigure"):  # TextIOWrapper on all supported consoles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SKIP_DIRS = {
    ".git", "archived", "archive", "archived_unified", "backup", "graphify-out",
    "node_modules", "__pycache__", ".venv", "venv", ".agents", "skills_staging",
    "dist", "build", ".mypy_cache", ".pytest_cache", "output",
}
# Generated / vendored doc trees that are not project meaning.
SKIP_PREFIX = ("skills/", "skills.win-backup/", "graphs/")

ROOT = Path(".")
out_path = Path("graphify-out/_digest.json")
digest: list[dict] = []
skipped = 0

for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for fn in filenames:
        if not fn.endswith(".md"):
            continue
        p = Path(dirpath) / fn
        rel = p.as_posix().lstrip("./")
        if rel.startswith(SKIP_PREFIX):
            skipped += 1
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        words = len(text.split())
        # Title: first H1, else first non-empty line.
        title = ""
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("# "):
                title = s[2:].strip()
                break
        if not title:
            for line in text.splitlines():
                s = line.strip()
                if s and not s.startswith(("---", "```")):
                    title = s[:120]
                    break
        # Headings (structure = the concepts the doc declares).
        headings = [m.group(1).strip() for m in re.finditer(r"^#{2,3}\s+(.+)$", text, re.M)]
        # Lead: first ~2 prose paragraphs, stripped of fences/tables.
        lead_lines: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith(("```", "|", "---", "<!--", "#")):
                continue
            if s.startswith(("-", "*", ">", "1.", "2.", "3.")):
                continue
            lead_lines.append(s)
            if sum(len(x) for x in lead_lines) > 600:
                break
        digest.append({
            "path": rel,
            "words": words,
            "title": title[:160],
            "headings": headings[:14],
            "lead": " ".join(lead_lines)[:600],
        })

digest.sort(key=lambda d: (-d["words"], d["path"]))
out_path.write_text(json.dumps(digest, indent=1, ensure_ascii=False), encoding="utf-8")

tot_words = sum(d["words"] for d in digest)
print(f"digest docs={len(digest)} skipped(generated)={skipped} words={tot_words}")
print(f"wrote {out_path} ({out_path.stat().st_size/1024:.0f} KB)")
print("=== top 30 by size ===")
for d in digest[:30]:
    print(f"{d['words']:7d}  {d['path']}  | {d['title'][:60]}")
print("=== by top dir ===")
import collections
c = collections.Counter()
for d in digest:
    parts = d["path"].split("/")
    c[parts[0] if len(parts) > 1 else "(root)"] += 1
for k, v in c.most_common(20):
    print(f"{v:5d}  {k}")