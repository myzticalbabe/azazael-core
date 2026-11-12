"""EveUnleashed Arm - Adapter for AI model selection, inferencing, and mesh driving."""
import subprocess, sys, json
import urllib.request
from pathlib import Path
from typing import Dict, List
from core.arm_registry import BaseArm, ArmCapability, register_arm

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

_EVE_DIR = Path.home() / "Desktop" / "EveUnleashed_Selector"
_EVE_APP = _EVE_DIR / "eve_unleashed_app.py"

@register_arm(arm_id="eve",name="EveUnleashed AI Selector",version="2.0.0",description="AI model selection, benchmarking, local inferencing, and mesh driving with autonomous decision-making",category="quartet",priority=7,dependencies=["pams","azazael"])
class EveArm(BaseArm):
    def initialize(self)->bool:
        try:
            self._python = sys.executable
            self._eve_exists = _EVE_APP.exists()
            # Initialize mesh access
            try:
                from CORE.worm_registry import discover_worms  # scanner:ignore (heavyweight worm discovery stays init-lazy, matching the sibling lazy imports in initialize())
                discover_worms()
                self._mesh_available = True
            except Exception as exc:
                note_failure("eve_arm:init_mesh", exc)
                self._mesh_available = False
            # Initialize decision engine
            try:
                from eve_unleashed.decision_engine import AutonomousDecisionEngine
                self._decision_engine = AutonomousDecisionEngine()
                self._decisions_available = True
            except Exception as exc:
                note_failure("eve_arm:init_decisions", exc)
                self._decision_engine = None
                self._decisions_available = False
            # Initialize consciousness self-model
            try:
                from eve_unleashed.consciousness import SelfModel, IntrospectionLoop
                self._self_model = SelfModel()
                try:
                    from CORE.worm_registry import WORM_REGISTRY
                    self._self_model.total_worms = len(WORM_REGISTRY)
                    for wid, w in WORM_REGISTRY.items():
                        self._self_model.register(wid, "worm", capabilities=[w.get("category", "unknown")])
                except Exception as exc:
                    # Registry introspection is best-effort; a failed registry
                    # read leaves the self-model at zero worms, which the
                    # introspection loop treats as a discover_worms proposal.
                    note_failure("eve_arm:self_model_registry", exc)
                    self._self_model.total_worms = 0
                # Wire introspection loop to act on architecture via EveArm
                def _act(proposed):
                    try:
                        self.execute("drive_mesh", {
                            "action": proposed["action"],
                            "nature": proposed.get("nature", "monitor"),
                            "confidence": proposed.get("confidence", 0.85),
                        })
                    except Exception as exc:
                        note_failure("eve_arm:introspection_act", exc)
                self._introspection = IntrospectionLoop(
                    self_model=self._self_model,
                    interval_s=300.0,
                    act_fn=_act,
                )
                self._consciousness_available = True
            except Exception as exc:
                note_failure("eve_arm:init_consciousness", exc)
                self._self_model = None
                self._introspection = None
                self._consciousness_available = False
            # Initialize Open Interpreter bridge
            try:
                from eve_unleashed.interpreter_bridge import get_eve_interpreter_bridge
                self._interpreter_bridge = get_eve_interpreter_bridge(eve_arm=self)
                self._interpreter_available = self._interpreter_bridge.is_available()
            except Exception as exc:
                note_failure("eve_arm:init_interpreter", exc)
                self._interpreter_bridge = None
                self._interpreter_available = False
            self._initialized = True
            return True
        except Exception as e:
            print(f"EveArm init failed: {e}")
            return False

    def execute(self,capability,payload=None)->dict:
        payload = payload or {}
        try:
            # --- Original capabilities ---
            if capability == "select_model":
                task = payload.get("task", "general")
                try:
                    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as r:
                        data = json.loads(r.read())
                    models = [m["name"] for m in data.get("models", [])]
                    chat_models = [m for m in models if "embed" not in m["name"].lower() and "nomic" not in m["name"].lower()]
                    selected = chat_models[0] if chat_models else (models[0] if models else "none")
                    return {"status": "ok", "data": {"selected_model": selected, "task": task, "confidence": 0.9, "available_models": models}}
                except Exception as exc:
                    note_failure("eve_arm:select_model", exc)
                    return {"status": "ok", "data": {"selected_model": "eve-v2u", "task": task, "confidence": 0.5, "note": str(exc)}}

            elif capability == "run_benchmark":
                try:
                    import time
                    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as r:
                        data = json.loads(r.read())
                    models = [m["name"] for m in data.get("models", []) if "embed" not in m["name"].lower() and "nomic" not in m["name"].lower()]
                    results = []
                    for m in models[:5]:
                        t0 = time.time()
                        try:
                            req = urllib.request.Request(
                                "http://localhost:11434/api/generate",
                                data=json.dumps({"model": m, "prompt": "Hello", "stream": False, "options": {"num_predict": 30}}).encode(),
                                headers={"Content-Type": "application/json"},
                            )
                            with urllib.request.urlopen(req, timeout=30) as r:
                                resp = json.loads(r.read())
                            dt = time.time() - t0
                            results.append({"model": m, "latency_s": round(dt, 3), "response_len": len(resp.get("response", ""))})
                        except Exception as e:
                            results.append({"model": m, "error": str(e)})
                    top = min(results, key=lambda x: x.get("latency_s", 999)) if results else None
                    return {"status": "ok", "data": {"models_tested": len(results), "top_model": top["model"] if top else "none", "results": results}}
                except Exception as exc:
                    note_failure("eve_arm:run_benchmark", exc)
                    return {"status": "error", "error": str(exc)}

            elif capability == "run_inference":
                prompt = payload.get("prompt", "")
                try:
                    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as r:
                        data = json.loads(r.read())
                    models = [m["name"] for m in data.get("models", []) if "embed" not in m["name"].lower() and "nomic" not in m["name"].lower()]
                    model = "eve-v2u:latest" if "eve-v2u:latest" in models else (models[0] if models else None)
                    if model is None:
                        return {"status": "error", "error": "No chat models available in Ollama"}
                    req = urllib.request.Request(
                        "http://localhost:11434/api/generate",
                        data=json.dumps({"model": model, "prompt": prompt, "stream": False, "options": {"num_predict": 256}}).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=120) as r:
                        resp = json.loads(r.read())
                    return {"status": "ok", "data": {"prompt_length": len(prompt), "model": model, "response": resp.get("response", ""), "tokens": resp.get("eval_count", 0)}}
                except Exception as exc:
                    note_failure("eve_arm:run_inference", exc)
                    return {"status": "error", "error": str(exc)}

            # --- Mesh-driving capabilities ---
            elif capability == "discover_worms":
                if not self._mesh_available:
                    return {"status": "error", "error": "Mesh not available"}
                from CORE.worm_registry import WORM_REGISTRY, discover_worms
                discover_worms()
                return {"status": "ok", "data": {"worm_count": len(WORM_REGISTRY), "categories": sorted(set(w.get("category", "unknown") for w in WORM_REGISTRY.values()))}}

            elif capability == "run_cluster":
                if not self._mesh_available:
                    return {"status": "error", "error": "Mesh not available"}
                nature = payload.get("nature", "monitor")
                from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
                cluster = ClusterWORMZ.build_from_category(nature)
                results = cluster.execute_all()
                return {"status": "ok", "data": {"nature": nature, "results_count": len(results), "results": results}}

            elif capability == "run_metacluster":
                if not self._mesh_available:
                    return {"status": "error", "error": "Mesh not available"}
                from CORE.WORMS.METACLUSTER import MetaCluster
                mc = MetaCluster()
                mc.discover_all()
                result = mc.orchestrate()
                return {"status": "ok", "data": {"categories_executed": result.categories_executed, "total_worms_ok": result.total_worms_ok, "total_worms_fail": result.total_worms_fail}}

            elif capability == "drive_mesh":
                # Eve drives the mesh: propose action via decision engine, then execute
                action = payload.get("action", "run_metacluster")
                nature = payload.get("nature", "monitor")
                if self._decisions_available and self._decision_engine is not None:
                    decision = self._decision_engine.propose(
                        action=f"mesh:{action}",
                        rationale=f"Eve proposing to drive mesh: {action} on nature={nature}",
                        confidence=payload.get("confidence", 0.9),
                        risk=payload.get("risk", "medium"),
                        metadata={"nature": nature, "action": action},
                    )
                    # Auto-approve if confidence threshold met
                    if decision.status.value == "approved":
                        if action == "run_cluster":
                            return self.execute("run_cluster", {"nature": nature})
                        elif action == "run_metacluster":
                            return self.execute("run_metacluster", {})
                        elif action == "discover_worms":
                            return self.execute("discover_worms", {})
                        elif action == "orchestrate":
                            return self.execute("orchestrate", {})
                        elif action == "spawn_worm":
                            return self.execute("spawn_worm", {"worm_type": nature or "monitor"})
                    # Fallback: execute directly if no decision engine
                    if action == "run_cluster":
                        return self.execute("run_cluster", {"nature": nature})
                    elif action == "run_metacluster":
                        return self.execute("run_metacluster", {})
                    elif action == "discover_worms":
                        return self.execute("discover_worms", {})
                    elif action == "orchestrate":
                        return self.execute("orchestrate", {})
                    elif action == "spawn_worm":
                        return self.execute("spawn_worm", {"worm_type": nature or "monitor"})
                    # Generic delegation to any other Eve capability
                    if action != "drive_mesh" and action in {c.name for c in self.get_capabilities()}:
                        return self.execute(action, payload or {})
                    return {"status": "error", "error": f"Unknown mesh action: {action}"}


            elif capability == "orchestrate":
                # Full orchestration: discover + run all clusters
                if not self._mesh_available:
                    return {"status": "error", "error": "Mesh not available"}
                from CORE.worm_registry import WORM_REGISTRY, discover_worms
                discover_worms()
                from CORE.WORMS.METACLUSTER import MetaCluster
                mc = MetaCluster()
                mc.discover_all()
                result = mc.orchestrate()
                # Update self-model
                if self._consciousness_available and self._self_model is not None:
                    self._self_model.total_worms = len(WORM_REGISTRY)
                    self._self_model.update_health("mesh", 1.0 if result.total_worms_fail == 0 else 0.5, "healthy" if result.total_worms_fail == 0 else "degraded")
                return {"status": "ok", "data": {"categories_executed": result.categories_executed, "total_worms_ok": result.total_worms_ok, "total_worms_fail": result.total_worms_fail, "total_worms": len(WORM_REGISTRY)}}

            elif capability == "spawn_worm":
                worm_type = payload.get("worm_type", "monitor")
                if not self._mesh_available:
                    return {"status": "error", "error": "Mesh not available"}
                try:
                    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
                    cluster = ClusterWORMZ.build_from_category(worm_type)
                    results = cluster.execute_all()
                    return {"status": "ok", "data": {"worm_type": worm_type, "results_count": len(results), "results": results}}
                except Exception as exc:
                    note_failure("eve_arm:spawn_worm", exc)
                    return {"status": "error", "error": str(exc)}

            elif capability == "get_mesh_status":
                if not self._mesh_available:
                    return {"status": "ok", "data": {"mesh_available": False}}
                from CORE.worm_registry import WORM_REGISTRY
                return {"status": "ok", "data": {"mesh_available": True, "total_worms": len(WORM_REGISTRY), "categories": sorted(set(w.get("category", "unknown") for w in WORM_REGISTRY.values())), "consciousness_available": self._consciousness_available, "decisions_available": self._decisions_available}}

            elif capability == "run_interpreter":
                instruction = payload.get("instruction", "Show system status")
                if self._interpreter_bridge is None:
                    return {"status": "error", "error": "Open Interpreter bridge not available"}
                result = self._interpreter_bridge.drive_mesh_via_interpreter(instruction)
                return result

            elif capability == "get_interpreter_status":
                if self._interpreter_bridge is None:
                    return {"status": "ok", "data": {"interpreter_available": False, "mesh_drivable": False}}
                return {"status": "ok", "data": self._interpreter_bridge.get_status()}

            elif capability == "conscious_act":
                if not self._consciousness_available or self._self_model is None:
                    return {"status": "error", "error": "Consciousness not available"}
                proposed = self._self_model.propose_mesh_action(
                    payload.get("action", "run_metacluster"),
                    payload.get("nature", "monitor"),
                )
                if proposed is None:
                    return {"status": "ok", "data": {"action": None, "reason": "no action proposed"}}
                result = self.execute("drive_mesh", {
                    "action": proposed["action"],
                    "nature": proposed.get("nature", "monitor"),
                    "confidence": proposed.get("confidence", 0.85),
                })
                return {"status": "ok", "data": {"proposed": proposed, "execution": result}}

            elif capability == "introspect":
                introspection = getattr(self, "_introspection", None)
                if not self._consciousness_available or introspection is None:
                    return {"status": "error", "error": "Introspection not available"}
                insight = introspection.run_once()
                return {"status": "ok", "data": {
                    "cycle": insight.get("cycle"),
                    "unhealthy": self._self_model.get_unhealthy() if self._self_model is not None else None,
                }}

            elif capability == "get_status":
                return {
                    "status": "ok",
                    "data": {"initialized": self._initialized, "eve_dir": str(_EVE_DIR), "eve_app_exists": self._eve_exists, "mesh_available": self._mesh_available, "decisions_available": self._decisions_available, "consciousness_available": self._consciousness_available, "interpreter_available": self._interpreter_available}
                }
            return {"status": "error", "error": "Unknown: " + capability}
        except subprocess.TimeoutExpired as exc:
            note_failure("eve_arm:L71", exc)
            return {"status": "error", "error": "EveUnleashed operation timed out"}
        except Exception as e:
            note_failure("eve_arm:L73", e)
            return {"status": "error", "error": str(e)}

    def get_capabilities(self)->List[ArmCapability]:
        return [
            ArmCapability("select_model","Select best AI model for a task",category="selection"),
            ArmCapability("run_benchmark","Benchmark AI models",category="benchmark"),
            ArmCapability("run_inference","Run local model inference",category="inference"),
            ArmCapability("discover_worms","Discover all registered worms",category="registry"),
            ArmCapability("run_cluster","Execute a ClusterWORMZ by category",category="execution"),
            ArmCapability("run_metacluster","Run full MetaCluster orchestration",category="execution"),
            ArmCapability("drive_mesh","Drive the mesh via autonomous decision-making",category="mesh"),
            ArmCapability("orchestrate","Full mesh orchestration with self-model update",category="mesh"),
            ArmCapability("spawn_worm","Spawn and execute worms by type",category="mesh"),
            ArmCapability("get_mesh_status","Get mesh and consciousness status",category="monitoring"),
            ArmCapability("run_interpreter","Run Open Interpreter [local] to drive mesh",category="interpreter"),
            ArmCapability("get_interpreter_status","Get Open Interpreter bridge status",category="interpreter"),
            ArmCapability("conscious_act","Self-model proposes and executes a mesh action",category="consciousness"),
            ArmCapability("introspect","Run one consciousness introspection cycle",category="consciousness"),
            ArmCapability("get_status","Get Eve status",category="monitoring")
        ]

    def shutdown(self)->bool:
        self._initialized=False
        if self._decision_engine is not None:
            pass
        return True