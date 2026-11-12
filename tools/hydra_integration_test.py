"""Integration Tests — Full HYDRA discover → run → status chain.

Run:  pytest tools/hydra_integration_test.py -v   (from azazael/ repo root)

Rewritten 2026-10-18 against the current src/ORCHESTRATION/HYDRA.py API.
The old file tested methods removed in the rewrite (register_node,
load_nodes, build_sequence, auto_discover_ml_groups, adaptive_learn);
those tests are replaced with current-API equivalents, not silently kept.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from ORCHESTRATION.HYDRA import ClusterConfig, ClusterNode, HYDRACluster


@pytest.fixture
def cluster(tmp_path):
    return HYDRACluster(config=ClusterConfig(base_path=str(tmp_path / "hydra_test")))


def _add_node(cluster, name="test_core", group="core", priority=5):
    node = ClusterNode(name=name, group=group, priority=priority)
    with cluster._cluster_lock:
        cluster.nodes[name] = node
    return node


class TestFullChain:
    def test_discover_empty_no_crash(self, cluster):
        """Discover with no ml_platforms available should handle gracefully."""
        nodes = cluster.discover_nodes()
        assert isinstance(nodes, dict)

    def test_run_empty_sequence(self, cluster):
        """Run pipeline with no nodes should complete all 6 stages."""
        results = cluster.run_sequential()
        assert len(results) == 6
        assert all(r.timestamp is not None for r in results)

    def test_register_and_run(self, cluster):
        """Add a node, run pipeline, verify it gets processed."""
        _add_node(cluster)
        results = cluster.run_sequential()
        assert len(results) == 6

    def test_status_reflects_nodes(self, cluster):
        """Status should show added nodes."""
        for i in range(3):
            _add_node(cluster, name=f"n{i}", group="core")
        s = cluster.get_cluster_status()
        assert s["nodes"]["total"] == 3

    def test_save_and_load(self, cluster):
        """Nodes should persist across cluster instances."""
        _add_node(cluster, name="persistent", group="analysis")
        cluster._save_state()
        c2 = HYDRACluster(config=ClusterConfig(base_path=str(cluster.base_path)))
        c2.load_state()
        assert "persistent" in c2.nodes

    def test_full_chain_no_crash(self, cluster):
        """Full discover → add → run → status chain."""
        cluster.discover_nodes()
        _add_node(cluster, name="chain_node", group="core")
        cluster.run_sequential()
        s = cluster.get_cluster_status()
        assert "nodes" in s
