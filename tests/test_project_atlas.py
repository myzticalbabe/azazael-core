"""Tests for the cross-project provenance atlas."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.project_atlas import scan_projects


def test_scans_projects_and_relationships(tmp_path: Path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "main.py").write_text("import json\n", encoding="utf-8")
    (root / "tests" / "test_main.py").write_text("from src import main\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    atlas = scan_projects({"demo": root, "missing": root / "missing"})
    payload = json.loads(atlas.to_json())
    assert payload["coverage"]["existing_projects"] == 1
    assert payload["coverage"]["unavailable_projects"] == 1
    assert any(item["path"] == "src/main.py" and item["sha256"] for item in payload["files"])
    assert any(edge["kind"] == "imports" for edge in payload["edges"])
    assert "project" in atlas.to_html().lower()
    assert atlas.to_csv().splitlines()[0].startswith("project,path")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        test_scans_projects_and_relationships(Path(directory))
        print("project atlas tests passed")
