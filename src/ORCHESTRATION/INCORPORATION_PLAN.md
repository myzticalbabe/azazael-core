---
title: Azazael Ecosystem — Cross-Project Incorporation Plan
version: 7.0.0
date: 2026-05-27
status: PRODUCTION PATH DEFINED
---

# Azazael Ecosystem — Cross-Project Incorporation Plan

## Production of Native Intelligence Artifacts via Local Inferencing

---

## 1. Project Inventory — What Exists Where

### Core Ecosystem (C:\Users\clint\azazael\)

| Layer | Directory | Contents |
|-------|-----------|----------|
| Worms | `src/CORE/WORMS/` | SYSTEMZ, PROCESSZ, DISKZ, NETWORXZ, CLOCKZ, WORMZ, CONCURRENCY, CLUSTERWORMS, OS_CONGRUENCY |
| Metaworms | `src/META/` | ERRORZ, HEALTHZ, TRIMZ, HYDRAMETA, METAWORMS, **MetaWormSpawner (NEW)** |
| Orchestration | `src/ORCHESTRATION/` | HYDRA, HORUS, TARBABY, ECOSYSTEM_OPTIMIZER, ECOSYSTEM_CRAWLER_LAUNCHER |
| Utilities | `src/UTILITIES/` | KLINK, TERENCE, POINDEXTER, PERSEPHONE |
| Config | `src/config/` | worm_definitions.json, morph_worm_state.json, ecosystem_config.yaml |
| Docs | `src/CODEX/` | Documentation, HTML reference, multimedia |
| Shortcuts | `src/SHORTCUTS/` | .bat automation scripts for all operations |

### LLM Inference Stack

| Component | Location | Status |
|-----------|----------|--------|
| llama-server | `C:\Users\clint\temp_llama_cpp_build\llama.cpp\build_server_multi\bin\Release\llama-server.exe` | ✅ Binary exists |
| Models | `C:\Users\clint\llama-server\models\` | 8 models including gemma-4-E2B, Qwen2.5, InternVL3 |
| Unified Router | `C:\Users\clint\llama-server\unified\router.py` | ✅ |
| Multiserve | `C:\Users\clint\llama-server\unified\multiserve.py` | ✅ Free-threaded capable |
| Launcher | `C:\Users\clint\Desktop\Llama Server\launch_chat.bat` | ✅ FIXED |
| Open Interpreter | `C:\Users\clint\Desktop\Llama Server\launch_interpreter.py` | ✅ |

### Memory & Persistence

| Component | Port | Status |
|-----------|------|--------|
| pams-server | 21434 | ✅ Running (Python wrapper, lifespan=off) |
| DB: conversations | — | 78KB, last mod May 2025 |
| DB: ai_memories | — | 20KB |
| DB: mcp_tool_calls | — | 29KB |

### Execution Engines

| Python | Path | GIL | Use |
|--------|------|-----|-----|
| Python 3.14.5 | `C:\Users\clint\AppData\Local\Programs\Python\Python314\python.exe` | Enabled | General worms, KLINK |
| Python 3.14.3t | `C:\Users\clint\AppData\Roaming\uv\python\cpython-3.14+freethreaded-windows-x86_64-none\python.exe` | **Disabled** | MetaWormSpawner, multiserve |

---

## 2. Production Pipeline — From Monitoring to Intelligence

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PRODUCTION PIPELINE                             │
│                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────────┐  │
│  │ MONITOR  │───▶│  LEARN   │───▶│ INFER    │───▶│  ARTIFACT    │  │
│  │ (Worms)  │    │ (HYDRA)  │    │ (Llama)  │    │  (Output)    │  │
│  └──────────┘    └──────────┘    └──────────┘    └──────────────┘  │
│       │               │               │                │           │
│  SYSTEMZ         HYDRAMETA      llama-server      HORUS formatted  │
│  PROCESSZ        MetaWorm       gemma-4-E2B       BIBL indexed     │
│  DISKZ           Spawner        Open Interp.     POINDEXTER math   │
│  NETWORXZ        HYDRA          multiserve       PAMS persisted    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Stage 1: MONITOR — Worm Fleet

The worm ecosystem continuously monitors the system:

- **SYSTEMZ**: CPU, memory, disk utilization
- **PROCESSZ**: Process tree, resource hogs
- **DISKZ**: I/O patterns, storage health
- **NETWORXZ**: Network traffic, interface states
- **CLOCKZ**: Temporal patterns, scheduling

**Entry point:** `klink worms` or `python -m MetaWormSpawner --start`

### Stage 2: LEARN — MetaWorm Adaptation

MetaWormSpawner with free-threaded Python 3.14t:

- Spawns worm sub-agents dynamically
- Morphs behavior based on system state (exploratory → aggressive → defensive → recovery)
- Evaluates patterns and adapts spawning policies
- Feeds structured telemetry to HYDRA cluster

**Entry point:** `python src/META/MetaWormSpawner.py --demo`

### Stage 3: INFER — Local LLM Processing

llama-server + Open Interpreter:

- Gemma-4-E2B for fast inference on E2B hardware
- Qwen2.5-1.5B for specialized tasks
- InternVL3-8B for vision/multimodal
- Open Interpreter for code generation
- Router automatically selects optimal model

**Entry point:** `C:\Users\clint\Desktop\Llama Server\launch_chat.bat`

### Stage 4: ARTIFACT — Intelligence Output

Results formatted and persisted:

- **HORUS**: Human-readable output formatting
- **BIBL**: Hyperlinked lexicon indexing (432K+ terms from 449 files)
- **POINDEXTER**: Mathematical indexing and analysis
- **PAMS**: Persistent memory across sessions
- **KLINK**: Unified command interface to all components

---

## 3. Cross-Project Communication Matrix

```
MetaWormSpawner ────▶ WORMZ (BIBL crawling)
       │
       ├────────────▶ HYDRA (cluster orchestration)
       │                    │
       │                    └──────▶ llama-server (inference)
       │                                   │
       │                                   └──────▶ pams (memory)
       │
       ├────────────▶ TERENCE (error resolution)
       │
       └────────────▶ KLINK (CLI launcher)
