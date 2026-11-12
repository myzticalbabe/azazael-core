#!/usr/bin/env python3
"""
Real-time Evolution Cycle Dashboard
Monitors evolution_cycle.py progress via memory store
"""

import sys
import time
import json
import sqlite3
from pathlib import Path

sys.path.insert(0, 'src')
from CORE.local_memory import get_memory_store

store = get_memory_store()
conn = sqlite3.connect(store.db_path, timeout=30)
conn.row_factory = sqlite3.Row

def get_latest_evolution():
    cur = conn.cursor()
    cur.execute('''
        SELECT data, timestamp FROM memories 
        WHERE event_type="evolution_result" 
        ORDER BY timestamp DESC LIMIT 1
    ''')
    row = cur.fetchone()
    if row:
        d = json.loads(row['data'])
        return {
            'generation': d.get('generations', 'N/A'),
            'best_total': d.get('best_total', 'N/A'),
            'margin': d.get('margin', 'N/A'),
            'promoted': d.get('promoted', 'N/A'),
            'trajectory': d.get('trajectory', []),
            'trajectory_avg': d.get('trajectory_avg', []),
            'timestamp': row['timestamp']
        }
    return None

def get_running_gen():
    """Check if there's a currently running evolution by looking at recent logs"""
    # Check for recent evolution logs
    cur = conn.cursor()
    cur.execute('''
        SELECT data, timestamp FROM memories 
        WHERE event_type IN ("evolution_gen", "evolution_eval", "evolution_log")
        ORDER BY timestamp DESC LIMIT 5
    ''')
    return [json.loads(r['data']) for r in cur.fetchall()]

def print_dashboard():
    latest = get_latest_evolution()
    print("\n" + "="*60)
    print("EVOLUTION CYCLE DASHBOARD")
    print("="*60)
    if latest:
        print(f"Last Completed Run: {latest['timestamp']}")
        print(f"  Generations: {latest['generation']}")
        print(f"  Best Total: {latest['best_total']}")
        print(f"  Margin: {latest['margin']}")
        print(f"  Promoted: {latest['promoted']}")
        if latest['trajectory']:
            print(f"  Trajectory: {latest['trajectory']}")
        if latest['trajectory_avg']:
            print(f"  Avg Trajectory: {latest['trajectory_avg']}")
    else:
        print("No completed runs found")
    
    # Check for running process
    import psutil
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if 'evolution_cycle' in ' '.join(proc.info['cmdline'] or []):
                print(f"\nRUNNING: PID {proc.info['pid']}")
                break
        except Exception as exc:
            note_failure("scripts/evolution_dashboard.py", exc)
            pass


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

print_dashboard()
print("\nPress Ctrl+C to exit...")
try:
    while True:
        time.sleep(5)
        print_dashboard()
except KeyboardInterrupt:
    print("\nDashboard stopped")