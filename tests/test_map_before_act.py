"""EP237 (map-before-act) has an act-time stage, not just map substrate.

The rescore found this row MIS-BUCKETED: the census recorded it hostless, but the
map substrate ships (`codebase_graph.py`, `project_atlas.py`, `graph_artifacts.py`,
`graph_drift.py`) -- only the *act-time* stage was missing, i.e. the thing an
execution layer (L4) runs before it acts on a target. `MapBeforeAct` is that
stage and `CodebaseGraph.consult` is its query.

The assertions, and why each is the discriminating one:

  * a real target answers with what it imports, what imports it, and whether it
    sits in a cycle -- the three facts an action would otherwise have to discover
    by failing;
  * a target named by FILE PATH resolves to the same node as its module id, so a
    caller holding a path is not forced to guess the module id;
  * an ABSENT target is reported as absent with a note, never as an empty success
    -- and an AMBIGUOUS stem resolves to None rather than a best guess, because
    acting on the wrong node is worse than reporting that the map cannot decide;
  * consulting does not mutate the graph, so the substrate's build and drift
    behaviour is unchanged (the diff of a graph against itself stays empty);
  * the stage records consultations where the caller asked, and builds the map
    once rather than per action.

Fixtures are written to `tmp_path`, so nothing here depends on the live tree.
"""

import json
import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _tree(tmp_path):
    """svc/ holds an import cycle (a<->b) and an isolated module (c);
    svc2/ holds a same-named module, so the bare stem `a` is ambiguous."""
    for package in ("svc", "svc2"):
        (tmp_path / package).mkdir()
        (tmp_path / package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "svc" / "a.py").write_text("import svc.b\n", encoding="utf-8")
    (tmp_path / "svc" / "b.py").write_text("import svc.a\n", encoding="utf-8")
    (tmp_path / "svc" / "c.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "svc2" / "a.py").write_text("OTHER = 2\n", encoding="utf-8")
    return tmp_path


def test_consult_answers_dependencies_dependents_and_cycle(tmp_path):
    from CORE.graph_artifacts import consult_map

    consultation = consult_map("svc.a", root=_tree(tmp_path))

    assert consultation.found is True
    assert consultation.node_id == "svc.a"
    assert consultation.path == "svc/a.py"
    assert consultation.dependencies == ["svc.b"], "a imports b"
    assert consultation.dependents == ["svc.b"], "b imports a"
    assert consultation.cycle == ["svc.a", "svc.b"], "a<->b is the cycle"
    assert any("import cycle" in note for note in consultation.notes)


def test_consult_resolves_a_file_path_to_the_module_node(tmp_path):
    from CORE.graph_artifacts import consult_map

    root = _tree(tmp_path)
    by_path = consult_map(str(root / "svc" / "c.py"), root=root)
    by_id = consult_map("svc.c", root=root)

    assert by_path.found is True
    assert by_path.node_id == by_id.node_id == "svc.c"
    assert by_path.dependencies == []
    assert any("no outgoing import edges" in note for note in by_path.notes)


def test_absent_and_ambiguous_targets_are_reported_not_guessed(tmp_path):
    from CORE.graph_artifacts import consult_map

    root = _tree(tmp_path)
    missing = consult_map("does_not_exist_at_all", root=root)
    ambiguous = consult_map("a", root=root)

    assert missing.found is False
    assert "target not present in the map" in missing.notes
    assert ambiguous.found is False, "two nodes share the stem `a`; do not guess"
    assert any(note.startswith("closest node ids: ") for note in ambiguous.notes)
    assert ambiguous.node_id == "" and ambiguous.path == ""


def test_consulting_does_not_mutate_the_map(tmp_path):
    from CORE.codebase_graph import build_graph

    graph = build_graph(_tree(tmp_path))
    before = (len(graph.nodes), len(graph.edges), len(graph.cycles()))
    graph.consult("svc.a")
    after = (len(graph.nodes), len(graph.edges), len(graph.cycles()))

    assert before == after, "the act-time stage must not edit the substrate"
    diff = graph.snapshot_diff(graph.to_dict())
    assert not any(diff.values()), "a graph compared with itself cannot drift"


def test_stage_builds_the_map_once_and_records_consultations(tmp_path):
    from CORE.graph_artifacts import MapBeforeAct

    root = _tree(tmp_path)
    log = root / "state" / "consultations.jsonl"
    stage = MapBeforeAct(root, record_to=log)

    assert stage._graph is None, "opt-in: constructing the stage builds nothing"
    stage.consult("svc.a")
    stage.consult("svc.c")

    assert stage.graph is stage.graph, "the map is built once, not per action"
    assert len(stage.consultations) == 2
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [item["node_id"] for item in lines] == ["svc.a", "svc.c"]
    assert all(item["found"] is True for item in lines)


def test_record_to_none_writes_nothing(tmp_path):
    from CORE.graph_artifacts import MapBeforeAct

    root = _tree(tmp_path)
    stage = MapBeforeAct(root)
    stage.consult("svc.a")

    assert list(root.rglob("*.jsonl")) == [], "recording is opt-in"


def test_atlas_annotates_a_consultation_without_rescanning(tmp_path):
    from CORE.graph_artifacts import consult_map
    from CORE.project_atlas import ProjectAtlas

    root = _tree(tmp_path)
    atlas = ProjectAtlas({"fixture": root}).scan()
    consultation = consult_map("svc.a", root=root, atlas=atlas)

    assert any(note.startswith("atlas: ") for note in consultation.notes)
    assert any("source" in note for note in consultation.notes)
