#!/usr/bin/env bash
set -euo pipefail

readonly LOCK_FILE="/run/lock/gpt-analyzer-healthcheck.lock"
readonly STATE_DIR="/run/gpt-analyzer-healthcheck"
readonly RESTART_COOLDOWN_SECONDS=900
readonly CONTAINER_HEALTH_ATTEMPTS=30
readonly SERVICE_HEALTH_ATTEMPTS=15
readonly HEALTH_INTERVAL_SECONDS=2

log() {
  echo "[$(date -Is)] $*"
}

if ! command -v flock >/dev/null 2>&1; then
  log "flock is required; refusing to run without a single-instance lock"
  exit 1
fi

# The timer runs every five minutes. Never let a slow recovery overlap with the
# next invocation, especially while Docker is under memory pressure.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another healthcheck is still running; skipping this invocation"
  exit 0
fi

if ! mkdir -p "$STATE_DIR"; then
  log "cannot create recovery state directory $STATE_DIR"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  log "Docker daemon is unavailable; container recovery cannot proceed"
  exit 1
fi

container_status() {
  docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null || echo "missing"
}

container_health() {
  docker inspect -f \
    '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    "$1" 2>/dev/null || echo "missing"
}

recovery_allowed() {
  local name="$1"
  local stamp_file="$STATE_DIR/$name.last-recovery"
  local last_recovery=""
  local now elapsed remaining

  if [ -r "$stamp_file" ]; then
    read -r last_recovery < "$stamp_file" || last_recovery=""
  fi

  if [[ "$last_recovery" =~ ^[0-9]+$ ]]; then
    now=$(date +%s)
    elapsed=$((now - last_recovery))
    if [ "$elapsed" -lt 0 ]; then
      elapsed=0
    fi
    if [ "$elapsed" -lt "$RESTART_COOLDOWN_SECONDS" ]; then
      remaining=$((RESTART_COOLDOWN_SECONDS - elapsed))
      log "recovery for $name is in cooldown for another ${remaining}s"
      return 1
    fi
  fi

  return 0
}

recover_container() {
  local name="$1"
  local action="$2"
  local stamp_file="$STATE_DIR/$name.last-recovery"

  if ! recovery_allowed "$name"; then
    return 1
  fi

  # Record the attempt before calling Docker. A daemon already under pressure
  # must not receive the same expensive recovery request every five minutes.
  if ! date +%s > "$stamp_file"; then
    log "cannot record recovery cooldown for $name"
    return 1
  fi

  log "${action}ing $name"
  if ! docker "$action" "$name" >/dev/null; then
    log "docker $action failed for $name"
    return 1
  fi
}

ensure_running() {
  local name="$1"
  local status

  status=$(container_status "$name")
  case "$status" in
    running)
      return 0
      ;;
    restarting)
      log "$name is already restarting; waiting for it to settle"
      return 0
      ;;
    missing)
      log "container $name is missing"
      return 1
      ;;
    *)
      log "container $name is not running; current status=$status"
      recover_container "$name" start
      ;;
  esac
}

wait_container_healthy() {
  local name="$1"
  local status="unknown"
  local attempt

  for ((attempt = 1; attempt <= CONTAINER_HEALTH_ATTEMPTS; attempt++)); do
    status=$(container_health "$name")
    if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
      return 0
    fi
    sleep "$HEALTH_INTERVAL_SECONDS"
  done

  log "container $name did not become healthy; status=$status"
  return 1
}

ensure_container_healthy() {
  local name="$1"

  if ! ensure_running "$name"; then
    return 1
  fi
  if wait_container_healthy "$name"; then
    return 0
  fi

  if ! recover_container "$name" restart; then
    return 1
  fi
  if ! wait_container_healthy "$name"; then
    log "$name is still unhealthy after restart"
    return 1
  fi
  return 0
}

probe_gpt() {
  docker exec gpt-analyzer-server python -c \
    'from urllib.request import urlopen; raise SystemExit(0 if urlopen("http://127.0.0.1:8095/gpt-api/health", timeout=5).status == 200 else 1)' \
    >/dev/null 2>&1
}

probe_sub() {
  curl -fsS --connect-timeout 2 --max-time 5 \
    http://127.0.0.1:8080/health >/dev/null 2>&1
}

wait_service_healthy() {
  local name="$1"
  local probe="$2"
  local attempt

  for ((attempt = 1; attempt <= SERVICE_HEALTH_ATTEMPTS; attempt++)); do
    if "$probe"; then
      return 0
    fi
    sleep "$HEALTH_INTERVAL_SECONDS"
  done

  log "$name application healthcheck did not pass"
  return 1
}

ensure_service_healthy() {
  local name="$1"
  local probe="$2"

  if ! ensure_running "$name"; then
    return 1
  fi
  if wait_service_healthy "$name" "$probe"; then
    return 0
  fi

  if ! recover_container "$name" restart; then
    return 1
  fi
  if ! wait_service_healthy "$name" "$probe"; then
    log "$name is still unhealthy after restart"
    return 1
  fi
  return 0
}

# Docker daemon recovery can leave unless-stopped containers marked as manually
# stopped. Recover dependencies serially so a 1.8 GB host does not start the
# entire stack at once. sub2api must never be started while a dependency is bad.
for dependency in mihomo-sub2 sub2api-postgres sub2api-redis; do
  if ! ensure_container_healthy "$dependency"; then
    log "dependency $dependency is unavailable; refusing to start or restart sub2api"
    exit 1
  fi
done

if ! ensure_service_healthy sub2api probe_sub; then
  log "sub2api recovery failed"
  exit 1
fi

if ! ensure_service_healthy gpt-analyzer-server probe_gpt; then
  log "gpt-analyzer-server recovery failed"
  exit 1
fi

if ! ensure_container_healthy note-ledger-caddy; then
  log "note-ledger-caddy recovery failed"
  exit 1
fi

log "all managed services are healthy"
