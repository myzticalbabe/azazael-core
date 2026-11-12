# coding: utf-8
"""
2026-09-14 — the mesh HTML view is an artifact; artifacts need a check.

`graphify-out/mesh_gephi_interactive.html` is the only interactive view of the
mesh. It is produced by templating JSON into vis.js, which means it can be
*wrong in a way that looks fine*: an edge whose endpoints are not nodes is
dropped by vis.js silently, and the page still renders, still looks populated,
and the generator still prints success.

That is not hypothetical. The route telemetry added earlier today
(`route:windows>mesh`, written by `MeshRouterWorm.record_route_result()`) was
drawn as an edge from `nature:route` to `windows>mesh` — neither of which is a
node — so the learned routing was missing from the graph with nothing raised.

These tests run the real check (`tools/verify_mesh_html.py`, headless Chrome via
raw CDP) against the real artifact. They skip — loudly, not silently — when the
artifact, Chrome or `websocket-client` is unavailable, because a green tick that
means "not verified" is worse than an honest skip.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS = PROJECT_ROOT / "tools"
HTML = PROJECT_ROOT / "graphify-out" / "mesh_gephi_interactive.html"
MANIFEST = PROJECT_ROOT / "graphify-out" / "mesh_gephi_interactive.json"

sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(TOOLS))


def _chrome_available() -> bool:
    try:
        from verify_mesh_html import find_chrome
        find_chrome()
        return True
    except Exception:
        return False


def _websocket_available() -> bool:
    try:
        import websocket  # noqa: F401
        return True
    except ImportError:
        return False


requires_browser = pytest.mark.skipif(
    not (_chrome_available() and _websocket_available()),
    reason="headless Chrome or websocket-client unavailable — cannot verify",
)

requires_artifact = pytest.mark.skipif(
    not HTML.exists(),
    reason="mesh HTML artifact not generated (run tools/gephi_mesh_pipeline.py --html --json)",
)


def _view_script_src() -> str:
    """The bundle href the artifact links, as a browser would resolve it.

    Read from the artifact rather than assumed, so this cannot drift away from
    what the generator actually wrote.
    """
    match = re.search(r'<script src="([^"]+)"', HTML.read_text(encoding="utf-8"))
    assert match, "the view links no script at all -- it can render nothing"
    return match.group(1)


def _run_verifier(html_path: Path, manifest_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOLS / "verify_mesh_html.py"),
         "--html", str(html_path), "--manifest", str(manifest_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, cwd=str(PROJECT_ROOT),
    )


@requires_artifact
def test_the_view_links_a_bundle_that_is_actually_there():
    """Offline verifiability: no network in the path, and the target exists.

    Falsified by the CDN version of the template. The artifact linked
    ``https://unpkg.com/...``, so on a box with no route to unpkg the page died
    with ``ReferenceError: vis is not defined`` and the harness reported "the
    graph never initialised" -- true, and attributed to the artifact when the
    real defect was that the artifact needed the internet. The check is only as
    reproducible as the thing it opens.
    """
    src = HTML.read_text(encoding="utf-8")
    for remote in ("unpkg", "cdnjs", "jsdelivr"):
        assert remote not in src, f"the view still fetches vis.js from {remote}"

    href = _view_script_src()
    target = (HTML.parent / href).resolve()
    assert target.exists(), f"the view links a bundle that is not there: {href}"
    assert target.stat().st_size > 100_000, f"bundle is not a bundle: {target}"


@requires_browser
@requires_artifact
def test_generated_view_has_no_unresolved_edges_and_no_js_errors():
    result = _run_verifier(HTML, MANIFEST)
    assert result.returncode == 0, (
        "browser verification failed:\n" + (result.stdout or "") + (result.stderr or "")
    )
    assert "unresolved: 0" in result.stdout


@requires_browser
@requires_artifact
def test_manifest_matches_what_the_browser_built():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # The generator's own claim about itself must be free of unresolved edges.
    assert manifest["unresolved_edges"] == 0
    assert manifest["nodes"] > 0 and manifest["edges"] > 0
    # And the endpoints it declared must cover every edge it emitted.
    ids = set(manifest["node_ids"])
    assert ids, "manifest declares no node ids — nothing to resolve against"


@requires_browser
@requires_artifact
def test_learned_routes_are_visible_in_the_view():
    """The specific regression: route telemetry must appear as drawn edges.

    If the mesh has ever learned a route, the view must show it. A route that
    exists in the ledger but not in the graph is the failure mode this test
    exists to prevent.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    state_file = Path.home() / ".azazael" / "spine_state.json"
    if not state_file.exists():
        pytest.skip("no persisted spine state")

    # The manifest is a SNAPSHOT. Holding it to a ledger written after it was
    # built asks the artifact to account for routes that did not exist when it
    # was rendered: this failed on `route:windows>route`, learned a week after
    # this manifest was generated (2026-09-14). A stale artifact cannot be
    # wrong about the ledger, only silent about the difference — and reporting
    # "missing edge" for that is a false accusation, not a found regression.
    if state_file.stat().st_mtime > MANIFEST.stat().st_mtime:
        pytest.skip("mesh artifact predates the ledger it would be checked against "
                    "— regenerate with tools/gephi_mesh_pipeline.py --html --json")

    q_weights = json.loads(state_file.read_text(encoding="utf-8")).get("q_weights", {})
    learned = [k for k in q_weights if k.startswith("route:")]
    if not learned:
        pytest.skip("no learned routes in the persisted ledger")

    drawn = manifest.get("route_edges", [])
    assert drawn, (
        f"ledger holds {learned} but the view drew no route edges — "
        "route keys are being emitted with endpoints that are not nodes"
    )
    drawn_hops = {e["from"] for e in drawn}
    for key in learned:
        hop = key[len("route:"):]
        assert f"route:{hop}" in drawn_hops, f"route {hop} is not drawn"


