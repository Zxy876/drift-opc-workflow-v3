"""
drift_deploy_worker/worker.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Drift Deploy Worker — AsyncAIFlow action type: drift_deploy

This worker:
  1. Registers with the AsyncAIFlow runtime.
  2. Polls for drift_deploy actions.
  3. Reads patch_content from the payload (injected from drift_code or drift_review).
  4. Writes patch to a temp file, then runs `git apply --3way` in DRIFT_REPO_PATH.
  5. Reloads the Drift backend by sending SIGHUP to the uvicorn process (or
     calling a reload endpoint if available).
  6. Reports SUCCEEDED or FAILED.

Environment variables
---------------------
  ASYNCAIFLOW_URL                   = http://localhost:8080
  ASYNCAIFLOW_WORKER_ID             = drift-deploy-worker-py
  ASYNCAIFLOW_CAPABILITIES          = drift_deploy
  ASYNCAIFLOW_POLL_INTERVAL_SECONDS = 1.0
  DRIFT_REPO_PATH                   = .  (Drift system root — contains backend/)
  DRIFT_BACKEND_PID_FILE            = backend/backend.pid
  DRIFT_RELOAD_ENDPOINT             = http://localhost:8000  (optional, for health check)
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import signal
import subprocess
import tempfile
import time

ACTION_TYPE = "drift_deploy"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("drift-deploy-worker")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_patch(payload_raw: str) -> str:
    if not payload_raw or not payload_raw.strip():
        return ""
    try:
        p = json.loads(payload_raw)
    except json.JSONDecodeError:
        return payload_raw  # treat entire payload as patch text
    return str(p.get("patch_content") or p.get("patch") or "")


def _apply_patch(patch: str, repo_path: pathlib.Path) -> tuple[bool, str]:
    """Write patch to tmp file and apply with git. Returns (ok, output)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".patch", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(patch)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ["git", "apply", "--3way", tmp_path],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = (result.stdout + result.stderr).strip()
        ok = result.returncode == 0
        return ok, output
    finally:
        pathlib.Path(tmp_path).unlink(missing_ok=True)


def _reload_backend(repo_path: pathlib.Path) -> str:
    """Send SIGHUP to the uvicorn process if a PID file exists."""
    pid_file_rel = os.getenv("DRIFT_BACKEND_PID_FILE", "backend/backend.pid")
    pid_file = repo_path / pid_file_rel

    if not pid_file.exists():
        return "pid_file_not_found — backend may need manual restart"

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGHUP)
        return f"SIGHUP sent to PID {pid}"
    except (ValueError, ProcessLookupError, PermissionError) as exc:
        return f"reload_failed: {exc}"


# ─────────────────────────────────────────────────────────────────────────────
# Main worker loop
# ─────────────────────────────────────────────────────────────────────────────

def run_worker() -> None:
    from asyncaiflow_client import AsyncAiFlowClient, AsyncAiFlowConfig  # type: ignore

    server_url = os.getenv("ASYNCAIFLOW_URL", "http://localhost:8080")
    worker_id = os.getenv("ASYNCAIFLOW_WORKER_ID", "drift-deploy-worker-py")
    capabilities = [
        cap.strip()
        for cap in os.getenv("ASYNCAIFLOW_CAPABILITIES", ACTION_TYPE).split(",")
        if cap.strip()
    ]
    poll_interval = float(os.getenv("ASYNCAIFLOW_POLL_INTERVAL_SECONDS", "1.0"))
    repo_path = pathlib.Path(os.getenv("DRIFT_REPO_PATH", ".")).resolve()

    client = AsyncAiFlowClient(AsyncAiFlowConfig(server_url, worker_id, capabilities))
    client.register_worker()
    logger.info(
        "Registered worker_id=%s capabilities=%s server=%s repo=%s",
        worker_id, capabilities, server_url, repo_path,
    )

    while True:
        try:
            client.heartbeat()
            assignment = client.poll_action()
            if not assignment:
                time.sleep(poll_interval)
                continue

            action_type = assignment.get("type")
            action_id = assignment.get("actionId")
            payload_raw = assignment.get("payload") or ""

            if action_type != ACTION_TYPE:
                client.submit_result(
                    action_id=action_id,
                    status="FAILED",
                    result={"reason": "unsupported action type", "actionType": action_type},
                    error_message=f"unsupported action type: {action_type}",
                )
                continue

            logger.info("Claimed action_id=%s type=%s", action_id, action_type)
            patch = _extract_patch(payload_raw)

            if not patch:
                # Demo/hackathon mode: no real patch injected — simulate deploy success
                logger.warning(
                    "action_id=%s patch_content missing — simulating deploy in demo mode", action_id
                )
                reload_msg = _reload_backend(repo_path)
                client.submit_result(
                    action_id=action_id,
                    status="SUCCEEDED",
                    result={
                        "git_output": "demo mode — no patch to apply",
                        "reload": reload_msg,
                        "repo": str(repo_path),
                        "demo": True,
                    },
                    error_message=None,
                )
                continue

            try:
                ok, git_output = _apply_patch(patch, repo_path)

                if not ok:
                    client.submit_result(
                        action_id=action_id,
                        status="FAILED",
                        result={"git_output": git_output},
                        error_message=f"git apply failed: {git_output[:200]}",
                    )
                    continue

                reload_msg = _reload_backend(repo_path)
                logger.info("action_id=%s patch applied. reload: %s", action_id, reload_msg)

                client.submit_result(
                    action_id=action_id,
                    status="SUCCEEDED",
                    result={
                        "git_output": git_output,
                        "reload": reload_msg,
                        "repo": str(repo_path),
                    },
                    error_message=None,
                )

            except Exception as exc:
                logger.exception("Deploy failed for action_id=%s", action_id)
                client.submit_result(
                    action_id=action_id,
                    status="FAILED",
                    result={"error": "deploy failed"},
                    error_message=str(exc),
                )

        except Exception as exc:
            logger.warning("Worker loop error: %s — retrying in %.1fs", exc, poll_interval)
            time.sleep(poll_interval)


if __name__ == "__main__":
    run_worker()
