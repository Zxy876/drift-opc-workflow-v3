#!/usr/bin/env bash
set -euo pipefail

LOG_PREFIX="[drift-healthcheck]"
FAILED=0

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

check_service "drift-backend.service" "http://127.0.0.1:8000/levels"
check_service "drift-asyncaiflow.service" "http://127.0.0.1:8080/workflows?page=0&size=1"
check_service "drift-panel.service" "http://127.0.0.1:8888/"
check_port "drift-minecraft.service" 25565

for worker in drift_trigger drift_web_search drift_plan drift_code drift_review drift_test drift_deploy drift_git_push drift_refresh drift_experience; do
  SVC="drift-python-worker@${worker}.service"
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
