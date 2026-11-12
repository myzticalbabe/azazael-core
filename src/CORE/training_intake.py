#!/usr/bin/env python3
"""training_intake.py — Phase-2 training intake over unity rows (Oct-10).

Forensic finding: Phase 1 (memory unity) exists — every production hook call
leaves a `technique-link` join row, queens leave canonical `reward-ledger`
rows "so the learning loop can...", and reflections leave
`reflection_calibration` rows — but NO training module reads any of them.
GRPO scores from execution history; the designed intake was never built.

This module is that intake. All reads, fail-open, stdlib-only:

- gather(): join the three unity streams into one intake summary —
  per-hook ok-rates, per-subject canonical rewards, calibration error stats.
  Consumers: monitor/fitness level only.

- build_training_records(): the TRAINER-facing half (Engram #770 / item H-3 of
  `state/ACTION_REGISTRY_2026-10-10.md`): normalise the measured technique
  outcomes into the record shape the four trainers actually consume, so a
  trajectory of run/exercise results is training INPUT instead of telemetry.
  Consumers: a trainer, through the orchestrator's INTAKE track.

REJECTED (prior Phase-2 lesson, obs #382): per-worm GRPO reward shaping
from global link terms — group normalization washes global terms, and
per-worm mapping would be substring-guessing. No shaping wrapper ships
in this module for that reason; shaping stays out by design (emphasis is
the trainer's job). Per-worm hook identity now exists via
`record_link(..., worm)` with caller-module derivation, carried per record
as `data["worm"]` with a `by_worm` aggregate. `build_training_records`
is NOT that wrapper: it introduces no per-worm term and changes no
trainer's objective — it only stops the row from arriving with its
outcome erased (see its docstring).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from CORE.local_memory import get_memory_store
from CORE.skill_library import family_index as _family_index

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass


def gather(limit: int = 200) -> Dict[str, Any]:
    """Join unity rows into one intake summary. Read-only, fail-open."""
    out: Dict[str, Any] = {"n_link": 0, "link_ok_rate": None,
                           "by_hook": {}, "rewards": {}, "n_rewards": 0,
                           "calibration": {"n": 0, "mean_abs_error": None}}
    try:
        store = get_memory_store()
    except Exception as exc:
        note_failure("training-intake:store", exc)
        out["store_error"] = type(exc).__name__
        return out
    try:
        link_rows = store.query_memories(source="technique-link",
                                         limit=int(limit)) or []
    except Exception as exc:
        note_failure("training-intake:link-read", exc)
        link_rows = []
    try:
        reward_rows = store.query_memories(source="reward-ledger",
                                           limit=int(limit)) or []
    except Exception as exc:
        note_failure("training-intake:reward-read", exc)
        reward_rows = []
    try:
        cal_rows = store.query_memories(source="reflection_calibration",
                                        limit=int(limit)) or []
    except Exception as exc:
        note_failure("training-intake:cal-read", exc)
        cal_rows = []

    ok = 0
    by_hook: Dict[str, List[int]] = {}
    for r in link_rows:
        d = r.get("data") or {}
        hook = str(d.get("hook", d.get("event_type", "?")))
        good = 1 if d.get("ok", False) else 0
        ok += good
        by_hook.setdefault(hook, [0, 0])
        by_hook[hook][0] += good
        by_hook[hook][1] += 1
    out["n_link"] = len(link_rows)
    out["link_ok_rate"] = round(ok / len(link_rows), 4) if link_rows else None
    out["by_hook"] = {h: round(g / max(t, 1), 4) for h, (g, t) in by_hook.items()}

    # Scale-aware rewards (forensic fix, Engram trace): values from
    # different scale_ids must never be mixed as if comparable, and rows
    # with no scale_id (100 blind rows in the live ledger) must be marked,
    # not silently absorbed. `rewards` keeps its float shape (no callers
    # depend on more); `reward_scales` + blind counts travel alongside so
    # the first comparing caller inherits the units instead of guessing.
    rewards: Dict[str, float] = {}
    reward_scales: Dict[str, str] = {}
    blind_subjects: list = []
    for r in reward_rows:
        d = _row_data(r)
        subj = str(d.get("subject", d.get("team", d.get("worm", "?"))))
        sid = d.get("scale_id")
        if not isinstance(sid, str) or not sid:
            if subj not in blind_subjects:
                blind_subjects.append(subj)
            note_failure("training-intake:blind-reward-scale", None)
            continue
        try:
            rewards[subj] = float(d.get("value", d.get("reward", 0.0)))
        except (TypeError, ValueError) as exc:
            note_failure("training-intake:86", exc)
            continue
        reward_scales[subj] = sid
    out["rewards"] = rewards
    out["n_rewards"] = len(rewards)
    out["reward_scales"] = reward_scales
    out["n_blind_rewards"] = len(blind_subjects)
    out["blind_subjects"] = blind_subjects

    errs: List[float] = []
    for r in cal_rows:
        d = r.get("data") or {}
        try:
            if d.get("predicted") is not None and d.get("realised") is not None:
                errs.append(abs(float(d["predicted"]) - float(d["realised"])))
        except (TypeError, ValueError) as exc:
            note_failure("training-intake:97", exc)
            continue
    out["calibration"] = {"n": len(cal_rows),
                          "n_scored": len(errs),
                          "mean_abs_error": (round(sum(errs) / len(errs), 6)
                                             if errs else None)}
    return out


#: The source the trainers already know how to read. A `source=` filter is how
#: `DataPipelineWorm.extract_from_memory` and `LocalMemoryStore.query_memories`
#: accept input, so this is the H-3 "rows DataPipelineWorm consumes" channel.
TRAINER_SOURCE = "technique-link"

#: The producer's failure vocabulary (Engram #813 F1, NAT arXiv:2402.11651
#: reading): `record_link()` writes severity "warning" for a failure and
#: "error" for an error — a severity outside "info" IS the producer measuring
#: a failure, even when the row's payload lost its boolean alongside the way.
NAT_NEGATIVE_SEVERITIES = ("warning", "error")

#: F2 Likra near-miss (Engram #813, arXiv:2503.14391 reading): among the
#: NAT negatives, the "warning" class is the NEAR-MISS segment — a
#: plausible, adjacent-to-success failure — while "error" is outright
#: breakage. Near-miss negatives carry a stronger learning signal than
#: arbitrary incorrect ones, so they are marked and counted here (see the
#: no-shaping boundary on the loop below: this module's own prior lesson
#: keeps the EMPHASIS application trainer-side (per-worm via
#: `data["worm"]` + `by_worm`); a consumer that wants the emphasis reads
#: the mark.
NEAR_MISS_SEVERITY = "warning"


def _row_data(row: Any) -> Dict[str, Any]:
    """A row's payload as a dict. Never raises: a row may carry a JSON string.

    `data` arrives as a dict from `query_memories()` (it decodes the column) but
    a hand-built or foreign row may still hold the raw JSON text, and the caller
    here must not die on it -- the intake's whole contract is fail-open.
    """
    data = (row or {}).get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        try:
            decoded = json.loads(data)
        except (TypeError, ValueError) as exc:
            note_failure("training-intake:row-data", exc)
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def build_training_records(limit: int = 500) -> Dict[str, Any]:
    """Normalise measured technique outcomes into trainer-consumable records.

    TWO GAPS, MEASURED (not assumed), and this closes the second. The first is
    that no trainer was pointed at the stream: `technique-link` rows existed and
    zero trainer filtered on that source (`tools/training_intake_audit.py`'s P3),
    which is why EP412/EP423/EP431/EP463 read PARTIAL -- the host module is
    imported, the outcomes reach no learning signal. The second is that the row
    carries its verdict HALF-VISIBLY to the trainers' reward model
    (`learning_rl._compute_reward`):

    * `severity` is declared by the producer (`record_link()` writes "info" for
      a success and "warning" for a failure), so that half already lands. This
      function PRESERVES it -- re-labelling a failure "error" would invent a
      verdict the producer did not make and would silently buy the learner a
      different incentive than the one the hook author chose;
    * the OUTCOME REFINEMENT is unreachable: the reward model reads
      `data["success"]` ("when present") and the row's payload carries `ok`.
      For a succeeded hook that is a real loss -- a `log`/`ignore` action on a
      known-good outcome earns +0.1 and earned nothing before -- and for any
      producer that declares `error` it is the difference between the
      remediation bonus/penalty firing and not. This function sets it.

    So the translation is additive and total: carry the verdict up as the
    producer declared it, add the missing refinement field, and change nothing
    else. Also:

    * a row whose `ok` is absent or not a real bool is COUNTED and SKIPPED,
      never labelled — UNLESS the producer already measured the verdict
      itself (F1 NAT, Engram #813): `record_link()` writes severity
      "warning"/"error" for a failure, so such a row arrives as a marked
      NEGATIVE (`data["nat_negative"]` is True, `success` False) and the
      count `n_nat_declared` names how many were rescued this way. Inventing
      an outcome is still forbidden — the rescue only ever uses the
      producer's own vocabulary;
    * nothing is written: this is a read-side adapter, so an intake run cannot
      perturb the store it reads.

    Returns a dict with `records` (the trainer input), the counts that make a
    thin or unmeasured stream diagnosable (`n_rows`, `n_unmeasured`, `ok_rate`,
    `by_hook`), and `store_error` / `read_error` when the store could not be
    opened or read. Both failure points are named in the RESULT as well as the
    failure ledger: a store that cannot be read yields an empty pool, and an
    empty pool must never be indistinguishable from "nothing was measured".
    """
    out: Dict[str, Any] = {"records": [], "n": 0, "n_rows": 0,
                           "n_unmeasured": 0, "n_negative": 0,
                           "n_nat_declared": 0, "n_near_miss": 0,
                           "ok_rate": None,
                           "source": TRAINER_SOURCE, "by_hook": {}}
    try:
        store = get_memory_store()
    except Exception as exc:
        note_failure("training-intake:records-store", exc)
        out["store_error"] = type(exc).__name__
        return out
    try:
        rows = store.query_memories(source=TRAINER_SOURCE,
                                    limit=int(limit)) or []
    except Exception as exc:
        note_failure("training-intake:records-read", exc)
        out["read_error"] = type(exc).__name__
        rows = []
    if not isinstance(rows, list):
        rows = []

    records: List[Dict[str, Any]] = []
    by_hook: Dict[str, List[int]] = {}
    unmeasured = 0
    negatives = 0
    nat_declared = 0
    n_near_miss = 0
    for row in rows:
        data = _row_data(row)
        ok = data.get("ok")
        severity = str((row or {}).get("severity") or "info")
        if not isinstance(ok, bool):
            # F1 NAT (Engram #813): a producer-declared failure is MEASURED.
            # The pool it fixes is 100% "info" today (measured: 500/500 rows,
            # ok_rate 1.0), so this path rescues the rows a degraded payload
            # would otherwise skip SILENTLY — the failure `record_link()`
            # already measured in severity, now carried as a marked negative
            # instead of vanishing between the store and the trainer. An
            # "info" row without a boolean stays unmeasured: no verdict, no
            # invention.
            if severity in NAT_NEGATIVE_SEVERITIES:
                ok = False
                nat_declared += 1
            else:
                unmeasured += 1
                continue
        # NAT marking is explicit in BOTH directions, so a trainer can read
        # the sign without re-deriving it from severity vocabulary.
        negative = (not ok) or (severity != "info")
        if negative:
            negatives += 1
        # F2 Likra (Engram #813): among the measured negatives, the
        # "warning" segment is the near-miss one — plausible, adjacent to
        # success — versus "error", outright breakage. MARKED and COUNTED,
        # deliberately not re-weighted into any reward here (this module's
        # REJECTED-lesson boundary: shaping is the trainer's job).
        # Per-worm hook identity travels with the record (`data["worm"]`,
        # aggregated in `by_worm`), so the trainer applies emphasis per
        # worm. The mark travels; applying emphasis is the trainer-side
        # next step.
        near_miss = negative and severity == NEAR_MISS_SEVERITY
        if near_miss:
            n_near_miss += 1
        event_type = str((row or {}).get("event_type") or "")
        # `record_link()` carries the hook in `event_type` ("hook:<hook>") and
        # not always in the payload, so the fallback strips the prefix and the
        # grouping key is the hook name rather than "hook:name".
        hook = str(data.get("hook") or "")
        if not hook:
            hook = event_type[len("hook:"):] if event_type.startswith("hook:") \
                else event_type
        hook = hook or "?"
        worm = str(data.get("worm", "") or "unknown")
        slot = by_hook.setdefault(hook, [0, 0])
        slot[0] += 1 if ok else 0
        slot[1] += 1
        records.append({
            "source": TRAINER_SOURCE,
            "event_type": event_type or "hook:%s" % hook,
            # The producer's own severity, untouched (see the docstring).
            "severity": str((row or {}).get("severity") or "info"),
            "data": {"success": ok, "ok": ok, "hook": hook,
                     "worm": worm,
                     "subject": str(data.get("subject", "")),
                     "citation": str(data.get("citation", "")),
                     "nat_negative": negative,
                     "near_miss": near_miss},
            "timestamp": (row or {}).get("timestamp"),
            "session_id": (row or {}).get("session_id"),
        })
    out["records"] = records
    out["n"] = len(records)
    out["n_rows"] = len(rows)
    out["n_unmeasured"] = unmeasured
    out["n_negative"] = negatives
    out["n_nat_declared"] = nat_declared
    out["n_near_miss"] = n_near_miss
    out["ok_rate"] = (round(sum(s[0] for s in by_hook.values()) / len(records), 4)
                      if records else None)
    out["by_hook"] = {h: [g, t] for h, (g, t) in by_hook.items()}
    by_worm: Dict[str, List[int]] = {}
    for _rec in records:
        _w = str(_rec["data"].get("worm", "") or "unknown")
        _slot = by_worm.setdefault(_w, [0, 0])
        _slot[0] += 1 if _rec["data"].get("success") else 0
        _slot[1] += 1
    out["by_worm"] = {w: [g, t] for w, (g, t) in by_worm.items()}
    # E1 (Engram #855): family attach — EP455 input side wired to
    # training. Each hook's producer namespace is its family; dotless
    # hooks land in family_index's own "unassigned" bucket. Additive
    # only: existing keys untouched, trainers can now slice by family.
    try:
        _fi = _family_index(
            [{"name": _h, "family": _h.split(".")[0] if "." in _h else ""}
             for _h in by_hook])
        _fam_of = {}
        for _fam, _names in (_fi.get("families") or {}).items():
            for _nm in _names:
                _fam_of[_nm] = _fam
        for _rec in records:
            _rec["data"]["family"] = _fam_of.get(
                _rec["data"].get("hook", "?"), "unassigned")
        out["families"] = _fi
        out["n_families"] = int(_fi.get("n_families", 0))
    except Exception as exc:
        note_failure("training-intake:family-attach", exc)
    return out


CLUSTER_SOURCES = ("cluster_execution_completed",
                   "category_cluster_executed")


def build_cluster_records(limit: int = 500) -> Dict[str, Any]:
    """Normalise CLUSTER execution rows into trainer-consumable records.

    Gap-4 resolve (Engram #1655): the cluster pool (ClusterWORMZ-sourced
    `cluster_execution_completed` rows, 500 live at wiring) was trainer-unread
    as a pool -- learning_grpo scores FROM these rows for rewards (Beam turf,
    untouched) but no trainer trained ON them. This is the reader half the
    H-3 producer shape-registration left open: same record contract as
    `build_training_records` (producer severity untouched, `data.success`
    added as the outcome refinement), so RLTrainLoop's `records=` path and
    the schema extractor consume both pools unchanged.

    Verdict logic mirrors the intake builder: `data.success` when the producer
    measured a bool; producer-declared failure severities rescue otherwise;
    anything else is unmeasured and skipped, never invented. Read-side only:
    nothing is written to the store it reads.
    """
    out: Dict[str, Any] = {"records": [], "n": 0, "n_rows": 0,
                           "n_unmeasured": 0, "n_negative": 0,
                           "n_nat_declared": 0, "n_near_miss": 0,
                           "ok_rate": None,
                           "source": "cluster_execution_completed",
                           "by_hook": {}}
    try:
        store = get_memory_store()
    except Exception as exc:
        note_failure("training-intake:cluster-store", exc)
        out["store_error"] = type(exc).__name__
        return out
    rows: List[Dict[str, Any]] = []
    try:
        for _et in CLUSTER_SOURCES:
            _rows = store.query_memories(event_type=_et,
                                         limit=int(limit)) or []
            if _rows:
                rows = _rows
                out["source"] = _et
                break
    except Exception as exc:
        note_failure("training-intake:cluster-read", exc)
        out["read_error"] = type(exc).__name__
        rows = []
    if not isinstance(rows, list):
        rows = []

    records: List[Dict[str, Any]] = []
    by_hook: Dict[str, List[int]] = {}
    unmeasured = 0
    negatives = 0
    nat_declared = 0
    n_near_miss = 0
    for row in rows:
        data = _row_data(row)
        ok = data.get("success")
        severity = str((row or {}).get("severity") or "info")
        success_rate = None
        if not isinstance(ok, bool):
            # Producer-count form (CLUSTERWORMS._record, transcribed from
            # src/CORE/WORMS/CLUSTERWORMS.py:1288): `results` = total worm
            # runs, `success` = runs with r.get("success") truthy. A full
            # sweep is ok; a partial sweep is a measured negative (rate is
            # the producer's own count ratio, not an invention); an empty
            # run carries no verdict and stays unmeasured.
            _total = data.get("results")
            if (isinstance(ok, int) and not isinstance(ok, bool)
                    and isinstance(_total, int) and not isinstance(_total, bool)
                    and _total > 0):
                success_rate = ok / _total
                ok = (ok == _total)
            elif severity in NAT_NEGATIVE_SEVERITIES:
                ok = False
                nat_declared += 1
            else:
                unmeasured += 1
                continue
        negative = (not ok) or (severity != "info")
        if negative:
            negatives += 1
        # Count-form partial sweeps (0 < rate < 1) are adjacent to success
        # by the producer's own counts, so they join the warning-segment
        # near-miss mark; the mark travels, emphasis stays trainer-side.
        near_miss = (negative and severity == NEAR_MISS_SEVERITY) or (
            success_rate is not None and 0 < success_rate < 1)
        if near_miss:
            n_near_miss += 1
        hook = str(data.get("cluster_type") or data.get("hook") or "")
        if not hook:
            hook = str((row or {}).get("source") or "?")
        worm = str(data.get("worm", "") or "unknown")
        slot = by_hook.setdefault(hook, [0, 0])
        slot[0] += 1 if ok else 0
        slot[1] += 1
        _rec_data = {"success": ok, "ok": ok, "hook": hook,
                     "worm": worm,
                     "subject": str(data.get("cluster_type", "")),
                     "nat_negative": negative,
                     "near_miss": near_miss}
        if success_rate is not None:
            _rec_data["success_rate"] = round(success_rate, 4)
        records.append({
            "source": "cluster_execution_completed",
            "event_type": "cluster:%s" % hook,
            "severity": severity,
            "data": _rec_data,
            "timestamp": (row or {}).get("timestamp"),
            "session_id": (row or {}).get("session_id"),
        })
    out["records"] = records
    out["n"] = len(records)
    out["n_rows"] = len(rows)
    out["n_unmeasured"] = unmeasured
    out["n_negative"] = negatives
    out["n_nat_declared"] = nat_declared
    out["n_near_miss"] = n_near_miss
    out["ok_rate"] = (round(sum(s[0] for s in by_hook.values()) / len(records), 4)
                      if records else None)
    out["by_hook"] = {h: [g, t] for h, (g, t) in by_hook.items()}
    by_worm = {}
    for _rec in records:
        _w = str(_rec["data"].get("worm", "") or "unknown")
        _slot = by_worm.setdefault(_w, [0, 0])
        _slot[0] += 1 if _rec["data"].get("success") else 0
        _slot[1] += 1
    out["by_worm"] = {w: [g, t] for w, (g, t) in by_worm.items()}
    try:
        _fi = _family_index(
            [{"name": _h, "family": _h.split(".")[0] if "." in _h else ""}
             for _h in by_hook])
        _fam_of = {}
        for _fam, _names in (_fi.get("families") or {}).items():
            for _nm in _names:
                _fam_of[_nm] = _fam
        for _rec in records:
            _rec["data"]["family"] = _fam_of.get(
                _rec["data"].get("hook", "?"), "unassigned")
        out["families"] = _fi
        out["n_families"] = int(_fi.get("n_families", 0))
    except Exception as exc:
        note_failure("training-intake:cluster-family-attach", exc)
    return out

