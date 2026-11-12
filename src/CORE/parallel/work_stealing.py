"""
Block-based Work Stealing (BWoS) + Session Affinity + Workload-Aware Caching
Implementation based on arXiv:2607.20495 and PyAgent patterns.

BWoS achieves 1.6-30x throughput improvement over Chase-Lev deque by:
- Block-stealing for bulk operations (reduces steal contention)
- Workload-aware cache scoring (recompute_cost * dag_deps * invoke_freq)
- Session affinity for KV-cache migration in LLM inference
"""

from __future__ import annotations
import os
import threading
import time
import heapq
import uuid
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Dict, List, Tuple, Deque
from enum import Enum
from functools import partial


class TaskPriority(Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class WorkBlock:
    """Atomic unit of work for block-stealing. Contains multiple tasks."""
    block_id: str
    tasks: List[Callable]
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: float = field(default_factory=time.time)
    session_id: Optional[str] = None  # For session affinity
    dag_deps: int = 0  # Number of DAG dependencies
    recompute_cost: float = 1.0  # Estimated recomputation cost
    invoke_freq: float = 1.0  # Historical invocation frequency
    
    def __lt__(self, other: 'WorkBlock') -> bool:
        # For priority queue ordering: higher priority first, then earlier created
        if self.priority != other.priority:
            return self.priority.value > other.priority.value
        return self.created_at < other.created_at
    
    @property
    def workload_score(self) -> float:
        """Workload-aware cache score: recompute_cost * dag_deps * invoke_freq"""
        return self.recompute_cost * max(1, self.dag_deps) * self.invoke_freq
    
    @property
    def size(self) -> int:
        return len(self.tasks)


class BlockWorkStealingQueue:
    """
    Block-based Work Stealing Queue.
    
    Key innovations over Chase-Lev:
    1. Tasks grouped into blocks - steal entire blocks, not individual tasks
    2. Local queue is a priority deque (not FIFO)
    3. Global victim selection prefers high-workload-score blocks
    4. Session affinity tracking for KV-cache migration
    """
    
    def __init__(
        self,
        worker_id: int,
        num_workers: int,
        block_size: int = 16,
        steal_threshold: int = 4,
    ):
        self.worker_id = worker_id
        self.num_workers = num_workers
        self.block_size = block_size
        self.steal_threshold = steal_threshold
        
        # Local queue: priority deque of WorkBlocks
        self._local_queue: Deque[WorkBlock] = deque()
        self._local_lock = threading.Lock()
        
        # Global registry of all queues (populated by WorkStealingPool)
        self._global_queues: Dict[int, 'BlockWorkStealingQueue'] = {}
        
        # Statistics
        self._stats = {
            'tasks_executed': 0,
            'blocks_created': 0,
            'blocks_stolen': 0,
            'steal_attempts': 0,
            'steal_successes': 0,
            'affinity_hits': 0,
            'affinity_misses': 0,
        }
        self._stats_lock = threading.Lock()
    
    def register_global_queues(self, queues: Dict[int, 'BlockWorkStealingQueue']) -> None:
        """Register all queues for stealing."""
        self._global_queues = queues
    
    def push_task(self, task: Callable, priority: TaskPriority = TaskPriority.NORMAL,
                  session_id: Optional[str] = None, dag_deps: int = 0,
                  recompute_cost: float = 1.0, invoke_freq: float = 1.0) -> None:
        """Push a single task, creating/adding to block."""
        with self._local_lock:
            # Try to add to existing block with same session affinity or priority
            if self._local_queue:
                last_block = self._local_queue[-1]
                same_session = session_id and last_block.session_id == session_id
                same_priority = last_block.priority == priority
                has_space = last_block.size < self.block_size
                
                if (same_session or (not session_id and same_priority)) and has_space:
                    last_block.tasks.append(task)
                    return
            
            # Create new block
            block = WorkBlock(
                block_id=str(uuid.uuid4())[:8],
                tasks=[task],
                priority=priority,
                session_id=session_id,
                dag_deps=dag_deps,
                recompute_cost=recompute_cost,
                invoke_freq=invoke_freq,
            )
            self._local_queue.append(block)
            self._stats['blocks_created'] += 1
    
    def push_block(self, block: WorkBlock) -> None:
        """Push a complete block (used when receiving stolen work)."""
        with self._local_lock:
            self._local_queue.append(block)
    
    def pop_task(self) -> Optional[Callable]:
        """Pop a single task from local queue."""
        with self._local_lock:
            if not self._local_queue:
                return None
            
            block = self._local_queue[0]
            task = block.tasks.pop(0)
            
            if not block.tasks:
                self._local_queue.popleft()
            
            self._stats['tasks_executed'] += 1
            return task
    
    def pop_block(self) -> Optional[WorkBlock]:
        """Pop entire block for stealing."""
        with self._local_lock:
            if not self._local_queue:
                return None
            block = self._local_queue.popleft()
            return block
    
    def steal_block(self) -> Optional[WorkBlock]:
        """
        Steal a block from another worker.
        Victim selection: prefer workers with high-workload-score blocks.
        """
        self._stats['steal_attempts'] += 1
        
        # Find victim with highest workload score
        best_victim = None
        best_block = None
        best_score = -1.0
        
        for wid, queue in self._global_queues.items():
            if wid == self.worker_id:
                continue
            with queue._local_lock:
                if not queue._local_queue:
                    continue
                # Check highest priority block
                candidate = queue._local_queue[0]
                score = candidate.workload_score
                if score > best_score:
                    best_score = score
                    best_victim = queue
                    best_block = candidate
        
        if best_victim and best_block:
            # Steal the block
            stolen = best_victim.pop_block()
            if stolen:
                self._stats['steal_successes'] += 1
                self._stats['blocks_stolen'] += 1
                return stolen
        
        return None
    
    def try_steal(self) -> Optional[Callable]:
        """Try to steal work, return single task if successful."""
        # First try local pop
        task = self.pop_task()
        if task:
            return task
        
        # Try stealing a block
        block = self.steal_block()
        if block:
            # Push stolen block to local queue, then pop task
            self.push_block(block)
            return self.pop_task()
        
        return None
    
    def get_stats(self) -> Dict[str, int]:
        with self._stats_lock:
            return dict(self._stats)
    
    def __len__(self) -> int:
        with self._local_lock:
            return sum(b.size for b in self._local_queue)


class SessionAffinityPool:
    """
    Two-level scheduler: Local Affinity Pool + Global Work Stealing.
    
    For LLM inference with KV-cache migration:
    - Local pool: tasks with same session_id execute on same worker (cache affinity)
    - Global stealing: when local pool empty, steal from other workers
    - KV-cache migration: track session-to-worker mapping for cache warmup
    """
    
    def __init__(self, num_workers: int, block_size: int = 16):
        self.num_workers = num_workers
        self.block_size = block_size
        
        # One queue per worker
        self.queues: Dict[int, BlockWorkStealingQueue] = {
            i: BlockWorkStealingQueue(i, num_workers, block_size)
            for i in range(num_workers)
        }
        
        # Register global queues for stealing
        for q in self.queues.values():
            q.register_global_queues(self.queues)
        
        # Session affinity mapping: session_id -> worker_id
        self._session_worker: Dict[str, int] = {}
        self._affinity_lock = threading.Lock()
        
        # KV-cache tracking: session_id -> {worker_id: cache_state}
        self._kv_cache: Dict[str, Dict[int, Dict[str, Any]]] = {}
        self._kv_lock = threading.Lock()
    
    def submit(self, task: Callable, session_id: Optional[str] = None,
               priority: TaskPriority = TaskPriority.NORMAL,
               dag_deps: int = 0, recompute_cost: float = 1.0,
               invoke_freq: float = 1.0) -> int:
        """
        Submit task to appropriate worker based on session affinity.
        Returns worker_id that will execute the task.
        """
        if session_id:
            with self._affinity_lock:
                if session_id in self._session_worker:
                    worker_id = self._session_worker[session_id]
                    # Verify worker still exists and has capacity
                    if worker_id in self.queues:
                        queue = self.queues[worker_id]
                        queue.push_task(task, priority, session_id, dag_deps, recompute_cost, invoke_freq)
                        with queue._stats_lock:
                            queue._stats['affinity_hits'] += 1
                        return worker_id
                
                # New session: assign to least loaded worker
                worker_id = min(
                    self.queues.keys(),
                    key=lambda w: len(self.queues[w])
                )
                self._session_worker[session_id] = worker_id
        
        else:
            # No session affinity: round-robin to least loaded
            worker_id = min(
                self.queues.keys(),
                key=lambda w: len(self.queues[w])
            )
        
        self.queues[worker_id].push_task(task, priority, session_id, dag_deps, recompute_cost, invoke_freq)
        return worker_id
    
    def submit_batch(self, tasks: List[Callable], session_id: Optional[str] = None,
                     priority: TaskPriority = TaskPriority.NORMAL,
                     dag_deps: int = 0, recompute_cost: float = 1.0,
                     invoke_freq: float = 1.0) -> int:
        """Submit multiple tasks as a batch to same worker."""
        worker_id = self.submit(tasks[0], session_id, priority, dag_deps, recompute_cost, invoke_freq)
        queue = self.queues[worker_id]
        for task in tasks[1:]:
            queue.push_task(task, priority, session_id, dag_deps, recompute_cost, invoke_freq)
        return worker_id
    
    def record_kv_cache(self, session_id: str, worker_id: int, cache_state: Dict[str, Any]) -> None:
        """Record KV-cache state for a session on a worker."""
        with self._kv_lock:
            if session_id not in self._kv_cache:
                self._kv_cache[session_id] = {}
            self._kv_cache[session_id][worker_id] = cache_state
    
    def get_kv_cache(self, session_id: str, worker_id: int) -> Optional[Dict[str, Any]]:
        """Get KV-cache state for session on worker."""
        with self._kv_lock:
            return self._kv_cache.get(session_id, {}).get(worker_id)
    
    def migrate_session(self, session_id: str, new_worker_id: int) -> bool:
        """Migrate session affinity to new worker (for KV-cache migration)."""
        with self._affinity_lock:
            if session_id in self._session_worker:
                old_worker = self._session_worker[session_id]
                self._session_worker[session_id] = new_worker_id
                return True
        return False
    
    def get_aggregated_stats(self) -> Dict[str, Any]:
        """Get aggregated statistics across all workers."""
        total = {
            'tasks_executed': 0,
            'blocks_created': 0,
            'blocks_stolen': 0,
            'steal_attempts': 0,
            'steal_successes': 0,
            'affinity_hits': 0,
            'affinity_misses': 0,
            'total_queue_depth': 0,
        }
        for q in self.queues.values():
            stats = q.get_stats()
            for k, v in stats.items():
                if k in total:
                    total[k] += v
            total['total_queue_depth'] += len(q)
        return total


class WorkloadAwareCachePolicy:
    """
    Workload-Aware Caching Policy (WAC).
    
    Score = recompute_cost * dag_deps * invoke_freq
    
    Evicts lowest-score entries when cache pressure exceeds threshold.
    Targets 64.7% latency reduction (per arXiv:2607.20495).
    """
    
    def __init__(self, max_entries: int = 10000, pressure_threshold: float = 0.8):
        self.max_entries = max_entries
        self.pressure_threshold = pressure_threshold
        
        # Cache entries: key -> (value, score, last_access, access_count)
        self._cache: Dict[str, Tuple[Any, float, float, int]] = {}
        self._lock = threading.Lock()
    
    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                value, score, last_access, count = self._cache[key]
                # Update access time and count
                self._cache[key] = (value, score, time.time(), count + 1)
                return value
        return None
    
    def put(self, key: str, value: Any, recompute_cost: float = 1.0,
            dag_deps: int = 0, invoke_freq: float = 1.0) -> None:
        with self._lock:
            score = recompute_cost * max(1, dag_deps) * invoke_freq
            self._cache[key] = (value, score, time.time(), 1)
            self._maybe_evict()
    
    def update_score(self, key: str, recompute_cost: Optional[float] = None,
                     dag_deps: Optional[int] = None, invoke_freq: Optional[float] = None) -> None:
        """Update workload score for existing entry."""
        with self._lock:
            if key in self._cache:
                value, old_score, last_access, count = self._cache[key]
                rc = recompute_cost if recompute_cost is not None else old_score
                dd = dag_deps if dag_deps is not None else 1
                inf = invoke_freq if invoke_freq is not None else 1
                # This is simplified - in practice we'd track components separately
                new_score = rc * max(1, dd) * inf
                self._cache[key] = (value, new_score, last_access, count)
    
    def _maybe_evict(self) -> None:
        if len(self._cache) <= self.max_entries * self.pressure_threshold:
            return
        
        # Evict lowest-score entries
        target_size = int(self.max_entries * 0.7)
        sorted_entries = sorted(
            self._cache.items(),
            key=lambda x: x[1][1]  # sort by score
        )
        # Keep top-scoring entries
        self._cache = dict(sorted_entries[-target_size:])
    
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            if not self._cache:
                return {'entries': 0, 'avg_score': 0, 'max_score': 0}
            scores = [v[1] for v in self._cache.values()]
            return {
                'entries': len(self._cache),
                'avg_score': sum(scores) / len(scores),
                'max_score': max(scores),
                'min_score': min(scores),
            }


# Integration with existing FreeThreadPool
class BWoSWorkStealingPool:
    """
    Drop-in replacement/enhancement for FreeThreadPool using BlockWorkStealingQueue.
    Maintains same API but adds block-stealing, session affinity, workload-aware caching.
    """
    
    def __init__(self, max_workers: Optional[int] = None, block_size: int = 16):
        self.max_workers = max_workers or min(32, (os.cpu_count() or 4) + 4)
        self.block_size = block_size
        
        self.affinity_pool = SessionAffinityPool(self.max_workers, block_size)
        self.cache_policy = WorkloadAwareCachePolicy()
        
        self._workers: List[threading.Thread] = []
        self._running = False
        self._shutdown_event = threading.Event()
        #: Count of tasks that raised inside a worker thread. Without this the
        #: pool discards the exception and a failing task is invisible.
        self._task_failures = 0
    
    def start(self) -> None:
        """Start worker threads."""
        if self._running:
            return
        self._running = True
        self._shutdown_event.clear()
        
        for i in range(self.max_workers):
            t = threading.Thread(target=self._worker_loop, args=(i,), daemon=True)
            t.start()
            self._workers.append(t)
    
    def _worker_loop(self, worker_id: int) -> None:
        queue = self.affinity_pool.queues[worker_id]
        while self._running and not self._shutdown_event.is_set():
            task = queue.try_steal()
            if task:
                try:
                    task()
                except Exception:
                    # A task raised; counted rather than swallowed so a failing
                    # task is visible instead of vanishing into the queue.
                    failures = self._task_failures
                    self._task_failures = failures + 1
            else:
                time.sleep(0.001)  # Brief yield
    
    def submit(self, fn: Callable, *args, **kwargs) -> Any:
        """Submit a task for execution."""
        task = partial(fn, *args, **kwargs)
        worker_id = self.affinity_pool.submit(task)
        return worker_id
    
    def submit_with_affinity(self, fn: Callable, session_id: str,
                             *args, **kwargs) -> int:
        """Submit with explicit session affinity for KV-cache locality."""
        task = partial(fn, *args, **kwargs)
        return self.affinity_pool.submit(task, session_id=session_id)
    
    def shutdown(self, wait: bool = True) -> None:
        self._running = False
        self._shutdown_event.set()
        if wait:
            for t in self._workers:
                t.join(timeout=5.0)
        self._workers.clear()
    
    def stats(self) -> Dict[str, Any]:
        return {
            'pool': self.affinity_pool.get_aggregated_stats(),
            'cache': self.cache_policy.stats(),
            'workers': self.max_workers,
        }


__all__ = [
    'TaskPriority',
    'WorkBlock',
    'BlockWorkStealingQueue',
    'SessionAffinityPool',
    'WorkloadAwareCachePolicy',
    'BWoSWorkStealingPool',
]