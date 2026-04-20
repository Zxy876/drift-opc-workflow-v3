#!/usr/bin/env bash
set -euo pipefail

source /etc/drift-stack.env

LOG_PREFIX="[drift-auto-update]"

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

cd "${WORKSPACE_ROOT}"

LOCAL_HEAD="$(git rev-parse HEAD)"
git fetch origin main --quiet
REMOTE_HEAD="$(git rev-parse origin/main)"

if [[ "${LOCAL_HEAD}" == "${REMOTE_HEAD}" ]]; then
  echo "$LOG_PREFIX No updates"
  exit 0
fi

echo "$LOG_PREFIX Updating: ${LOCAL_HEAD:0:8} -> ${REMOTE_HEAD:0:8}"
git pull --ff-only origin main --quiet

if git diff --name-only "${LOCAL_HEAD}" "${REMOTE_HEAD}" | grep -q '^drift-system_4.8/backend/'; then
  echo "$LOG_PREFIX Backend changed - restarting drift-backend.service..."
  run_systemctl restart drift-backend.service || echo "$LOG_PREFIX WARN: failed to restart drift-backend.service"
fi

if git diff --name-only "${LOCAL_HEAD}" "${REMOTE_HEAD}" | grep -qE '^(AsyncAIFlow|drift-system).*/worker'; then
  echo "$LOG_PREFIX Workers changed - restarting all workers..."
  for worker in drift_trigger drift_web_search drift_plan drift_code drift_review drift_test drift_deploy drift_git_push drift_refresh drift_experience; do
    run_systemctl restart "drift-python-worker@${worker}.service" || echo "$LOG_PREFIX WARN: failed to restart drift-python-worker@${worker}.service"
  done
  for worker in repository gpt git; do
    run_systemctl restart "drift-java-worker@${worker}.service" || echo "$LOG_PREFIX WARN: failed to restart drift-java-worker@${worker}.service"
  done
fi

if git diff --name-only "${LOCAL_HEAD}" "${REMOTE_HEAD}" | grep -q 'drift-experience-panel'; then
  echo "$LOG_PREFIX Panel changed - restarting drift-panel.service..."
  run_systemctl restart drift-panel.service || echo "$LOG_PREFIX WARN: failed to restart drift-panel.service"
fi

echo "$LOG_PREFIX Update complete. Current HEAD: $(git rev-parse --short HEAD)"
