"""
RULE 0X-1 + 0X-2: Policy-as-Code Gate + Runtime Broker Interception

PolicyEngine provides RBAC/ABAC-style policy evaluation without Casbin.
BrokerInterceptor provides @enforce_gate decorators using wrapt.

Usage:
    engine = PolicyEngine()
    engine.add_rule(PolicyRule("allow", "code_edit", role="developer"))
    assert engine.evaluate("code_edit", role="developer")

    @enforce_gate("code_edit", role="developer")
    def my_func():
        pass
"""

import functools
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Optional wrapt import for robust decorators
try:
    import wrapt
    HAS_WRAPT = True
except ImportError as exc:
    note_failure("policy:L27", exc)
    HAS_WRAPT = False


#: ASIL semantic-interface validation (EP168/EP269/EP327/EP382).
#: `structured_action` shipped with no production caller, so these rows were
#: EXERCISE-ONLY. Optional: when it is missing the gate behaves EXACTLY as
#: before and records the swallow -- an unrecorded fallback would silently
#: change a security decision.
#: Imported by SYMBOL, not as a module attribute: a reachability census reads
#: call sites, and `module.symbol(...)` is invisible to it -- the same blindness
#: that made an aliased import look dead in technique_runtime. The direct form
#: is also how the module is meant to be used.
try:
    from CORE.structured_action import effect_row, observe, semantic_action
except ImportError as exc:
    note_failure("policy:structured_action-import", exc)
    effect_row = observe = semantic_action = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Policy Rule
# ---------------------------------------------------------------------------

class PolicyEffect(str, Enum):
    """Effect vocabulary. ALLOW/DENY are unchanged and stay fail-closed.

    EP382 (ETAS effect-typed agent language): two members could not express a
    conditional grant or a duty, which is the gap the row names. These
    additions are additive -- ALLOW and DENY keep their exact meaning, and a
    rule that declares no obligations grants exactly as it did before.
    """

    ALLOW = "allow"
    DENY = "deny"
    #: Granted only while every obligation the rule declares is met.
    ALLOW_WITH_OBLIGATIONS = "allow_with_obligations"
    #: Never a grant: the action is refused and the duty is named instead.
    OBLIGATE = "obligate"


#: Effects that grant an action. OBLIGATE is ABSENT on purpose: naming a duty
#: refuses the action, it does not permit it.
GRANTING_EFFECTS = (PolicyEffect.ALLOW, PolicyEffect.ALLOW_WITH_OBLIGATIONS)


