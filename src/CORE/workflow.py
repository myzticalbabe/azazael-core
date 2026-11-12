# coding: utf-8
"""


__all__ = [
    "BUILTIN_TEMPLATES",
    "ConditionalRouter",
    "DependencyGraph",
    "StepStatus",
    "Workflow",
    "WorkflowEngine",
    "WorkflowResult",
    "WorkflowStep",
    "WorkflowTemplate",
    "get_workflow_engine",
]

CORE/workflow.py — Workflow Engine for Azazael

Provides:
- WorkflowTemplate: reusable workflow blueprints with parameterized steps
- WorkflowDSL: simple JSON/YAML-based workflow definition language
- ConditionalRouter: route execution based on previous step results
- DependencyGraph: resolve worm execution order from dependencies

Usage:
    from CORE.workflow import WorkflowEngine, WorkflowTemplate, WorkflowDSL

    # Define a workflow from a template
    engine = WorkflowEngine()
    wf = engine.create_from_template("daily_training", {
        "n_episodes": 100,
        "source": "SystemZ",
    })
    result = engine.execute(wf)
"""

import os
import time
import threading
from datetime import datetime, timezone
from typing import Any, List, Optional
from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed  # TimeoutError is a builtin — no import needed

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: EP237 MapBeforeAct substrate. Top-level (not lazy) so the import is
#: counted once by the anti-pattern ratchet; `None` only when the sibling
#: module is absent, and the seam treats that as map-unavailable (advice
#: stays None, noted) rather than a silent skip.
try:
    from CORE.graph_artifacts import MapBeforeAct as _MapBeforeAct
except ImportError:                             # never break the host
    _MapBeforeAct = None  # substrate absent; seam stands off loudly

#: EP44 ReAct driver (thought -> action -> observation). Imported at module level
#: because a workflow step is now able to use it; `None` only when the sibling
#: module is absent, which makes the `react_drive` action a NAMED error rather
#: than a silent no-op -- a step that cannot reason must not look like one that did.
try:
    from CORE.structured_action import drive as _react_drive_impl
    from CORE.structured_action import record_reasoning_trace as _record_reasoning_trace
except ImportError as exc:
    note_failure("workflow:structured_action-import", exc)
    _react_drive_impl = None
    _record_reasoning_trace = None

#: EP44 (completion): `drive_for_workflow_step()` is the host entry the sibling
#: module documents for an engine STEP -- it returns the plan `mutations` the graph
#: must apply, which plain `drive()` does not carry. Imported in its own guard so a
#: sibling without it degrades to the drive()+cite pair below instead of losing the
#: driver entirely.
try:
    from CORE.structured_action import drive_for_workflow_step as _react_step_drive
except ImportError as exc:
    note_failure("workflow:react-step-entry-import", exc)
    _react_step_drive = None

# -- Lazy Config (thread-safe) ------------------------------------
_cfg = None
_cfg_lock = threading.Lock()


def _get_config():
    """Lazy config — double-checked locking for Python 3.14t free-threading."""
    global _cfg
    if _cfg is None:
        with _cfg_lock:
            if _cfg is None:
                try:
                    from CORE.config import get_config
                    _cfg = get_config()
                except ImportError as exc:
                    note_failure("workflow:L60", exc)
                    _cfg = False
    return _cfg if _cfg else None


# =======================================================================
# DATA TYPES
# =======================================================================

class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class WorkflowStep:
    """A single step in a workflow."""
    id: str
    action: str                      # e.g., "execute_worm", "run_pipeline", "condition"
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)  # Step IDs this depends on
    condition: (str | None) = None  # Optional condition expression (DSL)
    timeout: int = 300
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: (str | None) = None
    started_at: (str | None) = None
    completed_at: (str | None) = None
    duration_ms: float = 0.0
    # EP237 caller seam: shadow-stage map advice attached by _execute_step.
    # Advisory only — never read by dispatch, so a wrong map cannot steer
    # execution. None when the seam is off or the consultation failed.
    map_advice: Any = None


@dataclass
class Workflow:
    """A complete executable workflow."""
    id: str
    name: str
    description: str = ""
    steps: list[WorkflowStep] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)  # Template parameter overrides
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tags: list[str] = field(default_factory=list)


@dataclass
class WorkflowResult:
    """Result of a workflow execution."""
    workflow_id: str
    workflow_name: str
    started_at: str
    completed_at: str
    duration_ms: float
    step_results: dict[str, Any] = field(default_factory=dict)
    success_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    errors: list[str] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    plan_order: dict[str, list[str]] = field(default_factory=dict)
    plan_mutations: list[dict[str, Any]] = field(default_factory=list)


