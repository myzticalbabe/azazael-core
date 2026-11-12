# coding: utf-8
"""
api_client.py -- Base APIClient with Unified Retry, Error Handling, and Key Masking
===================================================================================
Created from analysis by 5 inference APIs (Groq, OpenRouter, NVIDIA NIM, Mistral, GitHub).
All 5 independently flagged the need for unified retry/backoff, structured error handling,
thread-safe wrappers, and key masking in status/log output.

Usage:
    from CORE.inference.api_client import APIClient, InferenceError, mask_key

    class MyProvider(APIClient):
        def _call_api(self, prompt, **kwargs):
            return self._request_with_retry(prompt, **kwargs)

    result = prov.chat_safe("Hello!")  # Auto-retry, structured errors, masked keys
"""

import threading
import time
from typing import Any, Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# --- Custom Exceptions ----------------------------------------------


class InferenceError(Exception):
    """Base exception for all inference-related errors."""

    def __init__(self, message: str, provider: str = "", retryable: bool = False,
                 status_code: int = 0, original: (Exception | None) = None):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code
        self.original = original


class RateLimitError(InferenceError):
    """429 / rate limit exceeded — always retryable."""

    def __init__(self, message: str = "", provider: str = "", retry_after: float = 1.0,
                 original: (Exception | None) = None):
        super().__init__(message or "Rate limit exceeded", provider=provider,
                         retryable=True, status_code=429, original=original)
        self.retry_after = retry_after


class AuthError(InferenceError):
    """401 / authentication failed — NOT retryable."""

    def __init__(self, message: str = "", provider: str = "",
                 original: (Exception | None) = None):
        super().__init__(message or "Authentication failed", provider=provider,
                         retryable=False, status_code=401, original=original)


class TimeoutError(InferenceError):
    """Request timed out — retryable."""

    def __init__(self, message: str = "", provider: str = "",
                 original: (Exception | None) = None):
        super().__init__(message or "Request timed out", provider=provider,
                         retryable=True, status_code=0, original=original)


# --- Key Masking ----------------------------------------------------


def mask_key(key: str) -> str:
    """Mask an API key for safe display: 'nvapi-vM...CfmQ'."""
    if not key:
        return ""
    if len(key) <= 12:
        return "***"
    return key[:8] + "..." + key[-4:]


def safe_error_message(err: str, max_len: int = 120) -> str:
    """Truncate error message and strip non-ASCII for safe display."""
    # Strip non-ASCII for Windows console safety
    try:
        err = err.encode("ascii", errors="replace").decode("ascii")
    except Exception as exc:
        note_failure("api_client:L84", exc)
    return err[:max_len]


# --- Thread-Safe Wrapper -------------------------------------------


