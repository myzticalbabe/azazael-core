# coding: utf-8
"""


__all__ = [
    "HTML_TEMPLATE",
    "WebDashboardHandler",
    "WebDashboardServer",
    "build_html_page",
    "export_html",
    "main",
]

CORE/web_dashboard.py — HTTP Web Dashboard for Azazael Observability.

Serves a modern HTML/CSS/JS dashboard displaying health checks, metrics,
drift alerts, scheduling, MetaCluster, workflows, and dead letter queues.

Usage:
    python -m CORE.web_dashboard                    # Port 18422, one-shot
    python -m CORE.web_dashboard --host 0.0.0.0     # All interfaces
    python -m CORE.web_dashboard --port 18080       # Custom port
    python -m CORE.web_dashboard --watch            # Auto-refresh every 5s
    python -m CORE.web_dashboard --interval 30      # Custom refresh interval
"""

import os
import sys
import argparse
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, List
from datetime import datetime

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Azazael Observability</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;700&family=Space+Grotesk:wght@400;500;600;700&display=swap');

  :root {
    --bg-1: #060a16;
    --bg-2: #0b1224;
    --card-bg: rgba(12, 20, 38, 0.65);
    --card-border: rgba(88, 166, 255, 0.08);
    --card-hover-border: rgba(88, 166, 255, 0.30);
    --text: #e8edf5;
    --muted: #6a7d9e;
    --accent: #58a6ff;
    --accent-glow: rgba(88, 166, 255, 0.25);
    --green: #3fb950;
    --green-glow: rgba(63, 185, 80, 0.30);
    --red: #f85149;
    --red-glow: rgba(248, 81, 73, 0.30);
    --orange: #d29922;
    --orange-glow: rgba(210, 153, 34, 0.30);
    --purple: #bc8cff;
    --purple-glow: rgba(188, 140, 255, 0.25);
    --cyan: #56d4dd;
    --cyan-glow: rgba(86, 212, 221, 0.25);
    --pink: #ff7eb3;
    --pink-glow: rgba(255, 126, 179, 0.25);
    --yellow: #e8d44d;
    --yellow-glow: rgba(232, 212, 77, 0.25);
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg-1);
    color: var(--text);
    padding: 24px;
    min-height: 100vh;
    position: relative;
    overflow-x: hidden;
  }

  /* --- ANIMATED PARTICLE GRID BACKGROUND --- */
  #particles-canvas {
    position: fixed;
    top: 0; left: 0;
    width: 100%; height: 100%;
    z-index: 0;
    pointer-events: none;
  }

  body::before {
    content: '';
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background:
      radial-gradient(ellipse 70% 50% at 0% 50%, rgba(88, 166, 255, 0.07) 0%, transparent 100%),
      radial-gradient(ellipse 70% 50% at 100% 50%, rgba(188, 140, 255, 0.07) 0%, transparent 100%),
      radial-gradient(ellipse 50% 40% at 50% 0%, rgba(63, 185, 80, 0.05) 0%, transparent 100%),
      radial-gradient(ellipse 50% 40% at 50% 100%, rgba(255, 126, 179, 0.04) 0%, transparent 100%);
    pointer-events: none;
    z-index: 0;
    animation: ambientShift 25s ease-in-out infinite alternate;
  }

  @keyframes ambientShift {
    0%   { opacity: 0.5; }
    33%  { opacity: 0.8; }
    66%  { opacity: 1.0; }
    100% { opacity: 0.5; }
  }

  @keyframes float {
    0%, 100% { transform: translateY(0px) rotateX(0deg); }
    50% { transform: translateY(-6px) rotateX(0.5deg); }
  }

  @keyframes countUp {
    from { opacity: 0; transform: translateY(12px) scale(0.95); filter: blur(2px); }
    to { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
  }

  @keyframes slideIn {
    from { opacity: 0; transform: translateX(-20px); }
    to { opacity: 1; transform: translateX(0); }
  }

  @keyframes barFill {
    from { width: 0%; }
  }

  @keyframes dotPulse {
    0%, 100% { transform: scale(1); opacity: 1; }
    50% { transform: scale(2.2); opacity: 0.4; }
  }

  @keyframes shimmerBg {
    0%   { background-position: -300% 0; }
    100% { background-position: 300% 0; }
  }

  @keyframes cardGlowPulse {
    0%, 100% { box-shadow: 0 0 15px var(--card-glow, rgba(88,166,255,0.08)); }
    50% { box-shadow: 0 0 30px var(--card-glow, rgba(88,166,255,0.15)), 0 0 60px var(--card-glow, rgba(88,166,255,0.05)); }
  }

  @keyframes rainbowBorder {
    0%   { border-image: linear-gradient(135deg, #58a6ff, #bc8cff, #ff7eb3, #56d4dd, #58a6ff) 1; }
    25%  { border-image: linear-gradient(135deg, #bc8cff, #ff7eb3, #56d4dd, #58a6ff, #bc8cff) 1; }
    50%  { border-image: linear-gradient(135deg, #ff7eb3, #56d4dd, #58a6ff, #bc8cff, #ff7eb3) 1; }
    75%  { border-image: linear-gradient(135deg, #56d4dd, #58a6ff, #bc8cff, #ff7eb3, #56d4dd) 1; }
    100% { border-image: linear-gradient(135deg, #58a6ff, #bc8cff, #ff7eb3, #56d4dd, #58a6ff) 1; }
  }

  @keyframes textShimmer {
    0%   { background-position: -200% center; }
    100% { background-position: 200% center; }
  }

  @keyframes scaleIn {
    from { opacity: 0; transform: scale(0.9); }
    to { opacity: 1; transform: scale(1); }
  }

  @keyframes fadeSlideUp {
    from { opacity: 0; transform: translateY(20px); }
    to { opacity: 1; transform: translateY(0); }
  }

  @keyframes spinGlow {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }

  @keyframes hueShift {
    0%   { filter: hue-rotate(0deg); }
    100% { filter: hue-rotate(360deg); }
  }

  /* --- HEADER --- */
  .header {
    position: relative;
    z-index: 1;
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 28px;
    padding: 22px 28px;
    background: rgba(12, 20, 38, 0.55);
    border: 1px solid var(--card-border);
    border-radius: 18px;
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    animation: slideIn 0.7s cubic-bezier(0.22, 1, 0.36, 1);
    box-shadow: 0 0 30px rgba(88, 166, 255, 0.05);
  }
  .header::before {
    content: '';
    position: absolute;
    inset: -1px;
    border-radius: 19px;
    background: linear-gradient(135deg, rgba(88,166,255,0.15), transparent, rgba(188,140,255,0.15), transparent);
    z-index: -1;
    animation: spinGlow 8s linear infinite;
  }
  .header h1 {
    font-size: 24px;
    font-weight: 800;
    font-family: 'Space Grotesk', 'Inter', sans-serif;
    background: linear-gradient(135deg, #58a6ff, #bc8cff, #ff7eb3, #56d4dd, #58a6ff);
    background-size: 300% auto;
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    animation: textShimmer 6s linear infinite;
    letter-spacing: -0.5px;
  }
  .header .ts {
    color: var(--muted);
    font-size: 12px;
    margin-top: 4px;
    -webkit-text-fill-color: var(--muted);
  }
  .header-right { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

  /* --- GRID --- */
  .grid {
    position: relative;
    z-index: 1;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
    gap: 20px;
  }

  /* --- CARD THEMES --- */
  .card-theme-health   { --card-hue: 140; --card-accent: var(--green);  --card-glow: var(--green-glow); }
  .card-theme-db       { --card-hue: 210; --card-accent: var(--accent); --card-glow: var(--accent-glow); }
  .card-theme-metrics  { --card-hue: 185; --card-accent: var(--cyan);   --card-glow: var(--cyan-glow); }
  .card-theme-drift    { --card-hue: 35;  --card-accent: var(--orange); --card-glow: var(--orange-glow); }
  .card-theme-dead     { --card-hue: 0;   --card-accent: var(--red);    --card-glow: var(--red-glow); }
  .card-theme-session  { --card-hue: 270; --card-accent: var(--purple); --card-glow: var(--purple-glow); }
  .card-theme-cluster  { --card-hue: 220; --card-accent: var(--accent); --card-glow: var(--accent-glow); }
  .card-theme-workflow { --card-hue: 320; --card-accent: var(--pink);   --card-glow: var(--pink-glow); }
  .card-theme-train    { --card-hue: 50;  --card-accent: var(--yellow); --card-glow: var(--yellow-glow); }
  .card-theme-sb3      { --card-hue: 260; --card-accent: var(--purple); --card-glow: var(--purple-glow); }
  .card-theme-config   { --card-hue: 200; --card-accent: var(--muted);  --card-glow: rgba(106,125,158,0.2); }

  .card-theme-health   { --gradient-1: rgba(63,185,80,0.10);  --gradient-2: rgba(63,185,80,0.03); }
  .card-theme-db       { --gradient-1: rgba(88,166,255,0.10); --gradient-2: rgba(88,166,255,0.03); }
  .card-theme-metrics  { --gradient-1: rgba(86,212,221,0.10); --gradient-2: rgba(86,212,221,0.03); }
  .card-theme-drift    { --gradient-1: rgba(210,153,34,0.10); --gradient-2: rgba(210,153,34,0.03); }
  .card-theme-dead     { --gradient-1: rgba(248,81,73,0.10);  --gradient-2: rgba(248,81,73,0.03); }
  .card-theme-session  { --gradient-1: rgba(188,140,255,0.10);--gradient-2: rgba(188,140,255,0.03); }
  .card-theme-cluster  { --gradient-1: rgba(88,166,255,0.10); --gradient-2: rgba(88,166,255,0.03); }
  .card-theme-workflow { --gradient-1: rgba(255,126,179,0.10);--gradient-2: rgba(255,126,179,0.03); }
  .card-theme-train    { --gradient-1: rgba(232,212,77,0.10); --gradient-2: rgba(232,212,77,0.03); }
  .card-theme-sb3      { --gradient-1: rgba(188,140,255,0.10);--gradient-2: rgba(188,140,255,0.03); }
  .card-theme-config   { --gradient-1: rgba(106,125,158,0.08);--gradient-2: rgba(106,125,158,0.02); }

  /* --- CARD --- */
  .card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 16px;
    padding: 22px;
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
    position: relative;
    overflow: hidden;
    animation: fadeSlideUp 0.7s ease-out both;
    animation-delay: calc(var(--order, 0) * 0.08s);
    --card-accent: var(--accent);
    --card-glow: var(--accent-glow);
    perspective: 800px;
  }

  .card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background: radial-gradient(ellipse 120% 80% at 50% -20%, var(--gradient-1, rgba(88,166,255,0.08)), transparent 70%),
                radial-gradient(ellipse 80% 60% at 80% 100%, var(--gradient-2, rgba(188,140,255,0.05)), transparent 70%);
    pointer-events: none;
    transition: opacity 0.5s;
  }

  .card::after {
    content: '';
    position: absolute;
    top: -50%; left: -50%;
    width: 200%; height: 200%;
    background: conic-gradient(from 0deg at 50% 50%, transparent 0deg, var(--card-accent) 1deg, transparent 2deg, transparent 360deg);
    opacity: 0;
    transition: opacity 0.5s;
    animation: spinGlow 4s linear infinite;
    pointer-events: none;
  }

  .card:hover {
    border-color: var(--card-accent);
    transform: translateY(-3px) scale(1.01);
    box-shadow: 0 12px 48px rgba(0, 0, 0, 0.4), 0 0 20px var(--card-glow);
    animation: cardGlowPulse 2s ease-in-out infinite;
  }

  .card:hover::after {
    opacity: 0.06;
  }

  .card-content {
    position: relative;
    z-index: 1;
  }

  .card:nth-child(1)  { --order: 0; }
  .card:nth-child(2)  { --order: 1; }
  .card:nth-child(3)  { --order: 2; }
  .card:nth-child(4)  { --order: 3; }
  .card:nth-child(5)  { --order: 4; }
  .card:nth-child(6)  { --order: 5; }
  .card:nth-child(7)  { --order: 6; }
  .card:nth-child(8)  { --order: 7; }
  .card:nth-child(9)  { --order: 8; }
  .card:nth-child(10) { --order: 9; }
  .card:nth-child(11) { --order: 10; }

  .card h2 {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--card-accent);
    margin-bottom: 14px;
    padding-bottom: 10px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    display: flex;
    align-items: center;
    gap: 8px;
    position: relative;
    z-index: 1;
  }
  .card h2::after {
    content: '';
    flex: 1;
    height: 1px;
    background: linear-gradient(90deg, rgba(255,255,255,0.05), transparent);
  }

  .card .val {
    font-size: 34px;
    font-weight: 900;
    letter-spacing: -1px;
    line-height: 1;
    animation: countUp 0.8s cubic-bezier(0.22, 1, 0.36, 1);
    position: relative;
    z-index: 1;
  }
  .card .val + .sub { margin-top: 6px; }
  .card .sub {
    font-size: 12px;
    color: var(--muted);
    font-weight: 400;
    position: relative;
    z-index: 1;
  }

  /* -- Status colors (dynamic per card theme) -- */
  .status-ok { color: var(--green); text-shadow: 0 0 20px var(--green-glow); }
  .status-down { color: var(--red); text-shadow: 0 0 20px var(--red-glow); }
  .status-degraded { color: var(--orange); text-shadow: 0 0 20px var(--orange-glow); }

  /* -- Live dot -- */
  .live-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 6px;
    animation: dotPulse 2s ease-in-out infinite;
  }
  .live-dot.ok { background: var(--green); box-shadow: 0 0 10px var(--green-glow); }
  .live-dot.down { background: var(--red); box-shadow: 0 0 10px var(--red-glow); }
  .live-dot.degraded { background: var(--orange); box-shadow: 0 0 10px var(--orange-glow); }

  /* -- Badges -- */
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 600;
    transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  .badge-ok { background: rgba(63, 185, 80, 0.12); color: var(--green); border: 1px solid rgba(63, 185, 80, 0.2); }
  .badge-ok:hover { background: rgba(63, 185, 80, 0.2); box-shadow: 0 0 14px var(--green-glow); }
  .badge-down { background: rgba(248, 81, 73, 0.12); color: var(--red); border: 1px solid rgba(248, 81, 73, 0.2); }
  .badge-down:hover { background: rgba(248, 81, 73, 0.2); box-shadow: 0 0 14px var(--red-glow); }
  .badge-degraded { background: rgba(210, 153, 34, 0.12); color: var(--orange); border: 1px solid rgba(210, 153, 34, 0.2); }
  .badge-degraded:hover { background: rgba(210, 153, 34, 0.2); box-shadow: 0 0 14px var(--orange-glow); }
  .badge-neutral { background: rgba(106, 125, 158, 0.12); color: var(--muted); border: 1px solid rgba(106, 125, 158, 0.2); }

  /* -- Rows -- */
  .row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 13px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    transition: all 0.25s;
    padding: 7px 10px;
    margin: 0 -10px;
    border-radius: 8px;
    position: relative;
    z-index: 1;
  }
  .row:hover {
    background: rgba(255, 255, 255, 0.04);
    padding-left: 14px;
  }
  .row:last-child { border-bottom: none; }
  .row .label { color: var(--muted); font-weight: 400; }
  .row .value {
    font-weight: 500;
    color: var(--text);
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
  }

  .section-grid { display: grid; gap: 6px; position: relative; z-index: 1; }

  /* -- Metric rows -- */
  .metric-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 10px;
    margin: 0 -10px;
    border-radius: 8px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    transition: all 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
    position: relative;
    z-index: 1;
  }
  .metric-row:last-child { border-bottom: none; }
  .metric-row:hover {
    background: rgba(255, 255, 255, 0.04);
    transform: translateX(4px);
  }
  .metric-name {
    color: var(--card-accent, var(--cyan));
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    font-weight: 500;
    transition: all 0.3s;
  }
  .metric-row:hover .metric-name {
    text-shadow: 0 0 12px var(--card-glow, var(--cyan-glow));
  }
  .metric-stats {
    color: var(--muted);
    font-size: 11px;
    font-family: 'JetBrains Mono', monospace;
  }

  /* -- Mini progress bar -- */
  .mini-bar {
    height: 4px;
    background: rgba(255,255,255,0.05);
    border-radius: 4px;
    margin-top: 6px;
    overflow: hidden;
  }
  .mini-bar .fill {
    height: 100%;
    border-radius: 4px;
    animation: barFill 1.2s cubic-bezier(0.22, 1, 0.36, 1);
    transition: width 0.8s cubic-bezier(0.22, 1, 0.36, 1);
    position: relative;
  }
  .mini-bar .fill::after {
    content: '';
    position: absolute;
    top: 0; right: 0;
    width: 8px; height: 100%;
    border-radius: 4px;
    filter: blur(4px);
    opacity: 0.7;
  }
  .mini-bar .fill.ok { background: linear-gradient(90deg, var(--green), #5ceb6e); box-shadow: 0 0 8px var(--green-glow); }
  .mini-bar .fill.ok::after { background: var(--green); }
  .mini-bar .fill.warn { background: linear-gradient(90deg, var(--orange), #f0b429); box-shadow: 0 0 8px var(--orange-glow); }
  .mini-bar .fill.warn::after { background: var(--orange); }
  .mini-bar .fill.error { background: linear-gradient(90deg, var(--red), #ff6b6b); box-shadow: 0 0 8px var(--red-glow); }
  .mini-bar .fill.error::after { background: var(--red); }
  .mini-bar .fill.info { background: linear-gradient(90deg, var(--accent), #79b8ff); box-shadow: 0 0 8px var(--accent-glow); }
  .mini-bar .fill.info::after { background: var(--accent); }

  /* -- Drift items -- */
  .drift-item {
    padding: 9px 12px;
    margin: 0 -10px;
    border-radius: 8px;
    font-size: 13px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    transition: all 0.25s;
    position: relative;
    z-index: 1;
  }
  .drift-item:last-child { border-bottom: none; }
  .drift-item:hover {
    background: rgba(255, 255, 255, 0.04);
    padding-left: 16px;
  }
  .drift-critical {
    border-left: 3px solid var(--red);
    padding-left: 12px;
    background: linear-gradient(90deg, rgba(248,81,73,0.08), transparent);
  }
  .drift-critical:hover { border-left-color: #ff6b6b; }
  .drift-warning {
    border-left: 3px solid var(--orange);
    padding-left: 12px;
    background: linear-gradient(90deg, rgba(210,153,34,0.08), transparent);
  }
  .drift-warning:hover { border-left-color: #f0b429; }
  .drift-info {
    border-left: 3px solid var(--accent);
    padding-left: 12px;
    background: linear-gradient(90deg, rgba(88,166,255,0.08), transparent);
  }
  .drift-info:hover { border-left-color: #79b8ff; }

  /* -- Footer -- */
  .footer {
    position: relative;
    z-index: 1;
    margin-top: 28px;
    padding: 18px;
    border-radius: 14px;
    background: rgba(12, 20, 38, 0.5);
    border: 1px solid var(--card-border);
    font-size: 12px;
    color: var(--muted);
    text-align: center;
    backdrop-filter: blur(8px);
  }

  a { color: var(--accent); text-decoration: none; transition: all 0.25s; }
  a:hover { color: #79b8ff; text-shadow: 0 0 16px var(--accent-glow); }

  .refresh-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 7px 16px;
    border-radius: 20px;
    background: rgba(88, 166, 255, 0.08);
    border: 1px solid rgba(88, 166, 255, 0.15);
    color: var(--accent);
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
    text-decoration: none;
  }
  .refresh-btn:hover {
    background: rgba(88, 166, 255, 0.18);
    box-shadow: 0 0 20px var(--accent-glow), inset 0 0 20px rgba(88, 166, 255, 0.05);
    text-shadow: none;
    transform: scale(1.05);
  }
  .refresh-btn:active {
    transform: scale(0.95);
  }

  /* -- Responsive -- */
  @media (max-width: 768px) {
    .grid { grid-template-columns: 1fr; }
    body { padding: 12px; }
    .header { flex-direction: column; gap: 12px; align-items: flex-start; }
    .header-right { width: 100%; justify-content: flex-start; }
  }

  /* -- Custom scrollbar -- */
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: var(--bg-1); }
  ::-webkit-scrollbar-thumb { background: rgba(88, 166, 255, 0.15); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: rgba(88, 166, 255, 0.3); }

  /* -- Selection -- */
  ::selection { background: rgba(88, 166, 255, 0.25); color: #fff; }
</style>
</head>
<body>

<canvas id="particles-canvas"></canvas>

<script>
// --- Interactive particle system ---------------------------------
(function() {
  const canvas = document.getElementById('particles-canvas');
  const ctx = canvas.getContext('2d');
  let particles = [];
  let mouse = { x: -9999, y: -9999 };
  let animationId = null;

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  }
  window.addEventListener('resize', resize);
  resize();

  const COLORS = ['58,166,255', '188,140,255', '86,212,221', '255,126,179', '63,185,80'];
  const PARTICLE_COUNT = 80;

  class Particle {
    constructor() {
      this.reset();
    }
    reset() {
      this.x = Math.random() * canvas.width;
      this.y = Math.random() * canvas.height;
      this.size = Math.random() * 2.5 + 0.5;
      this.speedX = (Math.random() - 0.5) * 0.3;
      this.speedY = (Math.random() - 0.5) * 0.3;
      this.opacity = Math.random() * 0.5 + 0.15;
      this.color = COLORS[Math.floor(Math.random() * COLORS.length)];
      this.pulse = Math.random() * Math.PI * 2;
      this.pulseSpeed = Math.random() * 0.02 + 0.005;
    }
    update() {
      this.x += this.speedX;
      this.y += this.speedY;
      this.pulse += this.pulseSpeed;

      // Mouse interaction
      const dx = mouse.x - this.x;
      const dy = mouse.y - this.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < 120) {
        const force = (120 - dist) / 120 * 0.08;
        this.x -= dx * force;
        this.y -= dy * force;
      }

      // Wrap around
      if (this.x < -20) this.x = canvas.width + 20;
      if (this.x > canvas.width + 20) this.x = -20;
      if (this.y < -20) this.y = canvas.height + 20;
      if (this.y > canvas.height + 20) this.y = -20;
    }
    draw() {
      const pulseOpacity = this.opacity * (0.7 + 0.3 * Math.sin(this.pulse));
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(' + this.color + ',' + pulseOpacity + ')';
      ctx.fill();

      // Glow
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size * 3, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(' + this.color + ',' + (pulseOpacity * 0.15) + ')';
      ctx.fill();
    }
  }

  // Track mouse
  document.addEventListener('mousemove', function(e) {
    mouse.x = e.clientX;
    mouse.y = e.clientY;
  });
  document.addEventListener('mouseleave', function() {
    mouse.x = -9999;
    mouse.y = -9999;
  });

  function init() {
    particles = [];
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push(new Particle());
    }
  }

  function drawConnections() {
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 130) {
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.strokeStyle = 'rgba(88,166,255,' + ((1 - dist / 130) * 0.08) + ')';
          ctx.lineWidth = 0.5;
          ctx.stroke();
        }
      }
    }
  }

  function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const p of particles) {
      p.update();
      p.draw();
    }
    drawConnections();
    animationId = requestAnimationFrame(animate);
  }

  init();
  animate();

  // Pause when tab is hidden
  document.addEventListener('visibilitychange', function() {
    if (document.hidden) {
      if (animationId) cancelAnimationFrame(animationId);
    } else {
      animate();
    }
  });

  // Re-init on resize
  let resizeTimeout;
  window.addEventListener('resize', function() {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(function() {
      if (animationId) cancelAnimationFrame(animationId);
      init();
      animate();
    }, 300);
  });
})();

// --- 3D Tilt effect on hover (cards) ----------------------------
(function() {
  const cards = document.querySelectorAll('.card');
  for (const card of cards) {
    card.addEventListener('mousemove', function(e) {
      const rect = card.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const centerX = rect.width / 2;
      const centerY = rect.height / 2;
      const rotateX = ((y - centerY) / centerY) * -4;
      const rotateY = ((x - centerX) / centerX) * 4;
      card.style.setProperty('--mouse-x', x + 'px');
      card.style.setProperty('--mouse-y', y + 'px');
      card.style.transform = 'translateY(-3px) scale(1.01) perspective(800px) rotateX(' + rotateX + 'deg) rotateY(' + rotateY + 'deg)';
    });
    card.addEventListener('mouseleave', function() {
      card.style.transform = '';
    });
  }
})();

// --- Dynamic gradient follower on header ------------------------
(function() {
  const header = document.querySelector('.header');
  if (header) {
    header.addEventListener('mousemove', function(e) {
      const rect = header.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * 100;
      const y = ((e.clientY - rect.top) / rect.height) * 100;
      header.style.setProperty('--glow-x', x + '%');
      header.style.setProperty('--glow-y', y + '%');
      header.style.background = 'radial-gradient(circle at ' + x + '% ' + y + '%, rgba(88,166,255,0.10), rgba(12,20,38,0.55) 60%)';
    });
    header.addEventListener('mouseleave', function() {
      header.style.background = '';
    });
  }
})();
</script>

<div class="header">
  <div>
    <h1>Azazael<!-- --> <!-- -->Observability</h1>
    <div class="ts">Updated: {TIMESTAMP}</div>
  </div>
  <div class="header-right">
    <span class="badge badge-ok"><span class="live-dot ok"></span>Health: {HEALTH_STATUS}</span>
    <span class="badge {DEAD_LETTER_BADGE}">{DEAD_LETTER_COUNT} dead letters</span>
  </div>
</div>
<div class="grid">
  {CARDS}
</div>
<div class="footer">
  Azazael Observability Dashboard &mdash; <a href="/" class="refresh-btn">&#8635; Refresh</a>
  {REFRESH_META}
</div>
</body>
</html>"""


def _section_html(title: str, content: str, card_class: str = "", theme: str = "") -> str:
    """Build a card section HTML with optional theme class."""
    cls = f"card {card_class}".strip()
    if theme:
        cls = f"{cls} {theme}"
    return f'<div class="{cls}"><div class="card-content"><h2>{title}</h2>{content}</div></div>'


def _render_health_card(report: dict[str, Any]) -> str:
    """Render health check as a card."""
    health = report.get("health", {})
    overall = health.get("overall", "unknown")
    counts = health.get("counts", {})
    components = health.get("components", {})

    status_class = "status-ok" if overall == "ok" else "status-down"
    ok_c = counts.get("ok", 0)
    deg_c = counts.get("degraded", 0)
    down_c = counts.get("down", 0)
    rows = "\n".join(
        '<div class="row"><span class="label">' + name + '</span>' +
        '<span class="badge badge-' + comp["status"] + '">' + comp["status"] + '</span></div>'
        for name, comp in sorted(components.items())
    )
    rows_section = '<br>' + rows if rows else ""
    return _section_html(
        "Health Check",
        '<div class="val ' + status_class + '">' + overall.upper() + '</div>'
        '<div class="sub">' + str(ok_c) + ' ok <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--green);margin:0 2px;vertical-align:middle;box-shadow:0 0 6px var(--green-glow);"></span> / '
        + str(deg_c) + ' degraded <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--orange);margin:0 2px;vertical-align:middle;box-shadow:0 0 6px var(--orange-glow);"></span> / '
        + str(down_c) + ' down <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--red);margin:0 2px;vertical-align:middle;box-shadow:0 0 6px var(--red-glow);"></span></div>'
        + rows_section,
        theme="card-theme-health"
    )


def _render_metrics_card(report: dict[str, Any]) -> str:
    """Render metrics snapshot as a card."""
    metrics = report.get("metrics", {})
    if not metrics or "error" in metrics:
        return _section_html("Metrics", '<div class="sub">No metrics recorded</div>', theme="card-theme-metrics")

    items = []
    for name, agg in sorted(metrics.items()):
        if agg.get("count", 0) <= 0:
            continue
        avg_v = round(agg["avg"], 1)
        # Dynamic color: higher avg values get warmer colors
        max_possible = agg.get("max", 100) if agg.get("max", 100) > 0 else 100
        ratio = min(avg_v / max_possible, 1.0) if max_possible > 0 else 0
        bar_class = "ok" if ratio < 0.5 else ("warn" if ratio < 0.8 else "error")
        bar_pct = int(ratio * 100)
        items.append(
            '<div class="metric-row">'
            '<span class="metric-name">' + name + '</span>'
            '<span class="metric-stats">c=' + str(agg["count"]) + ' avg=' + str(avg_v) + '</span>'
            '</div>'
            '<div class="mini-bar"><div class="fill ' + bar_class + '" style="width:' + str(bar_pct) + '%"></div></div>'
        )
    return _section_html("Metrics (" + str(len(metrics)) + " tracked)", '<div class="section-grid">' + "".join(items) + '</div>', theme="card-theme-metrics")


def _render_drift_card(report: dict[str, Any]) -> str:
    """Render drift alerts as a card."""
    drift = report.get("drift", [])
    if isinstance(drift, str):
        return _section_html("Drift Alerts", '<div class="sub">No drift data</div>', theme="card-theme-drift")

    if not drift:
        return _section_html("Drift Alerts", '<div class="sub status-ok">No drift issues detected</div>', theme="card-theme-drift")

    sev_count = {"critical": 0, "warning": 0, "info": 0}
    for d in drift:
        sev_count[d.get("severity", "info")] = sev_count.get(d.get("severity", "info"), 0) + 1
    items = "".join(
        '<div class="drift-item drift-' + d.get("severity", "info") + '">' +
        '<strong>' + d.get("type", "unknown") + '</strong>: ' + d.get("message", "") +
        '</div>'
        for d in drift
    )
    severity_html = ''
    if sev_count.get("critical", 0) > 0:
        severity_html += '<span class="badge badge-down">' + str(sev_count["critical"]) + ' critical</span> '
    if sev_count.get("warning", 0) > 0:
        severity_html += '<span class="badge badge-degraded">' + str(sev_count["warning"]) + ' warning</span> '
    sev_line = '<br><div class="row" style="margin-bottom:6px;">' + severity_html + '</div>' if severity_html else ''
    return _section_html("Drift Alerts (" + str(len(drift)) + " issues)", sev_line + items, theme="card-theme-drift")


def _render_dead_letters_card(report: dict[str, Any]) -> str:
    """Render dead letter queue as a card."""
    dl = report.get("dead_letters", {})
    if isinstance(dl, dict) and "error" in dl:
        return _section_html("Dead Letters", '<div class="sub">No data</div>', theme="card-theme-dead")

    total = dl.get("total_dead_letters", 0) if isinstance(dl, dict) else 0
    status = "status-ok" if total == 0 else "status-down"
    # Dynamic glow intensity based on count
    glow_intensity = min(total * 0.05, 1.0) if total > 0 else 0
    glow_style = ''
    if total > 0:
        glow_style = ' style="text-shadow: 0 0 ' + str(20 + int(glow_intensity * 40)) + 'px var(--red-glow), 0 0 ' + str(40 + int(glow_intensity * 80)) + 'px var(--red-glow);"'
    return _section_html(
        "Dead Letter Queue",
        f'<div class="val {status}"{glow_style}>{total}</div>'
        f'<div class="sub">total dead letters</div>'
        + (f'<div class="mini-bar"><div class="fill error" style="width:{min(total, 100)}%"></div></div>' if total > 0 else ''),
        theme="card-theme-dead"
    )


def _render_session_card(report: dict[str, Any]) -> str:
    """Render session info as a card."""
    session = report.get("session", {})
    if not session:
        return _section_html("Session", '<div class="sub">No data</div>', theme="card-theme-session")

    memories = session.get("total_memories", 0)
    sessions = session.get("total_sessions", 0)
    db_size = session.get("db_size_mb", 0)
    sources = session.get("top_sources", {})
    events = session.get("top_events", {})

    src_rows = "".join(
        '<div class="row"><span class="label">' + s + '</span><span class="value">' + str(c) + '</span></div>'
        for s, c in sources.items()
    )
    evt_rows = "".join(
        '<div class="row"><span class="label">' + s + '</span><span>' + str(c) + '</span></div>'
        for s, c in events.items()
    )
    src_section = '<br><div class="sub">Top Sources</div>' + src_rows if src_rows else ""
    evt_section = '<br><div class="sub">Top Events</div>' + evt_rows if evt_rows else ""

    # Dynamic: memory bar proportional to typical max
    mem_bar_pct = min(int(memories / 10000 * 100), 100)
    mem_bar = '<div class="mini-bar"><div class="fill info" style="width:' + str(mem_bar_pct) + '%"></div></div>' if mem_bar_pct > 0 else ''

    return _section_html(
        "Session",
        '<div class="val" style="color:var(--purple);text-shadow:0 0 20px var(--purple-glow);">' + str(memories) + '</div><div class="sub">memories</div>'
        + mem_bar
        + '<br><div class="row"><span class="label">Sessions</span><span class="value">' + str(sessions) + '</span></div>'
        + '<div class="row"><span class="label">DB Size</span><span class="value">' + str(db_size) + ' MB</span></div>'
        + src_section + evt_section,
        theme="card-theme-session"
    )


def _render_metacluster_card(report: dict[str, Any]) -> str:
    """Render MetaCluster status as a card."""
    mc = report.get("metacluster", {})
    if not mc:
        return _section_html("MetaCluster", '<div class="sub">No data</div>', theme="card-theme-cluster")

    natures = mc.get("natures", 0)
    worms = mc.get("total_worms", 0)
    routes = mc.get("routing_paths", 0)
    cross = mc.get("cross_category_routes", 0)
    orch = mc.get("orchestration_history", 0)

    # Dynamic color intensity based on cluster size
    glow_intensity = min(worms / 100, 1.0)
    glow = ' style="color:var(--accent);text-shadow:0 0 ' + str(10 + int(glow_intensity * 30)) + 'px var(--accent-glow);"'

    return _section_html(
        "MetaCluster",
        f'<div class="val"{glow}>{worms}</div><div class="sub">total worms</div><br>'
        + '<div class="row"><span class="label">Natures</span><span class="value">' + str(natures) + '</span></div>'
        + '<div class="row"><span class="label">Routing Paths</span><span class="value">' + str(routes) + '</span></div>'
        + '<div class="row"><span class="label">Cross-Nature Routes</span><span class="value">' + str(cross) + '</span></div>'
        + '<div class="row"><span class="label">Orchestration Runs</span><span class="value">' + str(orch) + '</span></div>',
        theme="card-theme-cluster"
    )


def _render_workflows_card(report: dict[str, Any]) -> str:
    """Render workflow engine as a card."""
    wf = report.get("workflows", {})
    if not wf:
        return _section_html("Workflows", '<div class="sub">No data</div>', theme="card-theme-workflow")

    templates = wf.get("templates", [])
    total = wf.get("total_executions", 0)
    last = wf.get("last_execution", {})

    tmpl = ", ".join(templates) if templates else "None"
    last_info = ""
    if last:
        icon = "&#10003;" if last.get("success", False) else "&#10007;"
        icon_color = 'var(--green)' if last.get("success", False) else 'var(--red)'
        last_info = '<br><div class="row"><span class="label">Last Run</span><span class="value" style="color:' + icon_color + ';">' + last.get("name", "?") + ' ' + icon + '</span></div>'

    return _section_html(
        "Workflows",
        f'<div class="val" style="color:var(--pink);text-shadow:0 0 20px var(--pink-glow);">{total}</div><div class="sub">executions</div><br>'
        + '<div class="row"><span class="label">Templates</span><span class="value">' + tmpl + '</span></div>'
        + last_info,
        theme="card-theme-workflow"
    )


def _render_scheduling_card(report: dict[str, Any]) -> str:
    """Render scheduled training as a card."""
    sched = report.get("scheduling", {})
    if not sched:
        return _section_html("Training", '<div class="sub">No data</div>', theme="card-theme-train")

    runs = sched.get("recent_runs", [])
    total = sched.get("total_runs", 0)

    if not runs:
        return _section_html("Training", '<div class="sub">No training runs yet</div>', theme="card-theme-train")

    rows = "".join(
        '<div class="metric-row">'
        '<span class="metric-name">' + str(r.get("run_id", "?"))[:20] + '</span>'
        '<span class="metric-stats">' + str(r.get("episodes", 0)) + ' ep / reward ' + str(round(r.get("avg_reward", 0), 2)) + '</span>'
        '</div>'
        for r in runs
    )
    return _section_html("Training (" + str(total) + " runs)", '<div class="section-grid">' + rows + '</div>', theme="card-theme-train")


def _render_sb3_training_card(report: dict[str, Any]) -> str:
    """Render SB3 TrainingOrchestrator runs as a card."""
    sb3 = report.get("sb3_training", {})
    if not sb3:
        return _section_html("SB3 Training", '<div class="sub">No SB3 training runs yet</div>', theme="card-theme-sb3")

    runs = sb3.get("recent_runs", [])
    total = sb3.get("total_runs", 0)

    if not runs:
        return _section_html("SB3 Training", '<div class="sub">No SB3 training runs yet</div>', theme="card-theme-sb3")

    rows = "".join(
        '<div class="metric-row">'
        '<span class="metric-name">' + r.get("algo", "?") + '</span>'
        '<span class="metric-stats">' + str(r.get("total_timesteps", 0)) + ' steps / ' + str(r.get("records_used", 0)) + ' rec / ' + str(round(r.get("duration_ms", 0), 0)) + 'ms</span>'
        '</div>'
        for r in runs
    )
    return _section_html("SB3 Training (" + str(total) + " runs)", '<div class="section-grid">' + rows + '</div>', theme="card-theme-sb3")


def _render_db_monitor_card(report: dict[str, Any]) -> str:
    """Render DatabaseMonitorWorm snapshot as a card."""
    dbm = report.get("db_monitor", {})
    if not dbm:
        return _section_html("DB Monitor", '<div class="sub">No data</div>', theme="card-theme-db")

    status = dbm.get("status", "UNKNOWN")
    status_class = "status-ok" if status == "HEALTHY" else "status-down"
    alerts = dbm.get("alerts_pending", 0)
    alert_str = f'<span class="badge badge-down">{alerts} alerts</span>' if alerts > 0 else ''
    errors_7d = dbm.get("errors_7d", 0)
    growth_24h = dbm.get("growth_24h", 0)

    # Mini bars for data-driven visuals
    err_bar_class = "ok" if errors_7d == 0 else ("warn" if errors_7d < 10 else "error")
    err_bar_pct = min(errors_7d * 5, 100)
    growth_pct = min(int(growth_24h / 1000 * 100), 100) if growth_24h > 0 else 0

    return _section_html(
        "DB Monitor",
        '<div class="val ' + status_class + '" style="' + ('text-shadow:0 0 30px var(--green-glow);' if status == 'HEALTHY' else 'text-shadow:0 0 30px var(--red-glow);') + '">' + status + '</div>'
        + '<div class="sub">' + dbm.get("backend", "?") + ' database</div>'
        + ('<br><div class="row"><span class="label">DB Size</span><span class="value">' + str(dbm.get("size_mb", 0)) + ' MB</span></div>')
        + '<div class="row"><span class="label">Memories</span><span class="value">' + str(dbm.get("memories", 0)) + '</span></div>'
        + '<div class="row"><span class="label">Sessions</span><span class="value">' + str(dbm.get("sessions", 0)) + '</span></div>'
        + '<div class="row"><span class="label">Growth 24h</span><span class="value">' + str(growth_24h) + '</span></div>'
        + ('<div class="mini-bar"><div class="fill info" style="width:' + str(growth_pct) + '%"></div></div>' if growth_pct > 0 else '')
        + '<div class="row"><span class="label">Errors 7d</span><span class="value" style="color:' + ('var(--green)' if errors_7d == 0 else 'var(--red)') + ';">' + str(errors_7d) + '</span></div>'
        + ('<div class="mini-bar"><div class="fill ' + err_bar_class + '" style="width:' + str(err_bar_pct) + '%"></div></div>' if err_bar_pct > 0 else '')
        + '<div class="row"><span class="label">Warnings 7d</span><span class="value">' + str(dbm.get("warnings_7d", 0)) + '</span></div>'
        + ('<br>' + alert_str if alert_str else ''),
        theme="card-theme-db"
    )


def _render_config_card(report: dict[str, Any]) -> str:
    """Render config as a card."""
    cfg = report.get("config", {})
    if not cfg:
        return _section_html("Configuration", '<div class="sub">No data</div>', theme="card-theme-config")

    cb = cfg.get("circuit_breaker", {})
    cb_enabled = cb.get("enabled", "?")
    cb_style = ' style="color:var(--green);text-shadow:0 0 10px var(--green-glow);"' if cb_enabled == True else ''

    return _section_html(
        "Configuration",
        '<div class="row"><span class="label">Version</span><span class="value">' + str(cfg.get("version", "?")) + '</span></div>'
        + '<div class="row"><span class="label">PAMS URL</span><span class="value">' + str(cfg.get("pams_url", "?")) + '</span></div>'
        + '<div class="row"><span class="label">CB Enabled</span><span class="value"' + cb_style + '>' + str(cb_enabled) + '</span></div>'
        + '<div class="row"><span class="label">Log Level</span><span class="value">' + str(cfg.get("log_level", "?")) + '</span></div>',
        theme="card-theme-config"
    )


def build_html_page(watch: bool = False, interval: int = 5) -> str:
    """Build the full HTML page with latest observability data."""
    report = {}
    try:
        from CORE.dashboard import get_report
        report = get_report()
    except Exception as exc:
        note_failure("web_dashboard:L1109", exc)
        report = {"timestamp": datetime.now().isoformat(), "error": "Failed to gather report"}

    timestamp = report.get("timestamp", datetime.now().isoformat())
    health = report.get("health", {})
    health_status = health.get("overall", "unknown") if isinstance(health, dict) else "unknown"
    dl = report.get("dead_letters", {})
    dl_count = dl.get("total_dead_letters", 0) if isinstance(dl, dict) else 0
    dl_badge = "badge-ok" if dl_count == 0 else "badge-down"

    refresh_meta = ""
    if watch:
        refresh_meta = f'<br>Auto-refreshing every {interval}s. <a href="?stop">Stop</a>'

    cards = "".join([
        _render_health_card(report),
        _render_db_monitor_card(report),
        _render_metrics_card(report),
        _render_drift_card(report),
        _render_dead_letters_card(report),
        _render_session_card(report),
        _render_metacluster_card(report),
        _render_workflows_card(report),
        _render_scheduling_card(report),
        _render_sb3_training_card(report),
        _render_config_card(report),
    ])

    html = HTML_TEMPLATE
    html = html.replace("{TIMESTAMP}", str(timestamp))
    html = html.replace("{HEALTH_STATUS}", str(health_status))
    html = html.replace("{DEAD_LETTER_COUNT}", str(dl_count))
    html = html.replace("{DEAD_LETTER_BADGE}", str(dl_badge))
    html = html.replace("{CARDS}", cards)
    html = html.replace("{REFRESH_META}", refresh_meta)
    return html


class WebDashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves the observability HTML page."""

    # Shared state set by the server
    watch = False
    interval = 5

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/health":
            self._send_json()
        elif self.path == "/prometheus":
            self._send_prometheus()
        elif self.path.startswith("/?stop"):
            WebDashboardHandler.watch = False
            self._send_html("Auto-refresh stopped. <a href='/'>Refresh manually</a>")
        else:
            self._send_html()

    def _send_html(self, body: str = ""):
        """Send HTML response."""
        if not body:
            html = build_html_page(watch=self.watch, interval=self.interval)
        else:
            html = f"""<!DOCTYPE html><html><head><title>Azazael</title>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>body{{font-family:-apple-system,sans-serif;background:#0d1117;color:#c9d1d9;padding:40px;text-align:center}}</style>
</head><body>{body}</body></html>"""
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self):
        """Send JSON response (health endpoint)."""
        try:
            from CORE.health_server import get_health_json
            data = get_health_json().encode("utf-8")
        except Exception as exc:
            note_failure("web_dashboard:L1187", exc)
            data = b'{"error": "Failed to gather health data"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_prometheus(self):
        """Send Prometheus-format response."""
        try:
            from CORE.health_server import get_prometheus_metrics
            data = get_prometheus_metrics().encode("utf-8")
        except Exception as exc:
            note_failure("web_dashboard:L1200", exc)
            data = b"# ERROR: Failed to gather metrics"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        """Suppress default logging; print a compact line."""
        print(f"  [WEB] {args[0]} {args[1]} {args[2]}", flush=True)


class WebDashboardServer:
    """HTTP server for the observability web dashboard."""

    def __init__(self, host: str = "127.0.0.1", port: int = 18422,
                 watch: bool = False, interval: int = 5):
        self.host = host
        self.port = port
        self.watch = watch
        self.interval = interval
        self.server: (HTTPServer | None) = None
        self._running = threading.Event()

    def start(self):
        """Start the web dashboard server."""
        # Patch handler with watch/interval state
        WebDashboardHandler.watch = self.watch
        WebDashboardHandler.interval = self.interval

        self.server = HTTPServer((self.host, self.port), WebDashboardHandler)
        self._running.set()

        print(f"Web dashboard listening on http://{self.host}:{self.port}", flush=True)
        if self.watch:
            print(f"  Auto-refresh enabled (every {self.interval}s). Open in browser.", flush=True)
        print("  Endpoints:", flush=True)
        print("    /         — HTML dashboard", flush=True)
        print("    /health   — JSON health report", flush=True)
        print("    /prometheus — Prometheus metrics", flush=True)

        while self._running.is_set():
            self.server.handle_request()

        print("Web dashboard stopped.", flush=True)

    def stop(self):
        """Stop the web dashboard server."""
        self._running.clear()
        if self.server:
            try:
                self.server.server_close()
            except OSError as exc:
                note_failure("web_dashboard:L1253", exc) # scanner:ignore

    def start_background(self) -> threading.Thread:
        """Start the web dashboard in a background daemon thread."""
        t = threading.Thread(target=self.start, daemon=True)
        t.start()
        return t


def export_html(path: str, since: (str | None) = None) -> str:
    """Export a standalone HTML report to a file.

    Args:
        path: Output file path
        since: Optional time filter (e.g., "24h")

    Returns:
        The path the report was written to
    """
    report: dict[str, Any] = {}
    try:
        from CORE.dashboard import get_report
        report = get_report(since=since)
    except Exception as e:
        note_failure("web_dashboard:L1277", e)
        report = {"error": str(e)}

    cards_html = "".join([
        _render_health_card(report),
        _render_db_monitor_card(report),
        _render_metrics_card(report),
        _render_drift_card(report),
        _render_dead_letters_card(report),
        _render_session_card(report),
        _render_metacluster_card(report),
        _render_workflows_card(report),
        _render_scheduling_card(report),
        _render_sb3_training_card(report),
        _render_config_card(report),
    ])
    html = HTML_TEMPLATE
    html = html.replace("{TIMESTAMP}", str(report.get("timestamp", datetime.now().isoformat())))
    html = html.replace("{HEALTH_STATUS}", str(report.get("health", {}).get("overall", "unknown") if isinstance(report.get("health"), dict) else "unknown"))
    html = html.replace("{DEAD_LETTER_COUNT}", str(report.get("dead_letters", {}).get("total_dead_letters", 0) if isinstance(report.get("dead_letters"), dict) else 0))
    html = html.replace("{DEAD_LETTER_BADGE}", "badge-ok" if isinstance(report.get("dead_letters"), dict) and report["dead_letters"].get("total_dead_letters", 0) == 0 else "badge-down")
    html = html.replace("{CARDS}", cards_html)
    html = html.replace("{REFRESH_META}", "")

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return os.path.abspath(path)


def main():
    parser = argparse.ArgumentParser(
    description="Azazael Web Dashboard — HTTP observability dashboard",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=18422, help="HTTP port")
    parser.add_argument("--watch", action="store_true", help="Auto-refresh mode")
    parser.add_argument("--interval", type=int, default=5, help="Refresh interval (seconds)")
    parser.add_argument("--export", default=None, help="Export HTML to file and exit")
    parser.add_argument("--since", default=None, help='Time filter: "24h", "30m"')

    args = parser.parse_args()

    if args.export:
        path = export_html(args.export, since=args.since)
        print(f"  Exported HTML report to {path}")
        return

    server = WebDashboardServer(
        host=args.host,
        port=args.port,
        watch=args.watch,
        interval=args.interval,
    )

    try:
        server.start()
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        server.stop()


if __name__ == "__main__":
    # Add parent dir (src/) to sys.path for -m invocation
    from pathlib import Path
    _src = str(Path(__file__).resolve().parent.parent)
    if _src not in sys.path:
        sys.path.insert(0, _src)
    main()
