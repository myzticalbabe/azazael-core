# coding: utf-8
"""


__all__ = [
    "ALGO_DEFAULTS",
    "SB3Agent",
    "SUPPORTED_ALGOS",
]

SB3Agent — FreeThreadedAgent wrapping stable-baselines3 RL algorithms.

Provides a FreeThreadedAgent subclass that wraps SB3's PPO, DQN, A2C, SAC,
TD3, DDPG plus the sb3-contrib QRDQN/TRPO/TQC/ARS/MaskablePPO algorithms
with automatic DataPipelineWorm integration for training data, ModelVersioner
for version tracking, and PAMS/MemoryBus for observability.

Features:
- train() — Train from LocalMemoryStore data via DataPipelineWorm
- predict() — Inference using trained model
- save() / load() — Model persistence
- Auto-register with ModelVersioner after training
- MemoryBus events for training progress

Usage:
    agent = SB3Agent(algo="PPO", policy="MlpPolicy")
    
    # Train from memory data
    result = agent.train(source="SystemZ", total_timesteps=1000)
    
    # Predict
    action, state = agent.predict(observation)
    
    # Persist
    agent.save("models/my_model")
    agent.load("models/my_model")
"""

import sys
import json
import time
import os
import subprocess
import threading
import numpy as np

from datetime import datetime
from pathlib import Path
from typing import Any, List, TYPE_CHECKING, cast

if TYPE_CHECKING:
    # Annotation-only aliases: the runtime bindings of these names are
    # try/except imports with None fallbacks (`BaseAlgorithm = None`, etc.),
    # which would make the plain names resolve as variables in type
    # expressions. Distinct alias names keep a single class binding.
    # Never executed at runtime.
    from stable_baselines3.common.base_class import BaseAlgorithm as _SB3ModelT
    from CORE.WORMS.expansion.learning import (
        DataPipelineWorm as _DataPipelineT,
        ModelVersioner as _ModelVersionT,
    )
    import gymnasium as _GymModuleT

# -- Import CORE utilities ----------------------------------------------------
# Add parent dir (src/) to sys.path, NOT CORE/ directly, to avoid CORE/logging.py
# shadowing the stdlib logging module (required by gymnasium/sb3)
_azazael_src = str(Path(__file__).resolve().parent.parent)
if _azazael_src not in sys.path:
    sys.path.insert(0, _azazael_src)

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

from CORE.Agent import FreeThreadedAgent, AgentConfig, IS_FREE_THREADED

# -- SB3 with graceful fallback ----------------------------------------------
# NOTE: importing stable_baselines3 pulls in torch, whose C-extensions SEGFAULT
# free-threaded 3.14t at import time (even with -X gil=1). So on free-threaded
# Python with the GIL disabled, skip the import entirely — train() will route
# to the GIL subprocess fallback instead.
try:
    import sysconfig as _sysconfig
    _SKIP_SB3_IMPORT = (
        _sysconfig.get_config_var("Py_GIL_DISABLED") == 1
        and hasattr(sys, "_is_gil_enabled")
        and not sys._is_gil_enabled()
        and os.environ.get("AZAZAEL_GIL_ENFORCED") != "1"
    )
except Exception as exc:
    note_failure("sb3_agent:L75", exc)
    _SKIP_SB3_IMPORT = False

if not _SKIP_SB3_IMPORT:
    try:
        from stable_baselines3 import PPO, DQN, A2C, SAC, TD3, DDPG
        from stable_baselines3.common.base_class import BaseAlgorithm
        HAS_SB3 = True
    except ImportError as exc:
        note_failure("sb3_agent:L83", exc)
        HAS_SB3 = False
        PPO = DQN = A2C = SAC = TD3 = DDPG = None
        BaseAlgorithm = object
else:
    HAS_SB3 = False
    PPO = DQN = A2C = SAC = TD3 = DDPG = None
    BaseAlgorithm = object

# sb3-contrib algorithms (QRDQN discrete, TRPO/TQC/ARS continuous,
# MaskablePPO discrete with an all-ones action mask).
try:
    from sb3_contrib import QRDQN, TRPO, TQC, ARS, MaskablePPO
    HAS_SB3_CONTRIB = True
except Exception as exc:
    note_failure("sb3_agent:L97", exc)
    HAS_SB3_CONTRIB = False
    QRDQN = TRPO = TQC = ARS = MaskablePPO = None

try:
    from sb3_contrib.common.wrappers import ActionMasker
    HAS_ACTION_MASKER = True
except Exception as exc:
    note_failure("sb3_agent:L104", exc)
    HAS_ACTION_MASKER = False
    ActionMasker = None

try:
    import gymnasium as gym
    HAS_GYM = True
except ImportError as exc:
    note_failure("sb3_agent:L111", exc)
    HAS_GYM = False
    gym = None

try:
    import torch
    HAS_TORCH = True
except ImportError as exc:
    note_failure("sb3_agent:L118", exc)
    HAS_TORCH = False
    torch = None
    torch = None

try:
    HAS_SKLEARN = True
except ImportError as exc:
    note_failure("sb3_agent:L125", exc)
    HAS_SKLEARN = False

# -- DataPipelineWorm for training data --------------------------------------
try:
    from CORE.WORMS.expansion.learning import DataPipelineWorm
    HAS_PIPELINE = True
except ImportError as exc:
    note_failure("sb3_agent:L132", exc)
    HAS_PIPELINE = False
    DataPipelineWorm = None

# -- ModelVersioner for version tracking -------------------------------------
try:
    from CORE.WORMS.expansion.learning import ModelVersioner
    HAS_VERSIONER = True
except ImportError as exc:
    note_failure("sb3_agent:L140", exc)
    HAS_VERSIONER = False
    ModelVersioner = None

