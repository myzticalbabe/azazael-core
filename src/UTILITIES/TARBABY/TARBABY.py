# coding: utf-8
"""
TARBABY - Binary Compatibility & Version Manager
===============================================

Robust binary management for Python version switching and compatibility.
Handles .json, .yaml, .pickle, .parquet binaries and version interoperability.

Usage:
    >>> from TARBABY import Tarbaby, BinaryFormat, VersionManager
    >>> tarbaby = Tarbaby()
    >>> tarbaby.convert("data.json", "data.yaml")
"""

import json
import pickle
import sys
import tarfile
import zipfile
import shutil
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, List
from enum import Enum
from datetime import datetime
import os
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Dynamic path resolution
try:
    from CORE.paths import get_src_dir
except ImportError as exc:
    note_failure("TARBABY:L33", exc)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from CORE.paths import get_src_dir

DEFAULT_Azazael_ROOT = str(get_src_dir())


class BinaryFormat(Enum):
    """Supported binary formats."""

    JSON = "json"
    YAML = "yaml"
    PICKLE = "pickle"
    PARQUET = "parquet"
    TAR = "tar"
    ZIP = "zip"
    GZIP = "gz"
    BYTES = "bytes"


class PythonVersion(Enum):
    """Python version mappings."""

    PY312 = "3.12"
    PY313 = "3.13"
    PY314 = "3.14"


@dataclass
class BinaryMetadata:
    """Metadata for binary files."""

    path: str
    format: BinaryFormat
    size_bytes: int
    checksum: str
    created: str
    modified: str
    version: str = "1.0.0"
    python_version: str = ""
    dependencies: list[str] = field(default_factory=list)


@dataclass
class MigrationResult:
    """Result of a migration operation."""

    source: str
    destination: str
    success: bool
    files_migrated: int
    errors: list[str]
    duration_ms: float


class VersionManager:
    """Manage Python version compatibility."""

    def __init__(self):
        self.versions: dict[str, dict] = {}
        self.compat_matrix: dict[str, list[str]] = {}

    def add_version(self, name: str, path: str, version: str) -> bool:
        """Add a Python version."""
        self.versions[name] = {
            "path": path,
            "version": version,
            "modules": self._scan_modules(path),
        }
        return True

    def _scan_modules(self, path: str) -> list[str]:
        """Scan for Python modules."""
        modules: list[str] = []
        p = Path(path)
        if not p.exists():
            return modules
        for py_file in p.rglob("*.py"):
            modules.append(str(py_file.relative_to(p)))
        for pyc_file in p.rglob("*.pyc"):
            modules.append(str(pyc_file.relative_to(p)))
        return modules

    def get_compatible_versions(self, version: str) -> list[str]:
        """Get compatible Python versions."""
        compat = {
            "3.12": ["3.12", "3.13", "3.14"],
            "3.13": ["3.13", "3.14"],
            "3.14": ["3.14"],
        }
        return compat.get(version, [])


class BinaryConverter:
    """Convert between binary formats."""

    @staticmethod
    def convert(data: Any, from_format: BinaryFormat, to_format: BinaryFormat) -> Any:
        """Convert data between formats."""
        # First, serialize to intermediate
        if from_format == BinaryFormat.JSON:
            if isinstance(data, str):
                intermediate = json.loads(data)
            else:
                intermediate = data
        elif from_format == BinaryFormat.YAML:
            import yaml

            intermediate = yaml.safe_load(data) if isinstance(data, str) else data
        elif from_format == BinaryFormat.PICKLE:
            intermediate = pickle.loads(data) if isinstance(data, bytes) else data
        else:
            intermediate = data

        # Then convert to target format
        if to_format == BinaryFormat.JSON:
            return json.dumps(intermediate, indent=2)
        elif to_format == BinaryFormat.YAML:
            import yaml

            return yaml.dump(intermediate)
        elif to_format == BinaryFormat.PICKLE:
            return pickle.dumps(intermediate)
        elif to_format == BinaryFormat.BYTES:
            return str(intermediate).encode("utf-8")

        return intermediate

    @staticmethod
    def detect_format(filepath: str) -> BinaryFormat:
        """Detect binary format from file extension."""
        ext = Path(filepath).suffix.lower()
        format_map = {
            ".json": BinaryFormat.JSON,
            ".yaml": BinaryFormat.YAML,
            ".yml": BinaryFormat.YAML,
            ".pkl": BinaryFormat.PICKLE,
            ".pickle": BinaryFormat.PICKLE,
            ".parquet": BinaryFormat.PARQUET,
            ".tar": BinaryFormat.TAR,
            ".zip": BinaryFormat.ZIP,
            ".gz": BinaryFormat.GZIP,
        }
        return format_map.get(ext, BinaryFormat.BYTES)


