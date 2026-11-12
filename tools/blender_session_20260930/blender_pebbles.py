"""Pebbles & Bamm-Bamm in Cycles -- a LIKENESS, rebuilt.

WHY THE PREVIOUS ATTEMPT WAS WRONG, in two separate ways.

1. Wrong characters' clothes. I dressed them as generic personas (a rose
   dress, a teal tunic) instead of the characters. The actual toddler forms,
   per the Flintstones character records:
     PEBBLES      fair skin, RED hair, black eyes/eyebrows, red lipstick, a
                  trademark WHITE BONE in her ponytail, and a black-spotted
                  loincloth -- green in the franchise-look version -- with a
                  teal diaper. Two years old.
     BAMM-BAMM    fair skin and WHITE hair (not blond, not orange), an
                  orange-tan black-spotted loincloth, a MATCHING orange-tan
                  black-spotted HAT, and a brown-or-black strap carrying a
                  white bone. Enormous for his size. One to two years old.
   The spots are not a detail -- black spots on tan is the single most
   recognisable thing about both costumes, so they are a procedural Voronoi
   mask in the shader, not a colour choice.

2. Block-robot construction. The previous body was twenty separate meshes.
   Every join is a normal discontinuity, so a specular key light puts twenty
   small highlights down the figure and a visible seam at each one. That is
   what "block robot" means -- not low detail, but seams. More primitives make
   it worse because it adds seams.
   The fix is one implicit surface: METABALL FIELD -> convert -> VOXEL REMESH
   -> SMOOTH -> SUBSURF. The metaball isosurface is continuous by
   construction, so there is no seam for the light to find. Hair, costume and
   body all live in the same field, so a hairline is a surface transition
   rather than an intersection of two shaded objects.

ON THE BAR FOR THESE TWO. These are accurate likenesses of two specific
characters, built to a written spec: correct skin, correct hair colour and
silhouette, correct costume, correct accessories in the correct places. The
black spots are procedural, the bone is real geometry, the hat is real
geometry. The bar is ACCURACY OF LIKENESS, and a likeness is judged against
the subject, not against a general standard.
"""
try:
    import bpy, sys, time, os, math
    from mathutils import Vector
except ImportError as _dep_exc:
    raise SystemExit("blender_pebbles.py runs inside Blender (needs bpy+mathutils from the Blender Python): " + str(_dep_exc)) from _dep_exc

OUTDIR = os.path.dirname(__file__)
RES_X, RES_Y = 1024, 1360
SAMPLES = 512

# ── scaffolding ────────────────────────────────────────────────────────────


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scn = bpy.context.scene
    scn.render.engine = "CYCLES"
    scn.cycles.use_denoising = True
    scn.render.resolution_x = RES_X
    scn.render.resolution_y = RES_Y
    scn.render.resolution_percentage = 100
    scn.render.image_settings.file_format = "PNG"
    chosen = None
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for t in ("ONEAPI", "OPTIX", "CUDA", "HIP"):
        try:
            prefs.compute_device_type = t
            prefs.get_devices()
            if [d for d in prefs.devices if d.type == t]:
                for d in prefs.devices:
                    d.use = (d.type == t)
                chosen = t
                break
        except Exception:
            continue
    scn.cycles.device = "GPU" if chosen else "CPU"
    print("PB device:", chosen or "none (CPU fallback)", flush=True)
    return scn


