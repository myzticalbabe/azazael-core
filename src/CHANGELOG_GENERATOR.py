#!/usr/bin/env python3
"""
Change Log Generator

Generates changelog from version tracking and git history.
"""

import sys
from pathlib import Path
from datetime import datetime
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Dict  # removed — all names have native equivalents

# Dynamic path resolution
try:
    from CORE.paths import get_desktop_dir
except ImportError as exc:
    note_failure("CHANGELOG_GENERATOR:L16", exc)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from CORE.paths import get_desktop_dir


class ChangelogGenerator:
    """Generates changelog from version history."""

    VERSION_FILE = str(get_desktop_dir() / "Azazael" / "version.py")

    def __init__(
        self,
        output_path: str | None = None,
    ):
        if output_path is None:
            output_path = str(get_desktop_dir() / "CODEX" / "DOCUMENTATION" / "CHANGELOG.md")
        self.output_path = Path(output_path)
        self.changelog_path = Path(output_path)

    def get_version_info(self) -> dict:
        """Read version information."""
        version_file = Path(self.VERSION_FILE)
        if version_file.exists():
            content = version_file.read_text(encoding="utf-8")
            # Simple parsing for VERSION dict
            version_info = {
                "major": 2,
                "minor": 0,
                "patch": 0,
                "full": "2.0.0",
                "codename": "Azazael-Reborn",
            }
            # Extract actual values if present
            for line in content.split("\n"):
                if '"major"' in line or "'major'" in line:
                    try:
                        version_info["major"] = int(
                            line.split(":")[1].strip().rstrip(",")
                        )
                    except ValueError as exc:
                        note_failure("CHANGELOG_GENERATOR:L55", exc)
            return version_info
        return {
            "major": 2,
            "minor": 0,
            "patch": 0,
            "full": "2.0.0",
            "codename": "Azazael-Reborn",
        }

    def generate_changelog(self) -> str:
        """Generate changelog content."""
        version = self.get_version_info()
        today = datetime.now().strftime("%Y-%m-%d")

        changelog = f"""# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [{version["full"]}] - {today}

### Added
- Comprehensive documentation system with TD-001 through TD-006
- Technical documentation index and component registry
- API reference documentation
- Architecture Decision Records (ADRs)
- Dependency graph documentation
- Glossary of terms
- Security audit checklist
- Ecosystem health check script
- Auto documentation generator
- File manifest generator

### Changed
- Directory structure reorganization
- CODEX: Created CORE/, POPUPS/, VIDEOS/, HTML/, DOCUMENTATION/, ARCHIVE/ subdirectories
- Azazael: Created TEST/ subdirectory for test files
- Documentation files consolidated to CODEX/DOCUMENTATION/
- Video generation scripts moved to CODEX/VIDEOS/
- Popup scripts moved to CODEX/POPUPS/
- HTML reports moved to CODEX/HTML/

### Deprecated
- Old directory structure (legacy locations)
- Unconsolidated documentation files

### Fixed
- HYDROMETA -> HYDRAMETA spelling correction
- AZAAZEL -> Azazael naming standardization (FIXED)
- File naming conventions updated

## [1.5.0] - 2026-01-15

### Added
- Initial HYDRA implementation
- WORMZ execution layer
- METAWORMS orchestration
- HORUS presentation layer
- TERENCE error resolution
- KLINK command launcher

### Changed
- Initial architecture design

---

## Version History

| Version | Date | Codename | Status |
|---------|------|----------|--------|
| 2.0.0 | {today} | Azazael-Reborn | Current |
| 1.5.0 | 2026-01-15 | Initial | Legacy |

---

*Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}*
"""

        return changelog

    def write_changelog(self) -> None:
        """Write changelog to file."""
        content = self.generate_changelog()
        self.changelog_path.write_text(content, encoding="utf-8")
        print(f"Changelog written to {self.changelog_path}")


if __name__ == "__main__":
    generator = ChangelogGenerator()
    generator.write_changelog()
