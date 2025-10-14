#!/usr/bin/env python3
"""Run a synthetic load test against multiple load balancers.

The script mimics the configuration used by the original Kubernetes job
(`Tester.toml`) but in a self-contained manner so it can be executed locally.
"""
from __future__ import annotations

import argparse
import math
import statistics
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection, HTTPException
from pathlib import Path
from typing import Dict, List, Tuple

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - should never happen
    raise SystemExit("Python 3.11 or newer is required to run the benchmark") from exc


@dataclass(frozen=True)
class StageConfig:
    concurrency: int
    duration: float


@dataclass(frozen=True)
class TestConfig:
    method: str
    protocol: str
    min_clients: int
    max_clients: int
    stage_interval_s: float
    request_delay_ms: float
    request_timeout_ms: float
    stage_count: int

    @property
    def timeout_seconds(self) -> float:
        return self.request_timeout_ms / 1000.0

    @property
    def delay_seconds(self) -> float:
        return self.request_delay_ms / 1000.0

    def stages(self) -> List[StageConfig]:
        if self.stage_count <= 1:
            return [StageConfig(self.max_clients, self.stage_interval_s)]

        step = (self.max_clients - self.min_clients) / (self.stage_count - 1)
        stages: List[StageConfig] = []
        for i in range(self.stage_count):
            concurrency = int(round(self.min_clients + (step * i)))
            concurrency = max(1, concurrency)
            stages.append(StageConfig(concurrency, self.stage_interval_s))
        return stages


@dataclass(frozen=True)
class Target:
    name: str
    url: str


