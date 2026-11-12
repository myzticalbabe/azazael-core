#!/usr/bin/env python3
"""
Freebuff Desktop Parallel Agent Throughput Benchmark
Tests Freebuff's 20-agent parallel execution model.
"""

import subprocess
import json
import time
import statistics
from dataclasses import dataclass
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import argparse

@dataclass
class FreebuffResult:
    agent_id: int
    task: str
    success: bool
    duration_sec: float
    output_chars: int
    error: str = ""

class FreebuffBenchmark:
    def __init__(self, max_parallel: int = 20):
        self.max_parallel = max_parallel
        self.results: List[FreebuffResult] = []
        self.lock = threading.Lock()
    
    def run_single_agent(self, agent_id: int, task: str, model: str = "deepseek-v4.1-flash") -> FreebuffResult:
        """Run a single Freebuff agent task."""
        start = time.perf_counter()
        try:
            # Freebuff CLI command - adjust based on actual CLI
            cmd = [
                "freebuff", "run",
                "--model", model,
                "--task", task,
                "--workspace", f"bench_agent_{agent_id}"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=300)
            duration = time.perf_counter() - start
            
            success = result.returncode == 0
            output_chars = len(result.stdout) + len(result.stderr)
            error = result.stderr if not success else ""
            
            return FreebuffResult(agent_id, task, success, duration, output_chars, error)
        except subprocess.TimeoutExpired:
            duration = time.perf_counter() - start
            return FreebuffResult(agent_id, task, False, duration, 0, "Timeout")
        except Exception as e:
            duration = time.perf_counter() - start
            return FreebuffResult(agent_id, task, False, duration, 0, str(e))
    
    def run_parallel_benchmark(self, tasks: List[str], model: str = "deepseek-v4.1-flash") -> dict:
        """Run benchmark with N parallel agents."""
        print(f"Starting Freebuff benchmark: {len(tasks)} tasks, max_parallel={self.max_parallel}")
        
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=self.max_parallel) as executor:
            futures = {
                executor.submit(self.run_single_agent, i, task, model): i 
                for i, task in enumerate(tasks)
            }
            
            for future in as_completed(futures):
                result = future.result()
                with self.lock:
                    self.results.append(result)
                status = "OK" if result.success else "FAIL"
                print(f"  Agent {result.agent_id}: {status} ({result.duration_sec:.1f}s, {result.output_chars} chars)")
        
        total_time = time.perf_counter() - start
        
        successful = [r for r in self.results if r.success]
        failed = [r for r in self.results if not r.success]
        
        durations = [r.duration_sec for r in successful]
        output_chars = [r.output_chars for r in successful]
        
        return {
            "total_tasks": len(tasks),
            "successful": len(successful),
            "failed": len(failed),
            "total_time_sec": total_time,
            "tasks_per_sec": len(tasks) / total_time if total_time > 0 else 0,
            "avg_duration_sec": statistics.mean(durations) if durations else 0,
            "median_duration_sec": statistics.median(durations) if durations else 0,
            "total_output_chars": sum(output_chars),
            "chars_per_sec": sum(output_chars) / total_time if total_time > 0 else 0,
            "parallelism_used": self.max_parallel,
            "model": model
        }


def generate_coding_tasks(n: int) -> List[str]:
    """Generate N diverse coding tasks for benchmarking."""
    task_templates = [
        "Write a Python function that {action} with {constraint}",
        "Create a class that implements {pattern} for {domain}",
        "Refactor this code to {improvement}: {snippet}",
        "Write tests for {functionality} covering {edge_cases}",
        "Implement {algorithm} in Python with {requirements}",
    ]
    
    actions = ["parses JSON with schema validation", "compresses data using LZ4", "encrypts data with AES-GCM",
               "streams large files efficiently", "manages a connection pool", "parses CSV with type inference"]
    constraints = ["zero-copy operations", "async/await support", "memory limit of 100MB", "sub-millisecond latency"]
    patterns = ["observer", "factory", "strategy", "builder", "command", "state machine"]
    domains = ["logging", "caching", "rate limiting", "configuration", "plugin systems", "event sourcing"]
    improvements = ["remove global state", "add type hints", "improve error handling", "optimize hot path", "add concurrency"]
    edge_cases = ["empty input", "malformed data", "concurrent access", "resource exhaustion", "network failures"]
    algorithms = ["LRU cache", "consistent hashing", "Bloom filter", "merkle tree", "skip list", "radix tree"]
    requirements = ["O(1) operations", "thread safety", "persistence", "distributed consensus", "memory efficiency"]
    
    tasks = []
    for i in range(n):
        template = task_templates[i % len(task_templates)]
        task = template.format(
            action=actions[i % len(actions)],
            constraint=constraints[i % len(constraints)],
            pattern=patterns[i % len(patterns)],
            domain=domains[i % len(domains)],
            improvement=improvements[i % len(improvements)],
            edge_cases=edge_cases[i % len(edge_cases)],
            algorithm=algorithms[i % len(algorithms)],
            requirements=requirements[i % len(requirements)],
            snippet="def foo(x): return x * 2",
            functionality="user authentication"
        )
        tasks.append(task)
    return tasks


def main():
    parser = argparse.ArgumentParser(description="Freebuff Parallel Throughput Benchmark")
    parser.add_argument("--tasks", type=int, default=20, help="Number of tasks to run")
    parser.add_argument("--parallel", type=int, default=20, help="Max parallel agents")
    parser.add_argument("--model", default="deepseek-v4.1-flash", help="Model to use")
    parser.add_argument("--output", default="freebuff_benchmark_results.json", help="Output file")
    args = parser.parse_args()
    
    # Check if freebuff is available
    try:
        subprocess.run(["freebuff", "--version"], capture_output=True, check=True, timeout=30)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: freebuff CLI not found. Install with: npm install -g freebuff")
        return
    
    bench = FreebuffBenchmark(max_parallel=args.parallel)
    tasks = generate_coding_tasks(args.tasks)
    
    results = bench.run_parallel_benchmark(tasks, args.model)
    
    print("\n" + "=" * 60)
    print("FREEBUFF PARALLEL BENCHMARK RESULTS")
    print("=" * 60)
    print(f"Model: {results['model']}")
    print(f"Parallelism: {results['parallelism_used']}")
    print(f"Total tasks: {results['total_tasks']}")
    print(f"Successful: {results['successful']}")
    print(f"Failed: {results['failed']}")
    print(f"Total time: {results['total_time_sec']:.1f}s")
    print(f"Throughput: {results['tasks_per_sec']:.2f} tasks/sec")
    print(f"Avg duration: {results['avg_duration_sec']:.1f}s")
    print(f"Median duration: {results['median_duration_sec']:.1f}s")
    print(f"Output rate: {results['chars_per_sec']:,.0f} chars/sec")
    
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()