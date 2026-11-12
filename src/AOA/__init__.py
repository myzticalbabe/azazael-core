"""AOA — Armorie-of-Anubis Umbrella Orchestrator.



Master orchestrator for the Quartet Architecture and all neural agent systems.

Registers and coordinates Azazael, Libra, CODEX, EveUnleashed, and support Arms

through a unified event bus, session manager, and cross-arm routing engine.



Quick Start:

    from aoa.core import ArmRegistry, EventBus, SessionManager, CrossArmRouter



    registry = ArmRegistry()

    registry.initialize_all()

    print(registry.get_status_report())

"""



from .core import (

    ArmRegistry, BaseArm, register_arm,

    EventBus, AOEvent, AOEventType, EventPipeline, get_event_bus,

    SessionManager, Milestone, SessionState, get_session_manager,

    CrossArmRouter, WorkflowDAG,

)
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here



__version__ = "1.0.0"

__all__ = [

    "ArmRegistry", "BaseArm", "register_arm",

    "EventBus", "AOEvent", "AOEventType", "EventPipeline", "get_event_bus",

    "SessionManager", "Milestone", "SessionState", "get_session_manager",

    "CrossArmRouter", "WorkflowDAG",

]

