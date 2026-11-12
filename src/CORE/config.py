# coding: utf-8
"""
CORE/config.py — Centralized Configuration Loader for Azazael.

Loads azazael.yaml from project root with environment variable overrides.
Provides typed accessors for all config sections.

Usage:
    from CORE.config import get_config, AzazaelConfig
    cfg = get_config()
    print(cfg.pams.url)
    print(cfg.circuit_breaker.failure_threshold)
    print(cfg.clusters.default_timeout)

Environment Variable Overrides:
    AZAZAEL_ROOT       -> paths.project_root
    AZAZAEL_OUTPUT     -> paths.output_dir
    AZAZAEL_ARCHIVE    -> paths.archive_dir
    PAMS_URL           -> pams.url
    AZAZAEL_LOG_LEVEL  -> observability.logging.level

Version: 1.0.0
"""

import os
import threading
from pathlib import Path
# from typing import Optional, Dict, List  # removed — all names have native equivalents
from dataclasses import dataclass, field
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

VERSION = "1.0.0"

# -- Module-level locks for thread safety on 3.14t free-threading ------
_CONFIG_LOCK = threading.Lock()

# -- Try to import yaml ----------------------------------------------
try:
    import yaml
    _HAS_YAML = True
except ImportError as exc:
    note_failure("config:L40", exc)
    _HAS_YAML = False


__all__ = [
    "AzazaelConfig", "PathsConfig", "PamsConfig", "MemoryBusConfig",
    "CircuitBreakerConfig", "ClustersConfig", "CategoryConfig",
    "FreeThreadingConfig", "LoggingConfig", "MetricsConfig",
    "ObservabilityConfig", "SecurityConfig", "MemoryStoreConfig",
    "BackupConfig", "BuildConfig",
    "get_config", "get_warnings", "reset_config", "VERSION",
]

# =======================================================================
# CONFIG DATA CLASSES
# =======================================================================

@dataclass
class PathsConfig:
    """Path configuration section."""
    project_root: (str | None) = None
    output_dir: (str | None) = None
    archive_dir: (str | None) = None
    desktop_dir: (str | None) = None

    @classmethod
    def from_dict(cls, d: dict) -> "PathsConfig":
        with _CONFIG_LOCK:
            env_root = os.environ.get("AZAZAEL_ROOT", "")
            env_output = os.environ.get("AZAZAEL_OUTPUT", "")
            env_archive = os.environ.get("AZAZAEL_ARCHIVE", "")
        return cls(
            project_root=env_root or d.get("project_root"),
            output_dir=env_output or d.get("output_dir"),
            archive_dir=env_archive or d.get("archive_dir"),
            desktop_dir=d.get("desktop_dir"),
        )


@dataclass
class PamsConfig:
    """PAMS / Memory configuration section."""
    url: str = "http://localhost:8420"
    session_prefix: str = "azazael"
    poll_interval_sec: float = 1.0
    use_local_fallback: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "PamsConfig":
        with _CONFIG_LOCK:
            env_url = os.environ.get("PAMS_URL", "")
        return cls(
            url=env_url or d.get("url", "http://localhost:8420"),
            session_prefix=d.get("session_prefix", "azazael"),
            poll_interval_sec=d.get("poll_interval_sec", 1.0),
            use_local_fallback=d.get("use_local_fallback", True),
        )


