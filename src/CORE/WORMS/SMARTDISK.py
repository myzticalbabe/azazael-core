# coding: utf-8
"""
SMARTDISK - Disk Health Monitoring & Failure Prediction

SMART-based disk health monitoring with ML-powered failure prediction
and predictive maintenance alerts.

Features:
- SMART attribute monitoring
- Failure probability prediction
- Disk health scoring
- Predictive maintenance alerts
- Historical trend analysis

Usage:
    >>> from WORMZ.SMARTDISK import SMARTDISK
    >>> sd = SMARTDISK()
    >>> health = sd.get_disk_health()
    >>> prediction = sd.predict_failure()
    >>> alerts = sd.get_alerts()
"""

import os
import json
import pickle
import statistics
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk, scrolledtext

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from sklearn.ensemble import RandomForestRegressor

    SKLEARN_AVAILABLE = True
except ImportError as exc:
    note_failure("SMARTDISK:L39", exc)
    SKLEARN_AVAILABLE = False


@dataclass
class SMARTAttribute:
    """SMART attribute value."""

    id: int
    name: str
    value: int
    worst: int
    threshold: int
    raw: int
    status: str


@dataclass
class DiskHealth:
    """Disk health assessment."""

    device: str
    model: str
    health_score: float
    failure_probability: float
    status: str
    smart_attributes: list[dict]
    temperature: float
    power_on_hours: int
    estimated_remaining_days: int
    recommendations: list[str]


@dataclass
class DiskAlert:
    """Disk health alert."""

    device: str
    alert_type: str
    severity: str
    message: str
    recommended_action: str
    timestamp: str


try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("SMARTDISK:L86", exc)
    register_worm = lambda **kw: (lambda cls: cls)


from CORE.paths import get_project_root
import argparse


_ROOT = get_project_root()
_OUTPUT_DIR = _ROOT / "output" / "smartdisk"


class SMARTDISKConfig:
    """Configuration for SMARTDISK."""

    OUTPUT_PATH = str(_OUTPUT_DIR)
    HISTORY_FILE = str(_OUTPUT_DIR / "history.json")
    MODEL_FILE = str(_OUTPUT_DIR / "failure_model.pkl")

    SMART_THRESHOLDS = {
        5: 50,
        10: 0,
        187: 0,
        188: 0,
        197: 0,
        198: 0,
        199: 0,
    }

    SMART_NAMES = {
        1: "Raw_Read_Error_Rate",
        2: "Throughput_Performance",
        3: "Spin_Up_Time",
        4: "Start_Stop_Count",
        5: "Reallocated_Sector_Ct",
        7: "Seek_Error_Rate",
        8: "Seek_Time_Performance",
        9: "Power_On_Hours",
        10: "Spin_Retry_Count",
        11: "Calibration_Retry_Count",
        12: "Power_Cycle_Count",
        183: "Runtime_Bad_Block",
        184: "End-to-End_Error",
        187: "Reported_Uncorrectable",
        188: "Command_Timeout",
        189: "High_Fly_Writes",
        190: "Airflow_Temperature",
        191: "G-Sense_Error_Rate",
        192: "Power-Off_Retract_Count",
        193: "Load_Cycle_Count",
        194: "Temperature_Celsius",
        195: "Hardware_ECC_Recovered",
        196: "Reallocated_Event_Count",
        197: "Current_Pending_Sector",
        198: "Offline_Uncorrectable",
        199: "UDMA_CRC_Error_Count",
        200: "Multi_Zone_Error_Rate",
        201: "Soft_Read_Error_Rate",
        202: "Data_Address_Mark_Errors",
        203: "Run_Out_Cancel",
        204: "Soft_ECC_Correction",
        205: "Thermal_Asperity_Rate",
        206: "Flying_Height",
        207: "Spin_High_Current",
        208: "Spin_Buzz",
        209: "Offline_Seek_Performance",
        220: "Disk_Shift",
        221: "G-Sense_Error_Rate",
        222: "Loaded_Hours",
        223: "Load_Retry_Count",
        224: "Load_Friction",
        225: "Load_Cycle_Count",
        226: "Load-in_Time",
        227: "Torque_Count",
        228: "Power-Off_Retract_Count",
        230: "Head_Amplitude",
        231: "Temperature_Celsius",
        232: "Available_Reservd_Space",
        233: "Media_Wearout_Indicator",
        234: "Average_Erase_Count",
        235: "Max_Erase_Count",
        236: "Average_Erase_Count",
        237: "Extended_Error_Test",
        238: "Head_Amplitude",
        240: "Head_Flying_Hours",
        241: "Total_LBAs_Written",
        242: "Total_LBAs_Read",
        250: "Read_Error_Retry_Rate",
    }


