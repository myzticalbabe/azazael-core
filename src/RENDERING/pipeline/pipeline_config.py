# Rendering Pipeline Configuration — The Headless Oxman & Lilith\'s Wraith

PIPELINE_CONFIG = {
    'stage_1_comfyui': {
        'model': 'FLUX.1-dev',
        'resolution': (1024, 1360),
        'steps': 28,
        'cfg': 3.5,
        'controlnets': ['depth', 'openpose', 'canny'],
        'prompts_per_episode': 36,  # 6 angles x 2 chars x 3 variants
    },
    'stage_2_blender': {
        'photogrammetry': 'Meshroom / RealityCapture',
        'rig': 'MetaHuman-compatible + custom glyph/chain/OSL',
        'hair': 'Geometry Nodes (Wraith) / Particle System (Oxman chain)',
        'materials': 'Principled BSDF + custom OSL for glyph glow, Wraith dissipation',
    },
    'stage_3_cycles': {
        'engine': 'Cycles ONEAPI (Arc 140T)',
        'samples': 1024,
        'denoiser': 'OIDN',
        'resolution': (1024, 1360),
        'color': 'Filmic Log, sRGB output',
        'frames_per_episode': 2700,
        'episodes': 6,
        'total_frames': 16200,
    },
    'stage_4_interpolation': {
        'rife': 'RIFE 4.25 (2x interpolation)',
        'output_fps': 60,
        'color_grade': 'DaVinci Resolve, Kodak 2383 LUT + custom curve',
        'audio': 'Spatial mix (Oxman low freq rumble / Wraith high freq whisper)',
        'export': 'HEVC CRF 22, 10-bit, HDR10 metadata',
    },
}

QUALITY_GATES = {
    'asset': '3 distinct perceptual hashes per frame (pHash, dHash, aHash)',
    'animation': 'Temporal consistency: SSIM > 0.98 frame-to-frame',
    'render': 'No fireflies (max pixel < 1.0 after tonemap), no NaN pixels',
    'compression': 'VMAF > 95 vs source PNG',
    'sync': 'Audio-video drift < 5ms over 90s',
}

PRODUCTION_SCHEDULE = {
    'week_1': 'Character concept sheets (6 views each) - ComfyUI + Photoshop',
    'week_1_2': 'Environment concepts (6 locations) - ComfyUI',
    'week_2_3': '3D base meshes + rigs - Blender + Meshroom',
    'week_3_4': 'Animation blocking (all 6 eps) - Blender',
    'week_4_5': 'Lighting + render (16,200 frames total) - Blender Cycles ONEAPI',
    'week_5': 'RIFE interpolation + comp - RIFE + Resolve',
    'week_5_6': 'Audio design + final export - Reaper + Resolve',
}
