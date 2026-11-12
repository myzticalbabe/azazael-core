# coding: utf-8
"""Known-signer allowlist matrix for the spine's gossip transport.

Why this file exists
--------------------
The allowlist was restored 2026-09-28 (it had been discarded by a
`git reset --hard`; see FORENSIC_FOLLOWUPS_2026-09-28.md section 0). Before it,
a VALID Ed25519 signature was treated as proof of *identity*, so on the
LAN-bound listener (`state/mesh_listener.json`: bind 0.0.0.0:18210) anyone
could mint a keypair, sign anything, and be merged as `verified`.

The rule now is: a signature proves the KEY; `verified` is reserved for KNOWN
signers -- our own key, or a key the operator pinned via
``AZAZAEL_GOSSIP_TRUSTED_KEYS`` / ``~/.azazael/mesh_trusted_keys.json``.

Every branch of that decision is pinned below. Crypto is stubbed so the outcome
does not depend on the machine's `cryptography` backend (see the G9 audit note),
and the developer's real ``mesh_trusted_keys.json`` is never read.
"""
import hashlib
import json
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CORE import spine_registry as sr  # noqa: E402

_LOCAL_PUB = "LOCALPUB"


def _sign(canonical):
    return hashlib.sha256(("s::" + canonical).encode("utf-8")).hexdigest()


def _verify(canonical, sig, pub):
    return sig == _sign(canonical)


def _payload(env="peer", seq=1, pub="PEERKEY", sign=True, **extra):
    """A gossip envelope shaped like the real export, signed with the stub."""
    body = {"gossip_from": env, "seq": seq, "signer_pub": pub}
    body.update(extra)
    if sign:
        body["signature"] = _sign(sr._gossip_canonical(body))
    return body


@pytest.fixture(autouse=True)
def _isolated_trust(monkeypatch, tmp_path):
    """No test may read the real allowlist, or leak into global trust state."""
    monkeypatch.setattr(sr, "_TRUSTED_KEYS_FILE",
                        tmp_path / "mesh_trusted_keys.json")
    monkeypatch.delenv(sr._GOSSIP_TRUSTED_KEYS_ENV, raising=False)
    monkeypatch.setattr(sr, "_gossip_crypto", lambda: (_sign, _verify, _LOCAL_PUB))
    sr.spine_reset()
    yield
    sr.spine_reset()


def _allow(spec, monkeypatch, tmp_path=None):
    """Publish an allowlist inline (dict -> JSON) for the duration of a test."""
    monkeypatch.setenv(sr._GOSSIP_TRUSTED_KEYS_ENV, json.dumps(spec))


# ── source parsing ─────────────────────────────────────────────────────

