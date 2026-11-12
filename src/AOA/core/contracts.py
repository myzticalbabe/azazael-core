"""AOA typed contracts -- the provenance envelope for Arm results.

Source: AOA_ARCHITECTURE_AUDIT_2026-09-14.md section 5, step 1.

The gap this closes: every Arm returns a plain ``dict`` whose freshness is
merely conventional. ``{"status": "ok", "data": {...}}`` says nothing about
WHEN the enclosed datum was produced, nor how long it stays meaningful, so a
consumer cannot distinguish a live reading from a replay, and no monitor can
age a value it has no timestamp for. That is the same defect class already
repaired in the four live monitors (see the audit, sections 6.7 and 6.8).

``ArmResult`` makes freshness structural: a result carries ``produced_at``
and ``stale_after_s``, so ``is_stale()`` is derived rather than assumed.

Migration is deliberately WARN-ONLY. Nothing in this module raises. Arms that
do not yet declare staleness keep working exactly as before, and the violation
is *counted* so that promotion to a hard failure can later be decided from
measurement rather than optimism. This ordering was the unanimous verdict of
four live inference providers (Gemini, OpenRouter, Cohere, NVIDIA NIM) on
2026-09-14; the reviewing NVIDIA pass supplied the promotion criterion
(observe violation rates first, reject once the false-positive rate is known).
Evidence: ``.inference_cache/20260914_134832_*.log``.

Timestamp convention: naive LOCAL time, via ``datetime.now()``, matching the
router's existing ``started_at`` / ``completed_at`` fields. Mixing naive and
aware datetimes across this layer would turn every existing duration
subtraction into a ``TypeError`` at runtime, so local consistency is chosen
deliberately over theoretical purity. ``parse_timestamp`` accepts aware input
and normalises it to local, so a caller that supplies ``+00:00`` is still
correctly aged rather than silently rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import logging
import threading

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

log = logging.getLogger(__name__)


# Statuses an Arm is allowed to report. Anything else is a contract
# violation -- not an exception, because existing arms must keep running.
KNOWN_STATUSES = ("ok", "error", "partial", "skipped")

# How far into the future a timestamp may sit before it is treated as bogus.
# Small positive tolerance absorbs clock granularity between processes.
FUTURE_TOLERANCE_S = 5.0

# Provenance of ``produced_at``. Recorded so a reader can tell an Arm's own
# claim from the router's observation of it; the two are not equivalent, and
# conflating them is how a fabricated timestamp becomes indistinguishable
# from a measured one.
PROVENANCE_DECLARED = "arm_declared"
PROVENANCE_OBSERVED = "router_observed"

#: Verdicts an Arm is allowed to claim about ITS OWN answer.
#:
#: Why this exists at all: ``status`` says what came back ("ok"), and the
#: router's outcome says whether the call was accepted. Neither says whether the
#: answer was RIGHT, and only the Arm is in a position to know -- it holds the
#: input, the computation and whatever it computed against. A search arm that
#: finds nothing returns "ok"; a lookup that resolves to the wrong record returns
#: "ok". Without this field a span can distinguish a crash from a success and
#: nothing else.
#:
#: The claim is recorded, never trusted: it is labelled ``judged_by: arm:<id>``
#: so it can not be read as the router's finding, and it never overrides
#: ``outcome.verdict`` -- a disagreement is the interesting reading, and collapsing
#: the two would destroy it.
ARM_VERDICTS = ("correct", "incorrect", "undetermined")


def normalize_arm_verdict(raw: Any, arm_id: str = "") -> Optional[Dict[str, Any]]:
    """Normalise an Arm's self-assessment, or return None if it is unusable.

    Accepts a bare string (``"incorrect"``) or a dict with ``verdict`` plus
    optional ``confidence`` (0.0-1.0) and ``evidence``. Anything else yields
    None -- an unfalsifiable or unrecognised claim is dropped rather than
    coerced, because a claim the reader cannot interpret is worse than no claim.

    ``judged_by`` is SET HERE from the arm id, never taken from the input: an arm
    that could name its own judge could impersonate the router.
    """
    if raw is None:
        return None
    claimed: Any = raw
    if isinstance(claimed, str):
        claimed = {"verdict": claimed}
    if not isinstance(claimed, dict):
        note_failure("contracts:arm_verdict:shape",
                     TypeError("arm_verdict must be str or dict, got %s"
                               % type(raw).__name__))
        return None

    verdict = str(claimed.get("verdict") or "").strip().lower()
    if verdict not in ARM_VERDICTS:
        note_failure("contracts:arm_verdict:enum",
                     ValueError("unknown arm_verdict %r; expected one of %s"
                                % (raw if isinstance(raw, str) else verdict,
                                   ", ".join(ARM_VERDICTS))))
        return None

    out: Dict[str, Any] = {"verdict": verdict,
                           "judged_by": "arm:%s" % (arm_id or "?")}

    confidence = claimed.get("confidence")
    if confidence is not None:
        try:
            value = float(confidence)
        except (TypeError, ValueError) as exc:
            note_failure("contracts:arm_verdict:confidence", exc)
            value = None
        if value is not None and 0.0 <= value <= 1.0:
            out["confidence"] = value
        elif value is not None:
            # Out of range is dropped, not clamped: 1.7 means the arm and this
            # reader disagree about what confidence is, and a clamped 1.0 would
            # hide that behind a plausible number.
            note_failure("contracts:arm_verdict:confidence-range",
                         ValueError("confidence %r outside 0.0-1.0" % (confidence,)))

    evidence = claimed.get("evidence")
    if evidence:
        out["evidence"] = evidence
    return out


def verdict_agreement(router_verdict: Optional[str],
                      arm_verdict: Optional[Dict[str, Any]]) -> str:
    """Compare the router's outcome with the Arm's own claim.

    Returns ``agree`` / ``disagree`` / ``unknown``. ``unknown`` covers: the arm
    said nothing, it said ``undetermined``, or the router returned
    ``unverified`` (a stale answer is not evidence either way).

    This is the reading a span exists to make possible: an arm that returns
    ``ok`` while claiming ``incorrect`` is the case no status code can express.
    """
    claimed = (arm_verdict or {}).get("verdict") if isinstance(arm_verdict, dict) else None
    router = str(router_verdict or "").strip().lower()
    if claimed not in ("correct", "incorrect") or not router:
        return "unknown"
    if router == "accepted":
        return "agree" if claimed == "correct" else "disagree"
    if router == "rejected":
        return "agree" if claimed == "incorrect" else "disagree"
    return "unknown"


def now_local() -> datetime:
    """Current naive local time (the AOA layer's convention)."""
    return datetime.now()


def now_iso() -> str:
    """Current naive local time as an ISO-8601 string."""
    return now_local().isoformat()


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp to naive LOCAL time.

    Accepts both naive and timezone-aware input. Aware input is converted to
    local time and stripped, so an Arm that reports UTC cannot be compared
    wrongly against a naive local ``datetime.now()``. Returns None when the
    value is missing or unparseable -- never raises.
    """
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError) as exc:
        note_failure("contracts:L83", exc)
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


@dataclass
class ArmResult:
    """A typed Arm result carrying its own provenance.

    ``stale_after_s`` is optional so nothing is forced to guess. When it is
    None the datum is *unaged*: still returned, still usable, but explicitly
    not a freshness claim, and it counts as a contract violation while the
    migration is warn-only.
    """

    status: str = "ok"
    arm_id: str = ""
    capability: str = ""
    data: Any = None
    error: Optional[str] = None
    produced_at: str = field(default_factory=now_iso)
    stale_after_s: Optional[float] = None
    provenance: str = PROVENANCE_OBSERVED
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: The Arm's own claim about whether its answer was right, normalised by
    #: ``normalize_arm_verdict``. None means the Arm said nothing, and an Arm that
    #: says nothing behaves exactly as it did before this field existed.
    arm_verdict: Optional[Dict[str, Any]] = None

    def age_s(self, at: Optional[datetime] = None) -> Optional[float]:
        """Seconds since ``produced_at``. None when it cannot be parsed."""
        produced = parse_timestamp(self.produced_at)
        if produced is None:
            return None
        reference = at or now_local()
        if reference.tzinfo is not None:
            reference = reference.astimezone().replace(tzinfo=None)
        return (reference - produced).total_seconds()

    def is_stale(self, at: Optional[datetime] = None) -> Optional[bool]:
        """True when past its declared lifetime.

        Returns None -- not False -- when staleness is *undecidable*, i.e.
        the lifetime was never declared or the timestamp is unparseable.
        Collapsing "unknown" into "fresh" is the exact bug this envelope
        exists to prevent, so the three states are kept distinct.
        """
        if self.stale_after_s is None:
            return None
        age = self.age_s(at)
        if age is None:
            return None
        return age > float(self.stale_after_s)

    def to_dict(self) -> Dict[str, Any]:
        """Flatten to a plain dict, preserving the legacy Arm result shape.

        ``status`` / ``data`` / ``error`` / ``arm_id`` keep their existing
        meanings so every current consumer of ``router.route_task`` continues
        to work; the provenance keys are purely additive.
        """
        out: Dict[str, Any] = {
            "status": self.status,
            "arm_id": self.arm_id,
            "capability": self.capability,
            "data": self.data,
            "produced_at": self.produced_at,
            "provenance": self.provenance,
        }
        if self.error is not None:
            out["error"] = self.error
        if self.stale_after_s is not None:
            out["stale_after_s"] = self.stale_after_s
        age = self.age_s()
        if age is not None:
            out["age_s"] = round(age, 3)
        stale = self.is_stale()
        if stale is not None:
            out["is_stale"] = stale
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        if self.arm_verdict:
            out["arm_verdict"] = dict(self.arm_verdict)
        return out

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ArmResult":
        """Rebuild an envelope from a flattened dict."""
        return cls(
            status=str(raw.get("status", "ok")),
            arm_id=str(raw.get("arm_id", "") or ""),
            capability=str(raw.get("capability", "") or ""),
            data=raw.get("data"),
            error=raw.get("error"),
            produced_at=str(raw.get("produced_at") or now_iso()),
            stale_after_s=raw.get("stale_after_s"),
            provenance=str(raw.get("provenance") or PROVENANCE_OBSERVED),
            metadata=dict(raw.get("metadata") or {}),
            arm_verdict=normalize_arm_verdict(
                raw.get("arm_verdict"), str(raw.get("arm_id") or "")),
        )

    @classmethod
    def from_arm_output(
        cls,
        raw: Any,
        arm_id: str,
        capability: str,
        stale_after_s: Optional[float] = None,
    ) -> "ArmResult":
        """Wrap a legacy Arm return value without changing its meaning.

        An Arm that already returns an envelope is passed through. A plain
        dict is adopted as-is, with ``produced_at`` taken as NOW and
        ``provenance`` marked ``router_observed`` -- honest labelling: the
        router saw this value at this moment and does not know when the Arm
        computed it. ``stale_after_s`` is left None unless the caller
        supplies a policy default, in which case the provenance is downgraded
        to observed so the *policy's* lifetime is never mistaken for the
        Arm's own claim.
        """
        if isinstance(raw, ArmResult):
            return raw
        if isinstance(raw, dict) and "produced_at" in raw and "status" in raw:
            envelope = cls.from_dict(raw)
            if not envelope.arm_id:
                envelope.arm_id = arm_id
            if not envelope.capability:
                envelope.capability = capability
            return envelope
        if isinstance(raw, dict):
            payload = dict(raw)
            data = payload.get("data")
            return cls(
                status=str(payload.get("status", "ok")),
                arm_id=str(payload.get("arm_id") or arm_id),
                capability=str(payload.get("capability") or capability),
                data=data,
                error=payload.get("error"),
                produced_at=now_iso(),
                stale_after_s=stale_after_s,
                provenance=PROVENANCE_OBSERVED,
                # Read from the Arm's own output before it is thrown away: the
                # plain-dict path discards every key it does not name, and this
                # is the one key only the Arm can supply.
                arm_verdict=normalize_arm_verdict(
                    payload.get("arm_verdict"),
                    str(payload.get("arm_id") or arm_id)),
            )
        # An Arm returned something that is not a dict at all: an upstream
        # contract break. Report it as an error rather than coercing it into
        # a false success.
        return cls(
            status="error",
            arm_id=arm_id,
            capability=capability,
            data=None,
            error="Arm returned %s, expected dict" % type(raw).__name__,
            produced_at=now_iso(),
            provenance=PROVENANCE_OBSERVED,
        )


def check_result(result: ArmResult, at: Optional[datetime] = None) -> List[str]:
    """Return the contract violations in an envelope. Never raises.

    Each violation is a short machine-checkable string so callers can count
    and group them; human-facing wording lives in the log line.
    """
    violations: List[str] = []

    if result.status not in KNOWN_STATUSES:
        violations.append("unknown_status:%s" % result.status)

    produced = parse_timestamp(result.produced_at)
    if produced is None:
        violations.append("produced_at_unparseable")
    else:
        # A naive timestamp on the wire usually means someone wrote
        # datetime.now() on a machine in another timezone; it is worth
        # knowing about explicitly rather than absorbing silently.
        if produced - (at or now_local()) > timedelta(seconds=FUTURE_TOLERANCE_S):
            violations.append("produced_at_in_future")

    if result.stale_after_s is not None:
        try:
            value = float(result.stale_after_s)
            if value <= 0 or value != value or value == float("inf"):
                violations.append("stale_after_s_not_positive")
        except (TypeError, ValueError) as exc:
            note_failure("contracts:L260", exc)
            violations.append("stale_after_s_not_numeric")
    elif result.status == "ok" and result.data is not None:
        # A datum handed back with no declared lifetime is the original gap.
        # Only a violation when there IS data to age; an "ok" carrying None
        # makes no freshness claim and is left alone.
        violations.append("stale_after_s_missing")

    return violations


class ContractReport:
    """Thread-safe, bounded counter of envelope contract violations.

    Mirrors the callback-error accounting already used by ``EventBus`` so the
    two surfaces report defects the same way. Bounded, because an unbounded
    violation log on a hot routing path is itself a leak.
    """

    def __init__(self, max_kept: int = 100):
        self._lock = threading.Lock()
        self._counts: Dict[str, int] = {}
        self._recent: List[Dict[str, Any]] = []
        self._total = 0
        self._checked = 0
        self._max_kept = max_kept

    def record(self, arm_id: str, capability: str, result: ArmResult,
               violations: List[str]) -> None:
        if not violations:
            with self._lock:
                self._checked += 1
            return
        entry = {
            "at": now_iso(),
            "arm_id": arm_id,
            "capability": capability,
            "violations": list(violations),
            "provenance": result.provenance,
        }
        with self._lock:
            self._checked += 1
            self._total += 1
            for name in violations:
                self._counts[name] = self._counts.get(name, 0) + 1
            self._recent.append(entry)
            if len(self._recent) > self._max_kept:
                self._recent = self._recent[-self._max_kept:]
        # Warn-only: logged, never raised. The whole point of this phase is
        # to learn the violation rate before anything depends on it.
        log.warning(
            "AOA contract violation: arm=%s capability=%s violations=%s "
            "provenance=%s",
            arm_id, capability, ",".join(violations), result.provenance)

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

    @property
    def checked(self) -> int:
        with self._lock:
            return self._checked

    def counts(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._recent[-limit:])

    def snapshot(self) -> Dict[str, Any]:
        """Summary for monitors. ``clean`` is the promotion criterion: while
        it is False, forcing a hard failure would break working arms."""
        with self._lock:
            checked = self._checked
            total = self._total
            counts = dict(self._counts)
            recent = list(self._recent[-5:])
        return {
            "checked": checked,
            "violations": total,
            "violation_rate": round(total / checked, 4) if checked else 0.0,
            "by_kind": counts,
            "clean": total == 0,
            "recent": recent,
        }

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._recent.clear()
            self._total = 0
            self._checked = 0


_CONTRACT_REPORT = ContractReport()


def get_contract_report() -> ContractReport:
    """Process-wide contract report, so monitors see every layer's results."""
    return _CONTRACT_REPORT


def validate_result(result: ArmResult, arm_id: str = "",
                    capability: str = "",
                    report: Optional[ContractReport] = None) -> List[str]:
    """Check an envelope, count any violations, and return them.

    Warn-only by construction: this function cannot raise, so it is safe to
    call on the routing path before the migration completes.
    """
    violations = check_result(result)
    (report or _CONTRACT_REPORT).record(
        arm_id or result.arm_id, capability or result.capability,
        result, violations)
    return violations
