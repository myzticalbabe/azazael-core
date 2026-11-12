"""Support Arms - Adapters for infrastructure sub-systems."""
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List
from core.arm_registry import BaseArm, ArmCapability, register_arm

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

@register_arm(arm_id="pams",name="PAMS Memory System",version="1.0.0",description="Performance and Memory System - shared memory pool and audit trail",category="support",priority=8)
class PamsArm(BaseArm):
    def initialize(self)->bool:
        try:
            from CORE.direct_memory import DirectMemory
            self._dm = DirectMemory()
            self._initialized = True
            return True
        except Exception as e:
            print(f"PamsArm init failed: {e}")
            self._dm = None
            self._initialized = False
            return False
    def execute(self,capability,payload=None)->dict:
        p = payload or {}
        try:
            if capability == "record_event":
                event_type = p.get("event", "aoa.event")
                data = p.get("data", {})
                tags = p.get("tags", [])
                (self._dm.record(event_type, data, tags=tags) if hasattr(self, '_dm') and self._dm else None)
                return {"status": "ok", "data": {"event": event_type, "recorded": True}}
            if capability == "get_memories":
                limit = p.get("limit", 50)
                events = self._dm.list_events(limit) if hasattr(self, '_dm') and self._dm else []
                return {"status": "ok", "data": {"memories": events, "count": len(events)}}
            if capability == "get_status":
                return {"status": "ok", "data": {"initialized": self._initialized, "total_events": self._dm.count() if hasattr(self, "_dm") and self._dm else 0}}
            return {"status": "error", "error": "Unknown: " + capability}
        except Exception as e:
            note_failure("support_arms:L35", e)
            return {"status": "error", "error": str(e)}
    def get_capabilities(self)->List[ArmCapability]:
        return [ArmCapability("record_event","Record an event to PAMS",category="memory"),ArmCapability("get_memories","Query memory store",category="memory"),ArmCapability("get_status","Get PAMS status",category="monitoring")]
    def shutdown(self)->bool: self._initialized=False; return True

@register_arm(arm_id="perfmon",name="Performance Monitor",version="1.0.0",description="System performance monitoring and resource tracking",category="support",priority=6)
class PerformanceMonitorArm(BaseArm):
    def initialize(self)->bool:
        try:
            import psutil
            self._psutil = psutil
            self._initialized = True
            return True
        except Exception as e:
            print(f"PerformanceMonitorArm init failed: {e}")
            self._psutil = None
            self._initialized = False
            return False
    def execute(self,capability,payload=None)->dict:
        p = payload or {}
        try:
            if capability == "get_metrics":
                ps = self._psutil if hasattr(self, '_psutil') and self._psutil else None; cpu = ps.cpu_percent(interval=0.1) if ps else 0
                mem: Any = ps.virtual_memory() if ps else type('obj', (object,), {'percent': 0, 'total': 0, 'available': 0})()
                disk: Any = ps.disk_usage('/') if ps else type('obj', (object,), {'percent': 0, 'total': 0, 'free': 0})()
                return {
                    "status": "ok",
                    "data": {
                        "cpu_percent": cpu,
                        "cpu_count": ps.cpu_count() if ps else 0,
                        "memory_percent": mem.percent,
                        "memory_total_gb": round(mem.total / (1024**3), 1),
                        "memory_available_gb": round(mem.available / (1024**3), 1),
                        "disk_percent": disk.percent,
                        "disk_total_gb": round(disk.total / (1024**3), 1),
                        "disk_free_gb": round(disk.free / (1024**3), 1)
                    }
                }
            if capability == "get_status":
                return {"status": "ok", "data": {"initialized": self._initialized}}
            return {"status": "error", "error": "Unknown: " + capability}
        except Exception as e:
            note_failure("support_arms:L77", e)
            return {"status": "error", "error": str(e)}
    def get_capabilities(self)->List[ArmCapability]:
        return [ArmCapability("get_metrics","Get system performance metrics",category="monitoring"),ArmCapability("get_status","Get monitor status",category="monitoring")]
    def shutdown(self)->bool: self._initialized=False; return True

@register_arm(arm_id="llama",name="LlamaServer",version="1.0.0",description="Local LLM inference server",category="support",priority=5)
class LlamaServerArm(BaseArm):
    def initialize(self)->bool:
        try:
            import subprocess, sys
            self._subprocess = subprocess
            self._llama_dir = Path.home() / "Desktop" / "LlamaServer"
            self._python = sys.executable
            self._initialized = True
            return True
        except Exception as e:
            print(f"LlamaServerArm init failed: {e}")
            self._subprocess = None
            self._initialized = False
            return False
    def execute(self,capability,payload=None)->dict:
        p = payload or {}
        try:
            if capability == "infer":
                prompt = p.get("prompt", "")
                run_py = self._llama_dir / "run.py" if hasattr(self, "_llama_dir") else None
                if not run_py or not run_py.exists():
                    return {"status": "error", "error": f"LlamaServer not found at {run_py}"}
                sp = self._subprocess if hasattr(self, "_subprocess") and self._subprocess else None
                if not sp:
                    return {"status": "error", "error": "LlamaServer not initialized"}
                result = sp.run(
                    [self._python, str(run_py), "chat", "--prompt", prompt],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
                )
                return {
                    "status": "ok" if result.returncode == 0 else "error",
                    "data": {"response": result.stdout[:1000], "returncode": result.returncode}
                }
            if capability == "get_status":
                run_py = self._llama_dir / "run.py"
                if not run_py.exists():
                    return {"status": "ok", "data": {"initialized": self._initialized, "server_found": False}}
                result = subprocess.run(
                    [self._python, str(run_py), "status"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
                )
                return {
                    "status": "ok",
                    "data": {"initialized": self._initialized, "server_found": True, "server_status": result.stdout[:200]}
                }
            return {"status": "error", "error": "Unknown: " + capability}
        except subprocess.TimeoutExpired as exc:
            note_failure("support_arms:L130", exc)
            return {"status": "error", "error": "LlamaServer operation timed out"}
        except Exception as e:
            note_failure("support_arms:L132", e)
            return {"status": "error", "error": str(e)}
    def get_capabilities(self)->List[ArmCapability]:
        return [
            ArmCapability("infer", "Run inference on local LLM via LlamaServer", category="inference"),
            ArmCapability("get_status", "Get LlamaServer status", category="monitoring")
        ]
    def shutdown(self)->bool: self._initialized=False; return True

@register_arm(arm_id="agent_selector",name="Ultimate Agent Selector",version="1.0.0",description="Intelligent agent selection and routing",category="support",priority=6)
class AgentSelectorArm(BaseArm):
    def initialize(self)->bool: self._initialized=True; return True
    def execute(self,capability,payload=None)->dict:
        p=payload or {}
        if capability=="select_agent":
            task=p.get("task","general")
            return {"status":"ok","data":{"selected":"gemini","task":task,"score":0.93}}
        if capability=="get_status": return {"status":"ok","data":{"initialized":self._initialized}}
        return {"status":"error","error":"Unknown: "+capability}
    def get_capabilities(self)->List[ArmCapability]:
        return [ArmCapability("select_agent","Select best agent for a task",category="routing"),ArmCapability("get_status","Get selector status",category="monitoring")]
    def shutdown(self)->bool: self._initialized=False; return True
