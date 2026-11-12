"""Microcode that repairs itself -- and only when the evidence pays for it.

The ISA's predicate bodies are not rewritten at runtime. What a patch may change is
one of the **declared policy knobs** in :mod:`HYPER_EDGE.microcode` -- the places
where the old code hard-coded a judgement call (what to do when a value is
*absent*). That restriction is the point: a self-modifying rule that can rewrite
arbitrary code cannot be replayed, and a change nobody can replay is a claim.

A patch is admitted only when all of these hold, in this order:

1. **A counterexample exists and is real.** At least one case in the corpus must be
   failing (its op returns something other than the expected state), and that case
   must carry the command and digest that produced it. An unnoted failure is a
   recollection, and this tree already refuses those (ISA ``0x08``).
2. **The patch fixes it.** Replaying the whole corpus under the candidate policy
   must turn that case's answer into the expected one.
3. **Nothing else moves.** Any case that was right before and wrong after is a
   regression, and one regression refuses the patch. A repair that buys one ruling
   with another is not a repair.
4. **Least severity wins.** Candidate values are tried least-to-most severe
   (``skip`` -> ``blind`` -> ``cut``), so ``blind`` is chosen over ``cut`` when both
   would fix the case. Cutting on absent data is a claim about the world; going
   blind is a claim about the instrument, and it is the smaller one.

Admission is not application. ``apply`` writes ``config/microcode.json`` (so the
policy is in force in every future process) and appends to a hash-chained JSONL
log whose ``prev`` field names the entry before it. History is append-only:
``rollback`` writes the old value back and records that it did, so the log can
never be made to say a patch was never tried.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import kernel, microcode
from .compat import parallel_map

__all__ = ["PATCH_LOG", "POLICY_FILE", "PatchRefused", "admit", "apply", "chain",
           "corpus_digest", "optimize", "patches", "policy_file", "propose", "replay",
           "rollback"]


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


#: Append-only evidence ledger for every policy change ever admitted.
PATCH_LOG: Path = _root() / "ARC" / "cain" / "hyper_edge_microcode_patches.jsonl"

#: The live policy, read at import by :mod:`HYPER_EDGE.microcode`.
POLICY_FILE: Path = microcode.policy_path()

#: Last converge per environment, keyed by env: corpus digest + when.
CONVERGE_FILE: Path = _root() / "ARC" / "cain" / "hyper_edge_converge.json"

#: Below this much total CPU a parallel run is not judged for serialisation: two
#: environments that each cost 3 ms can finish in sequence inside one scheduling
#: quantum and a ratio test on that would be noise, not evidence.
SERIAL_FLOOR_S: float = 0.20


def policy_file(env: Optional[str] = None) -> Path:
    """The policy file that governs ``env`` (the default file when env is None)."""
    return microcode.policy_path(env)

GENESIS = "0" * 16


class PatchRefused(RuntimeError):
    """A patch that could not pay for itself. Carries every reason it failed."""

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons) or "patch refused")


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def corpus_digest(cases: Sequence[Mapping[str, Any]]) -> str:
    """Stable identity of the corpus a patch was proved against."""
    return _sha([{"id": case.get("id"), "code": case.get("code"),
                  "expected": case.get("expected"), "record": case.get("record"),
                  "ctx": case.get("ctx") or {}} for case in cases])


def _with(policy: Mapping[str, Mapping[str, str]], op: str, knob: str,
          value: str) -> Dict[str, Dict[str, str]]:
    candidate = {code: dict(knobs) for code, knobs in policy.items()}
    candidate.setdefault(op, {})[knob] = value
    return candidate


def replay(cases: Sequence[Mapping[str, Any]],
           policy: Optional[Mapping[str, Mapping[str, str]]] = None) -> Dict[str, Any]:
    """Grade every case under ``policy`` and report which answers moved."""
    rows: List[Dict[str, Any]] = []
    for case in cases:
        ctx = dict(case.get("ctx") or {})
        result = microcode.evaluate(case["record"], policy=policy,
                                    quorum=float(ctx.get("quorum", 0.66)),
                                    legacy=bool(ctx.get("legacy", False)),
                                    max_age_days=float(ctx.get("max_age_days", 0.0)))
        got = result["states"][case["code"]]
        rows.append({"id": case.get("id"), "code": case["code"],
                     "expected": case["expected"], "got": got,
                     "ok": got == case["expected"]})
    failing = [row["id"] for row in rows if not row["ok"]]
    return {"rows": rows, "failing": failing,
            "passing": len(rows) - len(failing), "total": len(rows)}


def propose(cases: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Every candidate knob move, with what it fixes and what it breaks.

    Nothing is hidden: a candidate with zero fixes is still reported, because "the
    corpus does not constrain this knob" is a finding, not an absence.
    """
    baseline = replay(cases)
    right = {row["id"] for row in baseline["rows"] if row["ok"]}
    wrong = set(baseline["failing"])
    out: List[Dict[str, Any]] = []
    for code, knobs in microcode.POLICY_CHOICES.items():
        for knob, choices in knobs.items():
            current = microcode.POLICY.get(code, {}).get(knob)
            for value in choices:
                if value == current:
                    continue
                after = replay(cases, _with(microcode.POLICY, code, knob, value))
                now_wrong = set(after["failing"])
                out.append({
                    "op": code, "knob": knob, "old": current, "new": value,
                    "fixes": sorted(wrong - now_wrong),
                    "regressions": sorted(right & now_wrong),
                    "admissible": bool(wrong - now_wrong) and not (right & now_wrong),
                    "corpus": corpus_digest(cases),
                })
    return out


