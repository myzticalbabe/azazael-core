"""A dropped gate token must actually verify, in the daemon that consumes it.

`request_auth.drop_token()` signs and writes a token; `parallax_validator`
verifies it before allowing a write, then consumes it. Those are two modules
with two copies of the token filename rule and two copies of the signature
check, and the signing side once produced signatures that NO verifier could
reproduce -- a per-call random salt returned inside the signature -- so
`is_token_valid()` answered False for every token ever dropped. That is a
deny-everything gate, and every unit test on either side still passed, because
each side tested its own half. This file tests the seam instead: sign with one
module, verify with the other.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from DAEMONS import parallax_validator as pv  # noqa: E402
from SCRIPTS import request_auth as ra  # noqa: E402

TARGET = "gate_round_trip_probe.py"


def _clear_tokens() -> None:
    """Tokens are one-time and specific, so isolation is deterministic."""
    for f in ra._get_tokens_dir().glob("*.token"):
        f.unlink()


def test_drop_then_verify_through_the_daemon():
    """The authorization path end to end: sign here, verify there."""
    _clear_tokens()
    path = ra.drop_token(TARGET, "str_replace", {"authorized": True})
    assert ra.is_token_valid(path, TARGET)

    valid, reason = pv.verify_token(TARGET, max_age_seconds=60)
    assert valid, reason

    # verify_token consumes the token: one authorization, one write.
    assert not pv._get_token_path(TARGET).exists()


def test_both_modules_agree_on_the_token_filename():
    """Each side builds the name itself -- a divergence authorizes nothing."""
    assert ra._get_tokens_dir() == pv._get_tokens_dir()
    _clear_tokens()
    path = ra.drop_token(TARGET, "str_replace", {})
    assert path == pv._get_token_path(TARGET)


def test_a_tampered_token_is_rejected():
    """Changing the payload after signing must invalidate the signature.

    This is the half the old random-salt scheme got wrong in the opposite
    direction: it rejected the honest token too, so a passing rejection test
    proved nothing. Paired with the round trip above, a rejection here means
    tampering, not a broken verifier.
    """
    _clear_tokens()
    path = ra.drop_token(TARGET, "str_replace", {"authorized": True})
    data = json.loads(path.read_text(encoding="utf-8"))
    data["gate_data"] = {"authorized": False}  # tamper after signing
    path.write_text(json.dumps(data), encoding="utf-8")

    valid, reason = pv.verify_token(TARGET, max_age_seconds=60)
    assert not valid
    assert "signature" in reason.lower(), reason
    # Not consumed: a rejected token must stay on disk for the audit trail.
    assert path.exists()


def test_an_expired_token_is_rejected_and_not_consumed():
    """Expiry is checked before the signature, and must not burn the token."""
    from datetime import datetime, timedelta, timezone

    _clear_tokens()
    path = ra.drop_token(TARGET, "str_replace", {})
    data = json.loads(path.read_text(encoding="utf-8"))
    data["timestamp"] = (
        datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")

    valid, reason = pv.verify_token(TARGET, max_age_seconds=15)
    assert not valid
    assert "expired" in reason.lower(), reason
    assert path.exists()
