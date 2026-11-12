"""AOA Core — Arm Registry, EventBus, Session Manager, Cross-Arm Router."""



from .arm_registry import ArmRegistry, BaseArm, register_arm

from .event_bus import EventBus, AOEvent, AOEventType, EventPipeline, get_event_bus

from .session_manager import SessionManager, Milestone, SessionState, get_session_manager

from .router import CrossArmRouter, WorkflowDAG
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here



__all__ = [

    "ArmRegistry", "BaseArm", "register_arm",

    "EventBus", "AOEvent", "AOEventType", "EventPipeline", "get_event_bus",

    "SessionManager", "Milestone", "SessionState", "get_session_manager",

    "CrossArmRouter", "WorkflowDAG",

]

