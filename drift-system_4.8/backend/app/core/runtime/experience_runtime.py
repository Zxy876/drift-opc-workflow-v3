"""
experience_runtime.py — Phase 5: Experience Runtime Engine (解耦阶段)
=====================================================================

独立 Experience Runtime — trigger event → rule evaluation → state update → outcome

设计原则:
- 所有逻辑在 runtime 层; API 只做转发
- 永远不向上抛出异常  
- 不依赖 story_api 或 inject 时的逻辑
- ExperienceStateStore: SQLite 持久化, 与 QuestStateStore 同模式
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from app.core.story.story_loader import (
    BACKEND_DIR,
    _candidate_filenames,
    _find_level_path,
)

logger = logging.getLogger("uvicorn.error")

# Phase 6: lazy import to avoid circular dependency
def _get_debug_store():
    from app.core.runtime.experience_debug_store import (
        experience_debug_store,
        build_rule_evaluation,
    )
    return experience_debug_store, build_rule_evaluation


# ─────────────────────────────────────────────────────────────────────────────
# ExperienceStateStore — SQLite 持久化
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_EXP_STATE_DB_PATH = os.path.join(BACKEND_DIR, "data", "experience_state.db")


class ExperienceStateStore:
    """每个 (player_id, level_id) 存储一份 experience state dict。"""

    def __init__(self, db_path: str | None = None) -> None:
        configured_path = str(os.environ.get("DRIFT_EXP_STATE_DB_PATH") or "").strip()
        resolved_path = db_path or configured_path or DEFAULT_EXP_STATE_DB_PATH
        self.db_path = os.path.abspath(str(resolved_path))
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_experience_state (
                player_id     TEXT NOT NULL,
                level_id      TEXT NOT NULL,
                state_json    TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY (player_id, level_id)
            )
            """
        )

    def save_state(self, player_id: str, level_id: str, state: Dict[str, Any]) -> None:
        np = str(player_id or "").strip()
        nl = str(level_id or "").strip()
        if not np or not nl or not isinstance(state, dict):
            return
        now_ms = int(time.time() * 1000)
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                conn.execute(
                    """
                    INSERT INTO player_experience_state (player_id, level_id, state_json, updated_at_ms)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(player_id, level_id) DO UPDATE SET
                        state_json = excluded.state_json,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (np, nl, payload, now_ms),
                )

    def load_state(self, player_id: str, level_id: str) -> Optional[Dict[str, Any]]:
        np = str(player_id or "").strip()
        nl = str(level_id or "").strip()
        if not np or not nl:
            return None
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT state_json FROM player_experience_state WHERE player_id = ? AND level_id = ?",
                    (np, nl),
                ).fetchone()
        if not row:
            return None
        raw = row["state_json"]
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None

    def delete_state(self, player_id: str, level_id: str) -> None:
        np = str(player_id or "").strip()
        nl = str(level_id or "").strip()
        if not np or not nl:
            return
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                conn.execute(
                    "DELETE FROM player_experience_state WHERE player_id = ? AND level_id = ?",
                    (np, nl),
                )


experience_state_store = ExperienceStateStore()


# ─────────────────────────────────────────────────────────────────────────────
# 关卡文档加载 (experience_spec + experience_state 从 meta 读取)
# ─────────────────────────────────────────────────────────────────────────────

def _load_level_doc(level_id: str) -> Optional[Dict[str, Any]]:
    """加载关卡 JSON，支持 flagship_levels/ 和 generated/ 两个目录。"""
    level_id_str = str(level_id or "").strip()
    if not level_id_str:
        return None
    for filename in _candidate_filenames(level_id_str):
        path = _find_level_path(filename)
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (OSError, json.JSONDecodeError):
                pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Trigger 匹配 — 按与 _bridge_exp_spec_to_world_patch 相同的公式推导 quest_event
# ─────────────────────────────────────────────────────────────────────────────

def _derive_trigger_quest_event(trigger: Dict[str, Any], level_id: str) -> Optional[str]:
    """
    按照与 story_api._bridge_exp_spec_to_world_patch 相同的公式重新推导触发器的
    quest_event 值，用于 runtime 匹配 MC plugin 发送的 payload.quest_event。
    """
    ttype = str(trigger.get("type") or "").strip().lower()
    target = str(trigger.get("target") or "").strip().lower().replace(" ", "_")

    if ttype == "proximity":
        return f"exp_proximity_{target}" if target else f"exp_proximity_{level_id}"
    elif ttype == "item_collect":
        return f"exp_collect_{target}" if target else f"exp_collect_{level_id}"
    elif ttype == "interact":
        return f"exp_interact_{target}" if target else f"exp_interact_{level_id}"
    elif ttype == "timer":
        duration = int(trigger.get("duration", 60))
        return f"exp_timer_{duration}s"
    elif ttype == "npc_talk":
        return f"exp_npc_talk_{target}" if target else f"exp_npc_talk_{level_id}"
    return None


def _infer_trigger_from_quest_event(quest_event: str) -> Optional[Dict[str, Any]]:
    """
    当 experience_spec.triggers 为空时的后备路径:
    从 quest_event 字符串推导触发器类型。
    quest_event 遵循 _bridge_exp_spec_to_world_patch 生成的命名规则:
      exp_collect_{target}    → item_collect
      exp_proximity_{target}  → proximity
      exp_interact_{target}   → interact
      exp_npc_talk_{target}   → npc_talk
      exp_timer_{N}s          → timer
    也覆盖非标准名称中含有 collect/pick/gem/gem 等关键词的情况。
    """
    qe = quest_event.lower()

    if qe.startswith("exp_collect_") or qe.startswith("collect_"):
        target = qe.split("_", 2)[-1] if "_" in qe else "item"
        return {"type": "item_collect", "target": target, "_inferred": True}

    if qe.startswith("exp_proximity_") or qe.startswith("proximity_"):
        target = qe.split("_", 2)[-1] if "_" in qe else ""
        return {"type": "proximity", "target": target, "_inferred": True}

    if qe.startswith("exp_interact_") or qe.startswith("interact_"):
        target = qe.split("_", 2)[-1] if "_" in qe else ""
        return {"type": "interact", "target": target, "_inferred": True}

    if qe.startswith("exp_npc_talk_") or qe.startswith("npc_talk_"):
        target = qe.split("_", 3)[-1] if "_" in qe else ""
        return {"type": "npc_talk", "target": target, "_inferred": True}

    if qe.startswith("exp_timer_") or qe.startswith("timer_"):
        try:
            duration = int(re.search(r"(\d+)", qe).group(1))
        except (AttributeError, ValueError):
            duration = 60
        return {"type": "timer", "target": "countdown", "duration": duration, "_inferred": True}

    # Keyword-based fallback for non-standard names
    COLLECT_KWS = ("collect", "gem", "pick", "gather", "item", "宝石", "收集", "捡")
    if any(kw in qe for kw in COLLECT_KWS):
        return {"type": "item_collect", "target": qe, "_inferred": True}

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Rule 评估器 — 支持 var >= N 形式的结构化条件
# ─────────────────────────────────────────────────────────────────────────────

_CONDITION_RE = re.compile(
    r"([a-z_][a-z_0-9]*)\s*(>=|<=|==|>|<|!=)\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)


def _eval_condition(condition: str, state: Dict[str, Any]) -> bool:
    """
    尝试解析 condition 中所有简单比较表达式 (var op N) 并对 state 求值。
    如果没有找到可解析的表达式则返回 False（不主动触发）。
    每个找到的表达式都必须为 True 才整体为 True。
    """
    matches = _CONDITION_RE.findall(condition)
    if not matches:
        return False
    for var_name, op, threshold_str in matches:
        var_val = state.get(var_name)
        if var_val is None:
            return False
        try:
            # 支持布尔值: True -> 1, False -> 0
            if isinstance(var_val, bool):
                lhs = 1.0 if var_val else 0.0
            else:
                lhs = float(var_val)
            rhs = float(threshold_str)
        except (TypeError, ValueError):
            return False
        if op == ">=" and not (lhs >= rhs):
            return False
        elif op == "<=" and not (lhs <= rhs):
            return False
        elif op == "==" and not (lhs == rhs):
            return False
        elif op == ">" and not (lhs > rhs):
            return False
        elif op == "<" and not (lhs < rhs):
            return False
        elif op == "!=" and not (lhs != rhs):
            return False
    return True


def _evaluate_rules(
    exp_spec: Dict[str, Any],
    state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """评估所有规则，返回条件满足的规则列表。"""
    triggered: List[Dict[str, Any]] = []
    for rule in (exp_spec.get("rules") or []):
        if not isinstance(rule, dict):
            continue
        condition = str(rule.get("condition") or "")
        if _eval_condition(condition, state):
            triggered.append(rule)
    return triggered


# ─────────────────────────────────────────────────────────────────────────────
# State 更新 — 根据触发器类型修改状态变量
# ─────────────────────────────────────────────────────────────────────────────

def _apply_trigger_to_state(
    state: Dict[str, Any],
    trigger: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """将触发器效果应用到 state 副本并返回新 state。"""
    new_state = dict(state)
    ttype = str(trigger.get("type") or "").strip().lower()
    target = str(trigger.get("target") or "").strip().lower().replace(" ", "_")

    if ttype == "item_collect":
        # 更新所有可能的计数器变量名，确保覆盖各种条件写法
        target_key = f"{target}_count" if target and target not in ("item", "") else "collected_count"
        count_keys = {target_key, "collected_count", f"collected_{target}"}
        for key in list(new_state.keys()):
            if ("count" in key or "collected" in key) and isinstance(new_state.get(key), (int, float)):
                count_keys.add(key)
        for key in count_keys:
            if key in new_state:
                try:
                    new_state[key] = int(float(new_state[key])) + 1
                except (TypeError, ValueError):
                    new_state[key] = 1
            elif key in {target_key, "collected_count"}:
                new_state[key] = 1
        # 通用 progress 计数器（如果 spec 中存在）
        if "progress" in new_state:
            try:
                new_state["progress"] = int(float(new_state["progress"])) + 1
            except (TypeError, ValueError):
                new_state["progress"] = 1

    elif ttype == "proximity":
        visited_key = f"visited_{target}" if target else "area_visited"
        new_state[visited_key] = 1

    elif ttype == "interact":
        interacted_key = f"interacted_{target}" if target else "interacted"
        new_state[interacted_key] = 1

    elif ttype == "npc_talk":
        talked_key = f"talked_to_{target}" if target else "npc_talked"
        new_state[talked_key] = 1

    elif ttype == "timer":
        new_state["timer_fired"] = 1

    # 元数据记录
    new_state["_last_trigger_type"] = ttype
    new_state["_last_trigger_at_ms"] = int(time.time() * 1000)
    return new_state


# ─────────────────────────────────────────────────────────────────────────────
# ExperienceRuntimeEngine — 主引擎
# ─────────────────────────────────────────────────────────────────────────────

class ExperienceRuntimeEngine:
    """
    Phase 5 独立 Experience Runtime Engine。

    职责:
    - 接收 event_type + payload（来自 world_api.story_rule_event）
    - 加载 ExperienceSpec（来自关卡 meta.experience_spec）
    - 匹配触发器
    - 更新 ExperienceState（via ExperienceStateStore）
    - 评估规则（win/lose/unlock/grant）
    - 返回 outcome

    永远不抛出异常；任何错误均返回 experience_engine_active=False。
    """

    def __init__(self, state_store: ExperienceStateStore | None = None) -> None:
        self._store = state_store or experience_state_store

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def handle_event(
        self,
        level_id: str,
        player_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        处理一条 rule event。

        Returns:
            {
                "experience_engine_active": bool,
                "state_updated": bool,
                "triggered_rules": list[dict],
                "outcome": "win" | "lose" | "unlock" | "grant" | None,
                "current_state": dict,
                "triggers_matched": list[str],
                "level_id": str,
            }
        """
        _empty: Dict[str, Any] = {
            "experience_engine_active": False,
            "state_updated": False,
            "triggered_rules": [],
            "outcome": None,
            "current_state": {},
            "triggers_matched": [],
            "level_id": str(level_id or ""),
        }
        try:
            return self._process(level_id, player_id, event_type, payload)
        except Exception as exc:
            logger.warning(
                "experience_runtime_handle_event_failed",
                extra={
                    "player_id": player_id,
                    "level_id": level_id,
                    "error": str(exc),
                },
            )
            return _empty

    def reset_player_level(self, player_id: str, level_id: str) -> None:
        """重置玩家在特定关卡的 experience state（调试 / 测试用）。"""
        self._store.delete_state(player_id, level_id)

    def get_current_state(
        self, player_id: str, level_id: str
    ) -> Optional[Dict[str, Any]]:
        """读取当前 experience state（不触发事件）。"""
        state = self._store.load_state(player_id, level_id)
        if state is not None:
            return state
        # 从关卡 meta 回退初始值
        doc = _load_level_doc(level_id)
        if doc:
            meta = doc.get("meta") or {}
            initial = meta.get("experience_state")
            if isinstance(initial, dict):
                return dict(initial)
        return None

    # ------------------------------------------------------------------
    # 内部处理
    # ------------------------------------------------------------------

    def _process(
        self,
        level_id: str,
        player_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        level_id_str = str(level_id or "").strip()
        player_id_str = str(player_id or "").strip()
        payload = payload if isinstance(payload, dict) else {}

        # 1. 加载关卡文档中的 experience_spec
        level_doc = _load_level_doc(level_id_str)
        if not level_doc:
            return {
                "experience_engine_active": False,
                "state_updated": False,
                "triggered_rules": [],
                "outcome": None,
                "current_state": {},
                "triggers_matched": [],
                "level_id": level_id_str,
            }

        meta = level_doc.get("meta") or {}
        exp_spec = meta.get("experience_spec")
        if not isinstance(exp_spec, dict):
            return {
                "experience_engine_active": False,
                "state_updated": False,
                "triggered_rules": [],
                "outcome": None,
                "current_state": {},
                "triggers_matched": [],
                "level_id": level_id_str,
            }

        has_rules = bool(exp_spec.get("rules"))
        has_triggers = bool(exp_spec.get("triggers"))
        if not has_rules and not has_triggers:
            return {
                "experience_engine_active": False,
                "state_updated": False,
                "triggered_rules": [],
                "outcome": None,
                "current_state": {},
                "triggers_matched": [],
                "level_id": level_id_str,
            }

        # 2. 加载/初始化 experience state
        current_state = self._store.load_state(player_id_str, level_id_str)
        if current_state is None:
            # 首次: 从关卡内置初始值初始化
            initial = meta.get("experience_state")
            current_state = dict(initial) if isinstance(initial, dict) else {}

        # 3. 匹配触发器
        quest_event_val = str(payload.get("quest_event") or "").strip()
        matched_triggers: List[Dict[str, Any]] = []

        # 3a. 按 quest_event 字段精确匹配 (主路径: MC plugin 发送 quest_event)
        if quest_event_val:
            for trigger in (exp_spec.get("triggers") or []):
                if not isinstance(trigger, dict):
                    continue
                expected_qe = _derive_trigger_quest_event(trigger, level_id_str)
                if expected_qe and quest_event_val == expected_qe:
                    matched_triggers.append(trigger)

        # 3b. 按 event_type 模糊匹配备用路径 (直接发送 item_collect / proximity 等)
        if not matched_triggers and event_type:
            et_lower = event_type.lower()
            for trigger in (exp_spec.get("triggers") or []):
                if not isinstance(trigger, dict):
                    continue
                ttype = str(trigger.get("type") or "").strip().lower()
                if et_lower in (ttype, f"exp_{ttype}"):
                    matched_triggers.append(trigger)

        # 3c. 无触发器回退: 从 quest_event 命名模式推导虚拟触发器
        #     当 spec 没有定义 triggers 但 quest_event 遵循已知命名规则时适用
        if not matched_triggers and quest_event_val:
            inferred = _infer_trigger_from_quest_event(quest_event_val)
            if inferred:
                matched_triggers.append(inferred)
                logger.debug(
                    "experience_runtime_inferred_trigger",
                    extra={
                        "player_id": player_id_str,
                        "level_id": level_id_str,
                        "quest_event": quest_event_val,
                        "inferred_type": inferred.get("type"),
                    },
                )

        # 4. 应用触发器效果到 state
        state_before_snapshot = dict(current_state)  # Phase 6: capture before mutation
        state_updated = False
        for trigger in matched_triggers:
            current_state = _apply_trigger_to_state(current_state, trigger, payload)
            state_updated = True

        # 5. 评估规则
        triggered_rules: List[Dict[str, Any]] = []
        if state_updated:
            triggered_rules = _evaluate_rules(exp_spec, current_state)

        # 6. 决定 outcome (优先级: lose > win > unlock > grant)
        outcome: Optional[str] = None
        for priority_type in ("lose", "win", "unlock", "grant"):
            for rule in triggered_rules:
                if rule.get("type") == priority_type:
                    outcome = priority_type
                    break
            if outcome:
                break

        # 7. 持久化更新后的 state
        if state_updated:
            self._store.save_state(player_id_str, level_id_str, current_state)

        serialized_rules = [
            {
                "type": r.get("type"),
                "condition": r.get("condition"),
                "desc": r.get("desc"),
            }
            for r in triggered_rules
        ]
        triggers_matched_names = [str(t.get("type")) for t in matched_triggers]

        # 8. Phase 6: record to debug store
        try:
            _debug_store, _build_rule_eval = _get_debug_store()
            rule_evaluation = _build_rule_eval(exp_spec, current_state)
            _debug_store.record(
                player_id=player_id_str,
                level_id=level_id_str,
                event_type=event_type,
                payload=payload,
                state_before=state_before_snapshot,
                state_after=current_state,
                matched_triggers=triggers_matched_names,
                triggered_rules=serialized_rules,
                rule_evaluation=rule_evaluation,
                outcome=outcome,
            )
        except Exception as _debug_exc:
            logger.debug("experience_debug_record_failed: %s", _debug_exc)

        return {
            "experience_engine_active": True,
            "state_updated": state_updated,
            "triggered_rules": serialized_rules,
            "outcome": outcome,
            "current_state": dict(current_state),
            "triggers_matched": triggers_matched_names,
            "level_id": level_id_str,
        }


# 模块级单例
experience_runtime_engine = ExperienceRuntimeEngine()
