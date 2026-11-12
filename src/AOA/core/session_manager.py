"""AOA Session Manager - Milestone tracking and cross-session state."""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path
from enum import Enum
import json, uuid

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class MilestoneStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Milestone:
    milestone_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    arm_id: str = ""
    action: str = ""
    description: str = ""
    status: MilestoneStatus = MilestoneStatus.PENDING
    artifacts: List[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    session_name: str = ""
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    status: str = "active"
    milestones: List[Milestone] = field(default_factory=list)
    active_arms: List[str] = field(default_factory=list)
    workflow_count: int = 0
    total_duration_ms: float = 0.0
    tags: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "session_name": self.session_name,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "milestones": [m.to_dict() for m in self.milestones],
            "active_arms": self.active_arms,
            "workflow_count": self.workflow_count,
            "total_duration_ms": self.total_duration_ms,
            "tags": self.tags,
            "notes": self.notes,
        }


class SessionManager:
    def __init__(self, storage_dir=None):
        self._sessions: Dict[str, SessionState] = {}
        self._active_session: Optional[SessionState] = None
        self._storage_dir = Path(storage_dir) if storage_dir else Path.home() / ".aoa" / "sessions"

    def create_session(self, name="", tags=None):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        session = SessionState(
            session_name=name or "AOA Session " + ts,
            tags=tags or [],
        )
        self._sessions[session.session_id] = session
        self._active_session = session
        return session

    def record_milestone(self, arm_id, action, description="", status=None, artifacts=None, metadata=None, depends_on=None, duration_ms=0.0):
        if status is None:
            status = MilestoneStatus.COMPLETED
        session = self._active_session
        if session is None:
            session = self.create_session()
        milestone = Milestone(
            arm_id=arm_id, action=action, description=description,
            status=status, artifacts=artifacts or [],
            metadata=metadata or {}, depends_on=depends_on or [],
            duration_ms=duration_ms,
        )
        session.milestones.append(milestone)
        return milestone

    def complete_session(self, status="completed"):
        if self._active_session is None:
            return None
        self._active_session.completed_at = datetime.now().isoformat()
        self._active_session.status = status
        if self._active_session.milestones:
            self._active_session.total_duration_ms = sum(
                m.duration_ms for m in self._active_session.milestones)
        session = self._active_session
        self._active_session = None
        return session

    def get_active_session(self):
        return self._active_session

    def get_session(self, session_id):
        return self._sessions.get(session_id)

    def list_sessions(self, status=None, tag=None):
        results = list(self._sessions.values())
        if status:
            results = [s for s in results if s.status == status]
        if tag:
            results = [s for s in results if tag in s.tags]
        return sorted(results, key=lambda s: s.started_at, reverse=True)

    def get_session_summary(self, session_id=None):
        session = self.get_session(session_id) if session_id else self._active_session
        if session is None:
            return {"error": "No session found"}
        milestones_by_arm = {}
        for m in session.milestones:
            if m.arm_id not in milestones_by_arm:
                milestones_by_arm[m.arm_id] = []
            milestones_by_arm[m.arm_id].append(m.action)
        completed = sum(1 for m in session.milestones
                       if m.status == MilestoneStatus.COMPLETED)
        failed = sum(1 for m in session.milestones
                    if m.status == MilestoneStatus.FAILED)
        return {
            "session_id": session.session_id,
            "session_name": session.session_name,
            "status": session.status,
            "total_milestones": len(session.milestones),
            "completed": completed,
            "failed": failed,
            "arms_involved": list(milestones_by_arm.keys()),
            "milestones_by_arm": milestones_by_arm,
            "duration_ms": session.total_duration_ms,
            "tags": session.tags,
        }

    def save_session(self, session_id):
        session = self._sessions.get(session_id)
        if session is None:
            return None
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        filepath = self._storage_dir / (session_id + ".json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(session.to_dict(), f, indent=2, default=str)
        return filepath

    def load_session(self, session_id):
        filepath = self._storage_dir / (session_id + ".json")
        if not filepath.exists():
            return None
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        session = SessionState(
            session_id=data["session_id"],
            session_name=data["session_name"],
            started_at=data["started_at"],
            completed_at=data.get("completed_at"),
            status=data.get("status", "archived"),
            tags=data.get("tags", []),
            notes=data.get("notes", ""),
        )
        for m in data.get("milestones", []):
            milestone = Milestone(
                milestone_id=m["milestone_id"],
                arm_id=m["arm_id"],
                action=m["action"],
                description=m.get("description", ""),
                status=MilestoneStatus(m.get("status", "completed")),
                artifacts=m.get("artifacts", []),
                metadata=m.get("metadata", {}),
                depends_on=m.get("depends_on", []),
                timestamp=m.get("timestamp", ""),
                duration_ms=m.get("duration_ms", 0),
            )
            session.milestones.append(milestone)
        self._sessions[session_id] = session
        return session

    def get_all_milestones(self, arm_id=None):
        all_milestones = []
        for session in self._sessions.values():
            for m in session.milestones:
                if arm_id is None or m.arm_id == arm_id:
                    all_milestones.append(m)
        return sorted(all_milestones, key=lambda m: m.timestamp)

    def clear(self):
        self._sessions.clear()
        self._active_session = None


_global_session_mgr: Optional[SessionManager] = None


def get_session_manager():
    global _global_session_mgr
    if _global_session_mgr is None:
        _global_session_mgr = SessionManager()
    return _global_session_mgr
