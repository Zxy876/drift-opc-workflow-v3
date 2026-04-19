"""
github_projects.py — GitHub Projects V2 集成

功能：
1) 关卡创建时自动创建 Project Item
2) 关卡状态变更时更新 Item 字段
3) 提供 API 查询同步状态
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import threading
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter

logger = logging.getLogger("github_projects")

_GH_GRAPHQL = "https://api.github.com/graphql"

# 内存缓存: level_id -> project_item_id
_level_item_map: Dict[str, str] = {}


router = APIRouter(prefix="/github", tags=["GitHub"])


def _enabled() -> bool:
    return os.environ.get("GITHUB_PROJECTS_ENABLED", "false").lower() == "true"


def _token() -> str:
    return os.environ.get("GITHUB_TOKEN", "")


def _project_id() -> str:
    return os.environ.get("GITHUB_PROJECT_ID", "")


def _graphql(query: str, variables: dict | None = None) -> dict:
    """执行 GitHub GraphQL 请求。"""
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
    }
    body: Dict[str, Any] = {"query": query}
    if variables:
        body["variables"] = variables

    resp = httpx.post(_GH_GRAPHQL, json=body, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        logger.warning("GraphQL errors: %s", data["errors"])
    return data


def _build_item_body(
    level_id: str,
    title: str,
    text: str,
    difficulty: int | None,
    player_id: str | None,
    source: str,
    extra_meta: dict | None,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"## 关卡: {title or level_id}",
        "",
        f"**Level ID:** `{level_id}`",
        f"**创建时间:** {now}",
        f"**来源:** {source}",
    ]
    if difficulty is not None:
        lines.append(f"**目标难度:** D{difficulty}")
    if player_id:
        lines.append(f"**创建者:** {player_id}")

    lines.extend(["", "### 关卡描述", "", (text or "(无描述)")[:1000]])

    if extra_meta:
        lines.extend(["", "### 元数据", ""])
        for key, value in extra_meta.items():
            lines.append(f"- **{key}:** {value}")

    lines.extend(["", "---", "*由 Drift Experience Panel 自动创建*"])
    return "\n".join(lines)


def _update_single_select_field(item_id: str, field: dict, value_name: str) -> None:
    options = field.get("options", [])
    option = next((o for o in options if o.get("name") == value_name), None)
    if not option:
        logger.warning("Option '%s' not found in field '%s'", value_name, field.get("name"))
        return

    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $projectId
        itemId: $itemId
        fieldId: $fieldId
        value: $value
      }) {
        projectV2Item { id }
      }
    }
    """

    _graphql(
        mutation,
        {
            "projectId": _project_id(),
            "itemId": item_id,
            "fieldId": field["id"],
            "value": {"singleSelectOptionId": option["id"]},
        },
    )


def _update_number_field(item_id: str, field: dict, value: float) -> None:
    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $projectId
        itemId: $itemId
        fieldId: $fieldId
        value: $value
      }) {
        projectV2Item { id }
      }
    }
    """

    _graphql(
        mutation,
        {
            "projectId": _project_id(),
            "itemId": item_id,
            "fieldId": field["id"],
            "value": {"number": value},
        },
    )


def _update_text_field(item_id: str, field: dict, value: str) -> None:
    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $projectId
        itemId: $itemId
        fieldId: $fieldId
        value: $value
      }) {
        projectV2Item { id }
      }
    }
    """

    _graphql(
        mutation,
        {
            "projectId": _project_id(),
            "itemId": item_id,
            "fieldId": field["id"],
            "value": {"text": value},
        },
    )


def _set_item_fields(
    item_id: str,
    level_id: str,
    difficulty: int | None,
    source: str,
) -> None:
    """设置 Project Item 字段：Status/Difficulty/Source/LevelID。"""

    query = """
    query($projectId: ID!) {
      node(id: $projectId) {
        ... on ProjectV2 {
          fields(first: 20) {
            nodes {
              ... on ProjectV2Field {
                id
                name
                dataType
              }
              ... on ProjectV2SingleSelectField {
                id
                name
                dataType
                options {
                  id
                  name
                }
              }
            }
          }
        }
      }
    }
    """

    result = _graphql(query, {"projectId": _project_id()})
    fields = result.get("data", {}).get("node", {}).get("fields", {}).get("nodes", [])
    field_map = {f.get("name"): f for f in fields if f.get("name")}

    if "Status" in field_map:
        _update_single_select_field(item_id, field_map["Status"], "Created")

    if "Difficulty" in field_map and difficulty is not None:
        _update_number_field(item_id, field_map["Difficulty"], float(difficulty))

    if "Source" in field_map:
        _update_single_select_field(item_id, field_map["Source"], source)

    if "LevelID" in field_map:
        _update_text_field(item_id, field_map["LevelID"], level_id)