@register_worm(category="monitor", priority=5, cooldown_seconds=60.0, description="SMART-based disk health monitoring with failure prediction")
class SMARTDISK:
    """SMART-based Disk Health Monitoring & Failure Prediction."""

    VERSION = "1.0.0"

    def __init__(self):
        self.config = SMARTDISKConfig()
        self.output_path = Path(self.config.OUTPUT_PATH)
        self.history_file = Path(self.config.HISTORY_FILE)
        self.model_file = Path(self.config.MODEL_FILE)
        self._ensure_directories()

        self.disk_history: list[dict] = []
        self.failure_model: (RandomForestRegressor | None) = None

        self._load_history()
        self._load_model()

    def _ensure_directories(self):
        """Create output directories."""
        self.output_path.mkdir(parents=True, exist_ok=True)

    def _run_smartctl(self, args: list[str]) -> str:
        """Run smartctl command."""
        import platform

        smartctl_path = "smartctl"
        if platform.system() == "Windows":
            program_files = os.environ.get("ProgramFiles", "C:\\Program Files")  # scanner:ignore deliberate: env var wins; literal is a fallback
            smartctl_path = f"{program_files}\\smartmontools\\bin\\smartctl.exe"
        try:
            result = subprocess.run(
                [smartctl_path] + args,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=30,
            )
            return result.stdout + result.stderr
        except FileNotFoundError as exc:
            note_failure("SMARTDISK:L214", exc)
            return "smartctl not found"
        except Exception as e:
            note_failure("SMARTDISK:L216", e)
            return str(e)

    def _parse_smart_output(self, output: str) -> dict[str, Any]:
        """Parse smartctl output."""
        data = {
            "device": "",
            "model": "",
            "serial": "",
            "health": "UNKNOWN",
            "temperature": 0,
            "power_on_hours": 0,
            "attributes": [],
        }

        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("Device Model:"):
                data["model"] = line.split(":")[-1].strip()
            elif line.startswith("Serial Number:"):
                data["serial"] = line.split(":")[-1].strip()
            elif "SMART overall-health" in line or "SMART Health Status" in line:
                health = line.split(":")[-1].strip()
                data["health"] = "PASSED" if "PASSED" in health else "FAILED"
            elif "Temperature:" in line or "Temperature Celsius" in line:
                try:
                    temp = line.split(":")[-1].split()[0].strip()
                    data["temperature"] = float(temp)
                except Exception as exc:
                    note_failure("SMARTDISK:L244", exc)
            elif "Power_On_Hours" in line:
                try:
                    poh = line.split()[-1].strip()
                    data["power_on_hours"] = int(poh)
                except Exception as exc:
                    note_failure("SMARTDISK:L250", exc)
            elif line.startswith("ID#") and "ATTRIBUTE_NAME" in line:
                data["attributes"] = self._parse_attributes(output)
                break

        return data

    def _parse_attributes(self, output: str) -> list[dict]:
        """Parse SMART attributes table."""
        attributes = []
        in_table = False

        for line in output.split("\n"):
            line = line.strip()
            if "ID# ATTRIBUTE_NAME" in line or "ID# ATTRIBUTE_NAME" in line.replace(
                " ", ""
            ):
                in_table = True
                continue
            if in_table:
                if not line or line.startswith("--") or line.startswith("SMART"):
                    break
                parts = line.split()
                if len(parts) >= 10:
                    try:
                        attr_id = int(parts[0])
                        name = self.config.SMART_NAMES.get(attr_id, "Unknown")
                        value = int(parts[3])
                        worst = int(parts[4])
                        threshold = int(parts[5])
                        raw = int(parts[9]) if len(parts) > 9 else 0

                        status = "OK"
                        if value <= threshold and threshold > 0:
                            status = "WARNING"
                        if value == 0:
                            status = "CRITICAL"

                        attributes.append(
                            {
                                "id": attr_id,
                                "name": name,
                                "value": value,
                                "worst": worst,
                                "threshold": threshold,
                                "raw": raw,
                                "status": status,
                            }
                        )
                    except Exception as exc:
                        note_failure("SMARTDISK:L300", exc)

        return attributes

    def get_disks(self) -> list[str]:
        """list available disks."""
        output = self._run_smartctl(["--scan", "-d", "sat"])
        disks = []
        for line in output.split("\n"):
            if line.strip() and not line.startswith("#"):
                parts = line.split()[0]
                if parts:
                    disks.append(parts)
        return disks

    def get_disk_info(self, device: str = "/dev/sda") -> dict[str, Any]:
        """Get detailed disk information."""
        output = self._run_smartctl(["-a", "-d", "sat", device])
        return self._parse_smart_output(output)

    def get_disk_health(self, device: str = "/dev/sda") -> DiskHealth:
        """Calculate disk health score."""
        info = self.get_disk_info(device)

        attributes = info.get("attributes", [])
        health_score = 100.0
        critical_count = 0
        warning_count = 0

        for attr in attributes:
            if attr["status"] == "CRITICAL":
                health_score -= 20
                critical_count += 1
            elif attr["status"] == "WARNING":
                health_score -= 5
                warning_count += 1

            if attr["id"] in [5, 197, 198]:
                if attr["raw"] > 0:
                    health_score -= min(attr["raw"] * 0.5, 30)

        health_score = max(0, min(100, health_score))

        status = (
            "HEALTHY"
            if health_score >= 80
            else "DEGRADED"
            if health_score >= 50
            else "CRITICAL"
        )
        if info["health"] == "FAILED":
            status = "FAILED"

        recommendations = []
        if health_score < 80:
            recommendations.append("Consider backing up important data")
        if health_score < 50:
            recommendations.append("Plan disk replacement soon")
        if critical_count > 0:
            recommendations.append("IMMEDIATE: Disk showing critical SMART errors")

        temp = info["temperature"]
        if temp > 60:
            recommendations.append(f"High temperature ({temp}Â°C) - improve cooling")

        poh = info.get("power_on_hours", 0)
        remaining_days = max(0, (365 * 5) - (poh / 24))

        return DiskHealth(
            device=device,
            model=info.get("model", "Unknown"),
            health_score=round(health_score, 1),
            failure_probability=max(0, (100 - health_score) / 100),
            status=status,
            smart_attributes=attributes,
            temperature=temp,
            power_on_hours=poh,
            estimated_remaining_days=int(remaining_days),
            recommendations=recommendations,
        )

    def predict_failure(self, device: str = "/dev/ssa") -> dict[str, Any]:
        """Predict disk failure probability."""
        health = self.get_disk_health(device)

        failure_prob = health.failure_probability

        if health.health_score < 50:
            failure_prob += 0.3
        if health.health_score < 30:
            failure_prob += 0.3

        for attr in health.smart_attributes:
            if attr["id"] in [5, 187, 197, 198]:
                if attr["raw"] > 100:
                    failure_prob += 0.1
                if attr["raw"] > 1000:
                    failure_prob += 0.2

        failure_prob = min(1.0, failure_prob)

        risk_level = (
            "LOW"
            if failure_prob < 0.1
            else "MEDIUM"
            if failure_prob < 0.3
            else "HIGH"
            if failure_prob < 0.6
            else "CRITICAL"
        )

        return {
            "device": device,
            "failure_probability": round(failure_prob, 3),
            "risk_level": risk_level,
            "estimated_days_to_failure": health.estimated_remaining_days
            if failure_prob > 0.3
            else None,
            "health_score": health.health_score,
            "recommendations": health.recommendations,
            "timestamp": datetime.now().isoformat(),
        }

    def get_alerts(self) -> list[DiskAlert]:
        """Generate disk health alerts."""
        alerts = []

        disks = self.get_disks()
        for disk in disks:
            health = self.get_disk_health(disk)

            if health.status == "FAILED":
                alerts.append(
                    DiskAlert(
                        device=disk,
                        alert_type="FAILURE",
                        severity="CRITICAL",
                        message=f"Disk {disk} ({health.model}) has failed SMART check",
                        recommended_action="Replace disk immediately and restore from backup",
                        timestamp=datetime.now().isoformat(),
                    )
                )

            for attr in health.smart_attributes:
                if attr["status"] == "CRITICAL":
                    alerts.append(
                        DiskAlert(
                            device=disk,
                            alert_type="SMART_CRITICAL",
                            severity="HIGH",
                            message=f"Critical SMART attribute {attr['name']}: value={attr['value']}",
                            recommended_action="Back up data immediately, plan disk replacement",
                            timestamp=datetime.now().isoformat(),
                        )
                    )

            if health.temperature > 65:
                alerts.append(
                    DiskAlert(
                        device=disk,
                        alert_type="TEMPERATURE",
                        severity="MEDIUM" if health.temperature < 75 else "HIGH",
                        message=f"High temperature: {health.temperature}Â°C",
                        recommended_action="Improve system cooling, check airflow",
                        timestamp=datetime.now().isoformat(),
                    )
                )

        return alerts

    def record_history(self, device: str = "/dev/sda"):
        """Record disk metrics to history."""
        health = self.get_disk_health(device)
        record = {
            "timestamp": datetime.now().isoformat(),
            "device": device,
            "health_score": health.health_score,
            "temperature": health.temperature,
            "power_on_hours": health.power_on_hours,
            "failure_probability": health.failure_probability,
        }

        self.disk_history.append(record)
        if len(self.disk_history) > 1000:
            self.disk_history = self.disk_history[-1000:]

        self._save_history()

        if len(self.disk_history) >= 100 and SKLEARN_AVAILABLE:
            self._train_failure_model()

    def _train_failure_model(self):
        """Train failure prediction model."""
        if not SKLEARN_AVAILABLE or len(self.disk_history) < 100:
            return

        try:
            features = []
            targets = []

            for i in range(10, len(self.disk_history)):
                if i + 10 < len(self.disk_history):
                    features.append(
                        [
                            self.disk_history[i]["health_score"],
                            self.disk_history[i]["temperature"],
                            self.disk_history[i]["power_on_hours"],
                        ]
                    )
                    targets.append(self.disk_history[i + 10]["failure_probability"])

            if len(features) > 50:
                self.failure_model = RandomForestRegressor(n_estimators=100)
                self.failure_model.fit(features, targets)
                self._save_model()
        except Exception as e:
            print(f"Failed to train model: {e}")

    def analyze_trends(
        self, device: str = "/dev/sda", hours: int = 168
    ) -> dict[str, Any]:
        """Analyze disk health trends."""
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = [
            h
            for h in self.disk_history
            if datetime.fromisoformat(h["timestamp"]) > cutoff
        ]

        if not recent:
            return {"message": "No historical data"}

        scores = [h["health_score"] for h in recent]
        temps = [h["temperature"] for h in recent]

        return {
            "device": device,
            "period_hours": hours,
            "samples": len(recent),
            "health_score": {
                "current": scores[-1] if scores else 0,
                "mean": statistics.mean(scores) if scores else 0,
                "min": min(scores) if scores else 0,
                "trend": "declining"
                if len(scores) > 10 and scores[-1] < scores[0] * 0.9
                else "stable",
            },
            "temperature": {
                "current": temps[-1] if temps else 0,
                "mean": statistics.mean(temps) if temps else 0,
                "max": max(temps) if temps else 0,
            },
        }

    def _save_history(self):
        """Persist history."""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(self.disk_history, f, indent=2)

    def _load_history(self):
        """Load persisted history."""
        if self.history_file.exists():
            try:
                with open(self.history_file, encoding="utf-8") as f:
                    self.disk_history = json.load(f)
            except Exception as exc:
                note_failure("SMARTDISK:L567", exc)
                self.disk_history = []

    def _save_model(self):
        """Persist failure model."""
        if not SKLEARN_AVAILABLE or self.failure_model is None:
            return
        try:
            with open(self.model_file, "wb") as f:
                pickle.dump(self.failure_model, f)
        except Exception as exc:
            note_failure("SMARTDISK:L577", exc)

    def _load_model(self):
        """Load persisted model."""
        if not SKLEARN_AVAILABLE or not self.model_file.exists():
            return
        try:
            with open(self.model_file, "rb") as f:
                self.failure_model = pickle.load(f)
        except Exception as exc:
            note_failure("SMARTDISK:L587", exc)

    def get_status(self) -> dict[str, Any]:
        """Get disk status summary."""
        disks = self.get_disks()
        return {
            "version": self.VERSION,
            "disks_found": len(disks),
            "model_trained": self.failure_model is not None,
            "history_samples": len(self.disk_history),
        }

    def run_popup(self):
        """Run with GUI popup."""
        root = tk.Tk()
        root.title("SMARTDISK - Disk Health Monitoring")
        root.geometry("800x600")

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        health_tab = ttk.Frame(notebook)
        notebook.add(health_tab, text="Health")

        alerts_tab = ttk.Frame(notebook)
        notebook.add(alerts_tab, text="Alerts")

        trends_tab = ttk.Frame(notebook)
        notebook.add(trends_tab, text="Trends")

        status_label = ttk.Label(health_tab, text="Initializing...", font=("Arial", 14))
        status_label.pack(pady=10)

        log_text = scrolledtext.ScrolledText(alerts_tab, width=80, height=20)
        log_text.pack(fill="both", expand=True, padx=10, pady=10)

        def refresh():
            disks = self.get_disks()
            if disks:
                health = self.get_disk_health(disks[0])
                prediction = self.predict_failure(disks[0])

                status_text = f"""Disk: {health.model}
Health Score: {health.health_score}%
Status: {health.status}
Temperature: {health.temperature}Â°C
Power On Hours: {health.power_on_hours}
Failure Risk: {prediction["risk_level"]}
Recommendations: {len(health.recommendations)}"""
                status_label.config(text=status_text)

                alerts = self.get_alerts()
                alert_text = json.dumps(
                    [
                        {
                            "device": a.device,
                            "severity": a.severity,
                            "message": a.message,
                        }
                        for a in alerts
                    ],
                    indent=2,
                )
                log_text.delete("1.0", "end")
                log_text.insert("end", alert_text or "No alerts")

        ttk.Button(health_tab, text="Refresh", command=refresh).pack(pady=5)
        ttk.Button(health_tab, text="Record History", command=self.record_history).pack(
            pady=5
        )

        root.mainloop()


