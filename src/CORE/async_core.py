#!/usr/bin/env python3
# coding: utf-8
"""
async_core.py — Async-ready base classes for Azazael CORE.

Provides async base classes and utilities to migrate CORE from synchronous
to asynchronous execution patterns.

WHY THIS EXISTS
---------------
CORE currently has only 8 async functions out of 6,250 (0.13%).
This module provides the foundation for full async migration.

USAGE
-----
    from CORE.async_core import AsyncWorm, AsyncWormRegistry
    
    class MyWorm(AsyncWorm):
        async def execute(self, input_data):
            result = await self.process(input_data)
            return result
    
    registry = AsyncWormRegistry()
    await registry.spawn_async("my_worm", params={})
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import (Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional,
                    Set, TYPE_CHECKING, Type, Union)
from enum import Enum
from contextvars import ContextVar

# Optional dependencies.
#
# Typed under TYPE_CHECKING so `Optional[redis.Redis]` is a real type when the
# package is absent. The old `redis = None` fallback turned the module alias
# into a variable, which made the annotation invalid and every attribute on it
# Optional.
if TYPE_CHECKING:  # pragma: no cover - typing only
    import redis.asyncio as redis

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


# Context variable for current worm execution
current_worm_id: ContextVar[Optional[str]] = ContextVar("current_worm_id", default=None)
current_worm_type: ContextVar[Optional[str]] = ContextVar("current_worm_type", default=None)


class WormStatus(str, Enum):
    """Worm lifecycle status."""
    PENDING = "pending"
    SPAWNING = "spawning"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class WormSpec:
    """Specification for a worm type."""
    worm_type: str
    class_path: str  # Module.ClassName
    default_params: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    tags: List[str] = field(default_factory=list)
    max_concurrent: int = 10
    timeout_seconds: float = 300.0


@dataclass
class WormInstance:
    """Runtime instance of a worm."""
    worm_id: str
    worm_type: str
    spec: WormSpec
    params: Dict[str, Any]
    status: WormStatus = WormStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class AsyncWorm(ABC):
    """
    Abstract base class for asynchronous worms.
    
    All worms should inherit from this class and implement execute_async.
    """
    
    def __init__(self, **params):
        self.params = params
        self.worm_id = str(uuid.uuid4())[:8]
        self.created_at = datetime.utcnow()
        self._started_at: Optional[datetime] = None
        self._completed_at: Optional[datetime] = None
        self._result: Any = None
        self._error: Optional[str] = None
    
    @property
    def worm_type(self) -> str:
        """Return the worm type name."""
        return self.__class__.__name__.lower().replace("worm", "").replace("_", "")
    
    @abstractmethod
    async def execute_async(self, input_data: Dict[str, Any]) -> Any:
        """
        Execute the worm asynchronously.
        
        Args:
            input_data: Input data for the worm
            
        Returns:
            Execution result
        """
        pass
    
    async def __call__(self, input_data: Dict[str, Any]) -> Any:
        """Allow calling worm directly."""
        return await self.execute_async(input_data)
    
    async def initialize(self) -> None:
        """Optional initialization hook."""
        pass
    
    async def cleanup(self) -> None:
        """Optional cleanup hook."""
        pass
    
    def get_status(self) -> Dict[str, Any]:
        """Get worm status."""
        return {
            "worm_id": self.worm_id,
            "worm_type": self.worm_type,
            "created_at": self.created_at.isoformat(),
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "completed_at": self._completed_at.isoformat() if self._completed_at else None,
            "error": self._error,
        }


class AsyncWormRegistry:
    """
    Async-native worm registry with concurrent execution support.
    
    Features:
    - Async worm spawning and execution
    - Concurrency control with semaphores
    - Redis-backed distributed registry (optional)
    - Lifecycle management
    - Metrics and monitoring
    """
    
    def __init__(
        self,
        redis_url: Optional[str] = None,
        max_global_concurrent: int = 100,
        default_timeout: float = 300.0,
    ):
        self._worm_specs: Dict[str, WormSpec] = {}
        self._worm_classes: Dict[str, Type[AsyncWorm]] = {}
        self._instances: Dict[str, WormInstance] = {}
        self._running: Dict[str, asyncio.Task] = {}
        self._semaphores: Dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(10)
        )
        self._global_semaphore = asyncio.Semaphore(max_global_concurrent)
        self._default_timeout = default_timeout
        self._redis: Optional[redis.Redis] = None
        self._redis_url = redis_url
        self._metrics = {
            "spawned": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }
        self._lock = asyncio.Lock()
    
    async def initialize(self) -> None:
        """Initialize the registry (connect to Redis if configured)."""
        if self._redis_url and REDIS_AVAILABLE:
            # Assigned only after the connection answers: the old order stored
            # the client first, so a server that refused the ping left a
            # half-configured `_redis` installed for the next caller.
            client = redis.from_url(self._redis_url)
            await client.ping()
            self._redis = client
    
    async def close(self) -> None:
        """Close connections and cancel running tasks."""
        # Cancel all running tasks
        for task in self._running.values():
            task.cancel()
        
        if self._running:
            await asyncio.gather(*self._running.values(), return_exceptions=True)
        
        if self._redis:
            await self._redis.close()
    
    def register_worm(
        self,
        worm_type: str,
        worm_class: Type[AsyncWorm],
        spec: Optional[WormSpec] = None,
    ) -> None:
        """Register a worm type."""
        if worm_type in self._worm_classes:
            raise ValueError(f"Worm type already registered: {worm_type}")
        
        self._worm_classes[worm_type] = worm_class
        
        if spec is None:
            spec = WormSpec(
                worm_type=worm_type,
                class_path=f"{worm_class.__module__}.{worm_class.__name__}",
            )
        self._worm_specs[worm_type] = spec
        self._semaphores[worm_type] = asyncio.Semaphore(spec.max_concurrent)
    
    def get_spec(self, worm_type: str) -> Optional[WormSpec]:
        """Get worm specification."""
        return self._worm_specs.get(worm_type)
    
    def list_worms(self) -> List[Dict[str, Any]]:
        """List all registered worm types."""
        return [
            {
                "worm_type": wt,
                "class": wc.__name__,
                "description": self._worm_specs[wt].description,
                "tags": self._worm_specs[wt].tags,
                "max_concurrent": self._worm_specs[wt].max_concurrent,
            }
            for wt, wc in self._worm_classes.items()
        ]
    
    @asynccontextmanager
    async def _track_execution(self, instance: WormInstance):
        """Context manager for tracking worm execution."""
        instance.status = WormStatus.RUNNING
        instance.started_at = datetime.utcnow()
        self._metrics["spawned"] += 1
        
        worm_id = instance.worm_id
        token = current_worm_id.set(worm_id)
        token2 = current_worm_type.set(instance.spec.worm_type)
        
        try:
            yield instance
            instance.status = WormStatus.COMPLETED
            instance.completed_at = datetime.utcnow()
            self._metrics["completed"] += 1
        except asyncio.CancelledError:
            instance.status = WormStatus.CANCELLED
            instance.completed_at = datetime.utcnow()
            self._metrics["cancelled"] += 1
            raise
        except Exception as e:
            instance.status = WormStatus.FAILED
            instance.completed_at = datetime.utcnow()
            instance.error = str(e)
            self._metrics["failed"] += 1
            raise
        finally:
            current_worm_id.reset(token)
            current_worm_type.reset(token2)
            if worm_id in self._running:
                del self._running[worm_id]
    
    async def spawn_async(
        self,
        worm_type: str,
        params: Optional[Dict[str, Any]] = None,
        worm_id: Optional[str] = None,
    ) -> WormInstance:
        """Spawn a worm instance asynchronously."""
        if worm_type not in self._worm_classes:
            raise ValueError(f"Unknown worm type: {worm_type}")
        
        worm_id = worm_id or str(uuid.uuid4())[:8]
        spec = self._worm_specs[worm_type]
        worm_class = self._worm_classes[worm_type]
        
        instance = WormInstance(
            worm_id=worm_id,
            worm_type=worm_type,
            spec=spec,
            params=params or {},
        )
        
        self._instances[worm_id] = instance
        return instance
    
    async def execute_async(
        self,
        worm_type: str,
        input_data: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
        worm_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Spawn and execute a worm in one call."""
        instance = await self.spawn_async(worm_type, params, worm_id)
        
        worm_class = self._worm_classes[worm_type]
        worm_instance = worm_class(**(params or {}))
        
        spec = self._worm_specs[worm_type]
        semaphore = self._semaphores[worm_type]
        timeout = timeout or spec.timeout_seconds
        
        async with self._global_semaphore:
            async with semaphore:
                async with self._track_execution(instance) as tracked:
                    try:
                        # Initialize worm
                        await worm_instance.initialize()
                        
                        # Execute with timeout
                        result = await asyncio.wait_for(
                            worm_instance.execute_async(input_data),
                            timeout=timeout,
                        )
                        
                        tracked.result = result
                        # `completed_at` is the dataclass field; `_completed_at`
                        # was never declared, so the timestamp was written to an
                        # attribute nothing reads while the real field stayed
                        # None.
                        tracked.completed_at = datetime.utcnow()
                        self._metrics["completed"] += 1
                        
                        return result
                        
                    except asyncio.TimeoutError:
                        error = f"Worm execution timed out after {timeout}s"
                        tracked.error = error
                        tracked.status = WormStatus.FAILED
                        tracked.completed_at = datetime.utcnow()
                        self._metrics["failed"] += 1
                        raise TimeoutError(error)
    
    async def execute_stream(
        self,
        worm_type: str,
        input_data: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Execute worm with streaming updates."""
        instance = await self.spawn_async(worm_type, params)
        worm_class = self._worm_classes[worm_type]
        worm_instance = worm_class(**(params or {}))
        
        spec = self._worm_specs[worm_type]
        semaphore = self._semaphores[worm_type]
        
        async with self._global_semaphore:
            async with semaphore:
                async with self._track_execution(instance) as tracked:
                    try:
                        await worm_instance.initialize()
                        
                        # Stream execution (worm must support streaming).
                        # `getattr` rather than `hasattr` + a direct call: the
                        # base class does not declare `execute_stream`, so the
                        # checker cannot narrow the attribute from a hasattr
                        # test, and the fallback below is the whole point of the
                        # guard.
                        _stream = getattr(worm_instance, "execute_stream", None)
                        if _stream is not None:
                            async for update in _stream(input_data):
                                yield update
                        else:
                            # Fallback: single execution
                            result = await asyncio.wait_for(
                                worm_instance.execute_async(input_data),
                                timeout=spec.timeout_seconds,
                            )
                            yield {"type": "result", "data": result}
                            
                    except Exception as e:
                        yield {"type": "error", "error": str(e)}
                        raise
    
    def get_instance(self, worm_id: str) -> Optional[WormInstance]:
        """Get worm instance by ID."""
        return self._instances.get(worm_id)
    
    def get_running_tasks(self) -> List[str]:
        """Get list of running task IDs."""
        return list(self._running.keys())
    
    def get_metrics(self) -> Dict[str, int]:
        """Get registry metrics."""
        return self._metrics.copy()
    
    async def cancel_worm(self, worm_id: str) -> bool:
        """Cancel a running worm."""
        if worm_id in self._running:
            self._running[worm_id].cancel()
            return True
        return False
    
    async def cancel_all(self) -> int:
        """Cancel all running worms."""
        count = len(self._running)
        for task in self._running.values():
            task.cancel()
        
        if self._running:
            await asyncio.gather(*self._running.values(), return_exceptions=True)
        
        return count


# Global registry instance
_async_registry: Optional[AsyncWormRegistry] = None


async def get_async_registry() -> AsyncWormRegistry:
    """Get or create global async registry."""
    global _async_registry
    if _async_registry is None:
        _async_registry = AsyncWormRegistry()
        await _async_registry.initialize()
    return _async_registry


# Convenience functions
async def spawn_worm_async(
    worm_type: str,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    """Quick spawn - returns worm_id."""
    registry = await get_async_registry()
    instance = await registry.spawn_async(worm_type, params)
    return instance.worm_id


async def execute_worm_async(
    worm_type: str,
    input_data: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    """Quick execute - returns result."""
    registry = await get_async_registry()
    return await registry.execute_async(worm_type, input_data, params)


# Example worm implementations
class AsyncSpineWorm(AsyncWorm):
    """Async spine registry worm."""
    
    async def execute_async(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        # Simulate async work
        await asyncio.sleep(0.1)
        return {"status": "analyzed", "input": input_data}


class AsyncExpansionWorm(AsyncWorm):
    """Async expansion worm."""
    
    async def execute_async(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        await asyncio.sleep(0.1)
        return {"expanded": True, "branches": 3}


class AsyncExMachinaWorm(AsyncWorm):
    """Async evolutionary machine worm."""
    
    async def execute_async(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        await asyncio.sleep(0.1)
        return {"evolved": True, "generation": 1}


# Registry setup function
def create_default_registry() -> AsyncWormRegistry:
    """Create registry with default worms."""
    registry = AsyncWormRegistry()
    
    # Register default worms
    registry.register_worm("spine", AsyncSpineWorm, WormSpec(
        worm_type="spine",
        class_path="CORE.async_core.AsyncSpineWorm",
        description="Spine registry analysis worm",
    ))
    
    registry.register_worm("expansion", AsyncExpansionWorm, WormSpec(
        worm_type="expansion",
        class_path="CORE.async_core.AsyncExpansionWorm",
        description="Expansion worm for branching analysis",
    ))
    
    registry.register_worm("exmachina", AsyncExMachinaWorm, WormSpec(
        worm_type="exmachina",
        class_path="CORE.async_core.AsyncExMachinaWorm",
        description="Evolutionary machine worm",
    ))
    
    return registry


async def demo():
    """Demo async worm execution."""
    registry = create_default_registry()
    
    # List worms
    print("Registered worms:")
    for w in registry.list_worms():
        print(f"  {w['worm_type']}: {w['description']}")
    
    # Execute a worm
    result = await registry.execute_async("spine", {"query": "test"})
    print(f"Result: {result}")
    
    # Metrics
    print(f"Metrics: {registry.get_metrics()}")


if __name__ == "__main__":
    asyncio.run(demo())