def create_project_item(
    level_id: str,
    title: str,
    text: str,
    difficulty: int | None = None,
    player_id: str | None = None,
    source: str = "panel",
    extra_meta: dict | None = None,
) -> Optional[str]:
    """创建 Draft Issue Item 并设置字段。"""
    if not _enabled():
        return None

    if not _token():
        logger.warning("GITHUB_TOKEN not set, skipping project sync")
        return None

    project_id = _project_id()
    if not project_id:
        logger.warning("GITHUB_PROJECT_ID not set, skipping project sync")
        return None

    body_md = _build_item_body(level_id, title, text, difficulty, player_id, source, extra_meta)

    mutation = """
    mutation($projectId: ID!, $title: String!, $body: String!) {
      addProjectV2DraftIssue(input: {
        projectId: $projectId
        title: $title
        body: $body
      }) {
        projectItem {
          id
        }
      }
    }
    """

    item_title = f"[Level] {title or level_id} ({level_id})"
    if len(item_title) > 200:
        item_title = item_title[:200]

    try:
        result = _graphql(
            mutation,
            {
                "projectId": project_id,
                "title": item_title,
                "body": body_md,
            },
        )
        item_id = (
            result.get("data", {})
            .get("addProjectV2DraftIssue", {})
            .get("projectItem", {})
            .get("id")
        )
        if item_id:
            logger.info("Created GitHub Project item %s for level %s", item_id, level_id)
            try:
                _set_item_fields(item_id, level_id, difficulty, source)
            except Exception as exc:
                logger.warning("Failed to set project item fields: %s", exc)
        return item_id
    except Exception as exc:
        logger.error("Failed to create GitHub Project item for level %s: %s", level_id, exc)
        return None


def create_project_item_async(
    level_id: str,
    title: str,
    text: str,
    difficulty: int | None = None,
    player_id: str | None = None,
    source: str = "panel",
    extra_meta: dict | None = None,
) -> None:
    """异步创建 Project Item，不阻塞主请求。"""
    if not _enabled():
        return

    def _do() -> None:
        item_id = create_project_item(
            level_id=level_id,
            title=title,
            text=text,
            difficulty=difficulty,
            player_id=player_id,
            source=source,
            extra_meta=extra_meta,
        )
        if item_id:
            _level_item_map[level_id] = item_id

    threading.Thread(target=_do, daemon=True).start()


def update_project_item_status(level_id: str, new_status: str) -> None:
    """按 level_id 更新 Status 字段。"""
    if not _enabled():
        return

    item_id = _level_item_map.get(level_id)
    if not item_id:
        logger.debug("No project item found for level %s, skipping status update", level_id)
        return

    try:
        query = """
        query($projectId: ID!) {
          node(id: $projectId) {
            ... on ProjectV2 {
              fields(first: 20) {
                nodes {
                  ... on ProjectV2SingleSelectField {
                    id
                    name
                    options { id name }
                  }
                }
              }
            }
          }
        }
        """
        result = _graphql(query, {"projectId": _project_id()})
        fields = result.get("data", {}).get("node", {}).get("fields", {}).get("nodes", [])
        status_field = next((f for f in fields if f.get("name") == "Status"), None)
        if status_field:
            _update_single_select_field(item_id, status_field, new_status)
    except Exception as exc:
        logger.warning("Failed to update project item status: %s", exc)


@router.get("/project/status")
def get_github_project_status() -> dict:
    """查询 GitHub Projects 集成状态。"""
    pid = _project_id()
    return {
        "enabled": _enabled(),
        "project_id": (pid[:10] + "...") if pid else None,
        "tracked_levels": len(_level_item_map),
        "level_ids": list(_level_item_map.keys()),
    }


@router.post("/project/sync/{level_id}")
def manual_sync_level(level_id: str, title: str = "", text: str = "") -> dict:
    """手动同步某个关卡到 GitHub Project。"""
    if not _enabled():
        return {"error": "GitHub Projects integration not enabled"}

    item_id = create_project_item(
        level_id=level_id,
        title=title or level_id,
        text=text,
        source="manual_sync",
    )
    if item_id:
        _level_item_map[level_id] = item_id
        return {"ok": True, "item_id": item_id}

    return {"ok": False, "error": "Failed to create project item"}
