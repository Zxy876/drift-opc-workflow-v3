"""
experience_spec_compiler.py  —  Phase 3 Experience Spec Layer
==============================================================
输入：玩家自由文本（设计文档、海龟汤规则、桌游规则等）
输出：ExperienceSpec —— 包含 world/rules/triggers/state 的完整体验规格

设计原则
- LLM 提取规则语义（在 API_KEY 可用时）
- 本地正则回退（无 LLM 时仍可产出基础 Spec）
- 不依赖任何现有 spec_llm_v1 / scene_spec_validator 模块
- 零破坏性：只新增字段，不修改现有 payload 结构
"""

from __future__ import annotations

import json
import os
import re
import copy
from typing import Any, Dict, List, Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# LLM 配置
# ─────────────────────────────────────────────────────────────────────────────
API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat")

EXPERIENCE_SPEC_VERSION = "1.0"

# LLM System Prompt —— 严格结构化，防止注入
_SYSTEM_PROMPT = """\
你是 Drift ExperienceSpec 提取器。
只输出 JSON，禁止任何解释文字。
必须包含以下字段：
{
  "rules": [{"type": "win|lose|unlock|grant", "condition": "string", "desc": "string"}],
  "triggers": [
    {
      "type": "proximity|interact|item_collect|timer|npc_talk|guard_detect",
      "target": "string（语义名称，小写下划线，如 gem / altar / guard）",
      "action": "string",
      "desc": "string",
      "semantic": ["flying|ground|ranged|melee|night|hostile|neutral|magical|undead|water|fire|cave|boss|patrol|ghost|fast|slow|stealth|gem|crystal|gold|coin|scroll|key|artifact|star|soul"],
      "mc_material": "MC物品名（仅 item_collect 填写）",
      "mc_entity_type": "MC实体名（仅敌对/guard 类型填写）",
      "quantity": 3,
      "count": 1,
      "radius": 3.0,
      "spawn_radius": 5.0,
      "patrol_radius": 8.0,
      "aggro_range": 10.0
    }
  ],
  "state": {"variables": {"key": "type_string"}, "initial_values": {"key": "value"}},
  "npc_hints": ["string"],
  "beats": [
    {
      "id": "beat_UNIQUE_SNAKE_CASE",
      "trigger": "auto（仅 auto beat 填 auto；其他所有 beat 必须填 rule）",
      "rule_refs": ["quest_event_name"],
      "scene_patch": "beat_UNIQUE_SNAKE_CASE",
      "mc_narrative": {
        "title": "§6标题文字（可选）",
        "subtitle": "§7副标题（可选）",
        "tell": "§e叙事提示文字（必填，贴合剧情，不能是通用文字）",
        "sound": "minecraft:ambient.cave（可选，填MC音效名）"
      }
    }
  ]
}

【quest_event推导规则（beats必须用这个公式生成rule_refs）】
- trigger type=item_collect, target=X  → quest_event = "exp_collect_X"
- trigger type=proximity, target=X     → quest_event = "exp_proximity_X"
- trigger type=guard_detect, target=X  → quest_event = "exp_guard_detect_X"
- trigger type=interact, target=X      → quest_event = "exp_interact_X"
- 关卡进入（自动触发，不对应任何 trigger） → trigger = "auto", rule_refs = []

【beats生成规则】
- 必须生成4-5个beat：①auto（关卡进入开场） ②至少一个进度里程碑 ③win ④lose
- auto beat 必须设置 "trigger": "auto"，rule_refs 为空列表 []，进关卡时自动激活
- 所有非auto beat（进度/win/lose）必须设置 "trigger": "rule"，否则会被提前错误激活
- win beat 的 rule_refs = ["exp_win"]，lose beat 的 rule_refs = ["exp_lose"]
- 进度beat的 rule_refs 必须用上面公式从 triggers 推导，与 triggers 中的 type+target 严格对应
- mc_narrative.tell 必须贴合具体关卡剧情，禁止通用文字（禁止"挑战成功"/"任务失败"等）
- 每个beat的 scene_patch 字段值必须 = 该beat的 id 字段值（完全相同）

规则：
- item_collect 触发器：必须填 mc_material 和 semantic
- 敌对/guard 触发器：必须填 mc_entity_type 和 semantic，aggro_range 建议8-15
- 禁止包含 blocks/world_patch/mc/build 字段
- 禁止超过 1200 tokens

同时生成 rule_document 字段（玩家可读规则文档）：
{
    "rule_document": {
        "story_intro": "2-4句话的故事背景（第二人称，让玩家代入）",
        "objective": "一句话任务目标",
        "win_condition": "简明胜利条件",
        "lose_condition": "简明失败条件",
        "items_guide": [{"name": "物品语义名", "appearance": "外观描述", "how_to_get": "获取方式"}],
        "npc_guide": [{"name": "NPC名", "behavior": "行为", "interaction": "互动方式"}],
        "controls_hint": "操作提示（用 | 分隔）"
    }
}
"""

