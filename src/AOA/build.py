import json, os, sys
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

BASE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE,'benchmark_data.json'),encoding='utf-8') as f:
    bm = json.load(f)
p = bm['perfmon']; a = bm['azazael']; s = bm['system']
pv = s['python_version']; cpu = s['cpu_count']; host = s['node']
cpup = p['cpu_percent']; memp = p['memory_percent']
diskp = p['disk_percent']
mt = p['memory_total_gb']; dt = p['disk_total_gb']; wc = a['total_worms']

# ── Live registry snapshot (2026-09-02: rebuild reflects source, not statics) ──
# Import arm modules to trigger @register_arm, then snapshot arms/capabilities.
sys.path.insert(0, BASE)
ARMS = []
N_ARMS = 0
N_CAPS = 0
QUARTET = []
SUPPORT = []
try:
    for mod in ('arms.azazael_arm', 'arms.codex_arm', 'arms.eve_arm', 'arms.libra_arm',
                'arms.support_arms', 'arms.kali_arm'):
        __import__(mod)
    from core.arm_registry import ArmRegistry
    for row in ArmRegistry().list_arms():
        ARMS.append(row)
        N_ARMS += 1
        N_CAPS += row['capabilities']
        (QUARTET if row['category'] == 'quartet' else SUPPORT).append(row)
    # Live worm count from the azazael arm (registry beats the json snapshot)
    try:
        _az_arm = ArmRegistry().get_arm('azazael')
        if _az_arm is not None:
            wc = int(_az_arm.execute('discover_worms')
                     .get('data', {}).get('worm_count', wc))
    except Exception as exc:
        note_failure("build:L35", exc)
except Exception as _e:
    print(f'Registry probe failed ({_e}); diagrams will fall back to benchmark statics')

H = []
def w(*lines):
    for line in lines:
        H.append(line)

# ── HTML HEAD ──
w('<!DOCTYPE html>',
'<html lang=en>',
'<head>',
'<meta charset=UTF-8>',
'<meta name="viewport" content="width=device-width,initial-scale=1.0">',
'<title>AOA — Armorie-of-Anubis | Neural Architecture Disclosure</title>')
w('<script src=https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js></script>')
w('<script>mermaid.initialize({startOnLoad:true,theme:"base",')
w('themeVariables:{primaryColor:"#1a1a2e",primaryTextColor:"#e0e0ff",')
w('primaryBorderColor:"#4a4aff",lineColor:"#6a6aff",')
w('secondaryColor:"#16213e",tertiaryColor:"#0f3460"},')
w('flowchart:{useMaxWidth:true,htmlLabels:true,curve:"basis"},')
w('sequence:{useMaxWidth:true,boxMargin:10}});</script>')

