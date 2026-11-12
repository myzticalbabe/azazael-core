"""Tests for CORE/WORMS/CLUSTERWORMS.py."""
import sys, os, unittest, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from CORE.WORMS.CLUSTERWORMS import (
    ClusterWORMZ, ClusterState, WormConfig, WormExecutor,
    ClusterResult, CLUSTER_REGISTRY, _resolve_worm_dynamic,
)


class TestClusterState(unittest.TestCase):
    def setUp(self):
        self.state = ClusterState("test_cluster")

    def test_add_result(self):
        self.state.add_result("worm1", {"status": "ok"})
        self.assertIn("worm1", self.state.results)
        self.assertEqual(self.state.results["worm1"]["status"], "ok")

    def test_send_message(self):
        self.state.send_message("wormA", "wormB", {"cmd": "do_thing"})
        msgs = self.state.get_messages("wormB")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["from"], "wormA")
        self.assertEqual(msgs[0]["data"]["cmd"], "do_thing")

    def test_broadcast(self):
        self.state.add_result("w1", {})
        self.state.add_result("w2", {})
        self.state.broadcast({"alert": "high_cpu"})
        msgs_w1 = self.state.get_messages("w1")
        msgs_w2 = self.state.get_messages("w2")
        self.assertEqual(len(msgs_w1), 1)
        self.assertEqual(len(msgs_w2), 1)

    def test_broadcast_exclude(self):
        self.state.add_result("w1", {})
        self.state.add_result("w2", {})
        self.state.broadcast({"alert": "test"}, exclude=["w1"])
        self.assertEqual(len(self.state.get_messages("w1")), 0)
        self.assertEqual(len(self.state.get_messages("w2")), 1)

    def test_clear_messages(self):
        self.state.send_message("a", "b", {})
        self.state.send_message("a", "c", {})
        self.state.clear_messages("b")
        self.assertEqual(len(self.state.get_messages("b")), 0)
        self.assertEqual(len(self.state.get_messages("c")), 1)

    def test_clear_all_messages(self):
        self.state.send_message("a", "b", {})
        self.state.send_message("a", "c", {})
        self.state.clear_messages()
        self.assertEqual(len(self.state.messages), 0)

    def test_thread_safety(self):
        def writer():
            for i in range(100):
                self.state.add_result(f"worm_{i}", {"i": i})
        threads = [threading.Thread(target=writer) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)
        self.assertEqual(len(self.state.results), 100)


class TestWormConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = WormConfig(name="test", module="TestMod")
        self.assertEqual(cfg.name, "test")
        self.assertEqual(cfg.module, "TestMod")
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.priority, 0)
        self.assertEqual(cfg.timeout, 30)
        self.assertEqual(cfg.retry, 1)
        self.assertEqual(cfg.params, {})

    def test_custom_values(self):
        cfg = WormConfig(name="x", module="X", enabled=False, priority=5, timeout=10, retry=3, params={"k": "v"})
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.priority, 5)
        self.assertEqual(cfg.timeout, 10)
        self.assertEqual(cfg.retry, 3)
        self.assertEqual(cfg.params, {"k": "v"})


class TestClusterRegistry(unittest.TestCase):
    def test_known_clusters_exist(self):
        for name in ["system", "network", "security", "analysis", "health", "full"]:
            self.assertIn(name, CLUSTER_REGISTRY)

    def test_cluster_has_worms(self):
        for name, info in CLUSTER_REGISTRY.items():
            self.assertIn("worms", info)
            self.assertGreater(len(info["worms"]), 0)
            self.assertIn("description", info)


