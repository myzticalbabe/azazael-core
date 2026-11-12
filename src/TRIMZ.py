# coding: utf-8
"""
TRIMZ - BIBL-Conjunctive Content Trimmer

Meta worm that works with WORMZ/BIBL to read, modify, and trim
.md and .txt files for updates while maintaining BIBL synchronization.

Features:
- Reads .md and .txt files conjunctively with BIBL entries
- Trims outdated content while preserving BIBL references
- Updates file content based on BIBL term changes
- Maintains synchronization between files and BIBL lexicon
- Popup window with persistence for monitoring
- Dry-run mode to preview changes before applying

Usage:
    >>> import trimz
    >>> trimz.scan_all()              # Scan all .md/.txt files
    >>> trimz.trim_outdated()          # Trim outdated content
    >>> trimz.sync_with_bibl()         # Sync with BIBL entries
    >>> trimz.run_popup()             # Run with popup window
"""

import os
import json
import re
import hashlib
import time
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional
from dataclasses import dataclass, field
from collections import defaultdict

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ── Worm Registry ─────────────────────────────────────────────────────
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("TRIMZ:L40", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5,
                      cooldown_seconds: float = 30.0, description: str = "",
                      tags: Optional[set] = None):
        def _identity(cls):
            return cls
        return _identity

import tkinter as tk
from tkinter import ttk, scrolledtext
import argparse


try:
    from CORE.paths import get_trim_targets, DESKTOP_TARGET_NAMES, LOCAL_TARGET_SUBDIRS
    _HAS_PATHS = True
except ImportError as exc:
    note_failure("TRIMZ:L51", exc)
    _HAS_PATHS = False
    # Standalone mirror of CORE/paths.py (kept in sync manually)
    DESKTOP_TARGET_NAMES = [
        "Azazael", "HYDRA.py", "HORUS.py", "TARBABY.py",
        "POINDEXTER", "PERSEPHONE", "WORMZ", "METAWORMS",
    ]
    LOCAL_TARGET_SUBDIRS = ["", "CORE/WORMS", "META"]

# Portability: use env vars with local fallbacks
_PROJECT_ROOT = str(Path(__file__).resolve().parent)
_DEFAULT_OUTPUT = os.path.join(_PROJECT_ROOT, "output")


@dataclass
class TrimResult:
    """Result from trimming a file."""

    file_path: str
    original_size: int = 0
    trimmed_size: int = 0
    sections_removed: int = 0
    sections_modified: int = 0
    changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    bibl_terms_found: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class BiblEntryRef:
    """Reference to a BIBL entry."""

    term: str
    file_path: str
    line_number: int
    checksum: str = ""
    last_updated: str = ""


def _get_default_trim_targets():
    """Compute default target folders (canonical names live in CORE.paths)."""
    if _HAS_PATHS:
        return get_trim_targets()
    # Fallback: standalone definitions (CORE.paths unavailable)
    _DESKTOP_TARGETS = [str(Path.home() / "Desktop" / d) for d in DESKTOP_TARGET_NAMES]
    _LOCAL_TARGETS = [_PROJECT_ROOT]
    return [p for p in _DESKTOP_TARGETS + _LOCAL_TARGETS if Path(p).exists() or p in _LOCAL_TARGETS]