_FORBIDDEN_INPUTS = ("ignore previous", "忽略上面", "system:", "你现在是", "JAILBREAK")


# ─────────────────────────────────────────────────────────────────────────────
# 空 Spec 模板
# ─────────────────────────────────────────────────────────────────────────────
def _empty_experience_spec() -> Dict[str, Any]:
    return {
        "spec_version": EXPERIENCE_SPEC_VERSION,
        "scene_class": "CONTENT",
        "rules": [],
        "triggers": [],
        "state": {
            "variables": {},
            "initial_values": {},
        },
        "npc_hints": [],
        "compiler_mode": "empty",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 安全校验 — 防止 prompt injection
# ─────────────────────────────────────────────────────────────────────────────
def _is_safe_input(text: str) -> bool:
    lowered = (text or "").lower()
    for forbidden in _FORBIDDEN_INPUTS:
        if forbidden.lower() in lowered:
            return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 本地回退解析器（无 LLM 时）
# ─────────────────────────────────────────────────────────────────────────────
_WIN_KEYWORDS = ("赢", "胜利", "通关", "完成", "找到", "到达", "收集", "win", "complete", "collect all")
_LOSE_KEYWORDS = ("输", "失败", "死亡", "game over", "lose", "被发现", "掉落", "坠落")
_UNLOCK_KEYWORDS = ("解锁", "打开", "开启", "activate", "unlock", "open")
_GRANT_KEYWORDS = ("获得", "奖励", "得到", "acquire", "grant", "reward")
_PROXIMITY_KEYWORDS = ("靠近", "走近", "触碰", "进入", "step on", "enter", "near", "approach")
_ITEM_COLLECT_KEYWORDS = ("捡起", "收集", "拾取", "获取", "pick up", "collect", "gather")
_NPC_TALK_KEYWORDS = ("对话", "交谈", "询问", "说话", "talk to", "speak", "dialogue")
_TIMER_KEYWORDS = ("时间", "倒计时", "限时", "秒内", "minutes", "timer", "countdown")


def _extract_rules_local(text: str) -> List[Dict[str, Any]]:
    rules: List[Dict[str, Any]] = []
    sentences = re.split(r"[。！？\n.!?]", text)
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if any(kw in sent for kw in _WIN_KEYWORDS):
            rules.append({"type": "win", "condition": "player_achieves_goal", "desc": sent[:80]})
        if any(kw in sent for kw in _LOSE_KEYWORDS):
            rules.append({"type": "lose", "condition": "player_fails_condition", "desc": sent[:80]})
        if any(kw in sent for kw in _UNLOCK_KEYWORDS):
            rules.append({"type": "unlock", "condition": "trigger_activated", "desc": sent[:80]})
        if any(kw in sent for kw in _GRANT_KEYWORDS):
            rules.append({"type": "grant", "condition": "condition_met", "desc": sent[:80]})
    return rules[:8]  # 最多8条规则


def _extract_triggers_local(text: str) -> List[Dict[str, Any]]:
    triggers: List[Dict[str, Any]] = []
    sentences = re.split(r"[。！？\n.!?]", text)
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if any(kw in sent for kw in _PROXIMITY_KEYWORDS):
            triggers.append({
                "type": "proximity",
                "target": "zone",
                "action": "activate_event",
                "desc": sent[:80],
            })
        if any(kw in sent for kw in _ITEM_COLLECT_KEYWORDS):
            triggers.append({
                "type": "item_collect",
                "target": "item",
                "action": "update_state",
                "desc": sent[:80],
            })
        if any(kw in sent for kw in _NPC_TALK_KEYWORDS):
            triggers.append({
                "type": "npc_talk",
                "target": "npc",
                "action": "reveal_clue",
                "desc": sent[:80],
            })
        if any(kw in sent for kw in _TIMER_KEYWORDS):
            triggers.append({
                "type": "timer",
                "target": "countdown",
                "action": "trigger_lose_condition",
                "desc": sent[:80],
            })
    return triggers[:6]


def _extract_state_local(text: str, rules: List[Dict], triggers: List[Dict]) -> Dict[str, Any]:
    variables: Dict[str, str] = {}
    initial_values: Dict[str, Any] = {}

    # 从规则推断状态变量
    for rule in rules:
        rule_type = rule.get("type", "")
        if rule_type == "win":
            variables["goal_achieved"] = "bool"
            initial_values["goal_achieved"] = False
        elif rule_type == "lose":
            variables["player_alive"] = "bool"
            initial_values["player_alive"] = True
        elif rule_type == "unlock":
            variables["locked"] = "bool"
            initial_values["locked"] = True

    # 从触发器推断计数变量
    item_triggers = [t for t in triggers if t.get("type") == "item_collect"]
    if item_triggers:
        variables["collected_count"] = "int"
        initial_values["collected_count"] = 0

    # 从文本推断阶段状态
    if any(kw in text for kw in ("第一阶段", "phase 1", "阶段1", "第一关")):
        variables["current_phase"] = "int"
        initial_values["current_phase"] = 1

    return {"variables": variables, "initial_values": initial_values}


def _extract_npc_hints_local(text: str) -> List[str]:
    hints: List[str] = []
    sentences = re.split(r"[。！？\n.!?]", text)
    for sent in sentences:
        sent = sent.strip()
        if 10 <= len(sent) <= 50:
            if any(kw in sent for kw in ("提示", "线索", "你需要", "请", "必须", "先", "hint", "clue", "find")):
                hints.append(sent)
    return hints[:4]


# ─────────────────────────────────────────────────────────────────────────────
# 条件标准化：确保所有条件都是可评估的 "var op N" 格式
# ─────────────────────────────────────────────────────────────────────────────
_CANONICAL_CONDITION_RE = re.compile(
    r"([a-z_][a-z_0-9]*)\s*(>=|<=|==|>|<|!=)\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)


def _is_evaluable_condition(condition: str) -> bool:
    """检查条件是否可被运行时评估器解析。"""
    return bool(_CANONICAL_CONDITION_RE.search(condition or ""))


def _normalize_condition(condition: str, rule_type: str, triggers: list) -> str:
    """
    将不可评估的条件转换为可评估格式。

    规则：
    - "player_achieves_goal" → 根据 triggers 推导实际条件
    - "player_fails_condition" → 根据 triggers 推导失败条件
    - "trigger_activated" → 转为对应的状态变量检查
    - "XXX == true" → "XXX == 1"（布尔值转数值）
    - 已经是 "var op N" 格式 → 保持不变
    """
    condition = str(condition or "")
    normalized_bool = re.sub(r"==\s*true\b", "== 1", condition, flags=re.IGNORECASE)
    normalized_bool = re.sub(r"==\s*false\b", "== 0", normalized_bool, flags=re.IGNORECASE)

    if _is_evaluable_condition(normalized_bool):
        return normalized_bool

    item_triggers = [t for t in triggers if t.get("type") == "item_collect"]
    proximity_triggers = [t for t in triggers if t.get("type") == "proximity"]
    interact_triggers = [t for t in triggers if t.get("type") == "interact"]
    npc_triggers = [t for t in triggers if t.get("type") == "npc_talk"]
    timer_triggers = [t for t in triggers if t.get("type") == "timer"]

    if rule_type == "win":
        if item_triggers:
            quantity = int(item_triggers[0].get("quantity", 3))
            return f"collected_count >= {quantity}"
        if proximity_triggers:
            target = proximity_triggers[0].get("target", "zone").lower().replace(" ", "_")
            return f"visited_{target} == 1"
        if interact_triggers:
            target = interact_triggers[0].get("target", "object").lower().replace(" ", "_")
            return f"interacted_{target} == 1"
        if npc_triggers:
            target = npc_triggers[0].get("target", "npc").lower().replace(" ", "_")
            return f"talked_to_{target} == 1"
        return "progress >= 1"

    if rule_type == "lose":
        if timer_triggers:
            return "timer_fired == 1"
        guard_triggers = [t for t in triggers if t.get("type") == "guard_detect"]
        if guard_triggers:
            return "guard_detected == 1"
        return "player_alive == 0"

    if rule_type == "unlock":
        if interact_triggers:
            target = interact_triggers[0].get("target", "door").lower().replace(" ", "_")
            return f"interacted_{target} == 1"
        return "trigger_count >= 1"

    if rule_type == "grant":
        return "condition_count >= 1"

    return normalized_bool


def _normalize_all_conditions(spec: dict) -> dict:
    """标准化 spec 中所有规则的条件格式。"""
    rules = spec.get("rules") or []
    triggers = spec.get("triggers") or []

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        condition = str(rule.get("condition") or "")
        rule_type = str(rule.get("type") or "")
        normalized = _normalize_condition(condition, rule_type, triggers)
        if normalized != condition:
            rule["_original_condition"] = condition
            rule["condition"] = normalized

    return spec


def _ensure_state_variables(spec: dict) -> dict:
    """确保 state.initial_values 包含所有条件引用的变量。"""
    rules = spec.get("rules") or []
    state = spec.get("state") or {}
    variables = dict(state.get("variables") or {})
    initial_values = dict(state.get("initial_values") or {})

    for rule in rules:
        condition = str(rule.get("condition") or "")
        matches = _CANONICAL_CONDITION_RE.findall(condition)
        for var_name, _, _ in matches:
            if var_name in initial_values:
                continue
            if "alive" in var_name:
                initial_values[var_name] = 1
            else:
                initial_values[var_name] = 0
            variables[var_name] = "int"

    spec["state"] = {"variables": variables, "initial_values": initial_values}
    return spec


def _compile_local(text: str, scene_class: str) -> Dict[str, Any]:
    rules = _extract_rules_local(text)
    triggers = _extract_triggers_local(text)
    state = _extract_state_local(text, rules, triggers)
    npc_hints = _extract_npc_hints_local(text)

    return {
        "spec_version": EXPERIENCE_SPEC_VERSION,
        "scene_class": scene_class,
        "rules": rules,
        "triggers": triggers,
        "state": state,
        "npc_hints": npc_hints,
        "compiler_mode": "local_fallback",
    }


# ─────────────────────────────────────────────────────────────────────────────
# LLM 提取器
# ─────────────────────────────────────────────────────────────────────────────
def _call_llm(text: str) -> tuple[bool, Dict[str, Any] | str]:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False, "UNAVAILABLE"
    if not API_KEY:
        return False, "NO_API_KEY"

    # 截断过长输入（防止 token 超限）
    truncated = text[:2000] if len(text) > 2000 else text

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": truncated},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": 600,
    }

    try:
        response = requests.post(
            f"{BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(10, 30),
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return False, "PARSE_ERROR"
        return True, parsed
    except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError):
        return False, "LLM_ERROR"


def _validate_llm_spec(raw: Dict[str, Any]) -> bool:
    if not isinstance(raw.get("rules"), list):
        return False
    if not isinstance(raw.get("triggers"), list):
        return False
    if not isinstance(raw.get("state"), dict):
        return False
    # 安全检查：LLM 不应返回方块数据
    forbidden_keys = {"blocks", "world_patch", "mc", "build"}
    if forbidden_keys & set(raw.keys()):
        return False
    return True


def _compile_llm(text: str, scene_class: str) -> Dict[str, Any]:
    ok, result = _call_llm(text)
    if not ok or not isinstance(result, dict):
        return _compile_local(text, scene_class)

    if not _validate_llm_spec(result):
        return _compile_local(text, scene_class)

    return {
        "spec_version": EXPERIENCE_SPEC_VERSION,
        "scene_class": scene_class,
        "rules": list(result.get("rules") or []),
        "triggers": list(result.get("triggers") or []),
        "state": dict(result.get("state") or {"variables": {}, "initial_values": {}}),
        "npc_hints": list(result.get("npc_hints") or []),
        "beats": list(result.get("beats") or []),
        "rule_document": result.get("rule_document") if isinstance(result.get("rule_document"), dict) else None,
        "compiler_mode": "llm",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 公共接口
# ─────────────────────────────────────────────────────────────────────────────
def compile_experience_spec(
    text: str,
    *,
    scene_class: str = "CONTENT",
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    将玩家设计文本编译为 ExperienceSpec。

    Args:
        text: 玩家输入的设计文本（海龟汤/桌游规则/剧情描述等）
        scene_class: 场景分类（CONTENT/RULE/SIMULATION），来自 classify_scene()
        use_llm: 是否调用 LLM 提取（默认True，测试环境自动跳过）

    Returns:
        ExperienceSpec dict，包含：
            spec_version, scene_class, rules, triggers, state, npc_hints, compiler_mode
    """
    normalized = (text or "").strip()
    if not normalized:
        spec = _empty_experience_spec()
        spec["scene_class"] = scene_class
        return spec

    # 安全检查
    if not _is_safe_input(normalized):
        spec = _empty_experience_spec()
        spec["scene_class"] = scene_class
        spec["compiler_mode"] = "blocked_unsafe_input"
        return spec

    if use_llm and API_KEY:
        spec = _compile_llm(normalized, scene_class)
    else:
        spec = _compile_local(normalized, scene_class)

    # 条件标准化 + 状态变量补全
    spec = _normalize_all_conditions(spec)
    spec = _ensure_state_variables(spec)

    try:
        from app.core.runtime.rule_document_generator import generate_rule_document

        existing_rule_doc = spec.get("rule_document")
        if not isinstance(existing_rule_doc, dict):
            spec["rule_document"] = generate_rule_document(spec, normalized, use_llm=use_llm)
    except Exception:
        pass

    return spec


def experience_spec_summary(spec: Dict[str, Any]) -> Dict[str, Any]:
    """提取 ExperienceSpec 的摘要信息，用于 progress_log / response。"""
    if not isinstance(spec, dict):
        return {"is_empty": True}

    rules = spec.get("rules") or []
    triggers = spec.get("triggers") or []
    state = spec.get("state") or {}
    variables = state.get("variables") or {}

    return {
        "spec_version": spec.get("spec_version", EXPERIENCE_SPEC_VERSION),
        "scene_class": spec.get("scene_class", "CONTENT"),
        "compiler_mode": spec.get("compiler_mode", "unknown"),
        "rule_count": len(rules),
        "trigger_count": len(triggers),
        "beat_count": len(spec.get("beats") or []),
        "state_variable_count": len(variables),
        "npc_hint_count": len(spec.get("npc_hints") or []),
        "has_win_condition": any(r.get("type") == "win" for r in rules),
        "has_lose_condition": any(r.get("type") == "lose" for r in rules),
        "has_proximity_triggers": any(t.get("type") == "proximity" for t in triggers),
        "is_empty": not rules and not triggers and not variables,
    }


def validate_spec_completeness(spec: dict) -> list[str]:
    """
    验证 ExperienceSpec 的完整性和可执行性。
    返回警告消息列表。
    """
    warnings: list[str] = []
    rules = spec.get("rules") or []
    triggers = spec.get("triggers") or []
    state = spec.get("state") or {}
    initial_values = state.get("initial_values") or {}

    if rules and not triggers:
        warnings.append("⚠ 有规则但无触发器：规则条件永远不会被满足。请添加至少一个触发器（如 item_collect, proximity 等）。")

    if triggers and not rules:
        warnings.append("⚠ 有触发器但无规则：事件会触发但没有胜利/失败判定。请添加 win 或 lose 规则。")

    for rule in rules:
        condition = str(rule.get("condition") or "")
        matches = _CANONICAL_CONDITION_RE.findall(condition)
        for var_name, _, _ in matches:
            if var_name not in initial_values:
                warnings.append(f"⚠ 规则条件引用变量 '{var_name}' 但初始状态中未定义。")

    for rule in rules:
        condition = str(rule.get("condition") or "")
        if condition and not _is_evaluable_condition(condition):
            warnings.append(f"⚠ 规则条件 '{condition}' 不是可评估格式（需要 'var op N' 格式）。")

    has_win = any(r.get("type") == "win" for r in rules)
    if not has_win and rules:
        warnings.append("⚠ 没有 win（胜利）条件：玩家无法通关。")

    has_collect_trigger = any(t.get("type") == "item_collect" for t in triggers)
    has_collect_condition = any(
        "collected" in str(r.get("condition", "")) or "count" in str(r.get("condition", ""))
        for r in rules
    )
    if has_collect_trigger and not has_collect_condition:
        warnings.append("⚠ 有 item_collect 触发器但没有收集计数条件。触发事件不会推进规则。")

    return warnings