class Tarbaby:
    """
    TARBABY - Binary Compatibility Manager

    Features:
        - Binary format conversion (.json, .yaml, .pickle, etc.)
        - Python version migration and compatibility
        - Module extraction and rebuilding
        - Dependency tracking
        - Checksum verification
    """

    VERSION = "2.0.0"

    @staticmethod
    def _default_root() -> str:
        return os.environ.get("AZAZAEL_ROOT", str(Path(__file__).resolve().parents[3]))

    def __init__(self, base_path: (str | None) = None):
        if base_path is None:
            base_path = self._default_root()
        self.base_path = Path(base_path)
        self.output_path = self.base_path / "output"
        self.temp_path = self.base_path / "temp"
        self.metadata_path = self.base_path / "metadata"

        # Create directories
        self.output_path.mkdir(parents=True, exist_ok=True)
        self.temp_path.mkdir(parents=True, exist_ok=True)
        self.metadata_path.mkdir(parents=True, exist_ok=True)

        self.version_manager = VersionManager()
        self.converter = BinaryConverter()
        self.migration_log: list[dict] = []

    def migrate(
        self,
        source_name: str,
        source_path: str,
        dest_path: str,
        target_version: str = "3.14",
    ) -> MigrationResult:
        """Migrate Python modules between versions."""
        start = datetime.now()
        source = Path(source_path)
        dest = Path(dest_path)

        if not source.exists():
            return MigrationResult(
                source=source_path,
                destination=dest_path,
                success=False,
                files_migrated=0,
                errors=[f"Source path does not exist: {source_path}"],
                duration_ms=0,
            )

        dest.mkdir(parents=True, exist_ok=True)
        files_migrated = 0
        errors: list[str] = []

        # Scan and migrate files
        for file_path in source.rglob("*"):
            if file_path.is_file():
                rel_path = file_path.relative_to(source)
                dest_file = dest / rel_path

                try:
                    # Convert Python version-specific syntax
                    content = self._convert_for_version(file_path, target_version)
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    dest_file.write_bytes(content)
                    files_migrated += 1
                except Exception as e:
                    note_failure("TARBABY:L252", e)
                    errors.append(f"{rel_path}: {str(e)}")

        # Create metadata
        self._save_metadata(source_name, source_path, dest_path, files_migrated)

        duration = (datetime.now() - start).total_seconds() * 1000

        result = MigrationResult(
            source=source_path,
            destination=dest_path,
            success=len(errors) == 0,
            files_migrated=files_migrated,
            errors=errors,
            duration_ms=duration,
        )

        self.migration_log.append(
            {
                "timestamp": datetime.now().isoformat(),
                "source": source_name,
                "files": files_migrated,
                "success": result.success,
            }
        )

        return result

    def _convert_for_version(self, filepath: Path, target_version: str) -> bytes:
        """Convert file for target Python version."""
        if filepath.suffix == ".py":
            content = filepath.read_text(encoding="utf-8")

            # Remove 3.14+ only syntax
            # No backward-compat transforms needed — project targets 3.14t

            return content.encode("utf-8")

        return filepath.read_bytes()



    def pack(
        self,
        source_path: str,
        output_name: str,
        format: BinaryFormat = BinaryFormat.TAR,
    ) -> str:
        """Pack directory into binary archive."""
        source = Path(source_path)
        output_file = self.output_path / f"{output_name}.{format.value}"

        if format == BinaryFormat.TAR:
            with tarfile.open(output_file, "w:gz") as tar:
                tar.add(source, arcname=source.name)
        elif format == BinaryFormat.ZIP:
            shutil.make_archive(str(self.output_path / output_name), "zip", source)
            output_file = self.output_path / f"{output_name}.zip"

        # Create metadata
        self._create_archive_metadata(output_file, source)

        return str(output_file)

    def unpack(self, archive_path: str, dest_path: (str | None) = None) -> str:
        """Extract binary archive."""
        archive = Path(archive_path)
        dest = Path(dest_path) if dest_path else self.temp_path / archive.stem
        dest.mkdir(parents=True, exist_ok=True)

        if archive.suffix == ".gz" or str(archive).endswith(".tar.gz"):
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(dest)
        elif archive.suffix == ".zip":
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(dest)

        return str(dest)

    def convert_format(
        self,
        input_path: str,
        output_path: str,
        target_format: BinaryFormat = BinaryFormat.YAML,
    ) -> bool:
        """Convert file to another format."""
        try:
            input_format = self.converter.detect_format(input_path)
            input_data = self._read_file(input_path, input_format)

            output_data = self.converter.convert(
                input_data, input_format, target_format
            )

            Path(output_path).write_text(str(output_data), encoding="utf-8")
            return True
        except Exception as e:
            print(f"Conversion error: {e}")
            return False

    def _read_file(self, filepath: str, format: BinaryFormat) -> Any:
        """Read file based on format."""
        if format == BinaryFormat.JSON:
            return json.loads(Path(filepath).read_text(encoding="utf-8"))
        elif format == BinaryFormat.YAML:
            import yaml

            return yaml.safe_load(Path(filepath).read_text(encoding="utf-8"))
        elif format == BinaryFormat.PICKLE:
            return pickle.loads(Path(filepath).read_bytes())
        return Path(filepath).read_bytes()

    def _create_archive_metadata(self, archive: Path, source: Path):
        """Create metadata for archive."""
        metadata = {
            "archive": archive.name,
            "source": str(source),
            "created": datetime.now().isoformat(),
            "size_bytes": archive.stat().st_size,
            "checksum": self._calculate_checksum(archive),
            "files": self._list_contents(source),
        }

        meta_file = self.metadata_path / f"{archive.stem}.meta.json"
        meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def _save_metadata(self, name: str, source: str, dest: str, files: int):
        """Save migration metadata."""
        meta = {
            "name": name,
            "source": source,
            "destination": dest,
            "files_migrated": files,
            "timestamp": datetime.now().isoformat(),
        }

        meta_file = self.metadata_path / f"{name.lower().replace(' ', '_')}.meta.json"
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _calculate_checksum(self, filepath: Path) -> str:
        """Calculate SHA256 checksum."""
        sha256 = hashlib.sha256()
        sha256.update(filepath.read_bytes())
        return sha256.hexdigest()

    def _list_contents(self, path: Path) -> list[dict]:
        """list directory contents."""
        contents: list[dict] = []
        for item in path.rglob("*"):
            if item.is_file():
                contents.append(
                    {"path": str(item.relative_to(path)), "size": item.stat().st_size}
                )
        return contents

    def build_binary(
        self, data: Any, name: str, format: BinaryFormat = BinaryFormat.PICKLE
    ) -> str:
        """Build binary from data."""
        output_file = self.output_path / f"{name}.{format.value}"

        if format == BinaryFormat.JSON:
            output_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        elif format == BinaryFormat.YAML:
            import yaml

            output_file.write_text(yaml.dump(data), encoding="utf-8")
        elif format == BinaryFormat.PICKLE:
            output_file.write_bytes(pickle.dumps(data))

        return str(output_file)

    def extract_binary(self, filepath: str) -> Any:
        """Extract data from binary."""
        format = self.converter.detect_format(filepath)
        return self._read_file(filepath, format)

    def get_status(self) -> dict:
        """Get TARBABY status."""
        return {
            "version": self.VERSION,
            "base_path": str(self.base_path),
            "output_files": len(list(self.output_path.glob("*"))),
            "migration_count": len(self.migration_log),
            "migrations": self.migration_log[-10:],
            "versions_registered": len(self.version_manager.versions),
        }

    def list_versions(self) -> dict:
        """list registered Python versions."""
        return self.version_manager.versions


