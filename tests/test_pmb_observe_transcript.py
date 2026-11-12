# coding: utf-8
"""Tests for tools/pmb_observe_transcript.py -- transcript command extraction.

The recorder half needs the `.pmb-venv` interpreter, so these cover the pure
extraction, which is where an unknown transcript shape would silently produce
zero outcome rows (the exact failure mode the whole observer exists to fix).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_WRAPPER = Path(__file__).resolve().parent.parent / "tools" / "pmb_observe_transcript.py"


def _load():
    spec = importlib.util.spec_from_file_location("pmb_observe_transcript", _WRAPPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def wrapper():
    return _load()


def test_extracts_nested_commands(wrapper, tmp_path):
    data = {"a": {"tool": "bash", "command": "pytest -q", "exit_code": 0},
            "b": [{"command": "git commit -m x", "result": {"exit_code": 1}}]}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    got = dict(wrapper.load_commands(str(p)))
    assert got.get("pytest -q") == "ok"
    assert got.get("git commit -m x") == "1"


def test_alternate_key_names(wrapper, tmp_path):
    data = [{"cmd": "cargo build", "exitCode": 0},
            {"shell_command": "make", "tool_response": {"exit_code": 2}}]
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    got = dict(wrapper.load_commands(str(p)))
    assert got.get("cargo build") == "ok"
    assert got.get("make") == "2"


def test_missing_code_is_treated_as_ok(wrapper, tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"command": "echo hi"}), encoding="utf-8")
    assert wrapper.load_commands(str(p)) == [("echo hi", "ok")]


def test_non_command_objects_ignored(wrapper, tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"tool": "read", "path": "foo.py"}), encoding="utf-8")
    assert wrapper.load_commands(str(p)) == []


def test_missing_file_is_empty(wrapper):
    assert wrapper.load_commands("nope.json") == []


def test_malformed_json_is_empty(wrapper, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert wrapper.load_commands(str(p)) == []