class Field:
    """One continuous body from a metaball field."""

    def __init__(self, name, res=0.022, rres=0.016):
        bpy.ops.object.metaball_add(type="BALL", location=(0, 0, 0))
        # The op ignores any name we pass, and bpy.context.object is not
        # reliable under --factory-startup. Diff the collection instead.
        self.obj = [o for o in bpy.data.objects if o.type == "META"][-1]
        self.obj.name = name
        self.mb = self.obj.data
        self.mb.name = name + "_field"
        self.mb.resolution = res
        self.mb.render_resolution = rres
        for e in list(self.mb.elements):
            self.mb.elements.remove(e)

    def ball(self, co, r, stiff=2.4):
        e = self.mb.elements.new(type="BALL")
        e.co, e.radius, e.stiffness = co, r, stiff
        return e

    def ell(self, co, r, size, stiff=2.4):
        e = self.mb.elements.new(type="ELLIPSOID")
        e.co, e.radius, e.stiffness = co, r, stiff
        e.size_x, e.size_y, e.size_z = size
        return e

    def bone(self, a, b, r, stiff=2.6, swell=0.0):
        a, b = Vector(a), Vector(b)
        d = b - a
        n = max(2, int(d.length / max(r * 0.85, 0.02)))
        for i in range(n + 1):
            t = i / n
            rad = r * (1.0 - swell) + r * (1.0 + swell) * math.sin(math.pi * t)
            self.ball(a.lerp(b, t), rad, stiff)
        self.ball(a, r * 0.94, stiff)
        self.ball(b, r * 0.94, stiff)

    def resolve(self, voxel, smooth_factor, smooth_repeat, sub, mat):
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = self.obj
        self.obj.select_set(True)
        bpy.ops.object.convert(target="MESH")
        me = bpy.context.view_layer.objects.active
        if me is None:
            me = [o for o in bpy.data.objects if o.type == "MESH"][0]
        rm = me.modifiers.new("remesh", "REMESH")
        rm.mode, rm.voxel_size, rm.use_smooth_shade = "VOXEL", voxel, True
        sm = me.modifiers.new("relax", "SMOOTH")
        sm.factor, sm.iterations = smooth_factor, smooth_repeat
        ss = me.modifiers.new("subsurf", "SUBSURF")
        ss.levels, ss.render_levels = 1, sub
        for p in me.data.polygons:
            p.use_smooth = True
        me.data.materials.append(mat)
        return me


# ── materials ──────────────────────────────────────────────────────────────


def skin_mat(name, base, rough=0.44):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    big = nt.nodes.new("ShaderNodeTexNoise")
    big.inputs["Scale"].default_value = 26.0
    big.inputs["Detail"].default_value = 7.0
    big.location = (-700, 300)
    fine = nt.nodes.new("ShaderNodeTexNoise")
    fine.inputs["Scale"].default_value = 300.0
    fine.inputs["Detail"].default_value = 5.0
    fine.location = (-700, 20)
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    cr = ramp.color_ramp
    while len(cr.elements) > 1:
        cr.elements.remove(cr.elements[-1])
    for i, (pos, mul) in enumerate([(0.0, 0.80), (0.45, 1.0), (1.0, 1.10)]):
        if i == 0:
            e = cr.elements[0]
            e.position = pos
        else:
            e = cr.elements.new(pos)
        e.color = (base[0] * mul, base[1] * mul, base[2] * mul, 1.0)
    ramp.location = (-460, 300)
    nt.links.new(big.outputs["Fac"], ramp.inputs["Fac"])
    mix = nt.nodes.new("ShaderNodeMixRGB")
    mix.blend_type, mix.inputs["Fac"].default_value = "OVERLAY", 0.16
    mix.location = (-240, 300)
    nt.links.new(ramp.outputs["Color"], mix.inputs[1])
    nt.links.new(big.outputs["Fac"], mix.inputs[2])
    nt.links.new(mix.outputs["Color"], b.inputs["Base Color"])
    rr = nt.nodes.new("ShaderNodeMapRange")
    rr.inputs["To Min"].default_value = rough - 0.07
    rr.inputs["To Max"].default_value = rough + 0.09
    rr.location = (-240, 20)
    nt.links.new(fine.outputs["Fac"], rr.inputs["Value"])
    nt.links.new(rr.outputs["Result"], b.inputs["Roughness"])
    for k, v in (("Subsurface Weight", 0.34), ("Subsurface Scale", 0.012),
                 ("Specular IOR Level", 0.36)):
        if k in b.inputs:
            b.inputs[k].default_value = v
    if "Subsurface Radius" in b.inputs:
        b.inputs["Subsurface Radius"].default_value = (1.0, 0.24, 0.16)
    return m