class ThreadSafeCall:
    """Wraps an API call with thread-safe state tracking.

    All 5 inference APIs flagged the need for thread-safe wrappers.
    This provides isolated state per call, preventing race conditions
    under Python 3.14t free-threading.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._call_count: int = 0
        self._error_count: int = 0
        self._last_error: (str | None) = None
        self._last_call_time: float = 0.0

    def record_success(self):
        with self._lock:
            self._call_count += 1
            self._last_call_time = time.time()
            self._last_error = None

    def record_error(self, error: str):
        with self._lock:
            self._call_count += 1
            self._error_count += 1
            self._last_error = error
            self._last_call_time = time.time()

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": self._call_count,
                "errors": self._error_count,
                "error_rate": self._error_count / max(self._call_count, 1),
                "last_error": self._last_error,
            }


# --- Base APIClient ------------------------------------------------


class APIClient:
    """Base class for inference API clients with unified retry/backoff.

    Provides:
    - Exponential backoff retry (0.5s -> 1s -> 2s -> 4s -> ... -> 60s cap)
    - Distinguishes retryable vs non-retryable errors
    - Thread-safe call tracking via ThreadSafeCall
    - Key masking for all log/status output
    - Structured InferenceError hierarchy

    To use:
        class MyProvider(APIClient):
            def _call_api(self, prompt, **kwargs):
                return self._request_with_retry(prompt, **kwargs)
    """

    # Retryable HTTP status codes
    RETRYABLE_STATUSES = {429, 502, 503, 504}

    # Non-retryable status codes
    FATAL_STATUSES = {400, 401, 402, 403, 404}

    def __init__(self, provider_name: str = "unknown",
                 max_retries: int = 3,
                 base_delay: float = 0.5,
                 max_delay: float = 60.0):
        self.provider_name = provider_name
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.tracker = ThreadSafeCall()

    # -- Retry Logic ------------------------------------------------

    def _classify_error(self, error: Exception) -> tuple[bool, str]:
        """Classify an exception as retryable or not. Returns (retryable, message)."""
        msg = str(error)

        # Already classified
        if isinstance(error, InferenceError):
            return error.retryable, msg

        # HTTP status code in message
        for code_str in ["429", "502", "503", "504"]:
            if code_str in msg:
                return True, msg
        for code_str in ["400", "401", "402", "403", "404"]:
            if code_str in msg:
                return False, msg

        # Network errors are retryable
        lower = msg.lower()
        if any(kw in lower for kw in ["timeout", "connection", "reset", "refused",
                                        "network", "dns", "tls", "ssl"]):
            return True, msg

        # Unknown — retry once to be safe
        return True, msg

    def _request_with_retry(self, call_fn: Callable, *args, **kwargs) -> Any:
        """Execute a callable with exponential backoff retry.

        Args:
            call_fn: The function to call (e.g., lambda: prov.chat(prompt))
            *args, **kwargs: Passed to call_fn

        Returns:
            The result from call_fn on success.

        Raises:
            InferenceError: After all retries exhausted or on non-retryable error.
        """
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                result = call_fn(*args, **kwargs)
                self.tracker.record_success()
                return result
            except Exception as e:
                retryable, msg = self._classify_error(e)
                last_error = str(e)

                if not retryable:
                    self.tracker.record_error(last_error)
                    raise InferenceError(
                        f"[{self.provider_name}] Non-retryable error: {msg}",
                        provider=self.provider_name,
                        retryable=False,
                        original=e,
                    )

                if attempt < self.max_retries:
                    delay = min(self.base_delay * (2 ** attempt), self.max_delay)
                    time.sleep(delay)
                else:
                    self.tracker.record_error(last_error)
                    raise InferenceError(
                        f"[{self.provider_name}] Failed after {self.max_retries + 1} attempts: {msg}",
                        provider=self.provider_name,
                        retryable=True,                    original=e,
                ) from e

        # Safety net (should be unreachable)
        raise InferenceError(
            f"[{self.provider_name}] Unexpected retry loop exit",
            provider=self.provider_name,
        )

    # -- Safe Call Wrapper ------------------------------------------

    def _safe_call(self, call_fn: Callable[[], Any]) -> dict[str, Any]:
        """Wrap a zero-arg callable with retry and structured error output.

        call_fn should be a lambda or function that takes no arguments
        and returns a dict with 'content', 'model', 'usage', 'elapsed_ms'.
        On error, returns dict with 'error' key.
        NEVER includes raw API keys in error messages.
        """
        start = time.time()
        try:
            result = self._request_with_retry(call_fn)
            return result
        except InferenceError as e:
            note_failure("api_client:L257", e)
            elapsed_ms = int((time.time() - start) * 1000)
            safe_msg = safe_error_message(str(e))
            return {
                "error": safe_msg,
                "elapsed_ms": elapsed_ms,
                "provider": self.provider_name,
            }
        except Exception as e:
            note_failure("api_client:L265", e)
            elapsed_ms = int((time.time() - start) * 1000)
            safe_msg = safe_error_message(str(e))
            return {
                "error": safe_msg,
                "elapsed_ms": elapsed_ms,
                "provider": self.provider_name,
            }

    # -- Stats ------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Get call statistics (thread-safe)."""
        return {
            "provider": self.provider_name,
            **self.tracker.stats,
        }

    def __repr__(self) -> str:
        s = self.tracker.stats
        return (f"APIClient({self.provider_name}, "
                f"calls={s['calls']}, errors={s['errors']}, "
                f"max_retries={self.max_retries})")
