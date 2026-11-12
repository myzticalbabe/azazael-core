# coding: utf-8
"""
error_triage — Structured Error Triage System

Parses ERROR_LOG.md entries, classifies by severity and category,
and assigns owners based on affected module/domain.

Provides:
- ErrorTriageSystem: Orchestrator for classifying, querying, and reporting
- ErrorEntry: Parsed error with severity, category, owner, file, etc.
- TriageReport: Summary report with severity/category/owner breakdowns

Usage:
    >>> from CORE.error_triage import ErrorTriageSystem
    >>> ets = ErrorTriageSystem()
    >>> ets.load()  # Load and classify all errors
    >>> report = ets.triage()  # Generate triage report
    >>> ets.by_severity("critical")  # All critical errors
    >>> ets.by_owner("worms")  # All worm-related errors
"""

import re
import os
from datetime import datetime
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "ErrorEntry",
    "ErrorTriageSystem",
    "load_triage_system",
    "print_triage_summary",
]

# =====================================================================
# Data Types
# =====================================================================

@dataclass
class ErrorEntry:
    """A single parsed error entry from ERROR_LOG.md."""
    number: int
    title: str
    severity: str          # "critical", "warning", "info"
    category: str          # classified category
    owner: str             # assigned owner
    affected_files: list[str]
    timestamp: (str | None)
    cause_summary: (str | None)
    fix_summary: (str | None)
    is_regression: bool    # True if marked as regressed/repeated
    repeat_count: int      # How many times this error type has occurred

    @property
    def severity_icon(self) -> str:
        return {"critical": "CRIT", "warning": "WARN", "info": "INFO"}.get(self.severity, "????")

    @property
    def severity_priority(self) -> int:
        return {"critical": 0, "warning": 1, "info": 2}.get(self.severity, 99)


# =====================================================================
# Classification Taxonomy
# =====================================================================