# =======================================================================
# WORKFLOW TEMPLATES
# =======================================================================

@dataclass
class WorkflowTemplate:
    """A reusable workflow blueprint."""
    name: str
    description: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)  # {param_name: default_value}
    tags: list[str] = field(default_factory=list)


# Built-in templates
BUILTIN_TEMPLATES = {
    "daily_training": WorkflowTemplate(
        name="daily_training",
        description="Daily ML training pipeline: extract -> engineer -> train -> version",
        parameters={
            "n_episodes": 50,
            "source": None,
            "data_limit": 200,
            "register_model": True,
        },
        steps=[
            {
                "id": "extract",
                "action": "run_pipeline",
                "params": {
                    "pipeline": "DataPipelineWorm",
                    "method": "extract_from_memory",
                    "args": {"limit": "${data_limit}", "source": "${source}"},
                },
            },
            {
                "id": "engineer",
                "action": "execute_worm",
                "params": {
                    "worm": "FeatureEngineer",
                    "method": "extract_worm_features",
                    "args": {"records": "${extract.result}"},
                },
                "depends_on": ["extract"],
            },
            {
                "id": "train",
                "action": "execute_worm",
                "params": {
                    "worm": "RLTrainLoop",
                    "method": "train_from_memory",
                    "args": {
                        "n_episodes": "${n_episodes}",
                        "source": "${source}",
                    },
                },
                "depends_on": ["engineer"],
                "condition": "engineer.success == true",
            },
            {
                "id": "register",
                "action": "execute_worm",
                "params": {
                    "worm": "ModelVersioner",
                    "method": "register_model",
                    "args": {
                        "name": "daily_training",
                        "version": "auto",
                        "metrics": {"avg_reward": "${train.result.avg_reward}"},
                    },
                },
                "depends_on": ["train"],
                "condition": "${register_model} == true",
            },
        ],
    ),
    "system_health_check": WorkflowTemplate(
        name="system_health_check",
        description="Run system health check: monitor -> diagnose -> report",
        parameters={
            "parallel": False,
        },
        steps=[
            {
                "id": "monitor",
                "action": "execute_nature",
                "params": {"nature": "monitor", "parallel": False},
            },
            {
                "id": "diagnose",
                "action": "execute_nature",
                "params": {"nature": "diagnostic", "parallel": False},
                "depends_on": ["monitor"],
                "condition": "monitor.result.failure_count > 0",
            },
            {
                "id": "report",
                "action": "execute_worm",
                "params": {
                    "worm": "DashboardGen",
                    "method": "execute",
                },
                "depends_on": ["monitor", "diagnose"],
            },
        ],
    ),
    "security_audit": WorkflowTemplate(
        name="security_audit",
        description="Security audit: scan -> analyze -> report",
        steps=[
            {
                "id": "scan",
                "action": "execute_nature",
                "params": {"nature": "security", "parallel": True},
            },
            {
                "id": "analyze",
                "action": "execute_nature",
                "params": {"nature": "analysis", "parallel": False},
                "depends_on": ["scan"],
                "condition": "scan.result.success_count > 0",
            },
        ],
    ),
}


# =======================================================================
# CONDITIONAL ROUTER
# =======================================================================

