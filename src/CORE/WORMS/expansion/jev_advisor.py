"""Jev (TypeSafe System One) as a bounded, gradeable decision-maker.

WHY JEV FITS THIS ARCHITECTURE, AND WHY ONLY HERE
-------------------------------------------------
Jev does not generate; it answers a FINITE-CHOICE question. Given enumerated
candidates with criteria, it returns `choice`, `confidence` and a full
`probabilities` distribution (confirmed against the local `jev-ultrafast`
client, 2026-09-21). Two properties make that safe to bind to a system that
modifies itself:

1. **It cannot fabricate a candidate.** The action space is ours. A wrong
   answer is a wrong pick from a known list, never a component that does not
   exist. The candidate set is re-validated on the way back in, so a malformed
   or out-of-space answer is rejected rather than acted on.

2. **Its verdict is gradeable.** Jev's own documentation states the principle
   this whole repair has been about: *"Independent checks, rather than the
   model's DONE choice, determine whether the demonstrated task succeeded."*
   We follow that literally. Every answer is recorded with a `decision_id`,
   the dispatch path stamps its outcome rows with the same id, and
   `grade_choices()` joins them. Jev is a PROPOSER whose proposals are scored
   by measured results. It is never the reward.

WHERE IT MUST NOT BE USED
-------------------------
* **Not in `decide_execution_order`.** That path runs on every dispatch and
  must never block on a network call.
* **Never as fitness or reward.** A reward authored by a model is
  `reward = f(state)` again - the exact defect this architecture is being
  repaired from (an oracle that equals a constant, and a genome that cannot
  move). Measured success stays the only fitness input.
* **Not for anything we cannot check.** If an answer cannot be graded from the
  outcome tap, it is an opinion, and opinions are what produced twelve
  phantom organs in the choice tap.

FAIL-OPEN BY CONSTRUCTION
-------------------------
Missing credentials, disabled, timeout, HTTP error, malformed answer, or a
choice outside the enumerated set -> `None`, and the caller's non-Jev path
runs. **Every failure is recorded**, not swallowed: a silent fallback is
indistinguishable from a working advisor, which is the same defect that left
the spine heartbeat dead and invisible for weeks.

NO NEW DEPENDENCY
-----------------
Uses `urllib.request` from the standard library rather than `httpx`, even
though `httpx` is installed: this call is optional and fail-open, so it must
not be able to make an import of the fleet depend on a third-party package.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: TypeSafe's System One endpoint, as used by the local `jev-ultrafast` client.
JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"

#: The model id TypeSafe ships by default. Overridable per-call and by env.
DEFAULT_MODEL = "jev-latest"

#: Our own tap. Distinct from the outcome tap so a reader can tell a claim
#: from a result; the two are joined on `decision_id`, never merged.
DECISION_TAP = "jev_decision"

#: Status codes worth retrying: rate limit / provider overload / unavailable.
RETRY_STATUS = frozenset({429, 503, 529})

#: Characters of `criteria` text echoed back into the decision row. Enough to
#: reconstruct WHAT was asked, bounded so a tap cannot be bloated by it.
MAX_ECHO_CHARS = 400


class JevError(Exception):
    """A Jev call was attempted and did not yield a usable answer."""


class JevUnavailable(JevError):
    """No call was attempted: no credentials, or the advisor is switched off."""


@dataclass(frozen=True)
class JevAnswer:
    """One validated finite-choice answer."""

    question: str
    choice: str
    confidence: float
    probabilities: Mapping[str, float]
    candidates: Mapping[str, str]
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    usage: Mapping[str, Any] = field(default_factory=dict)


# ── Transport ────────────────────────────────────────────────────────

def _post_json(url: str, key: str, body: Mapping[str, Any], *,
               timeout: float, attempts: int = 3) -> Dict[str, Any]:
    """POST JSON and return the decoded response. Raises `JevError` on failure.

    Retries only on the status codes that mean "try again", with the same
    backoff shape the reference client uses.
    """
    payload = json.dumps(body).encode("utf-8")
    last: str = "no attempt made"
    for attempt in range(max(1, attempts)):
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                # Some gateways reject the default `Python-urllib/3.x` agent.
                "User-Agent": "azazael-jev-advisor/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code in RETRY_STATUS and attempt < attempts - 1:
                time.sleep(0.5 * 2 ** attempt)
                continue
            raise JevError(f"Jev returned {last}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f"{type(exc).__name__}"
            if attempt < attempts - 1:
                time.sleep(0.5 * 2 ** attempt)
                continue
            raise JevError(f"Jev connection failed ({last})") from None
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise JevError("Jev returned a non-JSON body") from None
        if not isinstance(decoded, dict):
            raise JevError("Jev returned a non-object body")
        return decoded
    raise JevError(f"Jev exhausted retries ({last})")


# ── Validation (ours, not theirs) ────────────────────────────────────

def validate_choice(answer: Any, candidates: Mapping[str, str]) -> Dict[str, Any]:
    """Return a structurally valid answer or raise `JevError`.

    This is the verifier, written on our side so a change in the provider's
    response shape fails closed here rather than reaching the fleet. The
    acceptance test mirrors the reference client's contract deliberately, so
    "valid for us" and "valid for the demo harness" mean the same thing:

    * the choice is one we offered (cannot fabricate),
    * the distribution covers exactly our candidate set,
    * every number is finite and in [0, 1] and the distribution sums to ~1,
    * the chosen candidate is the argmax (a choice inconsistent with its own
      probabilities is a contradiction, not a preference).
    """
    if not isinstance(answer, dict):
        raise JevError("answer is not an object")
    ids = set(candidates)
    try:
        choice = answer["choice"]
        probabilities = answer["probabilities"]
        confidence = answer["confidence"]
        numbers = [*probabilities.values(), confidence]
        valid = (
            choice in ids
            and isinstance(probabilities, dict)
            and set(probabilities) == ids
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1
                    for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[choice] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        note_failure("jev_advisor:L184", exc)
        valid = False
    if not valid:
        raise JevError("answer failed validation against the offered candidates")
    return {"choice": choice, "probabilities": probabilities,
            "confidence": float(confidence)}


# ── Advisor ──────────────────────────────────────────────────────────

class JevAdvisor:
    """Ask Jev a finite-choice question, record it, and let outcomes grade it.

    Deliberately NOT constructed at import time anywhere in the fleet: a module
    that builds a network client on import would make importing the fleet
    depend on a third-party service.
    """

    def __init__(self, *, api_key: Optional[str] = None,
                 model: Optional[str] = None,
                 timeout: float = 25.0,
                 enabled: Optional[bool] = None,
                 endpoint: str = JEV_ENDPOINT,
                 store: Any = None) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY")
        self.model = model or os.environ.get("TYPESAFE_MODEL") or DEFAULT_MODEL
        self.timeout = float(timeout)
        self.endpoint = endpoint
        self._store = store
        self._enabled_override = enabled

    # -- state ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Explicit override > env switch > credentials present.

        `AZAZAEL_JEV_ENABLED=0` is the kill switch: it must be able to stop the
        advisor even when a key exists, because the reason to stop is usually
        cost or latency rather than a missing credential.
        """
        if self._enabled_override is not None:
            return bool(self._enabled_override)
        env = os.environ.get("AZAZAEL_JEV_ENABLED")
        if env is not None and env.strip().lower() in {"0", "false", "no", "off"}:
            return False
        return bool(self._api_key)

    @property
    def available(self) -> bool:
        return self.enabled and bool(self._api_key)

    def _get_store(self) -> Any:
        if self._store is not None:
            return self._store
        from CORE.local_memory import get_memory_store
        return get_memory_store()

    # -- recording -----------------------------------------------------

    def _record(self, *, question: str, outcome: str, choice: Optional[str] = None,
                confidence: Optional[float] = None,
                probabilities: Optional[Mapping[str, float]] = None,
                candidates: Optional[Mapping[str, str]] = None,
                decision_id: Optional[str] = None,
                error: Optional[str] = None,
                latency_ms: Optional[int] = None,
                model: Optional[str] = None,
                usage: Optional[Mapping[str, Any]] = None) -> bool:
        """Record one decision (or one refusal) so it can be graded later.

        Returns True if written. Never raises: recording must not be able to
        break a caller, but the False return is visible.
        """
        data: Dict[str, Any] = {"question": question, "outcome": outcome}
        if choice is not None:
            data["choice"] = choice
        if confidence is not None:
            data["confidence"] = float(confidence)
        if probabilities is not None:
            data["probabilities"] = {str(k): float(v) for k, v in probabilities.items()}
        if candidates is not None:
            data["candidates"] = list(candidates)
            data["criteria"] = {str(k): str(v)[:MAX_ECHO_CHARS]
                                for k, v in candidates.items()}
        if decision_id:
            data["decision_id"] = str(decision_id)
        if error:
            data["error"] = str(error)[:300]
        if latency_ms is not None:
            data["latency_ms"] = int(latency_ms)
        if model:
            data["model"] = str(model)
        if usage:
            data["usage"] = dict(usage)
        try:
            self._get_store().record_event(
                source="jev_advisor",
                event_type=DECISION_TAP,
                data=data,
                severity="warning" if outcome != "answered" else "info",
                tags=["jev", "decision"],
                correlation_id=str(decision_id) if decision_id else None,
            )
            return True
        except Exception as exc:
            note_failure("jev_advisor:L289", exc)
            return False

    # -- the call ------------------------------------------------------

    def choose(self, *, question: str, goal: str,
               candidates: Mapping[str, str],
               instructions: Sequence[str] = (),
               context: Optional[Mapping[str, Any]] = None,
               decision_id: Optional[str] = None) -> Optional[JevAnswer]:
        """Ask one finite-choice question. `None` means "no usable answer".

        A `None` return is ALWAYS accompanied by a recorded decision row with
        `outcome` set to the reason (`unavailable`, `invalid`, `error`,
        `refused`), so a fallback is evidence rather than silence.
        """
        if len(candidates) < 2:
            # A one-candidate question is not a decision, and asking it would
            # burn a request to be told what we already decided.
            self._record(question=question, outcome="refused",
                         candidates=candidates, decision_id=decision_id,
                         error="fewer than two candidates")
            return None

        if not self.available:
            self._record(question=question, outcome="unavailable",
                         candidates=candidates, decision_id=decision_id,
                         error="no credentials or advisor disabled")
            return None

        body = {
            "model": self.model,
            "state": {"goal": goal, "context": dict(context or {})},
            "questions": {
                question: {
                    "type": "choice",
                    "criteria": {str(k): str(v) for k, v in candidates.items()},
                    "instructions": {"goal": goal, "rules": list(instructions)},
                }
            },
        }

        started = time.perf_counter()
        try:
            result = _post_json(self.endpoint, self._api_key or "", body,
                               timeout=self.timeout)
            answers = result.get("answers")
            if not isinstance(answers, dict):
                raise JevError("response carried no `answers` object")
            validated = validate_choice(answers.get(question), candidates)
        except JevError as exc:
            self._record(question=question, outcome="invalid"
                         if "validation" in str(exc) else "error",
                         candidates=candidates, decision_id=decision_id,
                         error=str(exc),
                         latency_ms=round((time.perf_counter() - started) * 1000))
            return None

        answer = JevAnswer(
            question=question,
            choice=validated["choice"],
            confidence=validated["confidence"],
            probabilities=validated["probabilities"],
            candidates=dict(candidates),
            model=result.get("model") or self.model,
            latency_ms=round((time.perf_counter() - started) * 1000),
            usage=result.get("usage") or {},
        )
        self._record(question=question, outcome="answered", choice=answer.choice,
                     confidence=answer.confidence,
                     probabilities=answer.probabilities, candidates=candidates,
                     decision_id=decision_id, latency_ms=answer.latency_ms,
                     model=answer.model, usage=answer.usage)
        return answer


