"""Native file/format conversion, morphing and mutation for Eve.

Stdlib only, no new dependencies: CSV<->JSON conversion by extension,
Markdown report morphing from Eve's own bundles, and seeded deterministic
byte mutation for harness self-testing (mutate an input, re-verify the
output -- the harness evolving from each error as it happens).
"""
import csv
import io
import json
import random
from pathlib import Path

from .model_registry import note_failure


def convert_text(src_path, dst_path):
    """Convert between .csv and .json by extension. Returns row count."""
    src, dst = Path(src_path), Path(dst_path)
    text = src.read_text(encoding="utf-8")
    if src.suffix == ".csv" and dst.suffix == ".json":
        rows = list(csv.DictReader(io.StringIO(text)))
        dst.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return len(rows)
    if src.suffix == ".json" and dst.suffix == ".csv":
        rows = json.loads(text)
        if not rows:
            raise ValueError("empty JSON array converts to nothing")
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
        dst.write_text(buf.getvalue(), encoding="utf-8")
        return len(rows)
    raise ValueError("morph converts .csv<->.json only: %s -> %s"
                     % (src.suffix, dst.suffix))


def morph_markdown(state, title="Eve report"):
    """Morph an Eve state/bundle dict into a Markdown report."""
    lines = ["# %s" % title, ""]
    if state.get("judgment"):
        lines += ["**Judgment:** %s" % state["judgment"], ""]
    rows = state.get("rows") or state.get("checks") or []
    if rows:
        keys = list(rows[0].keys())
        lines.append("| " + " | ".join(keys) + " |")
        lines.append("| " + " | ".join("---" for _ in keys) + " |")
        for r in rows:
            lines.append("| " + " | ".join(str(r.get(k, ""))
                                           for k in keys) + " |")
        lines.append("")
    manifest = state.get("evidence_manifest") or []
    for m in manifest:
        lines.append("- evidence `%s` sha256 `%s`" % (
            m.get("path"), (m.get("sha256") or "")[:16]))
    if state.get("opinion"):
        lines += ["", "> %s" % state["opinion"]]
    return "\n".join(lines) + "\n"


def mutate_file(src_path, out_dir, seed=7, count=3):
    """Seeded byte mutations of a file for harness self-testing.

    Deterministic per seed: variant i flips bytes at PRNG positions.
    Returns the manifest list of {variant, byte, was, now}.
    """
    data = bytearray(Path(src_path).read_bytes())
    if not data:
        raise ValueError("cannot mutate an empty file")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    manifest = []
    name = Path(src_path).name
    for i in range(count):
        variant = bytearray(data)
        pos = rng.randrange(len(variant))
        was = variant[pos]
        variant[pos] = (was + 1 + i) % 256
        vp = out / ("%s.mut%d" % (name, i))
        vp.write_bytes(bytes(variant))
        manifest.append({"variant": str(vp), "byte": pos,
                         "was": was, "now": variant[pos]})
    (out / "mutations.json").write_text(json.dumps(manifest, indent=2),
                                        encoding="utf-8")
    return manifest
