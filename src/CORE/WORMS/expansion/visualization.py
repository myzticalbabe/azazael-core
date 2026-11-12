# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: VISUALIZATION (5 worms)
DashboardGen, GraphRenderer, ReportCompiler, HeatmapBuilder, TimelineViz
"""

import json
import time
import threading
import subprocess
from datetime import datetime
from typing import Any, List
from collections import defaultdict
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("visualization:L19", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("visualization:L28", exc)
    _FREETHREAD = False
    FreeThreadPool = None
    ThreadSafeDict = dict
    ThreadSafeList = list
    def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:
            note_failure("visualization:L37", exc)
            return False


@register_worm(category="visualization", priority=5, cooldown_seconds=60.0,
    description="Dashboard generator — auto-generate HTML dashboards from worm metrics")
class DashboardGen:
    """Auto-generates HTML dashboards from worm metrics."""

    VERSION = "1.0.0"

    def __init__(self):
        self._panels: list[dict] = []
        self._lock = threading.Lock()

    def add_panel(self, title: str, data: dict[str, Any], panel_type: str = "table"):
        with self._lock:
            self._panels.append({"title": title, "data": data, "type": panel_type, "added": time.time()})

    def render_html(self, title: str = "Azazael Dashboard") -> str:
        """Render all panels as an HTML dashboard."""
        html = f"""<!DOCTYPE html><html><head><title>{title}</title>