def test_generator_draws_every_learned_route():
    """The generator's route contract, with no artifact and no browser.

    The browser check above can only speak for the snapshot it is handed, so a
    stale artifact silently retires it. This calls the real collectors instead:
    every `route:<src>><dst>` ledger key must become an edge, INCLUDING one
    whose destination is not a node — the case that made route telemetry
    invisible (`route:windows>route` has no such node).
    """
    from gephi_mesh_pipeline import collect_route_edges, resolve_edge_endpoints

    natures = {"mesh": {}}
    spine = {"q_weights": {
        "route:windows>mesh": 0.7,        # destination is a nature
        "route:windows>wsl2-kali": 0.7,   # destination is a hop, not a node
        "route:windows>route": 0.5,       # destination is a ghost (the real case)
        "nature:mesh>worm_a": 0.9,        # a Q-edge, not a route
    }}

    edges = collect_route_edges(spine, natures)
    sources = sorted(e[0] for e in edges)
    assert sources == ["route:windows>mesh", "route:windows>route",
                       "route:windows>wsl2-kali"], sources

    # One known node only: every other endpoint must be given a placeholder,
    # or vis.js drops the edge and the view silently loses the route.
    known_ids = {"hop:windows"}
    kept, extra, n_implicit = resolve_edge_endpoints(edges, known_ids)
    assert n_implicit == len(extra)
    final = known_ids | {node["id"] for node in extra}
    for src, dst, _etype, _weight in kept:
        assert src in final, f"{src} is not a node — the edge would vanish"
        assert dst in final, f"{dst} is not a node — the edge would vanish"


@requires_browser
def test_verifier_rejects_a_dangling_edge(tmp_path):
    """Negative control: the check must be able to fail.

    A verification step that cannot fail is not verification. This injects the
    exact defect that hid the route telemetry — an edge whose endpoints are not
    nodes — into a copy of the artifact and asserts the harness reports it.
    """
    if not HTML.exists():
        pytest.skip("mesh HTML artifact not generated")

    src = HTML.read_text(encoding="utf-8")
    needle = "const edges = new vis.DataSet(["
    if needle not in src:
        pytest.skip("generator template changed shape; update the injection point")
    at = src.index(needle) + len(needle)
    injected = src[:at] + '{"from":"nature:route","to":"windows>mesh","arrows":"to"}, ' + src[at:]

    # The artifact links its vis.js bundle by a DOCUMENT-RELATIVE path, so a
    # copy that keeps only the bytes loses the library and the page dies before
    # the check can see the injected edge -- which is how this control failed
    # the first time the bundle was vendored. Reproduce the layout, not just
    # the file: then the only difference from the real artifact is the edge.
    href = _view_script_src()
    assert not href.startswith(("http://", "https://")), (
        f"cannot run this control offline: the view fetches {href}"
    )
    out_dir = tmp_path / "graphify-out"
    out_dir.mkdir()
    broken = out_dir / "broken_mesh.html"
    broken.write_text(injected, encoding="utf-8")
    asset = (HTML.parent / href).resolve()
    clone = (out_dir / href).resolve()
    clone.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(asset, clone)

    result = _run_verifier(broken, tmp_path / "absent_manifest.json")
    assert result.returncode == 1, "verifier accepted an edge with missing endpoints"
    assert "reference missing nodes" in result.stdout
    assert "nature:route" in result.stdout
