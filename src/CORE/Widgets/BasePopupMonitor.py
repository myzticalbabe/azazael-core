# coding: utf-8
"""
BasePopupMonitor — Thread-Safe Tkinter Popup Base Class

Provides a queue.Queue + root.after() polling architecture that enforces
all tkinter widget access from the main thread. Designed for Python 3.14t
free-threading on Windows where tkinter has strict main-thread affinity.

Features:
- queue.Queue for thread-safe message passing from background threads
- root.after(100, process_queue) polling loop on main thread only
- Runtime assertion that tkinter operations run on main_thread()
- QueueMessage dataclass for structured message passing
- SubprocessError dataclass for structured error propagation
- Configurable max queue size to prevent backpressure
- Safe close() via queue message (not direct root.after() from bg thread)
- handle_message() abstract method for subclass customization

Usage:
    class MyMonitor(BasePopupMonitor):
        def handle_message(self, msg):
            if msg.action == "log":
                self._log_to_text_area(msg.payload)
            elif msg.action == "status":
                self._update_progress(msg.payload)

    monitor = MyMonitor("My Title", 800, 600)
    monitor.create_window()  # Must call from main thread
    # From any thread:
    monitor.enqueue("log", "Hello from background thread!")
    monitor.run_loop()  # Blocks on main thread
"""

import tkinter as tk
from tkinter import scrolledtext as tk_scrolledtext  # typeshed: tk.scrolledtext attr unresolved
from tkinter import ttk as tk_ttk  # typeshed: tk.ttk attr unresolved; use tk_ttk.* directly
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class QueueMessage:
    """Structured message for thread-safe tkinter communication."""
    action: str          # 'log', 'status', 'command', 'error', 'render'
    payload: Any = None  # Data payload (string, dict, etc.)
    timestamp: float = field(default_factory=time.time)


@dataclass
class SubprocessError:
    """Structured error from background subprocess execution."""
    component: str        # Component name (e.g., "SYSTEMZ")
    error_type: str       # Exception class name
    message: str          # Error message
    traceback: str = ""   # Full traceback string