```

**Data flow:**
1. Worms collect system telemetry → MetaWormSpawner aggregates
2. MetaWormSpawner evaluates patterns → spawns/morphs worms
3. HYDRA cluster processes structured data → feeds llama-server
4. llama-server generates intelligence → pams persists
5. HORUS formats output → BIBL indexes for retrieval
6. KLINK provides unified CLI to all components

---

## 4. Immediate Production Steps

### Step 1: Start Core Services
```batch
REM Start pams-server (memory backend)
C:\temp\start_pams.py          # Already running on 21434

REM Start llama-server (inference)
C:\Users\clint\Desktop\Llama Server\launch_chat.bat
```

### Step 2: Launch Worm Fleet
```bash
cd C:\Users\clint\azazael
python src/UTILITIES/KLINK/klink.py status     # Check ecosystem
python src/UTILITIES/KLINK/klink.py worms      # Run monitoring worms
python src/UTILITIES/KLINK/klink.py hydra all  # Run HYDRA cluster
```

### Step 3: Activate MetaWormSpawner (Free-Threaded)
```bash
C:\Users\clint\AppData\Roaming\uv\python\cpython-3.14+freethreaded-windows-x86_64-none\python.exe src/META/MetaWormSpawner.py --demo --duration 120
```

### Step 4: Generate Intelligence Artifacts
```bash
python src/UTILITIES/KLINK/klink.py optimize   # Ecosystem optimizer
python src/UTILITIES/KLINK/klink.py analyze    # Batch file analysis
python src/UTILITIES/KLINK/klink.py horus      # HORUS output formatting
```

---

## 5. MetaWormSpawner → llama-server Bridge

The critical missing link for full production is the **Spawner-Inference Bridge**:

```python
# Proposed: src/META/InferenceBridge.py
class InferenceBridge:
    """Connects MetaWormSpawner telemetry to llama-server inference."""
    
    def collect_worm_telemetry(self) -> Dict:
        """Gather structured data from active worm fleet."""
        
    def format_inference_prompt(self, telemetry: Dict) -> str:
        """Format telemetry as llama-server prompt."""
        
    def send_to_llama(self, prompt: str) -> str:
        """Send prompt to llama-server, return response."""
        
    def record_intelligence(self, response: str):
        """Store inference result in pams and BIBL."""
```

This bridge would:
1. Take MetaWormSpawner fleet status as input
2. Format it as a structured prompt
3. Send to llama-server (port 18080)
4. Receive intelligence analysis
5. Store in pams and BIBL

---

## 6. Recommended Next Projects

| Priority | Project | Description |
|----------|---------|-------------|
| **P0** | InferenceBridge | Connect MetaWormSpawner → llama-server for real intelligence generation |
| **P0** | Start Services | Get llama-server running on 18080, verify pams recording |
| **P1** | Free-Threaded Fleet | Install psutil for free-threaded Python, run full fleet in parallel |
| **P1** | pams Recording | Verify MCP tools are recording to conversations.db |
| **P2** | Multiserve | Enable multi-model parallel inference via multiserve.py |
| **P2** | CURRENT_STATE.md | Update documentation to reflect actual paths (~/azazael not Desktop) |

---

## 7. Configuration Summary

| Config | Value |
|--------|-------|
| Azazael root | `C:\Users\clint\azazael` |
| pams port | 21434 |
| llama port (target) | 18080 |
| Free-threaded Python | `C:\Users\clint\AppData\Roaming\uv\python\cpython-3.14+freethreaded-windows-x86_64-none\python.exe` |
| Standard Python | `C:\Users\clint\AppData\Local\Programs\Python\Python314\python.exe` |
| Archive (G:) | `G:\archived_files\cleanup_20260527_205935\` |
| Config | `C:\Users\clint\config.yaml` |
| Models | `C:\Users\clint\llama-server\models\` (8 models) |

---

*Generated by Codebuff — May 27, 2026*
