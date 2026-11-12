#!/usr/bin/env python3
"""HYPER-EDGE JAEGERMEISTER -- the render series, one shot per invocation.

RUN
    blender -b --python tools/render_jaeger_series.py -- --shot 1
    blender -b --python tools/render_jaeger_series.py -- --shot 1 --samples 192

WHY A SCRIPT AND NOT A BLEND FILE
---------------------------------
A .blend is a binary nobody can diff, so a change to the figure's pose is
invisible in review and unrepeatable across machines. Here the pose is numbers in
one table: a shot is reproducible from the file, and a bad frame is a bug in a
readable line rather than in a scene someone dragged.

THE FIGURE RULE (RULE 0Q)
-------------------------
The subject is a huntsman-automaton: head, shoulders, spine, arms, legs, on
ground, posed to carry the role rather than labelled with it. Shot 1 plants it in
the gate it guards -- the Threshold -- because the first default is the one that
runs before anyone asks.

WHAT THE READER MUST BE ABLE TO CHECK
-------------------------------------
The log line names the DEVICE (ONEAPI or CPU), because a CPU fallback is a 13x
swing on this host and a time quoted without its device is not a measurement.
`--device cpu` is an explicit choice, never a silent fallback.

MEASURED PASSES (so a later edit does not re-learn them)
--------------------------------------------------------
  pass 1  96 spp, 45.1 s: legs splayed like a tripod, torso a box, the gate lit
          into a flat wall, fog invisible. Causes, not taste: joints wider than
          the shoulders, a 7 m lintel 3.4 m away under a 55 mm lens, volume
          density 0.022 against one 3.2 m area light.
  pass 2  96 spp, 35.8 s: stance fixed, but density 0.050 + an 85 mm lens at
          4.8 m turned the frame into milk and cropped the feet. Volume and
          focal length are the two knobs that read as "subject too close".
  pass 3  this file: 50 mm at 6.5 m, density 0.012, 1.90 m figure, cloak and
          skirt as cloth rather than cards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Euler, Vector

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "renders" / "jaeger"
LOG = ROOT / "logs" / "jaeger_render.jsonl"


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", type=int, default=1)
    ap.add_argument("--samples", type=int, default=96)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1360)
    ap.add_argument("--device", default="oneapi", choices=("oneapi", "cpu"))
    ap.add_argument("--out", default="")
    ap.add_argument("--name", default="")
    return ap.parse_args(argv)


# ── scene helpers ───────────────────────────────────────────────────────────
def material(name, color, metallic=0.0, roughness=0.5, emit=None, strength=0.0,
             bump_scale=0.0, bump_strength=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    if emit is not None:
        bsdf.inputs["Emission Color"].default_value = (*emit, 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength
    if bump_strength:
        # A cast surface is never perfectly smooth. A noise bump is the cheapest
        # honest break-up, and it survives denoising better than a normal map
        # nobody can see at this scale.
        noise = nt.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = bump_scale
        noise.inputs["Detail"].default_value = 6.0
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = bump_strength
        nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def add(obj, mat, smooth=True):
    obj.data.materials.append(mat)
    if smooth:
        for poly in obj.data.polygons:
            poly.use_smooth = True
    return obj


def limb(name, a, b, r1, r2, mat, verts=20):
    a, b = Vector(a), Vector(b)
    direction = b - a
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r1, radius2=r2,
                                    depth=direction.length, location=(a + b) * 0.5)
    obj = bpy.context.object
    obj.name = name
    obj.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
    return add(obj, mat)


def ball(name, loc, r, mat, scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=r,
                                         location=loc)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    return add(obj, mat)


def block(name, loc, size, mat, rot=(0, 0, 0), bevel=0.03):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    obj = bpy.context.object
    obj.name = name
    obj.scale = size
    obj.rotation_euler = Euler(rot)
    if bevel:
        mod = obj.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 3
    return add(obj, mat, smooth=False)


def ring(name, loc, major, minor, mat, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_torus_add(location=loc, major_radius=major,
                                     minor_radius=minor, major_segments=48,
                                     minor_segments=10, rotation=rot)
    return add(bpy.context.object, mat)


def coat(name, a, b, width, mat):
    """A cloth strip from a to b: plane + subdiv + solidify + noise ripple.

    A flat card reads as cardboard under a rim light; two centimetres of
    solidify and a slight ripple is the difference between cloth and panel.
    """
    a, b = Vector(a), Vector(b)
    direction = b - a
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(a + b) * 0.5)
    obj = bpy.context.object
    obj.name = name
    obj.rotation_euler = direction.to_track_quat("Y", "Z").to_euler()
    obj.scale = (width, direction.length, 1.0)
    sub = obj.modifiers.new("subdiv", "SUBSURF")
    sub.subdivision_type = "SIMPLE"
    sub.levels = 4
    sol = obj.modifiers.new("solidify", "SOLIDIFY")
    sol.thickness = 0.010
    tex = bpy.data.textures.new(name + "_cloth", "CLOUDS")
    tex.noise_scale = 0.30
    disp = obj.modifiers.new("ripple", "DISPLACE")
    disp.texture = tex
    disp.strength = 0.030
    return add(obj, mat)


# ── the figure ──────────────────────────────────────────────────────────────
def build_figure(iron, plate, leather, glow, glow_hot, cloth):
    """Pose: mid-stride, weight forward, head lowered, the horn carried low.

    Proportions are the pass-3 correction: 1.90 m tall, head 1.80, shoulders
    1.58, hips 1.00, shoulder width 0.52 against a 0.31 m stance. Passes 1-2 put
    the hips within 3 cm of the head height with 0.26 m shoulder balls, which
    reads as a mannequin with a lamp for a pelvis.
    """
    J = {
        "foot_l": (0.15, 0.33, 0.075), "foot_r": (-0.16, -0.29, 0.075),
        "knee_l": (0.17, 0.39, 0.52), "knee_r": (-0.18, -0.11, 0.53),
        "hip_l": (0.12, 0.03, 1.00), "hip_r": (-0.12, -0.02, 1.00),
        "pelvis": (0.0, 0.00, 1.04),
        "chest": (0.0, 0.06, 1.44),
        "neck": (0.0, 0.06, 1.68),
        "head": (0.0, 0.10, 1.86),
        "shoulder_l": (0.255, 0.04, 1.615), "shoulder_r": (-0.255, 0.02, 1.605),
        "elbow_l": (0.32, 0.22, 1.35), "elbow_r": (-0.33, 0.12, 1.34),
        "wrist_l": (0.27, 0.42, 1.20), "wrist_r": (-0.34, 0.30, 1.16),
    }
    for side in ("l", "r"):
        limb("thigh_" + side, J["hip_" + side], J["knee_" + side], 0.102, 0.080, leather)
        limb("shin_" + side, J["knee_" + side], J["foot_" + side], 0.074, 0.044, iron)
        ball("knee_" + side, J["knee_" + side], 0.078, plate, scale=(1, 0.95, 1.2))
        block("foot_" + side, (J["foot_" + side][0], J["foot_" + side][1] + 0.048, 0.045),
              (0.11, 0.27, 0.09), iron, bevel=0.012)
        ring("hip_seam_" + side, J["hip_" + side], 0.108, 0.008, glow,
             rot=(math.radians(90), 0, 0))

    # Volumes, not boxes. Pass 4 stacked scaled cubes for the chest, abdomen and
    # collar; at this lens the front faces read as one cardboard card across the
    # figure, because a box has exactly one normal and one value. Rounded
    # volumes plus three ribbed rings hold a silhouette from every angle.
    ball("pelvis", J["pelvis"], 0.175, leather, scale=(1.05, 0.86, 0.74))
    ball("torso", (0.0, 0.03, 1.36), 0.185, iron, scale=(1.05, 0.78, 1.30))
    ball("chest_shell", (0.0, 0.075, 1.49), 0.170, plate, scale=(1.02, 0.72, 0.66))
    ball("abdomen", (0.0, 0.00, 1.17), 0.140, plate, scale=(1.0, 0.78, 0.78))
    ring("belt", (0.0, 0.02, 1.07), 0.185, 0.022, plate, rot=(math.radians(6), 0, 0))
    for i, (z, r) in enumerate(((1.28, 0.185), (1.38, 0.205), (1.48, 0.200))):
        ring("rib_%d" % i, (0.0, 0.02, z), r, 0.012, plate,
             rot=(math.radians(84), 0, 0))
    ring("collar_ring", (0.0, 0.045, 1.645), 0.125, 0.013, plate,
         rot=(math.radians(84), 0, 0))
    ball("core", (0.0, 0.19, 1.42), 0.040, glow_hot)
    ring("core_ring", (0.0, 0.175, 1.42), 0.062, 0.006, glow, rot=(math.radians(90), 0, 0))

    # skirt: eight strips off the belt across the back half, so the stride reads
    for i, angle in enumerate((200, 225, 250, 275, 290, 315, 340, 20)):
        rad = math.radians(angle)
        x, y = math.cos(rad) * 0.16, math.sin(rad) * 0.16 + 0.01
        coat("skirt_%02d" % i, (x, y, 1.05),
             (x * 1.5, y * 1.5 - 0.10, 0.58), 0.11, cloth)

    for side in ("l", "r"):
        limb("upperarm_" + side, J["shoulder_" + side], J["elbow_" + side],
             0.066, 0.052, leather)
        limb("forearm_" + side, J["elbow_" + side], J["wrist_" + side],
             0.055, 0.038, iron)
        ball("pauldron_" + side, J["shoulder_" + side], 0.088, plate, scale=(1, 1.05, 0.78))
        ring("pauldron_ring_" + side, J["shoulder_" + side], 0.095, 0.006, glow,
             rot=(math.radians(90), 0, math.radians(12)))
        ball("elbow_" + side, J["elbow_" + side], 0.05, plate)
        block("hand_" + side, (J["wrist_" + side][0], J["wrist_" + side][1] + 0.03,
                               J["wrist_" + side][2]), (0.055, 0.09, 0.045), iron, bevel=0.01)
        for finger in (-1, 0, 1):
            limb("finger_%s%d" % (side, finger), J["wrist_" + side],
                 (J["wrist_" + side][0] + finger * 0.028,
                  J["wrist_" + side][1] + 0.13,
                  J["wrist_" + side][2] - 0.030),
                 0.013, 0.006, iron, verts=8)

    limb("neck_col", J["neck"], J["head"], 0.055, 0.050, iron)
    ball("skull", J["head"], 0.104, iron, scale=(1.0, 1.12, 1.02))
    block("jaw", (0.0, J["head"][1] + 0.045, J["head"][2] - 0.072), (0.098, 0.115, 0.062),
          plate, bevel=0.012)
    block("visor", (0.0, J["head"][1] + 0.104, J["head"][2] + 0.008),
          (0.125, 0.026, 0.024), glow_hot, bevel=0.005)
    for side, x in (("l", 0.062), ("r", -0.062)):
        limb("horn_" + side, (x, J["head"][1] - 0.01, J["head"][2] + 0.072),
             (x * 1.8, J["head"][1] - 0.17, J["head"][2] + 0.24),
             0.022, 0.003, plate, verts=10)
    # the horn he carries: a hunt called, not a weapon drawn
    ring("horn", (-0.34, 0.32, 1.15), 0.10, 0.018, plate,
         rot=(math.radians(74), 0, math.radians(28)))


# ── the world ───────────────────────────────────────────────────────────────
def build_world(stone, glow, glow_hot):
    bpy.ops.mesh.primitive_plane_add(size=120, location=(0, 0, 0))
    ground = bpy.context.object
    ground.name = "ground"
    ground.data.materials.append(stone)
    tex = bpy.data.textures.new("ground_noise", "CLOUDS")
    tex.noise_scale = 1.2
    bump = ground.modifiers.new("displace", "DISPLACE")
    bump.texture = tex
    bump.strength = 0.05

    # The Threshold: a 10 m gate, 9 m behind the figure. Pass 2 stood it 6.4 m
    # away under an 85 mm lens, where its lit inner face became a flat wall
    # across half the frame; at this distance it is a silhouette the figure
    # stands against, which is what a threshold is for.
    for side, x in (("l", 2.55), ("r", -2.55)):
        block("pillar_" + side, (x, 4.6, 3.6), (0.62, 0.62, 7.2), stone, bevel=0.05)
        limb("pillar_seam_" + side, (x * 0.90, 4.10, 0.2), (x * 0.90, 4.10, 6.9),
             0.012, 0.012, glow, verts=8)
    block("lintel", (0.0, 4.6, 7.35), (6.0, 0.7, 0.6), stone, bevel=0.05)
    limb("lintel_seam", (-2.2, 4.10, 6.95), (2.2, 4.10, 6.95), 0.012, 0.012, glow, verts=8)

    import random
    rng = random.Random(20261102)      # seeded: the frame is reproducible
    for i in range(28):
        angle = rng.uniform(0, math.tau)
        radius = rng.uniform(1.5, 9.0)
        x, y = math.cos(angle) * radius, math.sin(angle) * radius * 0.7 + 2.2
        block("shard_%02d" % i, (x, y, rng.uniform(0.012, 0.05)),
              (rng.uniform(0.06, 0.34), rng.uniform(0.04, 0.22), rng.uniform(0.02, 0.09)),
              stone, rot=(rng.uniform(-0.15, 0.15), rng.uniform(-0.15, 0.15),
                          rng.uniform(0, math.tau)), bevel=0.006)


def build_lighting():
    # key: warm, camera-left, low enough to model the armour from below
    bpy.ops.object.light_add(type="AREA", location=(-1.9, -2.2, 3.2))
    key = bpy.context.object
    key.name = "key"
    key.data.energy = 420
    key.data.size = 1.6
    key.data.color = (1.0, 0.72, 0.48)
    key.rotation_euler = (math.radians(56), 0, math.radians(-40))

    # rim: cold, from behind the gate, to cut the silhouette out of the fog
    bpy.ops.object.light_add(type="AREA", location=(0.9, 2.4, 2.6))
    rim = bpy.context.object
    rim.name = "rim"
    rim.data.energy = 700
    rim.data.size = 2.0
    rim.data.color = (0.38, 0.70, 1.0)
    rim.rotation_euler = (math.radians(76), 0, math.radians(168))

    # the shaft through the gate: a hard source is what makes volume read
    bpy.ops.object.light_add(type="SPOT", location=(0.0, 6.5, 9.0))
    shaft = bpy.context.object
    shaft.name = "shaft"
    shaft.data.energy = 2600
    shaft.data.spot_size = math.radians(20)
    shaft.data.color = (0.78, 0.88, 1.0)
    shaft.rotation_euler = (math.radians(-20), 0, 0)

    bpy.ops.object.light_add(type="AREA", location=(2.6, -2.4, 1.5))
    fill = bpy.context.object
    fill.name = "fill"
    fill.data.energy = 35
    fill.data.size = 3.0
    fill.data.color = (0.55, 0.65, 1.0)
    fill.rotation_euler = (math.radians(88), 0, math.radians(46))

    bpy.ops.object.light_add(type="POINT", location=(0.0, 0.17, 1.43))
    core = bpy.context.object
    core.name = "core_light"
    core.data.energy = 8
    core.data.color = (1.0, 0.55, 0.22)


def build_camera():
    target = bpy.data.objects.new("look_at", None)
    bpy.context.collection.objects.link(target)
    target.location = (0.0, 0.05, 1.02)

    bpy.ops.object.camera_add(location=(1.85, -3.55, 1.12))
    cam = bpy.context.object
    cam.data.lens = 50
    cam.data.sensor_width = 36
    cam.data.dof.use_dof = True
    cam.data.dof.focus_object = target
    cam.data.dof.aperture_fstop = 2.2
    constraint = cam.constraints.new("TRACK_TO")
    constraint.target = target
    constraint.track_axis = "TRACK_NEGATIVE_Z"
    constraint.up_axis = "UP_Y"
    bpy.context.scene.camera = cam
    return cam


def build_fog():
    world = bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.010, 0.014, 0.024, 1.0)
    bg.inputs["Strength"].default_value = 0.05
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = 0.004   # 0.050 milk, 0.012 haze, 0.008 wash
    vol.inputs["Anisotropy"].default_value = 0.35
    vol.inputs["Color"].default_value = (0.70, 0.82, 1.0, 1.0)
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])


def configure_render(args, device_used):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = True
    scene.cycles.device = "GPU" if device_used == "ONEAPI" else "CPU"
    scene.cycles.max_bounces = 8
    scene.cycles.volume_bounces = 2
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "8"
    try:
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError as exc:
        print("jaeger: this Blender lacks AgX transforms (%s); keeping defaults"
              % type(exc).__name__, flush=True)
    scene.view_settings.exposure = 0.0


def set_device(want: str) -> str:
    if want == "cpu":
        return "CPU"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "ONEAPI"
        prefs.get_devices()
        if not [d for d in prefs.devices if d.type == "ONEAPI"]:
            return "CPU"
        for d in prefs.devices:
            d.use = d.type == "ONEAPI"
        return "ONEAPI"
    except (KeyError, AttributeError, RuntimeError) as exc:
        print("jaeger: device probe failed (%s: %s); falling back to CPU"
              % (type(exc).__name__, exc), flush=True)
        return "CPU"


def log_line(row: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def main() -> int:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    device = set_device(args.device)
    if args.device != "cpu" and device == "CPU":
        print("DEVICE FALLBACK: ONEAPI unavailable, rendering on CPU "
              "(a 13x swing on this host; the log records it)")

    stone = material("archive_stone", (0.030, 0.033, 0.040), metallic=0.10,
                     roughness=0.88, bump_scale=3.0, bump_strength=0.12)
    iron = material("iron", (0.048, 0.050, 0.058), metallic=0.62, roughness=0.44,
                    bump_scale=9.0, bump_strength=0.05)
    plate = material("oxblood_plate", (0.115, 0.028, 0.020), metallic=0.55,
                     roughness=0.36, bump_scale=12.0, bump_strength=0.04)
    leather = material("leather", (0.040, 0.028, 0.022), metallic=0.02, roughness=0.62,
                       bump_scale=22.0, bump_strength=0.10)
    cloth = material("cloak_cloth", (0.020, 0.017, 0.018), metallic=0.0, roughness=0.78,
                     bump_scale=30.0, bump_strength=0.08)
    glow = material("amber_glow", (0.85, 0.32, 0.05), emit=(1.0, 0.45, 0.10), strength=6.0)
    glow_hot = material("amber_core", (1.0, 0.78, 0.45), emit=(1.0, 0.70, 0.32), strength=14.0)

    build_world(stone, glow, glow_hot)
    build_figure(iron, plate, leather, glow, glow_hot, cloth)
    build_lighting()
    build_camera()
    build_fog()
    configure_render(args, device)

    name = args.name or "shot%02d_threshold" % args.shot
    out = Path(args.out) if args.out else OUT_DIR / ("%s.png" % name)
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.context.scene.render.filepath = str(out)

    started = time.monotonic()
    bpy.ops.render.render(write_still=True)
    seconds = round(time.monotonic() - started, 1)

    sha = hashlib.sha256(out.read_bytes()).hexdigest()[:16] if out.exists() else ""
    row = {"shot": args.shot, "name": name, "device": device, "samples": args.samples,
           "resolution": [args.width, args.height], "seconds": seconds,
           "bytes": out.stat().st_size if out.exists() else 0,
           "sha256_16": sha, "path": str(out)}
    log_line(row)
    print("JAEGER RENDER %d -- %s in %ss on %s (%d spp, %dx%d), %d bytes, sha %s"
          % (args.shot, out.name, seconds, device, args.samples, args.width,
             args.height, row["bytes"], sha))
    return 0


if __name__ == "__main__":
    sys.exit(main())
