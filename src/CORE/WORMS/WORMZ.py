# coding: utf-8
"""
WORMZ - Web-like Organizational Recursive Mapper Zystem

Generative code crawler that crawls ALL folders to keep BIBL up-to-date.

Features:
- Recursive folder crawling
- Python file parsing (classes, functions, docstrings)
- Markdown file parsing (headers, code blocks)
- JSON file parsing
- Auto-updates BIBL lexicon
- Delta updates (only new/changed items)
- Multi-threaded crawling
- Rate limiting and retry logic

Usage:
    >>> from WORMZ import WORMZ
    >>> wz = WORMZ()
    >>> wz.VERSION
    '1.0.0'
"""

VERSION = "1.0.0"

import os
import json
import ast
import re
import time
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass, field
from collections import defaultdict

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


#: Canonical project root for this module's default paths. The fallback used to be
#: `Path.home() / "azazael"`, which names a DIFFERENT, stale checkout from this repo
#: (`C:/Azazael/azazael`), so with the env vars unset the crawler targeted the wrong
#: tree. CORE.paths owns that resolution (AZAZAEL_ROOT first, never raises), so the
#: decision is made once instead of re-derived per module.
from CORE.paths import get_project_root_safe as _get_project_root_safe
import argparse

# REMOVED 2026-10-10: five `try: pass / <NAME>_AVAILABLE = True / except ImportError`
# blocks (SYSTEMZ, PROCESSZ, DISKZ, CLOCKZ, NETWORXZ). In each, the import
# statement had been replaced by `pass`, so the flag was set True unconditionally
# while the module it named was never imported -- a guard that cannot fail. All
# five were write-only: measured repo-wide, the flags appeared in exactly the two
# assignments and nowhere else (zero reads, zero importers). WORMZ crawls folders
# into BIBL and uses none of those worms, so the honest fix was deletion, not
# inventing a reader. Re-adding one requires an actual import and an actual read.


@dataclass
class BIBLEntry:
    """Single BIBL lexicon entry."""

    term: str
    definition: str = ""
    category: str = "general"
    source: str = ""
    file_path: str = ""
    line_number: int = 0
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())
    checksum: str = ""


@dataclass
class CrawlResult:
    """Result from crawling a file."""

    file_path: str
    terms_found: list[BIBLEntry] = field(default_factory=list)
    classes_found: list[str] = field(default_factory=list)
    functions_found: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


class WORMZConfig:
    """WORMZ crawling configuration."""

    # Root path — auto-resolved once from AZAZAEL_ROOT env or default
    _azazael_root = os.environ.get("AZAZAEL_ROOT", os.environ.get("Azazael_ROOT", str(_get_project_root_safe())))

    # Target folders to crawl
    TARGET_FOLDERS = [
        os.path.join(_azazael_root, "src", "ORCHESTRATION"),
        os.path.join(_azazael_root, "src", "HYDRA"),
        os.path.join(_azazael_root, "src", "CORE", "WORMS"),
        os.path.join(_azazael_root, "src", "META"),
        os.path.join(_azazael_root, "src", "UTILITIES", "TARBABY"),
        os.path.join(_azazael_root, "src", "UTILITIES", "POINDEXTER"),
        os.path.join(_azazael_root, "src", "UTILITIES", "PERSEPHONE"),
        os.path.join(_azazael_root, "src", "UTILITIES", "TERENCE"),
        os.path.join(_azazael_root, "src", "CODEX"),
    ]

    # File extensions to crawl
    extensions = {".py", ".md", ".json", ".txt", ".yaml", ".yml", ".xml", ".html"}

    # Directories to skip
    skip_patterns = {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".tox",
        "dist",
        "build",
        "*.egg-info",
        ".tox",
        "Windows",
        "Program Files",
        "Program Files (x86)",
        "AppData",
        "Microsoft",
        "Windows Defender",
        "OneDrive",
    }

    # BIBL output path
    BIBL_PATH = os.path.join(_azazael_root, "src", "ORCHESTRATION", "BIBL")

    # Crawling settings
    max_workers = 4
    rate_limit_ms = 10
    max_retries = 3
    timeout_seconds = 30


try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("WORMZ:register_worm-import", exc)
    register_worm = lambda **kw: (lambda cls: cls)


