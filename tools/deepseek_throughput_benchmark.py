#!/usr/bin/env python3
"""
DeepSeek V4.1 Flash Maximum Throughput Benchmark
Tests all access modes to find the true throughput ceiling.
"""

import asyncio
import aiohttp
import time
import json
import statistics
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor
import os

@dataclass
class BenchmarkResult:
    mode: str
    concurrency: int
    total_requests: int
    successful: int
    failed: int
    total_tokens: int
    total_time: float
    tokens_per_sec: float
    requests_per_sec: float
    avg_latency_ms: float
    p50_latency_ms: float
    p99_latency_ms: float
    error_rate: float
    cost_estimate_usd: float
    errors: List[str] = field(default_factory=list)

class DeepSeekBenchmark:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com/v1"):
        self.api_key = api_key
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def __aenter__(self):
        connector = aiohttp.TCPConnector(limit=3000, limit_per_host=3000)
        timeout = aiohttp.ClientTimeout(total=300, connect=10)
        self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self
        
    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()
    
    async def single_request(self, prompt: str, max_tokens: int = 1000, reasoning_effort: int = 50) -> Dict:
        """Single request with timing."""
        start = time.perf_counter()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "deepseek-flash",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 1.0,
            "top_p": 0.95,
            "stream": False,
            "extra_body": {"reasoning_effort": reasoning_effort}
        }
        try:
            async with self.session.post(f"{self.base_url}/chat/completions", headers=headers, json=payload) as resp:
                latency_ms = (time.perf_counter() - start) * 1000
                data = await resp.json()
                if resp.status == 200:
                    usage = data.get("usage", {})
                    return {
                        "success": True,
                        "latency_ms": latency_ms,
                        "input_tokens": usage.get("prompt_tokens", 0),
                        "output_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0)
                    }
                else:
                    return {
                        "success": False,
                        "latency_ms": latency_ms,
                        "error": f"HTTP {resp.status}: {data.get('error', {}).get('message', 'Unknown')}"
                    }
        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            return {"success": False, "latency_ms": latency_ms, "error": str(e)}
    
    async def run_concurrency_test(self, concurrency: int, requests_per_worker: int, 
                                   prompt: str, max_tokens: int = 1000) -> BenchmarkResult:
        """Run benchmark at specific concurrency level."""
        semaphore = asyncio.Semaphore(concurrency)
        latencies = []
        total_tokens = 0
        successful = 0
        failed = 0
        errors = []
        
        async def worker():
            nonlocal successful, failed, total_tokens
            for _ in range(requests_per_worker):
                async with semaphore:
                    result = await self.single_request(prompt, max_tokens)
                    latencies.append(result["latency_ms"])
                    if result["success"]:
                        successful += 1
                        total_tokens += result["total_tokens"]
                    else:
                        failed += 1
                        errors.append(result.get("error", "Unknown"))
        
        start = time.perf_counter()
        await asyncio.gather(*[worker() for _ in range(concurrency)])
        total_time = time.perf_counter() - start
        
        total_requests = successful + failed
        tokens_per_sec = total_tokens / total_time if total_time > 0 else 0
        requests_per_sec = total_requests / total_time if total_time > 0 else 0
        error_rate = failed / total_requests if total_requests > 0 else 0
        
        # Cost estimate: $0.15/M in, $0.60/M out (off-peak 50%)
        input_tokens_est = successful * 100  # rough estimate
        output_tokens_est = total_tokens - input_tokens_est
        cost = (input_tokens_est / 1_000_000 * 0.15) + (output_tokens_est / 1_000_000 * 0.60)
        cost *= 0.5  # off-peak
        
        return BenchmarkResult(
            mode="direct_api",
            concurrency=concurrency,
            total_requests=total_requests,
            successful=successful,
            failed=failed,
            total_tokens=total_tokens,
            total_time=total_time,
            tokens_per_sec=tokens_per_sec,
            requests_per_sec=requests_per_sec,
            avg_latency_ms=statistics.mean(latencies) if latencies else 0,
            p50_latency_ms=statistics.median(latencies) if latencies else 0,
            p99_latency_ms=sorted(latencies)[int(len(latencies)*0.99)] if latencies else 0,
            error_rate=error_rate,
            cost_estimate_usd=cost,
            errors=errors[:10]
        )

    async def find_saturation_point(self, prompt: str, max_tokens: int = 1000) -> List[BenchmarkResult]:
        """Find concurrency saturation point via binary search + linear scan."""
        results = []
        
        # Exponential probe to find upper bound
        concurrency = 10
        while concurrency <= 2500:
            print(f"Testing concurrency={concurrency}...")
            result = await self.run_concurrency_test(concurrency, 3, prompt, max_tokens)
            results.append(result)
            print(f"  {result.requests_per_sec:.1f} req/s, {result.tokens_per_sec:.0f} tok/s, {result.error_rate*100:.1f}% errors")
            
            if result.error_rate > 0.05:  # 5% error rate = saturation
                break
            concurrency = min(concurrency * 2, 2500)
            await asyncio.sleep(2)  # cooldown
        
        # Fine-grained scan around saturation
        if len(results) > 1:
            last_ok = results[-2].concurrency if results[-1].error_rate > 0.05 else results[-1].concurrency
            for c in range(max(10, last_ok - 50), min(2500, last_ok + 100), 25):
                if not any(r.concurrency == c for r in results):
                    result = await self.run_concurrency_test(c, 3, prompt, max_tokens)
                    results.append(result)
                    print(f"  {result.requests_per_sec:.1f} req/s, {result.tokens_per_sec:.0f} tok/s, {result.error_rate*100:.1f}% errors")
        
        return sorted(results, key=lambda r: r.concurrency)