# -- Category definitions with keyword patterns and default owners --
CATEGORY_DEFINITIONS: dict[str, dict[str, Any]] = {
    "C-extension": {
        "description": "C extension module incompatibilities (psutil, pydantic-core, psycopg2, Nuitka)",
        "keywords": ["psutil", "pydantic", "nuitka", "segfault", "c extension", "cp314t",
                     "wheel", ".pyd", "dll load", "c-backend", "c compiled"],
        "default_owner": "devops",
        "severity_bias": "critical",
    },
    "shell-escaping": {
        "description": "Shell escaping and path translation bugs (Git Bash, MSYS2, PowerShell)",
        "keywords": ["msys2", "git bash", "shell escaping", "path translation",
                     "backslash", "forward slash", "subprocess", "basher",
                     "unicode escape", "cmd.exe"],
        "default_owner": "platform",
        "severity_bias": "warning",
    },
    "data-loss": {
        "description": "Data loss incidents (shutil.copytree+rmtree, archive loss, session loss)",
        "keywords": ["data loss", "lost", "deleted", "corrupt", "incomplete",
                     "7.5 gb", "archive", "rmtree", "copytree", "timeout killed"],
        "default_owner": "infrastructure",
        "severity_bias": "critical",
    },
    "sqlite": {
        "description": "SQLite concurrency, locking, and persistence issues",
        "keywords": ["sqlite", "database is locked", "journal mode", "wal",
                     "concurrent write", "busy_timeout", "operationalerror"],
        "default_owner": "infrastructure",
        "severity_bias": "critical",
    },
    "threading": {
        "description": "Thread safety, deadlocks, and race conditions (3.14t free-threading)",
        "keywords": ["deadlock", "race condition", "thread safe", "free-thread",
                     "double-checked lock", "lazy import", "import inside",
                     "contention", "re-entrancy"],
        "default_owner": "infrastructure",
        "severity_bias": "critical",
    },
    "process-violation": {
        "description": "SOP/process violations, protocol failures, instruction disobedience",
        "keywords": ["protocol violation", "process violation", "rule 0",
                     "failed instruction", "disobey", "forbidden",
                     "alter rule", "do not", "level 1 directive",
                     "unauthorized", "prohibited"],
        "default_owner": "governance",
        "severity_bias": "critical",
    },
    "memory-recording": {
        "description": "Memory recording, session tracking, and PAMS system failures",
        "keywords": ["pams", "memory recording", "session recording", "tracker",
                     "recorder", "directmemory", "localmemorystore", "memorybus",
                     "fallback", "record_session", "codebuff_tracker",
                     "session_id", "recording"],
        "default_owner": "memory",
        "severity_bias": "critical",
    },
    "package-dependency": {
        "description": "Package installation, wheel compatibility, and dependency issues",
        "keywords": ["pip install", "wheel", "package", "dependency", "install",
                     "requirement", "compatibility", "version mismatch",
                     "abort", "resolutionimpossible"],
        "default_owner": "devops",
        "severity_bias": "warning",
    },
    "test-infrastructure": {
        "description": "Test framework failures (pytest segfaults, test timeouts, CI)",
        "keywords": ["pytest", "segfault on test", "test timeout", "test file",
                     "ci pipeline", "test collection", "standalone",
                     "skip-slow"],
        "default_owner": "devops",
        "severity_bias": "warning",
    },
    "nuitka-build": {
        "description": "Nuitka compilation and standalone EXE build issues",
        "keywords": ["nuitka", "onefile", "exe", "standalone", "compile",
                     "build", "c-backend", "allocator", "compiledframe",
                     "onefile mode"],
        "default_owner": "devops",
        "severity_bias": "warning",
    },
    "worm-registry": {
        "description": "Worm registry, discovery, and execution issues",
        "keywords": ["worm registry", "discover_worms", "worm execution",
                     "registry", "spawn", "expansion.py", "ecosystem_expansion",
                     "worm count"],
        "default_owner": "worms",
        "severity_bias": "warning",
    },
    "code-quality": {
        "description": "Code quality issues (anti-patterns, dead code, backlogs)",
        "keywords": ["anti-pattern", "backlog", "unicode", "emoji", "non-ascii",
                     "t-string", "import inside", "os.system",
                     "naming violation", "dead code"],
        "default_owner": "infrastructure",
        "severity_bias": "info",
    },
    "inference": {
        "description": "Inference provider, API, and model serving issues",
        "keywords": ["inference", "provider", "api key", "model", "endpoint",
                     "groq", "openrouter", "nvidia", "mistral", "github model",
                     "huggingface", "rate limit", "quota"],
        "default_owner": "inference",
        "severity_bias": "warning",
    },
    "other": {
        "description": "Uncategorized errors",
        "keywords": [],
        "default_owner": "infrastructure",
        "severity_bias": "warning",
    },
}

# -- Owner descriptions --
OWNER_DESCRIPTIONS: dict[str, str] = {
    "infrastructure": "CORE infrastructure (SQLite, threading, imports, paths)",
    "devops": "DevOps (packages, builds, CI, Nuitka, wheels)",
    "platform": "Platform (shell, paths, cross-platform, WSL, MSYS2)",
    "governance": "Governance (SOP, process, compliance, rules)",
    "memory": "Memory systems (PAMS, DirectMemory, MemoryBus, recording)",
    "worms": "Worm registry and execution",
    "inference": "Inference providers and API integration",
}

# =====================================================================
# Error Triage System
# =====================================================================

