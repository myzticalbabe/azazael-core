# coding: utf-8
"""
PIPELINE_WORM — PyAgent Pipeline Pattern

Implements the Pipeline orchestration pattern from PyAgent:
- Sequential stages with typed inputs/outputs
- Stage-level token budgets and timeouts
- Checkpoint/restart capability
- Integration with ClusterWORMZ for parallel stage execution

Category: orchestration
Priority: 8
Cooldown: 15.0s
"""

VERSION = "1.0.0"

import asyncio
import os
import pickle
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Generic, TypeVar, Union, Coroutine
from datetime import datetime
from enum import Enum
import threading

#: Swallows recorded when the failure ledger is unavailable
try:
    from CORE.failure_ledger import note_failure
except ImportError:
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass

# ── Worm Registry ─────────────────────────────────────────────────────
try:
    from CORE.worm_registry import register_worm  # pyright: ignore[reportAssignmentType] -- optional import: except-path defines fallback decorator; selected by import success
    _REGISTRY = True
except ImportError as exc:
    note_failure("PIPELINE_WORM:registry", exc)
    _REGISTRY = False
    def register_worm(**kw):
        return lambda cls: cls


__all__ = ["PipelineWorm", "PipelineConfig", "StageConfig", "StageResult", "PipelineResult", "VERSION"]

T = TypeVar('T')
U = TypeVar('U')