@dataclass
class StageResult:
    target: Target
    stage: StageConfig
    successes: int
    failures: int
    latencies: List[float]

    @property
    def total_requests(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.successes / self.total_requests

    @property
    def rps(self) -> float:
        if self.stage.duration == 0:
            return 0.0
        return self.successes / self.stage.duration

    @property
    def latency_stats(self) -> Dict[str, float]:
        if not self.latencies:
            return {"p50": 0.0, "p90": 0.0, "p99": 0.0, "avg": 0.0}

        sorted_lat = sorted(self.latencies)
        return {
            "p50": percentile(sorted_lat, 0.50),
            "p90": percentile(sorted_lat, 0.90),
            "p99": percentile(sorted_lat, 0.99),
            "avg": statistics.fmean(sorted_lat),
        }


@dataclass
class BenchmarkResult:
    target: Target
    stage_results: List[StageResult]

    def summary(self) -> Dict[str, object]:
        return {
            "target": self.target.name,
            "stages": [
                {
                    "concurrency": result.stage.concurrency,
                    "duration_s": result.stage.duration,
                    "requests": result.total_requests,
                    "success_rate": round(result.success_rate * 100, 2),
                    "success_rps": round(result.rps, 2),
                    "latency_ms": {
                        key: round(value * 1000, 2)
                        for key, value in result.latency_stats.items()
                    },
                }
                for result in self.stage_results
            ],
        }


@dataclass
class RequestTarget:
    target: Target
    parsed: urllib.parse.SplitResult

    def connection(self) -> HTTPConnection:
        host = self.parsed.hostname or "localhost"
        port = self.parsed.port
        if self.parsed.scheme == "https":
            conn = HTTPSConnection(host, port, timeout=10)
        else:
            conn = HTTPConnection(host, port, timeout=10)
        return conn

    @property
    def path(self) -> str:
        path = self.parsed.path or "/"
        if self.parsed.query:
            path = f"{path}?{self.parsed.query}"
        return path


def percentile(data: List[float], pct: float) -> float:
    if not data:
        return 0.0
    idx = (len(data) - 1) * pct
    lower = math.floor(idx)
    upper = math.ceil(idx)
    if lower == upper:
        return data[int(idx)]
    weight = idx - lower
    return data[lower] * (1 - weight) + data[upper] * weight


def load_config(path: Path) -> Tuple[TestConfig, List[Target]]:
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    if "test" not in raw:
        raise ValueError("Configuration file must contain a [test] section")

    test_section = raw["test"]
    test = TestConfig(
        method=test_section.get("request", "GET").upper(),
        protocol=test_section.get("protocol", "http").lower(),
        min_clients=int(test_section.get("min_clients", 1)),
        max_clients=int(test_section.get("max_clients", 1)),
        stage_interval_s=float(test_section.get("stage_interval_s", 10)),
        request_delay_ms=float(test_section.get("request_delay_ms", 0)),
        request_timeout_ms=float(test_section.get("request_timeout_ms", 1000)),
        stage_count=int(test_section.get("stage_count", 5)),
    )

    if test.protocol not in {"http", "https"}:
        raise ValueError("Only http and https protocols are supported")

    targets_raw = raw.get("targets", [])
    if not targets_raw:
        raise ValueError("Configuration file must define at least one [[targets]] entry")

    targets = [Target(entry["name"], entry["url"]) for entry in targets_raw]
    return test, targets


def run_stage(
    request: RequestTarget,
    test: TestConfig,
    stage: StageConfig,
    *,
    delay: float,
    timeout: float,
) -> StageResult:
    lock = threading.Lock()
    successes = 0
    failures = 0
    latencies: List[float] = []
    deadline = time.perf_counter() + stage.duration

    def worker() -> None:
        nonlocal successes, failures
        while True:
            now = time.perf_counter()
            if now >= deadline:
                break
            started = time.perf_counter()
            try:
                conn = request.connection()
                conn.timeout = timeout
                conn.request(test.method, request.path)
                resp = conn.getresponse()
                resp.read()
                conn.close()
                elapsed = time.perf_counter() - started
                if 200 <= resp.status < 500:
                    with lock:
                        successes += 1
                        latencies.append(elapsed)
                else:
                    with lock:
                        failures += 1
            except (OSError, HTTPException):
                with lock:
                    failures += 1
            if delay:
                remaining = delay - (time.perf_counter() - started)
                if remaining > 0:
                    time.sleep(remaining)

    with ThreadPoolExecutor(max_workers=stage.concurrency) as executor:
        futures = [executor.submit(worker) for _ in range(stage.concurrency)]
        for future in futures:
            future.result()

    return StageResult(request.target, stage, successes, failures, latencies)


def run_target(test: TestConfig, target: Target) -> BenchmarkResult:
    parsed = urllib.parse.urlsplit(target.url)
    if parsed.scheme and parsed.scheme != test.protocol:
        raise ValueError(
            f"Target {target.name} uses scheme '{parsed.scheme}' which does not match test protocol "
            f"'{test.protocol}'"
        )
    request = RequestTarget(target, parsed)

    stage_results = []
    for stage in test.stages():
        result = run_stage(
            request,
            test,
            stage,
            delay=test.delay_seconds,
            timeout=test.timeout_seconds,
        )
        stage_results.append(result)
        print(
            f"[{target.name}] concurrency={stage.concurrency} duration={stage.duration:.0f}s "
            f"success={result.successes} fail={result.failures} "
            f"success_rps={result.rps:.2f}"
        )
    return BenchmarkResult(target, stage_results)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local load-balancer benchmark")
    parser.add_argument(
        "--config",
        default="benchmark/config.toml",
        type=Path,
        help="Path to the benchmark configuration file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the execution plan without sending any traffic.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    test, targets = load_config(args.config)

    print("Loaded configuration:")
    print(f"  Method: {test.method}")
    print(f"  Protocol: {test.protocol}")
    print(f"  Concurrency range: {test.min_clients}-{test.max_clients}")
    print(f"  Stage interval: {test.stage_interval_s}s")
    print(f"  Delay between requests: {test.request_delay_ms}ms")
    print(f"  Timeout per request: {test.request_timeout_ms}ms")
    print(f"  Number of stages: {test.stage_count}")
    print("  Targets:")
    for target in targets:
        print(f"    - {target.name}: {target.url}")

    if args.dry_run:
        print("Dry run mode enabled – no requests will be sent.")
        return

    results: List[BenchmarkResult] = []
    for target in targets:
        print(f"\nRunning benchmark for {target.name}...")
        result = run_target(test, target)
        results.append(result)

    print("\nBenchmark summary:")
    for result in results:
        summary = result.summary()
        print(f"- {summary['target']}")
        for stage in summary["stages"]:
            latency = stage["latency_ms"]
            print(
                "  * concurrency={concurrency} requests={requests} success_rate={success_rate:.2f}% "
                "rps={success_rps} latency(p50/p90/p99)={p50}/{p90}/{p99}ms".format(
                    concurrency=stage["concurrency"],
                    requests=stage["requests"],
                    success_rate=stage["success_rate"],
                    success_rps=stage["success_rps"],
                    p50=latency["p50"],
                    p90=latency["p90"],
                    p99=latency["p99"],
                )
            )


if __name__ == "__main__":
    main()