class TrimzConfig:
    """TRIMZ configuration."""

    # Configurable via AZAZAEL_TARGET_FOLDERS env var
    TARGET_FOLDERS = (
        os.environ.get("AZAZAEL_TARGET_FOLDERS", "").split(os.pathsep)
        if os.environ.get("AZAZAEL_TARGET_FOLDERS")
        else _get_default_trim_targets()
    )

    BIBL_PATH = os.path.join(_PROJECT_ROOT, "BIBL")

    extensions = {".md", ".txt"}

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
        "Windows",
        "Program Files",
        "Program Files (x86)",
        "AppData",
        "Microsoft",
    }

    OUTPUT_PATH = os.environ.get("AZAZAEL_OUTPUT_PATH", os.path.join(_PROJECT_ROOT, "output", "trimz"))

    trim_patterns = {
        "outdated_dates": re.compile(
            r"(?i)(\b(?:created|updated|modified|dated?|last modified)[:\s]*"
            r"\d{4}[-/]\d{2}[-/]\d{2}\b.*?(?=\n\n|\n#|\Z))"
        ),
        "old_versions": re.compile(
            r"(?i)(\b(?:version|v\d+\.\d+\.\d+)[^#\n]*?(?=##|\n\n|\Z))"
        ),
        "deprecated_sections": re.compile(
            r"(?i)(\n#{1,6}\s*.*(?:deprecated|outdated|legacy|obsolete).*?\n"
            r"(?:.*\n)*?)"
        ),
        "empty_headers": re.compile(r"\n(#{1,6}\s*[^\n]*)\n(?=\n#{1,6}\s*|\Z)"),
        "trailing_whitespace": re.compile(r"[ \t]+(?=\n)"),
        "multiple_blanklines": re.compile(r"\n{4,}"),
    }

    max_section_length = 5000
    min_content_length = 100
    dry_run = True
    backup_enabled = True


class BIBLIntegrator:
    """Integrates TRIMZ with BIBL lexicon."""

    def __init__(self, bibl_path: Optional[str] = None):
        self.bibl_path = Path(bibl_path or TrimzConfig.BIBL_PATH)
        self.entries: dict[str, BiblEntryRef] = {}
        self.term_locations: dict[str, list[dict]] = defaultdict(list)

    def load_bibl(self) -> dict[str, BiblEntryRef]:
        """Load BIBL entries from INDEX.md and alphabetical files."""
        self.entries = {}
        self.term_locations = defaultdict(list)

        if not self.bibl_path.exists():
            print(f"[TRIMZ] BIBL path not found: {self.bibl_path}")
            return {}

        try:
            index_file = self.bibl_path / "INDEX.md"
            if index_file.exists():
                self._parse_index(index_file)

            for md_file in self.bibl_path.glob("[A-Z].md"):
                self._parse_alphabetic(md_file)

            for md_file in self.bibl_path.glob("0-9.md"):
                self._parse_alphabetic(md_file)

        except Exception as e:
            print(f"[TRIMZ] Error loading BIBL: {e}")

        return self.entries

    def _parse_index(self, index_file: Path):
        """Parse BIBL INDEX.md."""
        content = index_file.read_text(encoding="utf-8")
        term_pattern = re.compile(r"\*\*(.+?)\*\*")
        for match in term_pattern.finditer(content):
            term = match.group(1)
            self.entries[term] = BiblEntryRef(
                term=term,
                file_path=str(index_file),
                line_number=content[: match.start()].count("\n") + 1,
            )

    def _parse_alphabetic(self, md_file: Path):
        """Parse alphabetical BIBL files."""
        content = md_file.read_text(encoding="utf-8")
        header_pattern = re.compile(r"^## (.+)$", re.MULTILINE)
        for match in header_pattern.finditer(content):
            term = match.group(1)
            line_number = content[: match.start()].count("\n") + 1
            self.entries[term] = BiblEntryRef(
                term=term,
                file_path=str(md_file),
                line_number=line_number,
            )

    def find_term_in_file(self, term: str, file_path: str) -> list[dict]:
        """Find all occurrences of a BIBL term in a file."""
        locations = []
        try:
            content = Path(file_path).read_text(encoding="utf-8")
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if term in line:
                    locations.append(
                        {
                            "line": i,
                            "content": line.strip(),
                            "context": lines[max(0, i - 2) : i + 3],
                        }
                    )
        except Exception as e:
            print(f"[TRIMZ] Error searching for term: {e}")
        return locations

    def get_stale_terms(self, days: int = 30) -> list[str]:
        """Get terms not updated within specified days."""
        stale = []
        cutoff = datetime.now().timestamp() - (days * 86400)
        for term, entry in self.entries.items():
            if entry.last_updated:
                try:
                    dt = datetime.fromisoformat(entry.last_updated)
                    if dt.timestamp() < cutoff:
                        stale.append(term)
                except (ValueError, OSError) as exc:
                    note_failure("TRIMZ:L244", exc)
        return stale


