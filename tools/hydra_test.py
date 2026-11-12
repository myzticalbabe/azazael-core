"""
Unit Tests for HYDRA — Adaptive Sequential ML Cluster
Run with:  pytest tools/hydra_test.py -v   (from azazael/ repo root)

Rewritten 2026-10-18 against the current src/ORCHESTRATION/HYDRA.py API:
  - import from src.ORCHESTRATION.HYDRA (was bare `from HYDRA import ...`
    plus a dead sys.path pointing at ~/Desktop/azazael-project-e)
  - constructor takes ClusterConfig(base_path=...) (was base_path kwarg)
  - nodes are added via the nodes dict (register_node/load_nodes were removed
    in the rewrite); status shape is nodes{total,...}/stages{...}
  - AdaptationType / build_sequence / adaptive_learn no longer exist, so the
    tests that covered them are dropped (documented, not silently skipped).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from ORCHESTRATION.HYDRA import (
    ClusterConfig,
    ClusterNode,
    HYDRACluster,
    ProcessingStage,
)


@pytest.fixture
def cluster(tmp_path):
    c = HYDRACluster(config=ClusterConfig(base_path=str(tmp_path)))
    return c


def _add_node(cluster, name="test_node", group="Core", priority=5):
    node = ClusterNode(name=name, group=group, priority=priority)
    with cluster._cluster_lock:
        cluster.nodes[name] = node
    return node


class TestHYDRAInit:
    def test_create_cluster(self, cluster):
        assert cluster is not None
        assert cluster.base_path.exists()
        assert cluster.nodes == {}

    def test_default_config(self, cluster):
        assert cluster.config.auto_retry is True
        assert cluster.config.max_retries == 3
        assert len(HYDRACluster.STAGES) == 6


class TestHYDRANodes:
    def test_register_node(self, cluster):
        _add_node(cluster)
        assert "test_node" in cluster.nodes
        assert cluster.nodes["test_node"].group == "Core"

    def test_nodes_persist_in_state(self, cluster):
        _add_node(cluster, name="saved_node", group="Analysis", priority=3)
        cluster._save_state()
        c2 = HYDRACluster(config=ClusterConfig(base_path=str(cluster.base_path)))
        c2.load_state()
        assert "saved_node" in c2.nodes
        assert c2.nodes["saved_node"].group == "Analysis"


class TestHYDRAStatus:
    def test_empty_status(self, cluster):
        status = cluster.get_cluster_status()
        assert status["nodes"]["total"] == 0
        assert status["stages"]["successful"] == 0
        assert status["stages"]["failed"] == 0

    def test_populated_status(self, cluster):
        for i in range(3):
            _add_node(cluster, name=f"node_{i}", group="Core")
        status = cluster.get_cluster_status()
        assert status["nodes"]["total"] == 3


class TestHYDRASequence:
    def test_stages_constant(self, cluster):
        assert len(HYDRACluster.STAGES) == 6
        values = [s.value for s in HYDRACluster.STAGES]
        assert "Discovery" in values
        assert "Feedback" in values


class TestProcessingStage:
    def test_stages_order(self):
        stages = list(ProcessingStage)
        assert stages[0] == ProcessingStage.DISCOVERY
        assert stages[-1] == ProcessingStage.FEEDBACK
        assert len(stages) == 6
