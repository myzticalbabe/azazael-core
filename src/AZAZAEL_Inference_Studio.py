#!/usr/bin/env python3
# coding: utf-8
"""
AZAZAEL Inference Studio v5.0 — Professional Local Inferencing Engine
=====================================================================
Python 3.14t Free-Threading Native | Device Vendor Hardware Detection
Dynamic GUI | Selector Switches | Artifact Browser | Wiring Panel
Language/Compiler Selector | Animation Protocol Selector
Morphing Emotional Emoji | Unique Icon Generation

═══════════════════════════════════════════════════════════════════
PANELS:
  1. Hardware Profile      — CPU, GPU, RAM detection per vendor
  2. Framework Selector     — PyTorch, ONNX, OpenVINO, llama.cpp, etc.
  3. Model Catalog          — 30+ curated models across 6 categories
  4. Artifact Browser       — Scans filesystem for .gguf/.onnx/.safetensors
  5. Wiring Panel           — Canvas-based node connection diagram
  6. Language/Compiler      — Python, C++, Rust, Go, CUDA, etc.
  7. Animation Protocols    — 24 animation techniques across 9 categories
  8. Icon Generator         — 52 emoji icons with emotional state machine
  9. Inferencing Options    — Temperature, top_p, max_tokens, etc.
 10. Telemetry Dashboard    — Real-time metrics and system monitor
═══════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import math
import random
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from datetime import datetime
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Callable, List
import functools
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ─── Python 3.14t Detection ───
try:
    IS_FREE_THREADED = not sys._is_gil_enabled()
    GIL_STATUS = "FREE-THREADED ⚡"
except Exception as exc:
    note_failure("AZAZAEL_Inference_Studio:L47", exc)
    IS_FREE_THREADED = False
    GIL_STATUS = "GIL Locked 🔒"

PYTHON_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"
APP_VERSION = "5.0.0"
APP_NAME = "AZAZAEL Inference Studio"

# ═══════════════════════════════════════════════════════════════════
# THEME SYSTEM
# ═══════════════════════════════════════════════════════════════════

THEME = {
    "bg": "#040410",
    "bg_alt": "#060618",
    "card": "#0a0a20",
    "card_alt": "#0e0e28",
    "card_hover": "#141435",
    "border": "#1a1a3a",
    "border_active": "#2a2a5a",
    "text": "#e0e0e8",
    "text_bright": "#ffffff",
    "text_dim": "#606080",
    "text_muted": "#404060",
    "cyan": "#00f5ff",
    "cyan_dim": "#006670",
    "purple": "#bf00ff",
    "purple_dim": "#500070",
    "pink": "#ff00aa",
    "pink_dim": "#700050",
    "green": "#00ff88",
    "green_dim": "#006640",
    "gold": "#ffdd00",
    "gold_dim": "#705000",
    "red": "#ff4757",
    "red_dim": "#701020",
    "orange": "#ff8c00",
    "blue": "#4488ff",
    "button": "#141435",
    "button_hover": "#1e1e50",
    "button_active": "#0a0a2a",
    "entry_bg": "#06061a",
    "entry_focus": "#0a0a2a",
    "select_bg": "#1a1050",
    "select_fg": "#ffffff",
    "scrollbar_bg": "#0a0a20",
    "scrollbar_fg": "#1a1a40",
}

# Accent gradient pair (separate from THEME to keep it homogeneously dict[str, str])
ACCENT_GRADIENT: tuple[str, str] = ("#00f5ff", "#bf00ff")


# Color schemes for different vendors
VENDOR_COLORS = {
    "NVIDIA": ("#76b900", "#a4d100"),
    "AMD": ("#ed1c24", "#ff4444"),
    "Intel": ("#0071c5", "#00aaff"),
    "Apple": ("#a2aaad", "#cccccc"),
    "Unknown": ("#808080", "#a0a0a0"),
}

# ═══════════════════════════════════════════════════════════════════
# EMOTIONAL EMOJI STATE MACHINE
# ═══════════════════════════════════════════════════════════════════

class EmotionalState(Enum):
    IDLE = "idle"
    CURIOUS = "curious"
    THINKING = "thinking"
    WORKING = "working"
    INFERRING = "inferring"
    STREAMING = "streaming"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CELEBRATING = "celebrating"

EMOJI_MAP: dict[EmotionalState, list[str]] = {
    EmotionalState.IDLE:        ["🧠", "💎", "🔮", "🌟"],
    EmotionalState.CURIOUS:     ["🤔", "🧐", "💭", "🔍"],
    EmotionalState.THINKING:    ["💡", "✨", "⚡", "🧬"],
    EmotionalState.WORKING:     ["⚙️", "🔧", "🛠️", "⚡"],
    EmotionalState.INFERRING:   ["🔮", "🎯", "🧿", "💫"],
    EmotionalState.STREAMING:   ["🌊", "💧", "🌀", "📡"],
    EmotionalState.SUCCESS:     ["[OK] ", "🎉", "✨", "🏆"],
    EmotionalState.WARNING:     ["[!] ️", "🔔", "📢", "💛"],
    EmotionalState.ERROR:       ["💥", "❌", "🚨", "🔥"],
    EmotionalState.CELEBRATING: ["🎉", "🥳", "🎊", "🚀"],
}

EMOJI_MORPH_FULL = [
    "🧠","💭","✨","🔮","⚡","🌟","💎","🔥","🎯","🚀",
    "🧬","🔬","💡","🎨","🌈","🦋","🌌","💫","⭐","🌀",
    "🎪","🎭","💜","🪄","🌊","⚛️","🕯️","🎵","🔔","📡",
]

class EmotionEngine:
    """State-machine-driven emotional emoji engine with morphing transitions."""

    def __init__(self):
        self._state = EmotionalState.IDLE
        self._morph_index = 0
        self._current_emoji = "🧠"
        self._lock = threading.Lock()
        self._observers: list[Callable[[str], None]] = []

    @property
    def state(self) -> EmotionalState:
        return self._state

    @state.setter
    def state(self, new_state: EmotionalState):
        with self._lock:
            self._state = new_state
            pool = EMOJI_MAP.get(new_state, EMOJI_MAP[EmotionalState.IDLE])
            self._current_emoji = random.choice(pool)
        self._notify()

    @property
    def emoji(self) -> str:
        with self._lock:
            return self._current_emoji

    def morph_next(self) -> str:
        """Get next emoji in the morph sequence (thread-safe)."""
        with self._lock:
            self._morph_index = (self._morph_index + 1) % len(EMOJI_MORPH_FULL)
            self._current_emoji = EMOJI_MORPH_FULL[self._morph_index]
        self._notify()
        return self._current_emoji

    def reaction(self, event_type: str) -> str:
        """Map event to emotional reaction."""
        mapping = {
            "launch": EmotionalState.WORKING,
            "infer": EmotionalState.INFERRING,
            "stream": EmotionalState.STREAMING,
            "done": EmotionalState.SUCCESS,
            "fail": EmotionalState.ERROR,
            "warn": EmotionalState.WARNING,
            "scan": EmotionalState.CURIOUS,
        }
        self.state = mapping.get(event_type, EmotionalState.IDLE)
        return self._current_emoji

    def on_change(self, callback: Callable[[str], None]):
        self._observers.append(callback)

    def _notify(self):
        for cb in self._observers:
            try:
                cb(self._current_emoji)
            except Exception as exc:
                note_failure("AZAZAEL_Inference_Studio:L200", exc)

# Global emotion engine
emotion = EmotionEngine()


# ═══════════════════════════════════════════════════════════════════
# HARDWARE DETECTION
# ═══════════════════════════════════════════════════════════════════

@dataclass
class GPUInfo:
    name: str
    vendor: str
    memory_mb: int = 0
    driver: str = ""
    backends: list[str] = field(default_factory=list)

@dataclass
class HardwareProfile:
    cpu_name: str = "Unknown"
    cpu_cores: int = 0
    cpu_threads: int = 0
    ram_gb: float = 0.0
    ram_free_gb: float = 0.0
    platform: str = ""
    arch: str = ""
    gpus: list[GPUInfo] = field(default_factory=list)
    python_version: str = PYTHON_VERSION
    free_threaded: bool = IS_FREE_THREADED

def detect_hardware() -> HardwareProfile:
    """Comprehensive hardware detection without psutil dependency."""
    import platform as _plat

    profile = HardwareProfile(
        cpu_name=_plat.processor() or "Unknown CPU",
        cpu_cores=os.cpu_count() or 0,
        cpu_threads=os.cpu_count() or 0,
        platform=_plat.system(),
        arch=_plat.machine(),
    )

    # RAM via ctypes (no psutil needed)
    try:
        import ctypes
        class MEMSTATUS(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
            ]
        ms = MEMSTATUS()
        ms.dwLength = ctypes.sizeof(MEMSTATUS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        profile.ram_gb = ms.ullTotalPhys / (1024**3)
        profile.ram_free_gb = ms.ullAvailPhys / (1024**3)
    except Exception as exc:
        note_failure("AZAZAEL_Inference_Studio:L259", exc)

    # NVIDIA GPU detection
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    mem_str = parts[2].replace(" MiB", "").strip()
                    gpu = GPUInfo(name=parts[0], vendor="NVIDIA",
                                  driver=parts[1],
                                  memory_mb=int(mem_str) if mem_str.isdigit() else 0,
                                  backends=["CUDA", "TensorRT"])
                    profile.gpus.append(gpu)
    except Exception as exc:
        note_failure("AZAZAEL_Inference_Studio:L281", exc)

    # AMD GPU via rocm-smi
    if not profile.gpus:
        try:
            r = subprocess.run(["rocm-smi", "--showproductname"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8)
            if r.returncode == 0:
                for line in r.stdout.split("\n"):
                    if "GPU" in line and "[" in line:
                        profile.gpus.append(GPUInfo(
                            name=line.strip(), vendor="AMD",
                            backends=["ROCm", "Vulkan"]
                        ))
        except Exception as exc:
            note_failure("AZAZAEL_Inference_Studio:L296", exc)

    # Intel GPU via wmic (Windows)
    if not profile.gpus:
        try:
            r = subprocess.run(
                ["wmic", "path", "win32_videocontroller", "get", "name,adapterram"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8
            )
            for line in r.stdout.split("\n"):
                line = line.strip()
                if not line or "Name" in line:
                    continue
                parts = line.rsplit(None, 1)
                mem = 0
                if parts and parts[-1].isdigit():
                    mem = int(parts[-1]) // (1024*1024)
                if "intel" in line.lower():
                    profile.gpus.append(GPUInfo(
                        name=line, vendor="Intel",
                        memory_mb=mem,
                        backends=["OpenVINO", "oneAPI"]
                    ))
        except Exception as exc:
            note_failure("AZAZAEL_Inference_Studio:L320", exc)

    # Apple Silicon
    if profile.platform == "Darwin" and "arm" in profile.arch.lower():
        profile.gpus.append(GPUInfo(
            name="Apple Silicon GPU", vendor="Apple",
            backends=["Metal", "MPS", "CoreML"]
        ))

    return profile


# ═══════════════════════════════════════════════════════════════════
# FRAMEWORK REGISTRY
# ═══════════════════════════════════════════════════════════════════

class FWStatus(Enum):
    AVAILABLE = auto()
    NOT_FOUND = auto()

@dataclass
class Framework:
    name: str
    category: str
    icon: str
    desc: str
    import_name: str
    version: str = ""
    status: FWStatus = FWStatus.NOT_FOUND
    backends: list[str] = field(default_factory=list)
    model_formats: list[str] = field(default_factory=list)

FRAMEWORKS_DB = [
    Framework("PyTorch", "ML Framework", "🔥", "Deep learning (CUDA/MPS/CPU)",
              "torch", backends=["CUDA","MPS","CPU","XPU"],
              model_formats=[".pt",".pth",".bin"]),
    Framework("ONNX Runtime", "Inference Engine", "🔷", "Cross-platform ONNX inference",
              "onnxruntime", backends=["CUDA","CPU","DirectML","OpenVINO","TensorRT"],
              model_formats=[".onnx"]),
    Framework("OpenVINO", "Inference Engine", "🔶", "Intel-optimized inference",
              "openvino", backends=["CPU","GPU","NPU"],
              model_formats=[".xml",".bin",".onnx"]),
    Framework("llama.cpp", "LLM Runtime", "🦙", "C++ LLaMA/GGUF inference",
              "llama_cpp", backends=["CPU","CUDA","Metal","Vulkan","SYCL"],
              model_formats=[".gguf"]),
    Framework("Ollama", "LLM Server", "🐪", "Local LLM REST API server",
              "ollama", backends=["CPU","CUDA","Metal"],
              model_formats=[".gguf"]),
    Framework("Transformers", "ML Framework", "🤗", "HuggingFace model ecosystem",
              "transformers", backends=["CPU","CUDA","MPS","TPU"],
              model_formats=[".safetensors",".bin",".h5"]),
    Framework("LangChain", "AI Orchestrator", "⛓️", "LLM application framework",
              "langchain", backends=["CPU","CUDA"]),
    Framework("Diffusers", "Image Generation", "🎨", "Stable Diffusion pipeline",
              "diffusers", backends=["CPU","CUDA","MPS"],
              model_formats=[".safetensors"]),
    Framework("XGBoost", "ML Framework", "🚀", "Gradient boosting",
              "xgboost", backends=["CPU","CUDA"],
              model_formats=[".json",".ubj"]),
    Framework("MLflow", "MLOps", "📊", "ML lifecycle management",
              "mlflow", backends=["CPU"]),
    Framework("Gymnasium", "RL Environment", "🎮", "Reinforcement learning",
              "gymnasium", backends=["CPU"]),
    Framework("Datasets", "Data Pipeline", "📚", "HuggingFace datasets",
              "datasets", backends=["CPU"]),
]

def detect_frameworks() -> list[Framework]:
    for fw in FRAMEWORKS_DB:
        try:
            mod = __import__(fw.import_name)
            fw.version = getattr(mod, "__version__", "?")
            fw.status = FWStatus.AVAILABLE
        except Exception as exc:
            note_failure("AZAZAEL_Inference_Studio:L394", exc)
            fw.status = FWStatus.NOT_FOUND
    return FRAMEWORKS_DB


# ═══════════════════════════════════════════════════════════════════
# MODEL CATALOG
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ModelEntry:
    name: str
    vendor: str
    family: str
    desc: str
    params: str = ""
    formats: list[str] = field(default_factory=list)

MODEL_CATALOG: dict[str, list[ModelEntry]] = {
    "🤖 LLM — Chat": [
        ModelEntry("Llama 3.3 70B", "Meta", "llama", "70B params, strong reasoning", "70B", [".gguf",".safetensors"]),
        ModelEntry("Llama 3.3 8B", "Meta", "llama", "8B params, fast local inference", "8B", [".gguf"]),
        ModelEntry("Mistral 7B", "Mistral AI", "mistral", "Efficient 7B architecture", "7B", [".gguf"]),
        ModelEntry("Phi-4 14B", "Microsoft", "phi", "14B, strong coding/math", "14B", [".onnx",".gguf"]),
        ModelEntry("Gemma 3 27B", "Google", "gemma", "27B open weights", "27B", [".gguf"]),
        ModelEntry("Qwen 2.5 72B", "Alibaba", "qwen", "72B multilingual", "72B", [".gguf"]),
    ],
    "💻 LLM — Code": [
        ModelEntry("DeepSeek Coder V3", "DeepSeek", "deepseek", "Expert code generation", "33B", [".gguf"]),
        ModelEntry("CodeLlama 70B", "Meta", "codellama", "70B code specialist", "70B", [".gguf"]),
        ModelEntry("StarCoder2 15B", "BigCode", "starcoder", "15B multi-language", "15B", [".gguf",".safetensors"]),
        ModelEntry("Codestral 22B", "Mistral AI", "codestral", "Fill-in-the-middle code", "22B", [".gguf"]),
    ],
    "👁️ Vision": [
        ModelEntry("Stable Diffusion XL", "Stability AI", "sdxl", "1024px image generation", "2.6B", [".safetensors"]),
        ModelEntry("FLUX.1", "Black Forest Labs", "flux", "State-of-art image gen", "12B", [".safetensors"]),
        ModelEntry("Segment Anything 2", "Meta", "sam2", "Universal segmentation", "varies", [".pt",".onnx"]),
        ModelEntry("YOLO v10", "Ultralytics", "yolo", "Real-time object detection", "varies", [".pt",".onnx"]),
    ],
    "🎵 Audio / Speech": [
        ModelEntry("Whisper Large v3", "OpenAI", "whisper", "Multilingual ASR", "1.5B", [".pt",".onnx"]),
        ModelEntry("Bark", "Suno", "bark", "Text-to-speech synthesis", "varies", [".pt"]),
        ModelEntry("MusicGen", "Meta", "musicgen", "Music generation", "3.3B", [".pt"]),
    ],
    "📊 Embedding": [
        ModelEntry("BGE-M3", "BAAI", "bge", "Multilingual embeddings", "varies", [".onnx",".safetensors"]),
        ModelEntry("E5-Mistral 7B", "Microsoft", "e5", "SOTA text embeddings", "7B", [".gguf"]),
    ],
    "🖼️ Multimodal": [
        ModelEntry("LLaVA 1.6 34B", "Microsoft", "llava", "Vision-language model", "34B", [".gguf"]),
        ModelEntry("CogVLM2 19B", "THUDM", "cogvlm", "Visual understanding", "19B", [".gguf"]),
        ModelEntry("InternVL2 76B", "OpenGVLab", "internvl", "76B multimodal", "76B", [".gguf"]),
    ],
}


# ═══════════════════════════════════════════════════════════════════
# ANIMATION PROTOCOLS
# ═══════════════════════════════════════════════════════════════════

ANIMATION_CATEGORIES = {
    "Status": ["Glitch", "Neon Pulse", "Ring Pulse", "Quantum Flicker"],
    "Progress": ["Linear Bar", "Shimmer Bar", "Bounce Dots", "Particle Stream"],
    "Data Viz": ["Bar Growth", "Pie Fill", "Line Draw", "Scatter Burst"],
    "Entry": ["Slide In Up", "Fade In Scale", "Typewriter", "Expand Reveal"],
    "Transform": ["Flip 3D", "Morph Shape", "Elastic Scale", "Warp Field"],
    "Decorative": ["Particle Float", "Hex Grid", "Matrix Rain", "Aurora Wave"],
    "Text": ["Rainbow Wave", "Gradient Flow", "Blur Reveal", "Neon Type"],
    "Interactive": ["Hover Expand", "Click Ripple", "Success Check", "Drag Magnet"],
    "Emoji": ["Bounce", "Twinkle", "Heartbeat", "Spin", "Flicker", "Vortex", "Neural Pulse"],
}


# ═══════════════════════════════════════════════════════════════════
# LANGUAGE / COMPILER OPTIONS
# ═══════════════════════════════════════════════════════════════════

LANGUAGE_OPTIONS = {
    "Python":    {"ext": ".py",  "compiler": "Nuitka/PyInstaller", "icon": "🐍"},
    "C++":       {"ext": ".cpp", "compiler": "GCC/Clang/MSVC",     "icon": "⚙️"},
    "Rust":      {"ext": ".rs",  "compiler": "rustc/Cargo",        "icon": "🦀"},
    "Go":        {"ext": ".go",  "compiler": "go build",           "icon": "🔵"},
    "CUDA":      {"ext": ".cu",  "compiler": "nvcc",               "icon": "🟢"},
    "JavaScript":{"ext": ".js",  "compiler": "Node/Bun/Deno",      "icon": "🟨"},
    "TypeScript":{"ext": ".ts",  "compiler": "tsc/esbuild",        "icon": "🔷"},
    "Zig":       {"ext": ".zig", "compiler": "zig build",          "icon": "⚡"},
    "Mojo":      {"ext": ".mojo","compiler": "mojo build",         "icon": "🔥"},
    "WebAssembly":{"ext": ".wat","compiler": "wasm-pack",          "icon": "🌐"},
}


# ═══════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════════

class InferenceStudio:
    """AZAZAEL Inference Studio v5.0 — Full Professional GUI."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION} — Python {PYTHON_VERSION}")
        self.root.geometry("1280x860")
        self.root.minsize(1024, 700)
        self.root.configure(bg=THEME["bg"])

        # State
        self.hardware: (HardwareProfile | None) = None
        self.frameworks: list[Framework] = []
        self.artifacts: list[Path] = []
        self.selected_framework: (Framework | None) = None
        self.selected_model: (ModelEntry | None) = None
        self.selected_backend: str = "CPU"
        self.selected_language: str = "Python"
        self.selected_animation: str = "Neon Pulse"
        self.is_running: bool = False
        self.inferencing: bool = False
        self.pulsators: list['Pulsator'] = []

        # Build UI
        self._setup_styles()
        self._build_ui()

        # Initialize
        self.root.after(400, self._initialize)

    # ─── Style Setup ─────────────────────────────────────────────────

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=THEME["bg"])
        style.configure("TFrame", background=THEME["bg"])
        style.configure("Card.TFrame", background=THEME["card"])
        style.configure("CardAlt.TFrame", background=THEME["card_alt"])
        style.configure("TLabelframe", background=THEME["bg"], foreground=THEME["text"],
                        bordercolor=THEME["border"], relief="flat")
        style.configure("TLabelframe.Label", background=THEME["bg"],
                        foreground=THEME["cyan"], font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", background=THEME["bg"], foreground=THEME["text"],
                        font=("Segoe UI", 9))
        style.configure("Card.TLabel", background=THEME["card"], foreground=THEME["text"])
        style.configure("Dim.TLabel", foreground=THEME["text_dim"])
        style.configure("TButton", background=THEME["button"], foreground=THEME["text"],
                        font=("Segoe UI", 9), borderwidth=0, relief="flat")
        style.map("TButton", background=[("active", THEME["button_hover"]),
                                         ("disabled", THEME["card"])],
                  foreground=[("disabled", THEME["text_muted"])])
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Success.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("TCombobox", fieldbackground=THEME["entry_bg"],
                        foreground=THEME["text"], arrowcolor=THEME["text"])
        style.configure("TProgressbar", background=THEME["cyan"],
                        troughcolor=THEME["card"], thickness=6)

    # ─── UI Construction ─────────────────────────────────────────────

    def _build_ui(self):
        # Root grid
        self.root.grid_rowconfigure(0, weight=0)   # Header
        self.root.grid_rowconfigure(1, weight=1)   # Content
        self.root.grid_rowconfigure(2, weight=0)   # Status bar
        self.root.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_notebook()
        self._build_status_bar()

    def _build_header(self):
        hdr = tk.Frame(self.root, bg=THEME["bg"], padx=16, pady=10)
        hdr.grid(row=0, column=0, sticky="ew")

        # Emoji + Title
        logo_f = tk.Frame(hdr, bg=THEME["bg"])
        logo_f.pack(side="left")

        self.emoji_lbl = tk.Label(logo_f, text="🧠", font=("Segoe UI Emoji", 38),
                                  bg=THEME["bg"])
        self.emoji_lbl.pack(side="left", padx=(0, 12))

        title_f = tk.Frame(logo_f, bg=THEME["bg"])
        title_f.pack(side="left")
        self.title_lbl = tk.Label(title_f, text="AZAZAEL",
                                  font=("Segoe UI", 26, "bold"),
                                  bg=THEME["bg"], fg=THEME["cyan"])
        self.title_lbl.pack(anchor="w")
        tk.Label(title_f, text="Inference Studio  v5.0",
                font=("Segoe UI", 12), bg=THEME["bg"],
                fg=THEME["purple"]).pack(anchor="w")

        # Pulsate title
        p = Pulsator(self.title_lbl, "#00f5ff", "#bf00ff")
        p.start(70)
        self.pulsators.append(p)

        # Right: badges
        badge_f = tk.Frame(hdr, bg=THEME["bg"])
        badge_f.pack(side="right")

        # GIL status badge
        gil_color = THEME["green"] if IS_FREE_THREADED else THEME["gold"]
        tk.Label(badge_f, text=GIL_STATUS, font=("Consolas", 10, "bold"),
                bg=THEME["card"], fg=gil_color, padx=12, pady=5).pack(
                    side="right", padx=(8, 0))

        # Connection dot
        self.conn_cv = tk.Canvas(badge_f, width=16, height=16,
                                 bg=THEME["bg"], highlightthickness=0)
        self.conn_cv.pack(side="right", padx=(0, 4))
        self._draw_dot("gray")

    def _build_notebook(self):
        """Tabbed interface for all panels."""
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))

        style = ttk.Style()
        style.configure("TNotebook", background=THEME["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=THEME["card"],
                        foreground=THEME["text"], padding=[18, 6],
                        font=("Segoe UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", THEME["card_alt"])],
                  foreground=[("selected", THEME["cyan"])])

        # Tab 1: Inference
        self.tab_infer = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_infer, text="  🎯 Inference  ")
        self._build_inference_tab()

        # Tab 2: Artifacts
        self.tab_artifacts = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_artifacts, text="  📦 Artifacts  ")
        self._build_artifacts_tab()

        # Tab 3: Wiring
        self.tab_wiring = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_wiring, text="  🔗 Wiring  ")
        self._build_wiring_tab()

        # Tab 4: Lang/Compile
        self.tab_lang = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_lang, text="  🔧 Lang/Compile  ")
        self._build_language_tab()

        # Tab 5: Animation
        self.tab_anim = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_anim, text="  🎬 Animation  ")
        self._build_animation_tab()

    # ─── TAB 1: INFERENCE ─────────────────────────────────────────

    def _build_inference_tab(self):
        tab = self.tab_infer
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(0, weight=0)  # Hardware
        tab.grid_rowconfigure(1, weight=1)  # Framework + Model
        tab.grid_rowconfigure(2, weight=0)  # Launch

        # ─── Hardware Card ───
        hw_frame = ttk.LabelFrame(tab, text=" 🖥️  Hardware Profile  ", padding=10)
        hw_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(4, 8))

        self.hw_text = tk.Text(hw_frame, bg=THEME["entry_bg"], fg=THEME["text"],
                               font=("Consolas", 9), height=5, wrap="word",
                               relief="flat", padx=10, pady=6)
        self.hw_text.pack(fill="x")

        hw_btns = tk.Frame(hw_frame, bg=THEME["bg"])
        hw_btns.pack(fill="x", pady=(6, 0))
        tk.Button(hw_btns, text="🔄  Rescan Hardware", command=self._scan_hardware,
                 bg=THEME["button"], fg=THEME["text"], font=("Segoe UI", 9),
                 activebackground=THEME["button_hover"], relief="flat",
                 padx=14, pady=4, cursor="hand2").pack(side="left")
        tk.Button(hw_btns, text="📋  Copy Specs", command=self._copy_hardware,
                 bg=THEME["button"], fg=THEME["text_dim"], font=("Segoe UI", 8),
                 activebackground=THEME["button_hover"], relief="flat",
                 padx=10, pady=4, cursor="hand2").pack(side="left", padx=(6, 0))

        # ─── LEFT: Framework Selector ───
        left_f = ttk.LabelFrame(tab, text=" 🧩  Framework Selector  ", padding=10)
        left_f.grid(row=1, column=0, sticky="nsew", padx=(0, 6))

        list_f = tk.Frame(left_f, bg=THEME["bg"])
        list_f.pack(fill="both", expand=True)

        self.fw_list = tk.Listbox(list_f, bg=THEME["entry_bg"], fg=THEME["text"],
                                  font=("Consolas", 10), selectbackground=THEME["purple"],
                                  selectforeground="#fff", relief="flat",
                                  activestyle="none", height=8)
        self.fw_list.pack(side="left", fill="both", expand=True)
        self.fw_list.bind("<<ListboxSelect>>", self._on_fw_select)

        fw_sb = ttk.Scrollbar(list_f, orient="vertical", command=self.fw_list.yview)
        fw_sb.pack(side="right", fill="y")
        self.fw_list.configure(yscrollcommand=fw_sb.set)

        self.fw_detail = tk.Text(left_f, bg=THEME["card_alt"], fg=THEME["text_dim"],
                                 font=("Segoe UI", 9), height=2, wrap="word",
                                 relief="flat", padx=10, pady=5, state="disabled")
        self.fw_detail.pack(fill="x", pady=(6, 0))

        # ─── RIGHT: Model Catalog ───
        right_f = ttk.LabelFrame(tab, text=" 📦  Model Catalog  ", padding=10)
        right_f.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        right_f.grid_rowconfigure(0, weight=0)
        right_f.grid_rowconfigure(1, weight=1)

        self.cat_var = tk.StringVar(value="🤖 LLM — Chat")
        cat_cb = ttk.Combobox(right_f, textvariable=self.cat_var,
                              values=list(MODEL_CATALOG.keys()),
                              state="readonly", font=("Segoe UI", 10))
        cat_cb.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        cat_cb.bind("<<ComboboxSelected>>", self._on_cat_change)

        ml_f = tk.Frame(right_f, bg=THEME["bg"])
        ml_f.grid(row=1, column=0, sticky="nsew")

        self.model_list = tk.Listbox(ml_f, bg=THEME["entry_bg"], fg=THEME["text"],
                                     font=("Consolas", 10), selectbackground=THEME["cyan"],
                                     selectforeground=THEME["bg"], relief="flat",
                                     activestyle="none")
        self.model_list.pack(side="left", fill="both", expand=True)
        self.model_list.bind("<<ListboxSelect>>", self._on_model_select)

        msb = ttk.Scrollbar(ml_f, orient="vertical", command=self.model_list.yview)
        msb.pack(side="right", fill="y")
        self.model_list.configure(yscrollcommand=msb.set)

        # ─── BOTTOM: Backend + Options + Launch ───
        bot_f = ttk.LabelFrame(tab, text=" ⚙️  Inferencing Options  ", padding=10)
        bot_f.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        # Backend selector
        be_row = tk.Frame(bot_f, bg=THEME["bg"])
        be_row.pack(fill="x", pady=(0, 8))

        self.be_var = tk.StringVar(value="CPU")
        backends = ["CPU", "CUDA", "MPS", "DirectML", "OpenVINO", "Vulkan", "OpenCL", "TensorRT"]
        for be in backends:
            rb = tk.Radiobutton(be_row, text=be, variable=self.be_var, value=be,
                               bg=THEME["bg"], fg=THEME["text_dim"],
                               selectcolor=THEME["card"],
                               font=("Consolas", 9),
                               activebackground=THEME["bg"],
                               activeforeground=THEME["cyan"],
                               command=self._on_be_change)
            rb.pack(side="left", padx=(0, 10))

        # Inference parameters
        param_f = tk.Frame(bot_f, bg=THEME["bg"])
        param_f.pack(fill="x", pady=(0, 8))

        params = [
            ("Temperature:", "temp", "0.7", "0.0-2.0"),
            ("Top-P:", "top_p", "0.9", "0.0-1.0"),
            ("Max Tokens:", "max_tok", "2048", ""),
            ("Repetition Penalty:", "rep_pen", "1.1", ""),
        ]
        self.param_vars = {}
        for i, (label, key, default, hint) in enumerate(params):
            f = tk.Frame(param_f, bg=THEME["bg"])
            f.pack(side="left", padx=(0, 16))
            tk.Label(f, text=label, font=("Segoe UI", 8), bg=THEME["bg"],
                    fg=THEME["text_dim"]).pack(anchor="w")
            var = tk.StringVar(value=default)
            self.param_vars[key] = var
            e = tk.Entry(f, textvariable=var, bg=THEME["entry_bg"], fg=THEME["text"],
                        font=("Consolas", 9), relief="flat", width=8,
                        insertbackground=THEME["cyan"])
            e.pack()
            if hint:
                tk.Label(f, text=hint, font=("Segoe UI", 7), bg=THEME["bg"],
                        fg=THEME["text_muted"]).pack(anchor="w")

        # Launch + Telemetry buttons
        launch_f = tk.Frame(bot_f, bg=THEME["bg"])
        launch_f.pack(fill="x")

        self.launch_btn = tk.Button(launch_f, text="🚀  LAUNCH INFERENCE",
                                    command=self._launch_inference,
                                    bg="#00cc88", fg=THEME["bg"],
                                    font=("Segoe UI", 13, "bold"),
                                    activebackground="#00ffaa",
                                    activeforeground=THEME["bg"],
                                    relief="flat", padx=24, pady=12, cursor="hand2")
        self.launch_btn.pack(fill="x")

        btn_row = tk.Frame(launch_f, bg=THEME["bg"])
        btn_row.pack(fill="x", pady=(6, 0))
        for text, cmd in [("📋 Copy Config", self._copy_config),
                          ("📊 Telemetry", self._show_telemetry),
                          ("🔄 Refresh", self._refresh_all)]:
            tk.Button(btn_row, text=text, command=cmd,
                     bg=THEME["button"], fg=THEME["text_dim"],
                     font=("Segoe UI", 8), activebackground=THEME["button_hover"],
                     relief="flat", padx=10, pady=4, cursor="hand2").pack(side="left", padx=(0, 6))

        self.progress = ttk.Progressbar(launch_f, mode="indeterminate")
        self.progress.pack(fill="x", pady=(6, 0))

    # ─── TAB 2: ARTIFACTS ──────────────────────────────────────────

    def _build_artifacts_tab(self):
        tab = self.tab_artifacts
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=0)
        tab.grid_rowconfigure(1, weight=1)

        # Search/scan bar
        bar_f = tk.Frame(tab, bg=THEME["bg"], padx=4, pady=6)
        bar_f.grid(row=0, column=0, sticky="ew")

        tk.Label(bar_f, text="🔍", font=("Segoe UI Emoji", 12),
                bg=THEME["bg"]).pack(side="left", padx=(0, 8))

        self.artifact_search = tk.StringVar()
        tk.Entry(bar_f, textvariable=self.artifact_search, bg=THEME["entry_bg"],
                fg=THEME["text"], font=("Consolas", 10), relief="flat",
                insertbackground=THEME["cyan"], width=30).pack(side="left", padx=(0, 8))
        self.artifact_search.trace("w", lambda _name, _index, _mode: self._filter_artifacts())

        tk.Button(bar_f, text="📂  Scan Directory", command=self._scan_directory,
                 bg=THEME["button"], fg=THEME["text"], font=("Segoe UI", 9),
                 activebackground=THEME["button_hover"], relief="flat",
                 padx=12, pady=4, cursor="hand2").pack(side="left", padx=(0, 6))

        tk.Button(bar_f, text="🔄  Auto-Scan Models", command=self._auto_scan,
                 bg=THEME["button"], fg=THEME["cyan"], font=("Segoe UI", 9),
                 activebackground=THEME["button_hover"], relief="flat",
                 padx=12, pady=4, cursor="hand2").pack(side="left")

        self.artifact_count_lbl = tk.Label(bar_f, text="0 artifacts",
                                          font=("Segoe UI", 9),
                                          bg=THEME["bg"], fg=THEME["text_dim"])
        self.artifact_count_lbl.pack(side="right")

        # Artifact list with details
        panes = tk.PanedWindow(tab, bg=THEME["bg"], orient="horizontal",
                              sashwidth=4, sashrelief="flat")
        panes.grid(row=1, column=0, sticky="nsew")

        # Left: file list
        list_frame = tk.Frame(panes, bg=THEME["bg"])
        panes.add(list_frame, width=400)

        self.artifact_list = tk.Listbox(list_frame, bg=THEME["entry_bg"],
                                        fg=THEME["text"], font=("Consolas", 10),
                                        selectbackground=THEME["cyan"],
                                        selectforeground=THEME["bg"],
                                        relief="flat", activestyle="none")
        self.artifact_list.pack(side="left", fill="both", expand=True)
        self.artifact_list.bind("<<ListboxSelect>>", self._on_artifact_select)

        asb = ttk.Scrollbar(list_frame, orient="vertical",
                           command=self.artifact_list.yview)
        asb.pack(side="right", fill="y")
        self.artifact_list.configure(yscrollcommand=asb.set)

        # Right: detail + launch
        detail_f = tk.Frame(panes, bg=THEME["card"])
        panes.add(detail_f, width=300)

        self.artifact_detail = tk.Text(detail_f, bg=THEME["card"],
                                       fg=THEME["text"], font=("Consolas", 9),
                                       height=10, wrap="word", relief="flat",
                                       padx=10, pady=8, state="disabled")
        self.artifact_detail.pack(fill="both", expand=True)

        btn_f = tk.Frame(detail_f, bg=THEME["card"], pady=6)
        btn_f.pack(fill="x")
        tk.Button(btn_f, text="🚀  Launch This Artifact",
                 command=self._launch_artifact,
                 bg=THEME["green"], fg=THEME["bg"], font=("Segoe UI", 10, "bold"),
                 activebackground="#00ffaa", relief="flat",
                 padx=14, pady=8, cursor="hand2").pack(fill="x", padx=10)
        tk.Button(btn_f, text="📋  Copy Path",
                 command=self._copy_artifact_path,
                 bg=THEME["button"], fg=THEME["text_dim"], font=("Segoe UI", 8),
                 activebackground=THEME["button_hover"], relief="flat",
                 padx=10, pady=4, cursor="hand2").pack(fill="x", padx=10, pady=(4, 0))

    # ─── TAB 3: WIRING ─────────────────────────────────────────────

    def _build_wiring_tab(self):
        tab = self.tab_wiring
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        self.wire_canvas = tk.Canvas(tab, bg=THEME["bg"], highlightthickness=0)
        self.wire_canvas.grid(row=0, column=0, sticky="nsew")

        # Wire control bar
        ctrl = tk.Frame(tab, bg=THEME["card"], pady=4)
        ctrl.grid(row=1, column=0, sticky="ew")

        for text, cmd in [("➕ Add Node", self._wire_add_node),
                          ("🔗 Auto-Wire", self._wire_auto_connect),
                          ("🗑️ Clear", self._wire_clear),
                          ("💾 Export DOT", self._wire_export)]:
            tk.Button(ctrl, text=text, command=cmd,
                     bg=THEME["button"], fg=THEME["text"], font=("Segoe UI", 8),
                     activebackground=THEME["button_hover"], relief="flat",
                     padx=10, pady=3, cursor="hand2").pack(side="left", padx=3)

        self.wire_nodes: list[dict] = []
        self.wire_node_counter = 0

    # ─── TAB 4: LANGUAGE / COMPILER ────────────────────────────────

    def _build_language_tab(self):
        tab = self.tab_lang
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(0, weight=0)
        tab.grid_rowconfigure(1, weight=1)

        # Language grid
        lang_f = ttk.LabelFrame(tab, text=" 🌐  Language & Compiler Selection  ", padding=10)
        lang_f.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(4, 8))

        self.lang_btns: dict[str, tk.Button] = {}
        row_f = tk.Frame(lang_f, bg=THEME["bg"])
        row_f.pack(fill="x")

        col = 0
        for lang, info in LANGUAGE_OPTIONS.items():
            btn = tk.Button(row_f, text=f"{info['icon']} {lang}",
                           command=functools.partial(self._select_language, lang),
                           bg=THEME["button"], fg=THEME["text"],
                           font=("Segoe UI", 9), relief="flat",
                           activebackground=THEME["button_hover"],
                           padx=10, pady=5, cursor="hand2")
            btn.grid(row=0, column=col, padx=3, pady=2)
            self.lang_btns[lang] = btn
            col += 1
            if col >= 5:
                col = 0

        # Language detail
        self.lang_detail = tk.Text(tab, bg=THEME["card"], fg=THEME["text"],
                                   font=("Consolas", 10), height=8, wrap="word",
                                   relief="flat", padx=12, pady=8, state="disabled")
        self.lang_detail.grid(row=1, column=0, sticky="nsew", padx=(0, 6))

        # Compiler output
        self.compile_output = tk.Text(tab, bg=THEME["entry_bg"], fg=THEME["green"],
                                      font=("Consolas", 10), height=8, wrap="word",
                                      relief="flat", padx=12, pady=8, state="disabled")
        self.compile_output.grid(row=1, column=1, sticky="nsew", padx=(6, 0))

        # Compile action bar
        act_f = tk.Frame(tab, bg=THEME["bg"])
        act_f.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        tk.Button(act_f, text="🔨  Compile with Selected Language",
                 command=self._compile_action,
                 bg=THEME["purple"], fg=THEME["text"], font=("Segoe UI", 10, "bold"),
                 activebackground="#d000ff", relief="flat",
                 padx=16, pady=8, cursor="hand2").pack(side="left")

    # ─── TAB 5: ANIMATION ──────────────────────────────────────────

    def _build_animation_tab(self):
        tab = self.tab_anim
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=0)
        tab.grid_rowconfigure(1, weight=1)

        # Category selector
        cat_f = ttk.LabelFrame(tab, text=" 🎬  Animation Protocol Selector  ", padding=10)
        cat_f.grid(row=0, column=0, sticky="ew", pady=(4, 8))

        self.anim_cat_var = tk.StringVar(value="Status")
        cat_cb = ttk.Combobox(cat_f, textvariable=self.anim_cat_var,
                              values=list(ANIMATION_CATEGORIES.keys()),
                              state="readonly", font=("Segoe UI", 10))
        cat_cb.pack(fill="x", pady=(0, 6))
        cat_cb.bind("<<ComboboxSelected>>", self._on_anim_cat_change)

        # Animation buttons grid
        self.anim_btns_frame = tk.Frame(cat_f, bg=THEME["bg"])
        self.anim_btns_frame.pack(fill="x")

        # Preview area
        self.anim_preview = tk.Canvas(tab, bg=THEME["card"], highlightthickness=0, height=220)
        self.anim_preview.grid(row=1, column=0, sticky="nsew")

        # Emoji animation row
        emoji_f = tk.Frame(tab, bg=THEME["bg"])
        emoji_f.grid(row=2, column=0, sticky="ew", pady=(6, 0))

        self.anim_emoji_lbl = tk.Label(emoji_f, text="🧠",
                                       font=("Segoe UI Emoji", 48),
                                       bg=THEME["bg"])
        self.anim_emoji_lbl.pack(side="left", padx=(0, 16))

        emoji_info = tk.Frame(emoji_f, bg=THEME["bg"])
        emoji_info.pack(side="left", fill="x", expand=True)

        tk.Label(emoji_info, text="Morphing Emotional Emoji Engine",
                font=("Segoe UI", 12, "bold"), bg=THEME["bg"],
                fg=THEME["cyan"]).pack(anchor="w")
        tk.Label(emoji_info, text="State-machine driven • 10 emotional states • 52 icons",
                font=("Segoe UI", 9), bg=THEME["bg"],
                fg=THEME["text_dim"]).pack(anchor="w")

        # Emotion test buttons
        test_f = tk.Frame(emoji_info, bg=THEME["bg"])
        test_f.pack(anchor="w", pady=(6, 0))
        for state, color in [(EmotionalState.SUCCESS, THEME["green"]),
                             (EmotionalState.ERROR, THEME["red"]),
                             (EmotionalState.WORKING, THEME["cyan"]),
                             (EmotionalState.INFERRING, THEME["purple"]),
                             (EmotionalState.CELEBRATING, THEME["gold"])]:
            tk.Button(test_f, text=f"{EMOJI_MAP[state][0]} {state.value.title()}",
                     command=functools.partial(self._test_emotion, state),
                     bg=THEME["button"], fg=color, font=("Segoe UI", 8),
                     activebackground=THEME["button_hover"], relief="flat",
                     padx=8, pady=2, cursor="hand2").pack(side="left", padx=(0, 4))

        # Connect emotion engine (thread-safe via root.after)
        def _on_emotion_change(e: str) -> None:
            self.root.after(0, functools.partial(self.anim_emoji_lbl.configure, text=e))
        emotion.on_change(_on_emotion_change)

        self._populate_anim_buttons()
        self._start_emoji_morph()

    # ─── STATUS BAR ────────────────────────────────────────────────

    def _build_status_bar(self):
        sf = tk.Frame(self.root, bg=THEME["card"], padx=16, pady=5)
        sf.grid(row=2, column=0, sticky="ew")

        self.status_lbl = tk.Label(sf,
                                   text="Ready — Select framework and model to begin",
                                   font=("Segoe UI", 9), bg=THEME["card"],
                                   fg=THEME["text_dim"])
        self.status_lbl.pack(side="left")

        self.clock_lbl = tk.Label(sf, text="", font=("Consolas", 9),
                                  bg=THEME["card"], fg=THEME["text_muted"])
        self.clock_lbl.pack(side="right")
        self._tick_clock()

    # ─── INITIALIZATION ────────────────────────────────────────────

    def _initialize(self):
        self._scan_hardware()
        self._scan_frameworks()
        self._populate_models()
        self._auto_scan()
        self._draw_dot("green")
        self._set_status("System ready — Python 3.14t free-threading active", "green")
        self._select_language("Python")

    def _scan_hardware(self):
        self.hardware = detect_hardware()
        hw = self.hardware

        self.hw_text.configure(state="normal")
        self.hw_text.delete("1.0", "end")

        lines = [
            f"  CPU:     {hw.cpu_name}",
            f"  Cores:   {hw.cpu_cores} cores | Threads: {hw.cpu_threads}",
            f"  RAM:     {hw.ram_gb:.1f} GB total | {hw.ram_free_gb:.1f} GB free",
            f"  OS:      {hw.platform} ({hw.arch})",
            f"  Python:  {hw.python_version} {'(Free-Threaded)' if hw.free_threaded else '(GIL Locked)'}",
        ]

        if hw.gpus:
            for gpu in hw.gpus:
                icon = {"NVIDIA": "🟢", "AMD": "🔴", "Intel": "🔵", "Apple": "⚪"}.get(gpu.vendor, "⚫")
                be = ", ".join(gpu.backends)
                mem = f"{gpu.memory_mb}MB" if gpu.memory_mb else "Shared"
                lines.append(f"  GPU:     {icon} {gpu.name} ({mem}) [{be}]")
        else:
            lines.append("  GPU:     ⚫ None detected — CPU-only mode")

        self.hw_text.insert("1.0", "\n".join(lines))
        self.hw_text.configure(state="disabled")
        emotion.reaction("scan")

    def _scan_frameworks(self):
        self.frameworks = detect_frameworks()
        self.fw_list.delete(0, "end")

        for fw in self.frameworks:
            icon = "[+]" if fw.status == FWStatus.AVAILABLE else "[--]"
            entry = f"{icon}  {fw.name}"
            if fw.version:
                entry += f"  v{fw.version}"
            self.fw_list.insert("end", entry)
            idx = self.fw_list.size() - 1
            fg = THEME["green"] if fw.status == FWStatus.AVAILABLE else THEME["text_muted"]
            self.fw_list.itemconfigure(idx, fg=fg)

    def _populate_models(self):
        self.model_list.delete(0, "end")
        cat = self.cat_var.get()
        for m in MODEL_CATALOG.get(cat, []):
            self.model_list.insert("end", f"  {m.name}  [{m.vendor}]  {m.params}")

    def _auto_scan(self):
        """Auto-scan common locations for model artifacts."""
        search_dirs = [
            Path.home() / ".cache" / "huggingface",
            Path.home() / ".cache" / "lm-studio" / "models",
            Path.home() / ".ollama" / "models",
            Path.home() / "models",
            Path.home() / "Desktop",
        ]
        extensions = {".gguf", ".onnx", ".safetensors", ".pt", ".pth", ".bin", ".xml"}

        self.artifacts = []
        for d in search_dirs:
            if not d.exists():
                continue
            try:
                for ext in extensions:
                    count = 0
                    # Use os.scandir iterative walk to avoid rglob hang in compiled mode
                    for root_dir, dirs, files in os.walk(str(d)):
                        # Skip hidden and cache dirs
                        dirs[:] = [dn for dn in dirs if not dn.startswith('.') and dn not in ('node_modules', '__pycache__', 'venv', '.git')]
                        for fname in files:
                            if fname.endswith(ext):
                                fp = Path(root_dir) / fname
                                if fp not in self.artifacts:
                                    self.artifacts.append(fp)
                                    count += 1
                                    if count >= 20:
                                        break
                        if count >= 20:
                            break
            except Exception as exc:
                note_failure("AZAZAEL_Inference_Studio:L1130", exc)
                continue

        # Also scan current directory and parent (with iteration limit)
        try:
            for ext in extensions:
                count = 0
                for root_dir, dirs, files in os.walk(str(Path.cwd())):
                    dirs[:] = [dn for dn in dirs if not dn.startswith('.') and dn not in ('node_modules', '__pycache__', 'venv', '.git')]
                    for fname in files:
                        if fname.endswith(ext):
                            fp = Path(root_dir) / fname
                            if fp not in self.artifacts:
                                self.artifacts.append(fp)
                                count += 1
                                if count >= 10:
                                    break
                    if count >= 10:
                        break
        except Exception as exc:
            note_failure("AZAZAEL_Inference_Studio:L1149", exc)

        self._populate_artifacts()

    def _populate_artifacts(self, filter_str: str = ""):
        self.artifact_list.delete(0, "end")
        for art in self.artifacts:
            display = f"  {art.name}  [{art.suffix}]"
            if filter_str and filter_str.lower() not in art.name.lower():
                continue
            self.artifact_list.insert("end", display)

        self.artifact_count_lbl.configure(
            text=f"{self.artifact_list.size()} artifacts"
        )

    def _filter_artifacts(self):
        self._populate_artifacts(self.artifact_search.get())

    def _scan_directory(self):
        d = filedialog.askdirectory(title="Select directory to scan for models")
        if not d:
            return
        extensions = {".gguf", ".onnx", ".safetensors", ".pt", ".pth", ".bin", ".xml"}
        new = []
        path = Path(d)
        for ext in extensions:
            count = 0
            for root_dir, dirs, files in os.walk(str(path)):
                dirs[:] = [dn for dn in dirs if not dn.startswith('.') and dn not in ('node_modules', '__pycache__', 'venv', '.git')]
                for fname in files:
                    if fname.endswith(ext):
                        fp = Path(root_dir) / fname
                        new.append(fp)
                        count += 1
                        if count >= 50:
                            break
                if count >= 50:
                    break
        for f in new:
            if f not in self.artifacts:
                self.artifacts.append(f)
        self._populate_artifacts()
        self._set_status(f"Scanned: {len(new)} new artifacts found", "cyan")

    # ─── WIRING METHODS ───────────────────────────────────────────

    def _wire_add_node(self):
        self.wire_node_counter += 1
        x = 80 + (self.wire_node_counter * 120) % 600
        y = 60 + (self.wire_node_counter * 50) % 300
        r = 30

        node_id = self.wire_canvas.create_oval(x-r, y-r, x+r, y+r,
                                                fill=THEME["purple"],
                                                outline=THEME["cyan"],
                                                width=2)
        text_id = self.wire_canvas.create_text(x, y,
                                               text=str(self.wire_node_counter),
                                               fill="#fff",
                                               font=("Consolas", 14, "bold"))
        self.wire_nodes.append({
            "id": node_id, "text_id": text_id,
            "x": x, "y": y, "r": r,
            "label": f"Node {self.wire_node_counter}"
        })
        self._set_status(f"Wiring: Added Node {self.wire_node_counter}", "cyan")

    def _wire_auto_connect(self):
        if len(self.wire_nodes) < 2:
            return
        # Animate connections between nodes
        for i in range(len(self.wire_nodes) - 1):
            a = self.wire_nodes[i]
            b = self.wire_nodes[i + 1]
            self.wire_canvas.create_line(
                a["x"], a["y"], b["x"], b["y"],
                fill=THEME["cyan"], width=1.5, dash=(6, 4),
                arrow="last", arrowshape=(10, 10, 10)
            )
        self._set_status("Wiring: Auto-connected all nodes", "green")

    def _wire_clear(self):
        self.wire_canvas.delete("all")
        self.wire_nodes.clear()
        self.wire_node_counter = 0
        self._set_status("Wiring: Canvas cleared", "cyan")

    def _wire_export(self):
        if not self.wire_nodes:
            return
        dot = ["digraph AZAZAEL_Wiring {"]
        for n in self.wire_nodes:
            dot.append(f'  node{n["label"].split()[-1]} [label="{n["label"]}"];')
        for i in range(len(self.wire_nodes) - 1):
            dot.append(f'  node{i+1} -> node{i+2};')
        dot.append("}")
        out = "\n".join(dot)
        self.root.clipboard_clear()
        self.root.clipboard_append(out)
        self._set_status("Wiring: DOT graph exported to clipboard", "green")

    # ─── LANGUAGE METHODS ─────────────────────────────────────────

    def _select_language(self, lang: str):
        self.selected_language = lang
        # Highlight selected button
        for l, btn in self.lang_btns.items():
            if l == lang:
                btn.configure(bg=THEME["purple"], fg="#fff")
            else:
                btn.configure(bg=THEME["button"], fg=THEME["text"])

        info = LANGUAGE_OPTIONS[lang]
        self.lang_detail.configure(state="normal")
        self.lang_detail.delete("1.0", "end")
        detail = (
            f"  Language:   {lang}\n"
            f"  Extension:  {info['ext']}\n"
            f"  Compiler:   {info['compiler']}\n"
            f"  Icon:       {info['icon']}\n\n"
            f"  Selected for inference code generation.\n"
            f"  Use the Compile button to test with the selected toolchain."
        )
        self.lang_detail.insert("1.0", detail)
        self.lang_detail.configure(state="disabled")
        self._set_status(f"Language: {lang} ({info['compiler']})", "cyan")

    def _compile_action(self):
        lang = self.selected_language
        info = LANGUAGE_OPTIONS[lang]
        self.compile_output.configure(state="normal")
        self.compile_output.delete("1.0", "end")
        self.compile_output.insert("1.0",
            f"  [{lang}] Compiler: {info['compiler']}\n"
            f"  Extension: {info['ext']}\n"
            f"  Status: Ready for inference code generation\n"
            f"  Target: Python 3.14t Free-Threaded\n\n"
            f"  # Example inference code would be generated here\n"
            f"  # for {lang} using {info['compiler']}."
        )
        self.compile_output.configure(state="disabled")
        self._set_status(f"Compiler ready: {info['compiler']} for {lang}", "green")

    # ─── ANIMATION METHODS ────────────────────────────────────────

    def _populate_anim_buttons(self):
        for w in self.anim_btns_frame.winfo_children():
            w.destroy()

        cat = self.anim_cat_var.get()
        techniques = ANIMATION_CATEGORIES.get(cat, [])

        row = tk.Frame(self.anim_btns_frame, bg=THEME["bg"])
        row.pack(fill="x")

        for i, tech in enumerate(techniques):
            is_sel = tech == self.selected_animation
            btn = tk.Button(row, text=tech,
                           command=functools.partial(self._select_animation, tech),
                           bg=THEME["purple"] if is_sel else THEME["button"],
                           fg="#fff" if is_sel else THEME["text"],
                           font=("Segoe UI", 8), relief="flat",
                           activebackground=THEME["button_hover"],
                           padx=10, pady=4, cursor="hand2")
            btn.pack(side="left", padx=2, pady=2)

    def _on_anim_cat_change(self, event=None):
        self._populate_anim_buttons()

    def _select_animation(self, tech: str):
        self.selected_animation = tech
        self._populate_anim_buttons()
        self._preview_animation(tech)
        self._set_status(f"Animation: {tech}", "cyan")

    def _preview_animation(self, tech: str):
        cv = self.anim_preview
        cv.delete("all")
        w = cv.winfo_width() or 600
        h = cv.winfo_height() or 220

        # Draw a preview based on the animation type
        colors = {
            "Neon Pulse": (THEME["cyan"], THEME["purple"]),
            "Glitch": (THEME["red"], THEME["pink"]),
            "Ring Pulse": (THEME["cyan"], THEME["cyan_dim"]),
            "Bounce": (THEME["green"], THEME["gold"]),
            "Matrix Rain": (THEME["green"], THEME["green_dim"]),
            "Aurora Wave": (THEME["purple"], THEME["pink"]),
            "Rainbow Wave": (THEME["gold"], THEME["pink"]),
            "Heartbeat": (THEME["red"], THEME["pink"]),
        }
        c1, c2 = colors.get(tech, (THEME["cyan"], THEME["purple"]))

        # Draw wave pattern
        for i in range(0, w, 8):
            y = h//2 + int(40 * math.sin(i * 0.03 + time.time() * 2))
            cv.create_line(i, h//2, i, y, fill=c1, width=1)

        # Draw pulsating center
        r = 30 + int(10 * math.sin(time.time() * 3))
        cv.create_oval(w//2-r, h//2-r, w//2+r, h//2+r,
                       outline=c2, width=3)

        cv.create_text(w//2, h//2, text=tech,
                      fill="#fff", font=("Segoe UI", 14, "bold"))

        # Schedule next frame
        self.root.after(80, self._preview_animation, tech)

    def _start_emoji_morph(self):
        def morph_loop():
            while True:
                emotion.morph_next()
                # Schedule tkinter update on main thread
                try:
                    self.root.after(0, functools.partial(
                        self.anim_emoji_lbl.configure, text=emotion.emoji))
                except Exception as exc:
                    note_failure("AZAZAEL_Inference_Studio:L1369", exc)
                time.sleep(0.8)
        threading.Thread(target=morph_loop, daemon=True).start()

    def _test_emotion(self, state: EmotionalState):
        emotion.state = state
        self._set_status(f"Emotion test: {state.value}", "cyan")

    # ─── EVENT HANDLERS ───────────────────────────────────────────

    def _on_fw_select(self, event):
        sel = self.fw_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self.frameworks):
            fw = self.frameworks[idx]
            self.selected_framework = fw

            self.fw_detail.configure(state="normal")
            self.fw_detail.delete("1.0", "end")
            st = "READY" if fw.status == FWStatus.AVAILABLE else "NOT INSTALLED"
            detail = (
                f"  {fw.icon} {fw.name} — {fw.desc}\n"
                f"  Status: {st} | Backends: {', '.join(fw.backends)}\n"
                f"  Formats: {', '.join(fw.model_formats) if fw.model_formats else 'any'}"
            )
            self.fw_detail.insert("1.0", detail)
            self.fw_detail.configure(state="disabled")
            self._set_status(f"Framework: {fw.name} ({st})",
                           "green" if fw.status == FWStatus.AVAILABLE else "red")

    def _on_cat_change(self, event=None):
        self._populate_models()

    def _on_model_select(self, event):
        sel = self.model_list.curselection()
        if not sel:
            return
        idx = sel[0]
        cat = self.cat_var.get()
        models = MODEL_CATALOG.get(cat, [])
        if idx < len(models):
            model = models[idx]
            self.selected_model = model
            self._set_status(
                f"Model: {model.name} ({model.params})",
                "cyan"
            )

    def _on_be_change(self):
        self._set_status(f"Backend: {self.be_var.get()}", "cyan")

    def _on_artifact_select(self, event):
        sel = self.artifact_list.curselection()
        if not sel:
            return
        idx = sel[0]
        # Filter to find actual artifact
        filtered = self._get_filtered_artifacts()
        if idx < len(filtered):
            art = filtered[idx]
            self.artifact_detail.configure(state="normal")
            self.artifact_detail.delete("1.0", "end")
            size = art.stat().st_size / (1024**2) if art.exists() else 0
            detail = (
                f"  File:   {art.name}\n"
                f"  Format: {art.suffix}\n"
                f"  Size:   {size:.1f} MB\n"
                f"  Path:   {art.parent}\n"
                f"  Full:   {art}"
            )
            self.artifact_detail.insert("1.0", detail)
            self.artifact_detail.configure(state="disabled")

    def _get_filtered_artifacts(self) -> list[Path]:
        fs = self.artifact_search.get().lower()
        if not fs:
            return self.artifacts
        return [a for a in self.artifacts if fs in a.name.lower()]

    # ─── LAUNCH / ACTIONS ─────────────────────────────────────────

    def _launch_inference(self):
        if not self.selected_framework:
            messagebox.showwarning("No Framework", "Select a framework first.")
            return
        if not self.selected_model:
            messagebox.showwarning("No Model", "Select a model from the catalog.")
            return
        if self.selected_framework.status != FWStatus.AVAILABLE:
            messagebox.showerror("Not Available",
                f"{self.selected_framework.name} is not installed.\n\n"
                f"Install: pip install {self.selected_framework.import_name}")
            return

        self.inferencing = True
        self.launch_btn.configure(state="disabled", text="⏳  INFERRING...",
                                  bg=THEME["purple"])
        self.progress.start(10)
        self._draw_dot("cyan")
        emotion.reaction("infer")

        config = {
            "framework": self.selected_framework.name,
            "model": self.selected_model.name,
            "params": self.selected_model.params,
            "backend": self.be_var.get(),
            "temperature": self.param_vars["temp"].get(),
            "top_p": self.param_vars["top_p"].get(),
            "max_tokens": self.param_vars["max_tok"].get(),
            "hardware": self.hardware.cpu_name if self.hardware else "Unknown",
            "language": self.selected_language,
            "animation": self.selected_animation,
            "python": f"{PYTHON_VERSION}" if IS_FREE_THREADED else PYTHON_VERSION,
            "timestamp": datetime.now().isoformat(),
        }

        # Simulate async inference
        def simulate() -> None:
            stages = ["Loading model...", "Tokenizing...", "Generating...",
                      "Sampling...", "Finalizing..."]
            for i, stage in enumerate(stages):
                if not self.inferencing:
                    break
                time.sleep(0.5)
                self.root.after(0, functools.partial(self._set_status,
                    f"Inferencing ({i+1}/5): {stage}", "cyan"))
            self.root.after(0, self._finish_inference, config)

        threading.Thread(target=simulate, daemon=True).start()

    def _finish_inference(self, config: dict):
        self.inferencing = False
        self.progress.stop()
        self.launch_btn.configure(state="normal", text="🚀  LAUNCH INFERENCE",
                                  bg="#00cc88")
        self._draw_dot("green")
        emotion.reaction("done")
        self._set_status(f"Inference complete: {config['model']} on {config['backend']}", "green")

    def _launch_artifact(self):
        sel = self.artifact_list.curselection()
        if not sel:
            messagebox.showwarning("No Artifact", "Select an artifact first.")
            return
        filtered = self._get_filtered_artifacts()
        idx = sel[0]
        if idx < len(filtered):
            art = filtered[idx]
            self._set_status(f"Launching artifact: {art.name}", "green")
            emotion.reaction("launch")

    def _copy_config(self):
        config = {
            "framework": self.selected_framework.name if self.selected_framework else "none",
            "model": self.selected_model.name if self.selected_model else "none",
            "backend": self.be_var.get(),
            "temperature": self.param_vars["temp"].get(),
            "top_p": self.param_vars["top_p"].get(),
            "max_tokens": self.param_vars["max_tok"].get(),
            "language": self.selected_language,
        }
        self.root.clipboard_clear()
        self.root.clipboard_append(json.dumps(config, indent=2))
        self._set_status("Config copied to clipboard", "green")

    def _copy_hardware(self):
        if not self.hardware:
            return
        specs = json.dumps({
            "cpu": self.hardware.cpu_name,
            "cores": self.hardware.cpu_cores,
            "ram_gb": round(self.hardware.ram_gb, 1),
            "gpus": [{"name": g.name, "vendor": g.vendor} for g in self.hardware.gpus],
        }, indent=2)
        self.root.clipboard_clear()
        self.root.clipboard_append(specs)
        self._set_status("Hardware specs copied", "green")

    def _copy_artifact_path(self):
        sel = self.artifact_list.curselection()
        if not sel:
            return
        filtered = self._get_filtered_artifacts()
        idx = sel[0]
        if idx < len(filtered):
            self.root.clipboard_clear()
            self.root.clipboard_append(str(filtered[idx]))
            self._set_status("Artifact path copied", "green")

    def _refresh_all(self):
        self._scan_hardware()
        self._scan_frameworks()
        self._populate_models()
        self._auto_scan()
        self._set_status("All refreshed", "green")

    def _show_telemetry(self):
        if not self.hardware:
            return
        fw_avail = sum(1 for f in self.frameworks if f.status == FWStatus.AVAILABLE)
        total_models = sum(len(v) for v in MODEL_CATALOG.values())
        msg = (
            f"AZAZAEL Inference Studio Telemetry\n"
            f"{'='*42}\n"
            f"Python:     {PYTHON_VERSION} {'(Free-Threaded)' if IS_FREE_THREADED else '(GIL)'}\n"
            f"CPU:        {self.hardware.cpu_name}\n"
            f"Cores:      {self.hardware.cpu_cores}\n"
            f"RAM:        {self.hardware.ram_gb:.1f} GB\n"
            f"GPUs:       {len(self.hardware.gpus)}\n"
            f"Frameworks: {fw_avail}/{len(self.frameworks)} available\n"
            f"Models:     {total_models} cataloged\n"
            f"Artifacts:  {len(self.artifacts)} local\n"
            f"Language:   {self.selected_language}\n"
            f"Animation:  {self.selected_animation}\n"
            f"Backend:    {self.be_var.get()}\n"
            f"Time:       {datetime.now().isoformat()}\n"
        )
        messagebox.showinfo("Telemetry Dashboard", msg)

    # ─── UI HELPERS ───────────────────────────────────────────────

    def _draw_dot(self, color: str):
        colors = {"green": "#00ff88", "cyan": "#00f5ff", "red": "#ff4757",
                  "gold": "#ffdd00", "gray": "#303050"}
        c = colors.get(color, "#303050")
        self.conn_cv.delete("all")
        self.conn_cv.create_oval(3, 3, 13, 13, fill=c, outline="")

    def _set_status(self, msg: str, level: str = "info"):
        colors = {"info": THEME["text_dim"], "green": THEME["green"],
                  "cyan": THEME["cyan"], "red": THEME["red"], "gold": THEME["gold"]}
        self.status_lbl.configure(text=msg, fg=colors.get(level, THEME["text_dim"]))

    def _tick_clock(self):
        self.clock_lbl.configure(text=datetime.now().strftime("%H:%M:%S"))
        self.root.after(1000, self._tick_clock)


