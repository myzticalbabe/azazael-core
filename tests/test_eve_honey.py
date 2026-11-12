"""Honey flow: propagate, residual-classify, Root-audit."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.honey import propagate, residual_report, root_audit


def _answer_log(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for url, state in rows:
            f.write(json.dumps({"event": "answer", "url": url,
                                "state": state}) + "\n")


def test_propagate_then_audit_passes(tmp_path):
    fix = tmp_path / "fx.json"
    fix.write_text('{"urls": ["a"]}')
    man = propagate(str(fix), str(tmp_path / "honey"), note="t",
                    artifacts={"fx": str(fix)})
    assert (tmp_path / "honey" / "fixture.json").exists()
    verdict = root_audit(str(tmp_path / "honey" / "honey.json"))
    assert verdict["verdict"] == "PASS" and verdict["clean"] is True


def test_relative_artifact_paths_resolve_against_manifest(tmp_path):
    # The Root travels: bundled blobs are cited relatively, so a fresh
    # clone (no /tmp) still audits PASS.
    fix = tmp_path / "fx.json"
    fix.write_text("{}")
    blobdir = tmp_path / "honey" / "blobs"
    blobdir.mkdir(parents=True)
    (blobdir / "pop.json").write_text("{}", encoding="utf-8")
    man = propagate(str(fix), str(tmp_path / "honey"),
                    artifacts={})
    from eve_unleashed.honey import sha_file
    man["artifacts"] = {"pop": {
        "path": "blobs/pop.json",
        "sha": sha_file(str(blobdir / "pop.json"))}}
    import json
    json.dump(man, open(tmp_path / "honey" / "honey.json", "w",
                         encoding="utf-8"), indent=2)
    verdict = root_audit(str(tmp_path / "honey" / "honey.json"))
    assert verdict["verdict"] == "PASS"


def test_tampered_fixture_fails_audit_by_name(tmp_path):
    fix = tmp_path / "fx.json"
    fix.write_text('{"urls": ["a"]}')
    propagate(str(fix), str(tmp_path / "honey"))
    (tmp_path / "honey" / "fixture.json").write_text('{"urls": ["b"]}')
    verdict = root_audit(str(tmp_path / "honey" / "honey.json"))
    assert verdict["verdict"] == "FAIL"
    assert any(leg["name"] == "fixture.sha" and leg["status"] == "error"
               for leg in verdict["legs"])


def test_missing_artifact_fails_audit(tmp_path):
    # Propagate refuses to cite what it cannot hash; audit catches what
    # vanished after a good propagate. Both shapes are covered.
    fix = tmp_path / "fx.json"
    fix.write_text("{}")
    raised = False
    try:
        propagate(str(fix), str(tmp_path / "honey"),
                  artifacts={"ghost": str(tmp_path / "nope.json")})
    except FileNotFoundError:
        raised = True  # the gate held: unhashable cannot be cited
    assert raised
    art = tmp_path / "pop.json"
    art.write_text("{}")
    propagate(str(fix), str(tmp_path / "honey"),
              artifacts={"pop": str(art)})
    art.unlink()
    verdict = root_audit(str(tmp_path / "honey" / "honey.json"))
    assert verdict["verdict"] == "FAIL"


def test_residuals_pin_stable_and_flag_flips(tmp_path):
    log = tmp_path / "t.jsonl"
    _answer_log(str(log),
                [("u-stable", "ok")] * 5 + [("u-flip", "ok"),
                                             ("u-flip", "blocked"),
                                             ("u-flip", "ok")])
    rep = residual_report([str(log)])
    assert rep["skipped_lines"] == 0
    assert rep["urls"]["u-stable"]["status"] == "PINNED"
    assert rep["urls"]["u-flip"]["status"] == "LIVE"
    assert rep["urls"]["u-flip"]["flips"] == 2
    with open(str(log), "a", encoding="utf-8") as f:
        f.write("not json\n")
    rep2 = residual_report([str(log)])
    assert rep2["skipped_lines"] == 1  # counted, never trusted