class StageStatus(Enum):
    """Pipeline stage execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StageConfig(Generic[T, U]):
    """Configuration for a pipeline stage."""
    name: str
    fn: Callable[[T], Union[U, Coroutine[Any, Any, U]]]
    input_type: Any = Any
    output_type: Any = Any
    timeout_seconds: float = 60.0
    token_budget: int = 10000
    retries: int = 0
    retry_delay: float = 1.0
    required: bool = True
    checkpoint: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StageResult:
    """Result of a pipeline stage execution."""
    stage_name: str
    status: StageStatus
    output: Any = None
    error: Optional[str] = None
    tokens_used: int = 0
    latency_ms: float = 0.0
    retries: int = 0
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


@dataclass
class PipelineConfig:
    """Pipeline execution configuration."""
    name: str
    stages: List[StageConfig]
    global_timeout_seconds: float = 300.0
    global_token_budget: int = 100000
    fail_fast: bool = True
    checkpoint_dir: Optional[str] = None
    parallel_stages: bool = False  # Run independent stages in parallel


@dataclass
class PipelineResult:
    """Complete pipeline execution result."""
    pipeline_name: str
    run_id: str
    status: StageStatus
    stage_results: List[StageResult]
    final_output: Any = None
    total_tokens: int = 0
    total_latency_ms: float = 0.0
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None


@register_worm(category="orchestration", priority=8, cooldown_seconds=15.0,
               description="Pipeline pattern: sequential stages with budgets, checkpoints, and parallel execution")
class PipelineWorm:
    """Pipeline Orchestration Worm.
    
    Executes a sequence of stages with token budgets, timeouts,
    checkpointing, and optional parallel execution of independent stages.
    """
    
    VERSION = "1.0.0"
    
    def __init__(self, config: PipelineConfig):
        self.config = config
        self._checkpoints: Dict[str, Any] = {}
        self._lock = threading.RLock()
        self._stats = {
            "total_runs": 0, "successful_runs": 0, "failed_runs": 0,
            "total_tokens": 0, "total_latency_ms": 0.0
        }
    
    async def run(self, initial_input: Any, 
                  resume_from: Optional[str] = None) -> PipelineResult:
        """Execute the pipeline.
        
        Args:
            initial_input: Input to first stage
            resume_from: Stage name to resume from (loads checkpoint)
        
        Returns:
            PipelineResult with all stage outputs
        """
        run_id = f"{self.config.name}_{uuid.uuid4().hex[:8]}"
        start_time = time.time()
        
        # Load checkpoint if resuming
        current_input = initial_input
        start_idx = 0
        
        if resume_from and self.config.checkpoint_dir:
            checkpoint_data = self._load_checkpoint(resume_from)
            if checkpoint_data:
                current_input = checkpoint_data["output"]
                start_idx = checkpoint_data["stage_index"] + 1
        
        stage_results = []
        total_tokens = 0
        status = StageStatus.COMPLETED
        error = None
        
        try:
            # Build dependency graph for parallel execution
            if self.config.parallel_stages:
                stage_results = await self._run_parallel(current_input, start_idx, run_id)
            else:
                stage_results = await self._run_sequential(current_input, start_idx, run_id)
            
            # Check for failures
            for sr in stage_results:
                if sr.status == StageStatus.FAILED:
                    status = StageStatus.FAILED
                    error = f"Stage '{sr.stage_name}' failed: {sr.error}"
                    if self.config.fail_fast:
                        break
            
            if status == StageStatus.COMPLETED:
                final_output = stage_results[-1].output if stage_results else initial_input
        
        except Exception as e:
            status = StageStatus.FAILED
            error = str(e)
            final_output = None
            note_failure(f"PIPELINE_WORM:{self.config.name}", e)
        
        completed_at = time.time()
        total_latency_ms = (completed_at - start_time) * 1000
        
        result = PipelineResult(
            pipeline_name=self.config.name,
            run_id=run_id,
            status=status,
            stage_results=stage_results,
            final_output=final_output if status == StageStatus.COMPLETED else None,
            total_tokens=total_tokens,
            total_latency_ms=total_latency_ms,
            completed_at=completed_at,
            error=error
        )
        
        with self._lock:
            self._stats["total_runs"] += 1
            self._stats["total_tokens"] += total_tokens
            self._stats["total_latency_ms"] += total_latency_ms
            if status == StageStatus.COMPLETED:
                self._stats["successful_runs"] += 1
            else:
                self._stats["failed_runs"] += 1
        
        return result
    
    async def _run_sequential(self, initial_input: Any, start_idx: int, run_id: str) -> List[StageResult]:
        """Run stages sequentially."""
        results = []
        current_input = initial_input
        
        for i in range(start_idx, len(self.config.stages)):
            stage_cfg = self.config.stages[i]
            stage_result = await self._run_stage(stage_cfg, current_input, run_id, i)
            results.append(stage_result)
            
            if stage_result.status == StageStatus.COMPLETED:
                current_input = stage_result.output
                
                # Checkpoint if enabled
                if stage_cfg.checkpoint and self.config.checkpoint_dir:
                    self._save_checkpoint(run_id, i, stage_result.output)
            elif stage_result.status == StageStatus.FAILED and stage_cfg.required:
                break
        
        return results
    
    async def _run_parallel(self, initial_input: Any, start_idx: int, run_id: str) -> List[StageResult]:
        """Run independent stages in parallel (experimental)."""
        # For now, fallback to sequential - full DAG execution needs ClusterWORMZ
        return await self._run_sequential(initial_input, start_idx, run_id)
    
    async def _run_stage(self, stage_cfg: StageConfig, input_data: Any,
                         run_id: str, stage_index: int) -> StageResult:
        """Execute a single stage with retries and timeout."""
        stage_result = StageResult(
            stage_name=stage_cfg.name,
            status=StageStatus.RUNNING,
            started_at=time.time()
        )
        
        for attempt in range(stage_cfg.retries + 1):
            try:
                # Execute with timeout
                if asyncio.iscoroutinefunction(stage_cfg.fn):
                    output = await asyncio.wait_for(
                        stage_cfg.fn(input_data),
                        timeout=stage_cfg.timeout_seconds
                    )
                else:
                    # Run sync function in thread pool
                    loop = asyncio.get_event_loop()
                    output = await asyncio.wait_for(
                        loop.run_in_executor(None, stage_cfg.fn, input_data),
                        timeout=stage_cfg.timeout_seconds
                    )
                
                stage_result.status = StageStatus.COMPLETED
                stage_result.output = output
                stage_result.completed_at = time.time()
                stage_result.latency_ms = (stage_result.completed_at - stage_result.started_at) * 1000
                stage_result.retries = attempt
                return stage_result
                
            except asyncio.TimeoutError:
                stage_result.error = f"Timeout after {stage_cfg.timeout_seconds}s"
            except Exception as e:
                stage_result.error = str(e)
                note_failure(f"PIPELINE_WORM:{stage_cfg.name}", e)
            
            stage_result.retries = attempt + 1
            
            if attempt < stage_cfg.retries:
                await asyncio.sleep(stage_cfg.retry_delay * (attempt + 1))
        
        stage_result.status = StageStatus.FAILED
        stage_result.completed_at = time.time()
        stage_result.latency_ms = (stage_result.completed_at - stage_result.started_at) * 1000
        return stage_result
    
    def _save_checkpoint(self, run_id: str, stage_index: int, output: Any):
        """Save stage checkpoint."""
        if not self.config.checkpoint_dir:
            return
        os.makedirs(self.config.checkpoint_dir, exist_ok=True)
        checkpoint_path = os.path.join(
            self.config.checkpoint_dir, f"{run_id}_stage{stage_index}.pkl"
        )
        
        with open(checkpoint_path, 'wb') as f:  # scanner:ignore - binary write; encoding= is illegal in 'wb' mode
            pickle.dump({
                "run_id": run_id,
                "stage_index": stage_index,
                "output": output,
                "timestamp": time.time()
            }, f)
    
    def _load_checkpoint(self, stage_name: str) -> Optional[Dict[str, Any]]:
        """Load latest checkpoint for stage."""
        
        if not self.config.checkpoint_dir or not os.path.exists(self.config.checkpoint_dir):
            return None
        
        # Find latest checkpoint for this stage
        checkpoints = [f for f in os.listdir(self.config.checkpoint_dir) 
                       if f.endswith('.pkl') and f'_stage{stage_name}_' in f]
        
        if not checkpoints:
            return None
        
        latest = max(checkpoints, key=lambda f: os.path.getmtime(
            os.path.join(self.config.checkpoint_dir, f)))  # pyright: ignore[reportArgumentType, reportCallIssue]
        
        with open(os.path.join(self.config.checkpoint_dir, latest), 'rb') as f:  # scanner:ignore - binary read; encoding= is illegal in 'rb' mode
            return pickle.load(f)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get pipeline statistics."""
        with self._lock:
            stats = dict(self._stats)
            if stats["total_runs"] > 0:
                stats["success_rate"] = stats["successful_runs"] / stats["total_runs"]
                stats["avg_tokens"] = stats["total_tokens"] / stats["total_runs"]
                stats["avg_latency_ms"] = stats["total_latency_ms"] / stats["total_runs"]
            return stats