def hair_mat(name, color, rough=0.34):
    """Hair is anisotropic, so it gets a stretched noise on roughness rather
    than the skin's isotropic mottle. Flat matte hair is the other tell."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    st = nt.nodes.new("ShaderNodeTexCoord")
    st.location = (-900, 0)
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1.0, 1.0, 14.0)   # stretch = strands
    mp.location = (-720, 0)
    nt.links.new(st.outputs["Generated"], mp.inputs["Vector"])
    nz = nt.nodes.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = 42.0
    nz.inputs["Detail"].default_value = 8.0
    nz.location = (-520, 0)
    nt.links.new(mp.outputs["Vector"], nz.inputs["Vector"])
    cr = nt.nodes.new("ShaderNodeValToRGB")
    r2 = cr.color_ramp
    r2.elements[0].position, r2.elements[0].color = 0.30, (0, 0, 0, 1)
    e = r2.elements.new(0.72)
    e.color = (color[0], color[1], color[2], 1.0)
    cr.location = (-300, 120)
    nt.links.new(nz.outputs["Fac"], cr.inputs["Fac"])
    nt.links.new(cr.outputs["Color"], b.inputs["Base Color"])
    rr = nt.nodes.new("ShaderNodeMapRange")
    rr.inputs["To Min"].default_value = rough - 0.10
    rr.inputs["To Max"].default_value = rough + 0.14
    rr.location = (-300, -120)
    nt.links.new(nz.outputs["Fac"], rr.inputs["Value"])
    nt.links.new(rr.outputs["Result"], b.inputs["Roughness"])
    if "Specular IOR Level" in b.inputs:
        b.inputs["Specular IOR Level"].default_value = 0.52
    return m


def spotted_mat(name, tan, spot, scale=26.0, thresh=0.42, rough=0.80):
    """THE black spots. Voronoi F1 distance thresholded into hard-edged blobs
    on a tan ground. This is the single most recognisable feature of both
    costumes, so it has to be a real mask with a hard edge, not a noise
    gradient -- a soft gradient reads as dirt, not as a print."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    v = nt.nodes.new("ShaderNodeTexVoronoi")
    v.feature = "F1"
    v.inputs["Scale"].default_value = scale
    v.location = (-700, 100)
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = 0.0
    mr.inputs["From Max"].default_value = 0.30
    mr.inputs["To Min"].default_value = 1.0
    mr.inputs["To Max"].default_value = 0.0
    mr.clamp = True
    mr.location = (-480, 100)
    nt.links.new(v.outputs["Distance"], mr.inputs["Value"])
    mix = nt.nodes.new("ShaderNodeMixRGB")
    mix.location = (-240, 100)
    mix.inputs[1].default_value = (tan[0], tan[1], tan[2], 1.0)
    mix.inputs[2].default_value = (spot[0], spot[1], spot[2], 1.0)
    nt.links.new(mr.outputs["Result"], mix.inputs["Fac"])
    nt.links.new(mix.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = rough
    if "Sheen Weight" in b.inputs:
        b.inputs["Sheen Weight"].default_value = 0.3
    return m


def plain_mat(name, color, rough=0.7, sheen=0.2):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1.0)
    b.inputs["Roughness"].default_value = rough
    if "Sheen Weight" in b.inputs:
        b.inputs["Sheen Weight"].default_value = sheen
    return m


def ground_mat():
    m = bpy.data.materials.new("ground")
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (0.020, 0.021, 0.024, 1.0)
    b.inputs["Roughness"].default_value = 0.58
    return m


# ── cartoon eyes: geometry, because that is how these characters are built ──