# -- New trainers_v2 (per-worm hook identity) --------------------------------
try:
    from CORE.WORMS.expansion.trainers_v2 import (
        TrainerContext,
        HookAwareTrainer,
        SB3TrainerV2,
        create_trainer,
    )
    HAS_TRAINERS_V2 = True
except ImportError as exc:
    note_failure("sb3_agent:L150", exc)
    HAS_TRAINERS_V2 = False
    TrainerContext = HookAwareTrainer = SB3TrainerV2 = create_trainer = None

# -- Morphed trainers (outer-loop closure) -----------------------------------
try:
    from CORE.morphed_trainers import MORPHABLE_KNOBS, resolve_morphed
    HAS_MORPHED = True
except ImportError as exc:
    note_failure("sb3_agent:morphed-import", exc)
    HAS_MORPHED = False
    resolve_morphed = None
    MORPHABLE_KNOBS = {}

# -- MemoryBus for messaging -------------------------------------------------
try:
    HAS_MEMORYBUS = True
except ImportError as exc:
    note_failure("sb3_agent:L147", exc)
    HAS_MEMORYBUS = False

# -- Agent registry ----------------------------------------------------------
try:
    from CORE.agent_registry import register_agent  # pyright: ignore[reportAssignmentType]
except ImportError as exc:
    note_failure("sb3_agent:L153", exc)

    def register_agent(**kw):
        return lambda cls: cls


# -- GIL-enforced fallback for free-threaded Python --------------------------

def _needs_gil_enforcement() -> bool:
    """True when running on free-threaded Python with the GIL disabled.

    SB3/torch C-extensions are known to segfault under 3.14t no-GIL, so
    deep training must run under a GIL-enabled interpreter.
    """
    try:
        import sysconfig
        return (sysconfig.get_config_var("Py_GIL_DISABLED") == 1
                and hasattr(sys, "_is_gil_enabled")
                and not sys._is_gil_enabled())
    except Exception as exc:
        note_failure("sb3_agent:L171", exc)
        return False


