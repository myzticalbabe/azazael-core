#!/usr/bin/env python3
"""Run deterministic policy and diversity laboratory evidence.

This runner is deliberately outside the production training rotation.  It
executes the local mathematical contracts, writes a result artifact, and
reports qualification status.  A missing regression report fails closed at
``executable``; it never claims that a laboratory component is production
served.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from CORE.algorithm_qualification import QualificationEvidence, classify  # noqa: E402

try:
    # Single source of truth for the production callable (fail closed: with
    # track_registry unimportable, no callable is recorded and `classify`
    # cannot reach `served`).
    from track_registry import LAB_COMPONENTS  # noqa: E402
except ImportError:  # pragma: no cover - defensive
    LAB_COMPONENTS = {}

try:
    from CORE.technique_runtime import record_link  # noqa: E402
except ImportError:  # pragma: no cover - fail-open
    record_link = None  # type: ignore[assignment]
try:
    from CORE.failure_ledger import note_failure  # noqa: E402
except ImportError:  # pragma: no cover - fail-open
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass
from CORE.WORMS.expansion.diversity import (  # noqa: E402
    Individual,
    NSGAIII,
    run_diversity_evidence,
)
from CORE.WORMS.expansion.policy_optimization import (  # noqa: E402
    TokenTrace,
    run_policy_lab,
    select_dapo_groups,
    sequence_policy_loss,
    token_policy_loss,
)

DEFAULT_OUTPUT = ROOT / "state" / "algorithm_lab" / "current.json"
DEFAULT_ARCHIVE = ROOT / "state" / "algorithm_lab" / "map_elites.json"
#: Regression evidence carried by every production run (the chain's `algolab`
#: job passes no args, and without this default the run qualified as
#: `executable` while real evidence sat unread on disk). Absent or unreadable
#: it still fails closed: `_read_regression_report` returns an error record and
#: `classify` cannot reach `qualified`/`served`.
DEFAULT_REGRESSION = ROOT / "state" / "algorithm_lab" / "regression_report.json"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_regression_report(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    report_path = Path(path)
    if not report_path.is_file():
        return {"error": f"regression report not found: {path}"}
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"error": f"invalid regression report: {exc}"}
    return payload if isinstance(payload, dict) else {"error": "regression report must be an object"}


def _policy_contracts(seed: int) -> dict[str, Any]:
    groups = [
        TokenTrace((0.0, 0.0), group_rewards=(0.0, 1.0)),
        TokenTrace((0.0, 0.0), group_rewards=(1.0, 1.0)),
        TokenTrace((0.0, 0.0), mask=(True, False), group_rewards=(0.0, 1.0), overlong=True),
    ]
    selected, skipped = select_dapo_groups(groups, max_tokens=16, target_groups=8)
    trace = TokenTrace((0.0, 0.0), mask=(True, False))
    return {
        "dapo": {
            "input_groups": len(groups),
            "selected_groups": len(selected),
            "skipped_groups": skipped,
            "informative_group_required": True,
        },
        "dapo_token_loss": token_policy_loss(trace, 1.0),
        "gspo_sequence_loss": sequence_policy_loss(trace, 1.0),
        "masked_tokens_ignored": len(trace.valid_indices()) == 1,
        "seed": seed,
    }


def _diversity_contract(seed: int) -> dict[str, Any]:
    candidates = [
        Individual({"x": float(index)}, (float(index % 3), float(index // 3)))
        for index in range(8)
    ]
    selected = NSGAIII(population_size=4, divisions=2).select(candidates)
    return {
        "input": len(candidates),
        "selected": len(selected),
        "objective_width": len(selected[0].objectives) if selected else 0,
        "seed": seed,
    }


def _deposit_nsga3(
    record_link_fn: Any, contract: dict[str, Any], seed: int
) -> None:
    """Persist NSGA-III contract evidence into the live memory store.

    Moves the DIVERSITY_QD_NSGA3 component from L3 (invoked) to L4 (evidenced
    in store) on the production-reach ladder. Fail-open: never raises.
    """
    try:
        ok = contract.get("selected", 0) > 0
        record_link_fn(
            "algorithm_lab.diversity_qd_nsga3",
            "nsga3_contract.seed=%d.selected=%d" % (seed, contract.get("selected", 0)),
            bool(ok),
            citation="nsga3_contract=%s" % json.dumps(contract, sort_keys=True),
            decision_id="nsga3_seed_%d" % seed,
        )
    except Exception as exc:
        note_failure("algorithm_lab_cycle:deposit-nsga3", exc)


def run_cycle(
    *,
    seed: int = 314,
    output: str | Path = DEFAULT_OUTPUT,
    archive: str | Path = DEFAULT_ARCHIVE,
    regression_report: str | None = str(DEFAULT_REGRESSION),
) -> dict[str, Any]:
    output_path = Path(output)
    archive_path = Path(archive)
    rewards = {
        "REINFORCE": [0.2, 0.5, 0.1, 0.8, 0.4, 0.7],
        "RLOO": [0.2, 0.5, 0.1, 0.8, 0.4, 0.7],
        "REINFORCE++": [0.2, 0.5, 0.1, 0.8, 0.4, 0.7],
        "GRPO": [0.2, 0.5, 0.1, 0.8, 0.4, 0.7],
        "DR_GRPO": [0.2, 0.5, 0.1, 0.8, 0.4, 0.7],
    }
    policy = [row.as_dict() for row in run_policy_lab(rewards, seed=seed)]
    diversity = run_diversity_evidence(seed=seed, archive_path=archive_path)
    payload: dict[str, Any] = {
        "schema": "azazael.algorithm_lab.evidence.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": int(seed),
        "policy": policy,
        "policy_contracts": _policy_contracts(seed),
        "diversity": diversity,
        "nsga3_contract": _diversity_contract(seed),
        "production_rotation_changed": False,
    }
    if record_link is not None:
        _deposit_nsga3(record_link, payload["nsga3_contract"], seed)
    _write_json(output_path, payload)

    regression = _read_regression_report(regression_report)
    evidence_rows = []
    for component, result_artifact in (
        ("POLICY_LAB", output_path),
        ("DIVERSITY_QD_NSGA3", output_path),
    ):
        spec = LAB_COMPONENTS.get(component, {})
        evidence_rows.append(classify(QualificationEvidence(
            component=component,
            executable_path=str(Path(__file__).resolve()),
            deterministic_seed=seed,
            result_artifact=str(result_artifact),
            regression_evidence=regression,
            production_callable=str(spec.get("production_callable", "")),
            notes=("runs in production on the chain (algolab job); "
                   "not a training rotation track"),
        )))
    payload["qualification"] = evidence_rows
    _write_json(output_path, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=314)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--archive", default=str(DEFAULT_ARCHIVE))
    parser.add_argument(
        "--regression-report",
        default=str(DEFAULT_REGRESSION),
        help="JSON report containing passed and vacuous fields "
             "(default: state/algorithm_lab/regression_report.json)",
    )
    args = parser.parse_args(argv)
    report = run_cycle(
        seed=args.seed,
        output=args.output,
        archive=args.archive,
        regression_report=args.regression_report,
    )
    print(json.dumps({
        "seed": report["seed"],
        "policy_rows": len(report["policy"]),
        "diversity": report["diversity"],
        "qualification": [
            {"component": row["component"], "status": row["status"]}
            for row in report["qualification"]
        ],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
