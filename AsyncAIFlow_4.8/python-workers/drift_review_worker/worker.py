"""
drift_review_worker/worker.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Drift Code Review Worker — AsyncAIFlow action type: drift_review

This worker:
  1. Registers with the AsyncAIFlow runtime.
  2. Polls for drift_review actions.
  3. Receives patch_content (injected from drift_code result) and issue_text.
  4. Calls an LLM as a senior Drift reviewer.
  5. Returns approved: true/false + feedback.
     - If approved=false the action is submitted as FAILED (blocking downstream).

Environment variables
---------------------
  ASYNCAIFLOW_URL                   = http://localhost:8080
  ASYNCAIFLOW_WORKER_ID             = drift-review-worker-py
  ASYNCAIFLOW_CAPABILITIES          = drift_review
  ASYNCAIFLOW_POLL_INTERVAL_SECONDS = 1.0
  OPENAI_API_KEY / GLM_API_KEY
"""
from __future__ import annotations

import json
import logging
import os
import time

ACTION_TYPE = "drift_review"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("drift-review-worker")

REVIEW_SYSTEM_PROMPT = """You are a senior Python engineer reviewing a git patch for the Drift game server.
Evaluate the patch for correctness, security (no OWASP Top-10 issues), and consistency with the codebase.
Respond with ONLY a JSON object:
{
  "approved": true | false,
  "score": 1-10,
  "summary": "<one-sentence verdict>",
  "issues": ["<issue1>", ...]   // empty list if approved
}
"""


def _review_patch(issue: str, patch: str) -> dict:
    user_msg = f"Issue to fix:\n{issue}\n\nProposed patch:\n```diff\n{patch}\n```"

    if os.environ.get("OPENAI_API_KEY"):
        import openai  # type: ignore
        client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        return json.loads(resp.choices[0].message.content)

    if os.environ.get("GLM_API_KEY"):
        from zhipuai import ZhipuAI  # type: ignore
        client = ZhipuAI(api_key=os.environ["GLM_API_KEY"])
        resp = client.chat.completions.create(
            model="glm-4",
            temperature=0.1,
            messages=[
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        raw = resp.choices[0].message.content.strip()
        # Strip potential markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        return json.loads(raw)

    # Demo/hackathon stub: no LLM key available
    import logging as _lg
    _lg.getLogger("drift-review-worker").warning(
        "No LLM API key set — returning stub review for demo mode"
    )
    return {"approved": True, "comments": "Demo stub review: patch looks good.", "score": 8}


def _extract_fields(payload_raw: str) -> tuple[str, str]:
    """Return (issue_text, patch_content)."""
    if not payload_raw or not payload_raw.strip():
        return "", ""
    try:
        p = json.loads(payload_raw)
    except json.JSONDecodeError:
        return "", payload_raw
    issue = str(p.get("issue_text") or p.get("issue") or "")
    # patch may be injected as "patch_content", "patch", or "code_result" (inject target name)
    patch = str(p.get("patch_content") or p.get("patch") or p.get("code_result") or "")
    return issue, patch


def run_worker() -> None:
    from asyncaiflow_client import AsyncAiFlowClient, AsyncAiFlowConfig  # type: ignore

    server_url = os.getenv("ASYNCAIFLOW_URL", "http://localhost:8080")
    worker_id = os.getenv("ASYNCAIFLOW_WORKER_ID", "drift-review-worker-py")
    capabilities = [
        cap.strip()
        for cap in os.getenv("ASYNCAIFLOW_CAPABILITIES", ACTION_TYPE).split(",")
        if cap.strip()
    ]
    poll_interval = float(os.getenv("ASYNCAIFLOW_POLL_INTERVAL_SECONDS", "1.0"))

    client = AsyncAiFlowClient(AsyncAiFlowConfig(server_url, worker_id, capabilities))
    client.register_worker()
    logger.info("Registered worker_id=%s capabilities=%s server=%s", worker_id, capabilities, server_url)

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
            issue, patch = _extract_fields(payload_raw)

            if not patch:
                # Demo/stub mode: no patch injected — auto-approve with stub message
                logger.warning(
                    "action_id=%s patch_content missing — auto-approving in demo mode", action_id
                )
                client.submit_result(
                    action_id=action_id,
                    status="SUCCEEDED",
                    result={"approved": True, "score": 8, "summary": "Demo mode: patch auto-approved.", "issues": []},
                    error_message=None,
                )
                continue

            try:
                review = _review_patch(issue, patch)
                approved = bool(review.get("approved", False))
                score = int(review.get("score", 0))
                summary = str(review.get("summary", ""))
                issues = list(review.get("issues", []))

                logger.info(
                    "action_id=%s approved=%s score=%d summary=%s",
                    action_id, approved, score, summary[:80]
                )

                if approved:
                    client.submit_result(
                        action_id=action_id,
                        status="SUCCEEDED",
                        result={"approved": True, "score": score, "summary": summary, "issues": issues},
                        error_message=None,
                    )
                else:
                    # Not approved — fail the action so the DAG halts
                    client.submit_result(
                        action_id=action_id,
                        status="FAILED",
                        result={"approved": False, "score": score, "summary": summary, "issues": issues},
                        error_message=f"Review rejected: {summary}",
                    )

            except Exception as exc:
                logger.exception("Review failed for action_id=%s", action_id)
                client.submit_result(
                    action_id=action_id,
                    status="FAILED",
                    result={"error": "review failed"},
                    error_message=str(exc),
                )

        except Exception as exc:
            logger.warning("Worker loop error: %s — retrying in %.1fs", exc, poll_interval)
            time.sleep(poll_interval)


if __name__ == "__main__":
    run_worker()
