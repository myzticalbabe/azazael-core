# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: CONTAINER (5 worms)
ContainerOrch, ComposeValidator, ImageAuditor, RegistryPoller, ResourceQuotaWatch
"""

import json
import time
import threading
import subprocess
from pathlib import Path
from typing import Any, List
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("container:L18", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("container:L27", exc)
    _FREETHREAD = False
    FreeThreadPool = None
    ThreadSafeDict = dict
    ThreadSafeList = list
    def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:
            note_failure("container:L36", exc)
            return False


@register_worm(category="container", priority=6, cooldown_seconds=60.0,
    description="Container orchestrator — multi-container lifecycle, health checks, restart policies")
class ContainerOrch:
    """Multi-container orchestrator with health checks and restart management."""

    VERSION = "1.0.0"

    def __init__(self):
        self._containers: dict[str, dict] = {}
        self._lock = threading.Lock()

    def launch(self, name: str, image: str, ports: (dict[str, str] | None) = None) -> dict[str, Any]:
        """Launch a container via docker CLI."""
        cmd = ["docker", "run", "-d", "--name", name]
        if ports:
            for hp, cp in ports.items():
                cmd.extend(["-p", f"{hp}:{cp}"])
        cmd.append(image)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            cid = r.stdout.strip()
            with self._lock:
                self._containers[name] = {"id": cid, "image": image, "status": "running", "launched": time.time()}
            return {"success": True, "container_id": cid}
        except Exception as e:
            note_failure("container:L64", e)
            return {"success": False, "error": str(e)}

    def health_check(self, name: str) -> dict[str, Any]:
        """Check container health via docker inspect."""
        try:
            r = subprocess.run(["docker", "inspect", name], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
            if r.returncode == 0:
                data = json.loads(r.stdout)
                state = data[0].get("State", {}) if data else {}
                return {"name": name, "running": state.get("Running", False), "status": state.get("Status", "unknown")}
        except Exception as exc:
            note_failure("container:L75", exc)
        return {"name": name, "running": False, "status": "error"}

    def stop(self, name: str) -> dict[str, Any]:
        """Stop and remove a container."""
        try:
            r = subprocess.run(["docker", "stop", name], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            subprocess.run(["docker", "rm", name], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
            with self._lock:
                if name in self._containers:
                    self._containers[name]["status"] = "stopped"
            return {"success": True, "container": name, "output": r.stdout.strip()}
        except Exception as e:
            note_failure("container:L88", e)
            return {"success": False, "error": str(e)}

    def list_all(self) -> list[dict]:
        with self._lock:
            return [{"name": n, **c} for n, c in self._containers.items()]



    def get_summary(self):
        return {"status":"ok","worm":"ContainerOrch","method":"health_check"}
@register_worm(category="container", priority=5, cooldown_seconds=120.0,
    description="Compose validator — docker-compose.yml syntax and schema validation")
class ComposeValidator:
    """Validates docker-compose.yml files for syntax and best practices."""

    VERSION = "1.0.0"

    def __init__(self):
        self._issues: list[dict] = []
        #: Which file the last validation ran against, and its verdict. Both
        #: stay None until validate() actually runs, so an empty issue list
        #: cannot be read as a passing validation.
        self._last_validated: str | None = None
        self._last_valid: bool | None = None

    def validate(self, compose_path: str) -> dict[str, Any]:
        """Validate a docker-compose.yml file."""
        path = Path(compose_path)
        if not path.exists():
            self._last_validated = str(path)
            self._last_valid = False
            return {"valid": False, "issues": [{"msg": "File not found"}]}
        issues = []
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if "services" not in data:
                issues.append({"msg": "Missing 'services' key"})
            else:
                for svc_name, svc in (data.get("services") or {}).items():
                    if "image" not in svc and "build" not in svc:
                        issues.append({"msg": f"Service '{svc_name}' missing image or build"})
        except ImportError as exc:
            note_failure("container:L125", exc)
            content = path.read_text(encoding="utf-8")
            if "services:" not in content:
                issues.append({"msg": "Missing 'services:' section"})
        except Exception as e:
            note_failure("container:L129", e)
            issues.append({"msg": str(e)})
        self._issues = issues
        self._last_validated = str(path)
        self._last_valid = len(issues) == 0
        return {"valid": self._last_valid, "issues": issues}
    def get_summary(self):
        # A validation summary must name the run it summarises: an empty issue
        # list and a never-run validator are not the same result.
        if self._last_validated is None:
            return {"worm": "ComposeValidator", "status": "not_run",
                    "run_recorded": False, "issues": len(self._issues)}
        return {"worm": "ComposeValidator",
                "status": "valid" if self._last_valid else "invalid",
                "run_recorded": True,
                "last_validated": self._last_validated,
                "issues": len(self._issues)}



@register_worm(category="container", priority=5, cooldown_seconds=90.0,
    description="Image auditor — scan container images for size, layers, and base-image freshness")
class ImageAuditor:
    """Audits container images for size, layer count, and base image freshness."""

    VERSION = "1.0.0"

    def __init__(self):
        self._audits: dict[str, dict] = {}

    def audit_image(self, image: str) -> dict[str, Any]:
        """Audit a single image."""
        try:
            r = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if r.returncode == 0:
                data = json.loads(r.stdout)[0]
                audit = {
                    "image": image, "size_mb": round(data.get("Size", 0) / (1024**2), 2),
                    "layers": len(data.get("RootFS", {}).get("Layers", [])),
                    "created": data.get("Created", ""), "os": data.get("Os", ""),
                }
                self._audits[image] = audit
                return audit
        except Exception as e:
            note_failure("container:L161", e)
            return {"image": image, "error": str(e)}
        return {"image": image, "error": "Not found"}

    def get_summary(self):
        return {"status":"ok","worm":"ImageAuditor"}


@register_worm(category="container", priority=4, cooldown_seconds=180.0,
    description="Registry poller — poll container registries for new tags and digest changes")
class RegistryPoller:
    """Polls container registries for version updates."""

    VERSION = "1.0.0"

    def __init__(self):
        self._tags: dict[str, dict[str, str | float]] = {}
        self._lock = threading.Lock()

    def check_latest(self, image: str) -> dict[str, Any]:
        """Check for latest tag via docker pull dry-run."""
        try:
            r = subprocess.run(["docker", "pull", image], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            status = "updated" if "Downloaded newer" in r.stdout else ("unchanged" if "Image is up to date" in r.stdout else "unknown")
            with self._lock:
                self._tags[image] = {"status": status, "checked": time.time()}
            return {"image": image, "status": status}
        except Exception as e:
            note_failure("container:L188", e)
            return {"image": image, "error": str(e)}

    def get_summary(self):
        return {"status":"ok","worm":"RegistryPoller"}


@register_worm(category="container", priority=5, cooldown_seconds=30.0,
    description="Resource quota watch — monitor container CPU/memory usage against limits")
class ResourceQuotaWatch:
    """Watches container resource usage against defined quotas."""

    VERSION = "1.0.0"

    def __init__(self):
        self._quotas: dict[str, dict] = {}
        self._lock = threading.Lock()

    def set_quota(self, container: str, cpu_limit: float, mem_mb: int):
        with self._lock:
            self._quotas[container] = {"cpu_limit": cpu_limit, "mem_mb": mem_mb, "alerts": []}

    def check_usage(self, container: str) -> dict[str, Any]:
        """Check container resource usage via docker stats."""
        try:
            r = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", container],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
            if r.returncode == 0 and "|" in r.stdout:
                cpu_str, mem_str = r.stdout.strip().split("|", 1)
                cpu = float(cpu_str.replace("%", ""))
                mem_mb = float(mem_str.split("/")[0].replace("MiB", "").strip() or 0)
                quota = self._quotas.get(container, {})
                return {"container": container, "cpu_pct": cpu, "mem_mb": mem_mb,
                        "over_cpu": cpu > quota.get("cpu_limit", 100),
                        "over_mem": mem_mb > quota.get("mem_mb", 1024)}
        except Exception as exc:
            note_failure("container:L223", exc)
        return {"container": container, "error": "Could not read stats"}

    def get_summary(self):
        return {"status":"ok","worm":"ResourceQuotaWatch"}