@register_worm(category="optimization", priority=4, cooldown_seconds=45.0, description="BIBL-conjunctive content trimmer — trims outdated content and syncs with BIBL lexicon")
class TRIMZ:
    """Main TRIMZ - Content Trimmer with BIBL integration."""

    VERSION = "1.0.0"

    def __init__(self, config: (TrimzConfig | None) = None):
        self.config = config or TrimzConfig()
        self.bibl_integrator = BIBLIntegrator(self.config.BIBL_PATH)
        self.results: list[TrimResult] = []
        self.running = False
        self.status_callback = None
        self._persistence_file = Path(self.config.OUTPUT_PATH) / ".trimz_state.json"

    def _should_skip(self, path: Path) -> bool:
        """Check if path should be skipped."""
        path_str = str(path)
        for pattern in self.config.skip_patterns:
            if pattern in path_str:
                return True
        return False

    def _should_process(self, file_path: Path) -> bool:
        """Check if file should be processed."""
        return file_path.suffix in self.config.extensions and not self._should_skip(
            file_path
        )

    def _compute_checksum(self, content: str) -> str:
        """Compute SHA256 checksum."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _create_backup(self, file_path: Path) -> (str | None):
        """Create backup of file before modification."""
        if not self.config.backup_enabled:
            return None
        backup_path = file_path.with_suffix(file_path.suffix + ".bak")
        try:
            shutil.copy2(file_path, backup_path)
            return str(backup_path)
        except Exception as e:
            print(f"[TRIMZ] Backup failed: {e}")
            return None

    def _scan_file(self, file_path: Path) -> TrimResult:
        """Scan a single file for trimming opportunities."""
        start = time.time()
        result = TrimResult(file_path=str(file_path))

        try:
            content = file_path.read_text(encoding="utf-8")
            result.original_size = len(content)

            lines = content.split("\n")
            bibl_terms = self.bibl_integrator.entries

            for term in bibl_terms:
                if term in content:
                    result.bibl_terms_found.append(term)

            for name, pattern in self.config.trim_patterns.items():
                matches = list(pattern.finditer(content))
                if matches:
                    result.changes.append(f"{name}: {len(matches)} matches")

        except Exception as e:
            note_failure("TRIMZ:L314", e)
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def _trim_file(self, file_path: Path, dry_run: bool = True) -> TrimResult:
        """Trim content from a file."""
        start = time.time()
        result = TrimResult(file_path=str(file_path))

        try:
            original_content = file_path.read_text(encoding="utf-8")
            result.original_size = len(original_content)
            content = original_content

            if self.config.backup_enabled and not dry_run:
                self._create_backup(file_path)

            for name, pattern in self.config.trim_patterns.items():
                matches = list(pattern.finditer(content))
                if matches:
                    content = pattern.sub("", content)
                    result.sections_removed += len(matches)
                    result.changes.append(f"Removed {len(matches)} {name}")

            content = self._trim_long_sections(content, result)
            content = self._normalize_whitespace(content, result)

            result.trimmed_size = len(content)

            if not dry_run:
                file_path.write_text(content, encoding="utf-8")
                result.changes.append("File updated")
            else:
                result.changes.append(
                    f"DRY-RUN: Would trim {result.original_size - result.trimmed_size} bytes"
                )

        except Exception as e:
            note_failure("TRIMZ:L353", e)
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def _trim_long_sections(self, content: str, result: TrimResult) -> str:
        """Trim sections exceeding max length."""
        lines = content.split("\n")
        trimmed_lines = []
        i = 0

        while i < len(lines):
            line = lines[i]

            if line.startswith("#"):
                section_lines = [line]
                j = i + 1
                while j < len(lines) and not lines[j].startswith("#"):
                    section_lines.append(lines[j])
                    j += 1

                if len("\n".join(section_lines)) > self.config.max_section_length:
                    trimmed = section_lines[: len(section_lines) // 2]
                    trimmed_lines.extend(trimmed)
                    result.sections_modified += 1
                    result.changes.append(f"Trimmed long section: {line}")
                else:
                    trimmed_lines.extend(section_lines)
                i = j
            else:
                trimmed_lines.append(line)
                i += 1

        return "\n".join(trimmed_lines)

    def _normalize_whitespace(self, content: str, result: TrimResult) -> str:
        """Normalize whitespace in content."""
        original = content

        content = re.sub(r"[ \t]+(?=\n)", "", content)
        content = re.sub(r"\n{4,}", "\n\n\n", content)
        content = content.strip()

        if content != original:
            changes = len(original) - len(content)
            result.changes.append(f"Normalized whitespace: -{changes} bytes")

        return content

    def _sync_with_bibl(self, file_path: Path, dry_run: bool = True) -> TrimResult:
        """Sync file content with BIBL entries."""
        start = time.time()
        result = TrimResult(file_path=str(file_path))

        try:
            content = file_path.read_text(encoding="utf-8")
            result.original_size = len(content)

            bibl_terms = self.bibl_integrator.entries
            stale_terms = []

            for term in bibl_terms:
                if term in content:
                    result.bibl_terms_found.append(term)

            stale = self.bibl_integrator.get_stale_terms()
            for term in stale:
                if term in content:
                    stale_terms.append(term)

            if stale_terms:
                result.changes.append(f"Found {len(stale_terms)} stale BIBL terms")
                pattern = re.compile(
                    r"(?i)(\n*###?\s*"
                    + "|".join(re.escape(t) for t in stale_terms)
                    + r".*?)(?=\n###?|\Z)"
                )
                content = pattern.sub("", content)

            result.trimmed_size = len(content)

            if not dry_run:
                if self.config.backup_enabled:
                    self._create_backup(file_path)
                file_path.write_text(content, encoding="utf-8")
                result.changes.append("Synced with BIBL")
            else:
                result.changes.append(
                    f"DRY-RUN: Would sync {len(result.bibl_terms_found)} terms"
                )

        except Exception as e:
            note_failure("TRIMZ:L445", e)
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def scan_all(self, show_progress: bool = True) -> list[TrimResult]:
        """Scan all .md/.txt files for trimming opportunities."""
        self.running = True
        start_time = time.time()
        self.results = []

        self.bibl_integrator.load_bibl()

        files_to_scan = []
        for folder in self.config.TARGET_FOLDERS:
            folder_path = Path(folder)
            if not folder_path.exists():
                continue
            for f in folder_path.rglob("*"):
                if self._should_process(f):
                    files_to_scan.append(f)

        total_files = len(files_to_scan)

        if show_progress:
            print("=" * 70)
            print("  TRIMZ - Content Scanner")
            print("=" * 70)
            print(f"BIBL Entries Loaded: {len(self.bibl_integrator.entries)}")
            print(f"Files to Scan: {total_files}")
            print("=" * 70)

        for i, file_path in enumerate(files_to_scan):
            if not self.running:
                break

            result = self._scan_file(file_path)
            self.results.append(result)

            if show_progress and (i + 1) % 10 == 0:
                print(f"  Scanned {i + 1}/{total_files} files...")

        duration = time.time() - start_time

        if show_progress:
            total_trimmable = sum(1 for r in self.results if r.changes)
            print()
            print("=" * 70)
            print("  SCAN COMPLETE")
            print("=" * 70)
            print(f"Duration: {duration:.2f}s")
            print(f"Files Scanned: {len(self.results)}")
            print(f"Files with Trimmable Content: {total_trimmable}")
            print(
                f"Total BIBL Terms Found: {sum(len(r.bibl_terms_found) for r in self.results)}"
            )
            print("=" * 70)

        self.running = False
        self._save_state()
        return self.results

    def trim_all(
        self, dry_run: Optional[bool] = None, show_progress: bool = True
    ) -> list[TrimResult]:
        """Trim all scannable files."""
        if dry_run is None:
            dry_run = self.config.dry_run

        self.running = True
        start_time = time.time()
        self.results = []

        if not self.results:
            self.scan_all(show_progress=False)

        trimmed_results = []
        total_files = len(self.results)
        trimmed_count = 0

        if show_progress:
            print("=" * 70)
            print("  TRIMZ - Content Trimmer")
            print("=" * 70)
            print(f"Mode: {'DRY-RUN' if dry_run else 'LIVE'}")
            print(f"Files to Trim: {total_files}")
            print("=" * 70)

        for i, result in enumerate(self.results):
            if not self.running:
                break

            if result.changes:
                file_path = Path(result.file_path)
                trim_result = self._trim_file(file_path, dry_run=dry_run)
                trimmed_results.append(trim_result)

                if dry_run:
                    trimmed_count += 1
                elif trim_result.errors:
                    pass
                else:
                    trimmed_count += 1

            if show_progress and (i + 1) % 10 == 0:
                mode = "DRY-RUN" if dry_run else "LIVE"
                print(f"  Processed {i + 1}/{total_files} files ({mode})...")

        duration = time.time() - start_time

        if show_progress:
            total_bytes = sum(r.original_size - r.trimmed_size for r in trimmed_results)
            print()
            print("=" * 70)
            print("  TRIM COMPLETE")
            print("=" * 70)
            print(f"Duration: {duration:.2f}s")
            print(f"Files Trimmed: {trimmed_count}")
            print(f"Bytes Removed: {total_bytes:,}")
            print("=" * 70)

        self.running = False
        return trimmed_results

    def sync_bibl(
        self, dry_run: Optional[bool] = None, show_progress: bool = True
    ) -> list[TrimResult]:
        """Sync files with BIBL lexicon."""
        if dry_run is None:
            dry_run = self.config.dry_run

        self.running = True
        start_time = time.time()
        self.results = []

        if not self.results:
            self.scan_all(show_progress=False)

        sync_results = []
        total_files = len(self.results)
        synced_count = 0

        if show_progress:
            print("=" * 70)
            print("  TRIMZ - BIBL Synchronizer")
            print("=" * 70)
            print(f"Mode: {'DRY-RUN' if dry_run else 'LIVE'}")
            print(f"BIBL Entries: {len(self.bibl_integrator.entries)}")
            print(f"Files to Sync: {total_files}")
            print("=" * 70)

        for i, result in enumerate(self.results):
            if not self.running:
                break

            file_path = Path(result.file_path)
            sync_result = self._sync_with_bibl(file_path, dry_run=dry_run)
            sync_results.append(sync_result)

            if sync_result.bibl_terms_found:
                if dry_run:
                    synced_count += 1
                elif not sync_result.errors:
                    synced_count += 1

            if show_progress and (i + 1) % 10 == 0:
                mode = "DRY-RUN" if dry_run else "LIVE"
                print(f"  Processed {i + 1}/{total_files} files ({mode})...")

        duration = time.time() - start_time

        if show_progress:
            print()
            print("=" * 70)
            print("  SYNC COMPLETE")
            print("=" * 70)
            print(f"Duration: {duration:.2f}s")
            print(f"Files Synced: {synced_count}")
            print("=" * 70)

        self.running = False
        return sync_results

    def _save_state(self):
        """Save persistence state."""
        try:
            Path(self.config.OUTPUT_PATH).mkdir(parents=True, exist_ok=True)
            state = {
                "last_run": datetime.now().isoformat(),
                "files_scanned": len(self.results),
                "dry_run": self.config.dry_run,
            }
            self._persistence_file.write_text(
                json.dumps(state, indent=2), encoding="utf-8"
            )
        except Exception as e:
            print(f"[TRIMZ] State save failed: {e}")

    def load_state(self) -> dict[str, Any]:
        """Load persistence state."""
        try:
            if self._persistence_file.exists():
                return json.loads(self._persistence_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[TRIMZ] State load failed: {e}")
        return {}

    def get_status(self) -> dict[str, Any]:
        """Get TRIMZ status."""
        return {
            "version": self.VERSION,
            "running": self.running,
            "files_scanned": len(self.results),
            "bibl_entries": len(self.bibl_integrator.entries),
            "dry_run": self.config.dry_run,
            "output_path": self.config.OUTPUT_PATH,
            "last_run": self.load_state().get("last_run"),
        }

    def set_dry_run(self, enabled: bool):
        """set dry run mode."""
        self.config.dry_run = enabled
        print(f"[TRIMZ] Dry run mode: {'enabled' if enabled else 'disabled'}")


def run_popup():
    """Run TRIMZ with popup window."""
    root = tk.Tk()
    root.title("TRIMZ - Content Trimmer with BIBL Integration")
    root.geometry("1000x800")
    root.configure(bg="#1a1a2e")

    trimz = TRIMZ()

    def log(msg, level="INFO"):
        colors = {
            "OK": "#00ff88",
            "FAIL": "#ff4444",
            "WARN": "#ffaa00",
            "INFO": "#00aaff",
            "DRY": "#ff00ff",
        }
        timestamp = datetime.now().strftime("%H:%M:%S")
        text_area.insert("end", f"[{timestamp}] [{level}] {msg}\n")
        text_area.see("end")
        root.update_idletasks()

    header = tk.Frame(root, bg="#16213e", pady=15)
    header.pack(fill="x")

    tk.Label(
        header,
        text="TRIMZ - BIBL Conjunctive Trimmer",
        font=("Consolas", 28, "bold"),
        bg="#16213e",
        fg="#00d4ff",
    ).pack()

    status_label = tk.Label(
        header,
        text="Ready",
        font=("Consolas", 14),
        bg="#16213e",
        fg="#7c3aed",
    )
    status_label.pack()

    control_frame = tk.Frame(root, bg="#1a1a2e", pady=10)
    control_frame.pack(fill="x", padx=20)

    dry_run_var = tk.BooleanVar(value=True)
    dry_run_check = tk.Checkbutton(
        control_frame,
        text="Dry Run (Preview Only)",
        variable=dry_run_var,
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00ff88",
        selectcolor="#16213e",
    )
    dry_run_check.pack(side="left", padx=10)

    backup_var = tk.BooleanVar(value=True)
    backup_check = tk.Checkbutton(
        control_frame,
        text="Create Backups",
        variable=backup_var,
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00ff88",
        selectcolor="#16213e",
    )
    backup_check.pack(side="left", padx=10)

    progress_frame = tk.Frame(root, bg="#1a1a2e", pady=10)
    progress_frame.pack(fill="x", padx=20)

    progress = ttk.Progressbar(progress_frame, mode="determinate", length=950)
    progress.pack(fill="x")

    progress_label = tk.Label(
        progress_frame,
        text="0 / 0 files",
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00ff88",
    )
    progress_label.pack()

    stats_frame = tk.Frame(root, bg="#1a1a2e", pady=5)
    stats_frame.pack(fill="x", padx=20)

    stats_label = tk.Label(
        stats_frame,
        text="Scanned: 0 | Trimmed: 0 | Bytes: 0",
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00d4ff",
    )
    stats_label.pack()

    text_area = scrolledtext.ScrolledText(
        root, wrap="word", font=("Consolas", 10), bg="#0f0f1a", fg="#00ff88", height=35
    )
    text_area.pack(fill="both", expand=True, padx=20, pady=10)

    button_frame = tk.Frame(root, bg="#1a1a2e", pady=10)
    button_frame.pack(fill="x", padx=20)

    def run_scan():
        status_label.config(text="Scanning...")
        progress["value"] = 0
        text_area.delete("1.0", "end")

        log("=" * 60, "INFO")
        log("TRIMZ - Content Scanner", "INFO")
        log("=" * 60, "INFO")

        trimz.config.dry_run = dry_run_var.get()
        trimz.config.backup_enabled = backup_var.get()

        results = trimz.scan_all(show_progress=False)

        log(f"BIBL Entries Loaded: {len(trimz.bibl_integrator.entries)}", "INFO")
        log(f"Files Scanned: {len(results)}", "INFO")

        trimmable = sum(1 for r in results if r.changes)
        log(f"Files with Trimmable Content: {trimmable}", "INFO")

        total_terms = sum(len(r.bibl_terms_found) for r in results)
        log(f"Total BIBL Terms Found: {total_terms}", "INFO")

        progress["value"] = 50
        progress_label.config(text=f"{len(results)} / {len(results)} files")
        stats_label.config(
            text=f"Scanned: {len(results)} | Trimmable: {trimmable} | Terms: {total_terms}"
        )

        status_label.config(text="Scan Complete - Ready to Trim")

        return results

    def run_trim():
        status_label.config(text="Trimming...")
        progress["value"] = 50

        trimz.config.dry_run = dry_run_var.get()
        trimz.config.backup_enabled = backup_var.get()

        results = trimz.trim_all(dry_run=dry_run_var.get(), show_progress=False)

        mode = "DRY-RUN" if dry_run_var.get() else "LIVE"
        log(f"\n{'=' * 60}", "INFO")
        log(f"TRIMZ - Trim Complete ({mode})", "OK" if dry_run_var.get() else "INFO")
        log(f"{'=' * 60}", "INFO")

        trimmed = sum(
            1 for r in results if r.sections_removed > 0 or r.sections_modified > 0
        )
        bytes_removed = sum(r.original_size - r.trimmed_size for r in results)

        log(f"Files Trimmed: {trimmed}", "INFO")
        log(f"Bytes Removed: {bytes_removed:,}", "INFO")

        progress["value"] = 100
        progress_label.config(text=f"{len(results)} / {len(results)} files")
        stats_label.config(
            text=f"Scanned: {len(results)} | Trimmed: {trimmed} | Bytes: {bytes_removed:,}"
        )

        status_label.config(text="Trim Complete")

        if dry_run_var.get():
            log("\nTo apply changes, disable Dry Run and click Trim again.", "DRY")

    def run_sync():
        status_label.config(text="Syncing with BIBL...")
        progress["value"] = 50

        trimz.config.dry_run = dry_run_var.get()
        trimz.config.backup_enabled = backup_var.get()

        results = trimz.sync_bibl(dry_run=dry_run_var.get(), show_progress=False)

        mode = "DRY-RUN" if dry_run_var.get() else "LIVE"
        log(f"\n{'=' * 60}", "INFO")
        log(
            f"TRIMZ - BIBL Sync Complete ({mode})",
            "OK" if dry_run_var.get() else "INFO",
        )
        log(f"{'=' * 60}", "INFO")

        synced = sum(1 for r in results if r.bibl_terms_found)
        log(f"Files Synced: {synced}", "INFO")

        total_terms = sum(len(r.bibl_terms_found) for r in results)
        log(f"BIBL Terms Processed: {total_terms}", "INFO")

        progress["value"] = 100
        stats_label.config(
            text=f"Synced: {synced} | Terms: {total_terms} | Mode: {mode}"
        )

        status_label.config(text="Sync Complete")

    def on_scan():
        button_scan.config(state="disabled")
        button_trim.config(state="disabled")
        button_sync.config(state="disabled")
        root.update()
        try:
            run_scan()
        finally:
            button_scan.config(state="normal")
            button_trim.config(state="normal")
            button_sync.config(state="normal")

    def on_trim():
        button_scan.config(state="disabled")
        button_trim.config(state="disabled")
        button_sync.config(state="disabled")
        root.update()
        try:
            run_trim()
        finally:
            button_scan.config(state="normal")
            button_trim.config(state="normal")
            button_sync.config(state="normal")

    def on_sync():
        button_scan.config(state="disabled")
        button_trim.config(state="disabled")
        button_sync.config(state="disabled")
        root.update()
        try:
            run_sync()
        finally:
            button_scan.config(state="normal")
            button_trim.config(state="normal")
            button_sync.config(state="normal")

    button_scan = tk.Button(
        button_frame,
        text="SCAN",
        command=on_scan,
        font=("Consolas", 14, "bold"),
        bg="#16213e",
        fg="#00d4ff",
        width=12,
    )
    button_scan.pack(side="left", padx=10)

    button_trim = tk.Button(
        button_frame,
        text="TRIM",
        command=on_trim,
        font=("Consolas", 14, "bold"),
        bg="#16213e",
        fg="#00ff88",
        width=12,
    )
    button_trim.pack(side="left", padx=10)

    button_sync = tk.Button(
        button_frame,
        text="SYNC BIBL",
        command=on_sync,
        font=("Consolas", 14, "bold"),
        bg="#16213e",
        fg="#ffaa00",
        width=12,
    )
    button_sync.pack(side="left", padx=10)

    close_btn = tk.Button(
        button_frame,
        text="CLOSE",
        command=root.destroy,
        font=("Consolas", 14, "bold"),
        bg="#ff4444",
        fg="#ffffff",
        width=10,
    )
    close_btn.pack(side="right", padx=10)

    log("TRIMZ Ready", "INFO")
    log("Configure options and click SCAN to begin", "INFO")

    root.mainloop()


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(
        description="TRIMZ - BIBL Conjunctive Content Trimmer"
    )
    parser.add_argument("--scan", action="store_true", help="Scan all files")
    parser.add_argument("--trim", action="store_true", help="Trim files")
    parser.add_argument("--sync", action="store_true", help="Sync with BIBL lexicon")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying (default)",
    )
    parser.add_argument("--apply", action="store_true", help="Actually apply changes")
    parser.add_argument(
        "--no-backup", action="store_true", help="Skip creating backups"
    )
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--popup", action="store_true", help="Show popup window")

    args = parser.parse_args()

    if args.popup:
        run_popup()
    elif args.status:
        trimz = TRIMZ()
        status = trimz.get_status()
        print(json.dumps(status, indent=2))
    elif args.scan:
        trimz = TRIMZ()
        if not args.apply:
            trimz.set_dry_run(True)
        if args.no_backup:
            trimz.config.backup_enabled = False
        results = trimz.scan_all()
        print(f"Scanned {len(results)} files")
    elif args.trim:
        trimz = TRIMZ()
        if not args.apply:
            trimz.set_dry_run(True)
        if args.no_backup:
            trimz.config.backup_enabled = False
        results = trimz.trim_all(dry_run=not args.apply)
        print(f"Trimmed {len(results)} files")
    elif args.sync:
        trimz = TRIMZ()
        if not args.apply:
            trimz.set_dry_run(True)
        if args.no_backup:
            trimz.config.backup_enabled = False
        results = trimz.sync_bibl(dry_run=not args.apply)
        print(f"Synced {len(results)} files")
    else:
        print("TRIMZ - BIBL Conjunctive Content Trimmer")
        print()
        print("Usage:")
        print("  python TRIMZ.py --scan      Scan all .md/.txt files")
        print("  python TRIMZ.py --trim      Trim content from files")
        print("  python TRIMZ.py --sync      Sync with BIBL lexicon")
        print("  python TRIMZ.py --dry-run   Preview (default)")
        print("  python TRIMZ.py --apply     Actually apply changes")
        print("  python TRIMZ.py --no-backup Skip backup creation")
        print("  python TRIMZ.py --status    Show status")
        print("  python TRIMZ.py --popup     Show popup window")
        print()
        print("BIBL Integration:")
        print("  - Reads BIBL entries from INDEX.md and alphabetical files")
        print("  - Finds BIBL terms in .md/.txt files")
        print("  - Syncs stale terms and trims outdated content")


if __name__ == "__main__":
    main()