# ── CSS ──
w('<style>')
w(':root{--bg:#08081a;--sf:#0d0d24;--cd:#111133;--bd:#1a1a4a;')
w('--tx:#d0d0f0;--mt:#7070a0;--ac:#5b5bff;--gn:#00e676;')
w('--or:#ff9100;--pu:#d500f9;--cy:#00e5ff;--rd:#ff1744;--gd:#ffd600}')
w('*{margin:0;padding:0;box-sizing:border-box}')
w('body{background:var(--bg);color:var(--tx);')
w('font-family:system-ui,sans-serif;overflow-x:hidden;line-height:1.6}')
w('body::before{content:"";position:fixed;top:0;left:0;')
w('width:100%;height:100%;')
w('background-image:linear-gradient(rgba(90,90,255,.03) 1px,transparent 1px),')
w('linear-gradient(90deg,rgba(90,90,255,.03) 1px,transparent 1px);')
w('background-size:40px 40px;z-index:0;pointer-events:none}')
w('#particles{position:fixed;top:0;left:0;width:100%;height:100%;')
w('z-index:0;pointer-events:none}')
w('.container{max-width:1400px;margin:0 auto;padding:30px 24px 80px;')
w('position:relative;z-index:1}')
w('.header{text-align:center;padding:60px 20px 40px}')
w('.header h1{font-size:3.2em;font-weight:800;letter-spacing:2px;')
w('background:linear-gradient(135deg,#5b5bff,#d500f9,#00e5ff);')
w('-webkit-background-clip:text;-webkit-text-fill-color:transparent;')
w('background-clip:text;animation:tg 4s ease-in-out infinite}')
w('@keyframes tg{0%,100%{filter:drop-shadow(0 0 15px rgba(91,91,255,.4))}')
w('50%{filter:drop-shadow(0 0 35px rgba(213,0,249,.6))}}')
w('.subtitle{color:var(--mt);font-size:1.05em;margin-top:10px;')
w('letter-spacing:3px;text-transform:uppercase}')
w('.badge{display:inline-block;background:var(--sf);')
w('border:1px solid var(--bd);border-radius:20px;padding:4px 16px;')
w('margin:12px 6px 0;font-size:.85em;color:var(--mt)}')
w('.badge .num{color:var(--gn);font-weight:700}')
w('h2{color:var(--ac);font-size:1.7em;margin:50px 0 24px;')
w('padding-bottom:10px;border-bottom:2px solid rgba(91,91,255,.2);')
w('letter-spacing:1px;display:flex;align-items:center;gap:10px}')
w('.stat-grid{display:grid;')
w('grid-template-columns:repeat(auto-fit,minmax(220px,1fr));')
w('gap:16px;margin:20px 0 30px}')
w('.stat-tile{background:var(--cd);border:1px solid var(--bd);')
w('border-radius:14px;padding:22px 20px;text-align:center;')
w('transition:transform .2s,box-shadow .2s}')
w('.stat-tile:hover{transform:translateY(-3px);')
w('box-shadow:0 8px 30px rgba(91,91,255,.15)}')
w('.stat-tile .value{font-size:2.4em;font-weight:800;')
w('background:linear-gradient(135deg,var(--ac),var(--pu));')
w('-webkit-background-clip:text;-webkit-text-fill-color:transparent;')
w('background-clip:text}')
w('.stat-tile .label{color:var(--mt);font-size:.85em;margin-top:6px;')
w('text-transform:uppercase;letter-spacing:1px}')
w('.card-grid{display:grid;')
w('grid-template-columns:repeat(auto-fit,minmax(320px,1fr));')
w('gap:20px;margin:20px 0}')
w('.card{background:var(--cd);border:1px solid var(--bd);')
w('border-radius:14px;padding:24px;transition:border-color .3s}')
w('.card:hover{border-color:rgba(91,91,255,.4)}')
w('.card h3{color:var(--ac);font-size:1.15em;margin-bottom:14px}')
w('.card table{width:100%;border-collapse:collapse}')
w('.card td{padding:8px 6px;border-bottom:1px solid rgba(255,255,255,.03);')
w('font-size:.92em}')
w('.card td:first-child{color:var(--mt);width:40%}')
w('.card td:last-child{font-family:Consolas,monospace;text-align:right}')
w('.ok{color:var(--gn)}.warn{color:var(--or)}.info{color:var(--cy)}')
w('.mermaid-wrap{background:rgba(13,13,36,.6);')
w('border:1px solid var(--bd);border-radius:16px;padding:30px 20px;')
w('margin:24px 0;overflow-x:auto;position:relative}')
w('.mermaid-wrap::before{content:"";position:absolute;')
w('top:-1px;left:-1px;right:-1px;bottom:-1px;border-radius:16px;')
w('padding:1px;')
w('background:linear-gradient(135deg,rgba(91,91,255,.3),rgba(213,0,249,.3));')
w('-webkit-mask:linear-gradient(#fff 0 0) content-box,')
w('linear-gradient(#fff 0 0);')
w('-webkit-mask-composite:xor;mask-composite:exclude;pointer-events:none}')
w('.mermaid{display:flex;justify-content:center}')
w('.arm-table{width:100%;border-collapse:collapse;')
w('margin:20px 0;font-size:.9em}')
w('.arm-table th{text-align:left;padding:12px 14px;')
w('background:rgba(91,91,255,.08);color:var(--ac);')
w('font-weight:600;letter-spacing:1px;text-transform:uppercase;font-size:.8em}')
w('.arm-table td{padding:12px 14px;')
w('border-bottom:1px solid rgba(255,255,255,.03)}')
w('.arm-table tr:hover td{background:rgba(91,91,255,.03)}')
w('.cat-quartet{background:rgba(213,0,249,.15);color:#e040fb;')
w('padding:2px 10px;border-radius:10px;font-size:.78em;font-weight:600}')
w('.cat-support{background:rgba(0,230,118,.12);color:#69f0ae;')
w('padding:2px 10px;border-radius:10px;font-size:.78em;font-weight:600}')
w('.footer{text-align:center;color:var(--mt);font-size:.8em;')
w('margin-top:60px;padding-top:30px;border-top:1px solid var(--bd)}')
w('@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}')
w('.pulse{display:inline-block;width:8px;height:8px;border-radius:50%;')
w('background:var(--gn);animation:pulse 1.5s ease-in-out infinite;')
w('margin-right:6px}')
w('::-webkit-scrollbar{width:8px;height:8px}')
w('::-webkit-scrollbar-track{background:var(--bg)}')
w('::-webkit-scrollbar-thumb{background:var(--bd);border-radius:4px}')
w('</style>')
w('</head>')
w('<body>')
w('<canvas id=particles></canvas>')
w('<div class=container>')

