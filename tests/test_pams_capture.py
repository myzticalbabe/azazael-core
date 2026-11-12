# coding: utf-8
"""The session-boundary capture must store REAL producer rows.

The PAMS conversation pipeline has been dead since 2026-05-27 (supervisor:
"pams conversation capture dead (0 messages)"): after the Codebuff client
(the row's own model_id='Codebuff' tells that story) stopped being the
writer, nothing drove `ConversationDatabase.store_message`. The capture
module now wires that producer call to the mandated session boundaries.
These tests pin the properties that make a silent gap impossible again:

  1. open stores a REAL start row through ConversationDatabase (session +
     conversation + message, same schema the MCP server reads) and writes
     a linkage file;
  2. close attaches the summary to the SAME session, consumes ids from the
     linkage file, and removes the file;
  3. a missing model id skips with a NAMED reason and writes nothing (the
     server refuses defaults; a silent default-id write would be exactly
     the identity pollution the separation discipline forbids);
  4. close without a linkage session or an explicit --session-id skips
     with a named reason instead of guessing;
  5. AZAZAEL_PAMS_CAPTURE=0 disables capture with a named skip.

Every DB is a tmp_path instance created by the producer's own
initialize_tables - never the production store.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path
import pytest

# Skip tests if persistent_ai_memory is not available
try:
    import persistent_ai_memory
    HAS_PERSISTENT_AI_MEMORY = True
except ImportError:
    HAS_PERSISTENT_AI_MEMORY = False

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "pams_capture.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("pams_capture_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clean_env(monkeypatch):
    """Force the identity knobs that target code tests must not inherit."""
    monkeypatch.delenv("AZAZAEL_MODEL_ID", raising=False)
    monkeypatch.delenv("AZAZAEL_AGENT", raising=False)
    monkeypatch.delenv("AZAZAEL_PAMS_CAPTURE", raising=False)


def _counts(db_path):
    c = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    out = {
        "messages": c.execute("select count(*) from messages").fetchone()[0],
        "sessions": c.execute("select count(*) from sessions").fetchone()[0],
        "conversations": c.execute(
            "select count(*) from conversations").fetchone()[0],
        "roles": [r[0] for r in c.execute(
            "select role from messages order by created_at")],
        "session_ids": [r[0] for r in c.execute(
            "select distinct session_id from conversations")],
        "contents": [r[0] for r in c.execute("select content from messages")],
    }
    c.close()
    return out


@pytest.mark.skipif(not HAS_PERSISTENT_AI_MEMORY, reason="persistent_ai_memory not installed")
def test_open_stores_start_message_and_writes_linkage(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    mod = _load_tool()
    db = str(tmp_path / "conversations.db")
    link = mod._linkage_path("t-agent", str(tmp_path / "state"))
    r = mod.open_capture("fix the bus", agent="t-agent", user_id="clint",
                         model_id="glm-test", db_path=db,
                         state_dir=str(tmp_path / "state"))
    assert r["status"] == "stored", r
    assert Path(str(r["linkage"])) == link
    assert json.loads(Path(str(r["linkage"])).read_text(
        encoding="utf-8"))["session_id"] == r["session_id"]
    c = _counts(db)
    assert c["messages"] == 1 and c["sessions"] == 1 and c["conversations"] == 1
    assert c["roles"] == ["user"]
    assert c["contents"][0].startswith("SESSION START [t-agent]:")


@pytest.mark.skipif(not HAS_PERSISTENT_AI_MEMORY, reason="persistent_ai_memory not installed")
def test_close_attaches_summary_to_same_session(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    mod = _load_tool()
    db = str(tmp_path / "conversations.db")
    state = str(tmp_path / "state")
    r1 = mod.open_capture("bus task", agent="t-agent", user_id="clint",
                          model_id="glm-test", db_path=db, state_dir=state)
    assert r1["status"] == "stored", r1
    # No explicit ids: the linkage file fills them - the real CLI shape.
    r2 = mod.close_capture("bus fixed at HEAD", agent="t-agent",
                           db_path=db, state_dir=state)
    assert r2["status"] == "stored", r2
    assert r2["session_id"] == r1["session_id"]
    assert not (tmp_path / "state" / "pams_session_t-agent.json").exists()
    c = _counts(db)
    assert c["messages"] == 2 and c["sessions"] == 1
    assert c["roles"] == ["user", "assistant"]
    assert c["session_ids"] == [r1["session_id"]]
    assert c["contents"][1] == "bus fixed at HEAD"


def test_open_without_model_id_skips_with_named_reason(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    mod = _load_tool()
    db = str(tmp_path / "conversations.db")
    r = mod.open_capture("bus task", agent="t-agent", user_id="clint",
                         model_id=None, db_path=db,
                         state_dir=str(tmp_path / "state"))
    assert r["status"] == "skipped" and "model id" in r["reason"], r
    assert not Path(db).exists(), "a skipped call must write nothing"


def test_close_without_linkage_skips_with_named_reason(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    mod = _load_tool()
    r = mod.close_capture("summary", agent="t-agent", user_id="clint",
                          model_id="glm-test", db_path=str(
                              tmp_path / "conversations.db"),
                          state_dir=str(tmp_path / "state"))
    assert r["status"] == "skipped" and "linkage" in r["reason"], r


def test_disabled_capture_skips_with_named_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("AZAZAEL_PAMS_CAPTURE", "0")
    mod = _load_tool()
    r = mod.open_capture("bus task", agent="t-agent", user_id="clint",
                         model_id="glm-test", db_path=str(
                             tmp_path / "conversations.db"),
                         state_dir=str(tmp_path / "state"))
    assert r["status"] == "disabled" and "AZAZAEL_PAMS_CAPTURE" in r["reason"], r