class PolicyRule:
    """A single policy rule with subject, action, and conditions.

    Rules are evaluated in priority order. First matching rule wins.
    If no rule matches, the default is DENY.

    Attributes:
        effect: ALLOW or DENY
        action: The action being requested (e.g., "str_replace", "write_file")
        role: Required role (subject attribute)
        conditions: Additional key=value conditions for fine-grained ABAC
        priority: Higher priority rules evaluated first (default 0)
    """

    def __init__(
        self,
        effect: str,
        action: str,
        role: str = "",
        conditions: (dict[str, Any] | None) = None,
        priority: int = 0,
        obligations: (dict[str, Any] | None) = None,
    ):
        self.effect = PolicyEffect(effect.lower())
        self.obligations = dict(obligations or {})
        self.action = action
        self.role = role
        self.conditions = conditions or {}
        self.priority = priority

    def matches(self, action: str, role: str = "", attributes: (dict[str, Any] | None) = None) -> bool:
        """Check if this rule matches the given action + subject + context."""
        attributes = attributes or {}

        # Action must match (exact or wildcard)
        if self.action != "*" and self.action != action:
            return False

        # Role must match if specified
        if self.role and self.role != role:
            return False

        # All conditions must match
        for key, value in self.conditions.items():
            actual = attributes.get(key)
            if actual != value:
                return False

        return True

    def unmet_obligations(self, attributes: (dict[str, Any] | None) = None) -> list[str]:
        """Obligations this rule declares that the request has not satisfied.

        The caller discharges them by presenting evidence --
        `attributes["obligations"]` as {name: met} -- so a conditional grant
        is decided by what the request carries, never by the rule's own text.
        """
        attributes = attributes or {}
        satisfied = dict(attributes.get("obligations") or {})
        return [name for name in self.obligations if not satisfied.get(name)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect.value,
            "action": self.action,
            "role": self.role,
            "conditions": self.conditions,
            "priority": self.priority,
            "obligations": dict(self.obligations),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyRule":
        return cls(
            effect=data["effect"],
            action=data["action"],
            role=data.get("role", ""),
            conditions=data.get("conditions", {}),
            priority=data.get("priority", 0),
            obligations=data.get("obligations", {}),
        )

    def __repr__(self) -> str:
        return f"PolicyRule({self.effect.value}, action={self.action}, role={self.role})"


# ---------------------------------------------------------------------------
# Policy Engine
# ---------------------------------------------------------------------------

class PolicyEngine:
    """Policy evaluation engine — replaces Casbin for Azazael compliance gates.

    Evaluates rules in priority order. First matching rule wins.
    Default is DENY (fail-closed). Thread-safe for concurrent evaluation.
    """

    def __init__(self):
        self._rules: list[PolicyRule] = []
        self._lock = threading.Lock()
        self._audit_log: list[dict[str, Any]] = []
        self._contracts: dict[str, dict[str, Any]] = {}
        #: action -> authority the action requires (EP327/EP269)
        self._authority_requirements: dict[str, str] = {}
        #: task_id -> scoped authority it holds (EP269)
        self._task_authority: dict[str, dict[str, Any]] = {}
        self._audit_lock = threading.Lock()

    def add_rule(self, rule: PolicyRule):
        """Add a policy rule. Re-sorts by priority after adding."""
        with self._lock:
            self._rules.append(rule)
            self._rules.sort(key=lambda r: r.priority, reverse=True)

    def add_rules(self, rules: list[PolicyRule]):
        """Add multiple policy rules at once."""
        with self._lock:
            self._rules.extend(rules)
            self._rules.sort(key=lambda r: r.priority, reverse=True)

    def remove_rule(self, action: str, role: str = "") -> int:
        """Remove all rules matching action + role. Returns count removed."""
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if not (r.action == action and r.role == role)]
            return before - len(self._rules)

    def register_interface(self, app: str, contract: dict[str, Any],
                           required_keys: list[str] | None = None) -> None:
        """Record an application's semantic interface contract (ASIL).

        contract: {"verbs": [...], "targets": [...]} -- the deepest feasible
        access path. required_keys: the state keys a complete observation needs.
        Without a registered contract the engine is unchanged for that app.
        """
        with self._lock:
            self._contracts[str(app or "?").strip()] = {
                "contract": dict(contract or {}),
                "required_keys": [str(k) for k in (required_keys or [])],
            }

    def _interface_verdict(self, attributes: dict[str, Any]) -> dict[str, Any] | None:
        """Validate a structured action against its contract (ASIL).

        Returns None unless the caller presents a structured action for a
        REGISTERED app, which is what keeps every existing caller unaffected.
        An unexecutable action names its repair instead of failing at click
        time, and every validated action declares its effect row (EP382).
        """
        # All three symbols come from the same optional import: when it
        # fails they are ALL None, so one guard covers all three call sites
        # below (semantic_action, observe, effect_row).
        if semantic_action is None or observe is None or effect_row is None:
            return None
        attributes = attributes or {}
        app = str(attributes.get("app", "") or "").strip()
        if not app or app not in self._contracts:
            return None
        spec = self._contracts[app]
        action = {"verb": attributes.get("verb", ""),
                  "target": attributes.get("target", ""),
                  "args": attributes.get("args", {}) or {}}
        verdict = semantic_action(action, spec["contract"])
        state = attributes.get("state")
        obs = (observe(app, state, spec["required_keys"])
               if isinstance(state, dict) else None)
        gaps = list(verdict.get("gaps", []))
        gaps += list((obs or {}).get("gaps", []))
        return {
            "app": app,
            "executable": bool(verdict.get("executable"))
            and (obs is None or bool(obs.get("complete"))),
            "gaps": gaps,
            "action": verdict,
            "effect": effect_row(action),
            "remediations": [f"repair interface gap: {g}" for g in gaps],
        }


    def evaluate(self, action: str, role: str = "", attributes: (dict[str, Any] | None) = None,
                 authority: (Any | None) = None) -> tuple[bool, str]:
        """Evaluate an action against all rules.

        Args:
            action: The action being requested (e.g., "str_replace", "write_file")
            role: The subject's role
            attributes: Additional context attributes for ABAC conditions

        Returns:
            (allowed: bool, reason: str)
        """
        attributes = attributes or {}

        # ASIL interface gate (EP168/EP269): a structured action the declared
        # contract cannot express, or one whose observation is state-incomplete,
        # is NOT granted. Validating here turns a click-time mystery into a named
        # repair. Bare (non-structured) calls take the path below unchanged.
        iface = self._interface_verdict(attributes)
        if iface is not None and not iface["executable"]:
            reason = "DENIED (interface): " + ", ".join(iface["gaps"])
            self._log_evaluation(action, role, attributes, False, reason)
            return False, reason

        # Authority-exercised vs authority-granted (EP327). Opt-in: with no
        # authority presented this returns None and the path below is exactly
        # what it was before the row existed.
        ceiling = self._authority_ceiling(authority, attributes, action)
        if ceiling is not None and not ceiling["allowed"]:
            reason = "DENIED (authority): " + str(ceiling["reason"])
            self._log_evaluation(action, role, attributes, False, reason)
            return False, reason

        with self._lock:
            for rule in self._rules:
                if rule.matches(action, role, attributes):
                    # Obligations are read for the CONDITIONAL effect only, so
                    # a DENY rule's text can never enter the decision.
                    unmet = (rule.unmet_obligations(attributes)
                             if rule.effect == PolicyEffect.ALLOW_WITH_OBLIGATIONS
                             else [])
                    allowed = rule.effect in GRANTING_EFFECTS and not unmet
                    if allowed:
                        reason = f"Rule {rule.effect.value}: {rule}"
                    elif rule.effect == PolicyEffect.OBLIGATE and rule.obligations:
                        reason = (f"DENIED (obligate): {rule} -- duty: "
                                  + ", ".join(rule.obligations))
                    elif unmet:
                        reason = (f"DENIED by rule: {rule} -- unmet "
                                  f"obligation(s): " + ", ".join(unmet))
                    else:
                        reason = f"DENIED by rule: {rule}"
                    self._log_evaluation(action, role, attributes, allowed, reason)
                    return allowed, reason

        # Default: DENY (fail-closed)
        reason = f"DENIED (default): no matching rule for action={action}, role={role}"
        self._log_evaluation(action, role, attributes, False, reason)
        return False, reason

    def evaluate_all(self, action: str, role: str = "", attributes: (dict[str, Any] | None) = None,
                     authority: (Any | None) = None) -> dict[str, Any]:
        """Full evaluation result with all metadata. Used by ComplianceOrchestrator."""
        allowed, reason = self.evaluate(action, role, attributes, authority)
        return {
            "allowed": allowed,
            "reason": reason,
            "action": action,
            "role": role,
            "policy_version": "1.0.0",
            "interface": self._interface_verdict(attributes or {}),
        }

    def _log_evaluation(self, action: str, role: str, attributes: dict, allowed: bool, reason: str):
        """Record evaluation to internal audit log."""
        with self._audit_lock:
            self._audit_log.append({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "action": action,
                "role": role,
                "attributes": attributes,
                "allowed": allowed,
                "reason": reason,
            })

    def get_audit_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._audit_lock:
            return list(self._audit_log[-limit:])

    def clear_rules(self):
        with self._lock:
            self._rules.clear()

    def check_ceiling(self, action: str, granted: str, requires: str) -> dict:
        """Authority-ceiling check (EP269, enforcement half).

        granted: the authority the subject holds ("read-only", "read-write",
        "admin"). requires: what the action needs ("read"/"write" alias
        to read-only/read-write). A ceiling is ENFORCED: read-only
        requiring write is DENIED. Pure predicate, no rule writes.
        """
        order = {"none": 0, "read-only": 1, "read-write": 2, "admin": 3}
        aliases = {"read": "read-only", "write": "read-write"}
        g = order.get(str(granted or "none").strip().lower(), 0)
        req = aliases.get(str(requires or "").strip().lower(),
                          str(requires or "none").strip().lower())
        r = order.get(req, 0)
        ok = g >= r
        return {"allowed": ok, "granted": granted, "requires": requires,
                "reason": ("ceiling-ok" if ok else
                           f"ceiling-deny:{granted}-requires-{requires}")}

    def attenuate(self, authority: str, depth: int) -> dict:
        """Delegation attenuation (EP289, enforcement half).

        Each delegation step drops one rung (admin->read-write->read-only->
        none); depth 0 keeps the authority. A delegate at depth 3 from an
        admin owner bottoms out at none — never the owner's full authority.
        Pure predicate.
        """
        order = ["none", "read-only", "read-write", "admin"]
        try:
            idx = order.index(str(authority or "none").strip().lower())
        except ValueError as exc:
            note_failure("policy:325", exc)
            idx = 0
        steps = max(0, int(depth))
        return {"delegated": order[max(0, idx - steps)],
                "from": authority, "depth": steps}

    def obligate(self, action: str, role: str = "",
                 obligations: (dict[str, Any] | None) = None) -> dict:
        """Obligation verdict (EP305, expression half).

        evaluate() answers ALLOW/DENY; this answers what MUST happen next:
        obligations {name: met_bool}. Verdict "obligated" iff allowed AND
        every obligation met; unmet names listed. Meta-policy conflicts
        (two rules disagreeing) surface as "conflict" — expression only,
        no resolution engine (that half stays deferred).
        """
        allowed, reason = self.evaluate(action, role, {})
        ob = dict(obligations or {})
        unmet = [k for k, v in ob.items() if not v]
        with self._lock:
            hits = [r for r in self._rules
                    if r.matches(action, role, {})]
        top = max((r.priority for r in hits), default=None)
        effects = {r.effect.value for r in hits if r.priority == top}
        conflict = len(effects) > 1
        obligated = bool(allowed) and not unmet and not conflict
        return {"allowed": allowed, "reason": reason,
                "obligations": ob, "unmet": unmet,
                "conflict": conflict,
                "obligated": obligated}

    def require_authority(self, action: str, authority: str) -> None:
        """Declare the authority an action requires (EP327).

        Registering a requirement is what lets a caller present only what it
        HOLDS (`granted`) and still be checked: without a registered or
        caller-supplied `requires`, no comparison is possible and the ceiling
        stays silent rather than guessing one.
        """
        with self._lock:
            self._authority_requirements[str(action)] = str(authority or "none")

    def _authority_ceiling(self, authority: Any, attributes: dict[str, Any],
                           action: str) -> dict[str, Any] | None:
        """Authority-exercised vs authority-granted (EP327, expression half).

        Returns None whenever the caller presents no authority at all, which
        is what keeps every existing call site on exactly the path it had.
        The comparison itself is `check_ceiling`, so a ceiling already
        enforced there is not enforced a second time by different code.
        """
        granted = ""
        requires = ""
        if isinstance(authority, dict):
            granted = str(authority.get("granted", "") or "")
            requires = str(authority.get("requires", "") or "")
        elif authority is not None:
            granted = str(authority or "")
        if not granted:
            granted = str(attributes.get("granted_authority", "") or "")
        if not requires:
            requires = str(attributes.get("requires_authority", "") or "")
        if not requires:
            with self._lock:
                requires = str(self._authority_requirements.get(action, "") or "")
        if not granted or not requires:
            return None
        return self.check_ceiling(action, granted, requires)

    def residual_obligations(self, action: str, role: str = "",
                             attributes: (dict[str, Any] | None) = None) -> dict[str, Any]:
        """Residual obligations of the deciding rule (EP382, expression half).

        `evaluate` answers ALLOW/DENY and stops; this names what is still
        owed after the decision, which is the shape a residual-obligation
        monitor reads. Undischarged duties are reported, never enforced here.
        """
        attributes = attributes or {}
        with self._lock:
            deciding = next((r for r in self._rules
                             if r.matches(action, role, attributes)), None)
        if deciding is None:
            return {"effect": None, "rule": None, "obligations": {},
                    "unmet": [], "discharged": True}
        unmet = deciding.unmet_obligations(attributes)
        return {"effect": deciding.effect.value, "rule": repr(deciding),
                "obligations": dict(deciding.obligations), "unmet": unmet,
                "discharged": not unmet}

    # -- EP269 (Agent libOS): a TASK holds a scoped authority ----------------

    def bind_task_authority(self, task_id: Any, authority: str,
                            delegation_depth: int = 0) -> dict[str, Any]:
        """Bind a scoped authority to a task (EP269, libOS half).

        The gap: authority existed as a ceiling predicate (`check_ceiling`)
        and as delegation attenuation (`attenuate`), but a *task* could not
        hold one -- there was no task-authority object to check against.
        Binding applies the attenuation, so a delegated task never holds more
        than the authority it was handed.
        """
        attenuated = self.attenuate(authority, max(0, int(delegation_depth)))
        record = {"task_id": str(task_id), "authority": attenuated["delegated"],
                  "origin": attenuated["from"], "depth": attenuated["depth"]}
        with self._lock:
            self._task_authority[str(task_id)] = record
        return record

    def task_authority(self, task_id: Any) -> dict[str, Any] | None:
        with self._lock:
            record = self._task_authority.get(str(task_id))
        return dict(record) if record else None

    def release_task_authority(self, task_id: Any) -> bool:
        with self._lock:
            return self._task_authority.pop(str(task_id), None) is not None

    def task_authority_verdict(self, task_id: Any, action: str) -> dict[str, Any]:
        """May this task exercise this action? The libOS enforcement point.

        Fail-closed on both unknowns: an unbound task and an action with no
        registered requirement are refused rather than waved through.
        """
        record = self.task_authority(task_id)
        if record is None:
            return {"allowed": False, "task_id": str(task_id),
                    "reason": "no-authority-bound"}
        with self._lock:
            requires = str(self._authority_requirements.get(action, "") or "")
        if not requires:
            return {"allowed": False, "task_id": str(task_id),
                    "reason": f"no-requirement-registered:{action}"}
        verdict = self.check_ceiling(action, record["authority"], requires)
        verdict["task_id"] = str(task_id)
        return verdict

    def rule_count(self) -> int:
        with self._lock:
            return len(self._rules)

    def __repr__(self) -> str:
        return f"PolicyEngine({self.rule_count()} rules)"


