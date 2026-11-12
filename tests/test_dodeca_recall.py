"""Dodecahedron recall: topology invariants + recall behavior.

Topology pins the implementation to G(10,2) -- 20 vertices, 30 edges,
3-regular, connected, diameter 5, girth 5, exactly 12 five-cycles (the
faces). Measured independently before baking in.
"""

import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed import dodeca_recall as D
from eve_unleashed import live_log as L


def _all_edges():
    seen = set()
    for v in range(D.N_VERTICES):
        for w in D.neighbors(v):
            seen.add((min(v, w), max(v, w)))
    return seen


def test_morph_space_diameters():
    assert D.diameter(10, D.petersen_neighbors) == 2
    assert D.diameter(20, D.dodeca_neighbors) == 5
    assert D.diameter(20, D.cycle_neighbors(20)) == 10
    assert D.diameter(20, D.complete_neighbors(20)) == 1
    assert D.diameter(32, D.hypercube(5)) == 5
    assert D.diameter(40, D.generalized_petersen(20)) == 7


def test_larger_solids_are_sound():
    n32, h32 = D.SHAPES["hypercube-32"]
    assert n32 == 32 and all(len(h32(v)) == 5 for v in range(32))
    assert len(D.bfs_distances(0, 32, h32)) == 32
    n40, g40 = D.SHAPES["g40"]
    assert n40 == 40 and all(len(g40(v)) == 3 for v in range(40))
    assert len(D.bfs_distances(0, 40, g40)) == 40


def _prism(v: int) -> list:
    """Triangular prism: 6 vertices, degree 3, diameter 2."""
    if not 0 <= v < 6:
        raise ValueError(v)
    if v < 3:
        return sorted([(v - 1) % 3, (v + 1) % 3, v + 3])
    i = v - 3
    return sorted([i, 3 + (i - 1) % 3, 3 + (i + 1) % 3])


def test_transmogrify_registers_any_valid_form():
    D.transmogrify("prism-6", 6, _prism)
    st = D.ShapeStore("prism-6")
    assert st.n == 6
    st.put("k", "v")
    assert st.recall("k")["exact"][0][0] == 0
    assert D.diameter(6, _prism) == 2


def test_transmogrify_refuses_broken_forms():
    import pytest as _p
    with _p.raises(ValueError):
        D.transmogrify("tiny", 3, lambda v: [])
    with _p.raises(ValueError):
        D.transmogrify("loopy", 6, lambda v: [v])
    with _p.raises(ValueError):
        D.transmogrify("split", 6,
                       lambda v: [u for u in _prism(v) if (u < 3) == (v < 3)])


def test_mutate_rewires_but_keeps_degree_and_cover():
    import random as _r
    new_name, diam = D.mutate_shape("dodecahedron", swaps=3,
                                    rng=_r.Random(316))
    n, nbr = D.SHAPES[new_name]
    assert n == 20 and all(len(nbr(v)) == 3 for v in range(20))
    assert len(D.bfs_distances(0, 20, nbr)) == 20
    st = D.ShapeStore(new_name)
    st.put("mk", "mv")
    assert [m[1] for m in st.recall("mk")["exact"]] == ["mk"]


def test_transsubstantiate_rehomes_every_entry():
    src = D.DodecaStore()
    src.put("a.example", {"rung": "archive"})
    src.put("b.example", {"rung": "render"})
    new = D.transsubstantiate(src, lambda k, v: v["rung"])
    assert new.shape == "dodecahedron"
    got = new.recall("archive")["exact"]
    assert [m[1] for m in got] == ["archive"]  # re-homed under rung
    assert got[0][2] == {"rung": "archive"}  # substance (value) survives
    assert [m[1] for m in new.recall("render")["exact"]] == ["render"]
    assert new.recall("a.example")["exact"] == []  # old keys are gone