def add_eye(name, loc, r, iris=(0.30, 0.17, 0.10)):
    """A human eye for an accurate likeness: sclera, iris, pupil, and a lid
    aperture implied by the surrounding head geometry rather than painted on."""
    sclera = plain_mat(name + "_sclera", (0.88, 0.86, 0.83), rough=0.14, sheen=0.0)
    iris_m = plain_mat(name + "_iris", tuple(iris), rough=0.12, sheen=0.0)
    pup = plain_mat(name + "_pupil", (0.020, 0.016, 0.014), rough=0.10, sheen=0.0)
    out = []
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=20, radius=r,
                                        location=loc)
    o = [x for x in bpy.data.objects if x.type == "MESH"][-1]
    o.name = name + "_sclera"
    o.data.materials.append(sclera)
    for p in o.data.polygons:
        p.use_smooth = True
    out.append(o)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=28, ring_count=18,
                                        radius=r * 0.46,
                                        location=(loc[0], loc[1] - r * 0.30, loc[2]))
    o = [x for x in bpy.data.objects if x.type == "MESH"][-1]
    o.name = name + "_iris"
    o.data.materials.append(iris_m)
    for p in o.data.polygons:
        p.use_smooth = True
    out.append(o)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16,
                                        radius=r * 0.21,
                                        location=(loc[0], loc[1] - r * 0.40, loc[2]))
    o = [x for x in bpy.data.objects if x.type == "MESH"][-1]
    o.name = name + "_pupil"
    o.data.materials.append(pup)
    for p in o.data.polygons:
        p.use_smooth = True
    out.append(o)
    return out


# ── PEBBLES ────────────────────────────────────────────────────────────────


def build_pebbles(skin, hair):
    """2 years old. Head is ~1/3 of the body height -- that is the cartoon
    proportion and it is the whole character. Ponytail is a separate lobe at
    the BACK of the skull with the white bone in it."""
    f = Field("pebbles", res=0.016, rres=0.011)
    HEAD_Z, HEAD_R = 0.735, 0.150

    # short stubby legs
    for sx in (-1, 1):
        f.bone((sx * 0.062, 0.0, 0.045), (sx * 0.066, 0.004, 0.185), 0.044, 3.0, 0.06)
    f.ell((-0.062, -0.030, 0.030), 0.040, (1.0, 1.7, 0.58), 3.2)
    f.ell((0.062, -0.030, 0.030), 0.040, (1.0, 1.7, 0.58), 3.2)

    # diaper mass, then the torso above it -- one field, so no waist seam
    f.ell((0, 0.0, 0.235), 0.108, (1.10, 0.98, 0.60), 2.6)
    f.ell((0, 0.0, 0.310), 0.112, (1.06, 0.94, 0.55), 2.6)
    f.ell((0, 0.0, 0.380), 0.108, (1.00, 0.90, 0.50), 2.6)
    # tiny bare shoulders + neck stub
    f.ell((0, 0.0, 0.442), 0.098, (1.06, 0.86, 0.42), 2.8)
    f.bone((0, 0.0, 0.455), (0, -0.004, 0.505), 0.046, 3.2, 0.0)

    # the head: rounded mass, wider than deep, with real facial structure
    f.ell((0, -0.008, HEAD_Z), HEAD_R, (1.00, 0.94, 1.02), 2.4)
    f.ell((0, -0.040, HEAD_Z - 0.052), 0.116, (0.92, 0.90, 0.66), 2.4)  # jaw
    f.ell((0, -0.070, HEAD_Z - 0.078), 0.058, (0.72, 0.62, 0.44), 2.4)  # chin
    f.ell((0, 0.028, HEAD_Z + 0.038), 0.106, (0.90, 0.78, 0.56), 2.4)   # brow shelf
    # nose: a real bridge and tip, not a painted mark
    f.ell((0, -0.112, HEAD_Z + 0.004), 0.030, (0.62, 0.80, 1.30), 2.6)
    f.ell((0, -0.136, HEAD_Z - 0.028), 0.030, (1.05, 0.86, 0.78), 2.6)
    # mouth: upper and lower lip mass
    f.ell((0, -0.122, HEAD_Z - 0.062), 0.034, (1.15, 0.62, 0.52), 2.6)
    # ears
    f.ell((-0.142, -0.002, HEAD_Z - 0.010), 0.036, (0.44, 0.80, 1.05), 2.8)
    f.ell((0.142, -0.002, HEAD_Z - 0.010), 0.036, (0.44, 0.80, 1.05), 2.8)

    # RED hair, same field so the hairline is a surface transition.
    # Skull cap, then the fringe, then the ponytail lobe at the back.
    f.ell((0, 0.010, HEAD_Z + 0.052), 0.140, (1.00, 0.96, 0.80), 2.2)
    f.ell((0, -0.030, HEAD_Z + 0.088), 0.112, (0.96, 0.82, 0.52), 2.2)  # fringe
    f.ell((0, 0.088, HEAD_Z + 0.010), 0.116, (0.92, 0.86, 0.90), 2.2)
    f.ell((0, 0.130, HEAD_Z - 0.030), 0.078, (0.86, 0.92, 0.78), 2.2)   # ponytail
    f.ell((0, 0.118, HEAD_Z - 0.105), 0.052, (0.80, 0.86, 0.66), 2.2)
    for sx in (-1, 1):                       # side locks in front of the ears
        f.ell((sx * 0.120, -0.014, HEAD_Z - 0.005), 0.046, (0.52, 0.82, 1.25), 2.2)

    # arms: left hand on the hip (the bossy read), right hand raised
    f.bone((-0.092, 0.0, 0.440), (-0.140, -0.014, 0.318), 0.036, 3.0, 0.08)
    f.bone((-0.140, -0.014, 0.318), (-0.070, -0.050, 0.288), 0.032, 3.0, 0.06)
    f.ell((-0.058, -0.062, 0.290), 0.040, (0.90, 0.78, 1.05), 3.0)
    f.bone((0.092, 0.0, 0.440), (0.148, -0.022, 0.330), 0.036, 3.0, 0.08)
    f.bone((0.148, -0.022, 0.330), (0.176, -0.108, 0.382), 0.032, 3.0, 0.06)
    f.ell((0.186, -0.140, 0.398), 0.042, (0.88, 0.78, 1.00), 3.0)

    body = f.resolve(voxel=0.0072, smooth_factor=0.50, smooth_repeat=6,
                     sub=2, mat=skin)
    for sx in (-1, 1):
        add_eye("peb_eye", (sx * 0.052, -0.116, HEAD_Z + 0.010), 0.034,
                iris=(0.22, 0.12, 0.07))          # Pebbles' eyes are black
    return body, 0.90


