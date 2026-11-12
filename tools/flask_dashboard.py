"""Flask Dashboard — HYDRA Cluster Monitoring Interface

Run:  python3 flask_dashboard.py
Open: http://localhost:5000
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / "Desktop/azazael-project-e"))

from flask import Flask, render_template_string, jsonify
import psutil

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>HYDRA Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #0f0f23; color: #e0e0e0; padding: 20px; }
        h1 { color: #00d4ff; margin-bottom: 20px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 30px; }
        .card { background: #1a1a3e; border-radius: 10px; padding: 20px; border: 1px solid #2a2a5e; }
        .card h3 { color: #8888cc; font-size: 14px; text-transform: uppercase; margin-bottom: 8px; }
        .card .value { font-size: 32px; font-weight: bold; color: #00ff88; }
        .card .sub { font-size: 12px; color: #666; margin-top: 4px; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th { background: #1a1a3e; padding: 10px; text-align: left; color: #8888cc; }
        td { padding: 8px 10px; border-bottom: 1px solid #1a1a3e; }
        .badge { padding: 2px 8px; border-radius: 4px; font-size: 12px; }
        .badge-ok { background: #00ff8833; color: #00ff88; }
        .badge-fail { background: #ff444433; color: #ff4444; }
        .refresh { text-align: right; margin-bottom: 10px; }
        .refresh button { background: #00d4ff; border: none; padding: 8px 16px; border-radius: 5px; cursor: pointer; }
        .refresh button:hover { background: #00b8e6; }
    </style>
</head>
<body>
    <div class="refresh"><button onclick="location.reload()">🔄 Refresh</button></div>
    <h1>🛰️ HYDRA Cluster Dashboard</h1>
    
    <div class="grid">
        <div class="card">
            <h3>HYDRA Nodes</h3>
            <div class="value" id="nodes">{{ status.total_nodes }}</div>
        </div>
        <div class="card">
            <h3>Successful</h3>
            <div class="value" style="color:#00ff88">{{ status.successful }}</div>
        </div>
        <div class="card">
            <h3>Failed</h3>
            <div class="value" style="color:#ff4444">{{ status.failed }}</div>
        </div>
        <div class="card">
            <h3>Pending</h3>
            <div class="value" style="color:#ffaa00">{{ status.pending }}</div>
        </div>
        <div class="card">
            <h3>CPU</h3>
            <div class="value">{{ cpu }}%</div>
        </div>
        <div class="card">
            <h3>RAM</h3>
            <div class="value">{{ mem.percent }}%</div>
            <div class="sub">{{ mem.available // 1024**3 }} GB free</div>
        </div>
    </div>

    <h2 style="margin-bottom:10px">📋 Nodes</h2>
    <table>
        <tr><th>Name</th><th>Group</th><th>Status</th><th>Priority</th></tr>
        {% for n in nodes %}
        <tr>
            <td>{{ n.name }}</td>
            <td>{{ n.group }}</td>
            <td><span class="badge badge-{{ 'ok' if n.status == 'success' else 'fail' }}">{{ n.status }}</span></td>
            <td>{{ n.priority }}</td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

@app.route("/")
def dashboard():
    from HYDRA import HYDRACluster
    cluster = HYDRACluster()
    cluster.load_nodes()
    status = cluster.get_cluster_status()
    nodes = [
        {"name": n.name, "group": n.group, "status": n.status, "priority": n.priority}
        for n in sorted(cluster.nodes.values(), key=lambda x: x.priority, reverse=True)
    ]
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory()
    return render_template_string(HTML, status=status, nodes=nodes, cpu=cpu, mem=mem)

@app.route("/api/status")
def api_status():
    from HYDRA import HYDRACluster
    cluster = HYDRACluster()
    cluster.load_nodes()
    return jsonify(cluster.get_cluster_status())

if __name__ == "__main__":
    print("[net] HYDRA Dashboard: http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
# scanner:file-ignore unicode_emoji -- HTML template strings (1 console print fixed to ASCII separately)