# ---------------------------------------------------------------------------
# Default Policy Rules (Azazael-specific)
# ---------------------------------------------------------------------------

def default_policy_rules() -> list[PolicyRule]:
    """Create the default Azazael compliance policy rules.

    These implement the PRE-ACTION GATE (RULE 0J) as programmatic policy:
    - DEVELOPER role: full access to all code actions
    - REVIEWER role: can read and review, but not edit
    - AUDITOR role: read-only access
    - GUEST role: no access
    """
    return [
        # DEVELOPER — full access
        PolicyRule("allow", "*", role="developer", priority=100),
        # REVIEWER — read + review, no edit
        PolicyRule("allow", "read_files", role="reviewer", priority=90),
        PolicyRule("allow", "spawn_agents", role="reviewer", priority=90),
        PolicyRule("deny", "str_replace", role="reviewer", priority=90),
        PolicyRule("deny", "write_file", role="reviewer", priority=90),
        # AUDITOR — read only
        PolicyRule("allow", "read_files", role="auditor", priority=80),
        PolicyRule("deny", "*", role="auditor", priority=80),
        # GUEST — no access
        PolicyRule("deny", "*", role="guest", priority=70),
        # Default deny for all other subjects
        PolicyRule("deny", "*", priority=0),
    ]


# ---------------------------------------------------------------------------
# @enforce_gate decorator (RULE 0X-2: Runtime Broker Interception)
# ---------------------------------------------------------------------------

