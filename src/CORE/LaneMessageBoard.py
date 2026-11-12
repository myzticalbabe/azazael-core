# coding: utf-8
"""
LaneMessageBoard — cross-lane agent message board and push hook.

Builds on MemoryBus (PAMS-backed pub/sub bus) to provide a durable,
lane-addressable message board for the three-lane agent system:
  opencode ↔ kilo ↔ freebuff

Topics:
  lane.<lane>.tasks      — tasks addressed to a specific lane
  lane.<lane>.status     — status updates from a lane
  lane.all.announce      — announcements broadcast to all lanes
  lane.push.<src>-><dst> — push hook: task pushed from src lane to dst lane

Usage:
    board = LaneMessageBoard(lane="opencode")
    row = board.post_task("kilo", "continue", "enggram #1039 — run long test",
                          task_id="1039")
    row = board.push_hook("freebuff", "mcp_config_fix", "string command → argv ARRAY",
                          task_id="mcp-001")
    msgs = board.poll_tasks("kilo")

Both post_task and push_hook return the **durable SQLite row id** of the
delivered task (read back after writing), not the MemoryBus message id.
The bus id is a per-process counter on the fallback path, so it is not
citable across sessions; the row id is what pollers cite as Board-<id>.

All messages are durably stored in PAMS (with SQLite fallback), so they
survive session restarts — unlike message-history.json which is ephemeral chat log.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from CORE.MemoryBus import MemoryBus, BusMessage, _MEMORYBUS_DB_PATH
from CORE.failure_ledger import note_failure
from tools.defaults_profile import read_default

_log = logging.getLogger(__name__)


@dataclass
class LaneTask:
    """A task addressed to a specific lane."""
    lane: str
    task_id: Optional[str] = None
    action: str = ""
    detail: str = ""
    source_lane: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: str = "pending"  # pending | in_progress | completed | blocked
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lane": self.lane,
            "task_id": self.task_id,
            "action": self.action,
            "detail": self.detail,
            "source_lane": self.source_lane,
            "created_at": self.created_at,
            "status": self.status,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LaneTask":
        return cls(
            lane=d.get("lane", ""),
            task_id=d.get("task_id"),
            action=d.get("action", ""),
            detail=d.get("detail", ""),
            source_lane=d.get("source_lane", ""),
            created_at=d.get("created_at", ""),
            status=d.get("status", "pending"),
            metadata=d.get("metadata", {}),
        )


def _task_sig(payload: Dict[str, Any]):
    """Identity of one piece of work: its id AND what it says.

    The duplicate check used `task_id` alone, so a deliberately revised task
    (same id, new detail) was retired as a duplicate of the row it superseded
    and the revision was silently lost -- measured 2026-10-31 on Board-46850.

    Values are normalised to a comparable STRING rather than handed to SQLite
    as bind parameters: `detail` is a dict on legacy rows, and binding a dict
    raises. The first version of this fix did exactly that and broke
    `test_a_dict_detail_does_not_crash_the_claim`.
    """
    tid = payload.get("task_id")
    if not tid:
        return None

    def norm(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        try:
            return json.dumps(v, sort_keys=True, default=str)
        except Exception as exc:
            note_failure("_task_sig.norm", exc)
            return str(v)

    return (tid, norm(payload.get("action")), norm(payload.get("detail")))


class LaneMessageBoard:
    """
    Cross-lane message board backed by MemoryBus (PAMS durable store).

    Provides task posting, lane subscriptions, push hooks, and polling
    for opencode / kilo / freebuff lanes.
    """

    # One tuple drives every lane check in this class (init, post, status,
    # claim, retire, release, broadcast). It carried three lanes while the
    # reader that fronts it advertised four: _board.py is documented as a
    # reader/poster for crush, kilo, agy and opencode, and the poster refused
    # crush and agy with "dst_lane must be one of ('opencode', 'kilo',
    # 'freebuff')" (measured 2026-11-05, rows 47672-47676). A lane the reader
    # names and the writer refuses has no queue, so the set is widened here
    # rather than left to a second copy: docs/scripts/lane_kit.py holds the
    # tutorial copy of this tuple, and tests/test_lane_kit.py pins the two
    # to each other so they cannot drift apart again.
    VALID_LANES = ("opencode", "kilo", "freebuff", "crush", "agy")

    def __init__(
        self,
        session_id: Optional[str] = None,
        pams_url: Optional[str] = None,
        lane: Optional[str] = None,
    ):
        if lane is None:
            raise ValueError("lane is required - omitting it silently writes as 'opencode' (see dive-laneboard-default-lane). Pass lane explicitly.")
        if lane not in self.VALID_LANES:
            raise ValueError(f"lane must be one of {self.VALID_LANES}, got {lane!r}")
        self.lane = lane
        self._bus = MemoryBus(pams_url=pams_url, session_id=session_id)
        self._lock = threading.Lock()
        self._callbacks: Dict[str, List[Callable[[LaneTask], None]]] = {
            lane: [] for lane in self.VALID_LANES
        }
        self._callbacks["all"] = []

        # Read coordination config from defaults
        self._coord_board_enabled = read_default("coordination.board_enabled", True)
        self._coord_claim_lease_seconds = read_default("coordination.claim_lease_seconds", 1800)
        self._coord_max_claim_limit = read_default("coordination.max_claim_limit", 10)
        self._coord_push_hook_enabled = read_default("coordination.push_hook_enabled", True)
        self._coord_dispatch_non_reasoning = read_default("coordination.dispatch_non_reasoning", True)
        self._coord_valid_lanes = read_default("coordination.valid_lanes", ["kilo", "freebuff", "opencode"])

    # -- topic helpers -------------------------------------------------------

    def _task_topic(self, lane: str) -> str:
        return f"lane.{lane}.tasks"

    def _status_topic(self, lane: str) -> str:
        return f"lane.{lane}.status"

    def _announce_topic(self) -> str:
        return "lane.all.announce"

    def _push_topic(self, src: str, dst: str) -> str:
        return f"lane.push.{src}->{dst}"

    # -- public API: post ---------------------------------------------------

    def _open_task_row(
        self,
        lane: str,
        task_id: str,
        action: str,
        detail: str,
    ) -> Optional[int]:
        """board_id of an UNRETIRED row that already says exactly this, else None.

        Measured 2026-10-31: 764 task rows for 370 distinct task_ids. `1039`
        was posted 75 times and `mcp-001` 76, by an opencode poster firing
        every ~130 s for six days. Nothing refused a duplicate, so a lane
        claiming work was handed the same task dozens of times and the queue
        became mostly copies.

        Only an IDENTICAL re-post is collapsed. A row whose action or detail
        differs is a changed instruction and must still post -- silently
        swallowing an update would be worse than the duplicate it prevents.
        A retired row (done/blocked) never blocks a fresh post.
        """
        try:
            conn = self._state_conn()
        except Exception as exc:
            # Not a silent None: the caller reads None as "no duplicate", so an
            # unlogged failure here would disable the duplicate guard invisibly.
            _log.warning("_open_task_row: board store unavailable (%s)", exc)
            return None
        try:
            rows = conn.execute(
                "SELECT q.id, q.content "
                "FROM memorybus_queue q "
                "LEFT JOIN lane_task_state s ON s.board_id = q.id "
                "WHERE q.topic = ? "
                "  AND json_extract(q.content, '$.task_id') = ? "
                "  AND (s.state IS NULL OR s.state IN ('open', 'claimed')) "
                "ORDER BY q.id DESC LIMIT 500",
                (self._task_topic(lane), task_id),
            ).fetchall()
        finally:
            conn.close()
        # Compared through _task_sig, the ONE definition of task identity, not
        # through SQL: a legacy `detail` holding a dict comes back from
        # json_extract as JSON text, so a string compare would call an identical
        # row different and post the duplicate this guard exists to prevent.
        want = _task_sig({"task_id": task_id, "action": action, "detail": detail})
        for row in rows:
            try:
                have = json.loads(row[1] or "{}")
            except Exception as exc:
                # A row whose content is not JSON is skipped, but the skip is
                # COUNTED: a dedupe guard that silently drops the row it was
                # asked about reports "no duplicate" for a row it never read.
                note_failure("LaneMessageBoard:_find_task_row", exc)
                continue
            if _task_sig(have) == want:
                return int(row[0])
        return None

    def _readback_task_row(
        self,
        topic: str,
        task_id: Optional[str] = None,
    ) -> Optional[int]:
        """Return the durable row id of the newest message just written.

        The house rule is "after posting, re-read the row back — a post you
        cannot read back did not land". `MemoryBus.publish` cannot satisfy it:
        on the fallback path it returns `next(self._message_counter)`, an
        in-memory counter that restarts at 1 in every process, and on the PAMS
        path it returns a PAMS message id. Neither is a `memorybus_queue.id`,
        so a caller logging the return value as a receipt cites a row that
        does not exist. This reads the queue back and returns the real id.
        """
        try:
            conn = sqlite3.connect(str(_MEMORYBUS_DB_PATH), timeout=2)
        except Exception as exc:
            _log.warning("readback open failed for %s: %s", topic, exc)
            return None
        try:
            if task_id is None:
                row = conn.execute(
                    "SELECT MAX(id) FROM memorybus_queue WHERE topic = ?", (topic,)
                ).fetchone()
            else:
                # json_extract keeps the match on the payload's own task_id so a
                # concurrent poster to the same topic cannot be mistaken for ours.
                row = conn.execute(
                    "SELECT MAX(id) FROM memorybus_queue "
                    "WHERE topic = ? AND json_extract(content, '$.task_id') = ?",
                    (topic, task_id),
                ).fetchone()
            return int(row[0]) if row and row[0] is not None else None
        except Exception as exc:
            _log.warning("readback query failed for %s: %s", topic, exc)
            return None
        finally:
            conn.close()

    def post_task(
        self,
        lane: str,
        action: str,
        detail: str = "",
        task_id: Optional[str] = None,
        source_lane: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Post a task to a lane's task queue.

        Returns the durable `memorybus_queue.id` of the delivered task, read
        back after the write — not the MemoryBus message id. Raises
        RuntimeError if the write cannot be read back.
        """
        if lane not in self.VALID_LANES:
            raise ValueError(f"lane must be one of {self.VALID_LANES}, got {lane!r}")
        if task_id:
            existing = self._open_task_row(lane, task_id, action, detail)
            if existing is not None:
                _log.info("post_task: %s already open on %s as board %d -- identical "
                          "re-post collapsed, not duplicated", task_id, lane, existing)
                return existing
        task = LaneTask(
            lane=lane,
            task_id=task_id,
            action=action,
            detail=detail,
            source_lane=source_lane or self.lane,
            metadata=metadata or {},
        )
        topic = self._task_topic(lane)
        self._bus.publish(
            topic,
            task.to_dict(),
            metadata={"type": "lane_task", "lane": lane, "source": self.lane},
            source=self.lane,
        )
        row = self._readback_task_row(topic, task_id)
        if row is None:
            raise RuntimeError(
                f"post_task: task for lane {lane!r} (task_id={task_id!r}) was "
                "published but no durable queue row could be read back — "
                "treat the task as NOT delivered"
            )
        return row

    def _publish_then_read_back(
        self,
        topic: str,
        payload: Dict[str, Any],
        metadata: Dict[str, Any],
        what: str,
    ) -> int:
        """Publish, then return the durable `memorybus_queue.id` of that write.

        `post_task` was taught this contract on 2026-10-31 and its two sibling
        posters were not: `post_status` returned 1 — the in-memory bus counter,
        which restarts at 1 in every process — while the row actually landed at
        id 46858. A caller logging that number cites a row that does not exist.

        The check is `after > before` on the queue, not `after is not None`:
        read-back alone would happily return somebody else's older row when our
        own write failed.
        """
        before = self.max_id()
        self._bus.publish(topic, payload, metadata=metadata, source=self.lane)
        row = self._readback_task_row(topic)
        if row is None or row <= before:
            raise RuntimeError(
                f"{what}: published to {topic} but no new durable queue row could "
                f"be read back (max id before={before}, after={row}) — treat this "
                "post as NOT delivered"
            )
        return row

    def post_announcement(
        self,
        announcement: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Broadcast an announcement to all lanes.

        Returns the durable `memorybus_queue.id`, read back after the write.
        """
        return self._publish_then_read_back(
            self._announce_topic(),
            {
                "announcement": announcement,
                "source_lane": self.lane,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {"type": "announcement", "source": self.lane},
            "post_announcement",
        )

    def post_status(
        self,
        status: str,
        detail: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Post a status update from this lane.

        Returns the durable `memorybus_queue.id`, read back after the write —
        not the MemoryBus counter, which is per-process and starts at 1 in
        every lane.
        """
        return self._publish_then_read_back(
            self._status_topic(self.lane),
            {
                "lane": self.lane,
                "status": status,
                "detail": detail,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {"type": "lane_status", "lane": self.lane, "source": self.lane},
            "post_status",
        )

    def push_hook(
        self,
        dst_lane: str,
        action: str,
        detail: str = "",
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Push a task from this lane to another lane via the push hook.

        Equivalent to post_task but recorded on a push-specific topic so
        push-hook traffic can be filtered/audited separately.

        Ordering is deliberate: the task is **delivered first**, then the
        audit row is written carrying `delivery_row` — the durable id of the
        task that actually landed. The audit row therefore records a fact
        ("this row exists in that lane's queue"), never an intent, and the two
        rows are always joinable even when `task_id` is None. A failure to
        write the audit row is logged rather than swallowed, because a hook
        whose stated purpose is auditability must not lose its audit silently.

        Returns the durable `memorybus_queue.id` of the delivered task.
        """
        if dst_lane not in self.VALID_LANES:
            raise ValueError(
                f"dst_lane must be one of {self.VALID_LANES}, got {dst_lane!r}"
            )
        # Delivery record on the destination lane's task queue.
        delivery_row = self.post_task(
            lane=dst_lane,
            action=action,
            detail=detail,
            task_id=task_id,
            source_lane=self.lane,
            metadata=metadata or {},
        )
        # Audit record on the push-specific topic, linked to the delivery.
        try:
            self._bus.publish(
                self._push_topic(self.lane, dst_lane),
                {
                    "src_lane": self.lane,
                    "dst_lane": dst_lane,
                    "task_id": task_id,
                    "action": action,
                    "detail": detail,
                    "delivery_row": delivery_row,
                    "self_push": dst_lane == self.lane,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                metadata={"type": "lane_push", "src": self.lane, "dst": dst_lane},
                source=self.lane,
            )
        except Exception as exc:
            # Delivered but unlogged: say so loudly instead of passing.
            _log.error(
                "push_hook: delivered to %s as row %s but the audit row on %s "
                "FAILED (%s) — this push is not auditable",
                dst_lane, delivery_row, self._push_topic(self.lane, dst_lane), exc,
            )
        return delivery_row

    # -- public API: subscribe ----------------------------------------------

    def subscribe_to_tasks(
        self,
        lane: str,
        callback: Callable[[LaneTask], None],
    ) -> int:
        """
        Subscribe to tasks addressed to a lane.

        Returns the number of active subscriptions for that lane's topic.
        """
        if lane not in self.VALID_LANES:
            raise ValueError(f"lane must be one of {self.VALID_LANES}, got {lane!r}")

        def _adapter(msg: BusMessage) -> None:
            try:
                task = LaneTask.from_dict(msg.content if isinstance(msg.content, dict)
                                          else json.loads(msg.content))
            except Exception as exc:
                note_failure("LaneMessageBoard:subscribe_tasks-adapter", exc)
                return  # malformed message; skip
            callback(task)

        with self._lock:
            self._callbacks[lane].append(callback)
        return self._bus.subscribe(self._task_topic(lane), _adapter)  # type: ignore[arg-type]

    def subscribe_to_all_tasks(
        self,
        callback: Callable[[LaneTask], None],
    ) -> None:
        """Subscribe to tasks for all lanes (for monitoring/audit)."""
        for lane in self.VALID_LANES:
            self.subscribe_to_tasks(lane, callback)

    def subscribe_to_announcements(
        self,
        callback: Callable[[Dict[str, Any]], None],
    ) -> int:
        """Subscribe to all-lane announcements."""
        return self._bus.subscribe(self._announce_topic(), callback)  # type: ignore[arg-type]

    # -- public API: poll ---------------------------------------------------

    def _poll_persistent_queue(
        self,
        topic: str,
        since_id: int = 0,
        limit: int = 50,
        exclude_retired: bool = False,
        exclude_claimed: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Read messages directly from the shared persistent SQLite queue.

        This enables cross-instance (cross-process) communication even when
        PAMS is unavailable: both lanes read from the same SQLite database.

        FIX 2026-10-26 (board-blindness): with since_id=0 the old
        ORDER BY id ASC LIMIT took the OLDEST N rows, so any queue longer
        than `limit` hid every new task from polling lanes. Now takes the
        NEWEST N (DESC) and re-sorts ASC, so a poll always sees live orders.

        FIX 2026-10-31 (reader-blindness): `exclude_retired` joins
        `lane_task_state`, the queue's other half. Until then the poll path
        never asked whether a row was retired, while `claim_tasks` and
        `task_stats` did — measured on the real board, `poll_tasks` returned
        340 rows for the kilo lane against 25 actionable ones, 313 of them
        `archived pre-cutover backlog`. The filter is applied IN SQL rather
        than after the LIMIT on purpose: post-filtering would hand back
        (limit - retired) rows, so a lane's batch would silently shrink as
        history grew. 'archived' is in the retired set because
        `archive_backlog` used to write 'blocked' — the same value that means
        cannot-proceed — so both spellings must read as retired.
        """
        import sqlite3

        # Read the module-level path, not a fresh import. The inner import
        # shadowed the global with the real store, so a caller that redirects
        # `LaneMessageBoard._MEMORYBUS_DB_PATH` (every test fixture does) was
        # silently ignored here and polled the live board instead.
        results: List[Dict[str, Any]] = []
        extra = ""
        extra_args: List[Any] = []
        if exclude_retired or exclude_claimed:
            try:
                # A store that predates the state table would make the NOT
                # EXISTS below raise, and the outer handler would turn that
                # into an EMPTY poll — a lane seeing no work at all. Create it
                # first so the failure mode cannot be "board looks empty".
                self._state_conn().close()
            except Exception as exc:
                _log.warning(
                    "poll: lane_task_state unavailable (%s); this poll includes "
                    "retired and claimed rows", exc)
                exclude_retired = exclude_claimed = False
        if exclude_retired:
            extra += (
                "AND NOT EXISTS (SELECT 1 FROM lane_task_state s "
                "WHERE s.board_id = memorybus_queue.id "
                "AND s.state IN ('done', 'blocked', 'archived')) "
            )
        if exclude_claimed:
            # A live lease is work in somebody's hands, not work on offer.
            # Keeping it here is the same defect as keeping retired rows: on
            # 2026-10-31 freebuff polled 12 rows against 5 claimable.
            extra += (
                "AND NOT EXISTS (SELECT 1 FROM lane_task_state c "
                "WHERE c.board_id = memorybus_queue.id "
                "AND c.state = 'claimed' AND c.claimed_at IS NOT NULL "
                "AND CAST(strftime('%s', c.claimed_at) AS INTEGER) >= ?) "
            )
            extra_args.append(
                int(datetime.now(timezone.utc).timestamp()) - self.CLAIM_LEASE_SECONDS)
        try:
            conn = sqlite3.connect(str(_MEMORYBUS_DB_PATH), timeout=2)
            conn.row_factory = sqlite3.Row
            if since_id:
                rows = conn.execute(
                    "SELECT id, topic, content, metadata, source, session_id, timestamp "
                    "FROM memorybus_queue "
                    "WHERE topic LIKE ? AND id > ? "
                    + extra +
                    "ORDER BY id ASC LIMIT ?",
                    (topic, since_id, *extra_args, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, topic, content, metadata, source, session_id, timestamp "
                    "FROM memorybus_queue "
                    "WHERE topic LIKE ? "
                    + extra +
                    "ORDER BY id DESC LIMIT ?",
                    (topic, *extra_args, limit),
                ).fetchall()
                rows = rows[::-1]
            conn.close()
            for row in rows:
                try:
                    content = row["content"]
                    if isinstance(content, str):
                        content = json.loads(content)
                    metadata = row["metadata"]
                    if isinstance(metadata, str):
                        metadata = json.loads(metadata)
                    results.append({
                        "id": row["id"],
                        "topic": row["topic"],
                        "content": content,
                        "metadata": metadata,
                        "source": row["source"],
                        "session_id": row["session_id"],
                        "timestamp": row["timestamp"],
                    })
                except Exception as exc:
                    note_failure("LaneMessageBoard:search_messages", exc)
                    continue
        except Exception as exc:
            note_failure("_poll_persistent_queue outer", exc)
        return results

    def poll_tasks(
        self,
        lane: str,
        since_id: int = 0,
        limit: int = 50,
        include_retired: bool = False,
        include_claimed: bool = False,
    ) -> List[LaneTask]:
        """
        Poll for tasks addressed to a lane.

        Reads from both the MemoryBus (PAMS or local in-memory queue) and the
        shared persistent SQLite queue, enabling cross-instance communication
        even when PAMS is unavailable.

        Returns tasks with message ID > since_id.

        `include_retired=False` (the default) excludes rows the state table
        marks done/blocked/archived. Before 2026-10-31 this method did not
        consult that table at all, so every lane instruction that says "poll
        your queue" showed retired history as live work: kilo 340 rows for 25
        actionable, freebuff 330 for 7, opencode 99 for 2. Pass
        `include_retired=True` for the        audit view.

        `include_claimed=False` (the default) also drops rows under a live
        lease, so what comes back is exactly what `claim_tasks` would hand out.
        Without it the two still disagreed: freebuff polled 12 against 5
        claimable. An expired lease is not a claim — that row is available, and
        `claim_tasks` will hand it back.
        """
        if lane not in self.VALID_LANES:
            raise ValueError(f"lane must be one of {self.VALID_LANES}, got {lane!r}")

        # Read from MemoryBus (PAMS or in-memory local queue)
        msgs = self._bus.poll(topic=self._task_topic(lane), since_id=since_id, limit=limit)

        # Also read from the shared persistent SQLite queue for cross-instance msgs
        persistent = self._poll_persistent_queue(
            self._task_topic(lane), since_id, limit,
            exclude_retired=not include_retired,
            # include_retired IS the audit view, so it must show leases too.
            # Measured 2026-10-31: `--all` returned 328 of freebuff's 335 rows
            # because the 7 live claims were still being filtered.
            exclude_claimed=not (include_claimed or include_retired),
        )

        # Merge, deduplicating by id
        seen: set = set()
        tasks: List[LaneTask] = []

        # A same-process post appears twice: once from the live bus (no row id)
        # and once from the persistent queue (with one). Different id spaces, so
        # the `seen` set cannot catch it and the lane would be handed the same
        # work twice. Signature-dedupe the bus copy against the durable copy.
        durable_sigs = set()
        for _p in persistent:
            try:
                _s = _task_sig(_p["content"] if isinstance(_p["content"], dict)
                               else json.loads(_p["content"]))
            except Exception as exc:
                note_failure("LaneMessageBoard:poll_tasks-durable-sig", exc)
                continue
            if _s:
                durable_sigs.add(_s)

        for m in msgs:
            mid = m.message_id
            if mid in seen:
                continue
            seen.add(mid)
            try:
                _content = m.content if isinstance(m.content, dict) else json.loads(m.content)
                if _task_sig(_content) in durable_sigs:
                    continue
                tasks.append(LaneTask.from_dict(_content))
            except Exception as exc:
                note_failure("LaneMessageBoard:poll_tasks-bus-row", exc)
                continue

        for p in persistent:
            mid = p["id"]
            if mid in seen:
                continue
            seen.add(mid)
            try:
                task = LaneTask.from_dict(p["content"])
                # Expose the durable queue row id: pollers cite Board-<id>,
                # and ordering below keys on it instead of the string task_id
                # (mixed numeric/string ids sort meaninglessly as strings).
                if isinstance(task.metadata, dict):
                    task.metadata.setdefault("_row_id", mid)
                tasks.append(task)
            except Exception as exc:
                note_failure("LaneMessageBoard:poll_tasks-durable-row", exc)
                continue

        # Newest-first when uncursored (see _poll_persistent_queue fix);
        # key on durable row id, fallback to legacy string task_id sort.
        def _order(t: LaneTask):
            try:
                rid = (t.metadata or {}).get("_row_id")
                if rid is not None:
                    return (0, int(rid))
            except (TypeError, ValueError) as exc:
                note_failure("LaneMessageBoard:poll_tasks-order-row_id", exc)
            try:
                if t.task_id and t.task_id.isdigit():
                    return (1, int(t.task_id))
            except (AttributeError, TypeError, ValueError) as exc:
                note_failure("LaneMessageBoard:poll_tasks-order-task_id", exc)
            return (2, 0)

        tasks.sort(key=_order)
        return tasks[:limit]

    def poll_announcements(
        self,
        since_id: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Poll for all-lane announcements."""
        msgs = self._bus.poll(topic=self._announce_topic(), since_id=since_id, limit=limit)

        # Also read from persistent queue
        persistent = self._poll_persistent_queue(self._announce_topic(), since_id, limit)

        out: List[Dict[str, Any]] = []
        seen: set = set()
        for m in msgs:
            mid = m.message_id
            if mid in seen:
                continue
            seen.add(mid)
            try:
                out.append(m.content if isinstance(m.content, dict) else json.loads(m.content))
            except Exception as exc:
                note_failure("LaneMessageBoard:poll_announces-bus-row", exc)
                continue

        for p in persistent:
            mid = p["id"]
            if mid in seen:
                continue
            seen.add(mid)
            try:
                out.append(p["content"])
            except Exception as exc:
                note_failure("LaneMessageBoard:poll_announces-durable-row", exc)
                continue

        return out[:limit]


    def poll_status(
        self,
        lane: str,
        since_id: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Poll status updates posted by a lane.

        Status rows were previously write-only across processes: post_status
        publishes via the bus, but MemoryBus.poll only sees the same-process
        in-memory queue (PAMS has no list/query endpoint), so cross-instance
        readers saw nothing. This mirrors poll_tasks: it merges the live bus
        poll with the shared persistent SQLite queue, deduplicates by durable
        row id, and returns newest-first when uncursored (same board-blindness
        fix as _poll_persistent_queue).

        Returns status dicts with an extra `_row_id` key (the durable queue
        id) for max-id-checked posting discipline.
        """
        if lane not in self.VALID_LANES:
            raise ValueError(f"lane must be one of {self.VALID_LANES}, got {lane!r}")

        topic = self._status_topic(lane)
        msgs = self._bus.poll(topic=topic, since_id=since_id, limit=limit)
        persistent = self._poll_persistent_queue(topic, since_id, limit)

        seen: set = set()
        rows: List[Dict[str, Any]] = []

        for m in msgs:
            mid = m.message_id
            if mid in seen:
                continue
            seen.add(mid)
            try:
                content = m.content if isinstance(m.content, dict) else json.loads(m.content)
            except Exception as exc:
                note_failure("LaneMessageBoard:poll_status-bus-row", exc)
                continue
            if isinstance(content, dict):
                content = dict(content)
                content.setdefault("_row_id", mid)
                rows.append(content)

        for p in persistent:
            mid = p["id"]
            if mid in seen:
                continue
            seen.add(mid)
            try:
                content = p["content"]
                if not isinstance(content, dict):
                    continue
                content = dict(content)
                content.setdefault("_row_id", mid)
                rows.append(content)
            except Exception as exc:
                note_failure("LaneMessageBoard:poll_status-durable-row", exc)
                continue

        def _order(r: Dict[str, Any]):
            try:
                rid = int(r.get("_row_id", 0))
            except Exception:
                rid = -1
            if rid >= 0:
                return (0, rid)
            return (1, 0)

        rows.sort(key=_order)
        return rows[:limit]

    def max_id(self) -> int:
        """Return the highest durable queue row id (for max-id-checked posting)."""
        top = 0
        try:
            conn = sqlite3.connect(str(_MEMORYBUS_DB_PATH), timeout=2)
        except Exception:
            conn = None
        if conn is not None:
            try:
                row = conn.execute("SELECT MAX(id) FROM memorybus_queue").fetchone()
                if row and row[0] is not None:
                    top = int(row[0])
            finally:
                conn.close()
        return top

    # -- task state: claim / complete (batch-loop support) -------------------
    #
    # Until 2026-10-31 nothing recorded that a task had been picked up. The
    # only `status` field lives INSIDE the content JSON, so a poller could not
    # filter on it without parsing every row, and every lane re-read its whole
    # backlog on every poll: lane.freebuff.tasks held 325 rows and
    # lane.kilo.tasks 321, all permanently `pending`. Driving work in batch
    # loops off that board made each pass slower, not faster. The state table
    # below is the missing queue half: `claim` hands out a bounded batch, and
    # `done`/`block` retire it so later passes see only new work.

    STATE_SCHEMA = """
        CREATE TABLE IF NOT EXISTS lane_task_state (
            board_id   INTEGER PRIMARY KEY,
            lane       TEXT NOT NULL,
            task_id    TEXT,
            state      TEXT NOT NULL,          -- claimed | available | done | blocked | archived
            claimed_by TEXT,
            claimed_at TEXT,
            retired_at TEXT,
            note       TEXT,
            attempts   INTEGER NOT NULL DEFAULT 1
        )
    """
    # A claim older than this is reclaimable. Without an expiry a lane that
    # dies mid-batch strands its tasks forever and the queue silently stalls.
    CLAIM_LEASE_SECONDS = 1800

    def _state_conn(self):
        conn = sqlite3.connect(str(_MEMORYBUS_DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute(self.STATE_SCHEMA)
        return conn

    def claim_tasks(
        self,
        lane: str,
        limit: int = 10,
        agent: str = "",
        lease_seconds: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Atomically claim up to `limit` unretired tasks for `lane`.

        Returns dicts: {board_id, task_id, action, detail, source_lane,
        created_at}. Claims are compare-and-set inside BEGIN IMMEDIATE, so two
        lanes polling at once cannot both receive the same row. Rows claimed
        more than `CLAIM_LEASE_SECONDS` ago are treated as abandoned and are
        handed out again, with `attempts` incremented.
        """
        if lane not in self.VALID_LANES:
            raise ValueError(f"lane must be one of {self.VALID_LANES}, got {lane!r}")
        lease = self.CLAIM_LEASE_SECONDS if lease_seconds is None else int(lease_seconds)
        now = datetime.now(timezone.utc)
        stamp = now.isoformat(timespec="seconds")
        cutoff = (now.timestamp() - lease)

        conn = self._state_conn()
        out: List[Dict[str, Any]] = []
        try:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
            # Candidate rows: on this lane's topic, not done/blocked, and not
            # claimed by a live claim. Oldest first so a backlog drains in the
            # order it was posted rather than reverse.
            rows = conn.execute(
                "SELECT q.id AS board_id, q.content AS content "
                "FROM memorybus_queue q "
                "LEFT JOIN lane_task_state s ON s.board_id = q.id "
                "WHERE q.topic = ? "
                "  AND (s.state IS NULL OR s.state = 'claimed' OR s.state = 'available') "
                "  AND (s.claimed_at IS NULL OR "
                "       CAST(strftime('%s', s.claimed_at) AS INTEGER) < ?) "
                "ORDER BY q.id ASC LIMIT ?",
                (self._task_topic(lane), int(cutoff), int(limit)),
            ).fetchall()
            # task_ids already live (claimed elsewhere) for this lane. A second
            # copy of a live task_id is a duplicate: handing it out too means two
            # agents doing one piece of work.
            # LIVE claims only. An expired claim is up for grabs, so counting it
            # as active would make a lease expiry indistinguishable from a
            # duplicate and the abandoned row could never be reclaimed.
            # The key is (task_id, action, detail), NOT task_id alone: measured
            # 2026-10-31, a deliberately revised task (same id, new detail) was
            # retired as "duplicate of Board-46849" and the revision was lost,
            # because the id alone cannot tell a re-post from an update.
            active = set()
            for _r in conn.execute(
                "SELECT q.content "
                "FROM lane_task_state s "
                "JOIN memorybus_queue q ON q.id = s.board_id "
                "WHERE s.lane = ? AND s.state = 'claimed' "
                "AND s.task_id IS NOT NULL AND s.claimed_at IS NOT NULL "
                "AND CAST(strftime('%s', s.claimed_at) AS INTEGER) >= ?",
                (lane, int(cutoff)),
            ).fetchall():
                try:
                    _p = json.loads(_r[0] or "{}")
                except Exception as exc:
                    note_failure("LaneMessageBoard:claim-active-scan", exc)
                    continue
                _s = _task_sig(_p)
                if _s:
                    active.add(_s)
            seen_in_batch: dict[tuple, int] = {}
            for row in rows:
                bid = int(row["board_id"])
                try:
                    payload = json.loads(row["content"] or "{}")
                except Exception as exc:
                    note_failure("LaneMessageBoard:claim-row-payload", exc)
                    continue
                # The same task re-posted by several sessions (mcp_config_fix
                # appears 76 times) must not become 76 pieces of work. A copy is
                # a duplicate when its CONTENT is already retired, already claimed
                # elsewhere, or already handed out earlier in this batch.
                tid = payload.get("task_id")
                sig = _task_sig(payload)
                dup_of = None
                if sig and sig in seen_in_batch:
                    dup_of = seen_in_batch[sig]
                elif sig and sig in active:
                    dup_of = -1          # a live claim elsewhere holds this work
                elif sig:
                    for _d in conn.execute(
                        "SELECT s.board_id, q.content FROM lane_task_state s "
                        "JOIN memorybus_queue q ON q.id = s.board_id "
                        "WHERE s.lane = ? AND s.task_id = ? "
                        "  AND s.state IN ('done','blocked')",
                        (lane, tid),
                    ).fetchall():
                        try:
                            _dp = json.loads(_d[1] or "{}")
                        except Exception:
                            _dp = {}
                        if _task_sig(_dp) == sig:
                            dup_of = int(_d[0])
                            break
                if dup_of is not None:
                    conn.execute(
                        "INSERT INTO lane_task_state (board_id, lane, task_id, state, "
                        "  retired_at, note, attempts) VALUES (?, ?, ?, 'done', ?, ?, 1) "
                        "ON CONFLICT(board_id) DO UPDATE SET state='done', "
                        "  retired_at=excluded.retired_at, note=excluded.note",
                        (bid, lane, tid, stamp,
                         f"duplicate of Board-{dup_of}" if dup_of >= 0
                         else "duplicate: same task_id is already claimed"),
                    )
                    continue
                if sig:
                    seen_in_batch[sig] = bid
                conn.execute(
                    "INSERT INTO lane_task_state (board_id, lane, task_id, state, "
                    "  claimed_by, claimed_at, attempts) VALUES (?, ?, ?, 'claimed', ?, ?, 1) "
                    "ON CONFLICT(board_id) DO UPDATE SET "
                    "  state='claimed', claimed_by=excluded.claimed_by, "
                    "  claimed_at=excluded.claimed_at, retired_at=NULL, "
                    "  attempts=lane_task_state.attempts + 1",
                    (bid, lane, payload.get("task_id"), agent, stamp),
                )
                out.append({
                    "board_id": bid,
                    "task_id": payload.get("task_id"),
                    "action": payload.get("action", ""),
                    "detail": str(payload.get("detail") or "")[:2000],
                    "source_lane": payload.get("source_lane", ""),
                    "created_at": payload.get("created_at", ""),
                })
            conn.execute("COMMIT")
        except Exception as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception as rb_exc:
                # A failed rollback means the transaction state is unknown; the
                # `pass` this replaces hid exactly that while the outer handler
                # reported only the original fault.
                _log.warning("claim_tasks rollback failed for lane %s: %s", lane, rb_exc)
            _log.warning("claim_tasks failed for lane %s: %s", lane, exc)
            raise
        finally:
            conn.close()
        return out

    def complete_task(
        self,
        board_id: int,
        state: str = "done",
        note: str = "",
    ) -> bool:
        """Retire a claimed task. state is 'done' or 'blocked'.

        Returns True only when a row moved; False means the id was never
        claimed (so nothing was retired and the caller must not report it as
        completed).
        """
        if state not in ("done", "blocked"):
            raise ValueError("state must be 'done' or 'blocked'")
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn = self._state_conn()
        try:
            cur = conn.execute(
                "UPDATE lane_task_state SET state=?, retired_at=?, note=? "
                "WHERE board_id=? AND state='claimed'",
                (state, stamp, note[:4000], int(board_id)),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def release_task(self, board_id: int) -> bool:
        """Hand a claimed task back to the queue unretired.

        `complete_task` is for work that is finished or blocked. A lane that
        claimed a row it cannot do -- wrong tool for the task, or a dispatch
        that has to go to another lane first -- must be able to put it back
        without waiting out the 30-minute lease and without pretending it is
        done. Only a live claim can be released; a retired row is refused so a
        release can never resurrect completed work.
        """
        conn = self._state_conn()
        try:
            row = conn.execute(
                "SELECT lane, claimed_by, attempts FROM lane_task_state "
                "WHERE board_id=? AND state='claimed'",
                (int(board_id),),
            ).fetchone()
            if row is None:
                return False
            lane = row["lane"]
            from_owner = row["claimed_by"]
            attempts = row["attempts"]
            # Preserve attempts: null the claim instead of deleting the row, so
            # the next claim re-uses it with attempts+1 and the history survives.
            conn.execute(
                "UPDATE lane_task_state SET state='available', claimed_by=NULL, "
                "claimed_at=NULL, note=? WHERE board_id=? AND state='claimed'",
                ("released %s" % datetime.now(timezone.utc).isoformat(), int(board_id)),
            )
            conn.commit()
        finally:
            conn.close()
        # Audit row so a release is reconstructable (it used to delete its own
        # evidence). Best-effort: a failed audit must not undo the release.
        try:
            self._publish_then_read_back(
                "lane.release.%s" % lane,
                {
                    "board_id": int(board_id),
                    "from": from_owner,
                    "released_at": datetime.now(timezone.utc).isoformat(),
                    "attempts": attempts,
                },
                {"type": "release", "lane": lane, "source": self.lane},
                "release_task",
            )
        except Exception as exc:
            note_failure("LaneMessageBoard:release_task-audit", exc)
        return True

    def archive_backlog(
        self,
        before_iso: str,
        lane: Optional[str] = None,
        dry_run: bool = True,
        dump_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retire tasks created before `before_iso` as 'archived', preserving them.

        'archived' is its own state, not 'blocked'. Reusing 'blocked' (the
        documented cannot-proceed value) meant `stats` reported kilo blocked=313
        / freebuff blocked=322 and neither number could be read without knowing
        which producer wrote it.

        A restart should not begin by draining nine days of stale rows in the
        order they were posted. Rows are NOT deleted and NOT silently dropped:
        each is written to `dump_path` first, then retired with a note naming
        this cutover, so the content stays inspectable and the queue starts
        clean. dry_run (the default) writes the dump and changes nothing.
        """
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lanes = [lane] if lane else list(self.VALID_LANES)
        stamped = []
        conn = self._state_conn()
        try:
            for ln in lanes:
                rows = conn.execute(
                    "SELECT q.id AS board_id, q.content AS content "
                    "FROM memorybus_queue q "
                    "LEFT JOIN lane_task_state s ON s.board_id = q.id "
                    "WHERE q.topic = ? "
                    "  AND (s.state IS NULL OR s.state = 'claimed')",
                    (self._task_topic(ln),),
                ).fetchall()
                for row in rows:
                    try:
                        payload = json.loads(row["content"] or "{}")
                    except Exception:
                        payload = {}
                    created = str(payload.get("created_at") or "")
                    if not created or created >= before_iso:
                        continue
                    stamped.append({
                        "lane": ln,
                        "board_id": int(row["board_id"]),
                        "task_id": payload.get("task_id"),
                        "action": payload.get("action"),
                        "created_at": created,
                        # detail is a string in most rows but a dict in a few
                        # legacy posts; slicing a dict raises KeyError: slice.
                        "detail": str(payload.get("detail") or "")[:4000],
                    })
            if dump_path:
                with open(dump_path, "w", encoding="utf-8") as fh:
                    fh.write("lane\tboard_id\ttask_id\taction\tcreated_at\tdetail\n")
                    for r in stamped:
                        fh.write("\t".join(str(r[k]).replace("\t", " ").replace("\n", " ")
                                          for k in ("lane", "board_id", "task_id", "action",
                                                    "created_at", "detail")) + "\n")
            if not dry_run:
                for r in stamped:
                    conn.execute(
                        "INSERT INTO lane_task_state (board_id, lane, task_id, state, "
                        "  retired_at, note, attempts) VALUES (?, ?, ?, 'archived', ?, ?, 1) "
                        "ON CONFLICT(board_id) DO UPDATE SET state='archived', "
                        "  retired_at=excluded.retired_at, note=excluded.note",
                        (r["board_id"], r["lane"], r["task_id"], stamp,
                         f"archived pre-cutover backlog (< {before_iso}) {stamp}"),
                    )
                conn.commit()
            return {"matched": len(stamped), "retired": 0 if dry_run else len(stamped),
                    "dry_run": dry_run, "dump_path": dump_path,
                    "by_lane": {ln: sum(1 for r in stamped if r["lane"] == ln)
                                for ln in lanes}}
        finally:
            conn.close()

    def task_stats(self, lane: Optional[str] = None) -> Dict[str, int]:
        """Per-state counts, plus the two numbers a batch loop actually needs.

        `available` is what `claim_tasks` can hand out right now;
        `claimed` is work under a live lease; `retired` rolls up the three
        end states. Until 2026-10-31 one number, `open`, counted
        `(state IS NULL OR state='claimed')` — so it included work another
        agent held a live claim on, while `claim_tasks` correctly excluded it.
        Measured on the real board the same minute: freebuff reported open=11
        with claimed=7 and `claim_tasks` yielded 4. A lane sizing its batch
        off `open` planned work it could not get.

        `open` is kept because the lane instruction files name it, but it now
        means `available`; `open_incl_claimed` carries the old inclusive
        figure so nothing silently changes meaning.

        A lapsed lease was counted twice until 2026-11-02: `claimed` was a raw
        `state='claimed'` count, so a row whose 30-minute lease had expired was
        in `claimed` AND in `available` (because `claim_tasks` will hand it out
        again), and `open_incl_claimed` added both. On the real board that read
        as work in flight that no lane held. The state is now split by the
        lease it actually holds — `claimed_live` (someone still has it),
        `claimed_expired` (lapsed, counted in `available`), `claimed_all` (the
        raw row count) — and `claimed` means live, so `open_incl_claimed`
        stays an arithmetic identity: `available + claimed_live`.

        `churned` is the queue's own defect count: unretired rows at
        `attempts >= 3`, meaning the work has been handed out three times and
        come back each time. A row there is not waiting on a lane — it is
        waiting on a decision, so it is named rather than left to inflate
        `available` forever.
        """
        conn = self._state_conn()
        try:
            where, args = ("WHERE s.lane = ?", (lane,)) if lane else ("", ())
            rows = conn.execute(
                "SELECT s.state, COUNT(*) FROM lane_task_state s " + where +
                " GROUP BY s.state", args,
            ).fetchall()
            counts = {r[0]: int(r[1]) for r in rows}
            for key in ("claimed", "done", "blocked", "archived"):
                counts.setdefault(key, 0)
            lanes = [lane] if lane else list(self.VALID_LANES)
            cutoff = int(datetime.now(timezone.utc).timestamp()) - self.CLAIM_LEASE_SECONDS
            claimed_live = 0
            churned = 0
            for ln in lanes:
                live = conn.execute(
                    "SELECT COUNT(*) FROM lane_task_state s "
                    "JOIN memorybus_queue q ON q.id = s.board_id "
                    "WHERE q.topic = ? AND s.state = 'claimed' "
                    "  AND s.claimed_at IS NOT NULL "
                    "  AND CAST(strftime('%s', s.claimed_at) AS INTEGER) >= ?",
                    (self._task_topic(ln), cutoff),
                ).fetchone()
                claimed_live += int(live[0] or 0)
                churn = conn.execute(
                    "SELECT COUNT(*) FROM lane_task_state s "
                    "JOIN memorybus_queue q ON q.id = s.board_id "
                    "WHERE q.topic = ? AND s.attempts >= 3 "
                    "  AND s.state NOT IN ('done','blocked','archived')",
                    (self._task_topic(ln),),
                ).fetchone()
                churned += int(churn[0] or 0)
            avail = 0
            for ln in lanes:
                row = conn.execute(
                    "SELECT COUNT(*) FROM memorybus_queue q "
                    "LEFT JOIN lane_task_state s ON s.board_id = q.id "
                    "WHERE q.topic = ? "
                    "  AND (s.state IS NULL OR s.state = 'available' "
                    "       OR (s.state = 'claimed' "
                    "           AND (s.claimed_at IS NULL "
                    "                OR CAST(strftime('%s', s.claimed_at) "
                    "                        AS INTEGER) < ?)))",
                    (self._task_topic(ln), cutoff),
                ).fetchone()
                avail += int(row[0] or 0)
            claimed_all = counts.get("claimed", 0)
            counts["claimed_all"] = claimed_all
            counts["claimed"] = claimed_live
            counts["claimed_live"] = claimed_live
            counts["claimed_expired"] = claimed_all - claimed_live
            counts["churned"] = churned
            counts["available"] = avail
            counts["open"] = avail
            # avail already includes lapsed leases, so pairing it with the live
            # count is the only form that does not double-count them.
            counts["open_incl_claimed"] = avail + claimed_live
            counts["retired"] = (counts["done"] + counts["blocked"]
                                 + counts["archived"])
            return counts
        finally:
            conn.close()

    def recent_transitions(self, lane: Optional[str] = None,
                           limit: int = 20) -> List[Dict[str, Any]]:
        """Newest-first state-table transitions — the sight layer (2026-11-09).

        Status rows are voluntary: a lane can do a day's work (kilo retired 3
        rows the morning of 2026-11-09) without posting one, and a reader of
        poll_status alone then reports the lane dark. The state table is not
        voluntary — every claim/done/block writes here — so it is the ground
        truth for who did what, when. Read-only; newest first.

        Returns dicts: {board_id, lane, task_id, state, claimed_by,
        retired_at, attempts, note (first 300 chars)}.
        """
        conn = self._state_conn()
        try:
            where, args = (("WHERE s.lane = ?", (lane,)) if lane else ("", ()))
            rows = conn.execute(
                "SELECT s.board_id, s.lane, s.task_id, s.state, s.claimed_by, "
                "       s.claimed_at, s.retired_at, s.attempts, "
                "       substr(s.note, 1, 300) "
                "FROM lane_task_state s " + where +
                " ORDER BY s.board_id DESC LIMIT ?",
                args + (int(limit),),
            ).fetchall()
            return [
                {"board_id": r[0], "lane": r[1], "task_id": r[2],
                 "state": r[3], "claimed_by": r[4], "claimed_at": r[5],
                 "retired_at": r[6], "attempts": r[7], "note": r[8]}
                for r in rows
            ]
        finally:
            conn.close()

    def poll_and_deliver(self) -> int:
        """Poll all lanes' task topics and deliver to subscribers.

        Returns the number of messages delivered.
        """
        delivered = 0
        for lane in self.VALID_LANES:
            delivered += self._bus.poll_and_deliver(topic=self._task_topic(lane))
        delivered += self._bus.poll_and_deliver(topic=self._announce_topic())
        return delivered

    def get_stats(self) -> Dict[str, int]:
        """Return bus statistics."""
        return dict(self._bus.get_stats())

    # -- legacy chat-log migration ------------------------------------------

    def import_from_chat_log(self, chat_log_path: str) -> int:
        """
        Import task-looking entries from message-history.json into the board.

        Parses the JSON array and tracks context across entries: when an enggram
        number appears in one entry and a lane name appears in a nearby entry,
        a pending task is created for that lane. Returns the number of tasks imported.
        """
        import json
        import os
        import re

        if not os.path.isfile(chat_log_path):
            return 0

        engram_re = re.compile(r"engram\s*(?:#)?(\d+)", re.IGNORECASE)
        lane_re = re.compile(r"\b(kilo|freebuff|opencode)\b", re.IGNORECASE)

        count = 0
        pending_engram: Optional[str] = None
        pending_detail: str = ""

        try:
            with open(chat_log_path, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
        except Exception as exc:
            note_failure("LaneMessageBoard:import_from_chat_log", exc)
            return 0

        if not isinstance(data, list):
            return 0

        for entry in data:
            if not isinstance(entry, str):
                continue
            text = entry.strip()
            if not text:
                continue

            em = engram_re.search(text)
            lm = lane_re.search(text)

            if em:
                pending_engram = em.group(1)
                pending_detail = text[:500]

            if lm:
                lane = lm.group(1).lower()
                if lane in self.VALID_LANES and pending_engram:
                    self.post_task(
                        lane=lane,
                        action="imported_from_chat_log",
                        detail=pending_detail,
                        task_id=pending_engram,
                        source_lane="migration",
                        metadata={"source": "message-history.json"},
                    )
                    count += 1
                    pending_engram = None
                    pending_detail = ""

        return count


def main() -> int:
    """CLI smoke test: post a task to each lane and poll kilo's queue."""
    board = LaneMessageBoard(lane="opencode")

    board.post_task("kilo", "continue", "enggram #1039 — continue needed tasks concurrently", task_id="1039")
    board.post_task("freebuff", "mcp_config_fix", "string command → argv ARRAY", task_id="mcp-001")
    board.post_announcement("LaneMessageBoard initialized — cross-lane task board live")

    tasks = board.poll_tasks("kilo")
    print(f"Kilo tasks: {len(tasks)}")
    for t in tasks:
        print(f"  #{t.task_id} {t.action}: {t.detail[:60]}")

    stats = board.get_stats()
    print(f"Bus stats: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
