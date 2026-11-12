# coding: utf-8
"""
Platform Health Monitor for ML Installations
Real-time monitoring, issue detection, and automatic remediation.
"""

import importlib
import time
import psutil
from typing import Any, Callable, List
from dataclasses import dataclass, field
from enum import Enum
import json
import threading
from collections import deque

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class HealthStatus(Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class HealthCheck:
    name: str
    status: HealthStatus
    message: str
    value: Any = None
    threshold: Any = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class Alert:
    check_name: str
    status: HealthStatus
    message: str
    timestamp: float = field(default_factory=time.time)
    resolved: bool = False


class PlatformHealthMonitor:
    """
    Real-time health monitoring for ML platforms.

    FEATURES:
    - Continuous monitoring of package health
    - GPU/CPU utilization tracking
    - Memory usage monitoring
    - Version compatibility checking
    - Automatic alerting
    - Historical tracking
    """

    def __init__(
        self,
        check_interval: float = 60.0,
        history_size: int = 1000,
        alert_callback: (Callable[[Alert], None] | None) = None,
    ):
        self.check_interval = check_interval
        self.history_size = history_size
        self.alert_callback = alert_callback
        self.health_history: deque[HealthCheck] = deque(maxlen=history_size)
        self.alerts: list[Alert] = []
        self.monitoring = False
        self.monitor_thread: (threading.Thread | None) = None
        self._lock = threading.Lock()

        self.checks: dict[str, Callable[[], HealthCheck]] = {
            "package_versions": self._check_package_versions,
            "gpu_availability": self._check_gpu,
            "memory_usage": self._check_memory,
            "cpu_usage": self._check_cpu,
            "disk_usage": self._check_disk,
            "import_errors": self._check_imports,
            "cuda_compatibility": self._check_cuda_compat,
        }

        self.thresholds = {
            "memory_warning": 80.0,
            "memory_critical": 95.0,
            "cpu_warning": 80.0,
            "cpu_critical": 95.0,
            "disk_warning": 85.0,
            "disk_critical": 95.0,
        }

        self.last_check: dict[str, HealthCheck] = {}

    def _check_package_versions(self) -> HealthCheck:
        """Check if packages can be imported and have valid versions."""
        packages = ["torch", "tensorflow", "numpy", "scipy", "pandas", "transformers"]
        available = []
        missing = []

        for pkg in packages:
            try:
                mod = importlib.import_module(pkg)
                version = getattr(mod, "__version__", "unknown")
                available.append(f"{pkg}:{version}")
            except ImportError as exc:
                note_failure("health_monitor:L104", exc)
                missing.append(pkg)

        if missing:
            return HealthCheck(
                name="package_versions",
                status=HealthStatus.CRITICAL,
                message=f"Missing packages: {', '.join(missing)}",
                value={"available": available, "missing": missing},
            )
        elif len(missing) > len(packages) * 0.2:
            return HealthCheck(
                name="package_versions",
                status=HealthStatus.WARNING,
                message="Many packages missing",
                value={"available": available, "missing": missing},
            )
        else:
            return HealthCheck(
                name="package_versions",
                status=HealthStatus.HEALTHY,
                message=f"All {len(available)} packages available",
                value={"available": available, "missing": missing},
            )

    def _check_gpu(self) -> HealthCheck:
        """Check GPU availability and utilization."""
        gpu_info = {"available": False, "count": 0, "utilization": [], "memory": []}

        try:
            import torch

            if torch.cuda.is_available():
                gpu_info["available"] = True
                gpu_info["count"] = torch.cuda.device_count()
                for i in range(gpu_info["count"]):
                    utilization = (
                        torch.cuda.utilization(i)
                        if hasattr(torch.cuda, "utilization")
                        else None
                    )
                    memory_used = torch.cuda.memory_allocated(i) / (1024**3)
                    memory_total = torch.cuda.get_device_properties(i).total_memory / (
                        1024**3
                    )
                    gpu_info["utilization"].append(utilization)
                    gpu_info["memory"].append(f"{memory_used:.1f}/{memory_total:.1f}GB")

                return HealthCheck(
                    name="gpu_availability",
                    status=HealthStatus.HEALTHY,
                    message=f"GPU available: {gpu_info['count']} device(s)",
                    value=gpu_info,
                )
        except ImportError as exc:
            note_failure("health_monitor:L158", exc)

        try:
            import tensorflow as tf  # type: ignore[reportMissingModuleSource]

            gpus = tf.config.list_physical_devices("GPU")
            if gpus:
                gpu_info["available"] = True
                gpu_info["count"] = len(gpus)
                return HealthCheck(
                    name="gpu_availability",
                    status=HealthStatus.HEALTHY,
                    message=f"TensorFlow GPU available: {gpu_info['count']} device(s)",
                    value=gpu_info,
                )
        except ImportError as exc:
            note_failure("health_monitor:L174", exc)

        return HealthCheck(
            name="gpu_availability",
            status=HealthStatus.WARNING,
            message="No GPU detected - using CPU",
            value=gpu_info,
        )

    def _check_memory(self) -> HealthCheck:
        """Check system memory usage."""
        memory = psutil.virtual_memory()
        usage_percent = memory.percent

        if usage_percent >= self.thresholds["memory_critical"]:
            status = HealthStatus.CRITICAL
        elif usage_percent >= self.thresholds["memory_warning"]:
            status = HealthStatus.WARNING
        else:
            status = HealthStatus.HEALTHY

        return HealthCheck(
            name="memory_usage",
            status=status,
            message=f"Memory usage: {usage_percent:.1f}%",
            value={
                "percent": usage_percent,
                "used_gb": memory.used / (1024**3),
                "available_gb": memory.available / (1024**3),
                "total_gb": memory.total / (1024**3),
            },
            threshold=self.thresholds["memory_warning"],
        )

    def _check_cpu(self) -> HealthCheck:
        """Check CPU utilization."""
        usage_percent = psutil.cpu_percent(interval=1)

        if usage_percent >= self.thresholds["cpu_critical"]:
            status = HealthStatus.CRITICAL
        elif usage_percent >= self.thresholds["cpu_warning"]:
            status = HealthStatus.WARNING
        else:
            status = HealthStatus.HEALTHY

        return HealthCheck(
            name="cpu_usage",
            status=status,
            message=f"CPU usage: {usage_percent:.1f}%",
            value={"percent": usage_percent},
            threshold=self.thresholds["cpu_warning"],
        )

    def _check_disk(self) -> HealthCheck:
        """Check disk usage."""
        disk = psutil.disk_usage("/")
        usage_percent = disk.percent

        if usage_percent >= self.thresholds["disk_critical"]:
            status = HealthStatus.CRITICAL
        elif usage_percent >= self.thresholds["disk_warning"]:
            status = HealthStatus.WARNING
        else:
            status = HealthStatus.HEALTHY

        return HealthCheck(
            name="disk_usage",
            status=status,
            message=f"Disk usage: {usage_percent:.1f}%",
            value={
                "percent": usage_percent,
                "used_gb": disk.used / (1024**3),
                "free_gb": disk.free / (1024**3),
                "total_gb": disk.total / (1024**3),
            },
            threshold=self.thresholds["disk_warning"],
        )

    def _check_imports(self) -> HealthCheck:
        """Check for import errors in critical packages."""
        critical_packages = [
            "torch",
            "tensorflow",
            "numpy",
            "scipy",
            "pandas",
            "transformers",
            "opencv-python",
            "scikit-learn",
        ]
        errors = []

        for pkg in critical_packages:
            try:
                importlib.import_module(pkg.replace("-", "_"))
            except Exception as e:
                note_failure("health_monitor:L270", e)
                errors.append(f"{pkg}: {str(e)[:800]}")

        if errors:
            return HealthCheck(
                name="import_errors",
                status=HealthStatus.CRITICAL,
                message=f"Import errors: {len(errors)} packages",
                value={"errors": errors},
            )
        else:
            return HealthCheck(
                name="import_errors",
                status=HealthStatus.HEALTHY,
                message="All critical packages import successfully",
                value={"errors": []},
            )

    def _check_cuda_compat(self) -> HealthCheck:
        """Check CUDA version compatibility."""
        compat_info = {
            "cuda_available": False,
            "torch_cuda": "N/A",
            "tf_cuda": "N/A",
            "compatible": False,
        }

        try:
            import torch

            compat_info["torch_cuda"] = getattr(torch.version, "cuda", "Not found")
            compat_info["cuda_available"] = torch.cuda.is_available()
        except ImportError as exc:
            note_failure("health_monitor:L302", exc)

        try:
            import tensorflow as tf  # type: ignore[reportMissingModuleSource]

            compat_info["tf_cuda"] = tf.sysconfig.get_build_info().get(
                "cuda_version", "Not found"
            )
        except ImportError as exc:
            note_failure("health_monitor:L311", exc)

        if compat_info["cuda_available"]:
            compat_info["compatible"] = True
            return HealthCheck(
                name="cuda_compatibility",
                status=HealthStatus.HEALTHY,
                message=f"CUDA available (torch: {compat_info['torch_cuda']})",
                value=compat_info,
            )
        else:
            return HealthCheck(
                name="cuda_compatibility",
                status=HealthStatus.WARNING,
                message="CUDA not available - using CPU",
                value=compat_info,
            )

    def run_health_check(
        self, check_name: (str | None) = None
    ) -> dict[str, HealthCheck]:
        """Run health checks and return results."""
        results = {}

        if check_name:
            if check_name in self.checks:
                with self._lock:
                    results[check_name] = self.checks[check_name]()
                    self.last_check[check_name] = results[check_name]
            return results

        for name, check_func in self.checks.items():
            with self._lock:
                check_result = check_func()
                results[name] = check_result
                self.last_check[name] = check_result
                self.health_history.append(check_result)

        self._process_alerts(results)
        return results

    def _process_alerts(self, results: dict[str, HealthCheck]):
        """Process health check results and generate alerts."""
        for name, check in results.items():
            if check.status in [HealthStatus.CRITICAL, HealthStatus.WARNING]:
                alert = Alert(
                    check_name=name, status=check.status, message=check.message
                )
                self.alerts.append(alert)

                if self.alert_callback:
                    self.alert_callback(alert)

    def get_health_summary(self) -> dict[str, Any]:
        """Get summary of current health status."""
        if not self.last_check:
            return {"status": HealthStatus.UNKNOWN, "message": "No checks run"}

        results = list(self.last_check.values())

        if any(c.status == HealthStatus.CRITICAL for c in results):
            status = HealthStatus.CRITICAL
        elif any(c.status == HealthStatus.WARNING for c in results):
            status = HealthStatus.WARNING
        else:
            status = HealthStatus.HEALTHY

        return {
            "status": status.value,
            "checks_run": len(results),
            "healthy_count": sum(
                1 for c in results if c.status == HealthStatus.HEALTHY
            ),
            "warning_count": sum(
                1 for c in results if c.status == HealthStatus.WARNING
            ),
            "critical_count": sum(
                1 for c in results if c.status == HealthStatus.CRITICAL
            ),
            "last_check_time": max(c.timestamp for c in results) if results else None,
            "alerts": len(self.alerts),
        }

    def print_status(self):
        """Print formatted health status."""
        summary = self.get_health_summary()
        results = self.run_health_check()

        print("=" * 70)
        print("ML PLATFORM HEALTH MONITOR")
        print("=" * 70)
        print(f"\nOverall Status: {summary['status'].upper()}")
        print(
            f"Checks: {summary['healthy_count']} healthy, {summary['warning_count']} warning, {summary['critical_count']} critical"
        )
        print(f"Active Alerts: {summary['alerts']}")

        print("\n--- Detailed Status ---")
        for name, check in results.items():
            status_icon = (
                "[OK]"
                if check.status == HealthStatus.HEALTHY
                else "[!!]"
                if check.status == HealthStatus.CRITICAL
                else "[W!]"
            )
            print(f"  {status_icon} {name:<25} {check.message}")

        print("\n--- Resource Usage ---")
        for name in ["memory_usage", "cpu_usage", "disk_usage"]:
            if name in results:
                check = results[name]
                value = check.value
                if "percent" in value:
                    print(f"  {name:<25} {value['percent']:.1f}%")

        print("=" * 70)

    def start_monitoring(self):
        """Start continuous background monitoring."""
        self.monitoring = True

        def monitor_loop():
            while self.monitoring:
                self.run_health_check()
                time.sleep(self.check_interval)

        self.monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.monitor_thread.start()

    def stop_monitoring(self):
        """Stop background monitoring."""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5.0)
            self.monitor_thread = None

    def export_history(self, path: str):
        """Export health check history to file."""
        history_data = [
            {
                "name": h.name,
                "status": h.status.value,
                "message": h.message,
                "value": str(h.value),
                "timestamp": h.timestamp,
            }
            for h in self.health_history
        ]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history_data, f, indent=2)

    def get_recommendations(self) -> list[str]:
        """Get recommendations based on health status."""
        recommendations = []

        results = self.run_health_check()

        if "memory_usage" in results:
            mem_check = results["memory_usage"]
            if mem_check.value.get("percent", 0) > 80:
                recommendations.append(
                    "High memory usage - consider reducing batch size or clearing cache"
                )

        if "gpu_availability" in results:
            gpu_check = results["gpu_availability"]
            if not gpu_check.value.get("available"):
                recommendations.append(
                    "No GPU detected - training will be slower on CPU"
                )

        if "import_errors" in results:
            err_check = results["import_errors"]
            if err_check.value.get("errors"):
                recommendations.append(
                    f"Fix import errors: {len(err_check.value['errors'])} packages have issues"
                )

        if "cuda_compatibility" in results:
            cuda_check = results["cuda_compatibility"]
            if not cuda_check.value.get("cuda_available"):
                recommendations.append("Install CUDA for GPU acceleration")

        return recommendations


def default_alert_handler(alert: Alert):
    """Default alert handler that prints alerts."""
    print(f"[ALERT {alert.status.value.upper()}] {alert.check_name}: {alert.message}")


if __name__ == "__main__":
    monitor = PlatformHealthMonitor(alert_callback=default_alert_handler)
    monitor.print_status()

    print("\n--- Recommendations ---")
    for rec in monitor.get_recommendations():
        print(f"  • {rec}")

    print("\n=== Demo Complete ===")
