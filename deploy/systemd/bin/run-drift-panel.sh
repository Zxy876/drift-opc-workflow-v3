#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="/etc/drift-stack.env"
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

WORKSPACE_ROOT="${WORKSPACE_ROOT:-$HOME/drift-opc-workflow-v3}"
PANEL_PORT="${DRIFT_PANEL_PORT:-8888}"

cd "${WORKSPACE_ROOT}"
exec python3 -m http.server "${PANEL_PORT}" --directory "${WORKSPACE_ROOT}"
