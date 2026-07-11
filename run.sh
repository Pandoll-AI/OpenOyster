#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_BIN="$ROOT_DIR/.venv/bin/openoyster"
HOST="0.0.0.0"
PORT="3388"
SLEEP_SECONDS="30"
LOG_DIR="$ROOT_DIR/workspace/logs"
SERVE_LOG="$LOG_DIR/openoyster-serve.log"
WORKER_LOG="$LOG_DIR/openoyster-worker.log"

usage() {
  printf 'Usage: %s {start|stop|restart}\n' "$0" >&2
}

kill_processes() {
  for pid in "$@"; do
    if [ "$pid" != "$$" ]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}

stop() {
  # Only kill processes on the port that are actually openoyster —
  # a foreign daemon squatting on the port must never be collateral.
  local port_pids openoyster_port_pids=""
  port_pids="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
  for pid in $port_pids; do
    if ps -p "$pid" -o command= 2>/dev/null | grep -q "openoyster"; then
      openoyster_port_pids="$openoyster_port_pids $pid"
    fi
  done
  if [ -n "$openoyster_port_pids" ]; then
    kill_processes $openoyster_port_pids
  fi

  local serve_pids
  serve_pids="$(pgrep -f '(^|/| )openoyster serve( |$)' 2>/dev/null || true)"
  if [ -n "$serve_pids" ]; then
    kill_processes $serve_pids
  fi

  local worker_pids
  worker_pids="$(pgrep -f '(^|/| )openoyster run( |$)' 2>/dev/null || true)"
  if [ -n "$worker_pids" ]; then
    kill_processes $worker_pids
  fi
}

start() {
  if [ ! -x "$APP_BIN" ]; then
    printf 'Missing executable: %s\n' "$APP_BIN" >&2
    exit 1
  fi

  stop
  mkdir -p "$LOG_DIR"

  nohup "$APP_BIN" serve --host "$HOST" --port "$PORT" >"$SERVE_LOG" 2>&1 &
  printf '%s\n' "$!" >"$LOG_DIR/openoyster-serve.pid"

  nohup "$APP_BIN" run --forever --sleep "$SLEEP_SECONDS" >"$WORKER_LOG" 2>&1 &
  printf '%s\n' "$!" >"$LOG_DIR/openoyster-worker.pid"

  printf 'OpenOyster API: http://127.0.0.1:%s\n' "$PORT"
  local tailscale_ip
  tailscale_ip="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
  if [ -n "$tailscale_ip" ]; then
    printf 'OpenOyster Tailscale API: http://%s:%s\n' "$tailscale_ip" "$PORT"
  fi
  printf 'Logs: %s\n' "$LOG_DIR"
}

case "${1:-}" in
  start)
    start
    ;;
  stop)
    stop
    ;;
  restart)
    start
    ;;
  *)
    usage
    exit 2
    ;;
esac
