#!/usr/bin/env python3

"""
Azazael Reference Video - Double Column Scrolling

Scrolling through the comprehensive markdown documentation

"""



import os

from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from typing import List
import subprocess

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here




WIDTH = 1920

HEIGHT = 1080

FPS = 30

DURATION = 180



OUTPUT_DIR = Path.home() / "Desktop" / "CODEX"

FRAME_DIR = os.path.join(OUTPUT_DIR, "frames_doc")

VIDEO_OUTPUT = os.path.join(OUTPUT_DIR, "Azazael_Reference_Video.mp4")



LEFT_CONTENT = """



                    🚀 Azazael ECOSYSTEM v2.0 





WHAT IS Azazael? 🤔

Azazael is a DISTRIBUTED COMPUTING FRAMEWORK designed from

the ground up to be SELF-OPTIMIZING and SELF-HEALING.

Think of it as an intelligent nervous system for your

infrastructure that never sleeps, constantly monitoring,

analyzing, and improving itself without human intervention.



CORE PHILOSOPHY 💡

SELF-OPTIMIZATION: Through genetic algorithms and machine

learning, Azazael continuously evolves its own parameters.

The HYDRAMETA layer is the brain behind this intelligence.



SELF-HEALING: When failures occur, Azazael doesn't panic.

TERENCE maintains a database of over 50,000 error patterns

and uses Deep Q-Learning to recover and prevent issues.



DISTRIBUTED INTELLIGENCE: Unlike traditional monitoring

tools that simply collect data, Azazael UNDERSTANDS it.

The worm-based architecture allows specialized agents to

focus on specific domains while working together seamlessly.





                     SYSTEM ARCHITECTURE 





TIER 0: FOUNDATION 🛠

SHORTY: Command aliases and shortcuts

TARBABY: Binary builder and packager

PERSEPHONE: Cross-platform deployment



TIER 1: CORE WORMS 

SYSTEMZ: Resource Monitor - CPU, Memory, Disk, Network

PROCESSZ: Process Manager - Tracking, spawning, termination

DISKZ: Storage Monitor - SMART, I/O statistics

NETWORXZ: Network Monitor - Bandwidth, latency, packets

CLOCKZ: Time Service - NTP, PTP, scheduling

WORMZ: Crawler/Indexer - Filesystem, Git, Database



TIER 2: CLUSTERWORMS 🔗

BIBL: Terminology crawling with deduplication

SYSTEM: Complete system monitoring

NETWORK: Infrastructure health analysis

SECURITY: Auditing and intrusion detection

ANALYSIS: Comprehensive code analysis

VALIDATION: Code validation and error detection

HEALTH: System health optimization





                     TIER 1: CORE WORMS 





SYSTEMZ - RESOURCE MONITOR 

SYSTEMZ is the primary eyes of Azazael. Capabilities include:

* CPU Monitoring - Usage, frequency, thermal, scheduling

* Memory Management - Physical/virtual, swap, per-process

* Disk I/O - Throughput, IOPS, queue depth, SMART

* Network Statistics - Bandwidth, connections, latency



PROCESSZ - PROCESS MANAGER 

PROCESSZ keeps track of every process running:

* Process enumeration with tree visualization

* Resource attribution per process

* Remote management via SSH, WMI, REST API

* Container-aware tracking (Kubernetes, Docker)



DISKZ - STORAGE MONITOR 💾

DISZK provides forensic-level storage analysis:

* SMART monitoring with predictive failure

* I/O performance analysis and histograms

* Capacity planning with trend prediction

* Support for HDD, SSD, NVMe, LVM, RAID



NETWORXZ - NETWORK MONITOR 🕸

NETWORXZ monitors every network packet:

* Interface discovery and monitoring

* Connection tracking (TCP, UDP)

* Protocol analysis and packet capture

* QoS configuration and traffic shaping



CLOCKZ - TIME SERVICE 

CLOCKZ ensures perfect time synchronization:

* NTP configuration with multi-pool support

* PTP for sub-microsecond precision

* Cron-like scheduling for automation

* Time quality metrics and monitoring





                    🔗 TIER 2: CLUSTERWORMS 🔗





CLUSTERS represent coordinated groups of Core Worms:



BIBL CLUSTER 📚

Combines WORMZ + TRIMZ for terminology databases

* Crawl all configured sources

* Terminology extraction and hashing

* Deduplication and normalization

* ElasticSearch or SQLite indexing



SYSTEM CLUSTER 🖥

Combines SYSTEMZ + PROCESSZ + DISKZ

* Comprehensive health monitoring

* Resource correlation analysis

* Real-time dashboard visualization

* Alert configuration and thresholds



NETWORK CLUSTER 

Combines NETWORXZ + CLOCKZ + DISKZ

* Infrastructure health analysis

* Latency correlation with storage

* Time-sensitive transaction monitoring

* Network path analysis



SECURITY CLUSTER 🔒

Combines PROCESSZ + NETWORXZ

* Process anomaly detection

* Port scan and intrusion detection

* Lateral movement identification

* Data exfiltration pattern alerts





                    🧬 TIER 3: METAWORMS 🧬





METAZ - METADATA EXTRACTION 📦

* Code metadata - Functions, classes, imports

* Structural metadata - Dependencies, schemas

* Semantic metadata - Business concepts, entities

* API endpoint definitions



DEPENDZ - DEPENDENCY MAPPING 🗺

* Compile-time dependencies (imports, inheritance)

* Runtime dependencies (RPC, plugins)

* Data dependencies (queries, messages)

* Infrastructure dependencies (DNS, load balancers)



METRICZ - CODE METRICS 📊

* Cyclomatic Complexity measurement

* Halstead Metrics (Volume, Difficulty, Effort)

* Maintainability Index (0-100 score)

* Lines of Code, Comment ratios



VALIDZ - SYNTAX VALIDATION [OK]

* Strict mode - All warnings as errors

* Pedantic mode - Style guide enforcement

* Type annotation coverage requirements

* Deprecated API detection



DEADZ - DEAD CODE DETECTION 👻

* Unused imports and variables

* Unreachable code paths

* Obsolete function detection

* Technical debt quantification





                    [-] TIER 4: HYDRAMETA [-]





ML OPTIMIZATION ENGINE 🤖

GENETIC ALGORITHMS explained:

* Population: 100-1000 configurations

* Fitness function: Execution speed, accuracy, efficiency

* Selection: Tournament, Roulette, Rank-based

* Crossover: Single-point, Two-point, Uniform

* Mutation: Bit flip, Swap, Inversion, Creep



DEEP Q-LEARNING 🎮

For complex decision-making in TERENCE:

* State space: Error type, context, history

* Action space: Retry, fix, escalate, skip

* Reward: Success (+10), Failure (-5), Time saved



PERSISTENCE LAYER 💾

* SQLite - Local development

* Redis - Real-time caching

* PostgreSQL - Enterprise deployment

* Knowledge transfer across sessions



EXECUTION STRATEGIES 🎲

* Round Robin - Sequential, predictable

* Performance - Fastest worms first

* Resource-Aware - Load balanced

* Randomized - Chaos mode for testing

* ML-Optimized - AI-guided execution





                    >> TIER 5: COORDINATION >>





TERENCE - ORCHESTRATION 🎹

TERENCE maintains 50,000+ error patterns:

* Pattern matching with fuzzy algorithms

* Causal graph analysis for root cause

* Auto-fix with 90% success rate

* Deep Q-Learning for decision making



HORUS - OUTPUT GENERATION 🎨

24 ANIMATION PROTOCOLS for visualization:

* Matrix, Graphviz, Timeline, Heatmap

* Radar, Particles, Spiral, Waveform

* Network, Treemap, Gauge, Chord

* ForceAtlas, Stream, Venn, WordCloud



DASHBOARD INTEGRATION 📺

* Grafana - Industry-standard dashboards

* Kibana - Log and analytics visualization

* Custom Web - Built-in web server with WebSocket

* Static Export - PNG, PDF, SVG outputs





                    🛠 SUPPORT SYSTEMS 🛠





SHORTY ⌨

Command aliases and shortcuts for frequently used commands

Define custom shortcuts: shorty add "sysmon" = "azazael systemz --all"



TARBABY 📦

Binary builder and packager

Cross-platform builds: Linux, Windows, macOS

Output formats: DEB, RPM, Docker, static binary



PERSEPHONE 🌈

Cross-platform deployment

Windows: Service installation, PowerShell

Linux: Systemd, cron integration

macOS: LaunchD, Homebrew

Docker: Multi-arch, Kubernetes manifests



BIBL 📖

Terminology crawler and dictionary

Crawl codebase for terminology

Build searchable dictionaries

Export to JSON, Markdown, HTML





"""



