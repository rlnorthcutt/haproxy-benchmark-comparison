# Nginx vs. HAProxy vs. Traefik: Performance

This repository now includes a fully automated, self-contained benchmark so you
can reproduce the original load-balancer comparison with a single command. The
setup uses Docker Compose to run two instances of the Rust `ntex` demo
application, PostgreSQL, and three load balancers (Nginx, HAProxy, Traefik).
A lightweight Python harness then drives synthetic traffic against each load
balancer and prints a consolidated summary.

## Requirements

* Docker (23+) and Docker Compose plugin
* Python 3.11+

## Quick start

```bash
./scripts/run-benchmark.sh
```

The script will:

1. Build and launch the entire stack with Docker Compose.
2. Wait for each load balancer to become healthy (`/healthz`).
3. Execute the benchmark defined in `benchmark/config.toml`.
4. Tear down the containers when the test finishes.

Use `./scripts/run-benchmark.sh --no-teardown` to keep the containers running
after the test. All additional arguments are forwarded to
`scripts/benchmark.py`, so you can run a dry run without sending traffic:

```bash
./scripts/run-benchmark.sh --no-teardown -- --dry-run
```

## Live monitoring with Grafana and Prometheus

When the stack is running you can explore live metrics for each load balancer
using the bundled Prometheus and Grafana services:

* **Prometheus** – <http://localhost:9090>
* **Grafana** – <http://localhost:3000> (default credentials `admin/admin`)

Grafana automatically provisions the included dashboard at
`dashboards/Load Balancer Benchmark`, which visualises CPU, memory, request
rate, and connection metrics for Nginx, HAProxy, and Traefik. The dashboard is
fed by:

* `nginx/nginx-prometheus-exporter` scraping the Nginx `stub_status` endpoint.
* HAProxy's built-in Prometheus exporter (exposed on port `8404`).
* Traefik's native Prometheus metrics endpoint.
* `cAdvisor` for container-level CPU and memory usage.

Keep the containers alive with `--no-teardown` while the benchmark runs, then
open Grafana to watch the traffic in real time or to analyse a completed run.

## Customising the benchmark

Update `benchmark/config.toml` to tweak the concurrency ramp, timing, or target
URLs. The file uses the same fields as the original `Tester.toml` Kubernetes
ConfigMap and supports multiple `[[targets]]` entries.

## Troubleshooting

* **Permission denied running the script** – ensure it is executable:
  `chmod +x scripts/run-benchmark.sh`.
* **Docker Compose command differs** – override via
  `COMPOSE_BIN="docker-compose" ./scripts/run-benchmark.sh`.

You can find the original tutorial [here](https://youtu.be/h-ygQbBROXY).