def _find_gil_python() -> (str | None):
    """Locate a standard (GIL-enabled) Python interpreter.

    Prefers AZAZAEL_GIL_PYTHON env var, then the same-version GIL sidecar
    (3.15t production pairs with the 3.15 GIL venv, so a suite comparison
    isolates threading rather than version drift), then the sibling standard
    build, then py -V:3.15. Returns None if no GIL interpreter is found.
    """
    # 1. Explicit override
    cand = os.environ.get("AZAZAEL_GIL_PYTHON")
    if cand and os.path.isfile(cand):
        return cand

    # 2. Same-version GIL sidecar. 3.14t was retired 2026-10-19; the 3.15
    #    GIL venv is the sidecar that actually matches production.
    sidecar = Path("C:/Azazael/.venvs/py315/Scripts/python.exe")
    if sidecar.is_file():
        return str(sidecar)

    # 2b. WSL pillar: the sanctioned GIL entry points unset PYTHON_GIL
    #     themselves, so the child survives an exported PYTHON_GIL=0.
    for wsl_gil in ("/usr/local/bin/python3.14-gil",
                    "/root/venv-314-gil/bin/py"):
        if os.path.isfile(wsl_gil):
            return wsl_gil

    # 3. Sibling standard build next to the free-threaded exe
    #    (.../python3.15t.exe -> .../python.exe)
    try:
        sib = Path(sys.executable).with_name("python.exe")
        if sib.is_file() and sib.resolve() != Path(sys.executable).resolve():
            return str(sib)
    except Exception as exc:
        note_failure("sb3_agent:L193", exc)

    # 4. py launcher discovery (best-effort, non-fatal)
    try:
        proc = subprocess.run(
            ["py", "-V:3.15", "-c",
             "import sys; print(sys.executable)"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        if proc.returncode == 0:
            p = proc.stdout.strip().splitlines()[-1].strip()
            if p and os.path.isfile(p):
                return p
    except Exception as exc:
        note_failure("sb3_agent:L207", exc)
    return None


def _run_training_in_gil_subprocess(
    algo: str, policy: str, device: str, seed: int,
    source: (str | None), event_type: (str | None),
    total_timesteps: int, model_name: (str | None),
    worm_id: (str | None) = None,
) -> dict:
    """Run SB3 training in a standard (GIL-enabled) Python subprocess.

    Used when the host process is free-threaded 3.14t, where SB3/torch
    segfault even with -X gil=1 (the t-build itself is unsafe to import
    these C-extensions). The subprocess uses a standard interpreter,
    trains, registers with ModelVersioner, and prints a JSON result.
    """
    gil_py = _find_gil_python()
    if gil_py is None:
        return {
            "error": "No GIL-enabled Python found for SB3 fallback "
                     "(set AZAZAEL_GIL_PYTHON)",
            "trained": False,
        }

    cmd = [
        gil_py, "-c", _GIL_SUBPROCESS_CODE,
        "--", algo, policy, device, str(seed),
        source or "", event_type or "", str(total_timesteps),
        model_name or "",
        worm_id or "",
    ]
    env = dict(os.environ)
    # A GIL build aborts when PYTHON_GIL=0 is exported (see
    # CORE/python_env.probe); the wrapper entry points above already
    # unset it, but never trust the ambient env for a GIL child.
    env.pop("PYTHON_GIL", None)
    env["AZAZAEL_GIL_ENFORCED"] = "1"  # guard against re-exec loops
    env["AZAZAEL_SRC"] = _azazael_src  # bootstrap sys.path for CORE import
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        note_failure("sb3_agent:L246", exc)
        return {"error": "GIL subprocess training timed out (30 min)", "trained": False}
    except FileNotFoundError as exc:
        note_failure("sb3_agent:L248", exc)
        return {"error": f"GIL interpreter not found: {gil_py}", "trained": False}

    # Parse the last JSON line of stdout (prints may precede it)
    payload = None
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError as exc:
                note_failure("sb3_agent:L259", exc)
                continue
    if payload is None:
        return {
            "error": f"GIL subprocess failed (rc={proc.returncode}): "
                     f"{(proc.stderr or proc.stdout or '')[-400:]}",
            "trained": False,
        }
    payload["via_gil_subprocess"] = True
    return payload


# Code executed inside the GIL-enforced subprocess. Reads argv after '--'.
_GIL_SUBPROCESS_CODE = r'''
import sys, json, os
_src = os.environ.get("AZAZAEL_SRC")
if _src and _src not in sys.path:
    sys.path.insert(0, _src)
sys.argv = sys.argv[sys.argv.index("--") + 1:]
algo, policy, device, seed = sys.argv[0], sys.argv[1], sys.argv[2], int(sys.argv[3])
source = sys.argv[4] or None
event_type = sys.argv[5] or None
total_timesteps = int(sys.argv[6])
model_name = sys.argv[7] or None
worm_id = sys.argv[8] if len(sys.argv) > 8 else None
try:
    from CORE.sb3_agent import SB3Agent
    agent = SB3Agent(algo=algo, policy=policy, device=device, seed=seed,
                       worm_id=worm_id)
    result = agent.train(source=source, event_type=event_type,
                         total_timesteps=total_timesteps,
                         model_name=model_name, register_model=True)
    result.setdefault("worm_id", worm_id)
    result["gil_enforced"] = True
    print(json.dumps(result))
except Exception as e:
    print(json.dumps({"error": f"{type(e).__name__}: {e}", "trained": False,
                      "worm_id": worm_id,
                      "gil_enforced": True}))
'''


# -- Supported algorithms ----------------------------------------------------


# -- Supported algorithms ----------------------------------------------------

# Algorithms whose env uses the continuous Box(3) soft-weight action space;
# the rest get the Discrete(3) severity-response space.
CONTINUOUS_ALGOS = frozenset({"SAC", "TD3", "DDPG", "TQC", "TRPO", "ARS"})

SUPPORTED_ALGOS = {
    "PPO": PPO,
    "DQN": DQN,
    "A2C": A2C,
    "SAC": SAC,        # continuous action space (Box) — see _create_env_from_records
    "TD3": TD3,         # continuous action space (Box)
    "DDPG": DDPG,       # continuous action space (Box)
    "QRDQN": QRDQN,     # discrete (distributional Q-learning)
    "TRPO": TRPO,       # continuous action space (Box)
    "TQC": TQC,         # continuous action space (Box)
    "ARS": ARS,         # continuous action space (Box)
    "MaskablePPO": MaskablePPO,  # discrete w/ all-ones action mask
}

ALGO_DEFAULTS = {
    "PPO": {
        "learning_rate": 3e-4,
        "n_steps": 2048,
        "batch_size": 64,
        "n_epochs": 10,
        "gamma": 0.99,
        "gae_lambda": 0.95,
        "clip_range": 0.2,
        "ent_coef": 0.0,
        "vf_coef": 0.5,
        "max_grad_norm": 0.5,
    },
    "DQN": {
        "learning_rate": 1e-4,
        "buffer_size": 50000,
        "learning_starts": 1000,
        "batch_size": 32,
        "tau": 1.0,
        "gamma": 0.99,
        "train_freq": 4,
        "target_update_interval": 1000,
    },
    "SAC": {
        "learning_rate": 3e-4,
        "buffer_size": 50000,
        "learning_starts": 500,
        "batch_size": 256,
        "tau": 0.005,
        "gamma": 0.99,
        "train_freq": 1,
        "gradient_steps": 1,
        "ent_coef": "auto",
    },
    "TD3": {
        "learning_rate": 3e-4,
        "buffer_size": 50000,
        "learning_starts": 500,
        "batch_size": 256,
        "tau": 0.005,
        "gamma": 0.99,
        "train_freq": 1,
        "gradient_steps": 1,
        "policy_kwargs": {"net_arch": [256, 256]},
    },
    "A2C": {
        "learning_rate": 7e-4,
        "n_steps": 5,
        "gamma": 0.99,
        "gae_lambda": 1.0,
        "ent_coef": 0.01,
        "vf_coef": 0.5,
        "max_grad_norm": 0.5,
    },
    "DDPG": {
        "learning_rate": 1e-3,
        "buffer_size": 50000,
        "learning_starts": 500,
        "batch_size": 256,
        "tau": 0.005,
        "gamma": 0.99,
        "train_freq": 1,
        "gradient_steps": 1,
        "policy_kwargs": {"net_arch": [256, 256]},
    },
    "QRDQN": {
        "learning_rate": 1e-4,
        "buffer_size": 50000,
        "learning_starts": 1000,
        "batch_size": 32,
        "tau": 1.0,
        "gamma": 0.99,
        "train_freq": 4,
        "target_update_interval": 1000,
    },
    "TRPO": {
        "learning_rate": 1e-3,
        "n_steps": 512,
        "batch_size": 128,
        "gamma": 0.99,
        "gae_lambda": 0.95,
        "cg_max_steps": 10,
        "target_kl": 0.01,
    },
    "TQC": {
        "learning_rate": 3e-4,
        "buffer_size": 50000,
        "learning_starts": 500,
        "batch_size": 256,
        "tau": 0.005,
        "gamma": 0.99,
        "train_freq": 1,
        "gradient_steps": 1,
        "ent_coef": "auto",
        "top_quantiles_to_drop_per_net": 2,
    },
    "ARS": {
        "learning_rate": 0.02,
        "n_delta": 8,
        "n_top": 4,
        "delta_std": 0.05,
    },
    "MaskablePPO": {
        "learning_rate": 3e-4,
        "n_steps": 2048,
        "batch_size": 64,
        "n_epochs": 10,
        "gamma": 0.99,
        "gae_lambda": 0.95,
        "clip_range": 0.2,
        "ent_coef": 0.0,
        "vf_coef": 0.5,
        "max_grad_norm": 0.5,
    },
}


def _next_model_version(versioner, name: str, fallback: int = 1) -> str:
    """Next vN for a stable per-algo model name (2026-09-09 fix).

    The old scheme (timestamped name + v{len(in-memory history)}) reset to
    v1 on every scheduled run because each run is a fresh process with an
    empty history. get_latest_version falls back to persisted
    model_registered rows, so this counter survives across processes.
    """
    try:
        latest = versioner.get_latest_version(name) if versioner else None
        if latest:
            n = int(str(latest.get("version", "v0")).lstrip("v").split(".")[0])
            return f"v{n + 1}"
    except (ValueError, TypeError, AttributeError) as exc:
        note_failure("sb3_agent:L450", exc)
    return f"v{max(int(fallback or 1), 1)}"


@register_agent(category="rl", priority=8, description="SB3-based RL agent wrapping PPO/DQN/A2C")
class SB3Agent(FreeThreadedAgent):
    """
    FreeThreadedAgent wrapping stable-baselines3 RL algorithms.

    Features:
    - train() from LocalMemoryStore data via DataPipelineWorm
    - predict() for inference
    - save() / load() for persistence
    - Auto ModelVersioner registration
    - MemoryBus event publishing
    - Gym environment auto-creation for training

    Usage:
        agent = SB3Agent(algo="PPO")
        result = agent.train(source="SystemZ", total_timesteps=1000)
        action, _ = agent.predict(observation)
        agent.save("models/my_model.zip")
    """

    VERSION = "1.0.0"

    def __init__(self, config: (AgentConfig | None) = None, algo: str = "PPO",
                 policy: str = "MlpPolicy", algo_kwargs: (dict | None) = None,
                 device: str = "auto", seed: (int | None) = None,
                 env_kwargs: (dict | None) = None,
                 worm_id: (str | None) = None):
        """
        Initialize SB3Agent.

        Args:
            config: AgentConfig for the base FreeThreadedAgent.
            algo: Algorithm name: "PPO", "DQN", "A2C", "SAC", "TD3",
                  "DDPG", "QRDQN", "TRPO", "TQC", "ARS", or "MaskablePPO"
                  (SAC/TD3/DDPG/TQC/TRPO/ARS use a continuous Box(3)
                  action space; the rest use Discrete(3); MaskablePPO
                  additionally gets an all-ones action mask).
            policy: SB3 policy type: "MlpPolicy", "CnnPolicy", "MultiInputPolicy".
            algo_kwargs: Additional kwargs passed to the SB3 constructor.
            device: Torch device ("auto", "cpu", "cuda"). "auto" picks cuda if available.
            seed: Random seed for reproducibility.
            env_kwargs: Additional kwargs for the gym environment.
            worm_id: Worm identity threaded into all memory writes
                (PAMS payload, bus events, versioner metadata).
                Defaults to f"sb3_<algo>".
        """
        super().__init__(config or AgentConfig(
            agent_name=f"SB3-{algo}",
            pams_source="sb3_agent",
        ))

        # Resolve to the canonical registry name case-insensitively. A blanket
        # .upper() previously turned "MaskablePPO" into MASKABLEPPO, which
        # matches no key in SUPPORTED_ALGOS and made it report "unsupported".
        _canon = next((k for k in SUPPORTED_ALGOS if str(k).lower() == str(algo).lower()), None)
        # The `else "PPO"` fallback used to rewrite EVERY name on a host without
        # stable-baselines3, including `morphed_ppo_g1` -- so the morphed branch
        # below could never match and a resolved registry entry arrived with no
        # params. A morphed name is kept as asked; it is resolved to its base
        # algo two blocks down, which is where the fallback belongs.
        _keep_as_asked = HAS_SB3 or str(algo).lower().startswith("morphed_")
        self.algo_name = _canon or (algo if _keep_as_asked else "PPO")
        # Morphed resolution (outer-loop closure): morphed_<algo>_g<gen>
        # names resolve to their base algo with Pareto params merged into
        # algo_kwargs. The name is preserved on worm_id for lineage.
        self.morphed_params: dict = {}
        self.algo_kwargs = algo_kwargs or {}
        if self.algo_name.lower().startswith("morphed_") and HAS_MORPHED:
            try:
                _entry = resolve_morphed(self.algo_name) if resolve_morphed is not None else None
            except Exception as exc:
                note_failure("sb3_agent:morphed-resolve", exc)
                _entry = None
            if _entry:
                _base = next((k for k in SUPPORTED_ALGOS
                              if k.lower() == str(_entry.get("algo", "")).lower()), None)
                if _base:
                    self.algo_name = _base
                    # E4: the registry is hand-editable tracked source, so
                    # stored params are allow-listed against the searchable
                    # knobs before they reach the SB3 constructor. Unknown
                    # keys are dropped with a ledger note, never crash.
                    _knobs = MORPHABLE_KNOBS.get(_base) or []
                    _allowed = {str(k) for k, *_ in _knobs}
                    if not _allowed:
                        _allowed = {str(k) for knobs in MORPHABLE_KNOBS.values()
                                    for k, *_ in knobs}
                    self.morphed_params = {}
                    for _k, _v in dict(_entry.get("params", {})).items():
                        if str(_k) in _allowed:
                            self.morphed_params[_k] = _v
                        else:
                            note_failure("sb3_agent:morphed-kwarg-drop", None)
                    merged = dict(self.morphed_params)
                    merged.update(self.algo_kwargs)
                    self.algo_kwargs = merged
        # Worm identity (trainers_v2 contract): every memory write
        # this agent emits carries this id in its payload.
        self.worm_id = worm_id or f"sb3_{self.algo_name.lower()}"
        self.policy = policy
        self.env_kwargs = env_kwargs or {}
        self.seed = seed or int(time.time())

        # Device selection
        if device == "auto" and HAS_TORCH and torch is not None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device or "cpu"

        # SB3 model (lazy init)
        self._model: '(_SB3ModelT | None)' = None
        self._env: '(_GymModuleT.Env | None)' = None
        self._model_lock = threading.Lock()

        # DataPipelineWorm (lazy)
        self._pipeline: '(_DataPipelineT | None)' = None

        # ModelVersioner (lazy)
        self._versioner: '(_ModelVersionT | None)' = None

        # Training metadata
        self._training_history: list[dict] = []
        self._last_train_time: (float | None) = None
        self._total_timesteps_trained = 0

        # Define observation space for the default environment
        self._obs_dim = 4  # Default: 4-dim continuous (can be overridden)
        self._action_dim = 2  # Default: 2-dim discrete

        if not HAS_SB3:
            self.set_state("sb3_available", False)
            if self.config.log_level in ("debug", "warning"):
                print(f"[{self.agent_id}] WARNING: SB3 not installed. "
                      f"Install: pip install stable-baselines3")

    # -- Properties ----------------------------------------------------------

    @property
    def is_trained(self) -> bool:
        """Whether the model has been trained."""
        return self._model is not None

    @property
    def algo_class(self):
        """Get the SB3 algorithm class for the current algo_name."""
        if not HAS_SB3:
            return None
        return SUPPORTED_ALGOS.get(self.algo_name)

    # -- Public API ----------------------------------------------------------

    def train(self, source: (str | None) = None, event_type: (str | None) = None,
              total_timesteps: int = 10000, n_episodes: (int | None) = None,
              learning_rate: (float | None) = None, use_data_split: bool = True,
              max_age_days: int = 30, model_name: (str | None) = None,
              register_model: bool = True, exclude_sources: (list[str] | None) = None,
              records: list[dict] | None = None,  # frozen-record seam
              **kwargs) -> dict[str, Any]:
        """
        Train the SB3 model using data from LocalMemoryStore.

        Uses DataPipelineWorm to extract training data, creates a gym
        environment from the extracted features, and trains the SB3 model.

        Args:
            source: Filter memories by source component.
            event_type: Filter by event type.
            total_timesteps: Total training timesteps for SB3.
            n_episodes: If set, overrides total_timesteps as episodes × max_steps.
            learning_rate: Override the default learning rate.
            use_data_split: If True, use train/val split from pipeline.
            max_age_days: Only include records newer than N days.
            model_name: Name for ModelVersioner registration.
            register_model: If True, auto-register with ModelVersioner.
            exclude_sources: Skip events from these source components so the
                training slice isn't dominated by self-generated bookkeeping
                (e.g. RLTrainLoop/ModelVersioner events).
            **kwargs: Additional kwargs passed to SB3 model.learn().

        Returns:
            dict with training stats.
        """
        # -- GIL enforcement fallback (checked FIRST — SB3 may not even be
        # importable here; on free-threaded 3.14t the torch import segfaults,
        # so HAS_SB3 is False and the fallback below is the only safe path) --
        if os.environ.get("AZAZAEL_GIL_ENFORCED") != "1" and (
                _needs_gil_enforcement() or not HAS_SB3):
            gil_py = _find_gil_python()
            if gil_py is not None:
                print(f"[SB3Agent] GIL-enforced fallback via {gil_py}", flush=True)
                gil_result = _run_training_in_gil_subprocess(
                    algo=self.algo_name, policy=self.policy, device=self.device,
                    seed=self.seed, source=source, event_type=event_type,
                    total_timesteps=(n_episodes * 200 if n_episodes else total_timesteps),
                    model_name=model_name, worm_id=self.worm_id,
                )
                gil_result.setdefault("worm_id", self.worm_id)
                return gil_result

        if not HAS_SB3:
            return {"error": "stable-baselines3 not installed", "trained": False,
                    "worm_id": self.worm_id}

        start = time.perf_counter()

        # -- Step 1: Extract training data (or use frozen records) -----------
        if records is not None:
            # Frozen-record seam: use the provided records and skip store extraction
            # this is the entry point that lets technique-intake records reach
            # trainers deterministically without touching the live store
            pass  # records already in scope; skip pipeline.extract_from_memory
        else:
            pipeline = self._get_pipeline()
            assert pipeline is not None  # None was an unguarded AttributeError here before
            records = pipeline.extract_from_memory(
                source=source, event_type=event_type,
                limit=5000, max_age_days=max_age_days,
                exclude_sources=exclude_sources,
            )

        if not records:
            return {"error": f"No training data found for source={source}", "trained": False,
                    "worm_id": self.worm_id}

        # -- Step 2: Create environment --------------------------------
        try:
            env = self._create_env_from_records(
                records, continuous=self.algo_name in CONTINUOUS_ALGOS)
            if self.algo_name == "MaskablePPO":
                # MaskablePPO requires the env to expose an action mask;
                # all-ones = no action is constrained yet, so this is a
                # no-op mask that keeps the algorithm trainable.
                if ActionMasker is None:
                    raise RuntimeError("sb3_contrib ActionMasker unavailable")

                def _noop_mask(venv):
                    return np.ones(venv.action_space.n, dtype=bool)

                env = ActionMasker(env, _noop_mask)
        except Exception as e:
            note_failure("sb3_agent:L636", e)
            return {"error": f"Failed to create environment: {e}", "trained": False,
                    "worm_id": self.worm_id}

        # -- Step 3: Override params ----------------------------------
        params = dict(ALGO_DEFAULTS.get(self.algo_name, {}))
        if learning_rate is not None:
            params["learning_rate"] = learning_rate
        params.update(self.algo_kwargs)
        params.update(kwargs)

        # -- Step 4: Create or re-init model ---------------------------
        try:
            with self._model_lock:
                if self._model is None:
                    algo_cls = self.algo_class
                    if algo_cls is None:
                        return {"error": f"Unsupported algorithm: {self.algo_name}"}

                    self._model = algo_cls(
                        self.policy,
                        env,
                        verbose=0 if self.config.log_level != "debug" else 1,
                        device=self.device,
                        seed=self.seed,
                        **params,
                    )
                else:
                    self._model.set_env(env)

                # Train
                n = n_episodes * 200 if n_episodes else total_timesteps
                model = self._model
                assert model is not None  # both branches above assign it; narrows for .learn()
                model.learn(total_timesteps=n, reset_num_timesteps=False)
                self._total_timesteps_trained += n

        except Exception as e:
            note_failure("sb3_agent:L670", e)
            return {"error": f"Training failed: {e}", "trained": False,
                    "worm_id": self.worm_id}

        duration_ms = (time.perf_counter() - start) * 1000
        self._last_train_time = time.time()

        # -- Step 4b: Evaluate the trained policy -----------------------
        # Roll out the learned policy over the same record-derived env so
        # artifacts carry a reward signal (previously only timestep counts
        # were recorded — you could see training happened, not whether the
        # policy got better). Best-effort; evaluation failures don't fail
        # the run.
        eval_mean_reward = None
        eval_std_reward = None
        try:
            from stable_baselines3.common.evaluation import evaluate_policy
            # evaluate_policy is an optional-dep stub here (sb3 is not installed):
            # cast through Any so the Unknown stub types flow into float()/round().
            _er_mean, _er_std = evaluate_policy(
                cast(Any, self._model), env, n_eval_episodes=10, deterministic=True)
            eval_mean_reward = round(float(cast(Any, _er_mean)), 4)
            eval_std_reward = round(float(cast(Any, _er_std)), 4)
        except Exception as exc:
            note_failure("sb3_agent:L690", exc)

        # -- Step 5: Record training stats -----------------------------
        train_result = {
            "algo": self.algo_name,
            "worm_id": self.worm_id,
            "policy": self.policy,
            "total_timesteps": n,
            "records_used": len(records),
            "data_source": source or "all",
            "duration_ms": round(duration_ms, 2),
            "device": self.device,
            "learning_rate": params.get("learning_rate"),
            "total_timesteps_trained": self._total_timesteps_trained,
            "eval_mean_reward": eval_mean_reward,
            "eval_std_reward": eval_std_reward,
            "trained": True,
        }

        self._training_history.append(train_result)
        if len(self._training_history) > 50:
            self._training_history = self._training_history[-50:]

        # Record in PAMS
        if self._pams:
            self._pams.record_event(
                "sb3_training_complete",
                train_result,
                source=self.config.pams_source, severity="info",
            )

        # Publish on bus
        self._publish_bus_event(f"agent.{self.agent_id}.training_complete", train_result)

        # -- Step 6: Register with ModelVersioner ----------------------
        # Stable per-algo name (run timestamp moves to metadata) so the
        # version lineage accumulates instead of restarting at v1.
        if register_model and HAS_VERSIONER:
            mname = model_name or f"SB3_{self.algo_name}"
            try:
                versioner = self._get_versioner()
                version = _next_model_version(
                    versioner, mname, fallback=len(self._training_history))
                assert versioner is not None  # None.register_model() previously crashed (caught at L789)
                versioner.register_model(
                    name=mname,
                    version=version,
                    metrics={
                        "total_timesteps": n,
                        "records_used": len(records),
                        "duration_ms": round(duration_ms, 2),
                    },
                    metadata={
                        "algo": self.algo_name,
                        "policy": self.policy,
                        "device": self.device,
                        "data_source": source or "all",
                        "worm_id": self.worm_id,
                        "run_ts": datetime.now().isoformat(),
                    },
                )
                train_result["registered_model"] = mname
            except Exception as e:
                note_failure("sb3_agent:L750", e)
                train_result["registry_error"] = str(e)

        return train_result

    def predict(self, observation: np.ndarray, deterministic: bool = True,
                state: Any = None) -> tuple[Any, Any]:
        """
        Predict an action from an observation.

        Args:
            observation: The environment observation.
            deterministic: Whether to use deterministic or stochastic policy.
            state: Optional recurrent state.

        Returns:
            (action, next_state) tuple.
        """
        if not HAS_SB3 or self._model is None:
            # Fallback: default action. No model means no learned policy —
            # returning a RANDOM action here lets an untrained agent randomly
            # fire remediate/alert on production records. Return deterministic
            # action 0 (ignore) instead. Scalar ndarray to match SB3's
            # Discrete-action output shape.
            return np.asarray(0, dtype=np.int64), None

        try:
            return self._model.predict(observation, deterministic=deterministic, state=state)
        except Exception as exc:
            note_failure("sb3_agent:L778", exc)
            self._increment_stat('tasks_failed')
            # Same default-action contract as the untrained fallback.
            return np.asarray(0, dtype=np.int64), None

    def predict_batch(self, observations: list[np.ndarray],
                      deterministic: bool = True) -> list[Any]:
        """Predict actions for a batch of observations."""
        return [self.predict(obs, deterministic=deterministic)[0] for obs in observations]

    def save(self, path: str | Path) -> bool:
        """
        Save the trained model to disk.

        Args:
            path: File path (without extension, .zip will be added).

        Returns:
            True if saved successfully.
        """
        if not HAS_SB3 or self._model is None:
            return False
        try:
            path = str(path)
            self._model.save(path)
            return True
        except Exception as exc:
            note_failure("sb3_agent:L804", exc)
            return False

    def load(self, path: str | Path, env: Any = None) -> bool:
        """
        Load a trained model from disk.

        Args:
            path: File path (without extension).
            env: Optional environment to associate with the model.

        Returns:
            True if loaded successfully.
        """
        if not HAS_SB3:
            return False
        try:
            path = str(path)
            algo_cls = self.algo_class
            if algo_cls is None:
                return False
            with self._model_lock:
                self._model = algo_cls.load(path, env=env or self._env)
            return True
        except Exception as exc:
            note_failure("sb3_agent:L828", exc)
            return False

    def get_training_history(self, limit: int = 10) -> list[dict]:
        """Get recent training history."""
        return self._training_history[-limit:]

    # -- Environment Creation ------------------------------------------------

    def _create_env_from_records(self, records: list[dict],
                                 continuous: bool = False) -> '_GymModuleT.Env':
        """
        Create a gym environment from LocalMemoryStore records.

        Transforms records into a simple vector environment where:
        - Observation: feature vector extracted from record data
        - Action: discrete action space (0 = info, 1 = warning, 2 = error), or
          continuous Box(3) (soft weights over the 3 actions) for SAC/TD3
        - Reward: based on record severity

        Handles variable-length feature vectors by padding shorter records
        with zeros to match the maximum feature dimension.
        """
        if not HAS_GYM:
            raise ImportError("gymnasium is required for environment creation")

        # Extract features from records
        features = []
        for r in records:
            data = r.get("data", {})
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (json.JSONDecodeError, TypeError) as exc:
                    note_failure("sb3_agent:L861", exc)
                    data = {}
            if not isinstance(data, dict):
                data = {}

            # Build observation vector
            obs = []
            # Severity ordinal
            sev_map = {"info": 0.0, "warning": 0.33, "error": 0.67, "critical": 1.0}
            obs.append(sev_map.get(r.get("severity", "info"), 0.0))

            # Numeric data fields (normalized)
            for val in data.values():
                if isinstance(val, (int, float)):
                    obs.append(min(max(float(val) / 100.0, 0.0), 1.0))
                elif isinstance(val, bool):
                    obs.append(1.0 if val else 0.0)

            if obs:
                features.append(obs)

        if not features:
            # Default observation space
            self._obs_dim = 4
            self._action_dim = 3 if continuous else 2
            return self._create_default_env(continuous=continuous)

        # Pad all feature vectors to the same length using a sentinel value
        # outside the normalized [0,1] range. The model learns to ignore -2.0
        # as a "missing feature" signal (P2-6: proper attention mask is future work).
        max_dim = max(len(f) for f in features)
        for f in features:
            if len(f) < max_dim:
                f.extend([-2.0] * (max_dim - len(f)))

        # Determine observation dimension
        self._obs_dim = max_dim
        self._action_dim = 3  # info, warning, error

        # Create environment
        return self._create_vector_env(features, continuous=continuous)

    def _create_vector_env(self, feature_vectors: list[list[float]],
                           continuous: bool = False) -> '_GymModuleT.Env':
        """
        Create a simple vector-based gym environment.

        The environment cycles through the feature vectors, and the agent's
        goal is to predict the correct severity level for each observation.
        """
        import gymnasium as gym
        from gymnasium.spaces import Box, Discrete

        class _MemoryDatasetEnv(gym.Env):
            """Gym environment from LocalMemoryStore data."""

            def __init__(self, features: list[list[float]], continuous: bool = False):
                super().__init__()
                self.features = features
                self.n = len(features)
                self.idx = 0
                self.continuous = continuous

                obs_dim = len(features[0]) if features else 4
                self.observation_space = Box(
                    low=-1.0, high=1.0,
                    shape=(obs_dim,),
                    dtype=np.float32,
                )
                # 0=improve, 1=maintain, 2=escalate. Continuous algos (SAC/TD3)
                # get a Box(3) soft-weight action; step() takes the argmax so
                # the reward semantics match the discrete case.
                self.action_space = (
                    Box(low=0.0, high=1.0, shape=(3,), dtype=np.float32)
                    if continuous else Discrete(3)
                )

                # Infer expected action from feature vector
                # First feature is severity ordinal (0.0=info, 0.33=warning, 0.67=error, 1.0=critical)
                self._expected_actions = []
                for feat in features:
                    sev = feat[0] if feat else 0.0
                    if sev <= 0.33:  # info/warning -> maintain
                        self._expected_actions.append(1)  # maintain
                    elif sev <= 0.67:  # error -> escalate
                        self._expected_actions.append(2)  # escalate
                    else:  # critical -> escalate
                        self._expected_actions.append(2)  # escalate

            def reset(self, *, seed=None, options=None):
                super().reset(seed=seed)
                self.idx = np.random.randint(0, self.n) if self.n > 0 else 0
                obs = np.array(self.features[self.idx], dtype=np.float32)
                return obs, {}

            def step(self, action):
                # Reward: +1 for correct action, -0.5 for incorrect
                expected = self._expected_actions[self.idx]
                if self.continuous:
                    action = int(np.argmax(np.asarray(action, dtype=np.float32)))
                reward = 1.0 if action == expected else -0.5
                self.idx = (self.idx + 1) % self.n
                obs = np.array(self.features[self.idx], dtype=np.float32)
                done = self.idx == 0  # Episode ends when we cycle through all
                return obs, reward, done, False, {}

        return _MemoryDatasetEnv(feature_vectors, continuous=continuous)

    def _create_default_env(self, continuous: bool = False) -> '_GymModuleT.Env':
        """Create a default simple environment when no data is available."""
        import gymnasium as gym
        from gymnasium.spaces import Box, Discrete

        class _DefaultEnv(gym.Env):
            """Simple default environment with 4-dim observation space."""

            def __init__(self, continuous: bool = False):
                super().__init__()
                self.continuous = continuous
                self.observation_space = Box(
                    low=-1.0, high=1.0, shape=(4,), dtype=np.float32
                )
                self.action_space = (
                    Box(low=0.0, high=1.0, shape=(3,), dtype=np.float32)
                    if continuous else Discrete(2)
                )
                self.t = 0

            def reset(self, *, seed=None, options=None):
                super().reset(seed=seed)
                self.t = 0
                return np.zeros(4, dtype=np.float32), {}

            def step(self, action):
                self.t += 1
                obs = np.random.randn(4).astype(np.float32) * 0.1
                if self.continuous:
                    action = int(np.argmax(np.asarray(action, dtype=np.float32)))
                reward = 1.0 if action == 0 else -0.1
                done = self.t >= 200
                return obs, reward, done, False, {}

        return _DefaultEnv(continuous=continuous)

    # -- Lazy helpers --------------------------------------------------------

    def _get_pipeline(self) -> '(_DataPipelineT | None)':
        """Lazy DataPipelineWorm for memory access."""
        if (self._pipeline is None and HAS_PIPELINE
                and DataPipelineWorm is not None):
            try:
                self._pipeline = DataPipelineWorm()
            except Exception as exc:
                note_failure("sb3_agent:L1012", exc)
                self._pipeline = False  # pyright: ignore[reportAttributeAccessIssue]  Sentinel: attempted-and-failed, no retry
        return self._pipeline if self._pipeline else None

    def _get_versioner(self) -> '(_ModelVersionT | None)':
        """Lazy ModelVersioner for version tracking."""
        if (self._versioner is None and HAS_VERSIONER
                and ModelVersioner is not None):
            try:
                self._versioner = ModelVersioner()
            except Exception as exc:
                note_failure("sb3_agent:L1021", exc)
                self._versioner = False  # pyright: ignore[reportAttributeAccessIssue]  Sentinel: attempted-and-failed, no retry
        return self._versioner if self._versioner else None

    # -- trainers_v2 delegation --------------------------------------------

    def get_trainer(self):
        """Build the trainers_v2 SB3TrainerV2 bound to this agent's worm_id.

        Returns None when trainers_v2 is unavailable (the direct-SB3
        train() path above remains the fallback).
        """
        if not HAS_TRAINERS_V2 or create_trainer is None:
            return None
        return create_trainer(self.algo_name.lower(), worm_id=self.worm_id)

    # -- FreeThreadedAgent._run override -------------------------------------

    def _run(self, task: dict[str, Any], pool: Any = None) -> Any:
        """
        Override FreeThreadedAgent._run() to handle SB3 agent tasks.

        Supported task payloads:
        - {"action": "train", "source": "...", "total_timesteps": 1000}
        - {"action": "predict", "observation": [...]}
        - {"action": "save", "path": "models/model"}
        - {"action": "load", "path": "models/model"}
        - {"action": "get_training_history"}
        """
        action = task.get("action", "predict")
        payload = task.get("payload", task)

        if action == "train":
            return self.train(**payload)
        elif action == "predict":
            obs = np.array(payload.get("observation", payload.get("data", [])))
            action_out, state = self.predict(obs)
            return {"action": action_out.tolist() if hasattr(action_out, 'tolist') else action_out}
        elif action == "save":
            path = payload.get("path", "models/sb3_model")
            return {"saved": self.save(path)}
        elif action == "load":
            path = payload.get("path", "models/sb3_model")
            return {"loaded": self.load(path)}
        elif action == "get_training_history":
            return self.get_training_history()
        else:
            return {"error": f"Unknown action: {action}"}

    def __repr__(self) -> str:
        trained = "trained" if self.is_trained else "untrained"
        return (f"SB3Agent(id={self.agent_id}, worm={self.worm_id}, algo={self.algo_name}, "
                f"policy={self.policy}, {trained}, "
                f"steps={self._total_timesteps_trained})")


# -- Self-test -----------------------------------------------------------------

if __name__ == '__main__':
    print("=" * 60)
    print("  SB3Agent - Self-test")
    print("=" * 60)
    print(f"  SB3 available: {HAS_SB3}")
    print(f"  Gymnasium available: {HAS_GYM}")
    print(f"  Torch available: {HAS_TORCH}")
    print(f"  Pipeline available: {HAS_PIPELINE}")
    print(f"  Free-threaded: {IS_FREE_THREADED}")

    if not HAS_SB3:
        print("\n  [SKIP] SB3 not installed — install with: pip install stable-baselines3")
    else:
        # Test 1: Create SB3Agent
        agent = SB3Agent(algo="PPO")
        print(f"\n  [1] Agent created: {agent}")
        print(f"      Device: {agent.device}")

        # Test 2: Train from memory
        result = agent.train(source="SystemZ", total_timesteps=500, register_model=False)
        print("\n  [2] Training result:")
        for k, v in result.items():
            print(f"      {k}: {v}")

        # Test 3: Predict
        if agent.is_trained:
            obs = np.zeros(4, dtype=np.float32)
            action, state = agent.predict(obs)
            print(f"\n  [3] Prediction: action={action}")

        # Test 4: Training history
        history = agent.get_training_history()
        print(f"\n  [4] Training history: {len(history)} entries")

    print("\n" + "=" * 60)
