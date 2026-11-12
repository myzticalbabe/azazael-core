# 🌐 Cross-Environment Manifest — Azazael Ecosystem

Generated: July 2, 2026
Purpose: Every environment must know what every other environment contains and can do.

---

## 📍 Environment 1: Windows Desktop (ACTIVE DEVELOPMENT)

**Host:** DESKTOP (Windows 11)  
**Python:** 3.14.6 free-threaded  
**Shell:** Git Bash

### Projects

| Project | Path | Size | Files | Last Modified | Status |
|---------|------|------|-------|---------------|--------|
| **AOA** | `Desktop/AOA/` (MISSING) | - | - | archived 2026-10-21 | archived - lives only in azazael.archived-20261021 (src/AOA, archive/restored/AOA); not an active standalone dev tree on this box |
| **Azazael** | `Users/clint/azazael/` | 50 GB | 5,277 | Active | ✅ Active |
| **Libra** | `Desktop/Libra/` | 1.8 MB | 181 | June 30, 2026 | ✅ Active |
| **EveUnleashed** | `Desktop/EveUnleashed_Selector/` | 23 MB | 172 | Active | ✅ Active |
| **CODEX Studio** | `Users/clint/build/CODEX_Studio_314t/` | 41 MB | 14 | Active | ✅ Active |

### Key Deliverables (AOA)
- `AOA_STRUCTURE.html` — 19.9 KB, 7 Mermaid diagrams, architecture disclosure
- `AOA_Structure_Disclosure.mp4` — 10.8 MB, 1920x1080, 25fps, 60s, H.264
- `benchmark_data.json` — Live system metrics
- `CROSS_ENV_MANIFEST.md` — This file

### Skills
- `.agents/skills/aoa-html-builder.md` — HTML builder workflow, heredoc strategy
- `.agents/skills/aoa-architecture.md` — AOA quartet architecture reference

### Capabilities
- Full AOA orchestration (8 arms, 56 cross-arm routes)
- CODEX video rendering pipeline (HTML→MP4 via Puppeteer/FFmpeg)
- Eve AI model selection and inferencing
- Libra legal document generation
- Azazael worm cluster execution (101 worms, 18 categories)
- DirectMemory event recording and session milestones

---

## 💾 Environment 2: G:\ Drive (ARCHIVE / SOURCE OF TRUTH)

**Drive:** External/Network  
**Total used:** ~50 GB across Azazael dirs

### Directories

