# coding: utf-8
"""PSYCHE Phase 7 gate: the channel streams and never drops silently.

- One line in, one line out, for valid AND malformed input: a bad line produces
  an error record with the same id and the session stays up.
- `id` is echoed; when absent it defaults to the line number so a caller can
  still correlate.
- The budget is reported per turn, and an overrun is counted rather than hidden.
- Truncation of a long message is stated in the payload.
- A subprocess round-trip proves the protocol actually streams through stdio,
  which is the only form of "live" that can be verified here.
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE import rt_agent as R
from PSYCHE.intuition import IntuitionIndex
from PSYCHE.network import PersonNetwork
from PSYCHE.rt_channel import SessionChannel


def _channel(budget_ms=50.0, max_message_chars=400):
    net = PersonNetwork()
    net.add_edge("threat_appraisal", "avoidance", 0.7, support=12)
    index = IntuitionIndex(net)
    for _ in range(5):
        index.absorb({"threat_appraisal": 0.7, "avoidance": 0.6}, "threat-loop")
        index.absorb({"approach": 0.6, "arousal": 0.2}, "settled")
    loop = R.TherapeuticLoop(net, index, budget_ms=budget_ms)
    return SessionChannel(loop, max_message_chars=max_message_chars)


def test_valid_request_gets_a_full_response():
    out = _channel().handle({"id": 7, "text": "hard day"})
    assert out["id"] == 7
    assert out["stance"] in R.STANCE_TEMPLATES
    assert out["risk"] in R.RISK_LEVELS
    assert out["latency_ms"] >= 0.0
    assert out["within_budget"] is True


def test_crisis_text_arrives_as_the_safety_stance():
    out = _channel().handle({"id": 1, "text": "I want to die"})
    assert out["stance"] == "safety"
    assert out["risk"] == "crisis"
    assert "988" in out["message"]


def test_malformed_json_does_not_end_the_session():
    channel = _channel()
    stdin = io.StringIO('{"id": 1, "text": "hello"}\nnot json at all\n'
                        '{"id": 3, "text": "still here"}\n')
    stdout = io.StringIO()
    stats = channel.serve(stdin, stdout)
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(lines) == 3
    assert lines[1]["error"].startswith("line 2 is not JSON")
    assert lines[2]["id"] == 3
    assert stats.handled == 2 and stats.errors == 1


def test_bad_field_types_are_answered_not_raised():
    channel = _channel()
    assert "error" in channel.handle({"id": 2, "text": 42})
    assert "error" in channel.handle({"id": 3, "text": "ok", "appraisal": "nope"})
    assert "error" in channel.handle({"id": 4, "text": "ok", "dt_s": "soon"})
    assert channel.stats.errors == 3


def test_missing_id_defaults_to_the_line_number():
    channel = _channel()
    stdout = io.StringIO()
    channel.serve(io.StringIO('{"text": "one"}\n{"text": "two"}\n'), stdout)
    ids = [json.loads(line)["id"] for line in stdout.getvalue().splitlines()]
    assert ids == [1, 2]


def test_over_budget_turns_are_counted_not_hidden():
    channel = _channel(budget_ms=0.0)
    out = channel.handle({"id": 1, "text": "anything"})
    assert out["within_budget"] is False
    assert channel.stats.over_budget == 1


def test_truncation_is_stated_in_the_payload():
    out = _channel(max_message_chars=20).handle({"id": 1, "text": "I want to die"})
    assert out["message"].endswith("[truncated]")


def test_blank_lines_are_skipped_without_a_response():
    channel = _channel()
    stdout = io.StringIO()
    channel.serve(io.StringIO('\n\n{"id": 9, "text": "hi"}\n\n'), stdout)
    lines = stdout.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == 9


def test_protocol_streams_through_a_real_subprocess():
    """A real subprocess, because a handler called in-process proves nothing about
    stdio framing.

    The script path comes from the imported module, not from `ROOT/src/PSYCHE`: in
    the public tree the package is `nurse_ratchet.psyche` and there is no `src`, so
    a path-built command is a monorepo-only test that ships green here and red
    there. Same defect class as the corpus path in the instruments.

    The PYTHONPATH entry is derived from the TOP-LEVEL package instead of by
    walking up a fixed number of directories. `script.parent.parent` is right for
    `src/PSYCHE` (one package level) and wrong for `nurse_ratchet.psyche` (two),
    which is exactly how the public tree's copy failed while this one passed.
    """
    import importlib.util

    from PSYCHE import rt_channel as C

    script = Path(C.__file__).resolve()
    assert script.name == "rt_channel.py", script  # R is rt_agent; naming it wrong
    top_level = C.__name__.split(".")[0]
    location = importlib.util.find_spec(top_level).submodule_search_locations[0]
    env = dict(os.environ)                       # runs the wrong script silently
    env["PYTHONPATH"] = str(Path(location).resolve().parent)
    env.pop("AZAZAEL_MEMORY_DIR", None)
    proc = subprocess.run(
        [sys.executable, str(script), "--demo", "--turns", "3"],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-800:]
    records = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert len(records) == 3
    assert records[0]["id"] == 1
    assert {r["stance"] for r in records} <= set(R.STANCE_TEMPLATES)


def test_stats_are_serialisable():
    channel = _channel()
    channel.handle({"id": 1, "text": "I want to die"})
    payload = json.loads(json.dumps(channel.stats.to_dict()))
    assert payload["handled"] == 1
    assert payload["safety_turns"] == 1
