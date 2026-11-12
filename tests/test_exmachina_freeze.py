# coding: utf-8
"""Frozen Ex Machina gate inputs, and the pinned clock that goes with them.

WHY THIS TEST EXISTS
--------------------
The gates read the memory store and the `state/` artifacts, both of which the
orchestrator rewrites continuously. That made a gate result a statement about
the moment it ran: during one session the morph ledger grew 69 -> 72 rows, the
signal count 12 -> 13, and the mood band changed -- so a failure could not be
reproduced and two passes were not the same claim.

Two things are therefore locked here:

* the freeze is a faithful, SOURCE-READ-ONLY copy -- only the event types the
  Ex Machina surface reads, artifacts with their mtimes so the panel's ages
  still describe the snapshot, and a manifest so `--frozen=DIR` reuses rather
  than silently re-reading the live source; and
* the pinned clock (`AZAZAEL_FROZEN_NOW`) drives the ages, the pulse and the
  header, because otherwise two runs of identical data still differ on the
  timestamp and `archive 10%` / `archive 11%`.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import importlib.util
from unittest.mock import patch

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_project_root, os.path.join(_project_root, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _exmachina_freeze as fz                          # noqa: E402

_MEMORIES_SCHEMA = """
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    severity TEXT,
    tags TEXT,
    timestamp TEXT,
    created_at TEXT,
    event_uuid TEXT,
    correlation_id TEXT,
    audit_hash TEXT,
    schema_version INTEGER,
    trace_id TEXT
)
"""


def _make_source(path, counts=None):
    """A synthetic store shaped like the real one (same columns).

    Idempotent: several tests re-call it to model the LIVE source moving on
    after a freeze, so it replaces the file instead of tripping over the
    schema it already created.
    """
    counts = counts or {"evolution_result": 5, "evolution_promotion": 2,
                        "training_complete": 40}
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    try:
        con.execute(_MEMORIES_SCHEMA)
        for et, n in counts.items():
            for i in range(n):
                con.execute(
                    "INSERT INTO memories (session_id, source, event_type, data, "
                    "timestamp, severity, tags) VALUES (?,?,?,?,?,?,?)",
                    ("s", "src", et,
                     json.dumps({"n": i, "field": "x" if i % 2 else "y"}),
                     "2026-09-12T00:00:%02d" % (i % 60), "info", "t"))
        con.commit()
    finally:
        con.close()
    return path


class TestFreezeBuildsAReadableSnapshot(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="exfz-test-")
        self.src = _make_source(os.path.join(self.tmp, "live.db"))
        self.root = os.path.join(self.tmp, "repo")
        self.state = os.path.join(self.root, "state")
        os.makedirs(self.state)

    def _freeze(self, **kw):
        return fz.freeze(dest=os.path.join(self.tmp, "frozen"),
                         root=self.root, store=self.src, **kw)

    def test_only_the_requested_event_types_are_copied(self):
        frozen = self._freeze()
        con = sqlite3.connect(frozen["store"])
        got = {et: n for et, n in con.execute(
            "SELECT event_type, COUNT(*) FROM memories GROUP BY event_type")}
        con.close()
        self.assertEqual(got, {"evolution_result": 5, "evolution_promotion": 2})
        self.assertNotIn("training_complete", got,
                         "a wider copy would feed the gates data they never read")
        self.assertEqual(frozen["rows"]["evolution_result"], 5)
        self.assertNotIn("training_complete", frozen["rows"],
                         "only the requested event types are reported")

    def test_the_viewers_own_query_works_against_the_copy(self):
        frozen = self._freeze()
        con = sqlite3.connect(frozen["store"])
        rows = con.execute(
            "SELECT timestamp, data FROM memories WHERE event_type=? "
            "ORDER BY id DESC LIMIT ?", ("evolution_result", 120)).fetchall()
        con.close()
        self.assertEqual(len(rows), 5)
        self.assertEqual(json.loads(rows[0][1])["n"], 4)      # newest first

    def test_per_type_limit_bounds_the_copy(self):
        frozen = self._freeze(per_type=2)
        con = sqlite3.connect(frozen["store"])
        n = con.execute("SELECT COUNT(*) FROM memories "
                        "WHERE event_type='evolution_result'").fetchone()[0]
        con.close()
        self.assertEqual(n, 2)

    def test_artifacts_are_copied_with_their_mtimes(self):
        for name in ("tensor_genome.json", "proteus_morph.jsonl"):
            p = os.path.join(self.state, name)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write('{"a": 1}\n')
            os.utime(p, (1700000000, 1700000000))
        frozen = self._freeze()
        self.assertIn("tensor_genome.json", frozen["files"])
        for name in frozen["files"]:
            src = os.path.getmtime(os.path.join(self.state, name))
            dst = os.path.getmtime(os.path.join(frozen["state"], name))
            self.assertAlmostEqual(src, dst, delta=1.0,
                                   msg="%s lost its mtime, so the panel's ages "
                                       "would describe the copy" % name)

    def test_oversized_artifacts_are_skipped(self):
        small = os.path.join(self.state, "small.json")
        big = os.path.join(self.state, "big.json")
        with open(small, "w", encoding="utf-8") as fh:
            fh.write("x")                # 1 byte: at the limit, kept
        with open(big, "w", encoding="utf-8") as fh:
            fh.write("xx")               # 2 bytes: over it, skipped
        with patch.object(fz, "_MAX_ARTIFACT_BYTES", 1):
            frozen = self._freeze()
        self.assertIn("small.json", frozen["files"])
        self.assertNotIn("big.json", frozen["files"])

    def test_the_source_store_is_never_written(self):
        before = (os.path.getsize(self.src), os.path.getmtime(self.src))
        con = sqlite3.connect(self.src)
        rows_before = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        con.close()
        self._freeze()
        after = (os.path.getsize(self.src), os.path.getmtime(self.src))
        con = sqlite3.connect(self.src)
        rows_after = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        con.close()
        self.assertEqual(before, after)
        self.assertEqual(rows_before, rows_after)


class TestReuseAndFingerprint(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="exfz-reuse-")
        self.src = _make_source(os.path.join(self.tmp, "live.db"))
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.root, "state"))
        with open(os.path.join(self.root, "state", "tensor_genome.json"), "w",
                  encoding="utf-8") as fh:
            fh.write('{"genes": []}')
        self.dest = os.path.join(self.tmp, "frozen")

    def test_fingerprint_names_the_inputs(self):
        frozen = fz.freeze(dest=self.dest, root=self.root, store=self.src)
        fp = fz.fingerprint(frozen)
        self.assertNotEqual(fp["store"], "absent")
        self.assertEqual(fp["rows"]["evolution_result"], 5)
        self.assertEqual(fp["files_hashed"], 1)
        self.assertIsNotNone(fp["now"], "a snapshot without a clock is not frozen")
        self.assertIn("clock pinned", fz.format_fingerprint(fp))

    def test_fingerprint_is_stable_for_the_same_frozen_set(self):
        frozen = fz.freeze(dest=self.dest, root=self.root, store=self.src)
        self.assertEqual(fz.fingerprint(frozen), fz.fingerprint(frozen))

    def test_reuse_reads_the_manifest_not_the_live_source(self):
        frozen = fz.freeze(dest=self.dest, root=self.root, store=self.src)
        first = fz.fingerprint(frozen)
        # the live source moves on, exactly as the orchestrator would move it
        _make_source(self.src, {"evolution_result": 99, "evolution_promotion": 1})
        with open(os.path.join(self.root, "state", "tensor_genome.json"), "w",
                  encoding="utf-8") as fh:
            fh.write('{"genes": ["changed"]}')
        again = fz.load_frozen(self.dest)
        self.assertEqual(fz.fingerprint(again), first,
                         "reusing a snapshot must not re-read the live store")
        self.assertEqual(again["rows"]["evolution_result"], 5)

    def test_reuse_refuses_a_directory_that_is_not_frozen(self):
        empty = os.path.join(self.tmp, "not-frozen")
        os.makedirs(empty)
        with self.assertRaises(RuntimeError):
            fz.load_frozen(empty)

    def test_reuse_refuses_a_missing_store(self):
        fz.freeze(dest=self.dest, root=self.root, store=self.src)
        os.remove(os.path.join(self.dest, "memory_store.db"))
        with self.assertRaises(RuntimeError):
            fz.load_frozen(self.dest)


class TestGateFlagAndClock(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="exfz-flag-")
        self.src = _make_source(os.path.join(self.tmp, "live.db"))
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.root, "state"))
        self.dest = os.path.join(self.tmp, "frozen")

    def test_no_flag_returns_none_and_changes_nothing(self):
        with patch.dict(os.environ, {"AZAZAEL_MEMORY_DIR": "sentinel"},
                        clear=False):
            argv = ["gate.py", "--frozen-mode-off"]
            self.assertIsNone(fz.gate_freeze_from_argv(argv, root=self.root))
            self.assertEqual(os.environ["AZAZAEL_MEMORY_DIR"], "sentinel")
            self.assertEqual(argv, ["gate.py", "--frozen-mode-off"])

    def test_freeze_builds_sets_the_env_and_strips_the_flag(self):
        argv = ["gate.py", "--freeze", "--other"]
        with patch.dict(os.environ, {}, clear=False):
            frozen = fz.gate_freeze_from_argv(argv, root=self.root, quiet=True)
            self.assertEqual(argv, ["gate.py", "--other"])
            self.assertIsNotNone(frozen)
            self.assertEqual(os.environ["AZAZAEL_MEMORY_DIR"], frozen["store"])
            self.assertEqual(os.environ["AZAZAEL_FROZEN_NOW"],
                             "%.3f" % frozen["now"])
            os.environ.pop("AZAZAEL_MEMORY_DIR", None)
            os.environ.pop("AZAZAEL_FROZEN_NOW", None)

    def test_frozen_dir_is_reused_rather_than_rebuilt(self):
        built = fz.freeze(dest=self.dest, root=self.root, store=self.src)
        _make_source(self.src, {"evolution_result": 50})
        argv = ["gate.py", "--frozen=%s" % self.dest]
        frozen = fz.gate_freeze_from_argv(argv, root=self.root, quiet=True)
        self.assertEqual(argv, ["gate.py"])
        self.assertEqual(frozen["rows"]["evolution_result"],
                         built["rows"]["evolution_result"])
        os.environ.pop("AZAZAEL_MEMORY_DIR", None)
        os.environ.pop("AZAZAEL_FROZEN_NOW", None)

    def test_apply_viewer_repoints_only_globals_that_exist(self):
        class Stub:
            DB = "live.db"
            _EX_STATE = "live/state"
            _EX_MORPH = "live/state/morph.jsonl"

        frozen = {"store": "frozen.db", "state": "frozen/state"}
        applied = fz.apply_viewer(Stub, frozen)
        self.assertEqual(sorted(applied), ["DB", "_EX_MORPH", "_EX_STATE"])
        self.assertEqual(Stub.DB, "frozen.db")
        self.assertEqual(Stub._EX_MORPH,
                         os.path.join("frozen/state", "proteus_morph.jsonl"))

        class Bare:
            pass
        self.assertEqual(fz.apply_viewer(Bare, frozen), [])
        self.assertEqual(fz.apply_tier(Bare, frozen), [])


class TestPinnedClockInTheViewer(unittest.TestCase):
    """The pinned clock is what makes two frozen runs byte-comparable."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(_project_root, "scripts", "exmachina_live_monitor.py")
        spec = importlib.util.spec_from_file_location("_exm_view_frozen", path)
        cls.lm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.lm)

    def test_pinned_now_drives_the_header_clock(self):
        with patch.dict(os.environ, {"AZAZAEL_FROZEN_NOW": "1700000000.000"}):
            self.assertEqual(self.lm._ex_now(), 1700000000.0)
            header = self.lm.render(size=(100, 30)).split("\n")[1]
            self.assertIn("2023-11-14", header,
                          "the header must follow the pinned clock: %r" % header)

    def test_unpinned_now_is_the_wall_clock(self):
        import time
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AZAZAEL_FROZEN_NOW", None)
            self.assertLess(abs(self.lm._ex_now() - time.time()), 5.0)

    def test_ages_are_a_function_of_the_pinned_clock(self):
        """Two runs of the same snapshot must report the SAME ages.

        `ex_ages()` is what the panel's age columns read, and it is
        `pinned_now - mtime`. Pinning the clock is therefore what makes two
        runs of ONE snapshot agree, instead of differing by however long the
        gate took between them.
        """
        state = tempfile.mkdtemp(prefix="exfz-ages-")
        art = os.path.join(state, "fitness_oracle.json")
        with open(art, "w", encoding="utf-8") as fh:
            fh.write("{}")
        os.utime(art, (1700000000, 1700000000))
        saved = self.lm._EX_STATE
        self.lm._EX_STATE = state
        try:
            with patch.dict(os.environ, {"AZAZAEL_FROZEN_NOW": "1700000000.000"}):
                a = self.lm.ex_ages()
                b = self.lm.ex_ages()
            self.assertEqual(a, b)
            self.assertAlmostEqual(a["oracle"], 0.0, delta=0.01)
            with patch.dict(os.environ, {"AZAZAEL_FROZEN_NOW": "1700000060.000"}):
                c = self.lm.ex_ages()
            self.assertAlmostEqual(c["oracle"], 60.0, delta=0.01)
            self.assertEqual(self.lm._ex_age(c["oracle"]), "1m")
        finally:
            self.lm._EX_STATE = saved
            shutil.rmtree(state, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
