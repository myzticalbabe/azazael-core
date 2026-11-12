"""Local-model client for grantforge: OpenAI-compatible HTTP, stdlib only.

Why this exists instead of `grantforge/config.py`'s defaults: the tree's config
points at :18090 (nothing listens there) and the live local model is llama.cpp's
llama-server on :8081, which answers the OpenAI chat shape but lists models as
`{"models":[{"name": ...}]}` rather than `{"data":[{"id": ...}]}`. A client that
parses one shape reports "no model" against a healthy endpoint — a silent
default, which is the defect class this module refuses to reproduce.

Every failure is named: `LLMUnavailable` (could not get an answer) and
`LLMBadReply` (got an answer that was not the JSON object asked for). Neither
is ever raised as an empty string, and neither is retried past the point where
a retry could help.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8081"
DEFAULT_TIMEOUT = 120.0
CHAT_SUFFIX = "/chat/completions"


class LLMError(RuntimeError):
    """Base for every failure in this module."""


class LLMUnavailable(LLMError):
    """The endpoint could not answer: connection refused, timeout, or 5xx."""


class LLMBadReply(LLMError):
    """The endpoint answered, but not with the JSON object that was required."""


def normalise(base):
    """(root, chat_url) from any of the forms a user or a config might hold.

    Accepted: `host:port`, `http://host:port`, `.../v1`, `.../v1/chat/completions`.
    A base that already carries a version path keeps it; one that does not gets
    `/v1`, which is what llama-server, Ollama, LM Studio and vLLM all serve.
    """
    raw = (base or DEFAULT_BASE).strip().rstrip("/")
    if "://" not in raw:
        raw = "http://" + raw
    if raw.endswith(CHAT_SUFFIX):
        raw = raw[: -len(CHAT_SUFFIX)]
    if not raw.endswith("/v1"):
        raw += "/v1"
    return raw, raw + CHAT_SUFFIX


def extract_json(text):
    """The first balanced `{...}` object in `text`, or None.

    Scans with string/escape awareness rather than a regex: a model that wraps
    its object in prose or a ```json fence is common, and a brace-counting regex
    truncates on any `}` inside a string, which then reads as "invalid JSON" and
    blames the model for the parser's defect.
    """
    if not isinstance(text, str):
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_str:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_str = False
                continue
            if char == '"':
                in_str = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except ValueError as exc:
                        _ = exc  # a nested object may still parse further along
                        break
                    return parsed if isinstance(parsed, dict) else None
        start = text.find("{", start + 1)
    return None


class LocalLLM:
    """A bounded client for one local endpoint.

    `retries` counts *additional* attempts. Only 5xx, timeouts and connection
    errors are retried: a 400 means the request is wrong and re-sending it
    produces the same wrong answer twice.
    """

    def __init__(self, base=None, model=None, timeout=None, retries=2, backoff=0.5):
        self.base = base or os.environ.get("GRANTFORGE_LLM_URL") or DEFAULT_BASE
        self.root, self.chat_url = normalise(self.base)
        self.model = model
        self.timeout = float(timeout if timeout is not None else
                             os.environ.get("GRANTFORGE_LLM_TIMEOUT") or DEFAULT_TIMEOUT)
        self.retries = max(0, int(retries))
        self.backoff = float(backoff)
        self.last = {}
        self.faults = []

    # -- transport ---------------------------------------------------------
    def _post(self, payload):
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.chat_url, data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST")
        attempts = 0
        while True:
            attempts += 1
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                elapsed = time.monotonic() - started
                self.last = {"url": self.chat_url, "attempts": attempts,
                             "seconds": round(elapsed, 3)}
                return json.loads(raw.decode("utf-8", "replace"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                retryable = exc.code >= 500
                self.faults.append("http %s: %s" % (exc.code, detail))
                if not retryable or attempts > self.retries:
                    raise LLMUnavailable(
                        "%s returned HTTP %s after %d attempt(s): %s"
                        % (self.chat_url, exc.code, attempts, detail)) from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
                self.faults.append("%s: %s" % (type(exc).__name__, exc))
                if attempts > self.retries:
                    raise LLMUnavailable(
                        "%s unreachable after %d attempt(s): %s"
                        % (self.chat_url, attempts, exc)) from exc
            except ValueError as exc:
                self.faults.append("bad json from endpoint: %s" % exc)
                raise LLMBadReply("%s answered with invalid JSON: %s"
                                  % (self.chat_url, exc)) from exc
            time.sleep(self.backoff * attempts)

    # -- discovery ---------------------------------------------------------
    def models(self):
        """Every model id the endpoint lists, from either shape, or [] only if it says so."""
        url = self.root + "/models"
        try:
            with urllib.request.urlopen(url, timeout=min(self.timeout, 15.0)) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout,
                TimeoutError, ValueError, ConnectionError) as exc:
            self.faults.append("models: %s: %s" % (type(exc).__name__, exc))
            raise LLMUnavailable("could not list models at %s: %s" % (url, exc)) from exc
        found = []
        for entry in payload.get("models") or []:
            name = entry.get("name") or entry.get("model") if isinstance(entry, dict) else None
            if name:
                found.append(name)
        for entry in payload.get("data") or []:
            ident = entry.get("id") or entry.get("name") if isinstance(entry, dict) else None
            if ident:
                found.append(ident)
        if isinstance(payload.get("model"), str):
            found.append(payload["model"])
        seen, unique = set(), []
        for name in found:
            if name not in seen:
                seen.add(name)
                unique.append(name)
        self._discovered = unique
        return unique

    def pick_model(self):
        if self.model:
            return self.model
        listed = getattr(self, "_discovered", None)
        if listed is None:
            listed = self.models()
        self.model = listed[0] if listed else "local"
        return self.model

    # -- generation --------------------------------------------------------
    def complete(self, messages, temperature=0.0, max_tokens=1024, timeout=None):
        """One chat completion. Returns the assistant text, never a default."""
        payload = {"model": self.pick_model(), "messages": list(messages),
                   "temperature": float(temperature), "max_tokens": int(max_tokens),
                   "stream": False}
        if timeout is not None:
            self.timeout = float(timeout)
        answer = self._post(payload)
        try:
            text = answer["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMBadReply("no choices[0].message.content in reply: %s"
                              % json.dumps(answer)[:300]) from exc
        if not isinstance(text, str):
            raise LLMBadReply("assistant content was %s, not text" % type(text).__name__)
        self.last["usage"] = answer.get("usage") or {}
        self.last["model"] = answer.get("model") or payload["model"]
        self.last["finish_reason"] = (answer["choices"][0] or {}).get("finish_reason")
        return text

    def json_object(self, messages, required, max_tokens=1024, temperature=0.0):
        """A dict carrying every key in `required`, with ONE repair attempt.

        Two measured facts shape this, both from llama-server on :8081:

        * The contract must be a LEADING system turn. A JSON instruction appended
          after a long user message was ignored and the model wrote markdown prose
          (`**Significance (300 words):** ...`), while the same instruction as a
          leading system turn produced the object.
        * Truncation is not the model's error. `finish_reason: "length"` means the
          answer was cut mid-object; re-sending the same budget cuts it in the same
          place. The budget is raised before the answer is judged malformed, and a
          doubly truncated reply is reported as truncation, naming the budget to fix
          — not as "the model cannot produce JSON".
        """
        instruction = ("Reply with a single JSON object and nothing else. "
                       "Required keys: %s." % ", ".join(sorted(required)))
        convo = [{"role": "system", "content": instruction}] + list(messages)
        attempts = []
        budget = int(max_tokens)
        repaired = False
        for attempt in range(3):
            text = self.complete(convo, temperature=temperature, max_tokens=budget)
            truncated = self.last.get("finish_reason") == "length"
            parsed = extract_json(text)
            defect = None
            if truncated:
                defect = "reply truncated at a %d-token budget" % budget
            elif parsed is None:
                defect = "reply contained no JSON object"
            else:
                missing = [key for key in required if key not in parsed]
                if missing:
                    defect = "missing key(s): %s" % ", ".join(missing)
            if defect is None:
                attempts.append("ok")
                self.last["json_attempts"] = len(attempts)
                return parsed
            attempts.append(defect)
            if truncated:
                if attempt == 0:
                    budget *= 3
                    continue
                break
            if not repaired:
                repaired = True
                convo = [{"role": "system", "content": instruction}] + list(messages) + [
                    {"role": "assistant", "content": text[:2000]},
                    {"role": "user", "content":
                     "That reply was rejected: %s. Return only a corrected JSON object "
                     "with every required key." % defect},
                ]
                continue
            break
        self.last["json_attempts"] = len(attempts)
        raise LLMBadReply("no usable JSON object after %d attempt(s): %s"
                          % (len(attempts), "; ".join(attempts)))

    def probe(self, roundtrip=True):
        """What `doctor` needs: url, models, model, and a measured round trip."""
        started = time.monotonic()
        models = self.models()
        record = {"base": self.base, "root": self.root, "models": models,
                  "model": self.pick_model() if models else None, "ok": bool(models)}
        if roundtrip and models:
            text = self.complete([{"role": "user", "content": "Reply with: ok"}],
                                 max_tokens=8, temperature=0.0)
            record["reply"] = text.strip()[:40]
        record["roundtrip_ms"] = round((time.monotonic() - started) * 1000, 1)
        return record
