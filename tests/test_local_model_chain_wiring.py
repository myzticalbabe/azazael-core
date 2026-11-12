"""Wiring matrix for the local model tier: mythos / v2 / qwopus / ornith
plus accelerator_sidecar and openvino.

The six names must be REAL chain members, not merely registry entries:
every failover consumer walks ProviderFailoverMeshWorm.CHAIN or
RewardShaperMeshWorm.PROVIDERS, and spine._provider_key_state consumes
those two structures directly (spine.py `fo.CHAIN` / `shaper.PROVIDERS`).
Registration in the RULE 0D registry alone would leave the models
inert - reachable by nobody - so each half of the matrix is pinned here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.inference.providers import (  # noqa: E402
    PROVIDER_REGISTRY,
    get_provider,
)

LOCAL_TIER = (
    "llama_mythos", "llama_mythos_v2", "llama_qwopus", "llama_ornith",
    "accelerator_sidecar", "openvino",
)


def test_local_tier_in_failover_chain():
    """Each of the six must sit in BOTH the registry (so _resolve_chain
    treats it as selectable) and the CHAIN itself (so next_provider can
    hand it back)."""
    from CORE.WORMS.expansion.servemesh import ProviderFailoverMeshWorm
    for name in LOCAL_TIER:
        assert name in PROVIDER_REGISTRY, name
        assert name in ProviderFailoverMeshWorm.CHAIN, name


def test_local_tier_in_reward_shaper():
    from CORE.WORMS.expansion.federated import RewardShaperMeshWorm
    for name in LOCAL_TIER:
        assert name in RewardShaperMeshWorm.PROVIDERS, name


def test_spine_key_state_consumes_the_real_chains():
    """spine._provider_key_state is chain-driven: fed the REAL structures it
    must return without raising, and every verdict it does report must be a
    bool verdict on a remotely-addressable provider. Local-tier entries are
    OMITTED by design - they are loopback endpoints (or env_var model paths
    with an empty base_url) with no credential to be missing, so their
    absence is the expected pass-verdict, not a drop-out."""
    from CORE.WORMS.expansion import spine as SP
    from CORE.WORMS.expansion.servemesh import ProviderFailoverMeshWorm
    from CORE.WORMS.expansion.federated import RewardShaperMeshWorm
    ks_chain = SP.SpineCycleWorm._provider_key_state(
        ProviderFailoverMeshWorm.CHAIN)
    ks_shapers = SP.SpineCycleWorm._provider_key_state(
        RewardShaperMeshWorm.PROVIDERS)
    for name in LOCAL_TIER:
        assert ks_chain.get(name, True), name
        assert ks_shapers.get(name, True), name
    for name, verdict in {**ks_chain, **ks_shapers}.items():
        assert isinstance(verdict, bool), name


def test_legacy_chain_tail_stays_broadly_unresolvable():
    """ Six tail names kept from the original 12-provider era have no RULE 0D
    entry, so _resolve_chain must report EXACTLY them as unresolvable (no
    invented entries), while `llamaserver` - which did gain a registry entry -
    resolves. Every CHAIN member must land in exactly one bucket."""
    from CORE.WORMS.expansion.servemesh import ProviderFailoverMeshWorm
    resolvable, unresolvable = ProviderFailoverMeshWorm._resolve_chain()
    assert set(unresolvable) == {
        "vllm", "sglang", "openwebui", "jan", "kilo", "opencode"}
    assert "llamaserver" in resolvable
    assert set(resolvable) | set(unresolvable) == set(ProviderFailoverMeshWorm.CHAIN)
    assert not (set(resolvable) & set(unresolvable))


def test_local_tier_constructs_through_get_provider():
    """A chain member that resolves in the registry but cannot produce a
    client would blow up on first hand-off. Each of the six must construct
    through the SAME dispatch get_best_provider uses (generic OpenAI client
    for the llama-servers / sidecar, OpenVINOProvider for openvino)."""
    for name in LOCAL_TIER:
        provider = get_provider(name)
        assert provider is not None, name
    assert get_provider("openvino").__class__.__name__ == "OpenVINOProvider"


def test_local_models_declared_local_in_get_best_provider_tiers():
    """AZAZAEL_LOCAL_PRIORITY tiering works over _LOCAL_PROVIDER_NAMES; all
    six must sit in the local tier so the env mode actually governs them."""
    from CORE.inference.providers import _LOCAL_PROVIDER_NAMES
    assert set(LOCAL_TIER) <= set(_LOCAL_PROVIDER_NAMES)