class ConditionalRouter:
    """Evaluates conditions to determine if a step should execute.

    Supports simple expression syntax:
    - "step_id.success == true"
    - "step_id.result.value > 10"
    - "${param} == 'value'"
    - "engineer.success == true AND extract.result.count > 0"
    """

    @staticmethod
    def evaluate(condition: str, step_results: dict[str, Any],
                 params: dict[str, Any]) -> bool:
        """Evaluate a condition expression against step results and params.

        Args:
            condition: Condition expression string
            step_results: dict of {step_id: {success, result, ...}}
            params: Workflow parameter overrides

        Returns:
            True if condition is met, False otherwise
        """
        if not condition:
            return True

        expr = condition.strip()

        # Replace ${param} references
        for key, val in params.items():
            placeholder = "${" + key + "}"
            if placeholder in expr:
                expr = expr.replace(placeholder, str(val).lower())

        # Handle AND/OR operators
        if " AND " in expr:
            parts = expr.split(" AND ")
            return all(ConditionalRouter._evaluate_simple(p.strip(), step_results) for p in parts)
        if " OR " in expr:
            parts = expr.split(" OR ")
            return any(ConditionalRouter._evaluate_simple(p.strip(), step_results) for p in parts)

        return ConditionalRouter._evaluate_simple(expr, step_results)

    @staticmethod
    def _evaluate_simple(expr: str, step_results: dict[str, Any]) -> bool:
        """Evaluate a simple condition like 'step_id.success == true' or 'false == true'."""
        # -- Determine comparison operator --
        op_str = None
        if " == " in expr:
            op_str = "=="
        elif " > " in expr:
            op_str = ">"
        elif " < " in expr:
            op_str = "<"
        else:
            return True

        left, right = expr.split(f" {op_str} ", 1)
        left = left.strip()
        right = right.strip().strip("'\"")

        # -- Literal comparison (no dot-path) — e.g. "false == true" --
        if "." not in left:
            left_lower = left.lower()
            right_lower = right.lower()
            if op_str == "==":
                if left_lower in ("true", "false"):
                    return (left_lower == "true") == (right_lower == "true")
                try:
                    return float(left) == float(right)
                except ValueError as exc:
                    note_failure("workflow:L325", exc)
                    return left_lower == right_lower
            elif op_str == ">":
                try:
                    return float(left) > float(right)
                except ValueError as exc:
                    note_failure("workflow:L330", exc)
                    return left_lower > right_lower
            elif op_str == "<":
                try:
                    return float(left) < float(right)
                except ValueError as exc:
                    note_failure("workflow:L335", exc)
                    return left_lower < right_lower

        # -- Dot-path comparison — e.g. "step_id.field == value" --
        parts = left.split(".")
        if len(parts) < 2:
            return True

        step_id = parts[0]
        field_path = parts[1:]
        step = step_results.get(step_id, {})

        # Navigate the field path
        value = step
        for p in field_path:
            if isinstance(value, dict):
                value = value.get(p, None if op_str == "==" else 0)
            else:
                value = None if op_str == "==" else 0
                break

        if op_str == "==":
            if value is None:
                return right.lower() == "none"
            if isinstance(value, bool):
                return value == (right.lower() == "true")
            if isinstance(value, (int, float)):
                try:
                    return value == float(right)
                except ValueError as exc:
                    note_failure("workflow:L364", exc)
                    return str(value) == right
            return str(value).lower() == right.lower()

        elif op_str == ">":
            try:
                return float(value) > float(right)  # pyright: ignore[reportArgumentType]
            except (ValueError, TypeError) as exc:
                note_failure("workflow:L371", exc)
                return False

        elif op_str == "<":
            try:
                return float(value) < float(right)  # pyright: ignore[reportArgumentType]
            except (ValueError, TypeError) as exc:
                note_failure("workflow:L377", exc)
                return False

        return True


# =======================================================================
# DEPENDENCY GRAPH
# =======================================================================

class DependencyGraph:
    """Resolves worm execution order from dependency declarations.

    Supports:
    - Parallel execution of independent steps
    - Sequential execution of dependent steps
    - Cycle detection
    - Topological sort
    """

    def __init__(self):
        self._graph: dict[str, list[str]] = {}  # {step_id: [depends_on_ids]}

    def build(self, steps: list[WorkflowStep]):
        """Build dependency graph from workflow steps."""
        self._graph = {}
        for step in steps:
            self._graph[step.id] = list(step.depends_on)

    def get_execution_order(self) -> list[list[str]]:
        """Get execution order as layers of parallel steps.

        Returns:
            list of layers, where each layer is a list of step IDs that can run in parallel.
        """
        # Compute in-degrees
        in_degree: dict[str, int] = {}
        for step_id, deps in self._graph.items():
            in_degree[step_id] = len(deps)
            for dep in deps:
                if dep not in in_degree:
                    in_degree[dep] = 0

        # Topological sort with layers
        layers = []
        remaining = set(self._graph.keys())
        unresolved = set()

        while remaining:
            # Find steps with no remaining dependencies
            ready = [
                sid for sid in remaining
                if in_degree.get(sid, 0) == 0
            ]
            if not ready:
                # Missing dependencies — check if deps exist in graph
                missing = set()
                for sid in remaining:
                    for dep in self._graph.get(sid, []):
                        if dep not in self._graph:
                            missing.add(dep)
                if missing:
                    # Dependencies that don't exist as steps — treat as blocked
                    unresolved.update(remaining)
                    break
                # Cycle detected
                raise RuntimeError(f"Cycle detected in dependency graph: {remaining}")

            layers.append(ready)
            for sid in ready:
                remaining.remove(sid)
                for step_id, deps in self._graph.items():
                    if sid in deps:
                        in_degree[step_id] -= 1

        return layers

    def detect_cycles(self) -> list[list[str]]:
        """Detect cycles in the dependency graph. Returns list of cycles found."""
        # Tarjan's algorithm for cycle detection
        visited: set[str] = set()
        rec_stack: set[str] = set()
        cycles: list[list[str]] = []

        def dfs(node: str, path: list[str]):
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for dep in self._graph.get(node, []):
                if dep not in visited:
                    dfs(dep, path)
                elif dep in rec_stack:
                    # Found a cycle
                    cycle_start = path.index(dep)
                    cycles.append(path[cycle_start:] + [dep])

            path.pop()
            rec_stack.remove(node)

        for node in self._graph:
            if node not in visited:
                dfs(node, [])

        return cycles

    def get_dependents(self, step_id: str) -> list[str]:
        """Get all steps that depend on the given step."""
        return [sid for sid, deps in self._graph.items() if step_id in deps]

    def to_mermaid(self) -> str:
        """Generate Mermaid graph of dependencies."""
        lines = ["graph LR"]
        lines.append('    title["Workflow Dependency Graph"]')
        for step_id, deps in self._graph.items():
            for dep in deps:
                lines.append(f"    {dep}[{dep}] --> {step_id}[{step_id}]")
        return "\n".join(lines)