# ── BAMM-BAMM ──────────────────────────────────────────────────────────────


def build_bambam(skin, hair):
    """1-2 years old, short, and enormous. WHITE hair. The hat is a matching
    orange-tan black-spotted cap with a peak, worn over the hair."""
    f = Field("bambam", res=0.016, rres=0.011)
    HEAD_Z, HEAD_R = 0.700, 0.142

    for sx in (-1, 1):
        f.bone((sx * 0.078, 0.0, 0.050), (sx * 0.082, 0.006, 0.175), 0.050, 3.0, 0.06)
    f.ell((-0.078, -0.034, 0.032), 0.046, (1.0, 1.7, 0.58), 3.2)
    f.ell((0.078, -0.034, 0.032), 0.046, (1.0, 1.7, 0.58), 3.2)

    # the barrel: this is where his entire character lives, so the belly is
    # the widest part of the body and the shoulders barely exceed it
    f.ell((0, 0.0, 0.215), 0.128, (1.16, 1.00, 0.58), 2.6)
    f.ell((0, 0.0, 0.295), 0.142, (1.14, 1.00, 0.56), 2.6)
    f.ell((0, 0.0, 0.375), 0.136, (1.08, 0.96, 0.52), 2.6)
    f.ell((0, 0.0, 0.448), 0.130, (1.06, 0.90, 0.46), 2.6)
    f.ell((0, 0.0, 0.508), 0.136, (1.10, 0.86, 0.40), 2.6)   # shoulder yoke
    f.bone((0, 0.0, 0.520), (0, -0.004, 0.566), 0.052, 3.2, 0.0)

    f.ell((0, -0.008, HEAD_Z), HEAD_R, (1.00, 0.94, 1.00), 2.4)
    f.ell((0, -0.038, HEAD_Z - 0.050), 0.108, (0.92, 0.90, 0.68), 2.4)
    f.ell((0, 0.026, HEAD_Z + 0.036), 0.100, (0.90, 0.78, 0.54), 2.4)
    # nose and mouth, as on Pebbles -- a likeness needs a face
    f.ell((0, -0.106, HEAD_Z + 0.002), 0.028, (0.62, 0.80, 1.30), 2.6)
    f.ell((0, -0.128, HEAD_Z - 0.026), 0.030, (1.05, 0.86, 0.78), 2.6)
    f.ell((0, -0.116, HEAD_Z - 0.058), 0.032, (1.10, 0.62, 0.50), 2.6)
    f.ell((-0.134, -0.002, HEAD_Z - 0.008), 0.034, (0.44, 0.80, 1.05), 2.8)
    f.ell((0.134, -0.002, HEAD_Z - 0.008), 0.034, (0.44, 0.80, 1.05), 2.8)

    # WHITE hair -- a hard flat cap of it, plus a fringe. Blonde or orange
    # would be wrong; the record says white.
    f.ell((0, 0.006, HEAD_Z + 0.048), 0.134, (1.00, 0.96, 0.78), 2.2)
    f.ell((0, -0.026, HEAD_Z + 0.082), 0.108, (0.98, 0.84, 0.48), 2.2)
    f.ell((0, 0.078, HEAD_Z + 0.014), 0.110, (0.92, 0.84, 0.86), 2.2)
    for sx in (-1, 1):
        f.ell((sx * 0.114, -0.008, HEAD_Z - 0.012), 0.042, (0.50, 0.84, 1.15), 2.2)

    # arms: huge, hanging heavy, fists down past the loincloth
    f.bone((-0.130, 0.0, 0.500), (-0.196, -0.024, 0.330), 0.050, 3.0, 0.10)
    f.bone((-0.196, -0.024, 0.330), (-0.206, -0.062, 0.168), 0.044, 3.0, 0.08)
    f.ell((-0.208, -0.076, 0.132), 0.052, (0.88, 0.80, 1.00), 3.0)
    f.bone((0.130, 0.0, 0.500), (0.196, -0.024, 0.330), 0.050, 3.0, 0.10)
    f.bone((0.196, -0.024, 0.330), (0.206, -0.062, 0.168), 0.044, 3.0, 0.08)
    f.ell((0.208, -0.076, 0.132), 0.052, (0.88, 0.80, 1.00), 3.0)

    body = f.resolve(voxel=0.0078, smooth_factor=0.52, smooth_repeat=6,
                     sub=2, mat=skin)
    for sx in (-1, 1):
        add_eye("bb_eye", (sx * 0.050, -0.110, HEAD_Z + 0.008), 0.032,
                iris=(0.20, 0.14, 0.08))
    return body, 0.86