class TestLoadTrustedKeys:
    def test_env_keys_list_is_env_agnostic(self, monkeypatch):
        _allow({"keys": ["K1", "K2"]}, monkeypatch)
        assert sr._load_trusted_keys() == ({}, {"K1", "K2"})

    def test_env_map_binds_a_key_to_one_env(self, monkeypatch):
        _allow({"envs": {"wsl2-ubuntu": "K2"}}, monkeypatch)
        assert sr._load_trusted_keys() == ({"wsl2-ubuntu": "K2"}, set())

    def test_bare_env_to_key_map_is_accepted(self, monkeypatch):
        _allow({"wsl2-kali": "K3"}, monkeypatch)
        assert sr._load_trusted_keys() == ({"wsl2-kali": "K3"}, set())

    def test_reserved_metadata_keys_are_ignored(self, monkeypatch):
        _allow({"version": 1, "updated_at": "2026-09-28", "keys": ["K"],
                "envs": {"wsl2-ubuntu": "K2"}}, monkeypatch)
        env_map, key_set = sr._load_trusted_keys()
        assert env_map == {"wsl2-ubuntu": "K2"}
        assert key_set == {"K"}

    def test_list_value_takes_the_last_entry(self, monkeypatch):
        _allow({"envs": {"wsl2-ubuntu": ["K2", "K9"]}}, monkeypatch)
        assert sr._load_trusted_keys()[0] == {"wsl2-ubuntu": "K9"}

    def test_env_var_may_be_a_file_path(self, monkeypatch, tmp_path):
        spec = tmp_path / "trusted.json"
        spec.write_text(json.dumps({"keys": ["FROMFILE"]}), encoding="utf-8")
        monkeypatch.setenv(sr._GOSSIP_TRUSTED_KEYS_ENV, str(spec))
        assert sr._load_trusted_keys() == ({}, {"FROMFILE"})

    def test_unparsable_allowlist_fails_closed(self, monkeypatch):
        monkeypatch.setenv(sr._GOSSIP_TRUSTED_KEYS_ENV, "{not json at all")
        assert sr._load_trusted_keys() == ({}, set())

    def test_missing_path_is_not_a_crash(self, monkeypatch, tmp_path):
        missing = tmp_path / "nope.json"
        monkeypatch.setenv(sr._GOSSIP_TRUSTED_KEYS_ENV, str(missing))
        assert sr._load_trusted_keys() == ({}, set())

    def test_non_dict_json_contributes_nothing(self, monkeypatch):
        monkeypatch.setenv(sr._GOSSIP_TRUSTED_KEYS_ENV, '["K1", "K2"]')
        assert sr._load_trusted_keys() == ({}, set())

    def test_env_and_file_sources_merge(self, monkeypatch, tmp_path):
        spec = tmp_path / "trusted.json"
        spec.write_text(json.dumps({"envs": {"wsl2-kali": "KFILE"}}),
                        encoding="utf-8")
        monkeypatch.setattr(sr, "_TRUSTED_KEYS_FILE", spec)
        _allow({"keys": ["KENV"]}, monkeypatch)
        assert sr._load_trusted_keys() == ({"wsl2-kali": "KFILE"}, {"KENV"})


# ── membership ─────────────────────────────────────────────────────────

class TestSignerIsKnown:
    def test_env_bound_key_matches_its_own_env(self, monkeypatch):
        _allow({"envs": {"wsl2-ubuntu": "K2"}}, monkeypatch)
        assert sr._signer_is_known("wsl2-ubuntu", "K2") is True

    def test_env_bound_key_does_not_match_another_env(self, monkeypatch):
        _allow({"envs": {"wsl2-ubuntu": "K2"}}, monkeypatch)
        assert sr._signer_is_known("wsl2-kali", "K2") is False

    def test_env_agnostic_key_matches_any_env(self, monkeypatch):
        _allow({"keys": ["K1"]}, monkeypatch)
        assert sr._signer_is_known("any-env", "K1") is True

    def test_unpinned_key_is_unknown(self, monkeypatch):
        _allow({"keys": ["K1"]}, monkeypatch)
        assert sr._signer_is_known("any-env", "OTHER") is False

    def test_empty_pub_is_never_known(self, monkeypatch):
        _allow({"keys": ["K1"]}, monkeypatch)
        assert sr._signer_is_known("any-env", "") is False


# ── the decision matrix ────────────────────────────────────────────────

class TestKnownSignerMatrix:
    def test_own_key_is_verified(self):
        res = sr.check_gossip_trust(_payload(pub=_LOCAL_PUB), policy="strict")
        assert (res["ok"], res["signature"]) == (True, "verified")

    def test_operator_pinned_env_key_is_verified(self, monkeypatch):
        _allow({"envs": {"wsl2-ubuntu": "PEERKEY"}}, monkeypatch)
        res = sr.check_gossip_trust(_payload(env="wsl2-ubuntu"), policy="strict")
        assert (res["ok"], res["signature"]) == (True, "verified")

    def test_operator_pinned_key_list_is_verified(self, monkeypatch):
        _allow({"keys": ["PEERKEY"]}, monkeypatch)
        res = sr.check_gossip_trust(_payload(env="anything"), policy="strict")
        assert (res["ok"], res["signature"]) == (True, "verified")

    def test_valid_signature_from_an_unknown_signer_is_refused_under_strict(self):
        """The whole point: a signature is not an identity."""
        res = sr.check_gossip_trust(_payload(env="stranger"), policy="strict")
        assert res["ok"] is False
        assert res["reason"] == "unknown_signer"
        assert res["signature"] == "unpinned"

    def test_unknown_signer_is_admitted_under_warn_but_never_verified(self):
        res = sr.check_gossip_trust(_payload(env="stranger"), policy="warn")
        assert res["ok"] is True
        assert res["reason"] == "unknown_signer_admitted"
        assert res["signature"] == "unpinned"

    def test_off_policy_skips_verification_entirely(self):
        res = sr.check_gossip_trust({"gossip_from": "peer", "seq": 1},
                                    policy="off")
        assert (res["ok"], res["signature"]) == (True, "skipped")

    def test_unreadable_allowlist_refuses_a_valid_signature(self, monkeypatch):
        monkeypatch.setenv(sr._GOSSIP_TRUSTED_KEYS_ENV, "{broken")
        res = sr.check_gossip_trust(_payload(env="stranger"), policy="strict")
        assert res["reason"] == "unknown_signer"


