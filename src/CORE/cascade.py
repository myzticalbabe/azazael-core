# coding: utf-8
"""cascade.py — 1.5B triage -> 9B escalation router.

Cheap model answers first with a confidence; escalate only when confidence
is below threshold (tuned by OOF ECE: default 0.65). Tracks savings.
"""
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


#: Reasoning modes the per-turn selector chooses between (EP266, ALAR):
#: the cheap tier's explicit chain-of-thought answer, or the escalated latent
#: reasoner. They are the two "arms" of the same cheap-vs-expensive decision.
MODE_EXPLICIT = "explicit"
MODE_LATENT = "latent"


class CascadeRouter:
    """triage_fn(task) -> (answer, confidence). big_fn(task) -> answer.

    ledger_path: every escalation appends {task, triage_answer, confidence,
    big_answer} JSONL — the future SFT corpus. Non-escalations are not
    logged (nothing to learn).

    `route()` also accepts a per-CALL `threshold` override: the instance gate
    stays the default, and an allocation policy can supply its own gate for one
    call without mutating the router (EP223/EP253). `route_node()` applies the
    same cheap-vs-expensive decision per plan NODE, which is what a DAG executor
    needs for per-node offloading (EP186).
    """

    VERSION = "1.1.0"

    def __init__(self, threshold: float = 0.65, ledger_path: str = ""):
        self.threshold = float(threshold)
        self.ledger_path = ledger_path
        self.n_triage = 0
        self.n_escalated = 0

    def _log_escalation(self, task, triage_ans, conf, big_ans):
        if not self.ledger_path:
            return
        try:
            import json as _json
            import datetime as _dt
            row = {"ts": _dt.datetime.now().isoformat(),
                   "task": str(task)[:2000],
                   "triage_answer": str(triage_ans)[:2000],
                   "confidence": float(conf), "big_answer": str(big_ans)[:2000]}
            with open(self.ledger_path, "a", encoding="utf-8") as fh:
                fh.write(_json.dumps(row) + "\n")
        except Exception as exc:
            note_failure("cascade:L37", exc)

    def route(self, task, triage_fn, big_fn, threshold=None) -> dict:
        ans, conf = triage_fn(task)
        self.n_triage += 1
        if conf is None:
            conf = 0.0
        gate = self.threshold if threshold is None else float(threshold)
        if float(conf) >= gate:
            return {"answer": ans, "via": "triage", "confidence": float(conf),
                    "escalated": False}
        big = big_fn(task)
        self.n_escalated += 1
        self._log_escalation(task, ans, conf, big)
        return {"answer": big, "via": "big", "confidence": float(conf),
                "escalated": True, "triage_answer": ans}

    def select_mode(self, task, triage_fn, latent_fn) -> dict:
        """Per-turn reasoning-MODE selector (EP266, ALAR adaptive latent reasoning).

        This is the decision `route()` already makes -- cheap first, escalate only
        while confidence is below `self.threshold` -- applied to the reasoning MODE
        rather than to the model: `MODE_EXPLICIT` at or above the threshold, and
        `MODE_LATENT` below it. Delegating to `route()` is deliberate: the tuned
        threshold, the escalation ledger and the counters behave identically, and
        `route()`'s existing callers are untouched.

        The latent reasoner is invoked ONLY on the escalation arm, which is what
        makes the mode a behaviour change rather than a label.
        """
        if not callable(triage_fn) or not callable(latent_fn):
            raise ValueError("select_mode requires callable triage_fn and latent_fn")
        routed = self.route(task, triage_fn, latent_fn)
        return {"mode": MODE_EXPLICIT if routed["via"] == "triage" else MODE_LATENT,
                "answer": routed["answer"],
                "confidence": routed["confidence"],
                "escalated": routed["escalated"]}

    def route_node(self, node, triage_fn, big_fn, threshold=None) -> dict:
        """Per-NODE cheap-vs-expensive offload (EP186 SGDe compilation, EP223).

        `dispatch_plan.plan_dag` groups independent branches into depth LEVELS,
        and a fan-out executor wants the same cheap-vs-expensive decision taken
        per node instead of once per task, so the expensive tier is spent only
        on the nodes that need it. `node` is a plan node (a mapping carrying an
        `id`, optionally a `task`); the decision itself is `route()`'s, so the
        tuned threshold, the escalation ledger and the counters are SHARED with
        the per-task path rather than being a second, drifting copy of it.

        Returns `route()`'s dict with the node's id added under `"node"`.
        """
        if isinstance(node, dict):
            node_id = node.get("id")
            task = node.get("task", node_id)
        else:
            node_id = None
            task = node
        if task is None:
            raise ValueError("route_node needs a node with an 'id' or 'task'")
        routed = self.route(task, triage_fn, big_fn, threshold=threshold)
        routed["node"] = node_id
        return routed

    def stats(self) -> dict:
        tot = max(1, self.n_triage)
        return {"triaged": self.n_triage, "escalated": self.n_escalated,
                "escalation_rate": self.n_escalated / tot,
                "saved_ratio": 1.0 - self.n_escalated / tot}