| Directory | Size | Purpose |
|-----------|------|---------|
| **G:/AZAZAEL/** | 16 GB | Main archive: wheels, CODEX_VIDEOS, AZAZAEL_ROOT |
| **G:/AZAZAEL_SOURCE_OF_TRUTH/** | 8.9 MB | Core docs, configs, function catalogs, src/CORE/ |
| **G:/AZAZAEL_Standalone/** | 2.9 GB | 39 .exe files + .bat scripts + HTML reports |
| **G:/AZAZAEL_UBUNTU/** | 207 MB | Ubuntu drivers, python-wheels, scripts |
| **G:/AZAZAEL_BOOT/** | 8.9 GB | Boot deps, wheels, AI_PROMPTS |
| **G:/WORKING.HTML.AZAZAEL/** | 7.2 GB | 200+ numbered HTML files (E0001-E0200.html) |
| **G:/Archive/** | 9.0 GB | Old backups (d_drive, e_drive, kilo) |
| **G:/Desktop_Archive/** | 3.2 GB | Desktop archive |
| **G:/D_archive_20260601_233818/** | 3.4 GB | Dated archive |

### Source of Truth Documents
- `SYSTEM_INSTRUCTIONS.md`, `MEMORIES.md`, `ERROR_LOG.md`
- `LESSONS_MASTER.md`, `SKILLS_CATALOG.md`, `SESSION_STARTUP.md`
- `CODEBASE_AUDIT_TODO.md`, `PRODUCTION_TODO.md`
- Function catalogs: `_function_catalog.json`, `_function_proofs.json`

### Capabilities
- Offline backup of all source code and documentation
- Standalone .exe deployment (39 executables)
- Ubuntu bootstrapping (drivers, wheels)
- Historical HTML render archive (200+ files)

---

## 🐧 Environment 3: WSL Ubuntu

**Status:** No native project files  
**Access:** Windows files via `/mnt/c/Users/clint/`

- `/mnt/c/Users/clint/Desktop/AOA` — accessible
- `/mnt/c/Users/clint/azazael/` — accessible
- No separate copies exist

### Capabilities
- Linux-native Python execution
- Can access all Windows project files via /mnt/c/
- Can run Linux-specific builds/tests

---

## 🐉 Environment 4: Kali Linux (WSL)

**Status:** No native project files  
**Access:** Windows files via `/mnt/c/Users/clint/`

- No separate copies exist

### Capabilities
- Security testing tools
- Can access Windows project files via /mnt/c/
- Can run security audits on codebase

---

## 🔍 Architecture Disclosure Audit (July 2, 2026)

**Auditor:** Codebuff via deepseek/deepseek-v4-pro  
**Status:** Previous disclosure (AOA_STRUCTURE.html + MP4) was INACCURATE  
**Corrected:** AOA_STRUCTURE.html has been rebuilt from actual source code

### Key Misrepresentations Found
| Claim | Reality | Source |
|-------|---------|--------|
| 101 worms, 18 categories | 77 worms, 17 categories + expansion | worm_registry.py |
| 56 cross-arm routes | 46 routes, 264 paths | METACLUSTER.py |
| Quartet = Core/Support split | Quartet = VF cross-platform paths + 4 pillars + 8 docs | worm_registry.py:102 |
| Arm capabilities simplified | Actual capabilities verified from arm source files | arms/*.py |

### Critical Omissions (Now Disclosed)
- **DreamingModule** (Tier 6): RECALL→DREAM→EVALUATE→INTEGRATE pipeline (802 lines)
- **MetaCognitionWorm**: OBSERVE→ANALYZE→ADAPT→VALIDATE loop (830 lines)
- **HypnagogicSynthesizer**: Cross-domain pattern blending
- **EvolutionEngine + GeneticOptimizer**: Population optimization + selection/crossover/mutation
- **CAIN Psychoanalysis Framework**: Jungian archetypes, collective unconscious
- **Expansion modules**: race_detector, error_recovery, scheduling, db_monitor
- **Free-threaded concurrency model**: 3.14t GIL-free, CurativePool double-checked locking

### Corrected HTML
- 7 diagrams reflecting actual 3-tier architecture (CLUSTERWORMS → NATURE_CLUSTERS → METACLUSTER)
- DreamingModule pipeline, MetaCognition loop, Neural Advancement suite
- Real Quartet: VF cross-platform paths + 4 pillars + 8 canonical documents
- 17-category worm registry + 5 expansion modules
- CAIN psychoanalysis framework diagram
- Corrections log table documenting every fix

## 🔄 Sync Strategy

### Source of Truth: Windows Desktop
All active development happens on Windows Desktop. G:\ is the archive mirror.

### Sync Direction
```
Windows Desktop ──sync──> G:\AZAZAEL_SOURCE_OF_TRUTH/
Windows Desktop ──sync──> G:\AZAZAEL/
```

### What Gets Synced
1. AOA deliverables (HTML, MP4, JSON, manifest)
2. Azazael source code (`src/`)
3. Azazael skills (`.agents/skills/`)
4. Libra source code
5. EveUnleashed source code

### What Gets Archived
1. Old HTML renders (`G:/WORKING.HTML.AZAZAEL/`)
2. Stale boot dependencies (`G:/AZAZAEL_BOOT/`)
3. Redundant archives

---

## 📋 Quick Reference

| Task | Environment | Command/Action |
|------|-------------|----------------|
| Dev work | Windows Desktop | Edit in `Desktop/AOA/`, `azazael/src/` |
| Sync to G:\ | Windows Desktop | `robocopy source G:/dest /MIR` |
| Linux build | WSL Ubuntu | `cd /mnt/c/Users/clint/azazael && python` |
| Security audit | Kali WSL | `cd /mnt/c/Users/clint/azazael && tool` |
| Deploy .exe | G:\ Standalone | Run from `G:/AZAZAEL_Standalone/` |
| Restore from backup | G:\ Archive | Copy from `G:/Archive/` or `G:/AZAZAEL_SOURCE_OF_TRUTH/` |

---

*Last synced: July 2, 2026 — Gap analysis added*