def enforce_gate(action: str = "", role: str = "developer", **conditions):
    """Decorator that enforces policy gate before function execution.

    Uses wrapt for robust signature-preserving decoration. Falls back to
    functools.wraps if wrapt is not available.

    Args:
        action: The policy action name (defaults to function name)
        role: Required role for execution
        **conditions: Additional ABAC conditions passed to PolicyEngine

    Usage:
        @enforce_gate("str_replace", role="developer")
        def str_replace_func(filepath, new_content):
            pass

        @enforce_gate()  # Uses function name as action
        def write_file(filepath):
            pass

    Raises:
        PermissionError: If the gate evaluation returns DENY
    """
    def decorator(wrapped=None, wrapper=None, instance=None):
        # Determine the action name
        nonlocal action
        actual_action = action or getattr(wrapped, "__name__", "unknown")

        def gate_wrapper(func, *args, **kwargs):
            # Build attributes from the function arguments
            attributes = dict(conditions)

            # Evaluate policy
            engine = _get_global_engine()
            allowed, reason = engine.evaluate(actual_action, role=role, attributes=attributes)

            if not allowed:
                raise PermissionError(
                    f"[COMPLIANCE GATE] Action '{actual_action}' DENIED for role '{role}': {reason}"
                )

            return func(*args, **kwargs)

        if HAS_WRAPT and isinstance(wrapped, wrapt.ObjectProxy):
            # wrapt-based decoration (signature-preserving). The adapter is
            # explicitly Any: binding calls it with just (wrapped) to get
            # the signature-preserving proxy, while passing all four args
            # would INVOKE instead of bind -- and the resolved stubs only
            # model the decorator protocol, not this bind call.
            wrapt_adapter: Any = wrapt.decorator(
                lambda w, i, a, k: gate_wrapper(w, *a, **k))
            return wrapt_adapter(wrapped)
        elif wrapped is not None:
            # Direct function decoration
            @functools.wraps(wrapped)
            def wrapper_func(*args, **kwargs):
                return gate_wrapper(wrapped, *args, **kwargs)
            return wrapper_func
        else:
            # Called with arguments (e.g., @enforce_gate("action"))
            return decorator

    return decorator


