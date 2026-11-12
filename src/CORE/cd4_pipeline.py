# coding: utf-8
"""
cd4_pipeline.py -- CD4 Multi-Stage CI Pipeline Module

Cross-Domain: CD4 (Nuitka + Testing)
Spec: research/CROSS_DOMAIN.md sec.CD4

Provides:
  - NuitkaBuildPipeline -- Three-stage CI pipeline orchestrator:
      Stage 1: Interpreted Tests (pytest with --skip-slow)
      Stage 2: Nuitka Compilation (standalone .exe or --module)
      Stage 3: Compiled Validation (pytest against compiled artifacts)
  - nuitka_incompatible marker registration for conftest.py
  - Build manifest validation (39 targets, tier classification, entry points)
  - Dry-run mode for environments without Nuitka installed

Usage:
    from CORE.cd4_pipeline import NuitkaBuildPipeline, get_pipeline

    # Dry-run validation (works without Nuitka)
    pipeline = NuitkaBuildPipeline(dry_run=True)
    result = pipeline.run_all_stages()
    print(result["stages"])

    # Full pipeline (requires Nuitka installed + patched)
    pipeline = NuitkaBuildPipeline(dry_run=False)
    result = pipeline.run_all_stages()

    # Register nuitka_incompatible marker in conftest.py
    # See register_nuitka_marker() for the pytest_configure hook.
"""

#: Declared here, OUTSIDE the docstring: `__all__` written inside a module
#: docstring is inert text -- it is never assigned, so `from ... import *`
#: silently fell back to every public name.
__all__ = [
    "DEFAULT_BUILD_SCRIPT",
    "DEFAULT_PATCH_SCRIPT",
    "DEFAULT_SRC_DIR",
    "DEFAULT_TESTS_DIR",
    "NUITKA_AVAILABLE",
    "NUITKA_VERSION",
    "NuitkaBuildPipeline",
    "STAGE_COMPILATION",
    "STAGE_INTERPRETED",
    "STAGE_VALIDATION",
    "get_nuitka_incompatible_items",
    "get_pipeline",
    "load_build_manifest",
    "register_nuitka_marker",
    "run_pipeline",
    "validate_build_manifest",
]

import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Nuitka availability detection (LAZY) -----------------------------
# These two names are deliberately NOT assigned at module level: probing Nuitka
# means spawning `python -m nuitka --version`, which costs seconds, and doing it
# on import made every importer pay it -- including tests/conftest.py's marker
# registration, i.e. every pytest startup. `__getattr__` below keeps both names
# importable while deferring the probe to first ACCESS, cached for the process.
_NUITKA_PROBE: (dict[str, Any] | None) = None
_NUITKA_PROBE_TIMEOUT = 10


