"""Eve native intuition -- memo, host prior, unknown, dream replay.

No network: socket connect and urlopen raise throughout, so any egress
attempt fails the test instead of reaching a host.
"""

import socket
import urllib.request

import pytest

from eve_unleashed import intuition as _in
from eve_unleashed import live_log as _l


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("network egress in intuition test")
    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def _answer(path, url, rung, state="ok", kind=None):
    _l.emit("fetch", "answer", path=path, url=url, rung=rung,
            state=state, kind=kind)


def test_unknown_without_memory(tmp_path):
    g = _in.intuit("https://never-seen.example/x",
                   log_path=str(tmp_path / "l.jsonl"))
    assert g["state"] == "unknown" and g["confidence"] == 0.0
    assert g["predicted_rung"] is None


def test_exact_memo_predicts_rung(tmp_path):
    p = str(tmp_path / "l.jsonl")
    _answer(p, "https://memo.example/page", "tls-impersonation")
    g = _in.intuit("https://memo.example/page", log_path=p)
    assert g["state"] == "guess" and g["confidence"] == 0.9
    assert g["predicted_rung"] == "tls-impersonation"


def test_memo_of_wall_predicts_blocked(tmp_path):
    p = str(tmp_path / "l.jsonl")
    _answer(p, "https://walled.example/", "stealth-render",
            state="blocked", kind="imperva")
    g = _in.intuit("https://walled.example/", log_path=p)
    assert g["state"] == "blocked" and g["confidence"] == 0.7


def test_host_prior_modal_rung(tmp_path):
    p = str(tmp_path / "l.jsonl")
    _answer(p, "https://host.example/a", "plain")
    _answer(p, "https://host.example/b", "plain")
    _answer(p, "https://host.example/c", "render", state="blocked",
            kind="captcha")
    g = _in.intuit("https://host.example/fresh", log_path=p)
    assert g["state"] == "guess"
    assert g["predicted_rung"] == "plain"
    assert g["confidence"] == pytest.approx(2 / 3, abs=0.01)


def test_dream_pairs_guesses_with_answers(tmp_path):
    p = str(tmp_path / "l.jsonl")
    _l.emit("fetch", "intuition", path=p, url="https://d.example/1",
            guess="guess", predicted="plain")
    _answer(p, "https://d.example/1", "plain")
    _l.emit("fetch", "intuition", path=p, url="https://d.example/2",
            guess="guess", predicted="plain")
    _answer(p, "https://d.example/2", "render")
    _l.emit("fetch", "intuition", path=p, url="https://d.example/3",
            guess="blocked", predicted="render")
    _answer(p, "https://d.example/3", "render", state="blocked",
            kind="captcha")
    rep = _in.dream(log_path=p)
    assert rep["pairs"] == 3 and rep["hits"] == 2  # blocked wall read: hit
    assert rep["hit_rate"] == 0.667  # round(2/3, 3), as dream() reports
    assert rep["by_rung"]["plain"] == {"pairs": 2, "hits": 1}
    assert rep["by_rung"]["render"] == {"pairs": 1, "hits": 1}
    assert rep["hosts"]["d.example"] == {"fetches": 3, "answered": 2,
                                            "salience": 4.0}


def test_empty_url_rejected(tmp_path):
    with pytest.raises(ValueError):
        _in.intuit("  ", log_path=str(tmp_path / "l.jsonl"))
