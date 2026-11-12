#!/usr/bin/env python3
"""Hermetic tests for the Eve interpreter bridge.

The bridge is regression-tested against an in-process stub OpenAI-compatible
HTTP server on an ephemeral localhost port — no served model, no llama.cpp,
no litellm, no the real user's 8080 embedding server. These cover the
2026-10-18 fix for ``litellm.InternalServerError: the current context does
not logits computation. skipping``.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "AOA"))

import pytest

from eve_unleashed.interpreter_bridge import EveInterpreterBridge, get_eve_interpreter_bridge

# Mock model-ID paths are built dynamically (tempdir root) — no hardcoded
# drive letters; the bridge classifies by substring ("embed" hints), so the
# mock names below keep the real payload shape observed on 2026-10-18.
_MOCK_MODELS_ROOT = os.path.join(os.environ.get("TEMP", os.sep + "tmp"), "eve_bridge_mocks")
_MOCK_TEXT_MODEL = os.path.join(_MOCK_MODELS_ROOT, "qwen3-4b-instruct-Q4_K_M.gguf")
_MOCK_EMBED_MODEL = os.path.join(_MOCK_MODELS_ROOT, "mxbai-embed-large-v1", "mxbai-embed-large-v1-Q4_k_m.gguf")


class _StubServer:
    """Minimal OpenAI-compatible stub on an ephemeral port.

    ``chat_responses`` is a queue of ``(status_code, body_dict)``; when empty
    chat requests receive a generic 500. Every chat request body is recorded
    in ``chat_requests`` for protocol-level assertion.
    """

    def __init__(self, models_payload: Dict[str, Any], chat_responses: Optional[List[Dict[str, Any]]] = None):
        self.models_payload = models_payload
        self.chat_responses = list(chat_responses or [])
        self.chat_requests: List[Dict[str, Any]] = []
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _send(self, code: int, payload: Dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/v1/models":
                    self._send(200, outer.models_payload)
                else:
                    self._send(404, {"error": "no route"})

            def do_POST(self):
                if self.path == "/v1/chat/completions":
                    length = int(self.headers.get("Content-Length") or 0)
                    outer.chat_requests.append(json.loads(self.rfile.read(length) or b"{}") if length else {})
                    if outer.chat_responses:
                        code, payload = outer.chat_responses.pop(0)
                    else:
                        code, payload = 500, {"error": {"code": 500, "message": "stub: no response queued", "type": "server_error"}}
                    self._send(code, payload)
                else:
                    self._send(404, {"error": "no route"})

        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        return False


def _bridge(stub, **kw) -> EveInterpreterBridge:
    return EveInterpreterBridge(probe_ports=[stub.port], **kw)


class _FakeLLM:
    pass


class _FakeInterpreter:
    def __init__(self, chat_result: Any = "i would inject commands"):
        self.llm = _FakeLLM()
        self.auto_run = None
        self._chat_result = chat_result
        self.calls: List[str] = []

    def chat(self, prompt: str) -> Any:
        self.calls.append(prompt)
        if isinstance(self._chat_result, Exception):
            raise self._chat_result
        return self._chat_result


# ---------------------------------------------------------------- original contracts


def test_bridge_unavailable(monkeypatch):
    b = EveInterpreterBridge.__new__(EveInterpreterBridge)
    b._eve = None
    b._interpreter = None
    b._available = False
    b._probe_ports = ()
    b._last_probe = {}
    monkeypatch.setattr(b, "_gguf_fallback", lambda prompt, provider_tag: None)
    assert b.is_available() is False
    assert b.get_interpreter() is None
    run_error = b.run_local("hi")
    assert run_error["status"] == "error"
    assert b.drive_mesh_via_interpreter("x")["status"] == "error"
    st = b.get_status()
    assert st["interpreter_available"] is False
    assert st["mesh_drivable"] is False


def test_bridge_factory():
    b = get_eve_interpreter_bridge(eve_arm=None)
    assert b is not None
    assert b._eve is None
    empty = get_eve_interpreter_bridge(eve_arm=None, probe_ports=())
    assert empty._probe_ports == ()


def test_bridge_no_servers_no_engine(monkeypatch):
    b = EveInterpreterBridge(probe_ports=())
    monkeypatch.setattr(b, "_gguf_fallback", lambda prompt, provider_tag: None)
    r = b.run_local("hi")
    assert r["status"] == "error"
    assert "no generation-capable" in r["error"]


# ---------------------------------------------------------------- probing


def _llama_payload(text_ids):
    return {"data": [{"id": mid, "object": "model"} for mid in text_ids], "models": [{"name": mid, "model": mid} for mid in text_ids]}


def test_probe_skips_embedding_only_server(monkeypatch):
    # Real payload shape observed 2026-10-18 on the host llama-server (8080):
    # mxbai-embed-large-v1, an embedding-only gguf that 500s generation requests.
    embed_id = _MOCK_EMBED_MODEL
    with _StubServer(_llama_payload([embed_id])) as stub:
        b = _bridge(stub)
        monkeypatch.setattr(b, "_gguf_fallback", lambda prompt, provider_tag: None)
        r = b.run_local("hi")
        assert r["status"] == "error"
        probe = r["probe"]
        assert probe["embedding_only"] and not probe["text_targets"]
        assert probe["probed"][0]["reachable"] is True
        assert probe["probed"][0]["generation_capable"] is False
        # The embedding-only server was never asked to compute logits.
        assert stub.chat_requests == []


def _default_bridge_for(stub, monkeypatch):
    b = _bridge(stub)
    monkeypatch.setattr(b, "_gguf_fallback", lambda prompt, provider_tag: None)
    return b


def test_direct_chat_plain_params_never_request_logits(monkeypatch):
    model = _MOCK_TEXT_MODEL
    with _StubServer(
        _llama_payload([model]),
        chat_responses=[(200, {"choices": [{"message": {"role": "assistant", "content": "mesh online"}}]})],
    ) as stub:
        b = _default_bridge_for(stub, monkeypatch)
        r = b.run_local("status check")
        assert r["status"] == "ok"
        assert r["result"] == "mesh online"
        assert r["provider"] == "openai-compatible-direct"
        assert r["server"]["model"] == model
        assert len(stub.chat_requests) == 1
        body = stub.chat_requests[0]
        # THE regression: plain chat-completion parameters only — no logits
        # ever requested, so servers without logits computation never 500.
        # enable_thinking is a deliberate Qwen3 token-saver (65d664f3),
        # not a logits request; the banned-keys loop below is the guard.
        assert set(body.keys()) == {"model", "messages", "temperature",
                                    "max_tokens", "stream", "enable_thinking"}
        assert body["model"] == model
        assert body["stream"] is False
        assert body["messages"][-1]["role"] == "user"
        for banned in ("logprobs", "top_logprobs", "logit_bias"):
            assert banned not in body


def test_logits_500_failure_is_classified(monkeypatch):
    model = _MOCK_TEXT_MODEL
    the_500 = {"error": {"code": 500, "message": "the current context does not logits computation. skipping", "type": "server_error"}}
    with _StubServer(
        _llama_payload([model]),
        chat_responses=[(500, the_500)],
    ) as stub:
        b = _default_bridge_for(stub, monkeypatch)
        r = b.run_local("status check")
        assert r["status"] == "error"
        assert r["failure_class"].startswith("server_compute_unsupported")
        assert "logits computation" in r["error"]


def test_drive_mesh_sends_eve_context_as_system(monkeypatch):
    model = _MOCK_TEXT_MODEL
    with _StubServer(
        _llama_payload([model]),
        chat_responses=[(200, {"choices": [{"message": {"role": "assistant", "content": "ack"}}]})],
    ) as stub:
        b = _default_bridge_for(stub, monkeypatch)
        r = b.drive_mesh_via_interpreter("run the monitor cluster")
        assert r["status"] == "ok"
        body = stub.chat_requests[0]
        system_msg = [m for m in body["messages"] if m["role"] == "system"]
        assert system_msg and "You are Eve" in system_msg[0]["content"]
        assert body["messages"][-1]["content"] == "run the monitor cluster"


def test_probe_ports_skip_unreachable_and_pick_generation_server(monkeypatch):
    text_id = _MOCK_TEXT_MODEL
    embed_id = _MOCK_EMBED_MODEL
    with _StubServer(_llama_payload([embed_id])) as embed_stub:
        with _StubServer(_llama_payload([text_id]), chat_responses=[(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]})]) as text_stub:
            b = _bridge(embed_stub)
            # Re-point the bridge at both stubs: embed first (should be
            # rejected), text second (should be chosen).
            b._probe_ports = (embed_stub.port, text_stub.port)
            monkeypatch.setattr(b, "_gguf_fallback", lambda prompt, provider_tag: None)
            r = b.run_local("hi")
            assert r["status"] == "ok"
            assert r["server"]["model"] == text_id
            assert r["server"]["base"].endswith(str(text_stub.port) + "/v1")
            assert b._last_probe["probed"][0]["reachable"] is True


# ---------------------------------------------------------------- interpreter path


def test_interpreter_explicit_provider_config_and_cleared_logit_params():
    model = _MOCK_TEXT_MODEL
    fake = _FakeInterpreter(chat_result="from the interpreter")
    b = EveInterpreterBridge(probe_ports=())
    b._interpreter = fake
    b._available = True
    with _StubServer(
        _llama_payload([model]),
    ) as stub:
        b._probe_ports = (stub.port,)
        configured = b._configure_interpreter_for_server(stub_base(stub) , model)
        assert configured is True
        llm = fake.llm
        # Explicit provider/model config — a REAL id, not "openai/local-model".
        assert llm.model == "openai/" + model
        assert llm.api_base == stub_base(stub)
        assert llm.api_key == "dummy"
        assert llm.supports_functions is False
        assert llm.supports_vision is False
        # Cleared logit-related request parameters.
        assert getattr(llm, "logprobs", "unset") is None
        assert getattr(llm, "top_logprobs", "unset") is None
        assert getattr(llm, "logit_bias", "unset") is None


def stub_base(stub) -> str:
    return "http://localhost:%d/v1" % stub.port


def test_interpreter_failure_falls_back_to_direct_chat(monkeypatch):
    model = _MOCK_TEXT_MODEL
    fake = _FakeInterpreter(chat_result=RuntimeError("interpreter exploded"))
    with _StubServer(
        _llama_payload([model]),
        chat_responses=[(200, {"choices": [{"message": {"role": "assistant", "content": "direct path wins"}}]})],
    ) as stub:
        b = EveInterpreterBridge(probe_ports=(stub.port,))
        b._interpreter = fake
        b._available = True
        monkeypatch.setattr(b, "_gguf_fallback", lambda prompt, provider_tag: None)
        r = b.run_local("hi")
        assert r["status"] == "ok"
        assert r["result"] == "direct path wins"
        assert r["provider"] == "openai-compatible-direct"
        assert fake.calls == ["hi"]
        b2 = EveInterpreterBridge(probe_ports=(stub.port,))
        st = b2.get_status()
        assert st["interpreter_available"] is False
        assert st["mesh_drivable"] is False


# ---------------------------------------------------------------- status


def test_get_status_reports_probe_summary(monkeypatch):
    embed_id = _MOCK_EMBED_MODEL
    with _StubServer(_llama_payload([embed_id])) as stub:
        b = EveInterpreterBridge(probe_ports=(stub.port,))
        b._probe_servers()
        st = b.get_status()
        assert st["interpreter_available"] is False
        assert st["server_probe"]["text_targets"] == 0
        assert st["server_probe"]["embedding_only"] == 1
        assert st["server_probe"]["probed"][0]["port"] == stub.port