class BasePopupMonitor:
    """Thread-safe tkinter popup window base class.

    All tkinter widget access MUST go through the queue. Background
    threads call enqueue(). The main thread processes messages via
    process_queue() which is polled by root.after().

    Subclasses override handle_message() to customize behavior.
    """

    VERSION = "1.0.0"

    def __init__(
        self,
        title: str = "Azazael Monitor",
        width: int = 800,
        height: int = 600,
        max_queue_size: int = 1000,
    ):
        self.title = title
        self.width = width
        self.height = height
        self.q = queue.Queue(maxsize=max_queue_size)
        self.running = False

        # UI elements (populated by create_window)
        self.root: (tk.Tk | None) = None
        self.text_area: (tk.Text | None) = None
        self.progress_bar: (tk_ttk.Progressbar | None) = None
        self.status_label: (tk.Label | None) = None
        self.time_label: (tk.Label | None) = None
        self.start_time: (datetime | None) = None

    # -- Window Creation (MAIN THREAD ONLY) -------------------------

    def create_window(self):
        """Create the tkinter window. MUST be called from main thread."""
        assert (
            threading.current_thread() is threading.main_thread()
        ), "create_window() must run on main thread"

        self.root = tk.Tk()
        self.root.title(self.title)
        self.root.geometry(f"{self.width}x{self.height}")
        self.root.configure(bg="#1a1a2e")

        self._build_header()
        self._build_body()
        self._build_footer()

        self.running = True
        self.start_time = datetime.now()
        self.root.after(100, self._process_queue_internal)

    def _build_header(self):
        """Build header frame with title and status label. Override as needed."""
        header_frame = tk.Frame(self.root, bg="#16213e", pady=10)
        header_frame.pack(fill="x")

        title_label = tk.Label(
            header_frame,
            text=self.title,
            font=("Consolas", 24, "bold"),
            bg="#16213e",
            fg="#00d4ff",
        )
        title_label.pack()

        self.status_label = tk.Label(
            header_frame,
            text="Initializing...",
            font=("Consolas", 12),
            bg="#16213e",
            fg="#7c3aed",
        )
        self.status_label.pack()

        self.progress_bar = tk_ttk.Progressbar(
            self.root,
            mode="determinate",
            length=self.width - 50,
        )
        self.progress_bar.pack(pady=10, padx=25)

        style = tk_ttk.Style()
        style.theme_use("clam")
        style.configure(
            "TProgressbar",
            troughcolor="#1a1a2e",
            thickness=20,
        )

    def _build_body(self):
        """Build scrollable text area. Override as needed."""
        text_frame = tk.Frame(self.root, bg="#1a1a2e", padx=10, pady=10)
        text_frame.pack(fill="both", expand=True)

        self.text_area = tk_scrolledtext.ScrolledText(
            text_frame,
            wrap="word",
            font=("Consolas", 10),
            bg="#0f0f1a",
            fg="#00ff88",
            insertbackground="#00ff88",
        )
        self.text_area.pack(fill="both", expand=True)

    def _build_footer(self):
        """Build footer frame with time label. Override as needed."""
        footer_frame = tk.Frame(self.root, bg="#16213e", pady=5)
        footer_frame.pack(fill="x")

        self.time_label = tk.Label(
            footer_frame,
            text="00:00:00",
            font=("Consolas", 12),
            bg="#16213e",
            fg="#ffffff",
        )
        self.time_label.pack()

    # -- Thread-Safe Queue API --------------------------------------

    def enqueue(self, action: str, payload: Any = None):
        """Thread-safe message submission from ANY thread.

        Args:
            action: Message type ('log', 'status', 'command', 'error', 'render')
            payload: Data payload (string, dict, SubprocessError, etc.)
        """
        try:
            self.q.put_nowait(QueueMessage(action=action, payload=payload))
        except queue.Full as exc:
            # Queue backpressure — drop oldest to make room
            # Queue backpressure — drop oldest to make room
            note_failure("BasePopupMonitor:L193", exc)
            try:
                self.q.get_nowait()
                self.q.put_nowait(QueueMessage(action=action, payload=payload))
            except queue.Empty as exc:
                note_failure("BasePopupMonitor:L198", exc)

    def _safe_log(self, message: str, level: str = "INFO"):
        """Convenience: enqueue a log message."""
        self.enqueue("log", {"message": message, "level": level})

    def _safe_status(self, status: str, progress: int = 0):
        """Convenience: enqueue a status update."""
        self.enqueue("status", {"text": status, "progress": progress})

    # -- Queue Processing (MAIN THREAD ONLY) ------------------------

    def _process_queue_internal(self):
        """Process queued messages. MUST run on main thread.

        Called by root.after() polling loop. Drains all available
        messages from the queue and dispatches them via handle_message().
        Handles 'close' command separately for safe shutdown.
        """
        assert (
            threading.current_thread() is threading.main_thread()
        ), "_process_queue_internal() escaped main thread!"

        try:
            while True:
                msg: QueueMessage = self.q.get_nowait()

                if msg.action == "command" and msg.payload == "close":
                    # Schedule close on main thread after delay
                    delay = 5000  # default 5s
                    if isinstance(msg.payload, dict):
                        delay = msg.payload.get("delay", delay)
                    if self.root is not None:
                        self.root.after(delay, self._close_internal)
                    continue

                self.handle_message(msg)

        except queue.Empty as exc:
            note_failure("BasePopupMonitor:L236", exc)

        if self.running and self.root:
            self.root.after(100, self._process_queue_internal)

    def handle_message(self, msg: QueueMessage):
        """Handle a queued message. Override in subclasses.

        Default implementation handles common message types.
        Subclasses should call super().handle_message() for
        unhandled types or override entirely.

        Args:
            msg: QueueMessage with action and payload
        """
        if msg.action == "log":
            self._handle_log(msg)
        elif msg.action == "status":
            self._handle_status(msg)
        elif msg.action == "error":
            self._handle_error(msg)
        # Unknown actions are silently ignored

    def _handle_log(self, msg: QueueMessage):
        """Render a log message in the text area."""
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        message = payload.get("message", str(msg.payload))
        level = payload.get("level", "INFO")

        if self.text_area:
            timestamp = datetime.now().strftime("%H:%M:%S")
            formatted = f"[{timestamp}] [{level}] {message}"
            self.text_area.insert("end", formatted + "\n")
            self.text_area.see("end")

    def _handle_status(self, msg: QueueMessage):
        """Update status label and progress bar."""
        payload = msg.payload if isinstance(msg.payload, dict) else {}

        if self.status_label and "text" in payload:
            self.status_label.config(text=payload["text"])
        if self.progress_bar and "progress" in payload:
            self.progress_bar["value"] = payload["progress"]
        if self.time_label and self.start_time:
            elapsed = (datetime.now() - self.start_time).total_seconds()
            self.time_label.config(text=f"Elapsed: {elapsed:.1f}s")

    def _handle_error(self, msg: QueueMessage):
        """Render an error in the text area with red coloring."""
        err = msg.payload
        if isinstance(err, SubprocessError):
            self._safe_log(f"[{err.component}] {err.error_type}: {err.message}", "ERROR")

    # -- Lifecycle --------------------------------------------------

    def _close_internal(self):
        """Close the window. Called on main thread only."""
        self.running = False
        if self.root:
            try:
                self.root.quit()
                self.root.destroy()
            except tk.TclError as exc:
                note_failure("BasePopupMonitor:L299", exc) # Window already destroyed

    def close(self):
        """Request window close from any thread (via queue)."""
        self.enqueue("command", "close")

    def run_loop(self):
        """Run tkinter mainloop. MUST be called from main thread."""
        assert (
            threading.current_thread() is threading.main_thread()
        ), "run_loop() must run on main thread"
        if self.root:
            self.root.mainloop()

    def __repr__(self) -> str:
        return f"BasePopupMonitor(title={self.title!r}, size={self.width}x{self.height})"
