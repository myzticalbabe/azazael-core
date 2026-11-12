"""AOA EventBus - Cross-Arm pub/sub messaging system."""
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Any, Optional
from datetime import datetime
from collections import defaultdict, deque
import threading, uuid

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class EventPriority:
    LOW = 0
    NORMAL = 1
    MEDIUM = 1  # alias for NORMAL
    HIGH = 2
    CRITICAL = 3

@dataclass
class AOEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    event_type: str = ""
    source_arm: str = ""
    target_arm: str = ""
    payload: dict = field(default_factory=dict)
    priority: int = 1
    correlation_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict = field(default_factory=dict)
    def to_dict(self) -> dict:
        return {"event_id":self.event_id,"event_type":self.event_type,"source_arm":self.source_arm,"target_arm":self.target_arm,"payload":self.payload,"priority":self.priority,"correlation_id":self.correlation_id,"timestamp":self.timestamp,"metadata":self.metadata}

class AOEventType:
    ARM_INITIALIZED="arm.initialized"; ARM_SHUTDOWN="arm.shutdown"; ARM_ERROR="arm.error"
    LIBRA_DOC_GENERATED="libra.doc.generated"; LIBRA_TEMPLATE_LOADED="libra.template.loaded"; LIBRA_FILING_COMPLETE="libra.filing.complete"
    AZAZAEL_WORM_EXECUTED="azazael.worm.executed"; AZAZAEL_CLUSTER_COMPLETE="azazael.cluster.complete"; AZAZAEL_ANOMALY_DETECTED="azazael.anomaly.detected"
    CODEX_RENDER_STARTED="codex.render.started"; CODEX_RENDER_COMPLETE="codex.render.complete"; CODEX_VIDEO_READY="codex.video.ready"
    EVE_MODEL_SELECTED="eve.model.selected"; EVE_BENCHMARK_COMPLETE="eve.benchmark.complete"; EVE_INFERENCE_COMPLETE="eve.inference.complete"
    WORKFLOW_STARTED="workflow.started"; WORKFLOW_STEP_COMPLETE="workflow.step.complete"; WORKFLOW_COMPLETE="workflow.complete"; WORKFLOW_FAILED="workflow.failed"
    SESSION_MILESTONE="session.milestone"; SESSION_STARTED="session.started"; SESSION_COMPLETED="session.completed"