def admit(patch: Mapping[str, Any],
          cases: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Re-derive a candidate's proof and decide. Never trusts the caller's counts."""
    reasons: List[str] = []
    code, knob, value = patch.get("op"), patch.get("knob"), patch.get("new")
    if code not in microcode.POLICY_CHOICES:
        reasons.append("unknown op %r" % (code,))
    elif knob not in microcode.POLICY_CHOICES[code]:
        reasons.append("unknown knob %s.%s" % (code, knob))
    elif value not in microcode.POLICY_CHOICES[code][knob]:
        reasons.append("%s.%s=%r is not one of %s"
                       % (code, knob, value, list(microcode.POLICY_CHOICES[code][knob])))
    if patch.get("corpus") and patch["corpus"] != corpus_digest(cases):
        reasons.append("corpus moved since the patch was proposed: re-propose against the live cases")

    baseline = replay(cases)
    right = {row["id"] for row in baseline["rows"] if row["ok"]}
    wrong = set(baseline["failing"])
    if not wrong:
        reasons.append("no failing case: a patch with nothing to fix is a preference, not a repair")
    after = replay(cases, _with(microcode.POLICY, str(code), str(knob), str(value)))
    now_wrong = set(after["failing"])
    fixes, regressions = sorted(wrong - now_wrong), sorted(right & now_wrong)
    if not fixes and wrong:
        reasons.append("the patch does not fix the failing case(s) %s" % sorted(wrong))
    if regressions:
        reasons.append("it would regress passing case(s) %s" % regressions)

    unproven = [case.get("id") for case in cases
                if case.get("id") in wrong and not (case.get("command") and case.get("digest"))]
    if unproven:
        reasons.append("counterexample(s) %s carry no command/digest: unprovable" % unproven)

    return {"admissible": not reasons, "reasons": reasons, "fixes": fixes,
            "regressions": regressions, "cases_failing_before": sorted(wrong),
            "cases_failing_after": sorted(now_wrong), "corpus": corpus_digest(cases)}


def _read_log(log: Path) -> List[Dict[str, Any]]:
    if not log.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for number, line in enumerate(log.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except ValueError as exc:
            raise PatchRefused(["patch log line %d is not JSON: %s" % (number, exc)]) from exc
    return entries


def patches(log: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every admitted change, oldest first, as recorded (not as remembered)."""
    return _read_log(Path(log) if log is not None else PATCH_LOG)


def chain(log: Optional[Path] = None) -> Dict[str, Any]:
    """Verify the log's hash chain and return its head. A broken link is named.

    Two hashes are checked per entry. ``patch`` covers the *content* of the change
    (op, knob, old, new, fixes, regressions, corpus, evidence, env) and is stable
    across runs; ``digest`` covers that content **plus** its position and creation
    time, and is the chain link. Measured 2026-11-05: the first version hashed
    ``created`` into the only identifier, so the same proposal produced digest
    ``c8a5873550629894`` dry-run and ``a6d7a81023ba29a2`` fifteen minutes later --
    an identity that drifts with the clock cannot be cited as evidence.
    """
    entries = patches(log)
    prev = GENESIS
    for number, entry in enumerate(entries, 1):
        body = {key: value for key, value in entry.items() if key != "digest"}
        if entry.get("prev") != prev:
            return {"ok": False, "at": number,
                    "reason": "entry %d names prev=%r, ledger says %r"
                              % (number, entry.get("prev"), prev)}
        if entry.get("digest") != _sha(body):
            return {"ok": False, "at": number,
                    "reason": "entry %d digest does not match its body" % number}
        if entry.get("patch") and entry["patch"] != _sha(_content(entry)):
            return {"ok": False, "at": number,
                    "reason": "entry %d content hash does not match its patch" % number}
        prev = str(entry["digest"])

    # The hash chain proves the ledger was not edited. It cannot prove the
    # artefact a patch was applied to is still that artefact: a policy file
    # regenerated from identical bytes inherits the entry's name and not its
    # identity. Measured 2026-11-05 -- only the kernel can answer this.
    bound, reissued, unbound = 0, [], 0
    for number, entry in enumerate(entries, 1):
        recorded = entry.get("artifact_id")
        if not recorded:
            unbound += 1
            continue
        live = kernel.file_identity(entry.get("artifact") or entry.get("policy_file") or "")
        if live is None:
            unbound += 1
            continue
        bound += 1
        if live["id"] != recorded:
            reissued.append({"entry": number, "op": entry.get("op"),
                             "artifact": entry.get("artifact"),
                             "recorded": recorded, "live": live["id"]})
    return {"ok": True, "entries": len(entries), "head": prev,
            "head_patch": str(entries[-1].get("patch")) if entries else "",
            "artifacts": {"bound": bound, "unbound": unbound, "reissued": reissued}}


def _content(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """The clock-free identity of a change: what was changed, and on what proof."""
    return {key: entry.get(key) for key in
            ("op", "knob", "old", "new", "fixes", "regressions", "corpus",
             "evidence", "env")}


def apply(patch: Mapping[str, Any], cases: Sequence[Mapping[str, Any]],
          dry_run: bool = False, env: Optional[str] = None,
          policy_path: Optional[Path] = None,
          log: Optional[Path] = None) -> Dict[str, Any]:
    """Admit, persist and log one patch. Raises :class:`PatchRefused` if unpaid.

    ``env`` selects the per-environment policy file; the ledger is shared, because
    there is one history of what this engine was told and by what evidence.
    """
    verdict = admit(patch, cases)
    if not verdict["admissible"]:
        raise PatchRefused(verdict["reasons"])

    # The head must come from the SAME ledger this entry will be appended to, or
    # a write to an alternate ledger (a test, a migration, a per-environment
    # ledger) names a prev that its own chain never held. Found 2026-11-05 by a
    # test whose first line read prev=<real head> against ledger says GENESIS.
    entries = patches(log)
    content = {
        "op": patch["op"], "knob": patch["knob"],
        "old": patch.get("old"), "new": patch["new"],
        "fixes": verdict["fixes"], "regressions": verdict["regressions"],
        "corpus": verdict["corpus"], "evidence": {
            "command": next((case.get("command") for case in cases
                             if case.get("id") in verdict["fixes"]), ""),
            "digest": next((case.get("digest") for case in cases
                            if case.get("id") in verdict["fixes"]), ""),
        },
        "env": env or "default",
    }
    content["patch"] = _sha(content)
    for number, old in enumerate(entries, 1):
        if old.get("patch") == content["patch"]:
            raise PatchRefused(["patch %s is already entry %d of the ledger -- the same "
                                "change re-proposed is one change, not two"
                                % (content["patch"][:12], number)])
    entry: Dict[str, Any] = dict(content)
    entry["created"] = _now()
    entry["prev"] = entries[-1]["digest"] if entries else GENESIS
    entry["digest"] = _sha(entry)
    if dry_run:
        entry["dry_run"] = True
        return entry

    target = Path(policy_path) if policy_path is not None else policy_file(env)
    ledger = Path(log) if log is not None else PATCH_LOG
    policy = _with(microcode.POLICY, entry["op"], entry["knob"], entry["new"])
    PAYLOAD = {"policy": policy,
               "_provenance": {"patch": entry["patch"], "digest": entry["digest"],
                               "op": entry["op"],
                               "knob": entry["knob"], "old": entry["old"],
                               "new": entry["new"], "corpus": entry["corpus"],
                               "applied": entry["created"]}}
    target.parent.mkdir(parents=True, exist_ok=True)
    PAYLOAD["_provenance"]["env"] = entry["env"]
    PAYLOAD["_provenance"]["evidence"] = entry["evidence"]
    target.write_text(json.dumps(PAYLOAD, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    faults = microcode.set_policy({entry["op"]: {entry["knob"]: entry["new"]}})
    if faults:
        raise PatchRefused(faults)

    # Bind the entry to the artefact it was written into. The kernel identity is
    # taken AFTER the write on purpose: truncate-and-write keeps the inode, so the
    # id is the id of this policy file, and a regenerate-elsewhere produces a
    # different one. Excluded from `_content`, because a patch id that changes per
    # filesystem is not citable evidence -- `digest` (the chain link) carries it.
    identity = kernel.file_identity(target)
    if identity is not None:
        entry["artifact"] = str(target)
        entry["artifact_id"] = identity["id"]
        # Re-hash the body WITHOUT the digest field it already carries: hashing a
        # dict that contains its own previous digest produces a link the chain's
        # own check can never reproduce (found 2026-11-05 by the binding test).
        entry["digest"] = _sha({key: value for key, value in entry.items()
                                if key != "digest"})
        PAYLOAD["_provenance"]["digest"] = entry["digest"]
        target.write_text(json.dumps(PAYLOAD, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(_canon(entry) + "\n")
    entry["policy_file"] = str(target)
    return entry


def _converge_record() -> Dict[str, Any]:
    """The last converge per environment, or ``{}`` when nothing was recorded."""
    if not CONVERGE_FILE.exists():
        return {}
    try:
        payload = json.loads(CONVERGE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # A malformed record must not read as "never converged": that would make
        # the guard silent, which is the failure mode it exists to catch.
        return {"_unreadable": "%s: %s" % (CONVERGE_FILE.name, exc)}
    return payload if isinstance(payload, dict) else {}


def _remember_converge(rows: Sequence[Mapping[str, Any]]) -> None:
    """Record what converged, against which corpus and which policy artefact."""
    record = _converge_record()
    if record.get("_unreadable"):
        record = {}
    for row in rows:
        # `stale` is recorded too: it means "this run proved the NEW corpus, the
        # previous converge described a different one". Without that the guard
        # could never clear -- every later run would compare against the first
        # corpus forever, which is a permanent alarm, not a watch.
        if row.get("status") not in ("converged", "patched", "proposed", "stale"):
            continue
        # The artefact that matters is the policy actually read, not the per-env
        # path `apply` would have written: identity of a file that does not exist
        # records as "" and reads like a binding that was checked.
        live_policy = microcode.policy_path()
        identity = kernel.file_identity(live_policy)
        record[str(row.get("env"))] = {
            "corpus": row.get("corpus"),
            "status": "converged" if row.get("status") == "stale" else row.get("status"),
            "at": _now(), "policy": str(live_policy),
            "artifact_id": (identity or {}).get("id", "")}
    CONVERGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONVERGE_FILE.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")


def optimize(envs: Sequence[str], corpora: Mapping[str, Sequence[Mapping[str, Any]]],
             apply_envs: Sequence[str] = (), workers: Optional[int] = None,
             log: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Prove the microcode against every environment at once, independently.

    One worker per environment, results in declared order. An environment with no
    corpus is reported ``no_evidence`` -- BLIND, not optimised and not refused,
    because "we did not look" and "we looked and it was wrong" are different
    findings and this tree loses an argument every time they are collapsed.

    Two guards sit on top of the per-environment answer, both earned 2026-11-05:
    a run whose thread CPU does not exceed its wall time was **serial** whatever
    its rows say (concurrency is measured, not declared), and an environment whose
    corpus moved since its last converge is **stale** -- the old converge is not
    evidence for the new corpus, so it is re-proved rather than quoted.
    """
    prior = _converge_record()

    def _one(env: str) -> Dict[str, Any]:
        cases = list(corpora.get(env) or [])
        row: Dict[str, Any] = {"env": env, "cases": len(cases), "corpus": ""}
        if not cases:
            row.update(status="no_evidence", patch=None, reasons=[
                "no corpus for %r: BLIND, not a pass" % env])
            return row
        row["corpus"] = corpus_digest(cases)
        was = prior.get(env) if isinstance(prior.get(env), dict) else None
        if was and was.get("corpus") and was["corpus"] != row["corpus"]:
            row.update(status="stale", patch=None, reasons=[
                "corpus moved since the last converge (%s -> %s): re-prove, do not "
                "quote the old one" % (str(was["corpus"])[:12], row["corpus"][:12])])
        if not replay(cases)["failing"]:
            if row.get("status") == "stale":
                return row
            # Nothing to repair is a converged environment, not a refusal: the
            # first version called this "refused: no admissible candidate", which
            # reads as "the microcode rejected a patch" and is false -- there was
            # no patch to reject (measured 2026-11-05, 4/4 envs after the fix).
            row.update(status="converged", patch=None, reasons=[
                "every control case passes under the policy in force: nothing to optimise"])
            return row
        proposal = propose(cases)
        for candidate in proposal:
            verdict = admit(candidate, cases)
            if not verdict["admissible"]:
                continue
            row["candidate"] = candidate
            if env in apply_envs:
                try:
                    row["applied"] = apply(candidate, cases, env=env, log=log)
                    row["status"] = "patched"
                except PatchRefused as exc:
                    row.update(status="refused", reasons=exc.reasons)
            else:
                row["status"] = "proposed"
            row["fixes"] = verdict["fixes"]
            return row
        row.update(status="refused", patch=None,
                   reasons=["no admissible candidate: a failing case exists but every "
                            "knob move either fixes nothing or regresses a passing case"])
        return row

    def _timed(env: str) -> Dict[str, Any]:
        # Kernel thread accounting, not wall clock: a serial and a concurrent run
        # take the same elapsed time on an idle machine, so only per-thread CPU
        # can say whether the environments were actually proved at once.
        cpu0 = kernel.thread_cpu()
        row = _one(env)
        cpu1 = kernel.thread_cpu()
        if cpu0 is not None and cpu1 is not None:
            row["cpu_s"] = round(cpu1 - cpu0, 6)
        return row

    started = time.perf_counter()
    rows = parallel_map(_timed, list(envs), workers=workers, min_items=2)
    wall = time.perf_counter() - started

    # Kernel accounting is the only witness to overlap: an idle box gives a
    # serial run and a concurrent run the same wall time. Below SERIAL_FLOOR_S
    # the run is too cheap to judge and is labelled as unjudged rather than
    # called concurrent on the strength of a 3 ms ratio.
    total_cpu = sum(float(row.get("cpu_s") or 0.0) for row in rows)
    effective = workers if workers else min(len(rows), 4)
    judged = kernel.available() and effective > 1 and len(rows) > 1 \
        and total_cpu >= SERIAL_FLOOR_S
    concurrency: Dict[str, Any] = {
        "workers": effective, "wall_s": round(wall, 6), "cpu_s": round(total_cpu, 6),
        "par_ratio": round(total_cpu / wall, 3) if wall else 0.0,
        "judged": judged,
        "verdict": (("concurrent" if total_cpu / wall >= 1.15 else "serial")
                    if judged else "unjudged")}
    if judged and concurrency["verdict"] == "serial":
        for row in rows:
            if row.get("status") in ("converged", "patched", "proposed"):
                row.update(status="serial", reasons=[
                    "the run claims workers=%d but kernel thread accounting measured "
                    "%.3f cpu-s over %.3f s wall (par %.2f): the environments were "
                    "proved one after another" % (effective, total_cpu, wall,
                                                  concurrency["par_ratio"])])
    for row in rows:
        row["concurrency"] = concurrency
    _remember_converge(rows)
    return rows


def rollback(digest: str, log: Optional[Path] = None,
             env: Optional[str] = None) -> Dict[str, Any]:
    """Undo one patch by writing its old value back, and say that you did."""
    ledger = Path(log) if log is not None else PATCH_LOG
    entries = _read_log(ledger)
    target = next((entry for entry in reversed(entries)
                   if digest in (entry.get("digest"), entry.get("patch"))
                   and "rollback_of" not in entry), None)
    if target is None:
        raise PatchRefused(["no patch with digest or content hash %s in %s"
                            % (digest, ledger.name)])
    policy = _with(microcode.POLICY, target["op"], target["knob"], target["old"])
    entry: Dict[str, Any] = {"op": target["op"], "knob": target["knob"], "old": target["new"],
                             "new": target["old"], "fixes": [], "regressions": [],
                             "rollback_of": digest, "corpus": target.get("corpus", ""),
                             "env": env or "default",
                             "evidence": {"command": "HYPER_EDGE.cli rollback %s" % digest[:12],
                                          "digest": digest}}
    entry["patch"] = _sha(_content(entry))
    entry["created"] = _now()
    entry["prev"] = entries[-1]["digest"] if entries else GENESIS
    entry["digest"] = _sha(entry)
    target = policy_file(env)
    target.write_text(json.dumps({"policy": policy, "_provenance": {
        "patch": entry["patch"], "digest": entry["digest"],
        "rolled_back": digest}}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    microcode.set_policy({target["op"]: {target["knob"]: target["old"]}})
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(_canon(entry) + "\n")
    return entry