RIGHT_CONTENT = """



                    🔄 6-STAGE PIPELINE 🔄





STEP 1: DISCOVERY 

* Scan target systems and assets

* Identify resources and relationships

* Build initial inventory

* Establish baseline metrics



STEP 2: ANALYSIS 

* Metaworms process collected data

* Extract patterns and anomalies

* Generate insights and recommendations

* Build comprehensive understanding



STEP 3: OPTIMIZE >>

* Apply genetic algorithm optimization

* Tune parameters for best performance

* Evolve execution strategies

* Learn from historical data



STEP 4: VALIDATE [OK]

* Verify optimizations work correctly

* Check for regressions or errors

* A/B test against baseline

* Ensure quality standards met



STEP 5: DEPLOY 🚀

* Roll out optimized configurations

* Apply learned changes to production

* Monitor for immediate issues

* Gradual rollout with canary testing



STEP 6: FEEDBACK 🔄

* Collect execution results

* Store in persistence layer

* Update pattern database

* Feed back to optimization engine





                    📡 API REFERENCE 📡





SYSTEM API 🖥

GET /api/v1/system/status - Overall health

GET /api/v1/system/resources - Detailed metrics

GET /api/v1/system/processes - Process list



METAZ API 🧬

POST /api/v1/metaz/scan - Start new scan

GET /api/v1/metaz/results/{id} - Get results

GET /api/v1/metaz/metrics - Code metrics



HYDRAMETA API [-]

POST /api/v1/hydrameta/optimize - Run optimization

GET /api/v1/hydrameta/population - Population status

GET /api/v1/hydrameta/history - Historical results



TERENCE API 🎯

POST /api/v1/terence/analyze - Analyze error

POST /api/v1/terence/fix - Apply fix

GET /api/v1/terence/suggestions - Get suggestions



HORUS API 📊

GET /api/v1/horus/dashboard - Dashboard data

GET /api/v1/horus/animations - list protocols

POST /api/v1/horus/export - Export report





                    💻 CLI COMMANDS 💻





INSTALLATION

pip install azazael-cli

docker pull azazael/cli:latest



CORE COMMANDS

azazael --help                    Show help

azazael scan --target /path       Scan codebase

azazael analyze --metaworms all   Run analysis

azazael optimize --population 100 Run GA optimization

azazael monitor --dashboard       Start dashboard

azazael deploy --cluster all      Deploy configurations

azazael health --full             Comprehensive health check



OPTIONS

--config FILE         Use specific config

--output FORMAT       Output format (json,yaml,table)

--verbose             Enable verbose logging

--interval SECONDS    Update interval





                    💾 DATABASE ADAPTERS 💾





SQLite 🟦

Best for: Local development, single-node

Features: Zero config, portable, ACID

Use case: Development and testing



Redis 🟥

Best for: Real-time caching, pub/sub

Features: In-memory speed, Lua scripting

Use case: High-performance production



PostgreSQL 🟨

Best for: Enterprise, complex queries

Features: Advanced SQL, JSONB support

Use case: Large-scale deployments



ElasticSearch 🟩

Best for: Log analysis, full-text search

Features: Inverted indexes, aggregations

Use case: Search and analytics



InfluxDB 🟪

Best for: Time-series metrics

Features: Time-series optimized, Flux

Use case: Monitoring and IoT





                    🎮 EXECUTION MODES 🎮





COTEMPORANEOUS 🔄

Execute worms one at a time

Best for: Debugging, testing, resource constraints

azazael execute --mode cotemporaneous



PARALLEL >>

Run multiple worms simultaneously

Best for: High-throughput, independent operations

azazael execute --mode parallel --workers 8



THREAD POOL 🧵

Managed thread pool with configurable resources

Best for: Mixed workloads, memory-constrained

azazael execute --mode threadpool --max-threads 16



ML-OPTIMIZED 🤖

AI determines optimal execution

Best for: General production, auto-tuning

azazael execute --mode ml-optimized --auto-tune





                    🎯 QUICK REFERENCE 🎯





NEW PROJECT WORKFLOW

1. Scan the codebase

   azazael scan --target /path/to/project --recursive



2. Run comprehensive analysis

   azazael analyze --metaworms all



3. Check system health

   azazael health --full



4. set up monitoring

   azazael monitor --dashboard &



PERFORMANCE TROUBLESHOOTING

1. Check current metrics

   azazael system --all



2. Run performance analysis

   azazael analyze --metaworms metricz,optimiz



3. Optimize

   azazael optimize --population 200 --generations 50



4. Monitor improvement

   azazael monitor --interval 5s



ERROR INVESTIGATION

1. Check recent errors

   azazael history --hours 24 --errors



2. Analyze with TERENCE

   azazael terence analyze --error-id ERROR_ID



3. Apply fix if available

   azazael terence fix --auto





                    📚 GLOSSARY 📖





WORM: Specialized agent for specific monitoring task

CLUSTER: Group of coordinated worms working together

METAWORMS: Higher-level analysis worms

HYDRAMETA: ML optimization layer

TERENCE: Orchestration and error resolution

HORUS: Output generation and visualization

PIPELINE: Continuous optimization loop

POPULATION: set of configurations in genetic algorithm





                    🎉 CONCLUSION 🎉





WHAT MAKES Azazael SPECIAL ✨

1. Self-Optimizing: Genetic algorithms continuously improve

2. Self-Healing: Pattern matching and RL fix issues auto

3. Comprehensive: From CPU metrics to code analysis to ML

4. Scalable: Single servers to massive distributed systems

5. Beautiful: Gorgeous visualizations and dashboards

6. Extensible: Plugin architecture for custom worms



NEXT STEPS 🚀

1. Install CLI: pip install azazael-cli

2. Configure database backend

3. Scan your first codebase

4. Explore the dashboard

5. Optimize with genetic algorithms

6. Automate with scheduled pipelines





                     Azazael ECOSYSTEM v2.0 



         Distributed Computing | Self-Optimizing | Self-Healing

                    

                    Made with  by the Azazael Team

                    

                 Documentation Version: 2.0.0

                 Last Updated: February 2026



"""





