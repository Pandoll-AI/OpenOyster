#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_BIN="$ROOT_DIR/.venv/bin/openoyster"
HOST="0.0.0.0"
PORT="3377"
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
  local port_pids
  port_pids="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
  if [ -n "$port_pids" ]; then
    kill_processes $port_pids
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
