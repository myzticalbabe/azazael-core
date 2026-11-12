# coding: utf-8
"""
SPECULATIVE_FORK_WORM — SPORK Pattern Speculative Forking

Implements the SPORK (Speculative Forking) pattern from PyAgent:
- Parallel speculative execution of multiple inference branches
- Early termination of losing branches
- Result merging with confidence scoring
- Integrates with synapse mesh for neural routing

Category: synapse
Priority: 7
Cooldown: 10.0s
"""

VERSION = "1.0.0"

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Tuple
from datetime import datetime
from enum import Enum
from collections import Counter
import threading

#: Swallows recorded when the failure ledger is unavailable
try:
    from CORE.failure_ledger import note_failure
except ImportError:
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass

# ── Worm Registry ─────────────────────────────────────────────────────
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("SPECULATIVE_FORK_WORM:registry", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


__all__ = ["SpeculativeForkWorm", "ForkConfig", "ForkResult", "BranchResult", "VERSION"]


class ForkStrategy(Enum):
    """Speculative forking strategies."""
    DIVERSE_TEMPERATURE = "diverse_temperature"      # Same prompt, different temps
    DIVERSE_PROMPTS = "diverse_prompts"              # Different prompt variations
    DIVERSE_MODELS = "diverse_models"                # Different models
    DIVERSE_AGENTS = "diverse_agents"                # Different agent configurations
    ENSEMBLE = "ensemble"                            # All of the above


@dataclass
class ForkConfig:
    """Configuration for speculative forking."""
    strategy: ForkStrategy = ForkStrategy.DIVERSE_TEMPERATURE
    num_branches: int = 3
    max_parallel: int = 3
    timeout_seconds: float = 30.0
    early_termination: bool = True
    termination_threshold: float = 0.3  # Confidence diff to terminate
    merge_strategy: str = "weighted_vote"  # weighted_vote, best_single, consensus
    temperature_range: Tuple[float, float] = (0.1, 0.9)


@dataclass
class BranchResult:
    """Result from a single speculative branch."""
    branch_id: str
    config: Dict[str, Any]
    output: Any
    confidence: float
    tokens_used: int
    latency_ms: float
    error: Optional[str] = None
    completed_at: float = field(default_factory=time.time)


@dataclass
class ForkResult:
    """Aggregated result from speculative forking."""
    fork_id: str
    strategy: ForkStrategy
    branches: List[BranchResult]
    merged_output: Any
    merged_confidence: float
    total_tokens: int
    total_latency_ms: float
    branches_completed: int
    branches_terminated: int
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


@register_worm(category="synapse", priority=7, cooldown_seconds=10.0,
               description="SPORK pattern: speculative forking with early termination and result merging")
class SpeculativeForkWorm:
    """SPORK Speculative Forking Worm.
    
    Executes multiple inference branches in parallel, terminates
    low-confidence branches early, and merges results.
    """
    
    VERSION = "1.0.0"
    
    def __init__(self, config: Optional[ForkConfig] = None):
        self.config = config or ForkConfig()
        self._active_forks: Dict[str, ForkResult] = {}
        self._lock = threading.RLock()
        self._stats = {
            "total_forks": 0, "branches_spawned": 0, "branches_terminated": 0,
            "total_tokens": 0, "avg_speedup": 0.0
        }
    
    async def fork(self, 
                   inference_fn: Callable,
                   base_prompt: str,
                   branch_configs: Optional[List[Dict[str, Any]]] = None,
                   config: Optional[ForkConfig] = None) -> ForkResult:
        """Execute speculative fork.
        
        Args:
            inference_fn: Async function(prompt, **config) -> (output, confidence, tokens)
            base_prompt: Base prompt for all branches
            branch_configs: Optional list of per-branch configs
            config: Override fork config
        
        Returns:
            ForkResult with merged output
        """
        cfg = config or self.config
        fork_id = f"fork_{uuid.uuid4().hex[:8]}"
        
        # Generate branch configs based on strategy
        if branch_configs is None:
            branch_configs = self._generate_branch_configs(base_prompt, cfg)
        
        # Limit to max_branches
        branch_configs = branch_configs[:cfg.num_branches]
        
        # Create fork result container
        fork_result = ForkResult(
            fork_id=fork_id,
            strategy=cfg.strategy,
            branches=[],
            merged_output=None,
            merged_confidence=0.0,
            total_tokens=0,
            total_latency_ms=0.0,
            branches_completed=0,
            branches_terminated=0
        )
        
        with self._lock:
            self._active_forks[fork_id] = fork_result
            self._stats["total_forks"] += 1
            self._stats["branches_spawned"] += len(branch_configs)
        
        # Execute branches with early termination
        semaphore = asyncio.Semaphore(cfg.max_parallel)
        branch_results = await self._execute_branches(
            inference_fn, base_prompt, branch_configs, cfg, semaphore, fork_result
        )
        
        # Merge results
        merged_output, merged_confidence = self._merge_results(branch_results, cfg.merge_strategy)
        
        fork_result.branches = branch_results
        fork_result.merged_output = merged_output
        fork_result.merged_confidence = merged_confidence
        fork_result.total_tokens = sum(b.tokens_used for b in branch_results)
        fork_result.total_latency_ms = sum(b.latency_ms for b in branch_results)
        fork_result.branches_completed = len([b for b in branch_results if not b.error])
        fork_result.branches_terminated = len([b for b in branch_results if b.error == "TERMINATED"])
        fork_result.completed_at = time.time()
        
        with self._lock:
            self._stats["total_tokens"] += fork_result.total_tokens
            self._stats["branches_terminated"] += fork_result.branches_terminated
        
        return fork_result
    
    def _generate_branch_configs(self, base_prompt: str, cfg: ForkConfig) -> List[Dict[str, Any]]:
        """Generate branch configurations based on strategy."""
        configs = []
        
        if cfg.strategy in (ForkStrategy.DIVERSE_TEMPERATURE, ForkStrategy.ENSEMBLE):
            temps = [cfg.temperature_range[0] + 
                     (cfg.temperature_range[1] - cfg.temperature_range[0]) * i / (cfg.num_branches - 1)
                     for i in range(cfg.num_branches)]
            for i, temp in enumerate(temps):
                configs.append({
                    "prompt": base_prompt,
                    "temperature": temp,
                    "branch_type": "temperature",
                    "branch_index": i
                })
        
        if cfg.strategy in (ForkStrategy.DIVERSE_PROMPTS, ForkStrategy.ENSEMBLE):
            prompt_variations = self._generate_prompt_variations(base_prompt, cfg.num_branches)
            for i, prompt in enumerate(prompt_variations):
                configs.append({
                    "prompt": prompt,
                    "temperature": 0.5,
                    "branch_type": "prompt_variation",
                    "branch_index": i
                })
        
        if cfg.strategy in (ForkStrategy.DIVERSE_MODELS, ForkStrategy.ENSEMBLE):
            # Would integrate with InferenceLoadBalancerWorm for model selection
            pass
        
        if cfg.strategy in (ForkStrategy.DIVERSE_AGENTS, ForkStrategy.ENSEMBLE):
            # Would integrate with agent registry for different configs
            pass
        
        return configs[:cfg.num_branches]
    
    def _generate_prompt_variations(self, base_prompt: str, n: int) -> List[str]:
        """Generate prompt variations for diverse prompting."""
        variations = [base_prompt]
        prefixes = [
            "Think step by step: ",
            "Consider multiple perspectives: ",
            "Be concise and precise: ",
            "Explain your reasoning: ",
            "Provide examples: "
        ]
        for i in range(min(n - 1, len(prefixes))):
            variations.append(prefixes[i] + base_prompt)
        return variations[:n]
    
    async def _execute_branches(self,
                                inference_fn: Callable,
                                base_prompt: str,
                                branch_configs: List[Dict[str, Any]],
                                cfg: ForkConfig,
                                semaphore: asyncio.Semaphore,
                                fork_result: ForkResult) -> List[BranchResult]:
        """Execute all branches with early termination."""
        
        async def run_branch(branch_cfg: Dict[str, Any]) -> BranchResult:
            branch_id = f"{fork_result.fork_id}_b{branch_cfg.get('branch_index', 0)}"
            start = time.time()
            
            async with semaphore:
                try:
                    prompt = branch_cfg["prompt"]
                    # Call inference function with branch config
                    output, confidence, tokens = await inference_fn(
                        prompt, 
                        temperature=branch_cfg.get("temperature", 0.5),
                        **{k: v for k, v in branch_cfg.items() if k not in ["prompt", "temperature", "branch_type", "branch_index"]}
                    )
                    
                    return BranchResult(
                        branch_id=branch_id,
                        config=branch_cfg,
                        output=output,
                        confidence=confidence,
                        tokens_used=tokens,
                        latency_ms=(time.time() - start) * 1000
                    )
                except Exception as e:
                    return BranchResult(
                        branch_id=branch_id,
                        config=branch_cfg,
                        output=None,
                        confidence=0.0,
                        tokens_used=0,
                        latency_ms=(time.time() - start) * 1000,
                        error=str(e)
                    )
        
        # Start all branches
        tasks = [asyncio.create_task(run_branch(cfg)) for cfg in branch_configs]
        
        # Early termination monitoring
        completed = []
        pending = set(tasks)
        
        while pending:
            done, pending = await asyncio.wait(
                pending, 
                timeout=0.1,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            for task in done:
                result = await task
                completed.append(result)
            
            # Check early termination condition
            if cfg.early_termination and len(completed) >= 2:
                # Sort by confidence
                completed.sort(key=lambda b: b.confidence, reverse=True)
                best_conf = completed[0].confidence
                worst_conf = completed[-1].confidence
                
                if best_conf - worst_conf >= cfg.termination_threshold:
                    # Cancel pending tasks
                    for task in pending:
                        task.cancel()
                    
                    # Mark cancelled as terminated
                    for task in pending:
                        try:
                            await task
                        except asyncio.CancelledError:
                            cancelled_result = BranchResult(
                                branch_id=f"{fork_result.fork_id}_cancelled",
                                config={},
                                output=None,
                                confidence=0.0,
                                tokens_used=0,
                                latency_ms=0.0,
                                error="TERMINATED"
                            )
                            completed.append(cancelled_result)
                    break
        
        # Wait for any remaining
        if pending:
            done, _ = await asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED)
            for task in done:
                try:
                    result = await task
                    completed.append(result)
                except asyncio.CancelledError:
                    # Cancelled during early termination -- recorded, not swallowed.
                    cancelled = self._stats.get("branches_cancelled", 0)
                    self._stats["branches_cancelled"] = cancelled + 1
        
        return completed
    
    def _merge_results(self, branches: List[BranchResult], strategy: str) -> Tuple[Any, float]:
        """Merge branch results based on strategy."""
        valid_branches = [b for b in branches if not b.error and b.output is not None]
        
        if not valid_branches:
            return None, 0.0
        
        if strategy == "best_single":
            best = max(valid_branches, key=lambda b: b.confidence)
            return best.output, best.confidence
        
        elif strategy == "weighted_vote":
            # Weight by confidence
            total_weight = sum(b.confidence for b in valid_branches)
            if total_weight == 0:
                return valid_branches[0].output, 0.0
            
            # For text outputs, return highest confidence
            # For structured outputs, could do weighted merge
            best = max(valid_branches, key=lambda b: b.confidence)
            weighted_conf = sum(b.confidence ** 2 for b in valid_branches) / total_weight
            return best.output, weighted_conf
        
        elif strategy == "consensus":
            # Simple consensus: most common output
            outputs = [str(b.output) for b in valid_branches]
            counts = Counter(outputs)
            most_common, count = counts.most_common(1)[0]
            consensus_conf = count / len(valid_branches)
            # Find branch with this output
            for b in valid_branches:
                if str(b.output) == most_common:
                    return b.output, consensus_conf
            return valid_branches[0].output, consensus_conf
        
        return valid_branches[0].output, valid_branches[0].confidence
    
    def get_stats(self) -> Dict[str, Any]:
        """Get forking statistics."""
        with self._lock:
            stats = dict(self._stats)
            if stats["total_forks"] > 0:
                stats["avg_branches_per_fork"] = stats["branches_spawned"] / stats["total_forks"]
                stats["termination_rate"] = stats["branches_terminated"] / max(1, stats["branches_spawned"])
            return stats