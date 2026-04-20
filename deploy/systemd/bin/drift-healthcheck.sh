#!/usr/bin/env bash
set -euo pipefail

LOG_PREFIX="[drift-healthcheck]"
FAILED=0

service_uptime_seconds() {
  local name="$1"
  local start_mono_us now_mono_us

  start_mono_us="$(systemctl show -p ExecMainStartTimestampMonotonic --value "$name" 2>/dev/null || true)"
  if [[ -z "$start_mono_us" || "$start_mono_us" == "0" ]]; then
    start_mono_us="$(systemctl show -p ActiveEnterTimestampMonotonic --value "$name" 2>/dev/null || true)"
  fi

  if [[ -z "$start_mono_us" || "$start_mono_us" == "0" ]]; then
    echo 0
    return
  fi

  now_mono_us="$(awk '{printf "%.0f", $1 * 1000000}' /proc/uptime)"
  if [[ -z "$now_mono_us" || "$now_mono_us" -le "$start_mono_us" ]]; then
    echo 0
    return
  fi

  awk -v now_us="$now_mono_us" -v start_us="$start_mono_us" 'BEGIN { printf "%d", (now_us - start_us) / 1000000 }'
}

run_systemctl() {
  if systemctl "$@" >/dev/null 2>&1; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo -n systemctl "$@"
    return $?
  fi
  return 1
}

check_service() {
  local name="$1"
  local url="$2"
  local timeout="${3:-10}"
  local min_uptime="${4:-0}"

  local uptime
  uptime="$(service_uptime_seconds "$name")"
  if [[ "$uptime" -lt "$min_uptime" ]]; then
    echo "$LOG_PREFIX WARMUP: $name uptime=${uptime}s < ${min_uptime}s, skip probe"
    return
  fi

  if ! curl -sf -m "$timeout" "$url" >/dev/null 2>&1; then
    echo "$LOG_PREFIX FAIL: $name ($url) - restarting..."
    if run_systemctl restart "$name"; then
      FAILED=$((FAILED + 1))
      sleep 5
    else
      echo "$LOG_PREFIX WARN: unable to restart $name (insufficient privileges?)"
      FAILED=$((FAILED + 1))
    fi
  else
    echo "$LOG_PREFIX OK: $name"
  fi
}

check_port() {
  local name="$1"
  local port="$2"
  local min_uptime="${3:-0}"

  local uptime
  uptime="$(service_uptime_seconds "$name")"
  if [[ "$uptime" -lt "$min_uptime" ]]; then
    echo "$LOG_PREFIX WARMUP: $name uptime=${uptime}s < ${min_uptime}s, skip port check"
    return
  fi

  if ! ss -tlnp | grep -q ":${port} "; then
    echo "$LOG_PREFIX FAIL: $name (port $port not listening) - restarting..."
    if run_systemctl restart "$name"; then
      FAILED=$((FAILED + 1))
      sleep 10
    else
      echo "$LOG_PREFIX WARN: unable to restart $name (insufficient privileges?)"
      FAILED=$((FAILED + 1))
    fi
  else
    echo "$LOG_PREFIX OK: $name (port $port)"
  fi
}

check_service "drift-backend.service" "http://127.0.0.1:8000/levels" 10 30
check_service "drift-asyncaiflow.service" "http://127.0.0.1:8080/workflows?page=0&size=1" 10 150
check_service "drift-panel.service" "http://127.0.0.1:8888/" 10 20
check_port "drift-minecraft.service" 25565 180

for worker in drift_trigger drift_web_search drift_plan drift_code drift_review drift_test drift_deploy drift_git_push drift_refresh drift_experience; do
  SVC="drift-python-worker@${worker}.service"
  if ! systemctl is-enabled --quiet "$SVC" 2>/dev/null && ! systemctl is-active --quiet "$SVC" 2>/dev/null; then
    echo "$LOG_PREFIX SKIP: $SVC is disabled"
    continue
  fi
  if ! systemctl is-active --quiet "$SVC" 2>/dev/null; then
    echo "$LOG_PREFIX FAIL: $SVC - restarting..."
    if run_systemctl restart "$SVC"; then
      FAILED=$((FAILED + 1))
    else
      echo "$LOG_PREFIX WARN: unable to restart $SVC (insufficient privileges?)"
      FAILED=$((FAILED + 1))
    fi
  fi
done

for worker in repository gpt git; do
  SVC="drift-java-worker@${worker}.service"
  if ! systemctl is-enabled --quiet "$SVC" 2>/dev/null && ! systemctl is-active --quiet "$SVC" 2>/dev/null; then
    echo "$LOG_PREFIX SKIP: $SVC is disabled"
    continue
  fi
  if ! systemctl is-active --quiet "$SVC" 2>/dev/null; then
    echo "$LOG_PREFIX FAIL: $SVC - restarting..."
    if run_systemctl restart "$SVC"; then
      FAILED=$((FAILED + 1))
    else
      echo "$LOG_PREFIX WARN: unable to restart $SVC (insufficient privileges?)"
      FAILED=$((FAILED + 1))
    fi
  fi
done

if [[ "$FAILED" -gt 0 ]]; then
  echo "$LOG_PREFIX Recovered $FAILED services"
  exit 1
else
  echo "$LOG_PREFIX All services healthy"
fi