def main():
    """CLI entry point."""

    parser = argparse.ArgumentParser(description="SMARTDISK - Disk Health Monitoring")
    parser.add_argument("--version", action="version", version="1.0.0")
    parser.add_argument("--list", action="store_true", help="list disks")
    parser.add_argument("--health", help="Get disk health")
    parser.add_argument("--predict", help="Predict failure")
    parser.add_argument("--alerts", action="store_true", help="Get alerts")
    parser.add_argument("--record", action="store_true", help="Record history")
    parser.add_argument("--trends", type=int, default=168, help="Analyze trends")
    parser.add_argument("--popup", action="store_true", help="Launch GUI")

    args = parser.parse_args()
    sd = SMARTDISK()

    if args.list:
        print(json.dumps(sd.get_disks(), indent=2))
    elif args.health:
        print(
            json.dumps(
                {
                    "health_score": sd.get_disk_health(args.health).health_score,
                    "status": sd.get_disk_health(args.health).status,
                },
                indent=2,
            )
        )
    elif args.predict:
        print(json.dumps(sd.predict_failure(args.predict), indent=2))
    elif args.alerts:
        alerts = sd.get_alerts()
        print(
            json.dumps(
                [
                    {
                        "device": a.device,
                        "severity": a.severity,
                        "message": a.message,
                    }
                    for a in alerts
                ],
                indent=2,
            )
        )
    elif args.record:
        sd.record_history()
        print("History recorded")
    elif args.trends:
        print(json.dumps(sd.analyze_trends(hours=args.trends), indent=2))
    elif args.popup:
        sd.run_popup()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()