class ErrorTriageSystem:
    """
    Parses ERROR_LOG.md, classifies errors, and produces triage reports.

    Features:
    - Automatic severity/category/owner classification from error content
    - Query by severity, category, owner, or number range
    - Triage reports with breakdowns and recommendations
    - Regression detection (same title appearing in multiple errors)
    """

    # -- Path to ERROR_LOG.md --
    ERROR_LOG_PATH = "ERROR_LOG.md"

    def __init__(self, error_log_path: (str | None) = None):
        self.errors: list[ErrorEntry] = []
        self._raw_text: str = ""
        self._error_log_path = error_log_path or self.ERROR_LOG_PATH
        self._loaded = False

    def load(self, error_log_path: (str | None) = None) -> int:
        """
        Load and classify all errors from ERROR_LOG.md.

        Args:
            error_log_path: Override path to ERROR_LOG.md

        Returns:
            Number of errors loaded
        """
        if error_log_path:
            self._error_log_path = error_log_path

        # Resolve path
        if not os.path.isabs(self._error_log_path):
            # Try project root
            root = Path(__file__).resolve().parent.parent.parent
            full_path = root / self._error_log_path
            if full_path.exists():
                self._error_log_path = str(full_path)

        if not os.path.exists(self._error_log_path):
            raise FileNotFoundError(
                f"ERROR_LOG.md not found at {self._error_log_path}"
            )

        with open(self._error_log_path, "r", encoding="utf-8") as f:
            self._raw_text = f.read()

        self.errors = self._parse_errors()
        self._classify_all()
        self._detect_regressions()
        self._loaded = True
        return len(self.errors)

    def reload(self) -> int:
        """Reload errors from disk, discarding current classifications."""
        self.errors = []
        return self.load()

    # -- Query methods --

    def by_severity(self, severity: str) -> list[ErrorEntry]:
        """Get all errors with a given severity level."""
        sev = severity.lower().strip()
        return [e for e in self.errors if e.severity == sev]

    def by_category(self, category: str) -> list[ErrorEntry]:
        """Get all errors in a given category."""
        cat = category.lower().strip()
        return [e for e in self.errors if e.category == cat]

    def by_owner(self, owner: str) -> list[ErrorEntry]:
        """Get all errors assigned to a given owner."""
        own = owner.lower().strip()
        return [e for e in self.errors if e.owner == own]

    def by_number(self, number: int) -> (ErrorEntry | None):
        """Get a specific error by its number."""
        for e in self.errors:
            if e.number == number:
                return e
        return None

    def by_range(self, start: int, end: int) -> list[ErrorEntry]:
        """Get errors in a number range [start, end]."""
        return sorted(
            [e for e in self.errors if start <= e.number <= end],
            key=lambda x: x.number,
        )

    def search(self, query: str) -> list[ErrorEntry]:
        """Search errors by keyword in title or cause summary."""
        q = query.lower()
        results = []
        for e in self.errors:
            if q in e.title.lower():
                results.append(e)
            elif e.cause_summary and q in e.cause_summary.lower():
                results.append(e)
            elif any(q in f.lower() for f in e.affected_files):
                results.append(e)
        return results

    def get_unowned(self) -> list[ErrorEntry]:
        """Get errors that defaulted to 'other' category."""
        return [e for e in self.errors if e.category == "other"]

    def get_regressions(self) -> list[ErrorEntry]:
        """Get errors marked as regressions (repeat occurrences)."""
        return [e for e in self.errors if e.is_regression]

    # -- Reporting --

    def triage(self) -> dict[str, Any]:
        """
        Generate a comprehensive triage report.

        Returns:
            dict with severity/category/owner breakdowns, unowned errors,
            regressions, and priority recommendations
        """
        if not self._loaded:
            return {"error": "No errors loaded. Call load() first."}

        sev_counts: dict[str, int] = {}
        cat_counts: dict[str, dict] = {}
        owner_counts: dict[str, dict] = {}
        regressions = self.get_regressions()
        unowned = self.get_unowned()

        for e in self.errors:
            # Severity counts
            sev_counts[e.severity] = sev_counts.get(e.severity, 0) + 1

            # Category counts
            if e.category not in cat_counts:
                cat_counts[e.category] = {"count": 0, "critical": 0, "warning": 0, "info": 0}
            cat_counts[e.category]["count"] += 1
            cat_counts[e.category][e.severity] += 1

            # Owner counts
            if e.owner not in owner_counts:
                owner_counts[e.owner] = {"count": 0, "critical": 0, "warning": 0, "info": 0}
            owner_counts[e.owner]["count"] += 1
            owner_counts[e.owner][e.severity] += 1

        return {
            "total_errors": len(self.errors),
            "error_range": (
                min(e.number for e in self.errors) if self.errors else 0,
                max(e.number for e in self.errors) if self.errors else 0,
            ),
            "severity_breakdown": sev_counts,
            "category_breakdown": dict(sorted(
                cat_counts.items(),
                key=lambda x: -x[1]["count"],
            )),
            "owner_breakdown": dict(sorted(
                owner_counts.items(),
                key=lambda x: -x[1]["count"],
            )),
            "regression_count": len(regressions),
            "unowned_count": len(unowned),
            "unowned_errors": [e.number for e in unowned],
            "recommendations": self._generate_recommendations(sev_counts, cat_counts, owner_counts),
        }

    def print_summary(self) -> None:
        """Print a human-readable triage summary."""
        report = self.triage()
        if "error" in report:
            print(report["error"])
            return

        print("=" * 60)
        print(f"ERROR TRIAGE REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)
        print(f"Total errors: {report['total_errors']}")
        print(f"Error range: #{report['error_range'][0]} to #{report['error_range'][1]}")
        print()

        # Severity
        print("[Severity Breakdown]")
        for sev in ["critical", "warning", "info"]:
            count = report["severity_breakdown"].get(sev, 0)
            icon = {"critical": "CRIT", "warning": "WARN", "info": "INFO"}[sev]
            bar = "#" * min(count, 40)
            print(f"  {icon:5s}  {count:3d}  |{bar:<40s}|")
        print()

        # Owner breakdown
        print("[Owner Breakdown]")
        for owner, data in report["owner_breakdown"].items():
            desc = OWNER_DESCRIPTIONS.get(owner, "")
            c = data["critical"]
            w = data["warning"]
            i = data["info"]
            print(f"  {owner:20s}  {data['count']:3d} total  ({c}C/{w}W/{i}I)  {desc}")
        print()

        # Category breakdown
        print("[Category Breakdown]")
        for cat, data in report["category_breakdown"].items():
            desc = CATEGORY_DEFINITIONS.get(cat, {}).get("description", "")
            c = data["critical"]
            w = data["warning"]
            i = data["info"]
            print(f"  {cat:22s}  {data['count']:3d} errors  ({c}C/{w}W/{i}I)  {desc[:50]}")
        print()

        # Regressions
        if report["regression_count"] > 0:
            print(f"[Regressions] {report['regression_count']} errors marked as repeats")
            for err in self.get_regressions()[:5]:
                print(f"  #{err.number}: {err.title[:60]}")
            if report["regression_count"] > 5:
                print(f"  ... and {report['regression_count'] - 5} more")
            print()

        # Recommendations
        print("[Recommendations]")
        for rec in report["recommendations"]:
            print(f"  * [{rec['priority']}] {rec['message']}")
        print()

        print("=" * 60)

    def print_error_detail(self, number: int) -> None:
        """Print detailed info for a single error."""
        err = self.by_number(number)
        if not err:
            print(f"Error #{number} not found")
            return

        print(f"Error #{err.number} — {err.title}")
        print(f"  Severity: {err.severity_icon} ({err.severity})")
        print(f"  Category: {err.category}")
        print(f"  Owner:    {err.owner}")
        print(f"  Files:    {', '.join(err.affected_files) if err.affected_files else 'N/A'}")
        print(f"  Timestamp: {err.timestamp or 'N/A'}")
        if err.is_regression:
            print(f"  Regression: YES (repeat count: {err.repeat_count})")
        if err.cause_summary:
            print(f"  Cause: {err.cause_summary[:200]}")
        if err.fix_summary:
            print(f"  Fix:   {err.fix_summary[:200]}")

    def export_json(self) -> list[dict[str, Any]]:
        """Export all errors as JSON-compatible dicts."""
        return [
            {
                "number": e.number,
                "title": e.title,
                "severity": e.severity,
                "category": e.category,
                "owner": e.owner,
                "files": e.affected_files,
                "timestamp": e.timestamp,
                "is_regression": e.is_regression,
                "repeat_count": e.repeat_count,
            }
            for e in self.errors
        ]

    # -- Internal: Parsing --

    def _parse_errors(self) -> list[ErrorEntry]:
        """Parse ERROR_LOG.md into structured error entries."""
        errors: list[ErrorEntry] = []
        blocks = re.split(r'\n(?=## Error #)', self._raw_text)

        for block in blocks:
            if not block.strip():
                continue

            # Extract error number and title
            header_match = re.match(r'## Error #(\d+) — (.+)', block)
            if not header_match:
                continue

            num = int(header_match.group(1))
            title = header_match.group(2).strip()

            # Extract severity
            severity = "warning"  # default
            if "🔴 Critical" in block:
                severity = "critical"
            elif "🟡 Warning" in block:
                severity = "warning"
            elif "🟢 Info" in block or "▪ Info" in block:
                severity = "info"

            # Extract timestamp
            ts_match = re.search(r'\*\*Timestamp:\*\* (.+)', block)
            timestamp = ts_match.group(1).strip() if ts_match else None

            # Extract affected files
            files: list[str] = []
            file_match = re.search(r'\*\*File\(s\):\*\* (.+)', block)
            if file_match:
                raw = file_match.group(1).strip()
                for f in re.split(r'[,;]', raw):
                    f = f.strip().strip('`"\'')
                    if f:
                        files.append(f)

            # Extract cause and fix summaries (multi-line support)
            cause_summary: (str | None) = None
            cause_m = re.search(r'\*\*Cause:\*\* ([^\n]+(?:\n(?!\*\*|##)[^\n]+)*)', block)
            if cause_m:
                cause_summary = cause_m.group(1).strip()

            fix_summary: (str | None) = None
            fix_m = re.search(r'\*\*Fix[^:]*:\*\* ([^\n]+(?:\n(?!\*\*|##)[^\n]+)*)', block)
            if not fix_m:
                fix_m = re.search(r'\*\*Fix Solution:\*\* ([^\n]+(?:\n(?!\*\*|##)[^\n]+)*)', block)
            if fix_m:
                fix_summary = fix_m.group(1).strip()

            # Detect regression
            is_regression = bool(re.search(r'regression|repeated|repeat|backlog', title, re.IGNORECASE))

            errors.append(ErrorEntry(
                number=num,
                title=title,
                severity=severity,
                category="other",       # Will be classified in _classify_all
                owner="infrastructure", # Will be classified in _classify_all
                affected_files=files,
                timestamp=timestamp,
                cause_summary=cause_summary,
                fix_summary=fix_summary,
                is_regression=is_regression,
                repeat_count=1,
            ))

        return errors

    def _classify_all(self) -> None:
        """Classify all errors by category and owner based on content analysis."""
        # Group similar titles to detect repeat counts
        title_groups: dict[str, list[ErrorEntry]] = {}
        for err in self.errors:
            # Normalize title for grouping
            norm = self._normalize_title(err.title)
            if norm not in title_groups:
                title_groups[norm] = []
            title_groups[norm].append(err)

        # Assign repeat counts
        for norm, group in title_groups.items():
            if len(group) > 1:
                for i, err in enumerate(group):
                    err.repeat_count = len(group)
                    if i > 0:
                        err.is_regression = True

        # Classify each error
        for err in self.errors:
            self._classify_single(err)

    def _classify_single(self, err: ErrorEntry) -> None:
        """Classify a single error entry by category and owner."""
        # Build search text from title + files + cause
        search_text = f"{err.title} {' '.join(err.affected_files)} {err.cause_summary or ''}".lower()

        # Score each category by keyword matches
        best_category = "other"
        best_score = 0
        best_owner = "infrastructure"

        for cat_name, cat_def in CATEGORY_DEFINITIONS.items():
            if cat_name == "other":
                continue
            score = 0
            for kw in cat_def["keywords"]:
                if kw.lower() in search_text:
                    score += 2 if kw.lower() in err.title.lower() else 1
            if score > best_score:
                best_score = score
                best_category = cat_name
                best_owner = cat_def.get("default_owner", "infrastructure")
            elif score > 0 and score == best_score:
                # Tie — prefer more specific category
                best_category = cat_name
                best_owner = cat_def.get("default_owner", "infrastructure")

        # Override: use file-based owner assignment for better precision
        file_owner = self._owner_from_files(err.affected_files)
        if file_owner:
            best_owner = file_owner
            # If files clearly indicate an owner, refine category too
            if best_category == "other":
                best_category = self._category_from_owner(best_owner)

        err.category = best_category
        err.owner = best_owner

    def _owner_from_files(self, files: list[str]) -> (str | None):
        """Determine owner from affected file paths."""
        for f in files:
            f_lower = f.lower()
            if any(kw in f_lower for kw in ["pams", "memorybus", "direct_memory",
                                             "local_memory", "codebuff_tracker",
                                             "record_session"]):
                return "memory"
            if any(kw in f_lower for kw in ["nuitka", "build_all", "patch_nuitka"]):
                return "devops"
            if any(kw in f_lower for kw in ["worm_registry", "worm", "expansion"]):
                return "worms"
            if any(kw in f_lower for kw in ["inference", "provider", "openai",
                                             "groq", "nvidia"]):
                return "inference"
            if any(kw in f_lower for kw in ["msys2", "git bash", "powershell",
                                             "cmd.exe", "wsl"]):
                return "platform"
            if any(kw in f_lower for kw in ["sqlite", "worm_governance",
                                             "freethreading", "agent"]):
                return "infrastructure"
            if any(kw in f_lower for kw in ["sop", "rule 0", "system_instructions"]):
                return "governance"
        return None

    @staticmethod
    def _category_from_owner(owner: str) -> str:
        """Map an owner to a default category."""
        mapping = {
            "memory": "memory-recording",
            "devops": "package-dependency",
            "worms": "worm-registry",
            "inference": "inference",
            "platform": "shell-escaping",
            "governance": "process-violation",
            "infrastructure": "other",
        }
        return mapping.get(owner, "other")

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Normalize an error title for grouping similar errors."""
        norm = title.lower().strip()
        # Remove error numbers
        norm = re.sub(r'#\d+', '', norm)
        # Remove parenthetical notes
        norm = re.sub(r'\(.*?\)', '', norm)
        # Remove leading/trailing punctuation
        norm = norm.strip().strip('—:;,.-')
        # Collapse whitespace
        norm = re.sub(r'\s+', ' ', norm)
        return norm[:80]

    def _detect_regressions(self) -> None:
        """Detect additional regression patterns from content analysis."""
        for err in self.errors:
            if "regression" in err.title.lower():
                err.is_regression = True
            if "repeat" in err.title.lower() or "backlog" in err.title.lower():
                err.is_regression = True
                # Backlog errors have many instances
                if "backlog" in err.title.lower():
                    count_m = re.search(r'(\d+)', err.title)
                    if count_m:
                        err.repeat_count = int(count_m.group(1))

    @staticmethod
    def _generate_recommendations(
        sev_counts: dict[str, int],
        cat_counts: dict[str, dict],
        owner_counts: dict[str, dict],
    ) -> list[dict[str, str]]:
        """Generate actionable recommendations based on triage data."""
        recommendations = []

        crit = sev_counts.get("critical", 0)
        warn = sev_counts.get("warning", 0)

        if crit > 20:
            recommendations.append({
                "priority": "HIGH",
                "message": f"{crit} critical errors — prioritize triaging these first. "
                           f"Focus on data-loss and process-violation categories."
            })
        if warn > 10:
            recommendations.append({
                "priority": "MEDIUM",
                "message": f"{warn} warnings — schedule for next triage pass."
            })

        # Category-specific recommendations
        for cat, data in cat_counts.items():
            if cat == "data-loss" and data["critical"] > 0:
                recommendations.append({
                    "priority": "CRITICAL",
                    "message": f"{data['critical']} data-loss incidents — implement "
                               f"verification checks before destructive operations."
                })
            if cat == "process-violation" and data["count"] > 3:
                recommendations.append({
                    "priority": "HIGH",
                    "message": f"{data['count']} process violations — review SOP enforcement "
                               f"and compliance sentinel effectiveness."
                })
            if cat == "shell-escaping" and data["count"] > 3:
                recommendations.append({
                    "priority": "MEDIUM",
                    "message": f"{data['count']} shell-escaping issues — ensure all "
                               f"basher commands use _tmp.py patterns."
                })
            if cat == "C-extension" and data["count"] > 0:
                recommendations.append({
                    "priority": "MEDIUM",
                    "message": f"{data['count']} C-extension issues — verify cp314t wheels "
                               f"before relying on compiled packages."
                })

        # Owner-specific recommendations
        for owner, data in owner_counts.items():
            if data["critical"] > 5:
                recommendations.append({
                    "priority": "HIGH",
                    "message": f"Owner '{owner}' has {data['critical']} critical issues — "
                               f"schedule focused remediation."
                })

        return recommendations


# =====================================================================
# Convenience Functions
# =====================================================================

def load_triage_system(error_log_path: (str | None) = None) -> ErrorTriageSystem:
    """Load and return a ready-to-use ErrorTriageSystem."""
    ets = ErrorTriageSystem(error_log_path)
    ets.load()
    return ets


def print_triage_summary(error_log_path: (str | None) = None) -> None:
    """Quick one-liner to load and print triage summary."""
    ets = load_triage_system(error_log_path)
    ets.print_summary()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