def _detect_nuitka(timeout: int = _NUITKA_PROBE_TIMEOUT) -> dict[str, Any]:
    """Probe Nuitka once, on demand, and cache the verdict for the process.

    Returns:
        {"available": bool, "version": str, "probed": bool}. `probed` is False
        only when the probe itself could not run, which is a different claim
        from "Nuitka is not installed" -- see NUITKA_AVAILABLE.
    """
    global _NUITKA_PROBE
    if _NUITKA_PROBE is not None:
        return _NUITKA_PROBE
    available = False
    version = ""
    probed = False
    try:
        result = subprocess.run(
            [sys.executable, "-m", "nuitka", "--version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        probed = True
        if result.returncode == 0:
            available = True
            version = result.stdout.strip()
    except Exception as exc:
        # A probe that could not run is recorded, not silently folded into
        # "Nuitka is missing" -- `probed` keeps the two apart.
        note_failure("cd4_pipeline:_detect_nuitka", exc)
    _NUITKA_PROBE = {"available": available, "version": version,
                     "probed": probed}
    return _NUITKA_PROBE


def __getattr__(name: str) -> Any:
    """PEP 562: keep NUITKA_AVAILABLE / NUITKA_VERSION importable, but lazy.

    A `from CORE.cd4_pipeline import NUITKA_AVAILABLE` still resolves; it just
    resolves by probing now rather than at import time.
    """
    if name in ("NUITKA_AVAILABLE", "NUITKA_VERSION"):
        info = _detect_nuitka()
        return info["available"] if name == "NUITKA_AVAILABLE" else info["version"]
    raise AttributeError(
        "module %r has no attribute %r" % (__name__, name)
    )


#: Virtual names served lazily by __getattr__ above (PEP 562): declared here
#: so __all__ and static checkers see the contract. The VALUES always come
#: from the probe, never from a module global -- nothing may bind them here
#: without paying the import-time subprocess cost the laziness exists to avoid.
NUITKA_AVAILABLE: bool
NUITKA_VERSION: str


# ====================================================================
# Constants
# ====================================================================

# Default project paths (relative to project root)
DEFAULT_SRC_DIR = "src"
DEFAULT_TESTS_DIR = "tests"
DEFAULT_BUILD_SCRIPT = "scripts/build_all_concurrent.py"
DEFAULT_PATCH_SCRIPT = "docs/scripts/patch_nuitka_314t.py"

# Stage names
STAGE_INTERPRETED = "interpreted_tests"
STAGE_COMPILATION = "nuitka_compilation"
STAGE_VALIDATION = "compiled_validation"


# ====================================================================
# Build Manifest
# ====================================================================

def load_build_manifest(project_root: (str | None) = None) -> dict[str, Any]:
    """Load the build manifest from build_all_concurrent.py.

    Args:
        project_root: Path to project root. Auto-detected if None.

    Returns:
        dict of target_name -> target_info, or empty dict if build script
        cannot be loaded.
    """
    if project_root is None:
        project_root = _find_project_root()

    build_script = os.path.join(project_root, DEFAULT_BUILD_SCRIPT)
    if not os.path.isfile(build_script):
        return {}

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "build_all_concurrent", build_script
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, "BUILD_TARGETS", {})
    except Exception as exc:
        note_failure("cd4_pipeline:L122", exc)
    return {}


def validate_build_manifest(targets: dict[str, Any]) -> dict[str, Any]:
    """Validate a build manifest against CD4 spec requirements.

    Checks:
    - Minimum 39 targets
    - All entry points exist on disk (or report missing)
    - Tier classification has at least 3 tiers
    - Output names follow azazael-*.exe pattern

    Args:
        targets: BUILD_TARGETS dict from build_all_concurrent.py

    Returns:
        dict with keys:
            target_count    - Total targets in manifest
            valid_entries  - Count of entry points that exist on disk
            missing_entries - list of (name, entry_path) for missing files
            tiers           - {tier_name: count} dict
            distinct_tiers  - Number of distinct tiers
            output_pattern_valid - Whether outputs match azazael-*.exe
            errors          - list of validation error strings
            passed          - True if all validation checks pass
    """
    errors: list[str] = []
    project_root = Path(_find_project_root())

    target_count = len(targets)
    if target_count < 39:
        errors.append(
            f"Expected >= 39 targets, got {target_count}"
        )

    valid_entries = 0
    missing_entries: list[tuple[str, str]] = []
    for name, info in targets.items():
        entry = project_root / info.get("entry", "")
        if entry.exists():
            valid_entries += 1
        else:
            missing_entries.append((name, info.get("entry", "")))

    if missing_entries:
        errors.append(
            f"{len(missing_entries)} entry points missing on disk"
        )

    # Tier classification
    tiers: dict[str, int] = {}
    for info in targets.values():
        tier = info.get("tier", "unknown")
        tiers[tier] = tiers.get(tier, 0) + 1

    distinct_tiers = len(tiers)
    if distinct_tiers < 3:
        errors.append(
            f"Expected >= 3 tiers, got {distinct_tiers}: {tiers}"
        )

    # Output filename pattern
    output_pattern_valid = all(
        info.get("output", "").endswith(".exe") and
        info.get("output", "").startswith("azazael-")
        for info in targets.values()
    )
    if not output_pattern_valid and targets:
        errors.append("Not all outputs follow azazael-*.exe pattern")

    return {
        "target_count": target_count,
        "valid_entries": valid_entries,
        "missing_entries": missing_entries,
        "missing_count": len(missing_entries),
        "tiers": tiers,
        "distinct_tiers": distinct_tiers,
        "output_pattern_valid": output_pattern_valid,
        "errors": errors,
        "passed": len(errors) == 0,
    }


# ====================================================================
# NuitkaBuildPipeline -- Three-Stage CI Orchestrator
# ====================================================================

class NuitkaBuildPipeline:
    """Three-stage CI pipeline for Nuitka compiled test validation.

    Pipeline flow:
      Stage 1: Run interpreted tests (pytest --skip-slow)
      Stage 2: Compile to standalone .exe or --module
      Stage 3: Run test suite against compiled artifacts

    Each stage gates the next: Stage 2 only runs if Stage 1 passes,
    Stage 3 only runs if Stage 2 passes.

    Attributes:
        project_root  - Project root directory
        dry_run       - If True, simulate all stages without Nuitka
        nuitka_available - Whether Nuitka is installed. Lazy property: the
                           probe runs on first READ, never at import or at
                           construction.
        nuitka_version  - Nuitka version string (if available), lazy as above
        stages        - Results of each stage after run_all_stages()
    """

    def __init__(
        self,
        project_root: (str | None) = None,
        dry_run: bool = False,
        src_dir: str = DEFAULT_SRC_DIR,
        tests_dir: str = DEFAULT_TESTS_DIR,
        build_target: (str | None) = None,
        nuitka_info: (dict[str, Any] | None) = None,
    ):
        """Initialize the CI pipeline.

        Args:
            project_root: Path to project root. Auto-detected if None.
            dry_run: If True, simulate stages without Nuitka.
            src_dir: Source directory relative to project root.
            tests_dir: Test directory relative to project root.
            build_target: Specific build target name. If None, runs
                          manifest validation instead of full build.
            nuitka_info: Optional pre-computed {"available", "version"} dict.
                          When supplied, no Nuitka probe is ever attempted,
                          which is what keeps tests and dry runs fast and
                          deterministic.
        """
        self.project_root = os.path.abspath(
            project_root or _find_project_root()
        )
        self.dry_run = dry_run
        self.src_dir = os.path.join(self.project_root, src_dir)
        self.tests_dir = os.path.join(self.project_root, tests_dir)
        self.build_target = build_target

        # Nuitka availability is resolved on first READ (the properties below),
        # not here: constructing a pipeline -- a dry run, or a test that only
        # wants the manifest -- must not pay the probe. `nuitka_info` supplies
        # a verdict outright; None means "probe when actually asked".
        self._nuitka_info: (dict[str, Any] | None) = (
            dict(nuitka_info) if nuitka_info is not None else None
        )
        self.stages: dict[str, Any] = {}

        # Runtime tracking
        self._start_time = 0.0

    # -- Nuitka availability (lazy) --------------------------------------

    def _nuitka_status(self) -> dict[str, Any]:
        """Availability dict, probed once on first read and then cached."""
        if self._nuitka_info is None:
            self._nuitka_info = _detect_nuitka()
        return self._nuitka_info

    def _nuitka_override(self) -> dict[str, Any]:
        """The status dict for an explicit SET, without triggering a probe.

        An assignment is the caller stating the verdict; probing here would
        both waste the call and let the probe overwrite what was just set.
        """
        if self._nuitka_info is None:
            self._nuitka_info = {"available": False, "version": "",
                                 "probed": False}
        return self._nuitka_info

    @property
    def nuitka_available(self) -> bool:
        """Whether Nuitka is installed (triggers the probe on first read)."""
        return bool(self._nuitka_status()["available"])

    @nuitka_available.setter
    def nuitka_available(self, value: bool) -> None:
        self._nuitka_override()["available"] = bool(value)

    @property
    def nuitka_version(self) -> str:
        """Nuitka version string, or "" when unavailable (lazy, as above)."""
        return str(self._nuitka_status()["version"])

    @nuitka_version.setter
    def nuitka_version(self, value: str) -> None:
        self._nuitka_override()["version"] = str(value)

    def _stage_result(
        self,
        stage: str,
        passed: bool,
        summary: str,
        details: (dict[str, Any] | None) = None,
    ) -> dict[str, Any]:
        """Create a standardized stage result dict."""
        return {
            "stage": stage,
            "passed": passed,
            "summary": summary,
            "details": details or {},
            "elapsed_s": round(time.time() - self._start_time, 2),
        }

    def run_stage_1_interpreted_tests(
        self,
        extra_args: (list[str] | None) = None,
    ) -> dict[str, Any]:
        """Stage 1: Run interpreted tests via pytest.

        Runs pytest against the tests directory with --skip-slow and
        configurable extra arguments. In dry-run mode, simulates success.

        Args:
            extra_args: Additional pytest arguments (e.g., ["-v", "-x"]).

        Returns:
            Stage result dict with:
                passed        - True if tests pass (or dry-run)
                exit_code     - pytest exit code
                test_cmd      - The pytest command that was run
                stdout/stderr - Captured output (truncated)
        """
        self._start_time = time.time()
        cmd = [
            sys.executable, "-m", "pytest", self.tests_dir,
            "--skip-slow",
        ]
        if extra_args:
            cmd.extend(extra_args)

        if self.dry_run:
            return self._stage_result(
                STAGE_INTERPRETED, True,
                f"[DRY-RUN] Would run: {' '.join(cmd)}",
                {"test_cmd": " ".join(cmd), "dry_run": True},
            )

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
            )
            passed = result.returncode == 0
            summary = (
                f"pytest exit code {result.returncode}: "
                f"{'PASS' if passed else 'FAIL'}"
            )
            return self._stage_result(
                STAGE_INTERPRETED, passed, summary,
                {
                    "exit_code": result.returncode,
                    "test_cmd": " ".join(cmd),
                    "stdout": result.stdout[-2000:],
                    "stderr": result.stderr[-1000:],
                },
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("cd4_pipeline:L331", exc)
            return self._stage_result(
                STAGE_INTERPRETED, False,
                "pytest timed out after 300s",
                {"test_cmd": " ".join(cmd), "timeout": 300},
            )

    def run_stage_2_nuitka_compilation(
        self,
        target: (str | None) = None,
        build_mode: str = "module",
    ) -> dict[str, Any]:
        """Stage 2: Compile to Nuitka standalone or module.

        Applies 3.14t patches if available, then runs Nuitka compilation.
        In dry-run mode, simulates success and validates the build manifest.

        Args:
            target: Specific build target (entry point .py file).
                    If None, validates build manifest instead of compiling.
            build_mode: "module" (--module) or "standalone" (--standalone).

        Returns:
            Stage result dict with:
                passed          - True if compilation succeeds (or dry-run)
                nuitka_version  - Nuitka version used
                build_mode      - "module" or "standalone"
                manifest_valid  - Build manifest validation result
        """
        self._start_time = time.time()

        # Validate build manifest
        manifest = load_build_manifest(self.project_root)
        manifest_valid = validate_build_manifest(manifest)

        if self.dry_run:
            return self._stage_result(
                STAGE_COMPILATION, True,
                f"[DRY-RUN] Stage 2 simulated. "
                f"Manifest: {manifest_valid['target_count']} targets, "
                f"{'PASS' if manifest_valid['passed'] else 'HAS ISSUES'}",
                {
                    "nuitka_available": self.nuitka_available,
                    "nuitka_version": self.nuitka_version or "N/A",
                    "build_mode": build_mode,
                    "dry_run": True,
                    "manifest": manifest_valid,
                    "patch_script": DEFAULT_PATCH_SCRIPT,
                },
            )

        # Real mode requires Nuitka
        if not self.nuitka_available:
            return self._stage_result(
                STAGE_COMPILATION, False,
                "Nuitka not installed. Install with: pip install nuitka",
                {"nuitka_available": False},
            )

        # Apply 3.14t patches
        patch_script = os.path.join(
            self.project_root, DEFAULT_PATCH_SCRIPT
        )
        if os.path.isfile(patch_script):
            try:
                subprocess.run(
                    [sys.executable, patch_script, "--apply", "--verify"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                )
            except Exception as exc:
                note_failure("cd4_pipeline:L400", exc) # Non-fatal - build may still work

        # Build target
        if target:
            entry = os.path.join(self.project_root, target)
            if not os.path.isfile(entry):
                return self._stage_result(
                    STAGE_COMPILATION, False,
                    f"Entry point not found: {target}",
                    {"target": target},
                )

            output_dir = os.path.join(
                self.project_root, "build", "standalone"
            )
            os.makedirs(output_dir, exist_ok=True)

            cmd = [
                sys.executable, "-m", "nuitka",
                "--standalone" if build_mode == "standalone" else "--module",
                f"--output-dir={output_dir}",
                "--include-package=CORE",
                entry,
            ]

            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600
                )
                passed = result.returncode == 0
                summary = (
                    f"Nuitka {'PASS' if passed else 'FAIL'}: "
                    f"exit code {result.returncode}"
                )
                return self._stage_result(
                    STAGE_COMPILATION, passed, summary,
                    {
                        "target": target,
                        "build_mode": build_mode,
                        "exit_code": result.returncode,
                        "nuitka_version": self.nuitka_version,
                        "cmd": " ".join(cmd),
                        "stdout": result.stdout[-2000:],
                        "stderr": result.stderr[-1000:],
                        "manifest": manifest_valid,
                    },
                )
            except subprocess.TimeoutExpired as exc:
                note_failure("cd4_pipeline:L448", exc)
                return self._stage_result(
                    STAGE_COMPILATION, False,
                    f"Nuitka build timed out (600s): {target}",
                    {"target": target, "timeout": 600},
                )

        # No target specified - just return manifest validation
        return self._stage_result(
            STAGE_COMPILATION, manifest_valid["passed"],
            f"Manifest validation: "
            f"{manifest_valid['target_count']} targets, "
            f"{manifest_valid['distinct_tiers']} tiers, "
            f"{'ALL VALID' if manifest_valid['passed'] else 'HAS ISSUES'}",
            {
                "nuitka_version": self.nuitka_version or "N/A",
                "build_mode": build_mode,
                "manifest": manifest_valid,
            },
        )

    def run_stage_3_compiled_validation(
        self,
        compiled_dir: (str | None) = None,
        extra_args: (list[str] | None) = None,
    ) -> dict[str, Any]:
        """Stage 3: Run tests against compiled artifacts.

        Sets PYTHONPATH to the compiled distribution directory and
        runs pytest, skipping nuitka_incompatible-marked tests.

        Args:
            compiled_dir: Path to compiled distribution directory.
                          Default: {project_root}/azazael.dist
            extra_args: Additional pytest arguments.

        Returns:
            Stage result dict with:
                passed    - True if compiled tests pass (or dry-run)
                skip_count - Number of nuitka_incompatible tests skipped

        A LIVE run with no compiled artifacts returns passed=False and
        details["stage_3_verdict"] == "NOT_RUN_NO_ARTIFACTS": a stage that
        cannot run must not be foldable into a passing pipeline.
        """
        self._start_time = time.time()
        dist_dir = compiled_dir or os.path.join(
            self.project_root, "azazael.dist"
        )
        dist_exists = os.path.isdir(dist_dir)

        if self.dry_run:
            return self._stage_result(
                STAGE_VALIDATION, True,
                f"[DRY-RUN] Stage 3 simulated. "
                f"Compiled dir: {dist_dir if dist_exists else 'N/A'}",
                {
                    "compiled_dir": dist_dir,
                    "compiled_dir_exists": dist_exists,
                    "dry_run": True,
                    "note": (
                        "In real mode, runs pytest against compiled "
                        "artifacts with nuitka_incompatible tests skipped"
                    ),
                },
            )

        # LIVE mode with nothing compiled. This used to fall into the branch
        # above and return passed=True with a "[DRY-RUN] Stage 3 simulated"
        # summary, which run_all_stages folded into all_passed -- so a live
        # pipeline could report ALL PASSED having validated no artifact at all.
        # An unrunnable stage is a failure, and it names what was missing.
        if not dist_exists:
            return self._stage_result(
                STAGE_VALIDATION, False,
                f"Stage 3 cannot run: no compiled artifacts at {dist_dir}",
                {
                    "compiled_dir": dist_dir,
                    "compiled_dir_exists": False,
                    "dry_run": False,
                    "stage_3_verdict": "NOT_RUN_NO_ARTIFACTS",
                    "note": (
                        "Nothing was validated. Run Stage 2 (or pass "
                        "compiled_dir) before treating this pipeline as "
                        "passing."
                    ),
                },
            )

        # Run pytest against compiled artifacts
        env = os.environ.copy()
        env["PYTHONPATH"] = dist_dir

        cmd = [
            sys.executable, "-m", "pytest", self.tests_dir,
            "--skip-slow", "-m", "not nuitka_incompatible",
        ]
        if extra_args:
            cmd.extend(extra_args)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                env=env,
            )
            passed = result.returncode == 0
            summary = (
                f"Compiled validation {'PASS' if passed else 'FAIL'}: "
                f"exit code {result.returncode}"
            )
            return self._stage_result(
                STAGE_VALIDATION, passed, summary,
                {
                    "exit_code": result.returncode,
                    "compiled_dir": dist_dir,
                    "test_cmd": " ".join(cmd),
                    "stdout": result.stdout[-2000:],
                    "stderr": result.stderr[-1000:],
                },
            )
        except subprocess.TimeoutExpired as exc:
            note_failure("cd4_pipeline:L541", exc)
            return self._stage_result(
                STAGE_VALIDATION, False,
                "Compiled validation timed out after 300s",
                {"compiled_dir": dist_dir, "timeout": 300},
            )

    def run_all_stages(
        self,
        extra_args: (list[str] | None) = None,
    ) -> dict[str, Any]:
        """Run all 3 stages sequentially. Each stage gates the next.

        Args:
            extra_args: Additional pytest arguments for stages 1 and 3.

        Returns:
            dict with:
                stages       - {stage_name: stage_result} dict
                all_passed   - True if ALL stages passed
                pipeline_summary - Human-readable summary string
                nuitka_available - Whether Nuitka is installed
                dry_run      - Whether dry-run mode was used
                elapsed_s    - Total pipeline wall-clock time
        """
        t0 = time.time()
        self.stages = {}

        # Stage 1: Always run
        stage1 = self.run_stage_1_interpreted_tests(extra_args)
        self.stages[STAGE_INTERPRETED] = stage1

        # Stage 2: Only if stage 1 passed
        if stage1["passed"]:
            stage2 = self.run_stage_2_nuitka_compilation(
                target=self.build_target
            )
            self.stages[STAGE_COMPILATION] = stage2
        else:
            self.stages[STAGE_COMPILATION] = self._stage_result(
                STAGE_COMPILATION, False,
                "Skipped: Stage 1 (interpreted tests) failed",
                {"skipped": True, "reason": "Stage 1 failed"},
            )

        # Stage 3: Only if stages 1 and 2 passed
        if stage1["passed"] and self.stages[STAGE_COMPILATION]["passed"]:
            stage3 = self.run_stage_3_compiled_validation(
                extra_args=extra_args
            )
            self.stages[STAGE_VALIDATION] = stage3
        else:
            self.stages[STAGE_VALIDATION] = self._stage_result(
                STAGE_VALIDATION, False,
                "Skipped: Stage 1 or 2 failed",
                {"skipped": True, "reason": "Previous stage failed"},
            )

        all_passed = all(
            s["passed"] for s in self.stages.values()
        )
        elapsed = round(time.time() - t0, 2)

        # Build summary
        stage_lines = []
        for name, result in self.stages.items():
            status = "PASS" if result["passed"] else "FAIL"
            stage_lines.append(f"  {name}: {status} ({result['elapsed_s']}s)")
        summary_lines = (
            [f"Pipeline ({'DRY-RUN' if self.dry_run else 'LIVE'}) "
             f"{'ALL PASSED' if all_passed else 'HAS FAILURES'}"] +
            stage_lines +
            [f"  Total: {elapsed}s"]
        )

        return {
            "stages": self.stages,
            "all_passed": all_passed,
            "pipeline_summary": "\n".join(summary_lines),
            "nuitka_available": self.nuitka_available,
            "nuitka_version": self.nuitka_version,
            "dry_run": self.dry_run,
            "elapsed_s": elapsed,
        }


