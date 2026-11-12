"""guard.py — the runtime boundary: fetched text is refused as an argument.

Why this file exists
--------------------
The other four modules need the caller to plumb labels. Real agents do not: they
read a page, then **type** what they read into the next tool call. There is no
label on a string literal. That is the actual injection path in this repository —
`spec-fetched-data-not-instruction` in the evals spec, and every "the agent pasted
the page into a command" incident.

So this module does not ask the caller to declare taint. It remembers what was
fetched and refuses an outbound argument that **reproduces it**:

* `TaintLedger.register(text, source)` stores the normalized text of the source
  (bounded), plus every **URL** in it at a much lower length floor — a bare URL is
  exactly what an injection needs to carry out of the page and is usually shorter
  than a sentence.
* Detection is **shingle membership**: any `SHINGLE`-character stretch of an
  argument that also appears anywhere in the stored text is a copy. This is the
  important design choice. Storing "sentences" instead (the first version) missed
  two real cases the tests caught immediately: a page whose sentences contain
  decimals splits at `$12,500.00`, and a quote that starts mid-sentence is not a
  sentence at all. A fixed-size slide has no alignment to be wrong about.
* `check_call(tool, args)` refuses a copy (`verbatim-quote`) or a fetched URL
  (`fetched-destination`) when the argument names a destination or the tool can
  act on the world. A copy into a *reading* tool is allowed — quoting a page back
  into a search is not exfiltration.
* The ledger is durable: the copy happens in a later turn, often in a later
  process, than the fetch. It is written atomically under a lock, and an
  unreadable ledger reports itself as **corrupt** rather than empty, because
  "empty" means "nothing was ever fetched" and would silently allow every copy
  after it.

What this deliberately is not: a semantic filter. A paraphrase is allowed, and a
test asserts it. Claiming otherwise would be the same wishful thinking as
prompting for safety; this is a **copy detector**, which is precisely the
capability an injection has to exercise.

`SHINGLE` is the false-positive dial and is a constant, not a parameter, so a
caller cannot quietly raise it to make a refusal go away — changing it is a
reviewed edit to this file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:
    from CORE.atomic_store import atomic_write_json, file_lock
except ImportError:  # package imported from a tree without CORE on the path
    atomic_write_json = None  # type: ignore[assignment]
    file_lock = None  # type: ignore[assignment]

from .capabilities import Label, Labeled, TRUST, label_of, origin
from .policy import Policy, PolicyViolation, ToolSpec, Verdict, default_policy

#: A copied stretch shorter than this is not evidence of a copy.
SHINGLE = 40
#: Normalized text kept per source. Past this a page is truncated, and the
#: truncation is reported in `stats()` rather than passed off as full coverage.
MAX_TEXT_PER_SOURCE = 120_000
#: URL floor: a bare URL IS the exfiltration payload, so it needs a lower bound.
MIN_TOKEN = 12
MAX_TOKENS_PER_SOURCE = 60
MAX_SOURCES = 50

_WS = re.compile(r"\s+")
_URL = re.compile(r"https?://[^\s<>\"')\]]{4,300}", re.IGNORECASE)

_QUOTE_RULE = "verbatim-quote"
_TOKEN_RULE = "fetched-destination"


def _norm(text: str) -> str:
    """Whitespace-collapsed and casefolded: the shape a copy keeps."""
    return _WS.sub(" ", unicodedata.normalize("NFC", text or "")).strip().casefold()


def _hash(span: str) -> int:
    return int.from_bytes(
        hashlib.blake2b(span.encode("utf-8"), digest_size=8).digest(), "big")


def _shingles(text: str, size: int = SHINGLE) -> set[int]:
    """Every `size`-character window of `text`, hashed. Alignment-free recall."""
    if len(text) < size:
        return set()
    return {_hash(text[i:i + size]) for i in range(len(text) - size + 1)}


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for url in _URL.findall(text or ""):
        token = _norm(url.rstrip(".,;"))
        if len(token) < MIN_TOKEN or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= MAX_TOKENS_PER_SOURCE:
            break
    return out


def default_ledger_path() -> Path:
    """`azazael/state/security/taint_ledger.json` — state/ is gitignored."""
    root = Path(__file__).resolve().parents[3]
    return root / "state" / "security" / "taint_ledger.json"


@dataclass
class TaintLedger:
    """What this machine has read from sources it does not command."""

    path: Path | None = None
    session: str = "default"
    max_sources: int = MAX_SOURCES
    max_text: int = MAX_TEXT_PER_SOURCE
    texts: dict[str, str] = field(default_factory=dict)
    tokens: dict[str, list[str]] = field(default_factory=dict)
    truncated: list[str] = field(default_factory=list)
    corrupt: bool = False
    hits: int = 0
    misses: int = 0
    _loaded: bool = False
    _index: dict[str, set[int]] = field(default_factory=dict)

    # -- persistence --------------------------------------------------------
    def _resolve(self) -> Path:
        return Path(self.path) if self.path else default_ledger_path()

    def load(self, *, force: bool = False) -> "TaintLedger":
        if self._loaded and not force:
            return self
        path = self._resolve()
        self.texts = {}
        self.tokens = {}
        self.corrupt = False
        self._index = {}
        if path.exists():
            payload: Any = None
            unreadable = False
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                unreadable = True
            if unreadable or not isinstance(payload, dict):
                # Visible, not silent: an unreadable ledger that reported "clean"
                # would allow every copy from that point on.
                self.corrupt = True
                self._loaded = True
                return self
            session = payload.get(self.session)
            if isinstance(session, dict):
                texts = session.get("texts", {})
                tokens = session.get("tokens", {})
                if isinstance(texts, dict):
                    self.texts = {k: v for k, v in texts.items() if isinstance(v, str)}
                if isinstance(tokens, dict):
                    self.tokens = {k: [s for s in v if isinstance(s, str)]
                                   for k, v in tokens.items() if isinstance(v, list)}
        self._loaded = True
        return self

    def save(self) -> None:
        path = self._resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    payload = existing
            except (ValueError, OSError):
                payload = {}
        payload[self.session] = {"texts": self.texts, "tokens": self.tokens}
        if len(payload) > 4:
            for stale in [k for k in payload if k != self.session][: len(payload) - 4]:
                payload.pop(stale, None)
        if atomic_write_json is None:  # pragma: no cover - stripped tree
            path.write_text(json.dumps(payload), encoding="utf-8")
            return
        lock = path.with_suffix(".lock")
        if file_lock is None:  # pragma: no cover - atomic_store provides both
            atomic_write_json(path, payload, "camel.guard")
            return
        with file_lock(lock, "camel.guard"):
            atomic_write_json(path, payload, "camel.guard")

    # -- ingestion ----------------------------------------------------------
    def register(self, text: str, *, source: str) -> Label:
        """Remember `text` as untrusted from `source`; return its label."""
        if not source:
            raise ValueError("register() needs a source name")
        if not isinstance(text, str) or not text.strip():
            return origin(source)
        self.load()
        normalized = _norm(text)
        if len(normalized) > self.max_text:
            if source not in self.truncated:
                self.truncated.append(source)
            normalized = normalized[: self.max_text]
        if len(self.texts) >= self.max_sources and source not in self.texts:
            # Evict the oldest: a ledger that stops recording at the cap would
            # report "clean" for everything fetched after it filled up.
            oldest = next(iter(self.texts))
            self.texts.pop(oldest, None)
            self.tokens.pop(oldest, None)
            self._index.pop(oldest, None)
        previous = self.texts.get(source, "")
        self.texts[source] = (previous + " " + normalized).strip()[: self.max_text]
        new_tokens = _tokens(text)
        if new_tokens:
            merged = self.tokens.setdefault(source, [])
            known = set(merged)
            for token in new_tokens:
                if token not in known:
                    known.add(token)
                    merged.append(token)
            self.tokens[source] = merged[: MAX_TOKENS_PER_SOURCE]
        self._index.pop(source, None)
        self.save()
        return origin(source)

    def forget(self, source: str | None = None) -> int:
        self.load()
        if source is None:
            removed = len(self.texts)
            self.texts = {}
            self.tokens = {}
        else:
            removed = 1 if self.texts.pop(source, None) is not None else 0
            self.tokens.pop(source, None)
        self._index = {}
        self.save()
        return removed

    # -- detection ----------------------------------------------------------
    def _index_for(self, source: str) -> set[int]:
        cached = self._index.get(source)
        if cached is None:
            cached = _shingles(self.texts.get(source, ""))
            self._index[source] = cached
        return cached

    def find_quote(self, value: Any) -> tuple[str, str] | None:
        """(source, copied_tail) when `value` reproduces stored text, else None."""
        if not isinstance(value, str) or len(value) < SHINGLE:
            return None
        self.load()
        haystack = _norm(value)
        for source in self.texts:
            index = self._index_for(source)
            if not index:
                continue
            for i in range(len(haystack) - SHINGLE + 1):
                if _hash(haystack[i:i + SHINGLE]) in index:
                    self.hits += 1
                    return (source, haystack[i:i + 120])
        self.misses += 1
        return None

    def find_token(self, value: Any) -> tuple[str, str] | None:
        """(source, url) when `value` contains a URL fetched from a source."""
        if not isinstance(value, str) or len(value) < MIN_TOKEN:
            return None
        self.load()
        haystack = _norm(value)
        for source, tokens in self.tokens.items():
            for token in tokens:
                if token and token in haystack:
                    self.hits += 1
                    return (source, token)
        return None

    # -- reporting ----------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        self.load()
        return {
            "session": self.session,
            "sources": len(self.texts),
            "chars": sum(len(v) for v in self.texts.values()),
            "tokens": sum(len(v) for v in self.tokens.values()),
            "truncated": list(self.truncated),
            "hits": self.hits,
            "misses": self.misses,
            "corrupt": self.corrupt,
            "path": str(self._resolve()),
        }


_DEFAULT_LEDGERS: dict[tuple[str, str], TaintLedger] = {}


def ledger_for(session: str | None = None, path: Path | None = None) -> TaintLedger:
    """Process-wide ledger for a session (default from `CAMEL_SESSION`)."""
    name = session or os.environ.get("CAMEL_SESSION") or "default"
    key = (name, str(path or ""))
    ledger = _DEFAULT_LEDGERS.get(key)
    if ledger is None:
        ledger = TaintLedger(path=path, session=name)
        _DEFAULT_LEDGERS[key] = ledger
    return ledger


def register_untrusted(text: str, *, source: str,
                       session: str | None = None,
                       ledger: TaintLedger | None = None) -> Label:
    """Public ingress: everything fetched from outside lands here first."""
    store = ledger or ledger_for(session)
    return store.register(text, source=source)


def _raw(value: Any) -> Any:
    return value.value if isinstance(value, Labeled) else value


def check_call(tool: str, args: Mapping[str, Any] | None = None, *,
               selection: Label = TRUST,
               policy: Policy | None = None,
               ledger: TaintLedger | None = None) -> Verdict:
    """Decide one call across both defences. Never raises; returns the verdict.

    Order matters. The copy checks run **before** the label policy so a copied
    page is refused as `verbatim-quote` / `fetched-destination`, which name the
    source and the copied text, instead of as a generic tainted argument. When
    nothing was copied the call still goes through the label policy, so a tainted
    `Labeled` argument supplied by a program is caught by P2/P3.
    """
    pol = policy or default_policy()
    store = ledger or ledger_for()
    spec: ToolSpec = pol.spec_for(tool)
    args = args or {}

    labeled: dict[str, Labeled] = {}
    for name, value in args.items():
        raw = _raw(value)
        label = label_of(value)
        quote = store.find_quote(raw)
        if quote:
            source, copied = quote
            label = label | origin("quote:" + source)
            if name in spec.destinations or spec.sensitive:
                return Verdict(
                    False, tool, _QUOTE_RULE,
                    "argument %r reproduces %d characters fetched from %s; a "
                    "source must not supply the argument of %s (copied: %r)"
                    % (name, len(copied), source, tool, copied[:60]),
                    (name,), selection.describe(), (source,))
        token = store.find_token(raw)
        if token:
            source, url = token
            label = label | origin("quote:" + source)
            if name in spec.destinations or spec.sensitive:
                return Verdict(
                    False, tool, _TOKEN_RULE,
                    "argument %r contains the URL %r fetched from %s; a fetched "
                    "source must not supply a destination or an outbound payload"
                    % (name, url[:80], source),
                    (name,), selection.describe(), (source,))
        labeled[name] = Labeled(raw, label)

    return pol.evaluate(tool, labeled, selection=selection)


def guard_call(tool: str, args: Mapping[str, Any] | None = None, *,
               selection: Label = TRUST,
               policy: Policy | None = None,
               ledger: TaintLedger | None = None) -> Verdict:
    """`check_call`, but a refusal raises. This is what a dispatcher calls."""
    verdict = check_call(tool, args, selection=selection, policy=policy,
                         ledger=ledger)
    if not verdict.allowed:
        raise PolicyViolation(verdict)
    return verdict


__all__ = [
    "MIN_TOKEN", "SHINGLE", "TaintLedger", "check_call", "default_ledger_path",
    "guard_call", "ledger_for", "register_untrusted",
]
