#!/usr/bin/env python3
"""
BIBL Crawler - Terminology Index Builder for the Azazael Ecosystem

Parses BIBL lexical databases (.md/.txt files in ORCHESTRATION/BIBL/)
and builds a searchable, cross-referenced terminology index.

Usage:
    python bibl_crawler.py --index          # Rebuild index from all BIBL files
    python bibl_crawler.py --search "term"  # Full-text search
    python bibl_crawler.py --term TERM      # Look up specific term
    python bibl_crawler.py --category cat   # List terms by category
    python bibl_crawler.py --stats          # Show index statistics
    python bibl_crawler.py --export json    # Export index as JSON
"""

import os
import re
import json
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: Canonical root resolver. `AZAZAEL_ROOT` below used to fall back to
#: `Path.home() / "azazael"` -- a DIFFERENT, stale checkout from this repo -- so with
#: the env vars unset the index was built against the wrong tree. The env chain is
#: kept verbatim, including the `Azazael_ROOT` variant; only the last fallback moved.
try:
    from CORE.paths import get_project_root_safe as _get_project_root_safe
except ImportError as exc:                               # never break the host
    note_failure("bibl_crawler:core-paths-import", exc)

    def _get_project_root_safe() -> Path:
        # CORE unreachable: derive from this file (src/ORCHESTRATION/BIBL/ -> root).
        return Path(__file__).resolve().parents[3]

# Configuration
AZAZAEL_ROOT = os.environ.get(
    "AZAZAEL_ROOT",
    os.environ.get("Azazael_ROOT", str(_get_project_root_safe()))
)
BIBL_DIRS = [
    Path(AZAZAEL_ROOT) / "src" / "ORCHESTRATION" / "BIBL",
    Path(AZAZAEL_ROOT) / "src" / "UTILITIES" / "BIBL",
]
OUTPUT_DIR = Path(AZAZAEL_ROOT) / "output" / "bibl"
INDEX_FILE = OUTPUT_DIR / "bibl_index.json"