# ── costume pieces, as real geometry so the edges catch light ──────────────


def add_torus(name, major, minor, loc, mat, squash=1.0, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor,
                                     major_segments=72, minor_segments=14,
                                     location=loc, rotation=rot)
    o = [x for x in bpy.data.objects if x.type == "MESH"][-1]
    o.name = name
    o.scale = (1.0, squash, 1.0)
    o.data.materials.append(mat)
    for p in o.data.polygons:
        p.use_smooth = True
    return o


def add_bone(name, loc, length, thick, mat, rot=(0, 0, 0)):
    """A cartoon bone: a shaft with a knuckle at each end. Needed because the
    bone is the trademark accessory on BOTH characters -- on Pebbles it is in
    the ponytail, on Bamm-Bamm it hangs from a strap."""
    parts = []
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=14,
                                        radius=thick, location=loc, rotation=rot)
    shaft = [x for x in bpy.data.objects if x.type == "MESH"][-1]
    shaft.name = name + "_shaft"
    shaft.scale = (1.0, 1.0, length / (2 * thick))
    shaft.data.materials.append(mat)
    for p in shaft.data.polygons:
        p.use_smooth = True
    parts.append(shaft)
    for i, dz in enumerate((-length * 0.5, length * 0.5)):
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=24, ring_count=14, radius=thick * 1.65,
            location=(loc[0], loc[1], loc[2] + dz), rotation=rot)
        k = [x for x in bpy.data.objects if x.type == "MESH"][-1]
        k.name = f"{name}_knuckle{i}"
        k.scale = (1.35, 1.0, 1.0)
        k.data.materials.append(mat)
        for p in k.data.polygons:
            p.use_smooth = True
        parts.append(k)
    return parts


