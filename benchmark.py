import asyncio
import sys
import os

import uuid
import time
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import aiohttp
import grpc
from typing import List, Tuple, Dict
from dataclasses import dataclass

from grpc_service import users_pb2_grpc, users_pb2

# Set seaborn style for better plots
sns.set_theme(style="whitegrid")

@dataclass
class BenchmarkResult:
    name: str
    latencies: List[float]
    total_requests: int
    duration: float
    failed_requests: int

    @property
    def throughput(self) -> float:
        return self.total_requests / self.duration if self.duration > 0 else 0

    def percentiles(self) -> Dict[str, float]:
        if not self.latencies:
            return {"p50": 0, "p90": 0, "p95": 0, "p99": 0}
        return {
            "p50": np.percentile(self.latencies, 50),
            "p90": np.percentile(self.latencies, 90),
            "p95": np.percentile(self.latencies, 95),
            "p99": np.percentile(self.latencies, 99)
        }

class BenchmarkTarget:
    async def setup(self):
        pass

    async def teardown(self):
        pass
    
    async def make_request(self) -> bool:
        """Returns True if successful, False otherwise"""
        raise NotImplementedError

class GraphQLTarget(BenchmarkTarget):
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.session = None

    async def setup(self):
        # Using a proper TCP connector matching production context (connection pooling)
        connector = aiohttp.TCPConnector(limit=0) # Handled by benchmark concurrency
        self.session = aiohttp.ClientSession(connector=connector)

    async def teardown(self):
        if self.session:
            await self.session.close()

    async def make_request(self) -> bool:
        try:
            async with self.session.post(self.endpoint, json={'query': f"query {{ users_GetUser(id: \"{uuid.uuid4()}\") {{ id name }} }}"}, headers={'Content-Type': 'application/json'}, timeout=5) as resp:
                await resp.read()
                return resp.status == 200
        except Exception:
            return False

class GrpcTarget(BenchmarkTarget):
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.channel = None
        self.stub = None

    async def setup(self):
        self.channel = grpc.aio.insecure_channel(self.endpoint)
        self.stub = users_pb2_grpc.UserServiceStub(self.channel)

    async def teardown(self):
        if self.channel:
            await self.channel.close()

    async def make_request(self) -> bool:
        try:
            request = users_pb2.UserRequest(id=str(uuid.uuid4()))
            await self.stub.GetUser(request)
            return True
        except grpc.RpcError:
            return False

async def worker(target: BenchmarkTarget, duration: float, latencies: List[float], stats: dict):
    start_time = time.time()
    while time.time() - start_time < duration:
        req_start = time.time()
        success = await target.make_request()
        req_duration = (time.time() - req_start) * 1000 # to ms
        
        if success:
            latencies.append(req_duration)
            stats['success'] += 1
        else:
            stats['failed'] += 1

async def run_load_test(name: str, target: BenchmarkTarget, concurrency: int, duration: float, warmup_duration: float = 2.0) -> BenchmarkResult:
    print(f"\n--- Benchmarking {name} ---")
    await target.setup()
    
    # Warmup Phase
    print(f"[{name}] Warming up for {warmup_duration} seconds...")
    warmup_lats = []
    w_stats = {'success': 0, 'failed': 0}
    tasks = [worker(target, warmup_duration, warmup_lats, w_stats) for _ in range(concurrency)]
    await asyncio.gather(*tasks)

    # Actual Benchmark Phase
    print(f"[{name}] Running benchmark for {duration} seconds with concurrency={concurrency}...")
    latencies = []
    stats = {'success': 0, 'failed': 0}
    start_time = time.time()
    
    tasks = [worker(target, duration, latencies, stats) for _ in range(concurrency)]
    await asyncio.gather(*tasks)
    
    actual_duration = time.time() - start_time
    await target.teardown()

    result = BenchmarkResult(
        name=name,
        latencies=latencies,
        total_requests=stats['success'],
        duration=actual_duration,
        failed_requests=stats['failed']
    )
    
    p = result.percentiles()
    print(f"[{name}] Done! Throughput: {result.throughput:.2f} req/sec")
    print(f"[{name}] Latency - p50: {p['p50']:.2f}ms | p90: {p['p90']:.2f}ms | p99: {p['p99']:.2f}ms")
    
    return result