def parse_yaml_header(content):
    """Extract YAML front matter from a BIBL file."""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            header = {}
            yaml_block = content[3:end].strip()
            for line in yaml_block.split("\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    header[key.strip()] = val.strip()
            return header, content[end + 3:]
    return {}, content


def parse_entries(content):
    """Parse H2 entries with Category, Section, Source fields."""
    entries = []
    # Split on ## headers
    sections = re.split(r"\n##\s+", content)
    for section in sections[1:]:  # Skip content before first H2
        lines = section.strip().split("\n")
        if not lines:
            continue
        name = lines[0].strip()
        entry = {"name": name, "category": "", "section": "", "source": ""}

        for line in lines[1:]:
            # Category
            cat_match = re.search(r"\*\*Category:\*\*\s*(.+)", line)
            if cat_match:
                entry["category"] = cat_match.group(1).strip()
            # Section
            sec_match = re.search(r"\*\*Section:\*\*\s*(.+)", line)
            if sec_match:
                entry["section"] = sec_match.group(1).strip()
            # Source
            src_match = re.search(r"\*Source:\s*(.+)", line)
            if src_match:
                entry["source"] = src_match.group(1).strip()
            # Stop at next blank line or new header
            if line.strip() == "" and entry["category"] and entry["section"]:
                break

        if entry["name"]:
            entries.append(entry)
    return entries


def crawl_all():
    """Crawl all BIBL directories and build unified index."""
    all_entries = []
    file_metadata = {}

    for bibl_dir in BIBL_DIRS:
        if not bibl_dir.exists():
            continue
        for filepath in sorted(bibl_dir.glob("*")):
            if filepath.suffix not in (".md", ".txt", ".html"):
                continue
            if filepath.name.startswith("INDEX"):
                continue
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
                header, body = parse_yaml_header(content)
                entries = parse_entries(body)

                file_metadata[str(filepath)] = {
                    "title": header.get("title", filepath.stem),
                    "version": header.get("version", ""),
                    "last_updated": header.get("last_updated", ""),
                    "status": header.get("status", ""),
                    "entry_count": len(entries),
                }
                all_entries.extend(entries)
            except Exception as e:
                print(f"  [WARN] Failed to parse {filepath}: {e}")

    return all_entries, file_metadata


def build_index():
    """Build a searchable index with cross-references."""
    entries, metadata = crawl_all()

    # Index structures
    by_name = {}
    by_category = defaultdict(list)
    by_word = defaultdict(set)  # Full-text index

    for entry in entries:
        name_key = entry["name"].lower()
        by_name[name_key] = entry
        if entry["category"]:
            by_category[entry["category"].lower()].append(entry)
        # Full-text: index each word in name and source
        for word in re.findall(r"\w+", entry["name"].lower()):
            if len(word) > 1:
                by_word[word].add(name_key)
        for word in re.findall(r"\w+", entry.get("source", "").lower()):
            if len(word) > 1:
                by_word[word].add(name_key)

    index = {
        "version": "1.0.0",
        "built": datetime.now().isoformat(),
        "statistics": {
            "total_entries": len(entries),
            "total_categories": len(by_category),
            "total_files": len(metadata),
            "categories": dict(sorted((k, len(v)) for k, v in by_category.items())),
        },
        "entries": by_name,
        "categories": {k: [e["name"] for e in v] for k, v in by_category.items()},
        "word_index": {k: sorted(list(v)) for k, v in by_word.items()},
        "file_metadata": metadata,
    }

    # Save index
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    return index


def load_index():
    """Load existing index or build new one."""
    if INDEX_FILE.exists():
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as exc:
            note_failure("bibl_crawler:L170", exc)
    return build_index()


def search_term(index, term):
    """Look up a specific term by exact or partial name match."""
    term_lower = term.lower()
    results = []

    # Exact match
    if term_lower in index["entries"]:
        results.append(index["entries"][term_lower])
    else:
        # Partial match
        for name, entry in index["entries"].items():
            if term_lower in name:
                results.append(entry)

    return results


def search_fulltext(index, query):
    """Full-text search across term names and sources."""
    words = re.findall(r"\w+", query.lower())
    if not words:
        return []

    # Intersection of word matches
    matched = set(index["entries"].keys())
    for word in words:
        if word in index["word_index"]:
            matched &= set(index["word_index"][word])
        else:
            # Also check partial matches
            partial = set()
            for idx_word in index["word_index"]:
                if word in idx_word:
                    partial |= set(index["word_index"][idx_word])
            matched &= partial if partial else set()

    return [index["entries"][name] for name in sorted(matched)]


def list_category(index, category):
    """list all terms in a category."""
    cat_lower = category.lower()
    if cat_lower in index["categories"]:
        return index["categories"][cat_lower]
    return []


def display_entry(entry):
    """Pretty-print a single entry."""
    print(f"\n  {'-' * 50}")
    print(f"  Term:     {entry['name']}")
    print(f"  Category: {entry.get('category', 'N/A')}")
    print(f"  Section:  {entry.get('section', 'N/A')}")
    if entry.get("source"):
        print(f"  Source:   {entry['source']}")
    print(f"  {'-' * 50}")


def cmd_index(args):
    """Rebuild index."""
    print("\n  BIBL Crawler - Building Index")
    print("  " + "=" * 40)
    index = build_index()
    stats = index["statistics"]
    print(f"\n  Index built: {INDEX_FILE}")
    print(f"  Total entries:    {stats['total_entries']}")
    print(f"  Total categories: {stats['total_categories']}")
    print(f"  Total files:      {stats['total_files']}")
    print("\n  Categories:")
    for cat, count in sorted(stats["categories"].items()):
        print(f"    {cat}: {count} terms")
    print()


def cmd_search(args):
    """Search for terms."""
    index = load_index()
    query = " ".join(args.query)
    results = search_fulltext(index, query)

    print(f"\n  BIBL Search: \"{query}\"")
    print("  " + "=" * 40)
    print(f"  Found {len(results)} result(s)\n")

    for entry in results[:25]:
        display_entry(entry)

    if len(results) > 25:
        print(f"\n  ... and {len(results) - 25} more results")
    print()


def cmd_term(args):
    """Look up specific term."""
    index = load_index()
    results = search_term(index, args.term)

    print(f"\n  BIBL Term Lookup: \"{args.term}\"")
    print("  " + "=" * 40)
    print(f"  Found {len(results)} result(s)")

    for entry in results:
        display_entry(entry)
    print()


def cmd_category(args):
    """list terms by category."""
    index = load_index()
    terms = list_category(index, args.category)

    print(f"\n  BIBL Category: \"{args.category}\"")
    print("  " + "=" * 40)
    print(f"  {len(terms)} term(s)\n")

    if terms:
        for name in sorted(terms):
            if name in index["entries"]:
                entry = index["entries"][name]
                print(f"  * {entry['name']} ({entry.get('category', 'N/A')})")
    print()


def cmd_stats(args):
    """Show index statistics."""
    index = load_index()
    stats = index["statistics"]

    print("\n  BIBL Index Statistics")
    print("  " + "=" * 40)
    print(f"  Version:           {index.get('version', 'N/A')}")
    print(f"  Built:             {index.get('built', 'N/A')}")
    print(f"  Total entries:     {stats['total_entries']}")
    print(f"  Total categories:  {stats['total_categories']}")
    print(f"  Total files:       {stats['total_files']}")
    print("\n  File sources:")
    for path, meta in index.get("file_metadata", {}).items():
        print(f"    {Path(path).name}: {meta['entry_count']} entries")
    print("\n  Category breakdown:")
    for cat, count in sorted(stats["categories"].items()):
        bar = "█" * min(40, count)
        print(f"    {cat:<25} {bar} ({count})")
    print()


def cmd_export(args):
    """Export index in specified format."""
    index = load_index()
    fmt = args.format.lower()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        outpath = OUTPUT_DIR / "bibl_export.json"
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
        print(f"\n  Exported JSON to: {outpath}")
        print(f"  Size: {outpath.stat().st_size:,} bytes")

    elif fmt == "html":
        outpath = OUTPUT_DIR / "bibl_export.html"
        _export_html(index, outpath)
        print(f"\n  Exported HTML to: {outpath}")
        print(f"  Size: {outpath.stat().st_size:,} bytes")

    elif fmt == "csv":
        outpath = OUTPUT_DIR / "bibl_export.csv"
        _export_csv(index, outpath)
        print(f"\n  Exported CSV to: {outpath}")
        print(f"  Size: {outpath.stat().st_size:,} bytes")

    else:
        print(f"  Unknown format: {fmt} (use json, html, or csv)")
    print()


def _export_html(index, outpath):
    """Export index as searchable HTML page."""
    entries_html = []
    for name, entry in sorted(index["entries"].items()):
        entries_html.append(
            f'<tr><td class="term">{entry["name"]}</td>'
            f'<td class="cat">{entry.get("category", "")}</td>'
            f'<td class="src">{entry.get("source", "")[:80]}</td></tr>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>BIBL Terminology Index</title>
<style>
body {{ font-family: 'Segoe UI', Consolas, monospace; background: #0a0a1a; color: #e0e0e0; margin: 20px; }}
h1 {{ color: #00f5ff; }} .stats {{ color: #888; margin-bottom: 20px; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ background: #111130; color: #00f5ff; padding: 10px; text-align: left; border-bottom: 2px solid #00f5ff; }}
td {{ padding: 8px 10px; border-bottom: 1px solid #222; }}
.term {{ color: #bf00ff; font-weight: bold; }} .cat {{ color: #00ff88; }} .src {{ color: #666; font-size: 0.85em; }}
tr:hover {{ background: rgba(0,245,255,0.05); }}
input[type=search] {{ padding: 10px; width: 300px; background: #111130; border: 1px solid #333; color: #fff; border-radius: 5px; }}
</style>
</head>
<body>
<h1>BIBL Terminology Index</h1>
<p class="stats">{index['statistics']['total_entries']} entries * {index['statistics']['total_categories']} categories * Built: {index.get('built', 'N/A')}</p>
<input type="search" placeholder="Filter terms..." oninput="
    const q = this.value.toLowerCase();
    document.querySelectorAll('tr.data-row').forEach(tr =>
        tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none'
    );
">
<table>
<thead><tr><th>Term</th><th>Category</th><th>Source</th></tr></thead>
<tbody>{''.join(f'<tr class="data-row">{e}</tr>' for e in entries_html)}</tbody>
</table>
</body>
</html>"""
    outpath.write_text(html, encoding="utf-8")


def _export_csv(index, outpath):
    """Export index as CSV."""
    lines = ["Term,Category,Source"]
    for name, entry in sorted(index["entries"].items()):
        lines.append(
            f'"{entry["name"]}","{entry.get("category", "")}","{entry.get("source", "")}"'
        )
    outpath.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
    description="BIBL Crawler - Terminology Index Builder"
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # Index
    sub.add_parser("index", help="Rebuild terminology index")

    # Search
    p = sub.add_parser("search", help="Full-text search")
    p.add_argument("query", nargs="+", help="Search terms")

    # Term lookup
    p = sub.add_parser("term", help="Look up specific term")
    p.add_argument("term", help="Term name")

    # Category
    p = sub.add_parser("category", help="list terms by category")
    p.add_argument("category", help="Category name")

    # Stats
    sub.add_parser("stats", help="Show index statistics")

    # Export
    p = sub.add_parser("export", help="Export index")
    p.add_argument("format", nargs="?", default="json", help="Format: json, html, csv")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

        commands = {
        "index": cmd_index,
        "search": cmd_search,
        "term": cmd_term,
        "category": cmd_category,
        "stats": cmd_stats,
        "export": cmd_export,
        }

        func = commands.get(args.command)
        if func:
            func(args)


if __name__ == "__main__":
    main()