def main():
    """TARBABY CLI."""
    parser = argparse.ArgumentParser(
        description="TARBABY - Binary Compatibility Manager"
    )
    parser.add_argument(
        "--version", action="version", version=f"TARBABY v{Tarbaby.VERSION}"
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Migrate command
    migrate_parser = subparsers.add_parser("migrate", help="Migrate Python modules")
    migrate_parser.add_argument("source_name", help="Name of source")
    migrate_parser.add_argument("source_path", help="Source path")
    migrate_parser.add_argument("dest_path", help="Destination path")
    migrate_parser.add_argument(
        "--version", default="3.14", help="Target Python version"
    )

    # Pack command
    pack_parser = subparsers.add_parser("pack", help="Pack directory to archive")
    pack_parser.add_argument("source", help="Source directory")
    pack_parser.add_argument("name", help="Output name")
    pack_parser.add_argument("--format", choices=["tar", "zip"], default="tar")

    # Unpack command
    unpack_parser = subparsers.add_parser("unpack", help="Extract archive")
    unpack_parser.add_argument("archive", help="Archive file")
    unpack_parser.add_argument("--dest", help="Destination path")

    # Convert command
    convert_parser = subparsers.add_parser("convert", help="Convert file format")
    convert_parser.add_argument("input", help="Input file")
    convert_parser.add_argument("output", help="Output file")
    convert_parser.add_argument(
        "--format", choices=["json", "yaml", "pickle"], default="yaml"
    )

    # Status command
    subparsers.add_parser("status", help="Show status")

    args = parser.parse_args()

    tarbaby = Tarbaby()

    if args.command == "migrate":
        result = tarbaby.migrate(
            args.source_name, args.source_path, args.dest_path, args.version
        )
        print(f"Migration: {'SUCCESS' if result.success else 'FAILED'}")
        print(f"Files migrated: {result.files_migrated}")
        if result.errors:
            print(f"Errors: {len(result.errors)}")

    elif args.command == "pack":
        output = tarbaby.pack(
            args.source,
            args.name,
            BinaryFormat.TAR if args.format == "tar" else BinaryFormat.ZIP,
        )
        print(f"Packed to: {output}")

    elif args.command == "unpack":
        dest = tarbaby.unpack(args.archive, args.dest)
        print(f"Extracted to: {dest}")

    elif args.command == "convert":
        success = tarbaby.convert_format(
            args.input,
            args.output,
            BinaryFormat.YAML
            if args.format == "yaml"
            else BinaryFormat.JSON
            if args.format == "json"
            else BinaryFormat.PICKLE,
        )
        print(f"Converted: {'SUCCESS' if success else 'FAILED'}")

    elif args.command == "status":
        status = tarbaby.get_status()
        print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