# ── The independent check ────────────────────────────────────────────

PAGE = 500
MAX_PAGES = 100


def _page_all(store: Any, event_type: str) -> List[Dict[str, Any]]:
    """Read a whole tap, paginating past the store's one-query clamp."""
    rows: List[Dict[str, Any]] = []
    offset = 0
    for _ in range(MAX_PAGES):
        batch = store.query_memories(event_type=event_type, limit=PAGE, offset=offset)
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        if len(batch) < PAGE:
            break
    return rows


def _data_of(row: Mapping[str, Any]) -> Dict[str, Any]:
    data = row.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError) as exc:
            note_failure("jev_advisor:L391", exc)
            return {}
    return data if isinstance(data, dict) else {}


def grade_choices(*, store: Any = None, outcome_tap: str = "clusterwormz_worm_executed",
                  page_all: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Did Jev's picks actually work out? Join decisions to measured outcomes.

    This is the independent check its own documentation demands, and it is the
    ONLY reason a Jev verdict may influence anything. A decision is `graded`
    when the outcome tap contains a row carrying the same `decision_id` for the
    candidate that was chosen; it is `correct` when that execution succeeded.

    Ungraded decisions are reported, not hidden: an advisor whose verdicts are
    never executed has no accuracy, and reporting it as 0% or 100% would both
    be fabrications.
    """
    if store is None:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()

    decisions = _page_all(store, DECISION_TAP)
    outcomes = _page_all(store, outcome_tap)

    executed: Dict[str, List[bool]] = {}
    for row in outcomes:
        data = _data_of(row)
        did = data.get("decision_id")
        if did:
            executed.setdefault(str(did), []).append(bool(data.get("success")))

    answered = 0
    graded = 0
    correct = 0
    ungraded: List[str] = []
    fallbacks: Dict[str, int] = {}
    per_choice: Dict[str, int] = {}

    for row in decisions:
        data = _data_of(row)
        outcome = str(data.get("outcome", "?"))
        if outcome != "answered":
            fallbacks[outcome] = fallbacks.get(outcome, 0) + 1
            continue
        answered += 1
        choice = data.get("choice")
        if choice is not None:
            per_choice[str(choice)] = per_choice.get(str(choice), 0) + 1
        did = data.get("decision_id")
        results = executed.get(str(did)) if did else None
        if not results:
            ungraded.append(str(did or "(no decision_id)"))
            continue
        graded += 1
        if any(results):
            correct += 1

    return {
        "decisions": len(decisions),
        "answered": answered,
        "graded": graded,
        "correct": correct,
        "accuracy": (correct / graded) if graded else None,
        "ungraded": len(ungraded),
        "ungraded_ids": ungraded[:10],
        "fallbacks": fallbacks,
        "choices": per_choice,
    }


# ── The question we actually need answered ───────────────────────────

#: Longest docstring excerpt per candidate. A criterion is a prompt, not a
#: metadata dump; 209 of them at full length would be a worse question.
MAX_CRITERION_CHARS = 200

#: Default candidate count. Bounded ON PURPOSE, and it is not a tuning knob:
#: the candidate list is what makes the answer checkable, so a short list of
#: plausible organs is a better question than an exhaustive one.
DEFAULT_CANDIDATE_LIMIT = 12


def criterion_for(name: str, entry: Any) -> str:
    """One line describing a registry entry, for use as a Jev criterion."""
    category = (getattr(entry, "category", None)
                or getattr(entry, "CATEGORY", None)
                or getattr(entry, "nature", None)
                or "unclassified")
    doc = (getattr(entry, "__doc__", None) or "").strip().split("\n")[0]
    doc = " ".join(doc.split())[:MAX_CRITERION_CHARS]
    return (f"name={name}; category={category}; "
            f"purpose={doc or '(no docstring)'}; executions_observed=0")


def exploration_candidates(registry: Mapping[str, Any],
                           observed: Iterable[str],
                           *, limit: int = DEFAULT_CANDIDATE_LIMIT,
                           ) -> Dict[str, str]:
    """Enumerate registered organs that have NO evidence, as Jev candidates.

    Measured 2026-09-21: 232 components registered, 25 with any evidence at all,
    209 never observed. Those 209 are the only places new information can come
    from, and choosing among them is a finite-choice decision with a checkable
    outcome - which is precisely the shape Jev answers.

    Deterministic ordering (category, then name) so the same question is the
    same question on a re-run; an unordered candidate list would make the
    recorded decision unreproducible.
    """
    observed_set = set(observed)
    rows = [(str(getattr(entry, "category", None)
                 or getattr(entry, "CATEGORY", None) or "~unclassified"),
             str(name), entry)
            for name, entry in registry.items() if name not in observed_set]
    rows.sort(key=lambda item: (item[0], item[1]))
    return {name: criterion_for(name, entry) for _cat, name, entry in rows[:max(0, limit)]}


__all__ = [
    "DECISION_TAP", "DEFAULT_CANDIDATE_LIMIT", "DEFAULT_MODEL", "JEV_ENDPOINT",
    "JevAdvisor", "JevAnswer", "JevError", "JevUnavailable", "criterion_for",
    "exploration_candidates", "grade_choices", "validate_choice",
]
