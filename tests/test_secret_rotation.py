"""SecretRotator must not be able to claim a rotation it did not perform.

The original body returned `success: True` after generating a password and
writing it nowhere, so every assertion here fails against that version: they
read the value back off disk instead of trusting the return value.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.WORMS.expansion.config import SecretRotator  # noqa: E402


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("OTHER=keepme\nTEST_ROTATE=oldvalue\n", encoding="utf-8")
    monkeypatch.setenv("AZAZAEL_SECRET_ROTATION_LOG", str(tmp_path / "rot.jsonl"))
    # add_to_env writes to ~/.env, so point HOME at the temp dir for this test.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return env


def _read_key(path: Path, key: str) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def test_rotation_changes_the_file_and_verifies_it(env_file):
    rot = SecretRotator()
    res = rot.rotate_secret("TEST_ROTATE", generator=lambda: "newvalue123",
                            env_path=str(env_file))
    assert res["success"] is True, res
    assert res["verified"] is True and res["changed"] is True
    # the claim is checked against the file, not the return value
    assert _read_key(env_file, "TEST_ROTATE") == "newvalue123"
    # untouched keys survive
    assert _read_key(env_file, "OTHER") == "keepme"
    # backup exists and holds the OLD value
    backup = Path(res["backup"])
    assert backup.exists()
    assert _read_key(backup, "TEST_ROTATE") == "oldvalue"


def test_unknown_key_is_refused_not_invented(env_file):
    rot = SecretRotator()
    res = rot.rotate_secret("NO_SUCH_KEY", generator=lambda: "x", env_path=str(env_file))
    assert res["success"] is False
    assert "unknown_secret" in res["reason"]
    assert "NO_SUCH_KEY" not in env_file.read_text(encoding="utf-8")


def test_create_true_adds_a_new_key(env_file):
    rot = SecretRotator()
    res = rot.rotate_secret("BRAND_NEW", generator=lambda: "abc123", create=True,
                            env_path=str(env_file))
    assert res["success"] is True, res
    assert _read_key(env_file, "BRAND_NEW") == "abc123"


def test_same_value_is_not_a_rotation(env_file):
    rot = SecretRotator()
    res = rot.rotate_secret("TEST_ROTATE", generator=lambda: "oldvalue",
                            env_path=str(env_file))
    assert res["success"] is False
    assert "existing value" in res["reason"]
    assert _read_key(env_file, "TEST_ROTATE") == "oldvalue"


def test_empty_generator_fails_closed(env_file):
    rot = SecretRotator()
    res = rot.rotate_secret("TEST_ROTATE", generator=lambda: "", env_path=str(env_file))
    assert res["success"] is False
    assert _read_key(env_file, "TEST_ROTATE") == "oldvalue"


def test_audit_trail_records_the_rotation_and_status_reads_it(env_file):
    rot = SecretRotator()
    rot.rotate_secret("TEST_ROTATE", generator=lambda: "fresh42", env_path=str(env_file))
    audit = Path(os.environ["AZAZAEL_SECRET_ROTATION_LOG"])
    rows = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows and rows[-1]["name"] == "TEST_ROTATE"
    assert rows[-1]["success"] is True and rows[-1]["verified"] is True
    # the secret itself is never written to the audit trail
    assert "fresh42" not in audit.read_text(encoding="utf-8")

    status = rot.rotation_status(max_age_days=0)
    assert status["keys_rotated"] == 1
    assert [s["name"] for s in status["stale"]] == ["TEST_ROTATE"]
    assert rot.rotation_status(max_age_days=3650)["stale"] == []


def test_no_name_is_refused(env_file):
    res = SecretRotator().rotate_secret(env_path=str(env_file))
    assert res["success"] is False
    assert "no_name" in res["reason"]