def create_video():

    os.makedirs(FRAME_DIR, exist_ok=True)

    print("Creating double-column scrolling video...")



    left_lines = LEFT_CONTENT.split("\n")

    right_lines = RIGHT_CONTENT.split("\n")






    for frame_num in range(DURATION * FPS):

        frame = Image.new("RGB", (WIDTH, HEIGHT), color="#0a0a1a")

        draw = ImageDraw.Draw(frame)



        try:

            font_header = ImageFont.truetype("arialbd.ttf", 42)

            font_big = ImageFont.truetype("arialbd.ttf", 32)

            font_medium = ImageFont.truetype("arial.ttf", 28)

            font_small = ImageFont.truetype("consolas.ttf", 24)

        except Exception as exc:

            note_failure("make_video_docs:L1025", exc)
            font_header = ImageFont.load_default()

            font_big = ImageFont.load_default()

            font_medium = ImageFont.load_default()

            font_small = ImageFont.load_default()



        progress = frame_num / (DURATION * FPS)



        left_line_h = 38

        right_line_h = 38



        total_height = max(

            len(left_lines) * left_line_h, len(right_lines) * right_line_h

        )

        y_offset = int(progress * (total_height + HEIGHT)) - HEIGHT



        for i, line in enumerate(left_lines):

            y = -y_offset + i * left_line_h

            if y > HEIGHT or y < -50:

                continue



            line = line.rstrip()

            if not line:

                continue



            if "" in line:

                color = f"hsl({(120 + i * 2) % 360}, 100%, 55%)"

                font = font_header

            elif line.startswith("    "):

                color = "#00ff88"

                font = font_small

            elif line.strip().startswith("*"):

                color = "#8b949e"

                font = font_small

            elif line.isupper() and len(line) < 50:

                color = "#00ffff"

                font = font_big

            else:

                color = "#e6edf3"

                font = font_medium



            draw.text((40, y), line, font=font, fill=color)



        for i, line in enumerate(right_lines):

            y = -y_offset + i * right_line_h

            if y > HEIGHT or y < -50:

                continue



            line = line.rstrip()

            if not line:

                continue



            if "" in line:

                color = f"hsl({(280 + i * 2) % 360}, 100%, 55%)"

                font = font_header

            elif line.startswith("    "):

                color = "#ff00ff"

                font = font_small

            elif line.strip().startswith("*"):

                color = "#8b949e"

                font = font_small

            elif line.isupper() and len(line) < 50:

                color = "#ff8800"

                font = font_big

            else:

                color = "#c9d1d9"

                font = font_medium



            draw.text((WIDTH // 2 + 20, y), line, font=font, fill=color)



        if frame_num % 300 == 0:

            print(f"Frame {frame_num}/{DURATION * FPS}")



        frame.save(os.path.join(FRAME_DIR, f"frame_{frame_num:06d}.png"), "PNG")



    print("Encoding video...")






    ffmpeg_cmd = [

        "ffmpeg",

        "-y",

        "-framerate",

        str(FPS),

        "-i",

        os.path.join(FRAME_DIR, "frame_%06d.png"),

        "-c:v",

        "libx264",

        "-preset",

        "fast",

        "-crf",

        "18",

        "-pix_fmt",

        "yuv420p",

        VIDEO_OUTPUT,

    ]



    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)

    if result.returncode == 0:

        print(f"Video: {VIDEO_OUTPUT}")

    else:

        print(f"Error: {result.stderr}")



    import shutil



    shutil.rmtree(FRAME_DIR)

    print("Done!")





if __name__ == "__main__":

    create_video()

# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