class TestSignatureAndReplayGuards:
    def test_unsigned_payload_refused_under_strict(self):
        res = sr.check_gossip_trust(_payload(sign=False), policy="strict")
        assert (res["ok"], res["reason"]) == (False, "unsigned_payload")

    def test_unsigned_payload_admitted_under_warn(self):
        res = sr.check_gossip_trust(_payload(sign=False), policy="warn")
        assert res["ok"] is True
        assert res["signature"] == "missing"

    def test_missing_signer_key_is_refused(self):
        res = sr.check_gossip_trust(
            {"gossip_from": "peer", "seq": 1, "signature": "deadbeef",
             "signer_pub": ""}, policy="strict")
        assert res["reason"] == "missing_signer_key"

    def test_tampering_invalidates_the_signature(self):
        bad = _payload(env="stranger")
        bad["seq"] = bad["seq"] + 7          # any edited field breaks canonical
        res = sr.check_gossip_trust(bad, policy="warn")
        assert (res["ok"], res["reason"]) == (False, "bad_signature")

    def test_replayed_sequence_is_refused_before_crypto(self):
        sr._GOSSIP_SEQ_SEEN["peer"] = 5
        res = sr.check_gossip_trust(_payload(seq=3), policy="strict")
        assert res["reason"] == "replayed_seq"

    def test_key_change_after_pinning_is_impersonation(self):
        sr._GOSSIP_KEYS["peer"] = "ORIGINAL"
        res = sr.check_gossip_trust(_payload(pub="NEWKEY"), policy="strict")
        assert res["reason"] == "key_changed"
        assert res["signature"] == "invalid"

    def test_non_payload_never_raises(self):
        assert sr.check_gossip_trust(None)["reason"] == "not_a_payload"
        assert sr.check_gossip_trust(["not", "a", "dict"])["reason"] == "not_a_payload"

    def test_malformed_seq_does_not_raise(self):
        res = sr.check_gossip_trust({"gossip_from": "peer", "seq": {"a": 1}},
                                    policy="lax-typo")
        assert isinstance(res, dict) and res["seq"] == 0

    def test_crypto_unavailable_degrades_to_seq_only_and_says_so(self, monkeypatch):
        monkeypatch.setattr(sr, "_gossip_crypto", lambda: None)
        res = sr.check_gossip_trust(_payload(), policy="strict")
        assert res["ok"] is True
        assert res["signature"] == "unavailable"
        assert res["reason"] == "crypto_unavailable_seq_only"

    def test_introspection_reports_the_allowlist_without_echoing_full_keys(
            self, monkeypatch):
        long_key = "PEERKEY-0123456789ABCDEF"
        _allow({"envs": {"wsl2-ubuntu": long_key}, "keys": ["K1"]}, monkeypatch)
        state = sr.get_gossip_trust_state()
        assert state["trusted_key_count"] == 1
        assert list(state["trusted_envs"]) == ["wsl2-ubuntu"]
        # Introspection names the env but must not echo a usable key.
        assert state["trusted_envs"]["wsl2-ubuntu"] == long_key[:16] + "..."
        assert long_key not in json.dumps(state)
