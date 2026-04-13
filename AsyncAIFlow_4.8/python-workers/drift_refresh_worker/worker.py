"""
drift_refresh_worker/worker.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Drift Refresh Worker — AsyncAIFlow action type: drift_refresh

This worker is the FINAL step in every Drift DAG. It:
  1. Registers with the AsyncAIFlow runtime as capability "drift_refresh".
  2. Polls for drift_refresh actions.
  3. Reads player_id, issue, summary from the action payload.
     (summary is injected from drift_deploy.result.summary via the inject mechanism;
      falls back to drift_code.result.summary or the raw issue text.)
  4. Calls Drift POST /story/refresh — triggers Drift world regeneration and stores
     the resulting world_patch in the Drift progress log for the player.
  5. Calls Drift POST /story/progress/notify so the MC plugin's poll loop can detect
     completion and apply the world_patch live in Minecraft.
  6. Submits SUCCEEDED (or FAILED) to AsyncAIFlow.

Environment variables
---------------------
  ASYNCAIFLOW_SERVER_BASE_URL       = http://localhost:8080
  ASYNCAIFLOW_WORKER_ID             = drift-refresh-worker-py
  ASYNCAIFLOW_CAPABILITIES          = drift_refresh
  ASYNCAIFLOW_POLL_INTERVAL_SECONDS = 2.0
  DRIFT_URL                         = http://localhost:8000
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

ACTION_TYPE = "drift_refresh"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("drift-refresh-worker")

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DRIFT_URL: str = os.environ.get("DRIFT_URL", "http://localhost:8000")

# HTTP session — trust_env=False avoids macOS/Linux proxy interference on localhost
_drift_session = requests.Session()
_drift_session.trust_env = False


# ─────────────────────────────────────────────────────────────────────────────
# Payload extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_fields(payload_raw: str | dict) -> tuple[str, str, str]:
    """Return (player_id, issue, summary) from the materialized action payload.

    The action payload after inject materialization looks like:
      {
        "player_id": "Steve",
        "issue": "Add a night-vision potion shop",
        "summary": "<injected from drift_deploy.result.summary>",  # may be missing
        "event": "drift_issue_resolved",
        ...
      }
    Falls back sensibly for each missing field.
    """
    if isinstance(payload_raw, str):
        try:
            p = json.loads(payload_raw)
        except json.JSONDecodeError:
            p = {}
    elif isinstance(payload_raw, dict):
        p = payload_raw
    else:
        p = {}

    player_id = str(p.get("player_id") or "demo").strip() or "demo"
    issue = str(p.get("issue") or p.get("issue_text") or "").strip()
    # summary may be injected from drift_deploy result; fall back to issue
    summary = str(p.get("summary") or issue or "AI workflow completed").strip()
    return player_id, issue, summary


def _extract_workflow_id(payload_raw: str | dict) -> str:
    """Extract workflow_id from payload if available."""
    if isinstance(payload_raw, str):
        try:
            p = json.loads(payload_raw)
        except json.JSONDecodeError:
            return ""
    elif isinstance(payload_raw, dict):
        p = payload_raw
    else:
        return ""
    return str(p.get("workflow_id") or "")


# ─────────────────────────────────────────────────────────────────────────────
# Drift API calls
# ─────────────────────────────────────────────────────────────────────────────

def call_story_refresh(player_id: str, workflow_id: str, issue: str, summary: str) -> dict[str, Any]:
    """POST /story/refresh — trigger world patch generation and progress storage.

    Returns the Drift response dict.  On HTTP error raises requests.HTTPError.
    """
    body = {
        "player_id": player_id,
        "workflow_id": workflow_id,
        "issue": issue,
        "summary": summary,
    }
    logger.info(
        "Calling Drift POST /story/refresh player_id=%s workflow_id=%s summary=%.80r",
        player_id, workflow_id, summary,
    )
    resp = _drift_session.post(f"{DRIFT_URL}/story/refresh", json=body, timeout=180)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text[:500]}
    logger.info(
        "Drift /story/refresh status=%d keys=%s",
        resp.status_code,
        list(data.keys()) if isinstance(data, dict) else type(data).__name__,
    )
    return data


def call_progress_notify(
    player_id: str,
    stage: str,
    message: str,
    workflow_id: str,
    status: str,
    world_patch: dict | None = None,
) -> None:
    """POST /story/progress/notify — update Drift progress log for MC plugin polling."""
    body: dict[str, Any] = {
        "player_id": player_id,
        "stage": stage,
        "message": message,
        "workflow_id": workflow_id,
        "status": status,
    }
    if world_patch is not None:
        body["world_patch"] = world_patch

    try:
        resp = _drift_session.post(
            f"{DRIFT_URL}/story/progress/notify", json=body, timeout=10
        )
        resp.raise_for_status()
        logger.info(
            "progress/notify player_id=%s stage=%s status=%s → %d",
            player_id, stage, status, resp.status_code,
        )
    except Exception as exc:
        # Non-fatal — main work already done
        logger.warning("progress/notify failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Worker main loop
# ─────────────────────────────────────────────────────────────────────────────

def run_worker() -> None:
    from asyncaiflow_client import AsyncAiFlowClient, AsyncAiFlowConfig  # type: ignore

    server_url = os.getenv("ASYNCAIFLOW_SERVER_BASE_URL", "http://localhost:8080")
    worker_id = os.getenv("ASYNCAIFLOW_WORKER_ID", "drift-refresh-worker-py")
    capabilities = [
        cap.strip()
        for cap in os.getenv("ASYNCAIFLOW_CAPABILITIES", ACTION_TYPE).split(",")
        if cap.strip()
    ]
    poll_interval = float(os.getenv("ASYNCAIFLOW_POLL_INTERVAL_SECONDS", "2.0"))

    client = AsyncAiFlowClient(AsyncAiFlowConfig(server_url, worker_id, capabilities))
    client.register_worker()
    logger.info(
        "Registered worker_id=%s capabilities=%s server=%s drift=%s",
        worker_id, capabilities, server_url, DRIFT_URL,
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
            payload_raw = assignment.get("payload") or {}

            if action_type != ACTION_TYPE:
                client.submit_result(
                    action_id=action_id,
                    status="FAILED",
                    result={"reason": "unsupported action type", "actionType": action_type},
                    error_message=f"unsupported action type: {action_type}",
                )
                continue

            logger.info("Claimed action_id=%s type=%s", action_id, action_type)

            player_id, issue, summary = _extract_fields(payload_raw)
            workflow_id = _extract_workflow_id(payload_raw) or str(assignment.get("workflowId", ""))

            # ── Stage: in-progress notify ─────────────────────────────────────
            call_progress_notify(
                player_id=player_id,
                stage="drift_refresh",
                message="正在刷新 Drift 世界...",
                workflow_id=workflow_id,
                status="RUNNING",
            )

            try:
                # ── Core: trigger Drift world refresh ───────────────────────────
                refresh_resp = call_story_refresh(
                    player_id=player_id,
                    workflow_id=workflow_id,
                    issue=issue,
                    summary=summary,
                )

                world_patch = refresh_resp.get("world_patch") or {}
                level_id = refresh_resp.get("level_id") or ""

                # ── Worker-side capability-trigger fallback ───────────────────────────
                # 设计：每次 issue 解决后同步补充触发方式；玩家通过进入触发区域
                # 交互感知世界能力变化（而非被动等待视觉刷新）。
                # 若后端 /story/refresh 在 demo 模式下仍返回空 world_patch，在此保底。
                if not world_patch:
                    cap_label = (summary or issue or "AI 能力更新")[:40]
                    cap_id = f"ai_cap_{workflow_id}" if workflow_id else f"ai_cap_{action_id}"
                    world_patch = {
                        "tell": (
                            f"§6[AI 代码引擎] §f能力已部署：§a{cap_label}\n"
                            "§7走入发光区域即可激活新能力"
                        ),
                        "title": {
                            "title": "§6✦ 能力已更新",
                            "subtitle": f"§a{cap_label[:30]}",
                            "fadeIn": 10,
                            "stay": 70,
                            "fadeOut": 20,
                        },
                        "sound": {"sound": "ENTITY_PLAYER_LEVELUP", "volume": 1.0, "pitch": 1.0},
                        "particle": {"type": "VILLAGER_HAPPY", "count": 40, "radius": 1.2},
                        "trigger_zones": [
                            {
                                "id": cap_id,
                                "quest_event": "ai_capability_activated",
                                "radius": 4.0,
                                "repeat": False,
                            }
                        ],
                    }
                    logger.info(
                        "action_id=%s world_patch was empty — injected capability-trigger fallback cap_id=%s",
                        action_id, cap_id,
                    )

                # ── Stage: success notify (MC plugin reads this to apply world_patch) ─
                call_progress_notify(
                    player_id=player_id,
                    stage="drift_refresh",
                    message=f"AI 工作流执行完毕，世界已更新 (level_id={level_id})",
                    workflow_id=workflow_id,
                    status="SUCCEEDED",
                    world_patch=world_patch,
                )

                result = {
                    "schemaVersion": "v1",
                    "worker": worker_id,
                    "actionId": action_id,
                    "player_id": player_id,
                    "level_id": level_id,
                    "drift_ok": refresh_resp.get("ok", bool(refresh_resp)),
                    "world_patch_keys": list(world_patch.keys()) if world_patch else [],
                    "summary": f"Drift world refreshed for player {player_id}",
                }

                client.submit_result(
                    action_id=action_id,
                    status="SUCCEEDED",
                    result=result,
                    error_message=None,
                )
                logger.info(
                    "action_id=%s SUCCEEDED player_id=%s level_id=%s world_patch_keys=%s",
                    action_id, player_id, level_id, result["world_patch_keys"],
                )

            except requests.HTTPError as http_exc:
                error_msg = f"Drift /story/refresh HTTP error: {http_exc}"
                logger.error("action_id=%s %s", action_id, error_msg)

                call_progress_notify(
                    player_id=player_id,
                    stage="drift_refresh",
                    message=f"世界刷新失败: {http_exc}",
                    workflow_id=workflow_id,
                    status="FAILED",
                )

                client.submit_result(
                    action_id=action_id,
                    status="FAILED",
                    result={"error": "drift_refresh_http_error", "player_id": player_id},
                    error_message=error_msg,
                )

            except Exception as exc:
                error_msg = str(exc)
                logger.exception("action_id=%s drift_refresh failed: %s", action_id, error_msg)

                call_progress_notify(
                    player_id=player_id,
                    stage="drift_refresh",
                    message=f"世界刷新异常: {error_msg[:120]}",
                    workflow_id=workflow_id,
                    status="FAILED",
                )

                client.submit_result(
                    action_id=action_id,
                    status="FAILED",
                    result={"error": "drift_refresh_exception", "player_id": player_id},
                    error_message=error_msg,
                )

        except Exception as exc:
            logger.warning("Worker loop error: %s — retrying in %.1fs", exc, poll_interval)
            time.sleep(poll_interval)


if __name__ == "__main__":
    run_worker()