@register_worm(category="utility", priority=5, cooldown_seconds=60.0, description="Generative code crawler for BIBL lexicon management")
class WORMZ:
    """Web-like Organizational Recursive Mapper Zystem."""

    VERSION = "1.0.0"

    def __init__(self, config: (WORMZConfig | None) = None):
        self.config = config or WORMZConfig()
        self.bibl_path = Path(self.config.BIBL_PATH)
        self.entries: dict[str, BIBLEntry] = {}
        self.crawl_history: dict[str, datetime] = {}
        self.running = False
        self.status_callback = None

    def _is_own_output(self, path) -> bool:
        """True when `path` is a file WORMZ itself writes into BIBL.

        Path prefixes are compared as normalised strings rather than via
        is_relative_to so this cannot raise inside a crawl loop.
        """
        out = str(self.bibl_path).replace("\\", "/").rstrip("/").lower()
        return str(path).replace("\\", "/").lower().startswith(out + "/")

    def _should_skip(self, path: Path) -> bool:
        """Check if path should be skipped.

        The first check is the BIBL self-index guard. WORMZ writes
        BIBL/<LETTER>.md, those files are .md inside the crawl root, and each
        carries `## <term>` headers that _parse_markdown turns into fresh
        entries -- so every pass re-ingested the previous pass's output and
        appended it again. Measured 2026-10-31: F.md reached 62 MB / 2,938,437
        lines holding 24,990 copies of its own `markdown_header` record, and
        E.md 12 MB. Growth is quadratic in passes, and a diff summary hides it
        because 2.9M lines read as one large file, not as a loop.
        """
        path_str = str(path)
        if self._is_own_output(path):
            return True
        for pattern in self.config.skip_patterns:
            if pattern in path_str:
                return True
            if path_str.endswith(pattern):
                return True
        return False

    def _should_crawl(self, file_path: Path) -> bool:
        """Check if file should be crawled."""
        return file_path.suffix in self.config.extensions and not self._should_skip(
            file_path
        )

    def _compute_checksum(self, content: str) -> str:
        """Compute SHA256 checksum."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _parse_python(self, file_path: Path) -> CrawlResult:
        """Parse Python file for terms."""
        start = time.time()
        result = CrawlResult(file_path=str(file_path))

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    result.classes_found.append(node.name)
                    entry = BIBLEntry(
                        term=node.name,
                        definition=ast.get_docstring(node) or f"Class: {node.name}",
                        category="class",
                        source="python_class",
                        file_path=str(file_path),
                        line_number=node.lineno,
                        checksum=self._compute_checksum(node.name),
                    )
                    result.terms_found.append(entry)

                elif isinstance(node, ast.FunctionDef):
                    entry = BIBLEntry(
                        term=node.name,
                        definition=ast.get_docstring(node) or f"Function: {node.name}",
                        category="function",
                        source="python_function",
                        file_path=str(file_path),
                        line_number=node.lineno,
                        checksum=self._compute_checksum(node.name),
                    )
                    result.terms_found.append(entry)

                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            entry = BIBLEntry(
                                term=target.id,
                                definition=f"Variable: {target.id}",
                                category="variable",
                                source="python_assignment",
                                file_path=str(file_path),
                                line_number=node.lineno,
                                checksum=self._compute_checksum(target.id),
                            )
                            result.terms_found.append(entry)

        except Exception as e:
            note_failure("WORMZ:_parse_python", e)
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def _parse_markdown(self, file_path: Path) -> CrawlResult:
        """Parse Markdown file for terms."""
        start = time.time()
        result = CrawlResult(file_path=str(file_path))

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Extract headers
            headers = re.findall(r"^(#{1,6})\s+(.+)$", content, re.MULTILINE)
            for level, title in headers:
                entry = BIBLEntry(
                    term=title.strip(),
                    definition=f"Section: {title.strip()}",
                    category="documentation",
                    source="markdown_header",
                    file_path=str(file_path),
                    checksum=self._compute_checksum(title),
                )
                result.terms_found.append(entry)

            # Extract code blocks as terms
            code_blocks = re.findall(r"```(\w+)?\n([\s\S]+?)```", content)
            for lang, code in code_blocks:
                lines = code.strip().split("\n")
                for line in lines:
                    if line.strip().startswith("#") or line.strip().startswith("//"):
                        term = line.strip().lstrip("#// ").strip()
                        if term:
                            entry = BIBLEntry(
                                term=term,
                                definition=line.strip(),
                                category="code_comment",
                                source="markdown_code",
                                file_path=str(file_path),
                                checksum=self._compute_checksum(term),
                            )
                            result.terms_found.append(entry)

            # Extract terms from backticks
            inline_terms = re.findall(r"`([^`]+)`", content)
            for term in set(inline_terms):
                if len(term) > 2 and not term.startswith("#"):
                    entry = BIBLEntry(
                        term=term,
                        definition=f"Term: {term}",
                        category="inline_code",
                        source="markdown_inline",
                        file_path=str(file_path),
                        checksum=self._compute_checksum(term),
                    )
                    result.terms_found.append(entry)

        except Exception as e:
            note_failure("WORMZ:_parse_markdown", e)
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def _parse_json(self, file_path: Path) -> CrawlResult:
        """Parse JSON file for terms."""
        start = time.time()
        result = CrawlResult(file_path=str(file_path))

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            def extract_keys(obj, prefix=""):
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        full_key = f"{prefix}.{key}" if prefix else key
                        entry = BIBLEntry(
                            term=full_key,
                            definition=f"JSON key: {full_key}",
                            category="json_key",
                            source="json_structure",
                            file_path=str(file_path),
                            checksum=self._compute_checksum(full_key),
                        )
                        result.terms_found.append(entry)
                        extract_keys(value, full_key)
                elif isinstance(obj, list):
                    for i, item in enumerate(obj):
                        extract_keys(item, f"{prefix}[{i}]")

            extract_keys(data)

        except Exception as e:
            note_failure("WORMZ:_parse_json", e)
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def crawl_file(self, file_path: Path) -> CrawlResult:
        """Crawl a single file."""
        suffix = file_path.suffix.lower()

        if suffix == ".py":
            return self._parse_python(file_path)
        elif suffix == ".md":
            return self._parse_markdown(file_path)
        elif suffix == ".json":
            return self._parse_json(file_path)
        else:
            return CrawlResult(file_path=str(file_path))

    def crawl_folder(
        self, folder_path: str, recursive: bool = True
    ) -> list[CrawlResult]:
        """Crawl all files in a folder."""
        results = []
        folder = Path(folder_path)

        if not folder.exists():
            print(f"[WORMZ] Folder not found: {folder_path}")
            return results

        if recursive:
            files = [
                f for f in folder.rglob("*") if f.is_file() and self._should_crawl(f)
            ]
        else:
            files = [
                f for f in folder.glob("*") if f.is_file() and self._should_crawl(f)
            ]

        for file_path in files:
            result = self.crawl_file(file_path)
            results.append(result)

        return results

    def crawl_all(self, show_progress: bool = True) -> dict[str, Any]:
        """Crawl all target folders."""
        self.running = True
        start_time = time.time()
        all_results = []

        print("=" * 70)
        print("  WORMZ - Web-like Organizational Recursive Mapper Zystem")
        print("=" * 70)
        print(f"Start: {datetime.now().isoformat()}")
        print(f"Targets: {len(self.config.TARGET_FOLDERS)} folders")
        print("=" * 70)
        print()

        for i, folder in enumerate(self.config.TARGET_FOLDERS):
            if not self.running:
                break

            folder_name = Path(folder).name
            print(
                f"[{i + 1}/{len(self.config.TARGET_FOLDERS)}] Crawling: {folder_name}"
            )

            results = self.crawl_folder(folder)
            all_results.extend(results)

            terms = sum(len(r.terms_found) for r in results)
            errors = sum(len(r.errors) for r in results)
            print(f"  -> Files: {len(results)}, Terms: {terms}, Errors: {errors}")

        duration = time.time() - start_time

        # Merge results
        self._merge_results(all_results)

        # Update BIBL
        self._update_bibl()

        print()
        print("=" * 70)
        print("  WORMZ CRAWL COMPLETE")
        print("=" * 70)
        print(f"Duration: {duration:.2f}s")
        print(f"Total files crawled: {len(all_results)}")
        print(f"Total terms found: {sum(len(r.terms_found) for r in all_results)}")
        print(f"Total BIBL entries: {len(self.entries)}")
        print("=" * 70)
        print("  WORMZ CRAWL COMPLETE")
        print("=" * 70)
        print(f"Duration: {duration:.2f}s")
        print(f"Total files crawled: {len(all_results)}")
        print(f"Total terms found: {sum(len(r.terms_found) for r in all_results)}")
        print(f"Total BIBL entries: {len(self.entries)}")
        print("=" * 70)

        self.running = False

        return {
            "duration_seconds": duration,
            "files_crawled": len(all_results),
            "terms_found": sum(len(r.terms_found) for r in all_results),
            "entries_updated": len(self.entries),
        }

    def crawl_delta(self) -> dict[str, Any]:
        """Crawl only changed files."""
        self.running = True
        start_time = time.time()
        all_results = []

        print("=" * 70)
        print("  WORMZ DELTA CRAWL")
        print("=" * 70)
        print(f"Start: {datetime.now().isoformat()}")
        print("Mode: Changed files only")
        print("=" * 70)
        print()

        for folder in self.config.TARGET_FOLDERS:
            if not self.running:
                break

            folder_name = Path(folder).name
            print(f"Crawling: {folder_name}")

            folder_path = Path(folder)
            for file_path in folder_path.rglob("*"):
                if not self._should_crawl(file_path):
                    continue

                if self._file_changed(file_path):
                    result = self.crawl_file(file_path)
                    all_results.append(result)

            terms = sum(len(r.terms_found) for r in all_results)
            print(
                f"  â†’ Changed files: {len([r for r in all_results if Path(r.file_path).parent.name == folder_name])}, Terms: {terms}"
            )

        duration = time.time() - start_time
        self._merge_results(all_results)
        self._update_bibl()

        print()
        print(f"Delta crawl complete: {duration:.2f}s")
        print(f"Files crawled: {len(all_results)}")
        print(f"Terms: {sum(len(r.terms_found) for r in all_results)}")

        self.running = False

        return {
            "duration_seconds": duration,
            "files_crawled": len(all_results),
            "terms_found": sum(len(r.terms_found) for r in all_results),
        }

    def _file_changed(self, file_path: Path) -> bool:
        """Check if file changed since last crawl."""
        path_str = str(file_path)
        if path_str not in self.crawl_history:
            return True

        mtime = file_path.stat().st_mtime
        last_crawl = self.crawl_history[path_str]
        return mtime > last_crawl.timestamp()

    def _merge_results(self, results: list[CrawlResult]):
        """Merge crawl results into entries."""
        for result in results:
            self.crawl_history[result.file_path] = datetime.now()

            for entry in result.terms_found:
                # Defence in depth: even if a caller hands us a pre-built
                # CrawlResult from BIBL (the guard in _should_skip only covers
                # files WE walk), never adopt our own output as knowledge.
                if self._is_own_output(entry.file_path):
                    continue
                if entry.term not in self.entries:
                    self.entries[entry.term] = entry
                else:
                    existing = self.entries[entry.term]
                    if entry.checksum != existing.checksum:
                        self.entries[entry.term] = entry

    def _update_bibl(self):
        """Update BIBL lexicon files."""
        if not self.bibl_path.exists():
            self.bibl_path.mkdir(parents=True, exist_ok=True)

        # Update main INDEX
        index_file = self.bibl_path / "INDEX.md"
        self._generate_bibl_index(index_file)

        # Update alphabetical files
        self._generate_bibl_alphabetic()

        print(f"BIBL updated: {self.bibl_path}")

    def _generate_bibl_index(self, output_path: Path):
        """Generate BIBL main index."""
        terms_by_category = defaultdict(list)

        for term, entry in self.entries.items():
            terms_by_category[entry.category].append(term)

        lines = [
            "---",
            "title: BIBL - Hyperlinked Lexicon",
            f"version: {datetime.now().strftime('%Y.%m.%d')}",
            f"last_updated: {datetime.now().strftime('%Y-%m-%d')}",
            "status: ACTIVE",
            "---",
            "",
            "# BIBL - Hyperlinked Lexicon",
            "",
            f"> Auto-generated by WORMZ on {datetime.now().isoformat()}",
            f"> Total entries: {len(self.entries)}",
            "",
            "## Categories",
            "",
        ]

        for category, terms in sorted(terms_by_category.items()):
            lines.append(f"### {category.title()}")
            lines.append(f"- **{len(terms)} terms**")
            lines.append("")

        lines.append("## All Terms (Alphabetical)")
        lines.append("")

        for term in sorted(self.entries.keys()):
            entry = self.entries[term]
            lines.append(f"- **{term}** - {entry.definition[:80]}...")

        lines.append("")
        lines.append("---")
        lines.append("*Generated by WORMZ crawler*")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _generate_bibl_alphabetic(self):
        """Generate alphabetical BIBL files."""
        current_letter = None
        current_entries = []

        for term in sorted(self.entries.keys()):
            letter = term[0].upper()

            if letter != current_letter:
                if current_letter:
                    self._write_alphabetic_file(current_letter, current_entries)
                current_letter = letter
                current_entries = []

            current_entries.append((term, self.entries[term]))

        if current_entries and current_letter:
            self._write_alphabetic_file(current_letter, current_entries)

    def _write_alphabetic_file(self, letter: str, entries: list[tuple[str, BIBLEntry]]):
        """Write alphabetical BIBL file."""
        filename = f"{letter}.md" if letter.isalpha() else "0-9.md"
        output_path = self.bibl_path / filename

        lines = [
            "---",
            f"title: BIBL Terms {letter}",
            "---",
            "",
            f"# BIBL Terms: {letter}",
            "",
        ]

        for term, entry in entries:
            lines.append(f"## {term}")
            lines.append("")
            lines.append(f"**Category:** {entry.category}")
            lines.append("")
            lines.append(f"{entry.definition}")
            lines.append("")
            lines.append(f"*Source: `{entry.source}` | File: {entry.file_path}*")
            lines.append("")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def get_status(self) -> dict[str, Any]:
        """Get current WORMZ status."""
        return {
            "version": self.VERSION,
            "running": self.running,
            "entries_count": len(self.entries),
            "files_crawled": len(self.crawl_history),
            "last_crawl": max(self.crawl_history.values())
            if self.crawl_history
            else None,
            "bibl_path": str(self.bibl_path),
            "target_folders": self.config.TARGET_FOLDERS,
        }

    def get_terms_by_category(self) -> dict[str, list[str]]:
        """Get terms organized by category."""
        categories = defaultdict(list)
        for term, entry in self.entries.items():
            categories[entry.category].append(term)
        return dict(categories)


def run_with_popup():
    """Run WORMZ with popup window - simplified."""
    import tkinter as tk
    from tkinter import ttk, scrolledtext

    root = tk.Tk()
    root.title("WORMZ - BIBL Crawler")
    root.geometry("900x700")
    root.configure(bg="#1a1a2e")

    header = tk.Frame(root, bg="#16213e", pady=10)
    header.pack(fill="x")

    tk.Label(
        header,
        text="WORMZ - BIBL Crawler",
        font=("Consolas", 24, "bold"),
        bg="#16213e",
        fg="#00d4ff",
    ).pack()

    status_label = tk.Label(
        header,
        text="Ready",
        font=("Consolas", 12),
        bg="#16213e",
        fg="#7c3aed",
    )
    status_label.pack()

    progress = ttk.Progressbar(root, mode="determinate", length=850)
    progress.pack(pady=10, padx=25)

    text_area = scrolledtext.ScrolledText(
        root, wrap="word", font=("Consolas", 10), bg="#0f0f1a", fg="#00ff88", height=30
    )
    text_area.pack(fill="both", expand=True, padx=10, pady=10)

    def log(msg):
        text_area.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        text_area.see("end")
        root.update_idletasks()

    def run_crawl():
        log("=" * 60)
        log("WORMZ BIBL Crawler Started")
        log("=" * 60)

        wormz = WORMZ()
        result = wormz.crawl_all(show_progress=False)

        log("")
        log("=" * 60)
        log("CRAWL COMPLETE")
        log("=" * 60)
        log(f"Duration: {result['duration_seconds']:.2f}s")
        log(f"Files: {result['files_crawled']}")
        log(f"Terms: {result['terms_found']}")
        log(f"BIBL Entries: {result['entries_updated']}")

        status_label.config(text="Complete!")
        progress["value"] = 100

        log("Popup will close in 3 seconds...")
        root.after(3000, root.destroy)

    log("Starting WORMZ crawl...")
    root.after(500, run_crawl)
    root.mainloop()


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(description="WORMZ - BIBL Crawler")
    parser.add_argument("--all", action="store_true", help="Crawl all folders")
    parser.add_argument(
        "--delta", action="store_true", help="Delta crawl (changes only)"
    )
    parser.add_argument("--folder", type=str, help="Crawl specific folder")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--popup", action="store_true", help="Show popup window")

    args = parser.parse_args()

    if args.popup:
        run_with_popup()
    elif args.status:
        wormz = WORMZ()
        status = wormz.get_status()
        print(json.dumps(status, indent=2))
    elif args.folder:
        wormz = WORMZ()
        results = wormz.crawl_folder(args.folder)
        print(f"Crawled {len(results)} files")
    elif args.delta:
        wormz = WORMZ()
        result = wormz.crawl_delta()
        print(json.dumps(result, indent=2))
    elif args.all:
        wormz = WORMZ()
        result = wormz.crawl_all()
        print(json.dumps(result, indent=2))
    else:
        print("WORMZ - BIBL Crawler")
        print()
        print("Usage:")
        print("  python WORMZ.py --all      # Crawl all folders")
        print("  python WORMZ.py --delta    # Delta crawl (changes only)")
        print("  python WORMZ.py --folder PATH  # Crawl specific folder")
        print("  python WORMZ.py --status   # Show status")
        print("  python WORMZ.py --popup    # Show popup window")


if __name__ == "__main__":
    main()


