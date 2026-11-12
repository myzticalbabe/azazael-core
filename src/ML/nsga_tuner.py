# coding: utf-8
"""Evolve azazael: NSGA-II tuning of EffGen agent hyperparameters.

Genome (decoded from [0, 1]^2): temperature in [0, 0.7], max_iterations in
[1, 8]. Objectives (both minimized): ``(1 - success_rate)`` over a task
battery plus ``wall_seconds / budget`` (a fixed time budget turns speed into
a second Pareto axis instead of a hidden constraint).

The evaluation harness is injected (``run_agent`` callable) so unit tests
run without a model; the live pilot at the bottom wires EffGen + Ollama.

Not CORE evolution.py (population-based worm optimization); this tunes
hyperparameters. Renamed from ML.evolve 2026-10-10.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass
from typing import Any, Callable, List

import numpy as np

from ML.nsga import NSGA2

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

TIME_BUDGET_S = 600.0


@dataclass(frozen=True)
class AgentGenome:
    temperature: float
    max_iterations: int


def decode(x: np.ndarray) -> AgentGenome:
    """Map unit-cube genes to agent hyperparameters."""
    temp = float(np.clip(x[0], 0.0, 1.0)) * 0.7
    iters = int(np.clip(round(float(x[1]) * 7.0) + 1, 1, 8))
    return AgentGenome(temperature=temp, max_iterations=iters)


Task = dict[str, Any]
RunAgent = Callable[[AgentGenome, Task], tuple[bool, float]]
"""``run_agent(genome, task) -> (success, wall_seconds)``."""


def make_objectives(
    tasks: list[Task],
    run_agent: RunAgent,
    per_task_timeout: float = 420.0,
) -> list[Callable[[np.ndarray], float]]:
    """Build ``(1 - success_rate, time_fraction)`` objectives over ``tasks``."""

    def _evaluate(genome_vec: np.ndarray, task_index: int) -> tuple[bool, float]:
        genome = decode(genome_vec)
        task = tasks[task_index]
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_agent, genome, task)
            try:
                return future.result(timeout=per_task_timeout)
            except FuturesTimeout as exc:
                note_failure("evolve:L59", exc)
                return False, per_task_timeout

    def objective_error(genome_vec: np.ndarray) -> float:
        oks = sum(1 for i in range(len(tasks)) if _evaluate(genome_vec, i)[0])
        return 1.0 - oks / max(1, len(tasks))

    def objective_time(genome_vec: np.ndarray) -> float:
        total = sum(_evaluate(genome_vec, i)[1] for i in range(len(tasks)))
        return total / TIME_BUDGET_S

    return [objective_error, objective_time]


def tune(
    tasks: list[Task],
    run_agent: RunAgent,
    pop_size: int = 8,
    generations: int = 3,
    seed: int = 0,
) -> tuple[list[AgentGenome], np.ndarray]:
    """Run NSGA-II over agent hyperparameters; returns (genomes, objectives)."""
    objectives = make_objectives(tasks, run_agent)
    optimizer = NSGA2(
        objectives,
        bounds=np.tile([0.0, 1.0], (2, 1)),
        pop_size=pop_size,
        seed=seed,
    )
    pareto_x, pareto_f = optimizer.run(generations)
    return [decode(x) for x in pareto_x], pareto_f


CALCULATOR_TASKS: list[Task] = [
    {"prompt": "What is 25 * 17 + sqrt(144)? Reply with only the integer.", "expect": "437"},
    {"prompt": "What is 12 * 12 - 44? Reply with only the integer.", "expect": "100"},
    {"prompt": "What is 7 * 13 + 9? Reply with only the integer.", "expect": "100"},
]


def live_run_agent(genome: AgentGenome, task: Task) -> tuple[bool, float]:
    """EffGen calculator agent on local eve-v2u via Ollama."""
    from effgen import Agent, load_model
    from effgen.core.agent import AgentConfig
    from effgen.tools.builtin import Calculator

    started = time.time()
    try:
        model = load_model("eve-v2u", base_url="http://127.0.0.1:11434/v1")
        agent = Agent(AgentConfig(
            model=model,
            tools=[Calculator()],
            system_prompt="You are a careful arithmetic assistant.",
            temperature=genome.temperature,
            max_iterations=genome.max_iterations,
        ))
        response = agent.run(task["prompt"])
        text = response.text if hasattr(response, "text") else str(response)
        return task["expect"] in text, time.time() - started
    except Exception as exc:
        note_failure("evolve:L118", exc)
        return False, time.time() - started
