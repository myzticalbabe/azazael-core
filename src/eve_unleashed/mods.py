"""Native game modding for Eve: patch configs, swap assets, revert clean.

A mod is a directory with mod.json {patches: [{file, key, value}],
assets: [{src, dst}]} applied onto a game directory. Every touched file
is backed up under .evebak with a manifest, so `revert` restores the
game byte-identical. JSON configs take dotted keys; anything else takes
KEY=VALUE lines. This is the first modding slice: data and assets, never
executables -- Eve mods files, not binaries.
"""
import json
import random
import shutil
from pathlib import Path

from .model_registry import note_failure


def _read_mod(mod_dir):
    spec = json.loads((Path(mod_dir) / "mod.json").read_text(
        encoding="utf-8"))
    return spec.get("patches", []), spec.get("assets", [])


def _backup(game_dir, rel, bak_dir):
    src = Path(game_dir) / rel
    if not src.is_file():
        raise ValueError("game file missing: %s" % rel)
    dst = Path(bak_dir) / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        dst.write_bytes(src.read_bytes())
    return src


def _patch_json(path, key, value):
    doc = json.loads(path.read_text(encoding="utf-8"))
    node = doc
    *trail, last = key.split(".")
    for part in trail:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[last] = value
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _patch_kv(path, key, value):
    text = path.read_text(encoding="utf-8")
    lines, hit = [], False
    for line in text.splitlines():
        if line.split("=")[0].strip() == key:
            lines.append("%s=%s" % (key, value))
            hit = True
        else:
            lines.append(line)
    if not hit:
        lines.append("%s=%s" % (key, value))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_mod(game_dir, mod_dir):
    """Apply a mod; returns the manifest list of backed-up relative paths."""
    game, mod = Path(game_dir), Path(mod_dir)
    bak = game / ".evebak"
    patches, assets = _read_mod(mod)
    manifest = []
    for p in patches:
        target = _backup(game, p["file"], bak)
        try:
            if target.suffix == ".json":
                _patch_json(target, p["key"], p["value"])
            else:
                _patch_kv(target, p["key"], p["value"])
        except Exception as e:
            note_failure("mods:patch", e)
            raise
        manifest.append(p["file"])
    for a in assets:
        target = _backup(game, a["dst"], bak)
        blob = (mod / a["src"]).read_bytes()
        target.write_bytes(blob)
        manifest.append(a["dst"])
    (bak / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                       encoding="utf-8")
    return manifest


def evolve_mod(game_dir, mod_dir, generations=5, seed=7, fitness=None):
    """Evolve a mod across generations: jitter numeric patch values with
    a seeded RNG, apply each generation to a copy, score fitness as the
    negative distance to user-supplied target values, keep the best.
    fitness maps 'file::dotted.key' to a numeric target; default scores
    higher total numeric values. Returns (best_gen, best_score, history)."""
    game = Path(game_dir)
    evo = game / ".eveevo"
    if evo.exists():
        shutil.rmtree(evo)
    evo.mkdir(parents=True)
    rng = random.Random(seed)
    patches, assets = _read_mod(mod_dir)
    numeric = [p for p in patches
               if isinstance(p["value"], (int, float))]
    if not numeric:
        raise ValueError("evolution needs at least one numeric patch")
    history = []
    for g in range(generations):
        gen_dir = evo / ("gen%d" % g)
        shutil.copytree(game, gen_dir,
                        ignore=shutil.ignore_patterns(".evebak",
                                                     ".eveevo"))
        trial = []
        for p in patches:
            q = dict(p)
            if q in numeric and g > 0:
                span = abs(q["value"]) or 1.0
                q["value"] = round(q["value"] + rng.uniform(-span,
                                                              span), 3)
            trial.append(q)
        gen_mod = evo / ("mod%d" % g)
        gen_mod.mkdir()
        (gen_mod / "mod.json").write_text(
            json.dumps({"patches": trial, "assets": assets}),
            encoding="utf-8")
        for a in assets:
            blob = (Path(mod_dir) / a["src"]).read_bytes()
            (gen_mod / a["src"]).write_bytes(blob)
        apply_mod(gen_dir, gen_mod)
        score = _score(gen_dir, trial, fitness)
        history.append({"gen": g, "score": score,
                        "patches": trial})
    best = max(history, key=lambda h: h["score"])
    (evo / "best.json").write_text(json.dumps(best, indent=2),
                                     encoding="utf-8")
    return best["gen"], best["score"], history


def _score(gen_dir, trial, fitness):
    total = 0.0
    for p in trial:
        if not isinstance(p["value"], (int, float)):
            continue
        target = (fitness or {}).get("%s::%s" % (p["file"], p["key"]))
        if target is None:
            total += float(p["value"])
        else:
            total -= abs(float(p["value"]) - float(target))
    return round(total, 3)


def apply_best(game_dir):
    """Apply the evolved best generation onto the live game dir."""
    import tempfile
    game = Path(game_dir)
    best = json.loads((game / ".eveevo" / "best.json").read_text(
        encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="evebest") as tmp:
        mod = Path(tmp) / "best"
        mod.mkdir()
        (mod / "mod.json").write_text(
            json.dumps({"patches": best["patches"], "assets": []}),
            encoding="utf-8")
        return apply_mod(game, mod)


def revert_mod(game_dir):
    """Restore every file in the backup manifest. Returns restored count."""
    bak = Path(game_dir) / ".evebak"
    manifest = json.loads((bak / "manifest.json").read_text(
        encoding="utf-8"))
    n = 0
    for rel in manifest:
        src = bak / rel
        if src.is_file():
            (Path(game_dir) / rel).write_bytes(src.read_bytes())
            n += 1
    return n