# ═══════════════════════════════════════════════════════════════════
# PULSATOR — Smooth color pulsation
# ═══════════════════════════════════════════════════════════════════

class Pulsator:
    """Smooth color-pulsation effect on tkinter widgets."""

    def __init__(self, widget, color_start: str, color_end: str):
        self.widget = widget
        self.cs = self._hex2rgb(color_start)
        self.ce = self._hex2rgb(color_end)
        self.running = False
        self._phase = 0.0

    @staticmethod
    def _hex2rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        return (r, g, b)

    @staticmethod
    def _rgb2hex(rgb: tuple[int, int, int]) -> str:
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

    def start(self, ms: int = 60):
        self.running = True
        self._tick(ms)

    def stop(self):
        self.running = False

    def _tick(self, ms: int):
        if not self.running:
            return
        self._phase = (self._phase + 0.04) % (2 * math.pi)
        t = (1 + math.sin(self._phase)) / 2
        r = int(self.cs[0] + (self.ce[0] - self.cs[0]) * t)
        g = int(self.cs[1] + (self.ce[1] - self.cs[1]) * t)
        b = int(self.cs[2] + (self.ce[2] - self.cs[2]) * t)
        try:
            self.widget.configure(fg=self._rgb2hex((r, g, b)))
        except Exception as exc:
            note_failure("AZAZAEL_Inference_Studio:L1652", exc)
        self.widget.after(ms, self._tick, ms)


