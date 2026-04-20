#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
INSTALL_ROOT="/opt/drift-stack-systemd"
ENV_FILE="/etc/drift-stack.env"
UNIT_DIR="/etc/systemd/system"

detect_dir() {
  local pattern="$1"
  find "${WORKSPACE_ROOT}" -maxdepth 1 -type d -name "${pattern}" | head -n 1
}

if [[ ! -f "${ENV_FILE}" ]]; then
  ASYNC_ROOT_DETECTED="$(detect_dir '*AsyncAIFlow_4.8')"
  DRIFT_ROOT_DETECTED="$(detect_dir 'drift-system-clean*')"

  if [[ -z "${ASYNC_ROOT_DETECTED}" || -z "${DRIFT_ROOT_DETECTED}" ]]; then
    echo "Failed to auto-detect AsyncAIFlow or Drift root under ${WORKSPACE_ROOT}" >&2
    exit 1
  fi

  PYTHON_BIN="$(command -v python3)"
  if [[ -x "${ASYNC_ROOT_DETECTED}/.venv/bin/python3" ]]; then
    PYTHON_BIN="${ASYNC_ROOT_DETECTED}/.venv/bin/python3"
  fi

  cat > "${ENV_FILE}" <<EOF
STACK_USER=$(logname 2>/dev/null || echo "${SUDO_USER:-root}")
WORKSPACE_ROOT="${WORKSPACE_ROOT}"
ASYNC_ROOT="${ASYNC_ROOT_DETECTED}"
DRIFT_ROOT="${DRIFT_ROOT_DETECTED}"
AIFLOW_JAR="${ASYNC_ROOT_DETECTED}/target/asyncaiflow-0.1.0-SNAPSHOT.jar"
AIFLOW_URL=http://127.0.0.1:8080
ASYNCAIFLOW_URL=http://127.0.0.1:8080
DRIFT_URL=http://127.0.0.1:8000
DRIFT_BACKEND_HOST=0.0.0.0
DRIFT_BACKEND_PORT=8000
DRIFT_BACKEND_VENV="${DRIFT_ROOT_DETECTED}/backend/venv"
PYTHON_WORKER_PYTHON="${PYTHON_BIN}"
DRIFT_PATCH_DIR="${ASYNC_ROOT_DETECTED}/tmp/drift_patches"
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=gpt-4o
GLM_API_KEY=
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
GLM_MODEL=glm-4
GLM_BASE_URL_CODING=https://open.bigmodel.cn/api/coding/paas/v4
GLM_MODEL_CODING=codegeex-4
DEEPSEEK_API_KEY=
GPT_MOCK_FALLBACK_ENABLED=false
ENABLE_DRIFT_MINECRAFT=false
MINECRAFT_START_SCRIPT="${DRIFT_ROOT_DETECTED}/backend/start_mc.sh"
EOF
  chmod 0644 "${ENV_FILE}"
  echo "Wrote ${ENV_FILE} with detected defaults. Review secrets before production use."
fi

mkdir -p "${INSTALL_ROOT}/bin"

install -m 0755 "${SCRIPT_DIR}/bin/run-asyncaiflow-runtime.sh" "${INSTALL_ROOT}/bin/run-asyncaiflow-runtime.sh"
install -m 0755 "${SCRIPT_DIR}/bin/run-drift-backend.sh" "${INSTALL_ROOT}/bin/run-drift-backend.sh"
install -m 0755 "${SCRIPT_DIR}/bin/run-java-worker.sh" "${INSTALL_ROOT}/bin/run-java-worker.sh"
install -m 0755 "${SCRIPT_DIR}/bin/run-python-worker.sh" "${INSTALL_ROOT}/bin/run-python-worker.sh"
install -m 0755 "${SCRIPT_DIR}/bin/run-minecraft.sh" "${INSTALL_ROOT}/bin/run-minecraft.sh"
install -m 0755 "${SCRIPT_DIR}/bin/run-drift-panel.sh" "${INSTALL_ROOT}/bin/run-drift-panel.sh"
install -m 0755 "${SCRIPT_DIR}/bin/drift-healthcheck.sh" "${INSTALL_ROOT}/bin/drift-healthcheck.sh"
install -m 0755 "${SCRIPT_DIR}/bin/drift-auto-update.sh" "${INSTALL_ROOT}/bin/drift-auto-update.sh"

STACK_USER_VALUE="$(grep '^STACK_USER=' "${ENV_FILE}" | head -n 1 | cut -d= -f2-)"
if [[ -z "${STACK_USER_VALUE}" ]]; then
  echo "STACK_USER is missing in ${ENV_FILE}" >&2
  exit 1
fi

