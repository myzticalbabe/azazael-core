"""G1 test: Eve prefers the local LLM, falls through cleanly.

_local_answer wraps the bridge; stub the bridge to prove both sides:
a local win returns tagged text, a local failure returns a named reason
(never raises) so callers can fall through to the swarm.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import eve_unleashed.cli as _cli


def test_local_win_returns_tagged_text(monkeypatch):
    class _B:
        def run_local(self, prompt, system=None):
            assert "hello" in prompt
            return {"status": "ok", "result": "hi local",
                    "provider": "llama-server"}
    monkeypatch.setattr("eve_unleashed.interpreter_bridge."
                        "EveInterpreterBridge", _B)
    text, tag = _cli._local_answer("hello local")
    assert text == "hi local" and tag.startswith("local:")


def test_local_failure_names_reason_never_raises(monkeypatch):
    class _B:
        def run_local(self, prompt, system=None):
            return {"status": "error", "error": "no server on :1234"}
    monkeypatch.setattr("eve_unleashed.interpreter_bridge."
                        "EveInterpreterBridge", _B)
    text, reason = _cli._local_answer("hello local")
    assert text is None and "1234" in reason