def _toa_genome():
    return {"worm_id": "gold-1", "worm_type": "fetcher",
            "aggressiveness": 0.28, "min_confidence": 0.72,
            "cooldown_s": 1.5, "scan_interval_s": 60.0,
            "resource_budget_mb": 256.0, "mutation_rate": 0.15,
            "generation": 5, "parent_ids": ["t1-0"],
            "hunches": ["archive", "commoncrawl"]}


def test_projection_casts_all_three_forms():
    import json as _j
    st = D.DodecaStore()
    st.put("a.example", {"rung": "archive"})
    cast = D.cast_projection(st, _toa_genome(), {"gen": 5})
    assert set(cast) == {"json", "py", "md", "payload"}
    back = _j.loads(cast["json"])
    assert back["shape"] == "dodecahedron" and back["diameter"] == 5
    assert back["genome"]["worm_id"] == "gold-1"
    assert [k for k, _ in back["entries"]] == ["a.example"]
    assert "gold-1" in cast["md"] and "dodecahedron" in cast["md"]
    assert "archive" in cast["md"] and "aggressiveness: 0.28" in cast["md"]


def test_projection_py_form_is_importable_gold(tmp_path):
    import importlib.util as _ilu
    st = D.DodecaStore()
    st.put("a.example", {"rung": "archive"})
    (tmp_path / "stone.py").write_text(
        D.cast_projection(st, _toa_genome())["py"], encoding="utf-8")
    spec = _ilu.spec_from_file_location("stone", str(tmp_path / "stone.py"))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert [m[1] for m in mod.load().recall("a.example")["exact"]] \
        == ["a.example"]


def test_icosahedron_is_twelve_slots_degree_five_diameter_three():
    n, nbr = D.SHAPES["icosahedron"]
    assert n == 12 and all(len(nbr(v)) == 5 for v in range(12))
    assert all(len(D.bfs_distances(v, 12, nbr)) == 12 for v in range(12))
    assert D.diameter(12, nbr) == 3
    st = D.ShapeStore("icosahedron")
    st.put("hub", "v")
    assert st.recall("hub")["exact"][0][0] == 0


def test_girth_pins_known_solids():
    assert D.girth(20, D.dodeca_neighbors) == 5
    assert D.girth(6, _prism) == 3
    assert D.girth(12, D.icosahedron_neighbors) == 3


def test_guardrails_refuse_flabby_and_mushy_solids():
    import pytest as _p
    D.transmogrify("lean", 6, _prism, max_diameter=2, min_girth=3)  # passes
    with _p.raises(ValueError):
        D.transmogrify("flabby", 6, _prism, max_diameter=1)
    with _p.raises(ValueError):
        D.transmogrify("mushy", 6, _prism, min_girth=4)
    D.unregister_shape("lean")


def test_weighted_recall_routes_around_heavy_edges():
    st = D.DodecaStore()
    st.put("near", "n")
    st.put("far", "f")
    home = D.home_of("probe", 20)
    heavy = {frozenset((home, w)): 100.0 for w in D.dodeca_neighbors(home)}
    plain = st.recall("probe")
    priced = st.recall("probe", weights=heavy)
    assert [m[1] for m in plain["context"]] != []
    assert priced["exact"] == plain["exact"]  # exact never moves
    assert {m[1:] for m in priced["context"]} == \
        {m[1:] for m in plain["context"]}  # same entries, repriced
    assert priced["visited"] == plain["visited"] == 20  # full cover
    assert [m[0] for m in priced["context"]] != \
        [m[0] for m in plain["context"]]  # detours changed the costs


def test_uniform_weights_reproduce_bfs_order():
    st = D.DodecaStore()
    for k in ["a", "b", "c"]:
        st.put(k, k)
    uni = {frozenset((v, w)) for v in range(20) for w in D.dodeca_neighbors(v)}
    w = {e: 1.0 for e in uni}
    for key in ["a", "b", "c"]:
        assert [m[1:] for m in st.recall(key, weights=w)["context"]] == \
               [m[1:] for m in st.recall(key)["context"]]


