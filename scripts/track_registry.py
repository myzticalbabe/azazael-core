"""track_registry.py — the single source of truth for the training tracks.

Before this module, "which learning algorithm runs" was written down in three
places — the `ROTATION` list in `run_training_orchestrator.py`, its
`_TRACK_EVENTS` reward table, and the pick list handed to `meta_director` — and
they drifted (the orchestrator docstring still said "13 tracks" when there were
17, and `WORMGA` was missing from the reward table entirely). A track added to
one place was invisible to the others, and a missing reward source silently
turned the director's arm into a fabricated win.

This is the one place. `run_training_orchestrator.py` derives its rotation,
reward table, and dispatch from here; `meta_director` is handed `ROTATION`;
`audit_loop_liveness.py` reads the chain's JOBS table independently.

Each spec declares:
  kind    "sb3"    -> TrainingOrchestrator (PPO/DQN/A2C/...)
          "cycle"  -> a run_*_cycle() function in run_training_orchestrator
          "neural" -> NeuralPipeline.run_end_to_end (parallel SB3+GRPO+QLEARN)
  runner  for "cycle"/"neural": the function name to call
  event   reward source for meta_director: (event_type, field, source|None),
          or absent when the track records no comparable scalar. SB3 tracks
          are absent on purpose: they share `sb3_training_orchestrated` and
          are matched by their `algo` field via the default path in
          `_track_recent_rewards`.

Stdlib only. Ordered: `ROTATION` is `list(TRACKS)`, so track order is the
rotation order.
"""

from __future__ import annotations

TRACKS: dict[str, dict] = {
    # -- SB3 policies: TrainingOrchestrator records sb3_training_orchestrated --
    "PPO": {"kind": "sb3"},
    "DQN": {"kind": "sb3"},
    "A2C": {"kind": "sb3"},
    "SAC": {"kind": "sb3"},
    "TD3": {"kind": "sb3"},
    "DDPG": {"kind": "sb3"},
    "QRDQN": {"kind": "sb3"},
    "TRPO": {"kind": "sb3"},
    "TQC": {"kind": "sb3"},
    "ARS": {"kind": "sb3"},
    "MaskablePPO": {"kind": "sb3"},

    # -- Dedicated cycle tracks ------------------------------------------
    "GRPO": {"kind": "cycle", "runner": "run_grpo_cycle",
             "event": ("grpo_training_orchestrated", "eval_mean_reward", None)},
    "DPO": {"kind": "cycle", "runner": "run_dpo_cycle",
            "event": ("dpo_training_orchestrated", "eval_mean_reward", None)},
    "OFFLINE": {"kind": "cycle", "runner": "run_offline_cycle",
                "event": ("offline_training_orchestrated", "eval_mean_reward", None)},
    "RL": {"kind": "cycle", "runner": "run_rl_cycle",
           "event": ("training_run_complete", "avg_reward", "ScheduledTrainingWorm")},
    "DREAMTUNE": {"kind": "cycle", "runner": "run_dreamtune_cycle",
                  "event": ("dreamtune_training_orchestrated", "eval_mean_reward", None)},
    "WORMGA": {"kind": "cycle", "runner": "run_wormga_cycle",
               "event": ("wormga_training_orchestrated", "eval_mean_reward", None)},
    # The technique-outcome intake track (Engram #770 / ACTION_REGISTRY H-3).
    # Its input is a row SHAPE, not a new module: `technique-link` rows already
    # existed and no trainer filtered on that source, and the row's verdict was
    # half-visible to the reward model -- the measured outcome sits in
    # `data["ok"]` while `learning_rl._compute_reward` reads the refinement
    # `data["success"]` (the producer's `severity` already distinguishes the
    # two). `CORE.training_intake.build_training_records()` is the translation;
    # `run_intake_cycle` is its production caller.
    "INTAKE": {"kind": "cycle", "runner": "run_intake_cycle",
               "event": ("intake_training_orchestrated", "eval_mean_reward", None)},

    # The cluster-outcome track (gap-4 resolve, Engram #1655). Same contract
    # as INTAKE but over the ClusterWORMZ pool: cluster_execution_completed
    # rows carried producer-count verdicts nobody trained on. Reader half via
    # `CORE.training_intake.build_cluster_records()`; `run_cluster_cycle`.
    "CLUSTER": {"kind": "cycle", "runner": "run_cluster_cycle",
                "event": ("cluster_training_orchestrated", "eval_mean_reward", None)},

    # -- The former shadow orchestrator, folded in as one arm -------------
    # CORE/NeuralPipeline's parallel SB3+GRPO+QLEARN ensemble used to be a
    # separate chain with its own (broken) result assembly. It is a track now:
    # directed by the same bandit, scored by the same reward path.
    "ALLPAR": {"kind": "neural", "runner": "run_all_parallel_cycle",
               "event": ("allpar_training_orchestrated", "eval_mean_reward", None)},
}

#: Rotation order — the ordered track keys.
ROTATION: list[str] = list(TRACKS)

#: track -> runner function name, for non-SB3 tracks.
RUNNERS: dict[str, str] = {t: s["runner"] for t, s in TRACKS.items()
                           if s.get("runner")}

#: Laboratory components are executable evidence surfaces, not training
#: tracks. They run in PRODUCTION (operator decision 2026-09-24): the chain's
#: `algolab` job invokes `scripts/algorithm_lab_cycle.py` on cadence, and each
#: component records its production callable so
#: `CORE.algorithm_qualification.classify` can reach `served` on real evidence
#: instead of by name alone.
#: They remain outside ROTATION: ROTATION is the training orchestrator's
#: dispatch table (kind sb3/cycle/neural) and these components have no reward
#: event and no orchestrator runner — a lab track added to ROTATION would be
#: dispatched to a runner that does not exist.
LAB_COMPONENTS: dict[str, dict] = {
    "POLICY_LAB": {
        "kind": "laboratory",
        "entrypoint": "scripts/algorithm_lab_cycle.py",
        "production_callable": "scripts/algorithm_lab_cycle.py:run_cycle",
        "production": True,
    },
    "DIVERSITY_QD_NSGA3": {
        "kind": "laboratory",
        "entrypoint": "scripts/algorithm_lab_cycle.py",
        "production_callable": "scripts/algorithm_lab_cycle.py:run_cycle",
        "production": True,
    },
}

#: track -> (event_type, reward_field, source) for tracks with a reward scalar.
REWARD_EVENTS: dict[str, tuple] = {t: tuple(s["event"]) for t, s in TRACKS.items()
                                   if s.get("event")}
