# Azazael Ecosystem: Dual-Pane Reference 🎯

## PANE A: Quick Reference

```
╔═══════════════════════════════════════════════════════════════════╗
║            🔮 AZAAZEL v7.0 - Distributed Computing                ║
╠═══════════════════════════════════════════════════════════════════╣
║  📦 TIER 0: SHORTY 🔗 (shortcuts), TARBABY 📦 (build)            ║
║  ⚙️ TIER 1: SYSTEMZ 🖥️, PROCESSZ ⚙️, DISKZ 💾, NETWORXZ 🌐,     ║
║             CLOCKZ ⏰, WORMZ 🐛                                   ║
║  🔗 TIER 2: BIBL 📚, SYSTEM 🖥️, NETWORK 🌐, SECURITY 🔒,         ║
║             ANALYSIS 📊, VALIDATION ✅, HEALTH 💚                  ║
║  🧠 TIER 3: METAZ 📝, DEPENDZ 🔗, VALIDZ ✅, DEADZ 💀,           ║
║             METRICZ 📈, ERRORZ 🚨, LOGZ 📋, CHANGEZ 🔄,           ║
║             TRIMZ ✂️, OPTIMIZ ⚡, HEALTHZ 🩺                       ║
║  🤖 TIER 4: HYDRAMETA 🧬 (ML Optimization, GA, RL)              ║
║  🎭 TIER 5: TERENCE 🎯 (orchestration), HORUS 📺 (output)       ║
╠═══════════════════════════════════════════════════════════════════╣
║  🔄 6-STAGE PIPELINE:                                             ║
║  [🔍 DISCOVERY] → [📊 ANALYSIS] → [⚡ OPTIMIZE] → [✅ VALIDATE]  ║
║               → [🚀 DEPLOY] → [📈 FEEDBACK]                       ║
╠═══════════════════════════════════════════════════════════════════╣
║  🔗 CLUSTERS: BIBL 📚, SYSTEM 🖥️, NETWORK 🌐, SECURITY 🔒,      ║
║              ANALYSIS 📊, VALIDATION ✅, HEALTH 💚                ║
╠═══════════════════════════════════════════════════════════════════╣
║  🗄️ DATABASES: SQLite 🪶, Redis ⚡, PostgreSQL 🐘,               ║
║                ElasticSearch 🔍, InfluxDB 📊                      ║
║  🌐 APIs: REST + WebSocket 🔌                                    ║
║  ⚡ EXECUTION: Parallel, Thread Pool, ML-Optimized, GA 🧬       ║
╚═══════════════════════════════════════════════════════════════════╝
```

---

## PANE B: Detailed Reference

### 🖥️ CLI Commands
```bash
🔮 azazael --help                          # Show commands
🔮 azazael scan --target /path/to/code     # Scan directory
🔮 azazael analyze --metaworms all         # Run metaworms
🔮 azazael optimize --population 100       # GA optimization
🔮 azazael monitor --dashboard             # Start dashboard
🔮 azazael deploy --cluster all            # Deploy clusters
🔮 azazael history --metrics               # Show metrics
```

### 🌐 API Endpoints
```http
GET  /api/v1/system/status              # 🩺 Health
GET  /api/v1/system/resources           # 📊 Resources
POST /api/v1/metaz/scan                 # 🔍 Start scan
GET  /api/v1/hydrameta/population      # 🧬 GA status
POST /api/v1/terence/analyze           # 🎯 Error analysis
POST /api/v1/terence/fix               # 🔧 Apply fix
GET  /api/v1/horus/dashboard           # 📺 Dashboard
```

### 🐛 Metaworm Functions
| Worm | Function | Icon |
|------|----------|------|
| METAZ | Metadata extraction | 📝 |
| DEPENDZ | Dependency mapping | 🔗 |
| VALIDZ | Syntax validation | ✅ |
| DEADZ | Dead code detection | 💀 |
| METRICZ | Complexity metrics | 📈 |
| ERRORZ | Error pattern matching | 🚨 |
| LOGZ | Log analysis | 📋 |
| CHANGEZ | Change tracking | 🔄 |
| TRIMZ | Content optimization | ✂️ |
| OPTIMIZ | Performance tuning | ⚡ |
| HEALTHZ | System diagnostics | 🩺 |

### 🧬 GA Parameters
| Parameter | Range |
|-----------|-------|
| Population | 100-1000 👥 |
| Selection | Tournament 🎰, Roulette |
| Crossover | Single ✂️, Two-point |
| Mutation | Bit Flip 🔀, Swap |

### ⚡ Execution Strategies
1. 🔄 Round Robin (Sequential)
2. 🚀 Performance (Fastest First)
3. ⚖️ Resource-Aware (Load Balanced)
4. 🎲 Randomized (Chaos Mode)
5. 🤖 ML-Optimized (Predicted Best)