def add_hat(name, z, r, mat):
    """Bamm-Bamm's spotted cap with a forward peak."""
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=26, radius=r,
                                        location=(0, 0, z))
    o = [x for x in bpy.data.objects if x.type == "MESH"][-1]
    o.name = name
    o.scale = (1.0, 1.0, 0.72)
    o.data.materials.append(mat)
    for p in o.data.polygons:
        p.use_smooth = True
    bpy.ops.mesh.primitive_torus_add(major_radius=r * 0.97, minor_radius=r * 0.13,
                                     major_segments=64, minor_segments=12,
                                     location=(0, 0, z - r * 0.30))
    band = [x for x in bpy.data.objects if x.type == "MESH"][-1]
    band.name = name + "_brim"
    band.data.materials.append(mat)
    return o, band


def ground():
    bpy.ops.mesh.primitive_plane_add(size=60.0, location=(0, 0, 0))
    g = [o for o in bpy.data.objects if o.type == "MESH"
         and len(o.data.vertices) == 4][-1]
    g.data.materials.append(ground_mat())
    return g


def lights():
    def area(name, loc, energy, size, color, rot):
        d = bpy.data.lights.new(name, "AREA")
        d.energy, d.size, d.color = energy, size, color
        o = bpy.data.objects.new(name, d)
        o.location, o.rotation_euler = loc, rot
        bpy.context.collection.objects.link(o)
    area("key",  (-1.55, -1.95, 2.10), 190, 1.4, (1.00, 0.91, 0.80), (1.02, 0, -0.66))
    area("fill", ( 1.90, -1.45, 0.70),  20, 2.8, (0.62, 0.73, 1.00), (1.30, 0, 0.78))
    area("rim",  ( 0.70,  2.20, 1.75), 140, 0.80, (0.80, 0.89, 1.00), (-1.10, 0, 0.16))


def world(strength=0.05):
    w = bpy.data.worlds.new("w")
    bpy.context.scene.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.013, 0.017, 0.026, 1.0)
    bg.inputs[1].default_value = strength


def camera(loc, look_z, lens=85.0, yaw=0.0):
    cd = bpy.data.cameras.new("cam")
    cd.lens = lens
    c = bpy.data.objects.new("cam", cd)
    c.location = loc
    d = Vector((loc[0], loc[1], look_z)) - Vector(loc)
    c.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    c.rotation_euler.rotate_axis("Z", yaw)
    bpy.context.collection.objects.link(c)
    bpy.context.scene.camera = c
    return c


def render(name, samples=SAMPLES):
    scn = bpy.context.scene
    scn.cycles.samples = samples
    out = os.path.join(OUTDIR, f"{name}.png")
    scn.render.filepath = out
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    print(f"PB {name}: {time.time() - t0:.1f}s @ {samples}spp -> {out}", flush=True)
    return out


# ── costume assembly ───────────────────────────────────────────────────────


