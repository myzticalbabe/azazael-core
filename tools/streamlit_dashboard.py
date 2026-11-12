"""Streamlit Dashboard — Azazael System Monitor

Run:  streamlit run streamlit_dashboard.py
"""

import sys
import json
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Desktop/azazael-project-e"))
sys.path.insert(0, str(Path.home() / "Desktop"))

try:
    import streamlit as st
except ImportError as _dep_exc:
    raise SystemExit("streamlit_dashboard.py needs streamlit (pip install streamlit; run via 'streamlit run'): " + str(_dep_exc)) from _dep_exc
import pandas as pd
import psutil

st.set_page_config(page_title="Azazael Monitor", layout="wide")
st.title("🛰️ Azazael System Monitor")

# ── Sidebar ──
st.sidebar.header("Controls")
refresh = st.sidebar.button("🔄 Refresh")
auto = st.sidebar.checkbox("Auto-refresh (30s)")

if auto:
    st.sidebar.info("Auto-refreshing every 30s...")
    st.cache_data.clear()

# ── System Health ──
col1, col2, col3, col4 = st.columns(4)
with col1:
    cpu = psutil.cpu_percent()
    st.metric("CPU", f"{cpu}%")
with col2:
    mem = psutil.virtual_memory()
    st.metric("RAM", f"{mem.percent}%", f"{mem.available/1024**3:.1f} GB free")
with col3:
    disk = psutil.disk_usage("/")
    st.metric("Disk", f"{disk.percent}%", f"{disk.free/1024**3:.1f} GB free")
with col4:
    st.metric("Python", sys.version.split()[0])

# ── HYDRA Status ──
st.subheader("🌐 HYDRA Cluster")
try:
    from HYDRA import HYDRACluster
    cluster = HYDRACluster()
    cluster.load_nodes()
    s = cluster.get_cluster_status()
    hcol1, hcol2, hcol3, hcol4 = st.columns(4)
    hcol1.metric("Nodes", s["total_nodes"])
    hcol2.metric("Successful", s["successful"])
    hcol3.metric("Failed", s["failed"])
    hcol4.metric("Pending", s.get("pending", 0))
except Exception as e:
    st.error(f"Cannot connect to HYDRA: {e}")

# ── Nodes Table ──
if st.checkbox("Show HYDRA nodes"):
    try:
        cluster.load_nodes()
        nodes_data = [
            {"Name": n.name, "Group": n.group, "Status": n.status, "Priority": n.priority}
            for n in cluster.nodes.values()
        ]
        st.dataframe(pd.DataFrame(nodes_data), use_container_width=True)
    except Exception as e:
        st.warning(f"No nodes loaded: {e}")

# ── Active Processes ──
st.subheader("📋 Active Python Processes")
procs = []
for p in psutil.process_iter(["pid", "name", "memory_percent", "cpu_percent"]):
    if "python" in p.info["name"] or "python" in p.info["name"]:
        procs.append(p.info)
st.dataframe(pd.DataFrame(procs), use_container_width=True)

# ── Recent Scripts ──
st.subheader("📁 Desktop Scripts")
scripts = sorted(Path.home().glob("Desktop/*.py"))
script_data = [{"Name": f.name, "Size": f.stat().st_size / 1024} for f in scripts]
st.dataframe(pd.DataFrame(script_data), use_container_width=True)
# scanner:file-ignore unicode_emoji -- template/output strings, UTF-8 by contract (0 console-print sites verified)
