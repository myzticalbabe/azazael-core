#!/usr/bin/env python3
# coding: utf-8
"""
langgraph_integration.py — LangGraph integration for Azazael CORE.

Wraps the worm registry with LangGraph for agentic orchestration.
Enables multi-agent workflows, state management, and tool use.

WHY THIS EXISTS
---------------
The worm registry currently uses imperative spawning. LangGraph provides:
- Declarative workflow definition
- State management with checkpoints
- Tool calling with validation
- Human-in-the-loop support
- Streaming and async execution
- Observability via LangSmith

USAGE
-----
    from CORE.langgraph_integration import LangGraphWormRegistry
    
    registry = LangGraphWormRegistry()
    
    # Run a workflow
    result = registry.run_workflow(
        "spawn_and_execute",
        input={"task": "analyze codebase", "worm_type": "spine"}
    )
    
    # Stream execution
    for chunk in registry.stream_workflow("analyze", {"query": "find bugs"}):
        print(chunk)
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
# TypedDict was used at line 74 (`class WormState(TypedDict)`) and never
# imported: the module raised NameError on import while every reference to it
# stayed lazily guarded, so nothing failed until a test actually imported it.
from typing import (Any, Callable, Dict, Iterator, List, Optional, Literal, Union,
                    TYPE_CHECKING, TypedDict)
from dataclasses import dataclass, field, asdict
from enum import Enum

# LangGraph imports (optional).
#
# Bound for the type checker under TYPE_CHECKING and for the runtime only when
# the packages are installed. The `= None` fallback made `StateGraph` a variable
# rather than a class, which invalidated every annotation that named it and made
# each `StateGraph(...)`, `@tool` and `MemorySaver()` a call on None.
if TYPE_CHECKING:  # pragma: no cover - typing only
    from langgraph.graph import END, StateGraph
    from langgraph.prebuilt import ToolNode, tools_condition
    from langgraph.checkpoint.memory import MemorySaver
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
    from langchain_core.tools import BaseTool, tool
    from langchain_openai import ChatOpenAI
    from langchain_anthropic import ChatAnthropic

try:
    from langgraph.graph import StateGraph, END
    from langgraph.prebuilt import ToolNode, tools_condition
    from langgraph.checkpoint.memory import MemorySaver
    from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
    from langchain_core.tools import BaseTool, tool
    from langchain_openai import ChatOpenAI
    from langchain_anthropic import ChatAnthropic
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False

# Local imports
try:
    from CORE.worm_registry import discover_worms, WORM_REGISTRY, register_worm
    from CORE.failure_ledger import note_failure
except ImportError:
    WORM_REGISTRY = {}
    discover_worms = lambda: {}
    register_worm = lambda *a, **k: None
    note_failure = lambda *a, **k: None


class WormState(TypedDict):
    """State for worm workflows."""
    messages: List[BaseMessage]
    worm_type: str
    task: str
    params: Dict[str, Any]
    result: Optional[Any]
    error: Optional[str]
    metadata: Dict[str, Any]


class WorkflowType(str, Enum):
    """Types of workflows supported."""
    SPAWN_AND_EXECUTE = "spawn_and_execute"
    ANALYZE_CODEBASE = "analyze_codebase"
    DEBUG_WORM = "debug_worm"
    EVOLVE_WORM = "evolve_worm"
    MULTI_AGENT = "multi_agent"
    CUSTOM = "custom"


@dataclass
class WormTool:
    """Tool definition for worm operations."""
    name: str
    description: str
    func: Callable[..., Any]
    args_schema: Optional[type] = None


class LangGraphWormRegistry:
    """
    LangGraph-enhanced worm registry.
    
    Provides:
    - Workflow-based worm spawning and execution
    - State management with checkpointing
    - Tool calling for worm operations
    - Multi-agent coordination
    - LangSmith observability
    """
    
    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        llm_model: str = "gpt-4o-mini",
        llm_provider: Literal["openai", "anthropic"] = "openai",
        langsmith_project: Optional[str] = None,
    ):
        if not LANGGRAPH_AVAILABLE:
            raise RuntimeError(
                "LangGraph not installed. Install with: pip install langgraph langchain-openai langchain-anthropic"
            )
        
        self.checkpointer = checkpointer or MemorySaver()
        self.llm_model = llm_model
        self.llm_provider = llm_provider
        self.langsmith_project = langsmith_project or os.environ.get("LANGSMITH_PROJECT", "azazael-core")
        
        # Initialize LLM
        self.llm = self._create_llm()
        
        # Build workflows
        self._workflows: Dict[str, StateGraph] = {}
        self._build_workflows()
        
        # Register tools
        self._tools = self._register_tools()
        self._tool_node = ToolNode(self._tools)
    
    def _create_llm(self):
        """Create LLM instance."""
        if self.llm_provider == "openai":
            return ChatOpenAI(
                model=self.llm_model,
                temperature=0.1,
                max_tokens=4096,
            )
        elif self.llm_provider == "anthropic":
            return ChatAnthropic(
                model=self.llm_model,
                temperature=0.1,
                max_tokens=4096,
            )
        else:
            raise ValueError(f"Unknown provider: {self.llm_provider}")
    
    def _register_tools(self) -> List[BaseTool]:
        """Register worm operations as tools."""
        
        @tool
        def spawn_worm(worm_type: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            """Spawn a worm of the given type with parameters."""
            if worm_type not in WORM_REGISTRY:
                return {"error": f"Unknown worm type: {worm_type}"}

            # WORM_REGISTRY values are descriptor dicts, not classes.
            worm_class = (WORM_REGISTRY.get(worm_type) or {}).get("class")
            if worm_class is None:
                return {"error": f"No class registered for worm type: {worm_type}"}
            worm_id = str(uuid.uuid4())[:8]
            
            try:
                worm_instance = worm_class(**(params or {}))
                # Register in our tracking
                worm_data = {
                    "worm_id": worm_id,
                    "worm_type": worm_type,
                    "params": params or {},
                    "created_at": datetime.utcnow().isoformat(),
                    "status": "spawned",
                }
                return {"success": True, "worm_id": worm_id, "worm": worm_data}
            except Exception as e:
                note_failure(f"spawn_worm:{worm_type}", e)
                return {"error": str(e)}
        
        @tool
        def execute_worm(worm_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
            """Execute a spawned worm with input data."""
            # In real implementation, retrieve worm from registry
            return {"worm_id": worm_id, "status": "executed", "output": {"result": "placeholder"}}
        
        @tool
        def list_worms() -> Dict[str, Any]:
            """List all available worm types."""
            # `info.get("class")`, not the value itself: each registry entry is a
            # descriptor dict, so `cls.__name__` raised AttributeError for every
            # worm the moment the tool was called.
            return {
                "worms": [
                    {"name": name,
                     "class": getattr(info.get("class"), "__name__", name)}
                    for name, info in WORM_REGISTRY.items()
                ]
            }
        
        @tool
        def get_worm_status(worm_id: str) -> Dict[str, Any]:
            """Get status of a specific worm."""
            return {"worm_id": worm_id, "status": "unknown"}
        
        @tool
        def analyze_codebase(query: str, path: str = ".") -> Dict[str, Any]:
            """Analyze codebase using available tools."""
            # In real implementation, use codegraph or AST analysis
            return {"query": query, "path": path, "analysis": "placeholder"}
        
        @tool
        def spawn_agent(agent_type: str, task: str) -> Dict[str, Any]:
            """Spawn a sub-agent for a specific task."""
            agent_id = str(uuid.uuid4())[:8]
            return {
                "agent_id": agent_id,
                "agent_type": agent_type,
                "task": task,
                "status": "spawned",
            }
        
        return [
            spawn_worm,
            execute_worm,
            list_worms,
            get_worm_status,
            analyze_codebase,
            spawn_agent,
        ]
    
    def _build_workflows(self):
        """Build all workflow graphs."""
        
        # 1. Spawn and Execute Workflow
        spawn_execute = StateGraph(WormState)
        spawn_execute.add_node("decide_worm", self._decide_worm_node)
        spawn_execute.add_node("spawn_worm", self._spawn_worm_node)
        spawn_execute.add_node("execute_worm", self._execute_worm_node)
        spawn_execute.add_edge("decide_worm", "spawn_worm")
        spawn_execute.add_edge("spawn_worm", "execute_worm")
        spawn_execute.add_edge("execute_worm", END)
        spawn_execute.set_entry_point("decide_worm")
        self._workflows["spawn_and_execute"] = spawn_execute.compile(
            checkpointer=self.checkpointer,
        )
        
        # 2. Analyze Codebase Workflow
        analyze = StateGraph(WormState)
        analyze.add_node("understand_task", self._understand_task_node)
        analyze.add_node("plan_analysis", self._plan_analysis_node)
        analyze.add_node("execute_analysis", self._execute_analysis_node)
        analyze.add_node("summarize", self._summarize_node)
        analyze.add_edge("understand_task", "plan_analysis")
        analyze.add_edge("plan_analysis", "execute_analysis")
        analyze.add_edge("execute_analysis", "summarize")
        analyze.add_edge("summarize", END)
        analyze.set_entry_point("understand_task")
        self._workflows["analyze_codebase"] = analyze.compile(
            checkpointer=self.checkpointer,
        )
        
        # 3. Debug Worm Workflow
        debug = StateGraph(WormState)
        debug.add_node("inspect_worm", self._inspect_worm_node)
        debug.add_node("identify_issue", self._identify_issue_node)
        debug.add_node("propose_fix", self._propose_fix_node)
        debug.add_edge("inspect_worm", "identify_issue")
        debug.add_edge("identify_issue", "propose_fix")
        debug.add_edge("propose_fix", END)
        debug.set_entry_point("inspect_worm")
        self._workflows["debug_worm"] = debug.compile(
            checkpointer=self.checkpointer,
        )
        
        # 4. Multi-Agent Workflow
        multi_agent = StateGraph(WormState)
        multi_agent.add_node("coordinator", self._coordinator_node)
        multi_agent.add_node("agent_pool", self._agent_pool_node)
        multi_agent.add_node("aggregator", self._aggregator_node)
        multi_agent.add_edge("coordinator", "agent_pool")
        multi_agent.add_edge("agent_pool", "aggregator")
        multi_agent.add_edge("aggregator", END)
        multi_agent.set_entry_point("coordinator")
        self._workflows["multi_agent"] = multi_agent.compile(
            checkpointer=self.checkpointer,
        )
    
    # Workflow nodes
    def _decide_worm_node(self, state: WormState) -> WormState:
        """Decide which worm to spawn based on task."""
        task = state.get("task", "")
        worm_type = state.get("worm_type", "")
        
        if not worm_type:
            # Use LLM to decide
            prompt = f"""Given this task, which worm type is most appropriate?