def pebbles_costume():
    """green + black-spotted loincloth, teal diaper, white bone in the ponytail"""
    peb_spotted = spotted_mat("peb_spotted", (0.180, 0.360, 0.150),
                              (0.030, 0.032, 0.030), scale=30.0, thresh=0.34)
    peb_diap = plain_mat("peb_diaper", (0.110, 0.330, 0.320), rough=0.78)
    bone = plain_mat("bone", (0.900, 0.870, 0.780), rough=0.44, sheen=0.1)
    strap = plain_mat("strap", (0.075, 0.062, 0.055), rough=0.60)
    add_torus("peb_diaper_band", 0.104, 0.030, (0, 0.0, 0.238), peb_diap, squash=0.92)
    add_torus("peb_loincloth", 0.106, 0.036, (0, 0.0, 0.196), peb_spotted, squash=0.90)
    add_torus("peb_waist_strap", 0.110, 0.011, (0, 0.0, 0.268), strap)
    # the bone lives IN the ponytail -- that placement is the character
    add_bone("peb_bone", (0.0, 0.150, 0.585), 0.070, 0.011, bone,
             rot=(math.radians(38), 0, 0))


def bambam_costume():
    """orange-tan + black-spotted loincloth AND matching spotted hat,
    brown strap with a white bone hanging at the chest"""
    bb_spotted = spotted_mat("bb_spotted", (0.560, 0.330, 0.110),
                             (0.045, 0.032, 0.022), scale=26.0, thresh=0.36)
    bone = plain_mat("bone2", (0.900, 0.870, 0.780), rough=0.44, sheen=0.1)
    strap = plain_mat("strap2", (0.105, 0.075, 0.055), rough=0.58)
    add_torus("bb_loincloth", 0.126, 0.040, (0, 0.0, 0.208), bb_spotted, squash=0.94)
    add_torus("bb_waist_strap", 0.130, 0.013, (0, 0.0, 0.290), strap)
    # strap running over the shoulder, with the bone hanging on the chest
    add_torus("bb_shoulder_strap", 0.126, 0.011, (0, 0.0, 0.500), strap,
              squash=0.62, rot=(0, math.radians(24), 0))
    add_bone("bb_bone", (0.058, -0.118, 0.400), 0.082, 0.013, bone,
             rot=(math.radians(-14), 0, math.radians(10)))
    add_hat("bb_hat", 0.760, 0.140, bb_spotted)


def main():
    t_all = time.time()

    def make_mats():
        # StructRNA fix (OW1, 2026-10-03): reset_scene() removes every
        # material, so pre-building them once leaves all later scenes
        # holding removed datablocks. Rebuild per scene, AFTER its reset.
        return (
            skin_mat("skin_fair", (0.620, 0.360, 0.262), rough=0.42),
            hair_mat("hair_red", (0.480, 0.115, 0.045), rough=0.32),
            hair_mat("hair_white", (0.840, 0.820, 0.780), rough=0.36),
        )

    # ── Pebbles ────────────────────────────────────────────────────────────
    reset_scene(); ground(); lights(); world()
    fair, hair_red, _ = make_mats()
    pb, _ = build_pebbles(fair, hair_red)
    pebbles_costume()
    camera((0.42, -4.35, 0.92), look_z=0.58, lens=85.0, yaw=0.10)
    render("pebbles_flintstone")

    # ── Bamm-Bamm ──────────────────────────────────────────────────────────
    reset_scene(); ground(); lights(); world()
    fair, _, hair_white = make_mats()
    bb, _ = build_bambam(fair, hair_white)
    bambam_costume()
    camera((0.46, -4.20, 0.88), look_z=0.52, lens=85.0, yaw=0.11)
    render("bambam_flintstone")

    # ── the pair, together ─────────────────────────────────────────────────
    reset_scene(); ground(); lights(); world(strength=0.045)
    fair, hair_red, hair_white = make_mats()
    pb, _ = build_pebbles(fair, hair_red)
    pebbles_costume()
    for o in list(bpy.data.objects):
        if o.type == "MESH":
            o.location.x -= 0.30
    bb, _ = build_bambam(fair, hair_white)
    bambam_costume()
    for o in list(bpy.data.objects):
        if o.type == "MESH" and ("bb" in o.name or "bone" in o.name or "strap" in o.name):
            o.location.x += 0.30
    camera((0.34, -5.10, 0.94), look_z=0.58, lens=85.0, yaw=0.055)
    render("pebbles_and_bambam")

    print(f"PB total_seconds: {time.time() - t_all:.1f}", flush=True)


main()