<style>body{{font-family:monospace;background:#1a1a2e;color:#eee;margin:20px}}h1{{color:#00d9ff}}
.panel{{background:#16213e;margin:10px 0;padding:15px;border-radius:8px}}.panel h2{{color:#00ff88;margin:0 0 10px}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;text-align:left;border-bottom:1px solid #333}}
th{{color:#ffaa00}}</style></head><body><h1>{title}</h1>"""
        with self._lock:
            for p in self._panels:
                html += f'<div class="panel"><h2>{p["title"]}</h2>'
                if p["type"] == "table" and isinstance(p["data"], dict):
                    html += "<table><tr><th>Key</th><th>Value</th></tr>"
                    for k, v in p["data"].items():
                        html += f"<tr><td>{k}</td><td>{v}</td></tr>"
                    html += "</table>"
                else:
                    html += f"<pre>{json.dumps(p['data'], indent=2, default=str)}</pre>"
                html += "</div>"
        return html + "</body></html>"



    def get_summary(self):
        return {"status":"ok","worm":"DashboardGen","method":"render_html"}
@register_worm(category="visualization", priority=4, cooldown_seconds=60.0,
    description="Graph renderer — render dependency graphs as DOT/SVG via Graphviz")
class GraphRenderer:
    """Renders dependency and relationship graphs."""

    VERSION = "1.0.0"

    def __init__(self):
        self._nodes: set[str] = set()
        self._edges: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def add_node(self, name: str, label: str = ""):
        with self._lock:
            self._nodes.add(name)

    def add_edge(self, src: str, dst: str, label: str = ""):
        with self._lock:
            self._edges.append((src, dst))

    def to_dot(self, graph_name: str = "AzazaelGraph") -> str:
        """Generate DOT format graph."""
        lines = [f"digraph {graph_name} {{", "  rankdir=LR;",
                 '  node [shape=box, style=filled, fillcolor="#16213e", fontcolor="#00ff88"];',
                 '  edge [color="#00d9ff"];']
        for node in sorted(self._nodes):
            lines.append(f'  "{node}";')
        for src, dst in self._edges:
            lines.append(f'  "{src}" -> "{dst}";')
        lines.append("}")
        return "\n".join(lines)

    def render_svg(self, output_path: str = "/tmp/azazael_graph.svg") -> bool:
        """Render to SVG using graphviz (dot) CLI."""
        dot = self.to_dot()
        try:
            r = subprocess.run(["dot", "-Tsvg", "-o", output_path],
                input=dot, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
            return r.returncode == 0
        except Exception as exc:
            note_failure("visualization:L119", exc)
            return False


@register_worm(category="visualization", priority=4, cooldown_seconds=90.0,
    description="Report compiler — compile JSON data into PDF/HTML reports with templates")
class ReportCompiler:
    """Compiles structured data into PDF/HTML reports."""

    VERSION = "1.0.0"

    def __init__(self):
        self._sections: list[dict] = []
        self._lock = threading.Lock()

    def add_section(self, title: str, content: Any, section_type: str = "text"):
        with self._lock:
            self._sections.append({"title": title, "content": content, "type": section_type})

    def compile_html(self, report_title: str = "Azazael Report") -> str:
        """Compile into a self-contained HTML report."""
        html = f"""<!DOCTYPE html><html><head><title>{report_title}</title>
<style>body{{font-family:'Segoe UI',sans-serif;max-width:900px;margin:auto;padding:20px;background:#0f0f1a;color:#ddd}}
h1{{color:#00d9ff;border-bottom:2px solid #333;padding-bottom:10px}}h2{{color:#00ff88}}pre{{background:#111;padding:10px;border-radius:4px;overflow-x:auto}}
.meta{{color:#888;font-size:0.9em}}</style></head><body><h1>{report_title}</h1>
<p class="meta">Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>"""
        with self._lock:
            for s in self._sections:
                html += f"<h2>{s['title']}</h2>"
                if s["type"] == "json":
                    html += f"<pre>{json.dumps(s['content'], indent=2, default=str)}</pre>"
                elif s["type"] == "table" and isinstance(s["content"], list):
                    html += "<table style='width:100%;border-collapse:collapse'>"
                    if s["content"]:
                        keys = list(s["content"][0].keys()) if isinstance(s["content"][0], dict) else []
                        html += "<tr>" + "".join(f"<th style='padding:8px;border-bottom:1px solid #333;color:#ffaa00'>{k}</th>" for k in keys) + "</tr>"
                        for row in s["content"]:
                            html += "<tr>" + "".join(f"<td style='padding:8px;border-bottom:1px solid #222'>{row.get(k, '')}</td>" for k in keys) + "</tr>"
                    html += "</table>"
                else:
                    html += f"<p>{s['content']}</p>"
        return html + "</body></html>"


@register_worm(category="visualization", priority=4, cooldown_seconds=60.0,
    description="Heatmap builder — generate activity heatmaps from timestamped event data")
class HeatmapBuilder:
    """Builds activity heatmaps from timestamped event data."""

    VERSION = "1.0.0"

    def __init__(self):
        self._events: list[dict] = []
        self._lock = threading.Lock()

    def record_event(self, category: str, timestamp: (float | None) = None):
        with self._lock:
            self._events.append({"category": category, "timestamp": timestamp or time.time()})

    def build_hourly_heatmap(self) -> dict[str, dict[int, int]]:
        """Build a category × hour heatmap matrix."""
        matrix: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        with self._lock:
            for ev in self._events:
                hour = datetime.fromtimestamp(ev["timestamp"]).hour
                matrix[ev["category"]][hour] += 1
        return {cat: dict(hours) for cat, hours in matrix.items()}

    def get_busiest_hours(self, top_n: int = 5) -> list[dict]:
        """Get busiest hours across all categories."""
        heatmap = self.build_hourly_heatmap()
        hour_totals = defaultdict(int)
        for cat, hours in heatmap.items():
            for h, count in hours.items():
                hour_totals[h] += count
        ranked = sorted(hour_totals.items(), key=lambda x: -x[1])[:top_n]
        return [{"hour": h, "events": c} for h, c in ranked]


@register_worm(category="visualization", priority=4, cooldown_seconds=60.0,
    description="Timeline visualizer — Gantt/timeline chart generation from worm execution histories")
class TimelineViz:
    """Generates timeline/Gantt visualizations from worm execution histories."""

    VERSION = "1.0.0"

    def __init__(self):
        self._spans: list[dict] = []
        self._lock = threading.Lock()

    def add_span(self, worm: str, start: float, end: float, status: str = "completed"):
        with self._lock:
            self._spans.append({"worm": worm, "start": start, "end": end,
                                "duration": end - start, "status": status})

    def to_timeline_html(self) -> str:
        """Generate an HTML Gantt-like timeline."""
        with self._lock:
            spans = sorted(self._spans, key=lambda s: s["start"])
        if not spans:
            return "<p>No timeline data</p>"
        min_start = spans[0]["start"]
        max_end = max(s["end"] for s in spans)
        total_dur = max(max_end - min_start, 1)
        colors = {"completed": "#00ff88", "failed": "#ff4444", "running": "#00d9ff", "stopped": "#ffaa00"}
        html = """<div style="font-family:monospace;font-size:12px">"""
        for s in spans[-50:]:
            left_pct = (s["start"] - min_start) / total_dur * 100
            width_pct = max(1, s["duration"] / total_dur * 100)
            color = colors.get(s["status"], "#888")
            html += f'<div style="margin:2px 0"><span style="display:inline-block;width:150px;text-align:right;padding-right:10px">{s["worm"]}</span>'
            html += f'<span style="display:inline-block;margin-left:{left_pct}%;width:{width_pct}%;background:{color};height:20px;border-radius:3px;position:relative" title="{s["duration"]:.2f}s"></span></div>'
        return html + "</div>"