async def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("Set DEEPSEEK_API_KEY environment variable")
        return
    
    # Test prompt - realistic coding task
    prompt = """Write a Python function that implements a thread-safe LRU cache with TTL support.
    Requirements:
    - O(1) get/put operations
    - Automatic expiration of stale entries
    - Thread-safe for concurrent access
    - Configurable max size and TTL
    - Include comprehensive type hints
    - Add docstring with usage examples"""
    
    async with DeepSeekBenchmark(api_key) as bench:
        print("=" * 60)
        print("DEEPSEEK V4.1 FLASH THROUGHPUT BENCHMARK")
        print("=" * 60)
        
        results = await bench.find_saturation_point(prompt, max_tokens=2000)
        
        print("\n" + "=" * 60)
        print("RESULTS SUMMARY")
        print("=" * 60)
        print(f"{'Concurrency':>12} | {'Req/s':>8} | {'Tok/s':>10} | {'P50 ms':>8} | {'P99 ms':>8} | {'Errors%':>7} | {'Cost $':>8}")
        print("-" * 80)
        for r in results:
            print(f"{r.concurrency:>12} | {r.requests_per_sec:>8.1f} | {r.tokens_per_sec:>10.0f} | "
                  f"{r.p50_latency_ms:>8.0f} | {r.p99_latency_ms:>8.0f} | {r.error_rate*100:>7.1f} | {r.cost_estimate_usd:>8.4f}")
        
        # Find peak throughput
        peak = max(results, key=lambda r: r.tokens_per_sec if r.error_rate < 0.05 else 0)
        print(f"\nPEAK THROUGHPUT: {peak.tokens_per_sec:,.0f} tokens/sec at concurrency={peak.concurrency}")
        print(f"  Requests/sec: {peak.requests_per_sec:.1f}")
        print(f"  P50 latency: {peak.p50_latency_ms:.0f}ms")
        print(f"  Error rate: {peak.error_rate*100:.1f}%")
        print(f"  Est. cost: ${peak.cost_estimate_usd:.4f} for test")
        
        # Save results
        with open("deepseek_benchmark_results.json", "w", encoding="utf-8") as f:
            json.dump([{
                "concurrency": r.concurrency,
                "requests_per_sec": r.requests_per_sec,
                "tokens_per_sec": r.tokens_per_sec,
                "p50_latency_ms": r.p50_latency_ms,
                "p99_latency_ms": r.p99_latency_ms,
                "error_rate": r.error_rate,
                "cost_estimate_usd": r.cost_estimate_usd
            } for r in results], f, indent=2)
        print("\nResults saved to deepseek_benchmark_results.json")


if __name__ == "__main__":
    asyncio.run(main())