# ---------------------------------------------------------------------------
# Broker Interceptor
# ---------------------------------------------------------------------------

class BrokerInterceptor:
    """Runtime broker that intercepts tool calls and enforces policy gates.

    Acts as middleware between the agent and the actual tool execution.
    Compatible with wrapt for transparent function wrapping.

    Usage:
        interceptor = BrokerInterceptor()
        interceptor.intercept("str_replace", actual_function)
        # Now all calls to str_replace go through policy evaluation first
    """

    def __init__(self, engine: (PolicyEngine | None) = None):
        self._engine = engine or _get_global_engine()
        self._intercepted: dict[str, Callable] = {}

    def intercept(self, action: str, func: Callable, role: str = "developer") -> Callable:
        """Wrap a function with policy enforcement.

        Args:
            action: The action name for policy evaluation
            func: The function to wrap
            role: Required role

        Returns:
            The wrapped function
        """
        actual_action = action

        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            allowed, reason = self._engine.evaluate(actual_action, role=role)
            if not allowed:
                raise PermissionError(f"[BROKER] Action '{actual_action}' DENIED: {reason}")
            return func(*args, **kwargs)

        self._intercepted[action] = wrapped
        return wrapped

    def get_intercepted(self, action: str) -> (Callable | None):
        return self._intercepted.get(action)

    def release(self, action: str):
        self._intercepted.pop(action, None)


# ---------------------------------------------------------------------------
# Global engine singleton
# ---------------------------------------------------------------------------

_global_engine: (PolicyEngine | None) = None
_global_lock = threading.Lock()


def _get_global_engine() -> PolicyEngine:
    """Get or create the global policy engine singleton with default rules."""
    global _global_engine
    if _global_engine is None:
        with _global_lock:
            if _global_engine is None:
                _global_engine = PolicyEngine()
                _global_engine.add_rules(default_policy_rules())
    return _global_engine


def reset_global_engine():
    """Reset the global policy engine (for testing)."""
    global _global_engine
    with _global_lock:
        _global_engine = None