class EventBus:
    def __init__(self, max_history=1000):
        self._subscribers=defaultdict(list); self._sinks=[]; self._max_history=max_history; self._lock=threading.Lock()
        # Use a bounded ring (deque with maxlen) for O(1) trimming instead of
        # O(n) list slice. The deque drops the oldest entry automatically when
        # it exceeds maxlen, so publish never reallocates the backing array.
        # Declared once, here, with its real type: a `self._history = None`
        # placeholder made every later read Optional and pushed the None case
        # into publish() and both readers.
        self._history: deque[AOEvent] = deque(maxlen=max_history)
        # `publish` used to `except: pass`, so a subscriber that raised was
        # silently never delivered to -- a broken consumer was indistinguishable
        # from one that legitimately did nothing, with 9 arms subscribed.
        # Delivery is still isolated (one bad consumer must not break the
        # publisher) but the failure is now counted and kept.
        self._cb_errors=[]; self._cb_error_count=0; self._cb_errors_lock=threading.Lock()
    def publish(self, event):
        with self._lock:
            self._history.append(event)
        # Invoke sinks OUTSIDE the lock so a slow tracer cannot stall publishers.
        # Sinks are read-only consumers (tracing, logging, metrics) that observe
        # events but must not block the publish path.
        for sink in list(self._sinks):
            try:
                sink(event)
            except Exception as _e:
                note_failure("event_bus:sink", _e)
        for pattern,cbs in list(self._subscribers.items()):
            if self._match_pattern(pattern, event.event_type):
                for cb in list(cbs):
                    try: cb(event)
                    except Exception as _e:
                        # Keep the LEAF of the qualname. A long
                        # `Class.method` truncated from the right keeps the
                        # class and throws away the identifying method --
                        # which is the only part that says which consumer broke.
                        # Keep the LEAF of the qualname. A long
                        # `Class.method` truncated from the right keeps the
                        # class and throws away the identifying method --
                        # which is the only part that says which consumer broke.
                        note_failure("event_bus:L56", _e)
                        _qn = str(getattr(cb,"__qualname__",None)
                                  or getattr(cb,"__name__",None) or repr(cb))
                        if len(_qn)>80:
                            _qn = "..."+_qn[-77:]
                        with self._cb_errors_lock:
                            self._cb_error_count+=1
                            self._cb_errors.append({
                                "event_type": getattr(event,"event_type","?"),
                                "pattern": pattern,
                                "callback": _qn,
                                "error": "%s: %s"%(type(_e).__name__,str(_e)[:120]),
                            })
                            self._cb_errors=self._cb_errors[-50:]
    def subscribe_sink(self, fn):
        """Register a sink invoked OUTSIDE the lock on every publish.

        Sinks are for tracing, logging, metrics -- read-only consumers that
        must not stall publishers. A slow sink cannot block publish because
        it runs after the lock is released.
        """
        self._sinks.append(fn)
        return fn
    def unsubscribe_sink(self, fn):
        """Remove a previously registered sink."""
        if fn in self._sinks:
            self._sinks.remove(fn)
    def get_callback_errors(self, limit=10):
        with self._cb_errors_lock: return list(self._cb_errors[-limit:])
    def subscribe(self, pattern, cb):
        with self._lock: self._subscribers[pattern].append(cb)
    def unsubscribe(self, pattern, cb):
        with self._lock:
            if pattern in self._subscribers: self._subscribers[pattern]=[c for c in self._subscribers[pattern] if c!=cb]
    def get_history(self, event_type=None, source_arm=None, limit=100):
        with self._lock:
            r = list(self._history)
            if event_type: r = [e for e in r if e.event_type == event_type]
            if source_arm: r = [e for e in r if e.source_arm == source_arm]
            return r[-limit:]
    def get_correlated_events(self, cid):
        with self._lock: return [e for e in list(self._history) if e.correlation_id == cid]
    def clear_history(self):
        with self._lock: self._history.clear()
    @staticmethod
    def _match_pattern(pattern, event_type):
        if pattern==event_type or pattern=="*": return True
        if pattern.endswith(".*") and event_type.startswith(pattern[:-2]): return True
        if pattern.startswith("*.") and event_type.endswith(pattern[2:]): return True
        return False
    @property
    def subscriber_count(self):
        with self._lock: return sum(len(cbs) for cbs in self._subscribers.values())
    @property
    def callback_error_count(self):
        """Subscriber deliveries that raised. Non-zero means a consumer is
        broken and its events are being dropped."""
        with self._cb_errors_lock:
            return self._cb_error_count

    @property
    def history_count(self):
        """Events retained right now (bounded by ``max_history``).

        This is the reader the AOA tests call. A bad merge had spliced its body
        into ``unsubscribe_sink`` (which then returned the error count instead
        of None) and left a second copy unreachable behind the return above.
        """
        with self._lock:
            return len(self._history)

class EventPipeline:
    def __init__(self, bus, name=""): self.bus=bus; self.name=name; self._steps=[]
    def add_step(self, h): self._steps.append(h)
    def execute(self, initial):
        results=[initial]; current=initial
        for i,step in enumerate(self._steps):
            try:
                nxt=step(current)
                if nxt is None: break
                results.append(nxt); current=nxt
            except Exception as e:
                note_failure("event_bus:L119", e)
                results.append(AOEvent(event_type=AOEventType.WORKFLOW_FAILED,source_arm="pipeline."+self.name,payload={"step":i,"error":str(e)},correlation_id=initial.correlation_id,priority=3))
                break
        return results

_gb=None
def get_event_bus():
    global _gb
    if _gb is None: _gb=EventBus()
    return _gb
