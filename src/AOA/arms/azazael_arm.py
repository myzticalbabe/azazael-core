"""Azazael Arm - Adapter for the 77-worm neural mesh system."""
import sys
from typing import Dict, List
from core.arm_registry import BaseArm, ArmCapability, register_arm

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

@register_arm(arm_id="azazael",name="Azazael Neural Mesh",version="3.14.0",description="77-worm neural agent system with 3-tier cluster architecture, PAMS/MemoryBus, and CODEX video generation",category="quartet",priority=10,dependencies=["pams"])
class AzazaelArm(BaseArm):
    def initialize(self)->bool:
        try:
            from CORE.worm_registry import discover_worms
            discover_worms()
            self._initialized=True
            return True
        except Exception as e:
            print("AzazaelArm init failed:",e)
            return False
    def execute(self,capability,payload=None)->dict:
        payload=payload or {}
        try:
            if capability=="discover_worms":
                from CORE.worm_registry import WORM_REGISTRY,discover_worms
                discover_worms()
                return {"status":"ok","data":{"worm_count":len(WORM_REGISTRY),"categories":list(set(w["category"] for w in WORM_REGISTRY.values()))}}
            elif capability=="run_cluster":
                nature=payload.get("nature","monitor")
                from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
                cluster=ClusterWORMZ.build_from_category(nature)
                results=cluster.execute_all()
                return {"status":"ok","data":{"nature":nature,"results_count":len(results),"results":results}}
            elif capability=="run_metacluster":
                from CORE.WORMS.METACLUSTER import MetaCluster
                mc=MetaCluster()
                mc.discover_all()
                result=mc.orchestrate()
                return {"status":"ok","data":{"categories_executed":result.categories_executed,"total_worms_ok":result.total_worms_ok,"total_worms_fail":result.total_worms_fail}}
            elif capability=="get_status":
                from CORE.worm_registry import WORM_REGISTRY
                return {"status":"ok","data":{"total_worms":len(WORM_REGISTRY),"initialized":self._initialized}}
            return {"status":"error","error":"Unknown: "+capability}
        except Exception as e:
            note_failure("azazael_arm:L40", e)
            return {"status":"error","error":str(e)}
    def get_capabilities(self)->List[ArmCapability]:
        return [ArmCapability("discover_worms","Discover all 77 registered worms",category="registry"),ArmCapability("run_cluster","Execute a ClusterWORMZ by category",category="execution"),ArmCapability("run_metacluster","Run full MetaCluster orchestration",category="execution"),ArmCapability("get_status","Get Azazael status",category="monitoring")]
    def shutdown(self)->bool:
        self._initialized=False
        return True