# =======================================================================
# WORKFLOW ENGINE
# =======================================================================

def _map_target_for(step: "WorkflowStep") -> str:
    """Derive the act-time map target for a step (EP237 caller seam).

    The step carries no ``target`` field (it names an action, not a thing),
    so the target is derived per action: the worm / nature / pipeline being
    acted on, else the step id itself (still consultable — a miss returns
    notes, never an exception).
    """
    params = step.params or {}
    if step.action == "execute_worm":
        return str(params.get("worm") or step.id)
    if step.action == "execute_nature":
        return str(params.get("nature") or step.id)
    if step.action == "run_pipeline":
        return str(params.get("pipeline") or step.id)
    return step.id


class WorkflowEngine:
    """Executes workflows with dependency resolution and conditional routing.

    Usage:
        engine = WorkflowEngine()

        # From template
        wf = engine.create_from_template("daily_training", {"n_episodes": 100})
        result = engine.execute(wf)

        # From DSL dict
        wf = engine.parse_dsl({
            "name": "custom_flow",
            "steps": [
                {"id": "step1", "action": "execute_worm",
                 "params": {"worm": "SystemZ"}},
            ],
        })
        result = engine.execute(wf)
    """

    def __init__(self):
        self._templates = dict(BUILTIN_TEMPLATES)
        self._history: list[WorkflowResult] = []
        self._lock = threading.Lock()
        self._map_stage = None  # EP237: lazy MapBeforeAct, one graph per engine

    def _get_map_stage(self):
        """Lazily built MapBeforeAct stage, one graph per engine (EP237).

        The graph cost falls once per engine, not per step. Separated for
        tests, which substitute a fake stage without scanning any tree.
        """
        if self._map_stage is None:
            if _MapBeforeAct is None:
                raise RuntimeError("MapBeforeAct substrate unavailable")
            root = os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))
            self._map_stage = _MapBeforeAct(root=root)
        return self._map_stage

    def _consult_map_shadow(self, step: WorkflowStep) -> None:
        """Attach map advice to a step WITHOUT changing execution (EP237).

        Shadow stage: the consultation is recorded on ``step.map_advice``
        for forensics and future promotion, but nothing in dispatch reads
        it. Kill-switch ``MAP_BEFORE_ACT=0`` skips the consultation
        entirely. Any failure (scan, consult, shape) leaves
        ``step.map_advice`` as None and is noted, never raised.
        """
        try:
            if os.environ.get("MAP_BEFORE_ACT", "1") == "0":
                return
            target = _map_target_for(step)
            step.map_advice = self._get_map_stage().consult(target).to_dict()
        except Exception as e:
            note_failure("workflow:map-seam", e)

    # -- Template Management -----------------------------------------

    def register_template(self, template: WorkflowTemplate):
        """Register a custom workflow template."""
        self._templates[template.name] = template

    def list_templates(self) -> list[str]:
        """list all registered template names."""
        return list(self._templates.keys())

    def get_template(self, name: str) -> (WorkflowTemplate | None):
        """Get a template by name."""
        return self._templates.get(name)

    # -- DSL Parsing -------------------------------------------------

    def parse_dsl(self, dsl: dict) -> Workflow:
        """Parse a workflow DSL dict into a Workflow object.

        Expected format:
        {
            "name": "my_workflow",
            "description": "...",
            "params": {"key": "default"},
            "steps": [
                {"id": "s1", "action": "execute_worm",
                 "params": {"worm": "SystemZ"},
                 "depends_on": [],
                 "condition": "..."},
            ]
        }
        """
        wf_id = f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{int(time.time())}"
        steps_dict = dsl.get("steps", [])
        steps = []
        for s in steps_dict:
            steps.append(WorkflowStep(
                id=s["id"],
                action=s.get("action", "execute_worm"),
                params=s.get("params", {}),
                depends_on=s.get("depends_on", []),
                condition=s.get("condition"),
                timeout=s.get("timeout", 300),
            ))

        return Workflow(
            id=wf_id,
            name=dsl.get("name", "unnamed"),
            description=dsl.get("description", ""),
            steps=steps,
            params=dsl.get("params", {}),
            tags=dsl.get("tags", []),
        )

    # -- Template Instantiation --------------------------------------

    def create_from_template(self, template_name: str,
                             params: Optional[dict[str, Any]] = None,
                             name_override: Optional[str] = None) -> (Workflow | None):
        """Create a workflow from a named template with parameter overrides."""
        template = self._templates.get(template_name)
        if not template:
            return None

        wf_id = f"wf_{template_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Merge params: defaults from template, overrides from caller
        merged_params = dict(template.parameters)
        if params:
            merged_params.update(params)

        # Build steps
        steps = []
        for s in template.steps:
            step_params = {}
            for k, v in s.get("params", {}).items():
                if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                    # Parameter reference
                    param_name = v[2:-1]
                    # Handle nested path like "step_id.result"
                    if "." in param_name:
                        step_params[k] = param_name  # Pass as template string
                    else:
                        step_params[k] = merged_params.get(param_name, v)
                else:
                    step_params[k] = v

            steps.append(WorkflowStep(
                id=s["id"],
                action=s.get("action", "execute_worm"),
                params=step_params,
                depends_on=s.get("depends_on", []),
                condition=s.get("condition"),
                timeout=s.get("timeout", 300),
            ))

        return Workflow(
            id=wf_id,
            name=name_override or template.name,
            description=template.description,
            steps=steps,
            params=merged_params,
            tags=list(template.tags),
        )

    # -- Step Execution ----------------------------------------------

    def _execute_step(self, step: WorkflowStep,
                      step_results: dict[str, Any],
                      params: dict[str, Any]) -> Any:
        """Execute a single workflow step."""
        step.status = StepStatus.RUNNING
        step.started_at = datetime.now(timezone.utc).isoformat()
        start = time.perf_counter()

        # EP237 caller seam (shadow stage): consult the map for this step's
        # target and attach the answer to step.map_advice. Advisory only and
        # internally fail-open, so it can never change what runs below.
        self._consult_map_shadow(step)

        try:
            action = step.action
            action_params = step.params

            if action == "execute_worm":
                result = self._run_execute_worm(action_params)
            elif action == "execute_nature":
                result = self._run_execute_category(action_params)
            elif action == "run_pipeline":
                result = self._run_pipeline(action_params, step_results)
            elif action == "react_drive":
                result = self._run_react_drive(action_params)
            elif action == "condition":
                result = self._run_condition(action_params, step_results, params)
            elif action == "sleep":
                duration = float(action_params.get("seconds", 1))
                time.sleep(duration)
                result = {"slept": duration}
            elif action == "log":
                msg = action_params.get("message", "")
                print(f"[Workflow] {msg}", flush=True)
                result = {"logged": msg}
            else:
                raise ValueError(f"Unknown action: {action}")

            step.status = StepStatus.SUCCESS
            step.result = result
            return result

        except Exception as e:
            step.status = StepStatus.FAILED
            step.error = str(e)
            raise

        finally:
            step.duration_ms = (time.perf_counter() - start) * 1000
            step.completed_at = datetime.now(timezone.utc).isoformat()

    def _run_execute_worm(self, params: dict) -> Any:
        """Execute a worm by name and method."""
        worm_name = params.get("worm")
        method_name = params.get("method", "execute")
        args = params.get("args", {})

        try:
            # Resolve worm from registry
            from CORE.worm_registry import WORM_REGISTRY, discover_worms
            discover_worms()

            worm_info = WORM_REGISTRY.get(worm_name)  # pyright: ignore[reportArgumentType]
            if not worm_info:
                raise ValueError(f"Worm '{worm_name}' not found in registry")

            worm_class = worm_info.get("class")
            if not worm_class:
                raise ValueError(f"Worm '{worm_name}' has no class registered")

            instance = worm_class()
            method = getattr(instance, method_name, None)
            if not method:
                raise ValueError(f"Worm '{worm_name}' has no method '{method_name}'")

            return method(**args)

        except ImportError:
            raise ValueError("Worm registry not available")

    def _run_execute_category(self, params: dict) -> Any:
        """Execute a registry category cluster."""
        nature = params.get("category", params.get("nature")) or ""
        parallel = params.get("parallel", False)

        try:
            from CORE.WORMS.METACLUSTER import MetaCluster
            mc = MetaCluster()
            mc.discover_all()
            result = mc.orchestrate_category(nature, parallel=parallel)
            if result:
                return {
                    "success_count": result.total_worms_ok,
                    "failure_count": result.total_worms_fail,
                    "duration_ms": result.duration_ms,
                }
            return {"success_count": 0, "failure_count": 0}
        except Exception as e:
            raise ValueError(f"Failed to execute category '{nature}': {e}")

    def _run_pipeline(self, params: dict, step_results: dict[str, Any]) -> Any:
        """Run a data pipeline step."""
        pipeline_name = params.get("pipeline")
        method_name = params.get("method", "execute")
        args = dict(params.get("args", {}))

        # Resolve template references in args
        for key, val in list(args.items()):
            if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
                ref = val[2:-1]
                # Check if it references a previous step result
                parts = ref.split(".")
                if len(parts) >= 2 and parts[0] in step_results:
                    step_data = step_results[parts[0]]
                    field_path = parts[1:] if len(parts) > 1 else []
                    resolved = step_data
                    for p in field_path:
                        if isinstance(resolved, dict):
                            resolved = resolved.get(p, val)
                        else:
                            resolved = val
                            break
                    args[key] = resolved
                else:
                    args[key] = val

        if pipeline_name == "DataPipelineWorm":
            from CORE.WORMS.expansion.learning import DataPipelineWorm
            pipeline = DataPipelineWorm()
            method = getattr(pipeline, method_name)
            return method(**args)

        raise ValueError(f"Unknown pipeline: {pipeline_name}")

    def _run_condition(self, params: dict, step_results: dict[str, Any],
                       wf_params: dict) -> bool:
        """Evaluate a condition."""
        condition = params.get("condition", "")
        return ConditionalRouter.evaluate(condition, step_results, wf_params)

    def _run_react_drive(self, params: dict) -> Any:
        """Drive this step with the EP44 ReAct loop (thought -> action -> observation).

        `CORE.structured_action.drive` is LLM-free: the caller supplies cognition
        through `thought_fn` (``fn(state) -> proposal``) and the loop supplies the
        step interleaving, both validations, and the plan repair. A gap does NOT
        fail the step -- it is recorded AND the plan gains a ``repair:<gap>``
        step, which is the technique EP44 is about (a controller that only
        reports gaps is the thing it replaces).
        """
        max_steps = int(params.get("max_steps", 12))
        # All five are needed by BOTH entry points below, so they are bound BEFORE
        # the branch. They used to sit after it, which made the `_react_step_drive`
        # path raise UnboundLocalError on `app` the moment the sibling's host entry
        # became importable -- and left the no-cognition guard unreachable there.
        app = str(params.get("app", "workflow"))
        plan = list(params.get("plan", []) or [])
        contract = dict(params.get("contract", {}) or {})
        required_keys = list(params.get("required_keys", []) or [])
        thought_fn = params.get("thought_fn")
        if not callable(thought_fn):
            raise ValueError(
                "react_drive requires a callable 'thought_fn' in step params "
                "(a step with no cognition would terminate at once and read as done)")

        if _react_step_drive is not None:
            # EP44: the sibling module's own host entry, which also returns the
            # `mutations` this engine must apply to its graph. `cite` stays a step
            # parameter so a caller can keep the run off the hash-chained ledger.
            result = _react_step_drive(
                app, plan, contract, required_keys, thought_fn,
                max_steps=max_steps, cite=bool(params.get("cite", True)))
            result.setdefault("mutations", [])
            return result

        if _react_drive_impl is None:
            raise ValueError("react_drive unavailable: CORE.structured_action "
                             "could not be imported")

        result = _react_drive_impl(
            app, plan, contract, required_keys, thought_fn,
            max_steps=max_steps,
        )
        if _record_reasoning_trace is not None:
            try:
                result["citation"] = _record_reasoning_trace(app, result)
            except Exception as exc:
                note_failure("workflow:react-cite", exc)
        return result

    # -- Workflow Execution ------------------------------------------

    def execute(self, workflow: Workflow, scheduler: Any = None,
                cost_fn: Any = None) -> WorkflowResult:
        """Execute a workflow end-to-end with dependency resolution and conditional routing.

        Args:
            workflow: The workflow to execute
            scheduler: optional `(layer, run_step, step_map, errors) -> None` that
                owns each layer's fan-out (EP209); None keeps the local pool.
            cost_fn: optional `(step) -> float` latency/cost estimate used to order
                a ready layer cheapest-first (EP248); None keeps graph order.

        Returns:
            WorkflowResult with step-by-step results
        """
        start = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()

        # Build dependency graph
        graph = DependencyGraph()
        graph.build(workflow.steps)
        step_map = {s.id: s for s in workflow.steps}

        # Check for cycles
        cycles = graph.detect_cycles()
        if cycles:
            return WorkflowResult(
                workflow_id=workflow.id,
                workflow_name=workflow.name,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=0,
                success=False,
                errors=[f"Cycle detected: {c}" for c in cycles],
            )

        # Resolve execution order
        try:
            layers = graph.get_execution_order()
        except RuntimeError as e:
            note_failure("workflow:L802", e)
            return WorkflowResult(
                workflow_id=workflow.id,
                workflow_name=workflow.name,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=0,
                success=False,
                errors=[str(e)],
            )

        step_results: dict[str, Any] = {}
        success_count = 0
        failure_count = 0
        skipped_count = 0
        errors: list[str] = []
        outputs: dict[str, Any] = {}

        pending_mutations: list[dict[str, Any]] = []
        mutations_lock = threading.Lock()
        applied: list[dict[str, Any]] = []
        plan_order: dict[str, list[str]] = {}

        for layer in layers:
            # EP248: a caller-supplied cost estimator orders a ready layer
            # cheapest-first. With no estimator the layer keeps the graph's order
            # exactly -- and none is invented here, because the latency model this
            # row asks for does not exist in this tree yet.
            if cost_fn is not None:
                layer = self._cost_ordered(layer, step_map, cost_fn)
            plan_order[str(len(plan_order) + 1)] = list(layer)
            # Execute steps in this layer in parallel using ThreadPoolExecutor
            results_lock = threading.Lock()

            def _run_step(step_id: str):
                nonlocal success_count, failure_count, skipped_count
                step = step_map[step_id]

                # Check condition
                if step.condition:
                    should_run = ConditionalRouter.evaluate(
                        step.condition, step_results, workflow.params
                    )
                    if not should_run:
                        with results_lock:
                            step.status = StepStatus.SKIPPED
                            step.result = {"skipped": True, "reason": f"Condition not met: {step.condition}"}
                            step_results[step_id] = {"success": False, "skipped": True}
                            skipped_count += 1
                        return

                try:
                    result = self._execute_step(step, step_results, workflow.params)
                    with results_lock:
                        step_results[step_id] = {
                            "success": True,
                            "result": result,
                            "duration_ms": step.duration_ms,
                        }
                        success_count += 1
                        outputs[step_id] = result
                        if isinstance(result, dict):
                            for mut in result.get("mutations") or []:
                                if isinstance(mut, dict):
                                    pending_mutations.append(mut)
                except Exception as e:
                    note_failure("workflow:L851", e)
                    with results_lock:
                        step_results[step_id] = {
                            "success": False,
                            "error": str(e),
                            "duration_ms": step.duration_ms,
                        }
                        failure_count += 1
                        errors.append(f"Step '{step_id}' failed: {e}")

            # EP209: a supplied scheduler schedules this layer as ONE unit; the
            # local pool still opens (and runs nothing) so the timeout path below
            # stays exactly as it is for the default case.
            _delegated = scheduler is not None
            if _delegated:
                scheduler(layer, _run_step, step_map, errors)
            with ThreadPoolExecutor(max_workers=1 if _delegated else min(len(layer), 8)) as executor:
                fut_map = {executor.submit(_run_step, sid): sid
                           for sid in ([] if _delegated else layer)}
                try:
                    for fut in as_completed(fut_map, timeout=300):
                        # _run_step handles its own exceptions and updates shared state via lock
                        pass
                except TimeoutError as exc:
                    note_failure("workflow:L867", exc)
                    for fut, sid in fut_map.items():
                        if not fut.done():
                            fut.cancel()
                            errors.append(f"Step '{sid}' timed out after 300s")

            # EP44: repairs induced by this layer's react_drive steps become real
            # steps for the NEXT iteration of this loop (appended to `layers`), so
            # a plan repair executes instead of being reported and dropped.
            applied.extend(self._apply_mutations(
                pending_mutations, mutations_lock, step_map, layers))

        duration_ms = (time.perf_counter() - start) * 1000

        result = WorkflowResult(
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=round(duration_ms, 2),
            step_results=step_results,
            success_count=success_count,
            failure_count=failure_count,
            skipped_count=skipped_count,
            errors=errors,
            outputs=outputs,
            plan_order=plan_order,
            plan_mutations=applied,
            success=failure_count == 0,
        )

        # Record to LocalMemoryStore
        try:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
            store.record_event(
                source="WorkflowEngine",
                event_type="workflow_completed",
                data={
                    "workflow_id": workflow.id,
                    "name": workflow.name,
                    "success": result.success,
                    "steps_ok": success_count,
                    "steps_fail": failure_count,
                    "steps_skipped": skipped_count,
                    "duration_ms": result.duration_ms,
                },
                severity="info" if result.success else "warning",
                tags=["workflow", workflow.name],
            )
        except Exception as exc:
            note_failure("workflow:L909", exc)

        with self._lock:
            self._history.append(result)
            if len(self._history) > 100:
                self._history = self._history[-50:]

        return result

    # -- EP209 / EP248 / EP44 host seams -----------------------------

    def submit_workflow(self, workflow: Workflow, executor: Any):
        """Schedule the WHOLE workflow as one unit on a caller-supplied executor.

        The schedulable unit is the workflow -- it carries its own dependency
        resolution and its own failure boundary -- rather than a bare task. This
        hands that unit to anything exposing `.submit(fn)` (`WorkStealingPool`,
        `AgentPool`, a `ThreadPoolExecutor`) and returns that executor's Future
        verbatim, so the caller keeps its own cancellation and result handling.
        """
        if executor is None or not hasattr(executor, "submit"):
            raise ValueError(
                "submit_workflow requires an executor exposing .submit(fn)")
        return executor.submit(self.execute, workflow)

    def _cost_ordered(self, layer: list[str], step_map: dict[str, WorkflowStep],
                      cost_fn: Any) -> list[str]:
        """Order one ready layer cheapest-first from a CALLER-supplied estimator.

        The Monte-Carlo latency model this row asks for does not exist in this
        tree, so the engine accepts an estimator instead of inventing one: a
        caller passes `cost_fn(step) -> float` and the layer is ordered by it. A
        step whose cost cannot be computed is NOT guessed at -- it keeps its graph
        position among the unmeasured, which is why the sort key is a triple.
        """
        positions = {sid: idx for idx, sid in enumerate(layer)}

        def _key(sid: str):
            try:
                return (0, float(cost_fn(step_map[sid])), positions[sid])
            except Exception as exc:
                note_failure("workflow:cost-fn", exc)
                return (1, 0.0, positions[sid])

        return sorted(layer, key=_key)

    def _apply_mutations(self, pending: list, lock: Any,
                         step_map: dict[str, WorkflowStep],
                         layers: list[list[str]]) -> list:
        """Materialise react_drive plan repairs into the graph as steps that RUN.

        `structured_action` states the hand-off contract in its own docstring: the
        engine applies the returned `mutations`. Applying them means an inserted
        node becomes a real step in the next layer rather than a field nobody
        reads -- and an id that already exists is SKIPPED, never overwritten,
        because a repair must not silently replace a declared step.
        """
        with lock:
            incoming = list(pending)
            pending.clear()

        applied: list = []
        new_ids: list[str] = []
        for mut in incoming:
            if not isinstance(mut, dict):
                note_failure("workflow:plan-mutation-not-a-dict", None)
                continue
            step_id = str(mut.get("step") or "").strip()
            if not step_id or step_id in step_map:
                note_failure("workflow:plan-mutation-skipped", None)
                continue
            step_map[step_id] = WorkflowStep(
                id=step_id,
                action="log",
                params={"message": "react-plan-repair: %s (because %s)" % (
                    mut.get("insert", "?"), mut.get("because") or "unspecified")},
            )
            new_ids.append(step_id)
            applied.append(mut)

        if new_ids:
            layers.append(new_ids)
        return applied

    # -- Status ------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Get workflow engine status."""
        with self._lock:
            return {
                "templates": len(self._templates),
                "templates_list": list(self._templates.keys()),
                "history_count": len(self._history),
                "last_execution": (
                    {
                        "id": self._history[-1].workflow_id,
                        "name": self._history[-1].workflow_name,
                        "success": self._history[-1].success,
                        "duration_ms": self._history[-1].duration_ms,
                    }
                    if self._history else None
                ),
            }

    def get_history(self, limit: int = 10) -> list[WorkflowResult]:
        """Get recent workflow execution history."""
        with self._lock:
            return self._history[-limit:]


# =======================================================================
# GLOBAL SINGLETON
# =======================================================================

_engine: (WorkflowEngine | None) = None
_engine_lock = threading.Lock()


def get_workflow_engine() -> WorkflowEngine:
    """Get or create the global WorkflowEngine singleton."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = WorkflowEngine()
    return _engine