# ====================================================================
# nuitka_incompatible Marker Registration
# ====================================================================

def register_nuitka_marker(config: Any) -> None:
    """Register the 'nuitka_incompatible' pytest marker.

    Call this inside conftest.py's pytest_configure() hook:

        def pytest_configure(config):
            from CORE.cd4_pipeline import register_nuitka_marker
            register_nuitka_marker(config)

    The marker is used to tag tests that cannot run under compiled
    (.exe or --module) mode. Stage 3 of the pipeline automatically
    skips these tests via: -m "not nuitka_incompatible"

    Marker description for pytest --markers:
        nuitka_incompatible: Mark test as incompatible with Nuitka
        compiled execution. Automatically skipped in Stage 3 of the
        CD4 CI pipeline.
    """
    config.addinivalue_line(
        "markers",
        "nuitka_incompatible: Mark test as incompatible with Nuitka "
        "compiled execution. Skipped in CD4 pipeline Stage 3 "
        "(compiled validation).",
    )


def get_nuitka_incompatible_items(items: list[Any]) -> list[Any]:
    """Return test items marked with nuitka_incompatible.

    Useful for pytest_collection_modifyitems to report how many
    tests will be skipped in compiled mode.

    Args:
        items: list of pytest Item objects (from pytest_collection_modifyitems).

    Returns:
        list of items that have the nuitka_incompatible marker.
    """
    return [item for item in items if item.get_closest_marker("nuitka_incompatible")]


