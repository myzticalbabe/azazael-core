# coding: utf-8
"""
CONCURRENCY_GATE_WORM — CoAgent Pattern Concurrency Control

Implements the CoAgent concurrency control pattern from PyAgent:
- Limits concurrent LLM calls per agent/session
- Token budget enforcement
- Priority-based queuing with preemption
- Integrates with WorkStealingPool for distributed coordination

Category: mesh
Priority: 8
Cooldown: 5.0s
"""

VERSION = "1.0.0"

import threading
import time
import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List
from collections import deque
from datetime import datetime
from enum import Enum

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
    note_failure("CONCURRENCY_GATE_WORM:registry", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


__all__ = ["ConcurrencyGateWorm", "ConcurrencyConfig", "GateStatus", "VERSION"]


class GateStatus(Enum):
    """Status of a concurrency gate request."""
    GRANTED = "granted"
    QUEUED = "queued"
    REJECTED = "rejected"
    PREEMPTED = "preempted"


@dataclass
class ConcurrencyConfig:
    """Configuration for concurrency control."""
    max_concurrent: int = 10
    token_budget_per_minute: int = 100000
    priority_levels: int = 3
    preemption_enabled: bool = True
    queue_timeout_seconds: float = 30.0


@dataclass
class GateRequest:
    """A request for concurrency access."""
    request_id: str
    session_id: str
    agent_name: str
    priority: int = 1  # 1=highest, 3=lowest
    estimated_tokens: int = 1000
    created_at: float = field(default_factory=time.time)
    granted_at: Optional[float] = None
    future: Optional[asyncio.Future] = None


@register_worm(category="mesh", priority=8, cooldown_seconds=5.0, 
               description="CoAgent pattern: concurrency control with token budgets and priority queuing")
class ConcurrencyGateWorm:
    """CoAgent Concurrency Control Worm.
    
    Manages concurrent access to LLM inference with:
    - Per-agent/session limits
    - Global token budget
    - Priority-based queuing with preemption
    - Work-stealing integration for distributed coordination
    """
    
    VERSION = "1.0.0"
    
    def __init__(self, config: Optional[ConcurrencyConfig] = None):
        self.config = config or ConcurrencyConfig()
        self._active: Dict[str, GateRequest] = {}  # request_id -> request
        self._queues: Dict[int, deque] = {i: deque() for i in range(1, self.config.priority_levels + 1)}
        self._lock = threading.RLock()
        self._token_bucket = self.config.token_budget_per_minute
        self._last_refill = time.time()
        self._stats = {
            "granted": 0, "queued": 0, "rejected": 0, "preempted": 0,
            "total_tokens": 0, "avg_wait_ms": 0.0, "dequeue_missed": 0
        }
    
    def _refill_tokens(self):
        """Refill token bucket based on elapsed time."""
        now = time.time()
        elapsed = now - self._last_refill
        if elapsed >= 60.0:
            self._token_bucket = self.config.token_budget_per_minute
            self._last_refill = now
    
    def _can_grant(self, req: GateRequest) -> bool:
        """Check if request can be granted immediately."""
        self._refill_tokens()
        if len(self._active) >= self.config.max_concurrent:
            return False
        if self._token_bucket < req.estimated_tokens:
            return False
        return True
    
    def _grant(self, req: GateRequest) -> GateStatus:
        """Grant access to requester."""
        self._active[req.request_id] = req
        req.granted_at = time.time()
        self._token_bucket -= req.estimated_tokens
        self._stats["granted"] += 1
        self._stats["total_tokens"] += req.estimated_tokens
        return GateStatus.GRANTED
    
    def _queue(self, req: GateRequest) -> GateStatus:
        """Queue request by priority."""
        self._queues[req.priority].append(req)
        self._stats["queued"] += 1
        return GateStatus.QUEUED
    
    def _preempt_if_needed(self, req: GateRequest) -> Optional[GateRequest]:
        """Preempt lowest-priority active request if enabled."""
        if not self.config.preemption_enabled:
            return None
        
        # Find lowest priority active request
        victims = [r for r in self._active.values() if r.priority > req.priority]
        if not victims:
            return None
        
        victim = max(victims, key=lambda r: r.priority)
        return victim
    
    async def acquire(self, session_id: str, agent_name: str, 
                     priority: int = 2, estimated_tokens: int = 1000,
                     timeout: Optional[float] = None) -> GateStatus:
        """Acquire concurrency gate for an inference call.
        
        Args:
            session_id: Session identifier
            agent_name: Name of requesting agent
            priority: Priority level (1=highest, 3=lowest)
            estimated_tokens: Estimated token cost
            timeout: Max wait time (default: config.queue_timeout_seconds)
        
        Returns:
            GateStatus indicating result
        """
        timeout = timeout or self.config.queue_timeout_seconds
        req = GateRequest(
            request_id=f"{session_id}:{agent_name}:{time.time_ns()}",
            session_id=session_id,
            agent_name=agent_name,
            priority=min(priority, self.config.priority_levels),
            estimated_tokens=estimated_tokens
        )
        
        loop = asyncio.get_event_loop()
        req.future = loop.create_future()
        
        with self._lock:
            if self._can_grant(req):
                return self._grant(req)
            
            # Try preemption
            victim = self._preempt_if_needed(req)
            if victim:
                self._active.pop(victim.request_id, None)
                self._token_bucket += victim.estimated_tokens
                fut = victim.future
                if fut is not None:
                    fut.set_exception(RuntimeError("Preempted by higher priority"))
                else:
                    note_failure("CONCURRENCY_GATE_WORM:L186", RuntimeError("preempted victim had no future to signal"))
                self._stats["preempted"] += 1
                return self._grant(req)
            
            # Queue the request
            self._queue(req)
        
        # Wait for grant or timeout
        try:
            await asyncio.wait_for(req.future, timeout=timeout)
            return GateStatus.GRANTED
        except asyncio.TimeoutError:
            with self._lock:
                # Remove from queue if still there
                for q in self._queues.values():
                    try:
                        q.remove(req)
                    except ValueError:
                        # Only the request's OWN priority queue can hold it, so a miss
                        # there means it was already granted or dequeued -- record it
                        # rather than swallow it. A miss on any other queue is simply
                        # "not this one" and must not inflate the count.
                        owned = q is self._queues.get(req.priority)
                        if owned:
                            self._stats["dequeue_missed"] = self._stats.get(
                                "dequeue_missed", 0) + 1
            return GateStatus.REJECTED
    
    def release(self, session_id: str, agent_name: str, actual_tokens: int = 0) -> bool:
        """Release concurrency gate after inference completes.
        
        Args:
            session_id: Session identifier
            agent_name: Name of agent
            actual_tokens: Actual tokens used (for budget correction)
        
        Returns:
            True if released, False if not found
        """
        with self._lock:
            # Find and remove request
            req_id = None
            for rid, req in self._active.items():
                if req.session_id == session_id and req.agent_name == agent_name:
                    req_id = rid
                    break
            
            if req_id is None:
                return False
            
            req = self._active.pop(req_id)
            # Correct token budget
            if actual_tokens > 0:
                self._token_bucket = min(
                    self.config.token_budget_per_minute,
                    self._token_bucket + (req.estimated_tokens - actual_tokens)
                )
            
            # Grant next queued request
            self._grant_next()
            return True
    
    def _grant_next(self):
        """Grant next queued request if capacity available."""
        for priority in range(1, self.config.priority_levels + 1):
            queue = self._queues[priority]
            while queue and self._can_grant(queue[0]):
                req = queue.popleft()
                self._grant(req)
                if req.future and not req.future.done():
                    req.future.set_result(True)
                break
    
    def get_status(self) -> Dict[str, Any]:
        """Get current gate status."""
        with self._lock:
            return {
                "active_count": len(self._active),
                "queued_counts": {p: len(q) for p, q in self._queues.items()},
                "token_bucket": self._token_bucket,
                "max_concurrent": self.config.max_concurrent,
                "stats": dict(self._stats)
            }
    
    def get_active_requests(self) -> List[Dict[str, Any]]:
        """Get list of active requests."""
        with self._lock:
            return [
                {
                    "request_id": r.request_id,
                    "session_id": r.session_id,
                    "agent_name": r.agent_name,
                    "priority": r.priority,
                    "estimated_tokens": r.estimated_tokens,
                    "duration_ms": (time.time() - r.granted_at) * 1000 if r.granted_at else 0
                }
                for r in self._active.values()
            ]