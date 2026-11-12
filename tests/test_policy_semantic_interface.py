"""The ASIL interface gate: an action the contract cannot express is not granted.

`structured_action` shipped complete with no production caller, so EP168, EP269,
EP327 and EP382 were EXERCISE-ONLY -- the module validated interface artifacts and
nothing invoked it. `PolicyEngine.evaluate` now validates a structured action
against a registered interface contract before granting it, so a brittle action
fails at GATE time with a named repair instead of at click time.

The two halves that make that a real integration rather than a call added to make
a counter rise:
  * a registered app whose action cannot be expressed is DENIED, and the reason
    names the gap (`unknown-verb` / `state-incomplete`);
  * a bare (non-structured) call, and a structured call for an UNREGISTERED app,
    take the original path unchanged -- asserted here so the change cannot
    silently alter every existing policy decision.
"""


import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _engine():
    from CORE.compliance.policy import PolicyEngine
    return PolicyEngine()


def test_unregistered_app_takes_the_original_path():
    """No contract registered -> the interface gate must not fire at all."""
    engine = _engine()
    allowed, reason = engine.evaluate(
        "code_edit", role="developer",
        attributes={"app": "never-registered", "verb": "write",
                    "target": "/tmp/x"})
    assert allowed is False
    # The default-deny reason, NOT an interface verdict: proves the gate was
    # bypassed rather than that the action merely failed for another reason.
    assert "no matching rule" in reason
    assert "interface" not in reason.lower()


def test_bare_call_is_unchanged():
    """A caller with no structured action sees exactly the old behaviour."""
    engine = _engine()
    allowed, reason = engine.evaluate("code_edit", role="developer")
    assert allowed is False
    assert "no matching rule" in reason
    assert "interface" not in reason.lower()


def test_unknown_verb_is_denied_with_a_named_gap():
    engine = _engine()
    engine.register_interface("demo", {"verbs": ["read"], "targets": ["/a"]},
                              required_keys=["ready"])
    allowed, reason = engine.evaluate(
        "code_edit", role="developer",
        attributes={"app": "demo", "verb": "write", "target": "/a"})
    assert allowed is False
    assert "DENIED (interface)" in reason
    assert "unknown-verb:write" in reason


def test_unknown_target_is_denied_with_a_named_gap():
    engine = _engine()
    engine.register_interface("demo", {"verbs": ["read"], "targets": ["/a"]})
    allowed, reason = engine.evaluate(
        "code_edit", role="developer",
        attributes={"app": "demo", "verb": "read", "target": "/b"})
    assert allowed is False
    assert "unknown-target:/b" in reason


def test_state_incomplete_observation_is_denied():
    """The screenshot failure mode, named: a required key is absent."""
    engine = _engine()
    engine.register_interface("demo", {"verbs": ["read"], "targets": ["/a"]},
                              required_keys=["ready", "visible"])
    allowed, reason = engine.evaluate(
        "code_edit", role="developer",
        attributes={"app": "demo", "verb": "read", "target": "/a",
                    "state": {"ready": True}})
    assert allowed is False
    assert "state-incomplete:visible" in reason
    assert "state-incomplete:ready" not in reason


def test_expressible_action_with_complete_state_passes_the_gate():
    """The gate must be able to PASS, or denying means nothing."""
    engine = _engine()
    engine.register_interface("demo", {"verbs": ["read"], "targets": ["/a"]},
                              required_keys=["ready"])
    result = engine.evaluate_all(
        "code_edit", role="developer",
        attributes={"app": "demo", "verb": "read", "target": "/a",
                    "state": {"ready": True}})
    iface = result["interface"]
    assert iface is not None
    assert iface["executable"] is True
    assert iface["gaps"] == []
    # EP382: every validated action declares its effect row.
    assert iface["effect"]["effect"] == "read"
    assert iface["effect"]["typed_trace"] == ["read:/a"]
    # No matching RULE still denies -- the interface gate is not an allow-list.
    assert result["allowed"] is False
    assert "no matching rule" in result["reason"]