def test_store_persists_and_resumes():
    st = D.ShapeStore("hypercube-32")
    st.put("a", {"rung": "archive"})
    st.put("b", "plain")
    d = st.to_dict()
    back = D.ShapeStore.from_dict(d)
    assert back.shape == "hypercube-32" and back.n == 32
    assert [m[1:] for m in back.recall("a")["exact"]] == \
        [("a", {"rung": "archive"})]
    assert [m[1] for m in back.recall("b")["exact"]] == ["b"]


def test_write_projection_lands_three_files(tmp_path):
    import os as _os
    st = D.DodecaStore()
    st.put("a.example", "v")
    paths = D.write_projection(st, _toa_genome(), str(tmp_path / "gold"))
    assert sorted(_os.path.basename(p) for p in paths) \
        == ["gold.json", "gold.md", "gold.py"]
    assert all(_os.path.getsize(p) > 0 for p in paths)


def test_conjoin_marries_two_stores():
    a, b = D.DodecaStore(), D.ShapeStore("petersen")
    a.put("shared", "from-a")
    a.put("only-a", 1)
    b.put("shared", "from-b")
    b.put("only-b", 2)
    m = D.conjoin(a, b)
    assert m.n == 20  # the larger solid wins by default
    assert sorted(x[2] for x in m.recall("shared")["exact"]) \
        == ["from-a", "from-b"]  # no silent winner
    assert m.recall("only-a")["exact"] and m.recall("only-b")["exact"]


def test_separate_partitions_completely():
    src = D.DodecaStore()
    src.put("a.example", {"rung": "archive"})
    src.put("b.example", {"rung": "render"})
    hot, cold = D.separate(src, lambda k, v: v["rung"] == "archive")
    assert [m[1] for m in hot.recall("a.example")["exact"]] == ["a.example"]
    assert hot.recall("b.example")["exact"] == []
    assert [m[1] for m in cold.recall("b.example")["exact"]] == ["b.example"]
    assert cold.recall("a.example")["exact"] == []


def test_dissolve_key_removes_and_counts():
    st = D.DodecaStore()
    st.put("gone", 1)
    st.put("gone", 2)
    st.put("stays", 3)
    assert D.dissolve_key(st, "gone") == 2
    assert st.recall("gone")["exact"] == []
    assert D.dissolve_key(st, "never-here") == 0
    assert [m[1] for m in st.recall("stays")["exact"]] == ["stays"]


def test_unregister_burns_custom_but_not_builtin():
    import pytest as _p
    D.transmogrify("doomed", 6, _prism)
    keeper = D.ShapeStore("doomed")
    keeper.put("k", "v")
    assert D.unregister_shape("doomed") == "doomed"
    assert "doomed" not in D.SHAPES
    assert keeper.recall("k")["exact"]  # holds its own reference: alive
    with _p.raises(ValueError):
        D.unregister_shape("dodecahedron")
    with _p.raises(ValueError):
        D.unregister_shape("never-registered")


def test_ferment_rots_what_the_lineage_ignores():
    st = D.DodecaStore()
    st.put("loved", {"rung": "archive"})
    st.put("dead", {"rung": "render"})
    removed = D.ferment(st, {"loved": 5.0, "dead": 0.2}, threshold=1.0)
    assert removed == [("dead", {"rung": "render"})]
    assert [m[1] for m in st.recall("loved")["exact"]] == ["loved"]
    assert st.recall("dead")["exact"] == []


def test_ferment_accepts_callable_salience():
    st = D.DodecaStore()
    st.put("a", 1)
    st.put("b", 2)
    removed = D.ferment(st, lambda k, v: float(v), threshold=2.0)
    assert removed == [("a", 1)] and st.recall("b")["exact"]


def test_heterogeneous_swarm_recalls_across_solids():
    sw = D.SwarmStore(["petersen", "g40"])
    assert sw.shapes == ["petersen", "g40"] and len(sw.stores) == 2
    for i in range(20):
        sw.put("m%d.example" % i, i)
    for i in range(20):
        res = sw.recall("m%d.example" % i)
        assert [m[1] for m in res["exact"]] == ["m%d.example" % i]
        assert res["exact"][0][0] == 0


