#!/usr/bin/env bash
set -euo pipefail

COMPOSE_BIN=${COMPOSE_BIN:-docker compose}
CONFIG_FILE=""
TEARDOWN=1

usage() {
  cat <<USAGE
Usage: $0 [--config PATH] [--no-teardown] [--compose CMD]

Options:
  --config PATH     Path to the benchmark configuration file (default: benchmark/config.toml)
  --no-teardown     Leave the docker compose stack running after the benchmark finishes
  --compose CMD     Override the docker compose command (default: "docker compose")
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    --no-teardown)
      TEARDOWN=0
      shift
      ;;
    --compose)
      COMPOSE_BIN="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

cleanup() {
  if [[ $TEARDOWN -eq 1 ]]; then
    $COMPOSE_BIN down --remove-orphans
  fi
}

trap cleanup EXIT

$COMPOSE_BIN up -d --build

wait_for() {
  local name="$1"
  local url="$2"
  echo "Waiting for $name at $url"
  for _ in {1..120}; do
    if curl -fsS "$url" > /dev/null 2>&1; then
      echo "$name is ready"
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for $name" >&2
  exit 1
}

wait_for "nginx" "http://localhost:8081/healthz"
wait_for "haproxy" "http://localhost:8082/healthz"
wait_for "traefik" "http://localhost:8083/healthz"

PY_ARGS=(scripts/benchmark.py)
if [[ -n $CONFIG_FILE ]]; then
  PY_ARGS+=(--config "$CONFIG_FILE")
fi
PY_ARGS+=("$@")

python3 "${PY_ARGS[@]}"