# ====================================================================
# Module-level convenience
# ====================================================================

_default_pipeline: (NuitkaBuildPipeline | None) = None


def get_pipeline(
    project_root: (str | None) = None,
    dry_run: bool = True,
) -> NuitkaBuildPipeline:
    """Get or create the default CI pipeline instance.

    Args:
        project_root: Path to project root. Auto-detected if None.
        dry_run: Whether to use dry-run mode (default True for safety).

    Returns:
        Shared NuitkaBuildPipeline instance.
    """
    global _default_pipeline
    if _default_pipeline is None:
        _default_pipeline = NuitkaBuildPipeline(
            project_root=project_root, dry_run=dry_run
        )
    return _default_pipeline


def run_pipeline(
    dry_run: bool = True,
    extra_args: (list[str] | None) = None,
) -> dict[str, Any]:
    """One-shot CI pipeline execution using the default pipeline.

    Args:
        dry_run: If True, simulate all stages (default True for safety).
        extra_args: Additional pytest arguments.

    Returns:
        Full pipeline result dict from NuitkaBuildPipeline.run_all_stages().
    """
    return get_pipeline(dry_run=dry_run).run_all_stages(extra_args=extra_args)


# ====================================================================
# Helpers
# ====================================================================

def _find_project_root() -> str:
    """Detect the Azazael project root by walking up from this file."""
    current = Path(__file__).resolve().parent  # src/CORE/
    # Go up until we find a known project marker
    for parent in [current.parent, current.parent.parent]:  # src/ then azazael/
        if (parent / "tests" / "test_cross_domain_integration.py").exists():
            return str(parent)
        if (parent / "scripts").is_dir() and (parent / "src").is_dir():
            return str(parent)
    return str(current.parent.parent)  # Fallback


