"""Hermetic tests for OpenAIProvider.structured_chat (no keys, no network).

A stub client stands in for the OpenAI SDK: scripted per-call behaviour
proves each ladder rung (schema -> plain -> forced tool -> honest error)
plus thinking-preamble stripping. Both interpreters.
"""
import sys
import os
import types
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from CORE.inference.providers import (
    OpenAIProvider,
    ProviderConfig,
)

SCHEMA = {"type": "object",
          "properties": {"a": {"type": "integer"}},
          "required": ["a"]}


def _msg(content="", tool_calls=None):
    message = types.SimpleNamespace(content=content,
                                    tool_calls=tool_calls or [],
                                    finish_reason="stop")
    usage = types.SimpleNamespace(prompt_tokens=1, completion_tokens=1,
                                  total_tokens=2)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(
        message=message, finish_reason="stop")],
        model="stub-model", usage=usage)


def _tool_call(args):
    fn = types.SimpleNamespace(arguments=args)
    return [types.SimpleNamespace(function=fn)]


class _StubCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def create(self, **kwargs):
        self.seen.append(kwargs)
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class _StubClient:
    def __init__(self, script):
        self.chat = types.SimpleNamespace(completions=_StubCompletions(script))


def _provider(script):
    # Unique provider name per test: hermetic isolation without touching
    # the shared circuit breaker's internals.
    pname = "teststruct-%s" % uuid.uuid4().hex[:8]
    prov = OpenAIProvider(config=ProviderConfig(
        provider=pname, model="stub-model",
        base_url="http://127.0.0.1:9/v1", requires_auth=False,
        rate_limit_rpm=1000000))
    # One shared stub: every ladder rung draws the NEXT scripted behaviour
    # (a fresh client per rung would replay the script from the top).
    stub = _StubClient(script)
    prov._get_client = lambda: stub  # noqa: SLF001 - hermetic stub
    return prov


def test_schema_direct_parsed():
    prov = _provider([_msg('{"a": 1}')])
    out = prov.structured_chat("hi", SCHEMA)
    assert out["parsed"] is True, out
    assert out["content"] == {"a": 1}, out
    assert out["path"] == "schema", out


def test_thinking_preamble_stripped():
    prov = _provider([_msg("<think>reasoning trace here</think>\n{\"a\": 2}")])
    out = prov.structured_chat("hi", SCHEMA)
    assert out["parsed"] is True, out
    assert out["content"] == {"a": 2}, out


def test_markdown_fence_stripped():
    prov = _provider([_msg("```json\n{\"a\": 3}\n```")])
    out = prov.structured_chat("hi", SCHEMA)
    assert out["parsed"] is True, out
    assert out["content"] == {"a": 3}, out


def test_plain_fallback_on_schema_rejection():
    prov = _provider([Exception("response_format unsupported"),
                      _msg('{"a": 4}')])
    out = prov.structured_chat("hi", SCHEMA)
    assert out["parsed"] is True, out
    assert out["path"] == "plain", out


def test_forced_tool_fallback():
    prov = _provider([Exception("no schema"),
                      Exception("no plain"),
                      _msg("", tool_calls=_tool_call('{"a": 5}'))])
    out = prov.structured_chat("hi", SCHEMA)
    assert out["parsed"] is True, out
    assert out["path"] == "tool", out
    assert out["content"] == {"a": 5}, out


def test_total_failure_is_honest_error():
    prov = _provider([Exception("down"), Exception("down"),
                      Exception("down")])
    out = prov.structured_chat("hi", SCHEMA)
    assert out["parsed"] is False, out
    assert "error" in out, out
    assert "content" not in out or out.get("content") in ("", None), out


def test_prose_is_never_success():
    prov = _provider([_msg("Sure! The answer is seven, obviously."),
                      _msg("Still prose, no braces at all")])
    out = prov.structured_chat("hi", SCHEMA, force_tool_fallback=False)
    assert out["parsed"] is False, out
    assert "error" in out, out
