"""
threading_utils — Thread-safety utilities for Python 3.14t free-threading.

Provides:
- @Locked decorator — Wrap instance methods with lock acquisition
- AtomicCounter — Thread-safe counter with increment/decrement/reset
- atomic_property — Descriptor that protects getter/setter with a lock
- ThreadSafeList — List wrapper with thread-safe operations
- SnapshotDict — Dict wrapper with atomic snapshot() for iteration safety

All utilities use stdlib only (threading). Compatible with Python 3.14+.
"""

import threading
from collections.abc import Iterable, MutableSequence
from types import TracebackType
from typing import (
    List,
    Any,
    Callable,
    Generic,
    ParamSpec,
    Self,
    TypeVar,
    cast,
)

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

P = ParamSpec("P")
R = TypeVar("R")
T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")


class _NoLock:
    """No-op lock context manager for disabling locking."""

    __slots__ = ()


    def __enter__(self) -> None:
        pass

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass

def _get_lock(
    obj: Any,
     lock_attr: str) -> threading.Lock | threading.RLock | _NoLock:
    """Get or create a lock attribute on an object."""
    if lock_attr == "_no_lock":
        return _NoLock()
    lock = getattr(obj, lock_attr, None)
    if lock is None:
        lock = threading.RLock()
        setattr(obj, lock_attr, lock)
    return lock
def Locked(
    lock_attr: str = "_lock") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator that wraps a function/method with lock acquisition.

    Args:
        lock_attr: Name of the lock attribute on the instance (default: "_lock").
                   Use "_no_lock" to disable locking.

    Example:
        class MyClass:
            _lock = threading.RLock()

            @Locked()
            def safe_method(self) -> None:                ...  # Runs with self._lock held
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            obj = args[0] if args else None
            lock = _get_lock(obj, lock_attr)
            with lock:
                return func(*args, **kwargs)
        return wrapper
    return decorator
class AtomicCounter:
    """Thread-safe counter with increment, decrement, and reset.

    Can be used as a context manager for grouped operations.

    Example:
        counter = AtomicCounter(initial=5)
        counter.increment()   # -> 6
        counter.decrement(3)  # -> 3
        counter.value         # -> 3
        counter.reset()       # -> 0
    """

    __slots__ = ("_value", "_lock")


    def __init__(self, initial: int = 0) -> None:
        self._value = initial
        self._lock = threading.RLock()

    def increment(self, amount: int = 1) -> int:
        """Increment counter by amount. Returns new value."""
        with self._lock:
            self._value += amount
            return self._value

    def decrement(self, amount: int = 1) -> int:
        """Decrement counter by amount. Returns new value."""
        with self._lock:
            self._value -= amount
            return self._value

    def reset(self) -> int:
        """Reset counter to 0. Returns previous value."""
        with self._lock:
            prev = self._value
            self._value = 0
            return prev

    @property
    def value(self) -> int:
        with self._lock:
            return self._value

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass

class atomic_property(Generic[T]):
    """Descriptor that protects getter and setter with a lock.

    Uses the instance's _lock attribute by default.

    Example:
        class MyClass:
            _lock = threading.RLock()

            @atomic_property()
            def count(self) -> int:                return self._count
            @count.setter            def count(self, value: int) -> None:                self._count = value
    """

    __slots__ = ("_lock_attr", "_default", "_name", "_fget", "_fset")
    def __init__(self, lock_attr: str = "_lock", default: T | None = None) -> None:
        self._lock_attr = lock_attr
        self._default = default
        self._name = ""
        self._fget: Callable[[Any], T] | None = None
        self._fset: Callable[[Any, T], None] | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, obj: Any, objtype: type | None = None) -> T:
        if obj is None:
            return cast(T, self)
        lock = _get_lock(obj, self._lock_attr)
        with lock:
            if self._fget:
                return self._fget(obj)
            return cast(T, getattr(obj, f"_{self._name}", self._default))

    def __set__(self, obj: Any, value: T) -> None:
        lock = _get_lock(obj, self._lock_attr)
        with lock:
            if self._fset:
                self._fset(obj, value)
            else:
                setattr(obj, f"_{self._name}", value)

    def getter(self, fget: Callable[[Any], T]) -> Self:
        """Set the getter function (used as @prop.getter)."""
        self._fget = fget
        return self

    def setter(self, fset: Callable[[Any, T], None]) -> Self:
        """Set the setter function (used as @prop.setter)."""
        self._fset = fset
        return self

class ThreadSafeList(MutableSequence[T]):
    """Thread-safe list with all operations protected by a lock.

    Provides .sorted_copy() for safe iteration without holding the lock.

    Example:
        safe_list = ThreadSafeList([1, 2, 3])
        safe_list.append(4)
        snapshot = safe_list.sorted_copy()  # Thread-safe copy
    """

    __slots__ = ("_list", "_lock")
    def __init__(self, iterable: Iterable[T] | None = None) -> None:
        self._list: list[T] = list(iterable) if iterable is not None else []
        self._lock = threading.RLock()

    def __getitem__(self, index: int) -> T:
        with self._lock:
            return self._list[index]

    def __len__(self) -> int:
        with self._lock:
            return len(self._list)

    def __setitem__(self, index: int, value: T) -> None:
        with self._lock:
            self._list[index] = value

    def __delitem__(self, index: int) -> None:
        with self._lock:
            del self._list[index]

    def insert(self, index: int, value: T) -> None:
        with self._lock:
            self._list.insert(index, value)

    def append(self, value: T) -> None:
        with self._lock:
            self._list.append(value)

    def extend(self, values: Iterable[T]) -> None:
        with self._lock:
            self._list.extend(values)

    def remove(self, value: T) -> None:
        with self._lock:
            self._list.remove(value)

    def pop(self, index: int = -1) -> T:
        with self._lock:
            return self._list.pop(index)

    def sorted_copy(self) -> list[T]:
        """Return a sorted copy of the list (snapshot for iteration safety)."""
        with self._lock:
            # Elements are orderable by caller contract; the generic cannot prove it.
            return sorted(cast("list[Any]", self._list))

    def snapshot(self) -> list[T]:
        """Return a shallow copy of the list (safe for iteration)."""
        with self._lock:
            return list(self._list)

class SnapshotDict(dict[K, V]):
    """Thread-safe dict with atomic snapshot() for iteration safety.

    All mutation operations require the lock. snapshot() returns
    a shallow copy for safe iteration without holding the lock.

    Example:
        sd = SnapshotDict()
        sd["key"] = "value"
        for k, v in sd.snapshot().items():
            ...  # Safe iteration
    """

    __slots__ = ("_lock",)
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lock = threading.RLock()

    def snapshot(self) -> dict[K, V]:
        """Return a shallow copy for safe iteration."""
        with self._lock:
            return dict(self)

    def __setitem__(self, key: K, value: V) -> None:
        with self._lock:
            super().__setitem__(key, value)

    def __delitem__(self, key: K) -> None:
        with self._lock:
            super().__delitem__(key)

    def clear(self) -> None:
        with self._lock:
            super().clear()

    def pop(self, key: K, *args: Any) -> V:
        with self._lock:
            return super().pop(key, *args)

    def popitem(self) -> tuple[K, V]:
        with self._lock:
            return super().popitem()

    def update(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            super().update(*args, **kwargs)
