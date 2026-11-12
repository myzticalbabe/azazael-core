"""AOA Arm Registry — Registration and discovery of all neural architecture Arms.

Analogous to Azazael's worm_registry.py, this module provides the central
registry for all sub-system Arms (Azazael, Libra, CODEX, EveUnleashed, etc.).

Architecture:
    BaseArm (ABC) — Standard interface all Arms must implement
    ArmRegistry   — Central registry with discovery and lifecycle management
    register_arm  — Decorator for declarative Arm registration
"""

import importlib
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
from pathlib import Path

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class ArmCapability:
    """Describes a single capability an Arm exposes."""
    name: str
    description: str
    input_schema: Optional[dict] = None
    output_schema: Optional[dict] = None
    category: str = "general"


@dataclass
class ArmMetadata:
    """Metadata for a registered Arm."""
    arm_id: str
    name: str
    version: str
    description: str
    category: str = "quartet"  # quartet, support, infrastructure
    priority: int = 5          # 1-10, higher = more critical
    dependencies: List[str] = field(default_factory=list)
    capabilities: List[ArmCapability] = field(default_factory=list)
    status: str = "uninitialized"
    registered_at: str = field(default_factory=lambda: datetime.now().isoformat())


class BaseArm(ABC):
    """Abstract base class for all AOA Arms.
    
    Every sub-system (Azazael, Libra, CODEX, EveUnleashed, etc.) must
    implement this interface to participate in AOA orchestration.
    
    Required methods:
        initialize()    — Boot the sub-system, return success/failure
        execute()       — Run a capability, return results
        get_capabilities() — Report available capabilities
        shutdown()      — Graceful teardown
    """

    def __init__(self):
        self._initialized = False
        self._metadata: Optional[ArmMetadata] = None

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize the Arm. Return True on success."""
        ...

    @abstractmethod
    def execute(self, capability: str, payload: Optional[dict] = None) -> dict:
        """Execute a named capability with optional payload.
        
        Returns: {"status": "ok"|"error", "data": ..., "arm_id": str}
        """
        ...

    @abstractmethod
    def get_capabilities(self) -> List[ArmCapability]:
        """Return list of capabilities this Arm exposes."""
        ...

    @abstractmethod
    def shutdown(self) -> bool:
        """Graceful shutdown. Return True on success."""
        ...

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def metadata(self) -> Optional[ArmMetadata]:
        return self._metadata

    def get_status(self) -> dict:
        """Return current status of this Arm."""
        return {
            "arm_id": self._metadata.arm_id if self._metadata else "unknown",
            "initialized": self._initialized,
            "capabilities_count": len(self.get_capabilities()),
        }


# Global Arm Registry
ARM_REGISTRY: Dict[str, BaseArm] = {}

# Set once the arm modules have been imported in this process.
_ARMS_IMPORTED = False

# The arm modules whose import fires @register_arm. Kept here (not only in
# `arms/__init__.py`) so a consumer that never touches the package can still
# populate the registry -- see `_ensure_arms_imported`.
_ARM_MODULES = (
    "azazael_arm", "libra_arm", "codex_arm", "eve_arm",
    "kali_arm", "support_arms",
)


def _arms_package_names() -> List[str]:
    """Candidate import names for the sibling ``arms`` package, in order.

    This module is imported either as ``AOA.core.arm_registry`` (PYTHONPATH=src)
    or as ``core.arm_registry`` (PYTHONPATH=src/AOA -- what ``aoa_monitor`` and
    ``cli/aoa_cli`` set up). The arms package is a sibling of ``core``, so the
    name is derived from our own package rather than hardcoded, and the other
    layout is tried second so both entry points populate the same registry.
    """
    pkg = __package__ or __name__.rpartition(".")[0]
    root = pkg.rpartition(".")[0]
    return ["%s.arms" % root, "arms"] if root else ["arms"]


def _alias_core_package() -> None:
    """Make ``core.*`` resolve to the SAME module objects as ``AOA.core.*``.

    The arm modules import absolutely (``from core.arm_registry import ...``).
    Loaded as ``AOA.core.arm_registry`` that name does not exist yet, so the
    import would build a SECOND copy of this module with its own empty
    ``ARM_REGISTRY`` -- registration would then land in a dict nobody reads and
    the router would still see zero Arms. Aliasing keeps one registry per
    process whichever entry point was used. ``setdefault`` means a real
    top-level ``core`` package is never clobbered.
    """
    pkg = __package__ or ""
    if not pkg.startswith("AOA."):
        return
    core_pkg = sys.modules.get(pkg)
    if core_pkg is None:
        return
    sys.modules.setdefault("core", core_pkg)
    self_mod = sys.modules.get(__name__)
    if self_mod is not None:
        sys.modules.setdefault("core.arm_registry", self_mod)
    prefix = pkg + "."
    for name, mod in list(sys.modules.items()):
        if name.startswith(prefix):
            sys.modules.setdefault("core." + name[len(prefix):], mod)


def _ensure_arms_imported() -> None:
    """Import every arm module so ``@register_arm`` fires.

    Registration is an IMPORT SIDE EFFECT, so an empty ``ARM_REGISTRY`` is what
    a consumer sees unless it remembers to import the arms package first. Three
    consumers did remember (``cli/aoa_cli``, ``build.py``, ``build_disclosure``)
    and ``aoa_monitor`` did not -- which is why the wallboard reported 0 Arms / 0
    capabilities and elastic routing failed with ``no_candidate`` for every
    request, so no outcome was ever written back and ``aoa_route:`` learning was
    never durable (measured: 0 keys in ``~/.azazael/spine_state.json`` while an
    in-process routed call produced 2). The forgetting is the defect, not the
    omission, so the fix belongs in the registry every consumer already touches.

    Best-effort and idempotent: a broken arm module must not make registry
    construction raise, and a partial import must not stop the rest.
    """
    global _ARMS_IMPORTED
    if _ARMS_IMPORTED:
        return
    _alias_core_package()
    for name in _arms_package_names():
        try:
            importlib.import_module(name)
            _ARMS_IMPORTED = True
            return
        except Exception as exc:
            note_failure("arm_registry:L184", exc)
            continue
    # The arm modules import ``core.*`` ABSOLUTELY, so the package only resolves
    # when the AOA root (the parent of ``core``) is importable. The documented
    # entry points already put it on the path; do it here for any other caller.
    try:
        aoa_root = str(Path(__file__).resolve().parents[1])
        if aoa_root not in sys.path:
            sys.path.insert(0, aoa_root)
    except Exception as exc:
        note_failure("arm_registry:L193", exc)
    for name in _arms_package_names():
        try:
            importlib.import_module(name)
            _ARMS_IMPORTED = True
            return
        except Exception as exc:
            note_failure("arm_registry:L200", exc)
            continue
    # Last resort: import the modules one by one. The package ``__init__`` fails
    # as a unit on a single bad arm, which would otherwise hide all the others.
    ok = False
    for base in _arms_package_names():
        for mod in _ARM_MODULES:
            try:
                importlib.import_module("%s.%s" % (base, mod))
                ok = True
            except Exception as exc:
                note_failure("arm_registry:L210", exc)
                continue
    _ARMS_IMPORTED = ok


def register_arm(
    arm_id: str,
    name: str,
    version: str = "1.0.0",
    description: str = "",
    category: str = "quartet",
    priority: int = 5,
    dependencies: Optional[List[str]] = None,
):
    """Decorator to register an Arm class in the ARM_REGISTRY.
    
    Usage:
        @register_arm("azazael", "Azazael Neural Mesh", priority=10)
        class AzazaelArm(BaseArm):
            ...
    """
    def decorator(cls):
        if not issubclass(cls, BaseArm):
            raise TypeError(
                f"{cls.__name__} must subclass BaseArm to use @register_arm"
            )
        instance = cls()
        instance._metadata = ArmMetadata(
            arm_id=arm_id,
            name=name,
            version=version,
            description=description,
            category=category,
            priority=priority,
            dependencies=dependencies or [],
        )
        ARM_REGISTRY[arm_id] = instance
        return cls
    return decorator


class ArmRegistry:
    """Central registry for all AOA Arms.
    
    Provides discovery, lifecycle management, and status reporting
    across all registered sub-systems.
    """

    def __init__(self):
        # Discovery is an import side effect; trigger it before snapshotting, or
        # every consumer that skips `import arms` silently gets an empty fleet.
        _ensure_arms_imported()
        self._arms: Dict[str, BaseArm] = dict(ARM_REGISTRY)
        self._initialized_arms: set = set()

    def register(self, arm_id: str, arm: BaseArm):
        """Register an Arm instance directly."""
        if not isinstance(arm, BaseArm):
            raise TypeError(f"{type(arm).__name__} must be a BaseArm")
        self._arms[arm_id] = arm

    def get_arm(self, arm_id: str) -> Optional[BaseArm]:
        """Get an Arm by ID."""
        return self._arms.get(arm_id)

    def list_arms(self, category: Optional[str] = None) -> List[dict]:
        """List all registered Arms with metadata."""
        result = []
        for arm_id, arm in self._arms.items():
            meta = arm.metadata
            if meta is None:
                continue
            if category and meta.category != category:
                continue
            result.append({
                "arm_id": meta.arm_id,
                "name": meta.name,
                "version": meta.version,
                "category": meta.category,
                "priority": meta.priority,
                "status": meta.status,
                "initialized": arm.is_initialized,
                "capabilities": len(arm.get_capabilities()),
                "dependencies": meta.dependencies,
            })
        return sorted(result, key=lambda x: (-x["priority"], x["name"]))

    def get_categories(self) -> List[str]:
        """Get all unique Arm categories."""
        cats = set()
        for arm in self._arms.values():
            if arm.metadata:
                cats.add(arm.metadata.category)
        return sorted(cats)

    def initialize_all(self, category: Optional[str] = None) -> Dict[str, bool]:
        """Initialize all Arms (optionally filtered by category).
        
        Returns: {arm_id: success}
        """
        results = {}
        for arm_id, arm in self._arms.items():
            if arm.metadata is None:
                continue
            if category and arm.metadata.category != category:
                continue
            try:
                success = arm.initialize()
                results[arm_id] = success
                if success:
                    self._initialized_arms.add(arm_id)
                    arm.metadata.status = "initialized"
                else:
                    arm.metadata.status = "init_failed"
            except Exception as e:
                note_failure("arm_registry:L324", e)
                results[arm_id] = False
                if arm.metadata:
                    arm.metadata.status = f"error: {e}"
        return results

    def shutdown_all(self) -> Dict[str, bool]:
        """Shutdown all initialized Arms."""
        results = {}
        for arm_id in self._initialized_arms.copy():
            arm = self._arms.get(arm_id)
            if arm:
                try:
                    success = arm.shutdown()
                    results[arm_id] = success
                    if arm.metadata:
                        arm.metadata.status = "shutdown"
                except Exception as e:
                    note_failure("arm_registry:L341", e)
                    results[arm_id] = False
                self._initialized_arms.discard(arm_id)
        return results

    def get_status_report(self) -> dict:
        """Generate a full status report of all Arms."""
        arms_list = self.list_arms()
        return {
            "total_arms": len(self._arms),
            "initialized": len(self._initialized_arms),
            "categories": self.get_categories(),
            "arms": arms_list,
            "timestamp": datetime.now().isoformat(),
        }

    @property
    def arms(self) -> Dict[str, BaseArm]:
        return self._arms