Task: {task}

Available worms: {list(WORM_REGISTRY.keys())}

Respond with just the worm type name."""
            
            response = self.llm.invoke([HumanMessage(content=prompt)])
            worm_type = response.content.strip()
        
        return {**state, "worm_type": worm_type}
    
    def _spawn_worm_node(self, state: WormState) -> WormState:
        """Spawn the selected worm."""
        worm_type = state.get("worm_type", "")
        params = state.get("params", {})
        
        if worm_type not in WORM_REGISTRY:
            return {**state, "error": f"Unknown worm type: {worm_type}"}
        
        try:
            worm_class = (WORM_REGISTRY.get(worm_type) or {}).get("class")
            if worm_class is None:
                return {**state, "error": f"No class registered for worm type: {worm_type}"}
            worm_id = str(uuid.uuid4())[:8]
            worm_instance = worm_class(**(params or {}))
            
            return {
                **state,
                "result": {
                    "worm_id": worm_id,
                    "worm_type": worm_type,
                    "status": "spawned",
                },
                "metadata": {"worm_id": worm_id, "spawned_at": datetime.utcnow().isoformat()},
            }
        except Exception as e:
            note_failure(f"spawn_worm:{worm_type}", e)
            return {**state, "error": str(e)}
    
    def _execute_worm_node(self, state: WormState) -> WormState:
        """Execute the spawned worm."""
        worm_id = state.get("metadata", {}).get("worm_id", "")
        task = state.get("task", "")
        
        # In real implementation, retrieve and execute worm
        result = {"worm_id": worm_id, "task": task, "output": "executed"}
        
        return {**state, "result": result}
    
    def _understand_task_node(self, state: WormState) -> WormState:
        """Understand the analysis task."""
        task = state.get("task", "")
        return {**state, "metadata": {**state.get("metadata", {}), "understood": task}}
    
    def _plan_analysis_node(self, state: WormState) -> WormState:
        """Plan the analysis approach."""
        # Use LLM to plan
        plan = "Analysis plan placeholder"
        return {**state, "metadata": {**state.get("metadata", {}), "plan": plan}}
    
    def _execute_analysis_node(self, state: WormState) -> WormState:
        """Execute the analysis using tools."""
        # In real implementation, use codegraph, AST, etc.
        return {**state, "result": {"analysis": "completed"}}
    
    def _summarize_node(self, state: WormState) -> WormState:
        """Summarize analysis results."""
        return {**state, "metadata": {**state.get("metadata", {}), "summarized": True}}
    
    def _inspect_worm_node(self, state: WormState) -> WormState:
        """Inspect a failing worm."""
        return {**state, "metadata": {**state.get("metadata", {}), "inspected": True}}
    
    def _identify_issue_node(self, state: WormState) -> WormState:
        """Identify the root cause."""
        return {**state, "metadata": {**state.get("metadata", {}), "issue_identified": True}}
    
    def _propose_fix_node(self, state: WormState) -> WormState:
        """Propose a fix."""
        return {**state, "result": {"fix_proposed": True}}
    
    def _coordinator_node(self, state: WormState) -> WormState:
        """Coordinate multi-agent workflow."""
        task = state.get("task", "")
        # Decompose task into subtasks
        subtasks = [f"subtask_{i}" for i in range(3)]
        return {**state, "metadata": {**state.get("metadata", {}), "subtasks": subtasks}}
    
    def _agent_pool_node(self, state: WormState) -> WormState:
        """Execute agent pool."""
        # Spawn agents for each subtask
        return {**state, "metadata": {**state.get("metadata", {}), "agents_spawned": True}}
    
    def _aggregator_node(self, state: WormState) -> WormState:
        """Aggregate results from agents."""
        return {**state, "result": {"aggregated": True}}
    
    # Public API
    def run_workflow(
        self,
        workflow_name: str,
        input_data: Dict[str, Any],
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run a workflow and return final state."""
        if workflow_name not in self._workflows:
            raise ValueError(f"Unknown workflow: {workflow_name}")
        
        workflow = self._workflows[workflow_name]
        
        initial_state = WormState(
            messages=[HumanMessage(content=input_data.get("task", ""))],
            worm_type=input_data.get("worm_type", ""),
            task=input_data.get("task", ""),
            params=input_data.get("params", {}),
            result=None,
            error=None,
            metadata={},
        )
        
        config = config or {"configurable": {"thread_id": str(uuid.uuid4())}}
        
        final_state = workflow.invoke(initial_state, config=config)
        
        return {
            "result": final_state.get("result"),
            "error": final_state.get("error"),
            "metadata": final_state.get("metadata", {}),
        }
    
    def stream_workflow(
        self,
        workflow_name: str,
        input_data: Dict[str, Any],
        config: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Stream workflow execution.

        A plain generator, not an async one: the body iterates the workflow
        synchronously and yields each chunk. The old annotation promised an ``AsyncGenerator``, so
        every caller that did ``async for`` over it was waiting on a coroutine
        that was never created.
        """
        if workflow_name not in self._workflows:
            raise ValueError(f"Unknown workflow: {workflow_name}")
        
        workflow = self._workflows[workflow_name]
        
        initial_state = WormState(
            messages=[HumanMessage(content=input_data.get("task", ""))],
            worm_type=input_data.get("worm_type", ""),
            task=input_data.get("task", ""),
            params=input_data.get("params", {}),
            result=None,
            error=None,
            metadata={},
        )
        
        config = config or {"configurable": {"thread_id": str(uuid.uuid4())}}
        
        for chunk in workflow.stream(initial_state, config=config):
            yield chunk
    
    def get_workflow_state(self, workflow_name: str, thread_id: str) -> Optional[Dict]:
        """Get current state of a workflow."""
        if workflow_name not in self._workflows:
            return None
        
        workflow = self._workflows[workflow_name]
        config = {"configurable": {"thread_id": thread_id}}
        
        try:
            state = workflow.get_state(config)
            return state.values if state else None
        except Exception as exc:
            note_failure("src/CORE/langgraph_integration.py", exc)
            return None
    
    def list_workflows(self) -> List[str]:
        """List available workflows."""
        return list(self._workflows.keys())


# Convenience function
def get_langgraph_registry() -> LangGraphWormRegistry:
    """Get or create LangGraphWormRegistry singleton."""
    global _LANGGRAPH_REGISTRY
    if '_LANGGRAPH_REGISTRY' not in globals():
        _LANGGRAPH_REGISTRY = LangGraphWormRegistry()
    return _LANGGRAPH_REGISTRY


async def run_worm_workflow(
    workflow: str,
    task: str,
    worm_type: str = "",
    params: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Convenience function to run a worm workflow."""
    registry = get_langgraph_registry()
    return registry.run_workflow(workflow, {
        "task": task,
        "worm_type": worm_type,
        "params": params or {},
    })


if __name__ == "__main__":
    # Test
    if not LANGGRAPH_AVAILABLE:
        print("LangGraph not available")
        sys.exit(1)
    
    registry = LangGraphWormRegistry()
    print(f"Available workflows: {registry.list_workflows()}")
    
    # Test run
    result = registry.run_workflow("spawn_and_execute", {
        "task": "Test worm spawn",
        "worm_type": "spine",
    })
    print(f"Result: {result}")