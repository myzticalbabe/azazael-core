# coding: utf-8
"""
Azazael Dynamic Logger v1.0
Real-time, persistent logging with automatic flush
"""

import os
import threading
from pathlib import Path
from datetime import datetime
from typing import Any, List
from collections import deque
import queue

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class LogLevel:
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"


class DynamicLogger:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._log_queue: "queue.Queue" = queue.Queue()
        self._log_history: "deque" = deque(maxlen=1000)
        self._level = LogLevel.DEBUG
        self._quiet = False
        self._output_enabled = True
        self._writer_thread = None
        self._setup_logging()

    def _setup_logging(self):
        try:
            self._start_background_writer()
        except Exception as e:
            import sys
            print(f"[AZAZAEL LOGGER] Failed to start background writer: {e}", file=sys.stderr)

    def _start_background_writer(self):
        self._writer_thread = threading.Thread(
            target=self._background_write_loop, daemon=True
        )
        self._writer_thread.start()

    def _background_write_loop(self):
        while True:
            try:
                msg_data = self._log_queue.get(timeout=0.1)
                if msg_data is None:
                    break
                self._write_to_file(msg_data)
            except queue.Empty as exc:
                note_failure("AzazaelDynamicLogger:L57", exc)
                continue
            except Exception as e:
                import sys
                print(f"[AZAZAEL LOGGER] Background write error: {e}", file=sys.stderr)

    def _write_to_file(self, msg_data: dict[str, Any]):
        try:
            log_dir = Path(__file__).parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "azazael_dynamic.log"
            with open(log_path, "a", encoding="utf-8") as f:
                timestamp = msg_data.get("timestamp", datetime.now().isoformat())
                level = msg_data.get("level", "INFO")
                message = msg_data.get("message", "")
                f.write(f"{timestamp} | {level:7} | {message}\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            import sys
            print(f"[AZAZAEL LOGGER] File write error: {e}", file=sys.stderr)

    def set_level(self, level: str):
        self._level = level

    def enable_quiet(self):
        self._quiet = True

    def disable_output(self):
        self._output_enabled = False

    def enable_output(self):
        self._output_enabled = True

    def _format_message(self, message: str, level: str) -> str:
        symbols = {
            LogLevel.DEBUG: "[?]",
            LogLevel.INFO: "[+]",
            LogLevel.WARN: "[!]",
            LogLevel.ERROR: "[-]",
            LogLevel.SUCCESS: "[*]",
        }
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        symbol = symbols.get(level, "[?]")
        return f"{symbol} {timestamp} {message}"

    def log(self, message: str, level: str = LogLevel.INFO, tag: str = ""):
        if self._quiet:
            return

        timestamp = datetime.now().isoformat()
        full_message = f"{tag} {message}".strip() if tag else message

        log_data = {"timestamp": timestamp, "level": level, "message": full_message}

        self._log_history.append(log_data)

        if self._output_enabled:
            formatted = self._format_message(full_message, level)
            print(formatted, flush=True)

        self._log_queue.put(log_data)

    def debug(self, message: str, tag: str = ""):
        self.log(message, LogLevel.DEBUG, tag)

    def info(self, message: str, tag: str = ""):
        self.log(message, LogLevel.INFO, tag)

    def warn(self, message: str, tag: str = ""):
        self.log(message, LogLevel.WARN, tag)

    def error(self, message: str, tag: str = ""):
        self.log(message, LogLevel.ERROR, tag)

    def success(self, message: str, tag: str = ""):
        self.log(message, LogLevel.SUCCESS, tag)

    def section(self, title: str):
        sep = "=" * 60
        self.log(sep, LogLevel.INFO)
        self.log(f" {title} ", LogLevel.INFO)
        self.log(sep, LogLevel.INFO)

    def progress(self, current: int, total: int, message: str = "", width: int = 30):
        percent = current / total if total > 0 else 0
        filled = int(width * percent)
        bar = "#" * filled + "-" * (width - filled)
        self.log(
            f"{bar} {percent * 100:5.1f}% ({current}/{total}) {message}",
            LogLevel.INFO,
            "PROG",
        )

    def get_history(self, limit: int = 100) -> list:
        return list(self._log_history)[-limit:]

    def force_flush(self):
        while not self._log_queue.empty():
            try:
                msg = self._log_queue.get_nowait()
                self._write_to_file(msg)
            except queue.Empty as exc:
                note_failure("AzazaelDynamicLogger:L159", exc)
                break


logger = DynamicLogger()


def get_logger() -> DynamicLogger:
    return logger


def log(message: str, level: str = LogLevel.INFO, tag: str = ""):
    logger.log(message, level, tag)


def info(message: str, tag: str = ""):
    logger.info(message, tag)


def warn(message: str, tag: str = ""):
    logger.warn(message, tag)


def error(message: str, tag: str = ""):
    logger.error(message, tag)


def success(message: str, tag: str = ""):
    logger.success(message, tag)


def section(title: str):
    logger.section(title)