sed "s/__STACK_USER__/${STACK_USER_VALUE}/g" "${SCRIPT_DIR}/units/drift-asyncaiflow.service" > "${UNIT_DIR}/drift-asyncaiflow.service"
sed "s/__STACK_USER__/${STACK_USER_VALUE}/g" "${SCRIPT_DIR}/units/drift-backend.service" > "${UNIT_DIR}/drift-backend.service"
sed "s/__STACK_USER__/${STACK_USER_VALUE}/g" "${SCRIPT_DIR}/units/drift-java-worker@.service" > "${UNIT_DIR}/drift-java-worker@.service"
sed "s/__STACK_USER__/${STACK_USER_VALUE}/g" "${SCRIPT_DIR}/units/drift-python-worker@.service" > "${UNIT_DIR}/drift-python-worker@.service"
sed "s/__STACK_USER__/${STACK_USER_VALUE}/g" "${SCRIPT_DIR}/units/drift-minecraft.service" > "${UNIT_DIR}/drift-minecraft.service"
sed "s/__STACK_USER__/${STACK_USER_VALUE}/g" "${SCRIPT_DIR}/units/drift-panel.service" > "${UNIT_DIR}/drift-panel.service"
sed "s/__STACK_USER__/${STACK_USER_VALUE}/g" "${SCRIPT_DIR}/units/drift-auto-update.service" > "${UNIT_DIR}/drift-auto-update.service"
chmod 0644 "${UNIT_DIR}/drift-asyncaiflow.service" "${UNIT_DIR}/drift-backend.service" \
  "${UNIT_DIR}/drift-java-worker@.service" "${UNIT_DIR}/drift-python-worker@.service" \
  "${UNIT_DIR}/drift-minecraft.service" "${UNIT_DIR}/drift-panel.service" \
  "${UNIT_DIR}/drift-auto-update.service"
install -m 0644 "${SCRIPT_DIR}/units/drift-stack.target" "${UNIT_DIR}/drift-stack.target"
install -m 0644 "${SCRIPT_DIR}/units/drift-healthcheck.service" "${UNIT_DIR}/drift-healthcheck.service"
install -m 0644 "${SCRIPT_DIR}/units/drift-healthcheck.timer" "${UNIT_DIR}/drift-healthcheck.timer"
install -m 0644 "${SCRIPT_DIR}/units/drift-auto-update.timer" "${UNIT_DIR}/drift-auto-update.timer"

systemctl daemon-reload
systemctl enable drift-stack.target
systemctl enable --now drift-healthcheck.timer
systemctl enable --now drift-auto-update.timer

if grep -q '^ENABLE_DRIFT_MINECRAFT=true$' "${ENV_FILE}"; then
  systemctl enable drift-minecraft.service
fi

systemctl restart drift-asyncaiflow.service || systemctl start drift-asyncaiflow.service
systemctl restart drift-backend.service || systemctl start drift-backend.service
systemctl restart drift-java-worker@repository.service || systemctl start drift-java-worker@repository.service
systemctl restart drift-java-worker@gpt.service || systemctl start drift-java-worker@gpt.service
systemctl restart drift-java-worker@git.service || systemctl start drift-java-worker@git.service
systemctl restart drift-python-worker@drift_trigger.service || systemctl start drift-python-worker@drift_trigger.service
systemctl restart drift-python-worker@drift_web_search.service || systemctl start drift-python-worker@drift_web_search.service
systemctl restart drift-python-worker@drift_plan.service || systemctl start drift-python-worker@drift_plan.service
systemctl restart drift-python-worker@drift_code.service || systemctl start drift-python-worker@drift_code.service
systemctl restart drift-python-worker@drift_review.service || systemctl start drift-python-worker@drift_review.service
systemctl restart drift-python-worker@drift_test.service || systemctl start drift-python-worker@drift_test.service
systemctl restart drift-python-worker@drift_deploy.service || systemctl start drift-python-worker@drift_deploy.service
systemctl restart drift-python-worker@drift_git_push.service || systemctl start drift-python-worker@drift_git_push.service
systemctl restart drift-python-worker@drift_refresh.service || systemctl start drift-python-worker@drift_refresh.service
systemctl restart drift-python-worker@drift_experience.service || systemctl start drift-python-worker@drift_experience.service
systemctl restart drift-panel.service || systemctl start drift-panel.service

if grep -q '^ENABLE_DRIFT_MINECRAFT=true$' "${ENV_FILE}"; then
  systemctl restart drift-minecraft.service || systemctl start drift-minecraft.service
fi

echo
echo "Installed systemd units. Useful commands:"
echo "  systemctl status drift-asyncaiflow.service"
echo "  systemctl status drift-backend.service"
echo "  systemctl status drift-python-worker@drift_code.service"
echo "  systemctl status drift-panel.service"
echo "  systemctl status drift-healthcheck.timer"
echo "  systemctl status drift-auto-update.timer"
echo "  journalctl -u drift-asyncaiflow.service -f"
echo "  journalctl -u drift-python-worker@drift_code.service -f"