def test_assemble_swarm_routes_hot_to_fast_solids():
    entries = [("hot-%d" % i, {"hot": True}) for i in range(6)] + \
              [("cold-%d" % i, {"hot": False}) for i in range(6)]
    sw = D.assemble_swarm(entries,
                          [("petersen", lambda k, v: v["hot"]),
                           ("g40", None)])
    assert sw.shapes == ["petersen", "g40"]
    for i in range(6):
        assert [m[1] for m in sw.stores[0].recall("hot-%d" % i)["exact"]] \
            == ["hot-%d" % i]
        assert [m[1] for m in sw.stores[1].recall("cold-%d" % i)["exact"]] \
            == ["cold-%d" % i]
    assert sw.count() == 12


def test_morph_shard_moves_entries_to_new_solid():
    sw = D.SwarmStore(["petersen", "dodecahedron"])
    sw.put_on(1, "keep", "v")
    D.morph_shard(sw, 1, "icosahedron")
    assert sw.shapes[1] == "icosahedron"
    assert [m[1] for m in sw.stores[1].recall("keep")["exact"]] == ["keep"]


def test_ferment_shard_rots_locally():
    sw = D.SwarmStore(["petersen", "petersen"])
    sw.put_on(0, "loved", 5.0)
    sw.put_on(1, "loved", 5.0)
    sw.put_on(0, "dead", 0.1)
    removed = D.ferment_shard(sw, 0, {"loved": 5.0, "dead": 0.1})
    assert removed == [("dead", 0.1)]
    assert [m[1] for m in sw.stores[1].recall("loved")["exact"]] == ["loved"]


def test_grow_cycles_heterogeneous_shapes():
    sw = D.SwarmStore(["petersen", "g40"])
    assert sw.grow(1) == 3
    assert sw.shapes == ["petersen", "g40", "petersen"]


def test_multiply_grows_and_shrinks_without_loss():
    import pytest as _p
    sw = D.SwarmStore("hypercube-32", shards=2)
    keys = ["m%d.example" % i for i in range(30)]
    for k in keys:
        sw.put(k, k)
    assert sw.count() == 30
    assert sw.grow(3) == 5
    assert sw.count() == 30
    for k in keys:
        assert [m[1] for m in sw.recall(k)["exact"]] == [k]
    assert sw.shrink(3) == 2
    assert sw.count() == 30
    for k in keys:
        assert [m[1] for m in sw.recall(k)["exact"]] == [k]
    with _p.raises(ValueError):
        sw.shrink(2)  # never below one


def test_swarm_hypercubes_fan_out_and_merge():
    sw = D.SwarmStore("hypercube-32", shards=4)
    keys = ["h%d.example" % i for i in range(40)]
    homes = {k: sw.put(k, k + "-val") for k in keys}
    assert len({s for s, _ in homes.values()}) > 1  # spread happened
    for k in keys:
        res = sw.recall(k)
        assert [m[1] for m in res["exact"]] == [k]
        assert res["exact"][0][0] == 0
        dists = [(m[0], m[1]) for m in res["context"]]
        assert dists == sorted(dists)  # merged nearest-first
    assert sw.recall("never-seen")["exact"] == []


def test_morph_preserves_every_entry():
    small = D.ShapeStore("dodecahedron")
    keys = [f"host-{i}.example" for i in range(25)]  # more keys than slots
    for k in keys:
        small.put(k, k + "-val")
    big = D.morph_store(small, "g40")
    assert big.shape == "g40" and big.n == 40
    for k in keys:
        res = big.recall(k)
        assert [m[1] for m in res["exact"]] == [k]
        assert res["exact"][0][0] == 0
    down = D.morph_store(big, "petersen")
    for k in keys:
        assert [m[1] for m in down.recall(k)["exact"]] == [k]
    assert len(_all_edges()) == 30
    assert all(len(D.neighbors(v)) == 3 for v in range(20))
    for v in range(20):
        assert len(D.bfs_distances(v, 20, D.dodeca_neighbors)) == 20  # connected