print('CSS done')

# ── HEADER ──
w('<div class=header>')
w('<h1>AOA — Armorie-of-Anubis</h1>')
w('<div class=subtitle>Neural Architecture Disclosure | Umbrella Orchestrator v1.1.0</div>')
w('<div style=margin-top:16px>')
w(f'<span class=badge><span class=num>{N_ARMS or 8}</span> Neural Arms</span>')
w(f'<span class=badge><span class=num>{wc}</span> Worm Modules</span>')
w(f'<span class=badge><span class=num>56</span> Cross-Arm Routes</span>')
w(f'<span class=badge><span class=num>2</span> Arm Categories</span>')
w(f'<span class=badge>Python <span class=num>{pv}</span> Free-Threaded</span>')
w(f'<span class=badge><span class=num>{cpu}</span> CPU Cores</span>')
w('</div></div>')

# ── LIVE METRICS ──
w('<h2>Live System Metrics</h2>')
w('<div class=stat-grid>')
w(f'<div class=stat-tile><div class=value>{cpup}%</div>')
w('<div class=label>CPU Utilization</div></div>')
w(f'<div class=stat-tile><div class=value>{memp}%</div>')
w(f'<div class=label>Memory ({mt} GB)</div></div>')
w(f'<div class=stat-tile><div class=value>{diskp}%</div>')
w(f'<div class=label>Disk ({dt} GB)</div></div>')
w(f'<div class=stat-tile><div class=value>{wc}</div>')
w('<div class=label>Active Worms</div></div>')
w(f'<div class=stat-tile><div class=value>{cpu}</div>')
w('<div class=label>Logical Processors</div></div>')
w('</div>')
print('Metrics done')

# ── DIAGRAM 1: System Architecture (registry-driven since 2026-09-02) ──
w('<h2>Neural System Architecture</h2>')
w('<div class=mermaid-wrap><div class=mermaid>')
w('graph TB')
w('    AOA{{"AOA Umbrella Orchestrator"}}')
if QUARTET:
    w('    subgraph Q["QUARTET NEURAL CORE"]')
    for row in sorted(QUARTET, key=lambda r: -r['priority']):
        wr_id = row['arm_id'].upper()
        w(f'        {wr_id}["{row["name"]} - {row["capabilities"]} caps - Priority {row["priority"]}"]')
    w('    end')
    w('    AOA --> Q')
if SUPPORT:
    w('    subgraph S["SUPPORT INFRASTRUCTURE"]')
    for row in sorted(SUPPORT, key=lambda r: -r['priority']):
        wr_id = row['arm_id'].upper()
        w(f'        {wr_id}["{row["name"]} - {row["capabilities"]} caps - Priority {row["priority"]}"]')
    w('    end')
    w('    AOA --> S')
w('    classDef orchestrator fill:#0d0d30,stroke:#ffd600,stroke-width:3px,color:#ffe080')
w('    classDef quartet fill:#1a0a2e,stroke:#d500f9,stroke-width:2px,color:#e0b0ff')
w('    classDef support fill:#0a1a1a,stroke:#00e676,stroke-width:2px,color:#b0ffe0')
w('    class AOA orchestrator')
if QUARTET:
    w('    class ' + ','.join(r['arm_id'].upper() for r in QUARTET) + ' quartet')
if SUPPORT:
    w('    class ' + ','.join(r['arm_id'].upper() for r in SUPPORT) + ' support')
w('</div></div>')
# Arm/capability summary line (registry live count)
if N_ARMS:
    w('<p class=footnote><strong>' + str(N_ARMS) + ' arms</strong> · <strong>' + str(N_CAPS) +
      ' capabilities</strong> (registry snapshot at build time)</p>')

# Write the generated disclosure.
out_path = os.path.join(BASE, 'AOA_STRUCTURE.html')
with open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(H))
print(f'Wrote {out_path} ({os.path.getsize(out_path)} bytes)')
