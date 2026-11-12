# coding: utf-8
"""M-48: the reaper's second office -- an inference, not just a frame.

The property that matters: an answer is only a claim about the world if the MODEL, the
ENDPOINT and the INSTRUCTION are the ones that were meant. Each of the three can be wrong
while everything else passes, and none of them raises an exception.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
SENTINEL = HERE / "tools" / "inference_sentinel.py"
CHAIN = HERE / "Project_FLAMING_SWORD" / "tools" / "chain_server.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sentinel = _load(SENTINEL, "inference_sentinel_under_test")


def test_the_selftest_proves_every_refusal() -> None:
    """The tool's own control suite: 7 assertions, including the three refusals."""
    code, lines = sentinel.selftest()
    assert code == 0, "\n".join(lines)
    assert len(lines) == 7, lines
    assert any("unserved model" in line for line in lines)
    assert any("changed instruction" in line for line in lines)
    assert any("no endpoint is exit 2" in line for line in lines)


def test_instruction_sha_reads_a_file_or_the_text(tmp_path: Path) -> None:
    text = "refuse a shard whose golden run does not reproduce"
    file_path = tmp_path / "aio.txt"
    file_path.write_text(text, encoding="utf-8")
    assert sentinel.instruction_sha(str(file_path)) == \
        hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert sentinel.instruction_sha(text) == \
        hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_exit_codes_are_three_and_distinct() -> None:
    """0 held, 1 refused, 2 could not run -- 2 is never folded into 0."""
    assert (sentinel.OK, sentinel.FAILED, sentinel.COULD_NOT_RUN) == (0, 1, 2)


def test_the_context_window_is_never_guessed_from_a_name(tmp_path: Path) -> None:
    """An endpoint that advertises nothing gets a stated absence, not '128k'."""
    payload = {"data": [{"id": "llama3.1:8b"}]}
    assert sentinel.context_window(payload, "llama3.1:8b") is None
    assert sentinel.context_window({"data": [{"id": "m", "context_length": 4096}]},
                                   "m") == 4096


def test_chain_server_echoes_the_instruction_it_used() -> None:
    """Which instruction produced an answer is now nameable, so it can be checked."""
    root = HERE / "Project_FLAMING_SWORD"
    chain = _load(CHAIN, "chain_server_under_test")
    frame = {"prompt": "refuse a shard whose golden run does not reproduce",
             "target": "tools/sword.py",
             "acceptance": "pytest -q exits 0 and prints 98 passed",
             "evidence": "tests/test_defender.py",
             "defends": ["the golden run's state_sha256 token"],
             "must_not_break": ["tests/test_defender.py"],
             "root": str(root)}
    status, body = chain.ask(frame, state_dir=None, model_url="", model_name="")
    assert status == 503, (status, body)
    assert "instruction_sha256" not in body, \
        "a refusal must not look like an answer with a hash"


def test_an_answer_carries_the_sha_of_the_instruction_that_produced_it(monkeypatch) -> None:
    """The whole point of M-48: the answer names its instruction, so it can be checked.

    The sha is taken from the text ACTUALLY handed to the model, captured here by
    replacing ask_model. A hash of anything else -- the prompt, the frame, a template --
    would be a number that looks like a proof and proves nothing.
    """
    root = HERE / "Project_FLAMING_SWORD"
    chain = _load(CHAIN, "chain_server_under_test_2")
    seen = {}

    def capture(aio, prompt, model_url, model_name, api_key="", timeout=0):
        seen["aio"] = aio
        return "ok"

    monkeypatch.setattr(chain, "ask_model", capture)
    status, body = chain.ask(
        {"prompt": "refuse a shard whose golden run does not reproduce",
         "target": "tools/sword.py",
         "acceptance": "pytest -q exits 0 and prints 98 passed",
         "evidence": "tests/test_defender.py",
         "defends": ["the golden run's state_sha256 token"],
         "must_not_break": ["tests/test_defender.py"],
         "root": str(root)}, state_dir=None, model_url="http://stub/v1",
        model_name="stub")
    assert status == 200, body
    assert body["verified"] is False
    assert body["instruction_sha256"] == hashlib.sha256(
        seen["aio"].encode("utf-8")).hexdigest(), "the sha must cover the text sent"
    # and it is a real sha over a real AIO, not a placeholder
    assert len(body["instruction_sha256"]) == 64
    assert "AIO" in seen["aio"]


def test_the_fingerprint_is_appended_not_overwritten(tmp_path: Path) -> None:
    sentinel.record_fingerprint("m1", "http://a/v1", "a" * 64, 8192, str(tmp_path))
    sentinel.record_fingerprint("m1", "http://a/v1", "b" * 64, 8192, str(tmp_path))
    path = tmp_path / sentinel.FINGERPRINTS
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2, lines
    assert lines[-1]["instruction_sha256"] == "b" * 64


def test_an_unreadable_fingerprint_is_refused_not_ignored(tmp_path: Path) -> None:
    """A record you cannot read is not a record that agrees with you."""
    path = tmp_path / sentinel.FINGERPRINTS
    path.write_text("not json\n", encoding="utf-8")
    record, error = sentinel.read_fingerprint(str(tmp_path))
    assert record is None
    assert "not JSON" in error, error


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