def test_diameter_is_five():
    diam = max(max(D.bfs_distances(v, 20, D.dodeca_neighbors).values())
               for v in range(20))
    assert diam == 5


def test_girth_is_five():
    def girth_at(s):
        depth = {s: 0}
        parent = {s: None}
        q = deque([s])
        best = None
        while q:
            x = q.popleft()
            for y in D.neighbors(x):
                if y == parent[x]:
                    continue
                if y in depth:
                    c = depth[x] + depth[y] + 1
                    if best is None or c < best:
                        best = c
                else:
                    parent[y] = x
                    depth[y] = depth[x] + 1
                    q.append(y)
        return best
    assert min(girth_at(v) for v in range(20)) == 5


def test_twelve_pentagonal_faces():
    found = set()
    def dfs(path):
        if len(path) == 5:
            if path[0] in D.neighbors(path[-1]):
                rots = [tuple(path[i:] + path[:i]) for i in range(5)]
                rev = tuple(reversed(path))
                rots += [tuple(rev[i:] + rev[:i]) for i in range(5)]
                found.add(min(rots))
            return
        for y in D.neighbors(path[-1]):
            if y not in path:
                dfs(path + [y])
    for v in range(20):
        dfs([v])
    assert len(found) == 12


def test_recall_finds_seeded_item_within_five_hops():
    store = D.DodecaStore()
    store.put("alpha", {"rung": "example", "state": "ok"})
    res = store.recall("alpha")
    assert len(res["exact"]) == 1
    assert res["exact"][0][1:] == ("alpha", {"rung": "example", "state": "ok"})
    assert res["exact"][0][0] == 0  # the key IS the address: instantaneous
    assert res["hops"] <= 5 and res["visited"] <= 20


def test_recall_reaches_antipode():
    store = D.DodecaStore()
    # plant a foreign entry at max distance from far-key's home vertex
    home = D.home_of("far-key", 20)
    dist = D.bfs_distances(home, 20, D.dodeca_neighbors)
    anti = max(dist, key=lambda v: dist[v])
    assert dist[anti] == 5
    store.slots[anti].append(("pole-marker", "x"))  # planted at far pole
    res = store.recall("far-key")
    far = [c for c in res["context"] if c[1] == "pole-marker"]
    assert len(far) == 1 and far[0][0] == 5
    assert res["hops"] == 5  # full coverage costs the diameter, no more


def test_recall_is_distance_ordered_and_matches_matrix_set():
    store = D.DodecaStore()
    for k in ["k1", "k2", "k3", "k1", "k2"]:
        store.put(k, k + "-val")
    for key in ["k1", "k2", "k3"]:
        walk = store.recall(key)
        scan = store.matrix_scan(key)
        assert [m[1:] for m in walk["exact"]] == [m[1:] for m in scan["exact"]]
        assert {m[1:] for m in walk["context"]} == {m[1:] for m in scan["context"]}
        dists = [m[0] for m in walk["context"]]
        assert dists == sorted(dists)  # nearest context first, always
        assert walk["visited"] <= scan["visited"]


def test_recall_unknown_key_is_empty_not_an_error():
    res = D.DodecaStore().recall("never-seen")
    assert res["exact"] == [] and res["context"] == []


def test_live_log_index_and_host_recall(tmp_path):
    log = str(tmp_path / "t.jsonl")
    L.emit("fetch", "intuition", path=log, url="https://example.com",
           guess="guess", confidence=0.9, predicted="example")
    L.emit("fetch", "answer", path=log, url="https://example.com",
           rung="example", state="ok", kind="live")
    L.emit("fetch", "answer", path=log, url="http://unseen-train.invalid/",
           rung="commoncrawl", state="blocked", kind="wall")
    store = D.index_live_log(log)
    res = D.recall_host(store, "example.com")
    assert len(res["exact"]) == 1
    assert res["exact"][0][2]["rung"] == "example"
    assert res["exact"][0][0] == 0  # own home vertex: instantaneous
    assert D.recall_host(store, "no-such-host")["exact"] == []