def plot_results(results: List[BenchmarkResult]):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Plot 1: Throughput
    names = [r.name for r in results]
    throughputs = [r.throughput for r in results]
    bars = ax1.bar(names, throughputs, color=['#3498db', '#e74c3c'])
    ax1.set_title("Throughput Comparison (Requests/sec)", fontsize=14)
    ax1.set_ylabel("Requests / sec")
    ax1.bar_label(bars, fmt='%.1f')

    # Plot 2: Latency Percentiles
    x = np.arange(len(names))
    width = 0.2
    
    p50s = [r.percentiles()['p50'] for r in results]
    p90s = [r.percentiles()['p90'] for r in results]
    p95s = [r.percentiles()['p95'] for r in results]
    p99s = [r.percentiles()['p99'] for r in results]

    ax2.bar(x - 1.5*width, p50s, width, label='p50', color='#2ecc71')
    ax2.bar(x - 0.5*width, p90s, width, label='p90', color='#f1c40f')
    ax2.bar(x + 0.5*width, p95s, width, label='p95', color='#e67e22')
    ax2.bar(x + 1.5*width, p99s, width, label='p99', color='#c0392b')

    ax2.set_title("Latency Percentiles (ms)", fontsize=14)
    ax2.set_ylabel("Latency (ms)")
    ax2.set_xticks(x)
    ax2.set_xticklabels(names)
    ax2.legend()
    
    plt.tight_layout()
    plt.savefig("benchmark_comparison.png", dpi=300)
    print("\n[+] Created configuration charts: benchmark_comparison.png")

def interpret_results(graphql_res: BenchmarkResult, grpc_res: BenchmarkResult):
    print("\n" + "="*50)
    print("📈 BENCHMARK INTERPRETATION")
    print("="*50)
    
    # Throughput Analysis
    tp_diff = abs(graphql_res.throughput - grpc_res.throughput)
    tp_percent = (tp_diff / max(graphql_res.throughput, grpc_res.throughput)) * 100
    winner_tp = graphql_res.name if graphql_res.throughput > grpc_res.throughput else grpc_res.name
    
    print(f"\n1. Throughput (Capacity):")
    print(f"- {winner_tp} achieved higher throughput by {tp_percent:.1f}%.")
    if grpc_res.throughput > graphql_res.throughput:
        print("- INTERPRETATION: As expected in typical microservice architectures, gRPC provides higher raw throughput due to HTTP/2 multiplexing, lighter binary framing (Protobuf), and reduced serialization/deserialization CPU overhead compared to JSON over HTTP.")
    else:
        print("- INTERPRETATION: Interestingly, WunderGraph achieved comparable or higher throughput. This might be due to optimized Go-based request batching or a scenario where JSON parsing isn't the primary bottleneck.")

    # Latency Analysis
    gql_p99 = graphql_res.percentiles()['p99']
    grpc_p99 = grpc_res.percentiles()['p99']
    winner_lat = graphql_res.name if gql_p99 < grpc_p99 else grpc_res.name
    
    print(f"\n2. Tail Latency (p99):")
    print(f"- {graphql_res.name} p99: {gql_p99:.2f} ms")
    print(f"- {grpc_res.name} p99: {grpc_p99:.2f} ms")
    print(f"- {winner_lat} had better tail latency.")
    
    print("- INTERPRETATION: While p50 represents typical user experience, p99 is crucial for backend systems (handling outlier complex queries).")
    print("  gRPC traditionally excels here by avoiding large JSON payload parsing garbage collection pauses.")
    print("  WunderGraph federation introduces an extra routing/aggregation hop, which typically adds slight latency, but its caching layer often rescues repeated queries.")
    
    print("\n3. Production Context Caveats:")
    print("- Payload size: If you transport large arrays, Protobuf's binary nature outclasses HTTP JSON.")
    print("- Browser usage: WunderGraph handles cross-origin and web-friendly JSON out of the box, whereas gRPC requires grpc-web proxies.")
    print("- Recommendation: Use gRPC for high-intensity internal server-to-server traffic. Use WunderGraph if aggregating multiple external APIs to frontend applications.")
    print("="*50 + "\n")

async def main():
    # Production Configuration Parameters
    CONCURRENCY = 100        # Simulates 100 parallel active connections
    DURATION = 10.0          # 10 seconds sustained load
    WARMUP_DURATION = 3.0    # JIT compiler warming up, connection pooling establishing

    # =========================================================================
    # CONFIGURE REAL TARGETS HERE
    # =========================================================================
    target_graphql = GraphQLTarget("http://localhost:3002/graphql")
    target_grpc = GrpcTarget("localhost:50051")
    
    print("Starting automated benchmark scenario using Docker Compose services...")
    res_graphql = await run_load_test("WunderGraph (GraphQL)", target_graphql, CONCURRENCY, DURATION, WARMUP_DURATION)
    res_grpc = await run_load_test("gRPC (Direct)", target_grpc, CONCURRENCY, DURATION, WARMUP_DURATION)

    # 1. Generate Figures
    plot_results([res_graphql, res_grpc])
    
    # 2. Provide Interpretations
    interpret_results(res_graphql, res_grpc)

if __name__ == "__main__":
    asyncio.run(main())