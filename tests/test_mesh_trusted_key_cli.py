# coding: utf-8
"""The allowlist write path: `scripts/mesh_trusted_key.py`.

Why this file exists
--------------------
Followup #6 (2026-09-28): the restored known-signer allowlist had no writer.
Hand-editing the JSON is not a mechanism -- an unparsable file fails CLOSED, so
one typo silently refuses every peer. These tests drive the CLI end to end and
then validate its output through the READER
(`spine_registry._load_trusted_keys` / `_signer_is_known`), so the writer cannot
drift from the gate that consumes it.
"""
import json
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_PROJECT_ROOT, "scripts"), _PROJECT_ROOT,
           os.path.join(_PROJECT_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import mesh_trusted_key as cli  # noqa: E402
from CORE import spine_registry as sr  # noqa: E402

_LONG_KEY = "PEERKEY-0123456789ABCDEF"


@pytest.fixture(autouse=True)
def _isolated_allowlist(monkeypatch, tmp_path):
    """Point BOTH the CLI and the reader at a temp file; never the real one."""
    path = tmp_path / "mesh_trusted_keys.json"
    monkeypatch.setattr(sr, "_TRUSTED_KEYS_FILE", path)
    monkeypatch.delenv(sr._GOSSIP_TRUSTED_KEYS_ENV, raising=False)
    sr.spine_reset()
    yield path
    sr.spine_reset()


def _run(path, *argv):
    """`--file` belongs to the subparser, so it follows the subcommand."""
    return cli.main([argv[0], "--file", str(path), *argv[1:]])


def test_pin_env_bound_key_is_readable_by_the_reader(_isolated_allowlist):
    path = _isolated_allowlist
    assert _run(path, "pin", "wsl2-ubuntu", "PEERKEY") == 0
    assert sr._load_trusted_keys() == ({"wsl2-ubuntu": "PEERKEY"}, set())
    assert sr._signer_is_known("wsl2-ubuntu", "PEERKEY") is True
    # env-bound means exactly that: the same key elsewhere is still unknown.
    assert sr._signer_is_known("wsl2-kali", "PEERKEY") is False


def test_pin_env_agnostic_key_matches_any_env(_isolated_allowlist):
    path = _isolated_allowlist
    assert _run(path, "pin", "--key", "SHAREDKEY") == 0
    assert sr._load_trusted_keys() == ({}, {"SHAREDKEY"})
    assert sr._signer_is_known("anywhere", "SHAREDKEY") is True


def test_pins_accumulate_without_clobbering(_isolated_allowlist):
    path = _isolated_allowlist
    _run(path, "pin", "wsl2-ubuntu", "KEY_A")
    _run(path, "pin", "wsl2-kali", "KEY_B")
    _run(path, "pin", "--key", "KEY_C")
    env_map, key_set = sr._load_trusted_keys()
    assert env_map == {"wsl2-ubuntu": "KEY_A", "wsl2-kali": "KEY_B"}
    assert key_set == {"KEY_C"}
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["version"] == 1 and doc["updated_at"]


def test_pinning_the_same_bare_key_twice_is_idempotent(_isolated_allowlist):
    path = _isolated_allowlist
    _run(path, "pin", "--key", "KEY_C")
    _run(path, "pin", "--key", "KEY_C")
    assert sr._load_trusted_keys()[1] == {"KEY_C"}


def test_unpin_env_removes_only_that_binding(_isolated_allowlist):
    path = _isolated_allowlist
    _run(path, "pin", "wsl2-ubuntu", "KEY_A")
    _run(path, "pin", "wsl2-kali", "KEY_B")
    assert _run(path, "unpin", "wsl2-ubuntu") == 0
    assert sr._load_trusted_keys()[0] == {"wsl2-kali": "KEY_B"}


def test_unpin_bare_key(_isolated_allowlist):
    path = _isolated_allowlist
    _run(path, "pin", "--key", "KEY_C")
    assert _run(path, "unpin", "--key", "KEY_C") == 0
    assert sr._load_trusted_keys() == ({}, set())


def test_verify_exit_codes_match_the_reader(_isolated_allowlist):
    path = _isolated_allowlist
    _run(path, "pin", "wsl2-ubuntu", "PEERKEY")
    assert _run(path, "verify", "wsl2-ubuntu", "PEERKEY") == 0
    assert _run(path, "verify", "wsl2-ubuntu", "OTHER") == 2


def test_list_names_pins_without_echoing_full_keys(_isolated_allowlist, capsys):
    path = _isolated_allowlist
    _run(path, "pin", "wsl2-ubuntu", _LONG_KEY)
    assert _run(path, "list") == 0
    out = capsys.readouterr().out
    assert "wsl2-ubuntu" in out
    assert _LONG_KEY[:16] in out
    assert _LONG_KEY not in out


def test_list_outputs_json_when_asked(_isolated_allowlist, capsys):
    path = _isolated_allowlist
    _run(path, "pin", "wsl2-ubuntu", "PEERKEY")
    capsys.readouterr()                       # drop the pin's own output
    assert _run(path, "list", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["envs"] == {"wsl2-ubuntu": "PEERKEY"}


def test_corrupt_file_is_refused_not_overwritten(_isolated_allowlist):
    path = _isolated_allowlist
    path.write_text("{not json", encoding="utf-8")
    assert _run(path, "pin", "wsl2-ubuntu", "PEERKEY") == 1
    assert path.read_text(encoding="utf-8") == "{not json"


def test_legacy_bare_map_survives_a_pin(_isolated_allowlist):
    """A pre-schema `{env: key}` file must not lose its pins on the next write."""
    path = _isolated_allowlist
    path.write_text(json.dumps({"wsl2-kali": "LEGACY"}), encoding="utf-8")
    _run(path, "pin", "wsl2-ubuntu", "PEERKEY")
    env_map, _keys = sr._load_trusted_keys()
    assert env_map == {"wsl2-kali": "LEGACY", "wsl2-ubuntu": "PEERKEY"}


def test_pin_without_a_key_is_rejected(_isolated_allowlist):
    assert _run(_isolated_allowlist, "pin", "wsl2-ubuntu") == 1


def test_env_and_key_flag_together_is_rejected(_isolated_allowlist):
    assert _run(_isolated_allowlist, "pin", "wsl2-ubuntu", "--key", "K") == 1