### 🎯 TERENCE Capabilities
- 🗂️ 50K+ pattern database
- 🧠 Deep Q-Learning agent
- 📊 Directed Acyclic causal graphs
- ✅ 90% auto-fix success rate

### 📺 HORUS Outputs
- 🎬 24 animation protocols
- 📄 HTML dashboards, JSON 📋, GraphML
- 📊 Grafana integration
- 🔌 WebSocket real-time

---

## PANE C: Architecture Summary

```
                              🎯 TERENCE
                         ┌─────────────────┐
                         │   Orchestration │
                         │   50K patterns  │
                         │   DQN agent 🧠  │
                         └────────┬────────┘
                                  │
     ┌────────────────────────────┼────────────────────────────┐
     │                            │                            │
     ▼                            ▼                            ▼
┌─────────┐              ┌─────────┐              ┌─────────┐
│  HORUS  │              │HYDRAMETA│              │METAWORMS│
│   📺    │              │   🧬    │              │   🧠    │
│  Output │              │  ML + GA│              │ METAZ-  │
│         │              │         │              │HEALTHZ  │
└────┬────┘              └────┬────┘              └────┬────┘
     │                        │                         │
     │     ┌──────────────────┼──────────────────┐      │
     │     │                  │                  │      │
     ▼     ▼                  ▼                  ▼      ▼
┌─────────────────────────────────────────────────────────────┐
│                     🔗 CLUSTERS (Tier 2)                    │
│  📚 BIBL   🖥️ SYSTEM   🌐 NETWORK   🔒 SECURITY            │
│  📊 ANALYSIS   ✅ VALIDATION   💚 HEALTH                    │
└─────────────────────────────────────────────────────────────┘
                              │
     ┌────────────────────────┼────────────────────────┐
     │                        │                        │
     ▼                        ▼                        ▼
┌─────────────────────────────────────────────────────────────┐
│                  ⚙️ CORE WORMS (Tier 1)                    │
│   🖥️ SYSTEMZ   ⚙️ PROCESSZ   💾 DISKZ   🌐 NETWORXZ       │
│   ⏰ CLOCKZ   🐛 WORMZ                                    │
└─────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
                    ▼                   ▼
          ┌─────────────────┐ ┌─────────────────┐
          │    SHORTY 🔗    │ │   TARBABY 📦    │
          │   (Tier 0)      │ │   (Tier 0)      │
          │   Shortcuts 🔗  │ │   Build/Pkg 📦  │
          └─────────────────┘ └─────────────────┘
```

---

## PANE D: Portability Quick Reference

### 📱 F:\Azazael_PORTABLE Structure
```
📂 Azazael_PORTABLE/
├── 📂 CORE/
│   └── 🐛 WORMZ.py              # Core crawlers
├── 📂 system/
│   ├── 🏗️ architecture.py        # System manager
│   ├── 🚀 launcher.py           # Main entry
│   ├── ⚡ spawn_manager.py      # Extendable spawn
│   └── 💾 persistence.py        # State saving
├── 📂 boot/
│   ├── 🔧 make_bootable.bat     # USB boot maker
│   └── ⚙️ syslinux.cfg         # Boot config
├── ⚙️ config/system_config.json
├── ▶️ autorun.bat, launcher.bat
└── 📖 README.md
```

### ⚡ Spawn Sub-Functions
| Function | Purpose | Icon |
|----------|---------|------|
| `spawn_crawler(name)` | Ecosystem crawlers | 🔍 |
| `spawn_protocol(name)` | Protocol handlers | 📜 |
| `spawn_ml_worker(task, model)` | ML training/inference | 🤖 |
| `spawn_distributed(n, backend)` | Parallel workers | ⚡ |
| `spawn_hyperopt(obj, max)` | Hyperparameter tuning | 🎯 |
| `spawn_qlearner(episodes, env)` | Q-learning agents | 🧠 |
| `spawn_ensemble(models, method)` | Model ensembling | 🎭 |

### 🖥️ Usage
```cmd
F:\Azazael_PORTABLE> 🚀 launcher.bat status
F:\Azazael_PORTABLE> 🩺 launcher.bat health
F:\Azazael_PORTABLE> 🔍 launcher.py spawn system --type crawler
```

---

## 🎨 Color Legend

| Color | Meaning |
|-------|---------|
| 🔮 Purple | Azazael core branding |
| 🟢 Green | Health, Success, Validation |
| 🔵 Blue | System, Infrastructure |
| 🟡 Yellow | Warnings, Alerts |
| 🔴 Red | Errors, Dead code |
| 🟣 Magenta | ML, Optimization |
| 🟠 Orange | Performance, Timing |
| ⚪ Gray | Infrastructure |

---

*📄 Document Version: 1.1.0 - Emoji Enhanced*
*📖 See Azazael_ESSAY.md for complete documentation*