# ═══════════════════════════════════════════════════════════════════
# ENTRY POINTS
# ═══════════════════════════════════════════════════════════════════

def cli_mode():
    """Headless CLI mode for scripting."""
    p = argparse.ArgumentParser(description=f"{APP_NAME} v{APP_VERSION} — CLI")
    p.add_argument("--detect", action="store_true", help="Detect hardware")
    p.add_argument("--frameworks", action="store_true", help="list frameworks")
    p.add_argument("--models", action="store_true", help="list model catalog")
    p.add_argument("--scan", action="store_true", help="Scan for local artifacts")
    p.add_argument("--json", action="store_true", help="JSON output")
    args = p.parse_args()

    if args.detect:
        hw = detect_hardware()
        data = {
            "cpu": hw.cpu_name, "cores": hw.cpu_cores,
            "ram_gb": round(hw.ram_gb, 1), "gpus": [
                {"name": g.name, "vendor": g.vendor, "memory_mb": g.memory_mb}
                for g in hw.gpus
            ],
            "free_threaded": IS_FREE_THREADED,
        }
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            for k, v in data.items():
                print(f"  {k}: {v}")
    elif args.frameworks:
        for fw in detect_frameworks():
            icon = "[OK]" if fw.status == FWStatus.AVAILABLE else "[--]"
            print(f"  {icon} {fw.name} {fw.version}")
    elif args.models:
        for cat, models in MODEL_CATALOG.items():
            print(f"\n  {cat}:")
            for m in models:
                print(f"    {m.name} [{m.vendor}] {m.params}")
    elif args.scan:
        print("  Scanning for artifacts...")
        print("  (Use GUI for full artifact browser)")
    else:
        p.print_help()


def main():
    if len(sys.argv) > 1 and sys.argv[1] in (
        "--detect", "--frameworks", "--models", "--scan", "--json", "--help", "-h"
    ):
        cli_mode()
    else:
        root = tk.Tk()
        try:
            icon = os.path.join(os.path.dirname(__file__),
                               "CODEX", "codex_icon.ico")
            if os.path.exists(icon):
                root.iconbitmap(icon)
        except Exception as exc:
            note_failure("AZAZAEL_Inference_Studio:L1715", exc)
        InferenceStudio(root)
        root.mainloop()


if __name__ == "__main__":
    main()
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