@dataclass
class MemoryBusConfig:
    """MemoryBus configuration section."""
    enabled: bool = True
    session_id: (str | None) = None
    default_topic_prefix: str = "azazael"

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryBusConfig":
        return cls(
            enabled=d.get("enabled", True),
            session_id=d.get("session_id"),
            default_topic_prefix=d.get("default_topic_prefix", "azazael"),
        )


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration section."""
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_requests: int = 3
    retry_base_delay: float = 0.1
    retry_max_delay: float = 60.0
    retry_backoff: float = 2.0
    dead_letter_max: int = 1000
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "CircuitBreakerConfig":
        return cls(
            failure_threshold=d.get("failure_threshold", 5),
            recovery_timeout=d.get("recovery_timeout", 30.0),
            half_open_max_requests=d.get("half_open_max_requests", 3),
            retry_base_delay=d.get("retry_base_delay", 0.1),
            retry_max_delay=d.get("retry_max_delay", 60.0),
            retry_backoff=d.get("retry_backoff", 2.0),
            dead_letter_max=d.get("dead_letter_max", 1000),
            enabled=d.get("enabled", True),
        )

    def to_circuit_config(self):
        """Convert to CORE.circuit_breaker.CircuitConfig.

        Returns a CircuitConfig with validated values, falling back to
        safe defaults if the import fails or values are invalid.
        """
        try:
            from CORE.circuit_breaker import CircuitConfig
            return CircuitConfig(
                failure_threshold=max(1, self.failure_threshold),
                recovery_timeout=max(1.0, self.recovery_timeout),
                half_open_max_requests=max(1, self.half_open_max_requests),
                retry_base_delay=max(0.01, self.retry_base_delay),
                retry_max_delay=max(self.retry_base_delay, self.retry_max_delay),
                retry_backoff=max(1.0, self.retry_backoff),
                dead_letter_max=max(10, self.dead_letter_max),
            )
        except ImportError as exc:
            # Fallback — construct from raw defaults if CircuitConfig not importable
            # Fallback — construct from raw defaults if CircuitConfig not importable
            note_failure("config:L157", exc)
            from dataclasses import dataclass
            @dataclass
            class _FallbackCircuitConfig:
                failure_threshold: int = 5
                recovery_timeout: float = 30.0
                half_open_max_requests: int = 3
                retry_base_delay: float = 0.1
                retry_max_delay: float = 60.0
                retry_backoff: float = 2.0
                dead_letter_max: int = 1000
            return _FallbackCircuitConfig()


@dataclass
class ClustersConfig:
    """Cluster execution configuration section."""
    default_timeout: int = 60
    max_parallel_worms: int = 8
    retry_enabled: bool = True
    max_retries: int = 3
    record_to_pams: bool = True
    partial_failure_threshold: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> "ClustersConfig":
        return cls(
            default_timeout=d.get("default_timeout", 60),
            max_parallel_worms=d.get("max_parallel_worms", 8),
            retry_enabled=d.get("retry_enabled", True),
            max_retries=d.get("max_retries", 3),
            record_to_pams=d.get("record_to_pams", True),
            partial_failure_threshold=d.get("partial_failure_threshold", 0.0),
        )


@dataclass
class CategoryConfig:
    """Per-nature execution configuration override section."""
    enabled: bool = True
    timeout: (int | None) = None  # None = use cluster default
    max_retries: (int | None) = None
    parallel: (bool | None) = None
    feeds_into: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "CategoryConfig":
        return cls(
            enabled=d.get("enabled", True),
            timeout=d.get("timeout"),
            max_retries=d.get("max_retries"),
            parallel=d.get("parallel"),
            feeds_into=d.get("feeds_into", []),
        )

    def merge_with_cluster(self, cluster_cfg: "ClustersConfig") -> dict:
        """Merge per-nature overrides with cluster defaults, validated.

        Returns validated merged config with safe defaults for all fields.
        Does not mutate input configs.
        """
        return {
            "timeout": max(1, self.timeout or cluster_cfg.default_timeout),
            "max_retries": max(0, self.max_retries or cluster_cfg.max_retries),
            "parallel": bool(self.parallel) if self.parallel is not None else False,
        }


@dataclass
class FreeThreadingConfig:
    """Free-threading configuration section."""
    enabled: str = "auto"
    max_workers: (int | None) = None

    @classmethod
    def from_dict(cls, d: dict) -> "FreeThreadingConfig":
        return cls(
            enabled=d.get("enabled", "auto"),
            max_workers=d.get("max_workers"),
        )


@dataclass
class LoggingConfig:
    """Logging configuration section."""
    level: str = "INFO"
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    file: (str | None) = None

    @classmethod
    def from_dict(cls, d: dict) -> "LoggingConfig":
        with _CONFIG_LOCK:
            env_level = os.environ.get("AZAZAEL_LOG_LEVEL", "")
        return cls(
            level=env_level or d.get("level", "INFO"),
            format=d.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s"),
            file=d.get("file"),
        )


@dataclass
class MetricsConfig:
    """Metrics configuration section."""
    enabled: bool = True
    history_size: int = 1000

    @classmethod
    def from_dict(cls, d: dict) -> "MetricsConfig":
        return cls(
            enabled=d.get("enabled", True),
            history_size=d.get("history_size", 1000),
        )


@dataclass
class ObservabilityConfig:
    """Observability configuration section."""
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "ObservabilityConfig":
        return cls(
            logging=LoggingConfig.from_dict(d.get("logging", {})),
            metrics=MetricsConfig.from_dict(d.get("metrics", {})),
        )


@dataclass
class SecurityConfig:
    """Security configuration section."""
    secret_rotation_days: int = 30
    audit_enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "SecurityConfig":
        return cls(
            secret_rotation_days=d.get("secret_rotation_days", 30),
            audit_enabled=d.get("audit_enabled", True),
        )


@dataclass
class MemoryStoreConfig:
    """Memory store (SQLite) configuration section."""
    auto_vacuum: bool = True
    vacuum_interval_hours: float = 24.0
    max_size_mb_warn: int = 400

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryStoreConfig":
        return cls(
            auto_vacuum=d.get("auto_vacuum", True),
            vacuum_interval_hours=d.get("vacuum_interval_hours", 24.0),
            max_size_mb_warn=d.get("max_size_mb_warn", 400),
        )


@dataclass
class BackupConfig:
    """Backup configuration section."""
    interval_hours: int = 24
    keep_count: int = 7
    target_dir: (str | None) = None

    @classmethod
    def from_dict(cls, d: dict) -> "BackupConfig":
        return cls(
            interval_hours=d.get("interval_hours", 24),
            keep_count=d.get("keep_count", 7),
            target_dir=d.get("target_dir"),
        )


@dataclass
class BuildConfig:
    """Nuitka build configuration section."""
    python_version: str = "3.14"
    output_dir: str = "G:/AZAZAEL_Standalone"
    nofollow_imports: list[str] = field(default_factory=lambda: ["torch", "optree", "openvino"])

    @classmethod
    def from_dict(cls, d: dict) -> "BuildConfig":
        return cls(
            python_version=d.get("python_version", "3.14"),
            output_dir=d.get("output_dir", "G:/AZAZAEL_Standalone"),
            nofollow_imports=d.get("nofollow_imports", ["torch", "optree", "openvino"]),
        )


# =======================================================================
# MAIN CONFIG CLASS
# =======================================================================

@dataclass
class AzazaelConfig:
    """Complete Azazael configuration loaded from azazael.yaml."""
    version: str = "1.0.0"
    paths: PathsConfig = field(default_factory=PathsConfig)
    pams: PamsConfig = field(default_factory=PamsConfig)
    memory_bus: MemoryBusConfig = field(default_factory=MemoryBusConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    clusters: ClustersConfig = field(default_factory=ClustersConfig)
    categories: dict[str, CategoryConfig] = field(default_factory=dict)
    free_threading: FreeThreadingConfig = field(default_factory=FreeThreadingConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    memory_store: MemoryStoreConfig = field(default_factory=MemoryStoreConfig)
    build: BuildConfig = field(default_factory=BuildConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "AzazaelConfig":
        # 'categories' is canonical; 'natures' read as legacy fallback (rip-out, Oct 2026).
        categories_raw = d.get("categories", d.get("natures", {}))
        categories = {}
        if isinstance(categories_raw, dict):
            for category_name, category_cfg in categories_raw.items():
                if isinstance(category_cfg, dict):
                    categories[category_name] = CategoryConfig.from_dict(category_cfg)
        return cls(
            version=d.get("version", "1.0.0"),
            paths=PathsConfig.from_dict(d.get("paths", {})),
            pams=PamsConfig.from_dict(d.get("pams", {})),
            memory_bus=MemoryBusConfig.from_dict(d.get("memory_bus", {})),
            circuit_breaker=CircuitBreakerConfig.from_dict(d.get("circuit_breaker", {})),
            clusters=ClustersConfig.from_dict(d.get("clusters", {})),
            categories=categories,
            free_threading=FreeThreadingConfig.from_dict(d.get("free_threading", {})),
            observability=ObservabilityConfig.from_dict(d.get("observability", {})),
            security=SecurityConfig.from_dict(d.get("security", {})),
            backup=BackupConfig.from_dict(d.get("backup", {})),
            memory_store=MemoryStoreConfig.from_dict(d.get("memory_store", {})),
            build=BuildConfig.from_dict(d.get("build", {})),
        )

    def get_category_config(self, category_name: str) -> CategoryConfig:
        """Get config for a specific category, returning defaults if not overridden."""
        return self.categories.get(category_name, CategoryConfig())

    def validate(self) -> list[str]:
        """Validate configuration and return list of warnings."""
        warnings = []
        if self.clusters.default_timeout <= 0:
            warnings.append("clusters.default_timeout must be positive")
        if self.clusters.max_parallel_worms <= 0:
            warnings.append("clusters.max_parallel_worms must be positive")
        if self.circuit_breaker.failure_threshold <= 0:
            warnings.append("circuit_breaker.failure_threshold must be positive")
        if self.circuit_breaker.recovery_timeout <= 0:
            warnings.append("circuit_breaker.recovery_timeout must be positive")
        if self.security.secret_rotation_days <= 0:
            warnings.append("security.secret_rotation_days must be positive")
        return warnings


# =======================================================================
# CONFIG LOADING
# =======================================================================

def _find_config_file() -> (Path | None):
    """Find azazael.yaml from the project root with validation.

    Search order:
      1. Relative to this file's location (3 levels up = azazael/)
      2. Parent of src/ (for tests running from src/)
      3. Current working directory (CWD guard — works from any subdirectory)
      4. Walk up from CWD looking for azazael.yaml in parent dirs

    Returns None (not raises) if no valid config file found.
    """
    candidates: list[Path] = []

    try:
        this_file = Path(__file__).resolve()
        # 1. 3 levels up from src/CORE/config.py = azazael/
        candidates.append(this_file.parent.parent.parent / "azazael.yaml")
        # 2. 2 levels up from src/CORE/config.py = src/
        candidates.append(this_file.parent.parent / "azazael.yaml")
    except (OSError, RuntimeError) as exc:
        note_failure("config:L436", exc)

    # 3. Current working directory
    candidates.append(Path.cwd() / "azazael.yaml")

    # 4. Walk up from CWD looking for azazael.yaml (max 5 parent levels)
    cwd = Path.cwd()
    for i, parent in enumerate(cwd.parents):
        if i >= 5:
            break
        candidates.append(parent / "azazael.yaml")

    for path in candidates:
        try:
            if path.is_file():
                return path
        except (OSError, RuntimeError) as exc:
            note_failure("config:L453", exc)
            continue

    return None


def _load_yaml(path: Path) -> dict:
    """Load and parse azazael.yaml."""
    if not _HAS_YAML:
        raise ImportError(
            "PyYAML is required to load azazael.yaml. "
            "Install with: pip install pyyaml"
        )
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"azazael.yaml must contain a mapping, got {type(data).__name__}")
    return data


# -- Global config singleton ------------------------------------------
_config: (AzazaelConfig | None) = None
_config_warnings: list[str] = []
_config_singleton_lock = threading.Lock()


def get_config(reload: bool = False) -> AzazaelConfig:
    """Get the global AzazaelConfig singleton.

    Loads azazael.yaml on first call, caches result.
    set reload=True to force re-read.

    Returns:
        AzazaelConfig instance (never None — returns defaults if file not found)

    Raises:
        ImportError: if PyYAML is not installed and config file exists
    """
    global _config, _config_warnings

    with _config_singleton_lock:
        if _config is not None and not reload:
            return _config

        # Entire config loading block under lock to prevent TOCTOU race
        config_file = _find_config_file()

        if config_file:
            try:
                data = _load_yaml(config_file)
                new_config = AzazaelConfig.from_dict(data)
                new_warnings = new_config.validate()
            except ImportError as exc:
                note_failure("config:L505", exc)
                new_config = AzazaelConfig()
                new_warnings = ["PyYAML not installed — using default config. Install: pip install pyyaml"]
            except Exception as e:
                note_failure("config:L508", e)
                new_config = AzazaelConfig()
                new_warnings = [f"Failed to load azazael.yaml: {e} — using defaults"]
        else:
            new_config = AzazaelConfig()
            new_warnings = ["azazael.yaml not found at project root — using defaults"]

        _config = new_config
        _config_warnings = new_warnings

    return _config


def get_warnings() -> list[str]:
    """Get validation warnings from the last config load.

    Returns a copy of the warnings list to prevent mutation by callers.
    Thread-safe: reads _config and _config_warnings under lock.
    """
    global _config_warnings
    with _config_singleton_lock:
        if _config is None:
            # Release lock, call get_config, re-acquire
            pass
        else:
            return list(_config_warnings)
    # Lock released — safe to call get_config (it manages its own locking)
    get_config()
    with _config_singleton_lock:
        return list(_config_warnings)


def reset_config():
    """Reset the config singleton (useful for testing).

    Thread-safe via global reassignment under lock. All subsequent get_config()
    calls will re-load from disk.
    """
    global _config, _config_warnings
    with _config_singleton_lock:
        _config = None
        _config_warnings = []


# =======================================================================
# MAIN
# =======================================================================

if __name__ == "__main__":
    cfg = get_config()
    print(f"CORE/config.py v{VERSION}")
    print(f"  Config file: {_find_config_file() or 'NOT FOUND (using defaults)'}")
    print(f"  Version: {cfg.version}")
    print(f"  PAMS URL: {cfg.pams.url}")
    print(f"  Circuit Breaker: enabled={cfg.circuit_breaker.enabled}, "
          f"threshold={cfg.circuit_breaker.failure_threshold}")
    print(f"  Clusters: timeout={cfg.clusters.default_timeout}s, "
          f"max_parallel={cfg.clusters.max_parallel_worms}")
    print(f"  Build: Python {cfg.build.python_version}, "
          f"output={cfg.build.output_dir}")
    print(f"  Free-threading: {cfg.free_threading.enabled}")

    warnings = get_warnings()
    if warnings:
        print(f"\n  Warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    - {w}")
    else:
        print("\n  Validation: OK (no warnings)")
