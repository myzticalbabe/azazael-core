"""The reasoning-overhead pad: an unspecified cap must survive a thinking model.

The local llama-server entries are reasoning models. Chain-of-thought streams to
`reasoning_content` and `content` stays EMPTY until thinking ends, so a cap that
does not budget for the thinking is spent entirely on it and the turn reads as a
blank answer.

`PROVIDER_REGISTRY[...]["reasoning_overhead"]` is the declared budget for that
thinking, and `OpenAIProvider._default_max_tokens()` applies it -- but ONLY when
the caller did not set a cap, because an explicit cap is the caller's own budget.

The consumer contract on the other side is `azazael_operator_task.py`, which
lives outside this project and resolves the same field from the same registry
(by base_url, then by default_model); the two lookups are pinned below so a
registry rename cannot silently zero the pad for either side.
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.inference.providers import (  # noqa: E402
    PROVIDER_REGISTRY,
    OpenAIProvider,
)

LOCAL_REASONING = "llama_qwopus"


def test_local_reasoning_entry_declares_an_overhead():
    """The pad is data. Without the field the whole mechanism is a no-op."""
    assert PROVIDER_REGISTRY[LOCAL_REASONING]["reasoning_overhead"] > 0


def test_overhead_resolves_by_base_url_and_by_default_model():
    """Both lookups `azazael_operator_task.provider_reasoning_overhead` uses."""
    info = PROVIDER_REGISTRY[LOCAL_REASONING]

    want_url = str(info["base_url"]).rstrip("/").lower()
    by_url = [n for n, i in PROVIDER_REGISTRY.items()
              if str(i.get("base_url") or "").rstrip("/").lower() == want_url]
    assert LOCAL_REASONING in by_url

    want_model = info["default_model"]
    by_model = [n for n, i in PROVIDER_REGISTRY.items()
                if i.get("default_model") == want_model]
    assert LOCAL_REASONING in by_model


def test_default_cap_is_padded_for_a_reasoning_entry():
    prov = OpenAIProvider(LOCAL_REASONING)
    overhead = int(PROVIDER_REGISTRY[LOCAL_REASONING]["reasoning_overhead"])
    assert prov._default_max_tokens() == int(prov.config.max_tokens) + overhead


def test_default_cap_is_unchanged_without_a_declared_overhead():
    """A hosted API must not gain tokens it never asked for."""
    prov = OpenAIProvider("groq")
    assert int(PROVIDER_REGISTRY["groq"].get("reasoning_overhead", 0) or 0) == 0
    assert prov._default_max_tokens() == int(prov.config.max_tokens)


def test_an_unregistered_provider_name_is_not_padded():
    """A lookup miss is +0, not an exception and not a bonus."""
    prov = OpenAIProvider(None)
    prov.provider_name = "definitely_not_registered"
    assert prov._default_max_tokens() == int(prov.config.max_tokens)


def test_every_entry_that_requires_server_side_reasoning_off_declares_a_pad():
    """A new local reasoning model cannot be added unpadded unnoticed.

    Keyed on the entry's own description rather than a list kept here: the
    registry states that an entry serves a thinking model by requiring
    `--reasoning off` SERVER-SIDE, so the test reads the same signal a
    maintainer would. `llamaserver` is deliberately NOT matched -- its wording
    is conditional ("Must run with ... if it serves a thinking model"), because
    it may serve anything the supervisor has loaded.
    """
    marked = {
        n for n, i in PROVIDER_REGISTRY.items()
        if i.get("local")
        and "Requires --reasoning off" in str(i.get("description", ""))
    }
    # A reworded description would silently make this test vacuous.
    assert marked, "no entry declares the marker text; this test checks nothing"
    for name in sorted(marked):
        overhead = int(PROVIDER_REGISTRY[name].get("reasoning_overhead", 0) or 0)
        assert overhead > 0, f"{name} serves a thinking model but declares no pad"


def test_entries_without_a_declared_reasoning_requirement_stay_unpadded():
    """Leaving these at zero is a decision, not an oversight -- pin it.

    `llama_mythos_v2`'s description credits FTPO training with clean turns
    where v1 ran away to the cap, so it has not shown the blank-turn failure
    the pad exists to prevent, and `llamaserver` may serve anything.
    """
    for name in ("llama_mythos_v2", "llamaserver"):
        assert int(PROVIDER_REGISTRY[name].get("reasoning_overhead", 0) or 0) == 0


def test_all_reasoning_entries_share_the_measured_floor():
    """One number, declared once: only qwopus has been measured."""
    overheads = {n: int(PROVIDER_REGISTRY[n]["reasoning_overhead"])
                 for n in PROVIDER_REGISTRY
                 if PROVIDER_REGISTRY[n].get("reasoning_overhead")}
    assert overheads, "no entry declares an overhead"
    assert set(overheads.values()) == {512}, overheads


def test_chat_pads_the_default_and_never_an_explicit_cap():
    """Structural pin on the call sites.

    An explicit cap is checked first (`max_tokens or ...`), so a caller that
    sized the cap on purpose keeps it; both the sync and the streaming path
    must go through the padded default rather than reading the config directly.
    """
    src = inspect.getsource(OpenAIProvider.chat)
    assert "max_tokens or self._default_max_tokens()" in src

    src_stream = inspect.getsource(OpenAIProvider.chat_stream)
    assert "max_tokens=self._default_max_tokens()" in src_stream
    assert "max_tokens=self.config.max_tokens" not in src_stream
