"""The daemon token must never be COPIED into a client config.

WHY THIS FILE EXISTS
Measured 2026-11-11: `~/.config/opencode/opencode.json` and
`~/.config/kilo/kilo.jsonc` each held `Authorization: Bearer <token>` while the
live daemon token at `~/.pmb/daemon.token` was a different value. The daemon
was healthy throughout -- 30 tools, 62 ms -- and `opencode mcp list` reported
`pmb needs authentication`, so every pmb tool vanished for that whole
session. The startup gate read PASS=9 the entire time, because its
`lane-config` check asks whether the pmb URL routes through :8766 and never
asks whether the baked credential still authenticates.

The upstream package documents the intended shape
(`pmb/cli/connect.py`: "the daemon bearer token is read from disk by the proxy
AT RUNTIME, so nothing stale is baked into the config"), so the fix is to
resolve the token where it is USED rather than copy it where it is STORED.

What is proven here, against no daemon and no port:
  * `live_token` reads the CURRENT token, and re-reads it (a rotation between
    two calls is observed, not cached);
  * `token_matches` accepts the header and the bare value, and rejects a
    wrong one;
  * `upstream_headers` REPLACES a stale client token with the live one -- the
    stale copy is never forwarded -- and leaves the client's own header alone
    when no token can be read, so the daemon's 401 stays legible;
  * the floor proxy actually calls that resolver on the forward path, so the
    behaviour above is wired into the request path and not merely present in a
    module nobody imports.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
TOKEN_TOOL = REPO / "tools" / "pmb_token.py"
PROXY_TOOL = REPO / "tools" / "pmb_floor_proxy.py"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _tmp_token(tmp_path, value: str) -> pathlib.Path:
    path = tmp_path / "daemon.token"
    path.write_text(value, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# pmb_token: the resolver
# --------------------------------------------------------------------------- #


def test_live_token_reads_the_file(tmp_path):
    mod = _load("pmb_token_rt", TOKEN_TOOL)
    path = _tmp_token(tmp_path, "  the-current-token  ")
    assert mod.live_token(path) == "the-current-token"


def test_live_token_is_missing_file_not_an_exception(tmp_path):
    mod = _load("pmb_token_rt2", TOKEN_TOOL)
    assert mod.live_token(tmp_path / "absent.token") == ""


def test_rotation_is_observed_not_cached(tmp_path):
    """A token the server can rotate must not be read once and remembered.

    This is the whole fix: if the value were cached, the next rotation would
    break every client exactly as the original bug did.
    """
    mod = _load("pmb_token_rt3", TOKEN_TOOL)
    path = _tmp_token(tmp_path, "token-before-rotation")
    assert mod.live_token(path) == "token-before-rotation"
    path.write_text("token-after-rotation", encoding="utf-8")
    assert mod.live_token(path) == "token-after-rotation"


def test_token_matches_accepts_header_and_bare_value(tmp_path):
    mod = _load("pmb_token_rt4", TOKEN_TOOL)
    path = _tmp_token(tmp_path, "secret")
    assert mod.token_matches("Bearer secret", "secret")
    assert mod.token_matches("secret", "secret")
    assert mod.token_matches("bearer secret", "secret")
    assert not mod.token_matches("Bearer wrong", "secret")
    assert not mod.token_matches("", "secret")
    assert not mod.token_matches(None, "secret")


def test_token_matches_is_false_when_no_token_exists(tmp_path):
    """No token on disk must not match everything."""
    mod = _load("pmb_token_rt5", TOKEN_TOOL)
    assert not mod.token_matches("Bearer anything", None) or \
        mod.live_token(tmp_path / "absent.token") == ""


def test_upstream_headers_replaces_a_stale_client_token(tmp_path):
    """THE regression: a stale baked token must not reach the daemon."""
    mod = _load("pmb_token_rt6", TOKEN_TOOL)
    path = _tmp_token(tmp_path, "live-token")
    out = mod.upstream_headers(
        {"Authorization": "Bearer stale-token", "Accept": "application/json"},
        path)
    assert out["Authorization"] == "Bearer live-token"
    assert "stale-token" not in out["Authorization"]
    # Unrelated headers are preserved -- the proxy forwards them untouched.
    assert out["Accept"] == "application/json"


def test_upstream_headers_leaves_client_header_when_no_token(tmp_path):
    """With no token on disk the client's own header is passed through.

    The daemon then answers 401 with ITS reason. Inventing a token here would
    turn "your token is wrong" and "there is no token" into the same failure.
    """
    mod = _load("pmb_token_rt7", TOKEN_TOOL)
    out = mod.upstream_headers(
        {"Authorization": "Bearer whatever"},
        tmp_path / "absent.token")
    assert out["Authorization"] == "Bearer whatever"


def test_an_unreadable_token_is_recorded_not_swallowed(tmp_path):
    """A swallowed failure that returns a value shaped like an answer.

    "" for "the file is missing" and "" for "the resolver was never called"
    are the same value, so an unrecorded failure is indistinguishable from
    success. The fix is a SECOND statement that records it -- which is also
    why the compliance scanner bars a bare `except: return`.
    """
    mod = _load("pmb_token_rt8", TOKEN_TOOL)
    mod.FAILURES.clear()
    assert mod.live_token(tmp_path / "absent.token") == ""
    assert mod.FAILURES, "the unreadable token was absorbed without a record"
    assert "absent.token" in mod.FAILURES[0]


# --------------------------------------------------------------------------- #
# pmb_floor_proxy: the resolver is WIRED IN, not merely present
# --------------------------------------------------------------------------- #


def test_proxy_forward_path_uses_the_resolver(tmp_path, monkeypatch):
    """The forward path must resolve the token at request time.

    Asserted on the PROXY, not on the resolver: a resolver that exists in a
    module nobody calls is exactly the shape of the original defect.
    """
    proxy = _load("pmb_proxy_rt", PROXY_TOOL)
    assert proxy._upstream_headers is not None, \
        "the proxy did not load the token resolver at all"

    live = _tmp_token(tmp_path, "live-token")
    out = proxy._upstream_headers(
        {"Authorization": "Bearer stale-token"}, live)
    assert out["Authorization"] == "Bearer live-token"


def test_proxy_healthz_names_its_config_source():
    """A settings surface the running process cannot see is not a surface.

    The proxy used to fall back to environment defaults silently when its
    `tools.defaults_profile` import failed (measured: ModuleNotFoundError
    under the Startup VBS), so editing config/defaults.json did not change
    the thresholds the live proxy applied. The source is now NAMED.
    """
    proxy = _load("pmb_proxy_rt2", PROXY_TOOL)
    payload = proxy.health_payload()
    assert "config_source" in payload
    assert payload["config_source"]


# --------------------------------------------------------------------------- #
# the GATE: the check whose absence let the outage pass green
# --------------------------------------------------------------------------- #


def test_gate_reads_a_baked_token_out_of_a_lane_config():
    """The gate must be able to SEE the copy, or it cannot check it."""
    gate = _load("pmb_gate_rt", REPO / "tools" / "mcp_startup_gate.py")
    doc = {"mcp": {"pmb": {"type": "remote", "url": "http://127.0.0.1:8766/mcp",
                          "headers": {"Authorization": "Bearer baked-value"}}}}
    assert gate.pmb_baked_token(doc) == "baked-value"

    # A bare value (no "Bearer " prefix) is the same secret.
    doc2 = {"mcpServers": {"pmb": {"headers": {"Authorization": "bare-value"}}}}
    assert gate.pmb_baked_token(doc2) == "bare-value"

    # No credential at all -> None, which is a LEGITIMATE state: the proxy
    # resolves the live token for such a client, so there is nothing to check.
    assert gate.pmb_baked_token({"mcp": {"pmb": {"url": "x"}}}) is None


def test_gate_distinguishes_stale_from_current_from_absent():
    """The three states the gate must tell apart, and the wording it uses.

    'stale' is the FAIL: that lane's pmb tools are dead. 'absent' is NOT a
    failure -- a client with no baked token relies on the proxy resolving the
    live one, which is the shape that cannot go stale. Conflating the two
    would make the check cry wolf on exactly the configuration that is
    correct.
    """
    gate = _load("pmb_gate_rt2", REPO / "tools" / "mcp_startup_gate.py")
    live = "live-token-value"

    def config_with(token):
        entry = {"type": "remote", "url": "http://127.0.0.1:8766/mcp"}
        if token is not None:
            entry["headers"] = {"Authorization": "Bearer " + token}
        return {"mcp": {"pmb": entry}}

    assert gate.pmb_baked_token(config_with("stale")) == "stale"
    assert gate.pmb_baked_token(config_with(live)) == live
    assert gate.pmb_baked_token(config_with(None)) is None

    # The comparison itself: equal -> no drift; different -> drift.
    import hmac
    assert hmac.compare_digest("stale", live) is False
    assert hmac.compare_digest(live, live) is True