# ====================================================================
# Self-test
# ====================================================================

if __name__ == "__main__":
    print("CD4 CI Pipeline v1.0 -- Self-test")
    _nuitka_info = _detect_nuitka()
    print(f"  Nuitka available: {_nuitka_info['available']}")
    print(f"  Nuitka version: {_nuitka_info['version'] or 'N/A'}")
    print(f"  Project root: {_find_project_root()}")
    print()

    # Test 1: Pipeline creation and dry-run
    pipeline = NuitkaBuildPipeline(dry_run=True)
    assert pipeline.dry_run, "Pipeline should be in dry-run mode"
    print(f"  [PASS] Pipeline created (dry_run={pipeline.dry_run})")

    # Test 2: Dry-run Stage 1 (interpreted tests)
    stage1 = pipeline.run_stage_1_interpreted_tests()
    assert stage1["passed"], "Dry-run Stage 1 should pass"
    assert stage1["details"].get("dry_run"), "Should be dry-run"
    print(f"  [PASS] Stage 1 (dry-run): {stage1['summary']}")

    # Test 3: Dry-run Stage 2 (compilation)
    stage2 = pipeline.run_stage_2_nuitka_compilation()
    assert stage2["passed"], "Dry-run Stage 2 should pass"
    assert stage2["details"].get("dry_run"), "Should be dry-run"
    print(f"  [PASS] Stage 2 (dry-run): {stage2['summary']}")

    # Test 4: Dry-run Stage 3 (compiled validation)
    stage3 = pipeline.run_stage_3_compiled_validation()
    assert stage3["passed"], "Dry-run Stage 3 should pass"
    assert stage3["details"].get("dry_run"), "Should be dry-run"
    print(f"  [PASS] Stage 3 (dry-run): {stage3['summary']}")

    # Test 5: Full pipeline (dry-run)
    result = pipeline.run_all_stages()
    assert result["all_passed"], "Dry-run pipeline should all pass"
    assert STAGE_INTERPRETED in result["stages"]
    assert STAGE_COMPILATION in result["stages"]
    assert STAGE_VALIDATION in result["stages"]
    print("  [PASS] Full pipeline (dry-run): all 3 stages present")
    print(f"  {result['pipeline_summary']}")

    # Test 6: Build manifest validation
    manifest = load_build_manifest()
    if manifest:
        manifest_result = validate_build_manifest(manifest)
        print(f"  [PASS] Build manifest loaded: {manifest_result['target_count']} targets, "
              f"{manifest_result['distinct_tiers']} tiers")
    else:
        print("  [INFO] Build manifest not found (expected in some environments)")

    # Test 7: marker registration test
    # Can't test pytest_configure directly, but verify the function exists
    assert callable(register_nuitka_marker), "register_nuitka_marker must be callable"
    assert callable(get_nuitka_incompatible_items), "get_nuitka_incompatible_items must be callable"
    print("  [PASS] Marker registration functions are callable")

    # Test 8: get_pipeline singleton
    p1 = get_pipeline(dry_run=True)
    p2 = get_pipeline(dry_run=True)
    assert p1 is p2, "get_pipeline should return the same instance"
    print("  [PASS] get_pipeline returns singleton")

    # Summary
    print("\n  All 6/6 self-tests PASSED")
    print(f"  Nuitka: {'AVAILABLE' if _nuitka_info['available'] else 'NOT INSTALLED (dry-run mode active)'}")