class TestClusterWORMZ(unittest.TestCase):
    def test_init_default(self):
        cluster = ClusterWORMZ("system")
        self.assertEqual(cluster.cluster_type, "system")
        self.assertGreater(len(cluster.configs), 0)
        self.assertIn("cluster_", cluster.cluster_id)

    def test_init_unknown_type(self):
        cluster = ClusterWORMZ("nonexistent_type_xyz")
        self.assertEqual(cluster.cluster_type, "nonexistent_type_xyz")
        self.assertEqual(len(cluster.configs), 0)

    def test_add_worm(self):
        cluster = ClusterWORMZ()
        cluster.configs = []
        cluster.add_worm("TestWorm", "TestModule")
        self.assertEqual(len(cluster.configs), 1)
        self.assertEqual(cluster.configs[0].name, "TestWorm")
        self.assertEqual(cluster.configs[0].module, "TestModule")

    def test_remove_worm(self):
        cluster = ClusterWORMZ("system")
        initial_count = len(cluster.configs)
        name_to_remove = cluster.configs[0].name
        cluster.remove_worm(name_to_remove)
        self.assertEqual(len(cluster.configs), initial_count - 1)
        self.assertFalse(any(c.name == name_to_remove for c in cluster.configs))

    def test_set_parallel(self):
        cluster = ClusterWORMZ()
        cluster.set_parallel(True)
        self.assertTrue(cluster.parallel)
        cluster.set_parallel(False)
        self.assertFalse(cluster.parallel)

    def test_set_timeout(self):
        cluster = ClusterWORMZ()
        cluster.set_timeout(120)
        self.assertEqual(cluster.timeout, 120)

    def test_get_status(self):
        cluster = ClusterWORMZ("system")
        status = cluster.get_status()
        self.assertIn("cluster_id", status)
        self.assertIn("cluster_type", status)
        self.assertIn("version", status)
        self.assertIn("worm_count", status)
        self.assertIn("free_threaded", status)
        self.assertEqual(status["version"], "2.2.0")

    def test_build_from_category(self):
        cluster = ClusterWORMZ.build_from_category("orchestration")
        if cluster is not None:
            self.assertGreater(len(cluster.configs), 0)
            self.assertIn("cat_orchestration", cluster.cluster_type)

    def test_build_from_registry(self):
        cluster = ClusterWORMZ.build_from_registry(["VulnScanner", "PortAuditor"])
        self.assertEqual(len(cluster.configs), 2)
        names = [c.name for c in cluster.configs]
        self.assertIn("VulnScanner", names)
        self.assertIn("PortAuditor", names)

    def test_build_full_ecosystem(self):
        cluster = ClusterWORMZ.build_full_ecosystem()
        self.assertGreater(len(cluster.configs), 0)

    def test_aggregate_results(self):
        cluster = ClusterWORMZ("system")
        cluster.state.add_result("w1", {"success": True, "duration_ms": 100})
        cluster.state.add_result("w2", {"success": False, "duration_ms": 50})
        agg = cluster.aggregate_results()
        self.assertIsInstance(agg, ClusterResult)
        self.assertEqual(agg.success_count, 1)
        self.assertEqual(agg.failure_count, 1)
        self.assertEqual(agg.duration_ms, 150)

    def test_aggregate_empty(self):
        cluster = ClusterWORMZ()
        agg = cluster.aggregate_results()
        self.assertEqual(agg.success_count, 0)
        self.assertEqual(agg.failure_count, 0)

    def test_to_mermaid(self):
        cluster = ClusterWORMZ("system")
        mermaid = cluster.to_mermaid()
        self.assertIn("graph TB", mermaid)
        self.assertIn("CLUSTER:", mermaid)

    def test_to_mermaid_execution_flow(self):
        cluster = ClusterWORMZ("system")
        cluster.state.add_result("w1", {"success": True, "duration_ms": 10})
        flow = cluster.to_mermaid_execution_flow()
        self.assertIn("graph LR", flow)

    def test_repr(self):
        cluster = ClusterWORMZ("system")
        r = repr(cluster)
        self.assertIn("ClusterWORMZ", r)
        self.assertIn("system", r)

    def test_version(self):
        # Pin shape, not literal: a "2.2.0" literal fails on any legit bump.
        self.assertRegex(ClusterWORMZ.VERSION, r"^\d+\.\d+\.\d+$")


class TestResolveWormDynamic(unittest.TestCase):
    def test_unknown_worm_returns_none(self):
        # No contradiction with test_worm_telemetry.py:292-309: None here
        # is the RESOLVER contract (unresolvable name). The EXECUTOR
        # contract converts None into a recorded failure row
        # (CLUSTERWORMS.py:824-826), so unknown worms are still observed.
        result = _resolve_worm_dynamic("nonexistent_worm_xyz_999")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
