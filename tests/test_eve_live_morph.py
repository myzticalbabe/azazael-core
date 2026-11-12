"""G1 tests for src/eve_unleashed/live_morph.py.

The parser is the contract: every documented instruction parses,
anything else is UNUNDERSTOOD and never applied. The server test
speaks two instructions over real HTTP on loopback and checks the
document changed, then undoes one. The page must carry the speech
hook or the voice button is decoration.
"""
import json
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import live_morph as L


def test_parser_covers_documented_verbs():
    assert L.parse_instruction("replace dam with levee")["op"] == "replace"
    assert L.parse_instruction("change night to day")["op"] == "replace"
    assert L.parse_instruction("uppercase in Inspection") == {
        "op": "case", "upper": True, "section": "Inspection",
        "raw": "uppercase in Inspection"}
    assert L.parse_instruction("make steps")["op"] == "steps"
    assert L.parse_instruction("outline")["op"] == "outline"
    assert L.parse_instruction("mutate")["op"] == "mutate"
    assert L.parse_instruction("undo")["op"] == "undo"
    with pytest.raises(ValueError):
        L.parse_instruction("polish it nicely")
    with pytest.raises(ValueError):
        L.parse_instruction("   ")


def test_replace_and_undo_roundtrip():
    st = L.LiveState("The dam holds.\nThe dam stands.\n", "t")
    r = st.instruct("replace dam with levee")
    assert r["ok"] and st.text.count("levee") == 2
    assert r["entry"]["before_lines"] == 2
    r2 = st.instruct("undo")
    assert r2["ok"] and "dam holds" in st.text
    r3 = st.instruct("undo")
    assert not r3["ok"] and r3["entry"]["result"] == "NOTHING_TO_UNDO"


def test_ununderstood_never_applies():
    st = L.LiveState("fixed text\n", "t")
    r = st.instruct("make it better somehow")
    assert not r["ok"] and st.text == "fixed text\n"
    assert st.log[-1]["result"] == "UNUNDERSTOOD"


def test_steps_and_outline_report():
    st = L.LiveState("# Guide\n\n1. Open it\n2. Close it\n", "t")
    r = st.instruct("make steps")
    assert r["ok"] and "1. Open it" in st.text
    st2 = L.LiveState("# Guide\n\ntext here\n", "t")
    r2 = st2.instruct("outline")
    assert r2["ok"] and "Guide" in r2["entry"]["detail"]
    assert st2.text == "# Guide\n\ntext here\n"


def test_server_speaks_over_http():
    st = L.LiveState("The dam holds.\n", "dam")
    server, url = L.serve(st, 0)
    try:
        page = urllib.request.urlopen(url, timeout=10).read().decode()
        assert "SpeechRecognition" in page and "webkitSpeechRecognition" in page
        req = urllib.request.Request(
            url + "api/instruct",
            data=json.dumps({"text": "replace dam with levee"}).encode(),
            headers={"Content-Type": "application/json"})
        res = json.loads(urllib.request.urlopen(req, timeout=10).read())
        assert res["ok"] and "levee" in res["text"]
        doc = json.loads(urllib.request.urlopen(url + "api/doc",
                                                timeout=10).read())
        assert "levee" in doc["text"] and len(doc["log"]) == 1
    finally:
        server.shutdown()
        server.server_close()
