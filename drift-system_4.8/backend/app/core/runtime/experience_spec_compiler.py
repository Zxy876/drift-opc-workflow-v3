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
    "game_type": "adventure|puzzle|parkour|racing|survival|stealth|board|quiz|build|tower_defense",
  "rules": [{"type": "win|lose|unlock|grant", "condition": "string", "desc": "string"}],
  "triggers": [
    {
            "type": "proximity|interact|item_collect|timer|npc_talk|guard_detect|block_place|lever_toggle|checkpoint_reach|fall_detect|wave_start|wave_clear|mob_kill|player_damage|detection_alert|piece_place|turn_end|answer_submit|structure_match",
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

新增 game_type 字段（根据设计文本自动判断）：
"game_type": "adventure|puzzle|parkour|racing|survival|stealth|board|quiz|build|tower_defense"

各类型对应 trigger 关系：
- adventure: proximity, interact, item_collect, timer, npc_talk, guard_detect
- puzzle: interact, lever_toggle, block_place, item_collect
- parkour: checkpoint_reach, fall_detect, timer, proximity
- racing: checkpoint_reach, timer, proximity
- survival: wave_start, wave_clear, mob_kill, player_damage, timer
- stealth: detection_alert, guard_detect, proximity, timer
- board: piece_place, turn_end, block_place
- quiz: answer_submit, npc_talk, timer
- build: block_place, structure_match, timer
- tower_defense: wave_start, wave_clear, mob_kill, block_place

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

【游戏类型识别规则】
根据设计文本识别游戏类型，输出到 "game_type" 字段：
- 收集/到达/躲避/NPC -> adventure
- 解谜/机关/拉杆/密码/逻辑 -> puzzle
- 跑酷/跳跃/障碍/平台 -> parkour
- 竞速/赛跑/最快/计时 -> racing
- 生存/波次/怪物/防御 -> survival
- 潜行/躲藏/不被发现 -> stealth
- 棋/下棋/落子/对弈 -> board
- 问答/答题/知识/竞猜 -> quiz
- 建造/搭建/设计/复制 -> build
- 塔防/放塔/守路/波次 -> tower_defense

如果无法识别类型，默认 "adventure"。

各类型 condition 写法示范：
- puzzle win: "steps_completed >= 5" / "puzzle_solved >= 1"
- parkour win: "checkpoints_reached >= 10" / "course_completed == 1"
- racing win: "laps_completed >= 3"
- survival win: "waves_survived >= 5"
- stealth win: "areas_cleared >= 3" (lose: "times_detected >= 3")
- board win: "winner == 1"
- quiz win: "correct_count >= 7"
- build win: "match_score >= 80"
- tower_defense win: "waves_survived >= 10" (lose: "lives_remaining == 0")

各类型 state 初始值示范：
- puzzle: {"steps_completed": 0, "puzzle_solved": 0}
- parkour: {"checkpoints_reached": 0, "falls_count": 0, "course_completed": 0}
- racing: {"laps_completed": 0, "checkpoints_hit": 0, "race_time_ms": 0}
- survival: {"current_wave": 0, "mobs_killed": 0, "player_health": 20, "waves_survived": 0}
- stealth: {"stealth_level": 100, "times_detected": 0, "areas_cleared": 0}
- board: {"moves_count": 0, "current_turn": 0, "winner": 0}
- quiz: {"score": 0, "questions_answered": 0, "correct_count": 0}
- build: {"blocks_placed": 0, "match_score": 0, "build_complete": 0}
- tower_defense: {"towers_placed": 0, "enemies_killed": 0, "lives_remaining": 10, "gold": 100}
"""

_FORBIDDEN_INPUTS = ("ignore previous", "忽略上面", "system:", "你现在是", "JAILBREAK")


# ─────────────────────────────────────────────────────────────────────────────
# 空 Spec 模板
# ─────────────────────────────────────────────────────────────────────────────
def _empty_experience_spec() -> Dict[str, Any]:
    return {
        "spec_version": EXPERIENCE_SPEC_VERSION,
        "scene_class": "CONTENT",
        "game_type": "adventure",
        "game_type_confidence": 0.5,
        "game_type_config": {},
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


# ─────────────────────────────────────────────────────────────────────────────
# 游戏类型自动分类
# ─────────────────────────────────────────────────────────────────────────────
GAME_TYPE_KEYWORDS: Dict[str, Dict[str, List[str]]] = {
    "puzzle": {
        "zh": ["解谜", "谜题", "密码", "机关", "拼图", "逻辑", "开锁", "暗号", "线索", "推理", "密室", "解密", "破解", "钥匙", "锁", "按钮", "拉杆", "压力板", "红石"],
        "en": ["puzzle", "riddle", "cipher", "mechanism", "logic", "combination", "clue", "mystery", "lever", "button", "pressure plate", "redstone"],
    },
    "parkour": {
        "zh": ["跑酷", "跳跃", "攀爬", "障碍", "平台", "空中", "跳台", "赛道", "关卡跳"],
        "en": ["parkour", "jump", "climb", "obstacle course", "platform", "obby"],
    },
    "racing": {
        "zh": ["竞速", "赛跑", "比赛", "速度", "冲刺", "赛道", "圈速", "最快"],
        "en": ["race", "racing", "speed run", "sprint", "track", "lap", "fastest", "time trial"],
    },
    "survival": {
        "zh": ["生存", "波次", "防御", "抵挡", "存活", "怪物潮", "僵尸围城", "守护", "击杀", "怪物", "血量", "治疗"],
        "en": ["survive", "wave", "defend", "horde", "zombie", "protect", "last stand", "mob", "health", "heal", "combat"],
    },
    "stealth": {
        "zh": ["潜行", "隐匿", "躲藏", "不被发现", "偷偷", "暗杀", "捉迷藏", "隐身"],
        "en": ["stealth", "hide", "sneak", "undetected", "invisible", "seek", "spy", "covert"],
    },
    "board": {
        "zh": ["棋", "五子棋", "围棋", "象棋", "井字", "下棋", "落子", "棋盘", "对弈", "黑白", "棋子", "连珠"],
        "en": ["chess", "gomoku", "go", "checkers", "tic-tac-toe", "board game", "piece", "grid", "five in a row"],
    },
    "quiz": {
        "zh": ["问答", "答题", "知识", "竞猜", "选择题", "判断题", "考试", "测验", "抢答", "提问", "回答", "问", "题"],
        "en": ["quiz", "trivia", "question", "answer", "test", "exam", "knowledge"],
    },
    "build": {
        "zh": ["建造", "搭建", "建筑", "复制", "还原", "盖房", "建城", "结构"],
        "en": ["build", "construct", "create", "replicate", "architecture", "build battle"],
    },
    "tower_defense": {
        "zh": ["塔防", "防御塔", "放置塔", "敌人路线", "守塔", "据点"],
        "en": ["tower defense", "td", "place tower", "defend path", "turret"],
    },
}

GAME_TYPE_SUPPORT_LEVEL: Dict[str, Dict[str, Any]] = {
    "adventure": {"tier": 0, "supported": True, "label": "动作冒险", "label_en": "Action Adventure"},
    "puzzle": {"tier": 1, "supported": True, "label": "解谜", "label_en": "Puzzle"},
    "parkour": {"tier": 1, "supported": True, "label": "跑酷", "label_en": "Parkour"},
    "racing": {"tier": 1, "supported": True, "label": "竞速", "label_en": "Racing"},
    "survival": {"tier": 1, "supported": True, "label": "生存防御", "label_en": "Survival"},
    "stealth": {"tier": 1, "supported": True, "label": "潜行隐匿", "label_en": "Stealth"},
    "board": {"tier": 2, "supported": True, "label": "棋盘对弈", "label_en": "Board Game"},
    "quiz": {"tier": 2, "supported": True, "label": "问答竞猜", "label_en": "Quiz/Trivia"},
    "build": {"tier": 2, "supported": True, "label": "建造创意", "label_en": "Build Battle"},
    "tower_defense": {"tier": 2, "supported": True, "label": "塔防策略", "label_en": "Tower Defense"},
}

UNSUPPORTED_GAME_KEYWORDS: Dict[str, Dict[str, Any]] = {
    "card_game": {
        "zh": ["卡牌", "扑克", "纸牌", "斗地主", "炉石", "卡组"],
        "en": ["card game", "poker", "card battle", "deck", "hearthstone"],
        "tip": "卡牌类游戏需要复杂的手牌UI，建议改为问答类或收集类玩法",
    },
    "rhythm": {
        "zh": ["音乐", "节奏", "节拍", "弹奏", "音符"],
        "en": ["rhythm", "music game", "beat", "note", "dance"],
        "tip": "音乐节奏类需要精确音频同步，建议用NPC对话+计时器实现简化版",
    },
    "sports": {
        "zh": ["足球", "篮球", "排球", "乒乓", "羽毛球", "高尔夫"],
        "en": ["soccer", "basketball", "football", "volleyball", "tennis", "golf"],
        "tip": "MC缺少球类物理，建议改为竞速类或用雪球模拟投掷玩法",
    },
    "mmorpg": {
        "zh": ["网游", "公会", "副本", "装备强化", "角色扮演"],
        "en": ["mmorpg", "guild", "dungeon", "raid", "gear"],
        "tip": "MMORPG需要大量持久化系统，建议简化为单关卡冒险+收集",
    },
}


def classify_game_type(text: str) -> tuple[str, float, list[str]]:
    """从设计文本自动识别游戏类型。"""
    text_lower = (text or "").lower()
    warnings: list[str] = []

    for unsup_type, info in UNSUPPORTED_GAME_KEYWORDS.items():
        for kw in list(info.get("zh", [])) + list(info.get("en", [])):
            if kw in text_lower:
                warnings.append(f"[UNSUPPORTED_GAME_TYPE:{unsup_type}] {info['tip']}")
                break

    scores: Dict[str, int] = {}
    for game_type, keywords in GAME_TYPE_KEYWORDS.items():
        score = 0
        for kw in keywords.get("zh", []) + keywords.get("en", []):
            if kw in text_lower:
                score += 1
        scores[game_type] = score

    best_type = max(scores, key=scores.get) if scores else "adventure"
    best_score = scores.get(best_type, 0)
    if best_score == 0:
        return "adventure", 0.5, warnings

    total_kw = len(GAME_TYPE_KEYWORDS.get(best_type, {}).get("zh", [])) + len(GAME_TYPE_KEYWORDS.get(best_type, {}).get("en", []))
    confidence = min(best_score / max(total_kw * 0.3, 1), 1.0)
    return best_type, confidence, warnings


def _get_default_game_type_config(game_type: str) -> Dict[str, Any]:
    """返回游戏类型的默认配置。"""
    defaults = {
        "adventure": {},
        "puzzle": {"max_attempts": 10, "hint_after_fails": 3},
        "parkour": {"checkpoints": 5, "time_limit_sec": 300, "allow_fall_reset": True},
        "racing": {"laps": 3, "checkpoints_per_lap": 5},
        "survival": {"waves": 5, "mobs_per_wave": 10, "rest_between_waves_sec": 15},
        "stealth": {"max_detections": 3, "patrol_speed": "slow"},
        "board": {"board_size": 9, "win_pattern": "five_in_row", "npc_difficulty": "easy"},
        "quiz": {"questions_count": 10, "time_per_question_sec": 30, "pass_score": 7},
        "build": {"time_limit_sec": 300, "reference_structure": True},
        "tower_defense": {"waves": 10, "starting_gold": 100, "tower_types": 3},
    }
    return defaults.get(game_type, {})


def _extract_first_int(text: str, default: int) -> int:
    m = re.search(r"(\d+)", text or "")
    if not m:
        return default
    try:
        return int(m.group(1))
    except Exception:
        return default


def _merge_state_defaults(spec: Dict[str, Any], variables: Dict[str, str], initial_values: Dict[str, Any]) -> None:
    state = dict(spec.get("state") or {})
    vars_old = dict(state.get("variables") or {})
    init_old = dict(state.get("initial_values") or {})
    vars_old.update({k: v for k, v in variables.items() if k not in vars_old})
    init_old.update({k: v for k, v in initial_values.items() if k not in init_old})
    spec["state"] = {"variables": vars_old, "initial_values": init_old}


def _apply_game_type_templates(spec: Dict[str, Any], text: str, game_type: str) -> None:
    rules = list(spec.get("rules") or [])
    triggers = list(spec.get("triggers") or [])

    if game_type in ("board", "parkour", "quiz"):
        generic_conditions = {"player_achieves_goal", "player_fails_condition", "trigger_activated", "condition_met"}
        rules = [r for r in rules if str(r.get("condition") or "") not in generic_conditions]

    def has_rule_var(name: str) -> bool:
        return any(name in str(r.get("condition") or "") for r in rules if isinstance(r, dict))

    def has_trigger(ttype: str) -> bool:
        return any(str(t.get("type") or "").lower() == ttype for t in triggers if isinstance(t, dict))

    if game_type == "board":
        if not any(r.get("type") == "win" for r in rules if isinstance(r, dict)):
            rules.append({"type": "win", "condition": "winner == 1", "desc": "先连成五子的一方获胜"})
        if not has_rule_var("moves_count"):
            rules.append({"type": "win", "condition": "moves_count >= 25", "desc": "25步内完成对弈（备用条件）"})
        if not has_trigger("piece_place"):
            triggers.append({"type": "piece_place", "target": "board", "action": "place_piece", "desc": "玩家落子"})
        if not has_trigger("turn_end"):
            triggers.append({"type": "turn_end", "target": "board", "action": "switch_turn", "desc": "回合切换"})
        _merge_state_defaults(
            spec,
            {"moves_count": "int", "current_turn": "int", "winner": "int"},
            {"moves_count": 0, "current_turn": 0, "winner": 0},
        )

    elif game_type == "parkour":
        checkpoints = _extract_first_int(text, 10)
        falls = 3
        m_falls = re.search(r"掉落\s*(\d+)\s*次", text or "")
        if m_falls:
            falls = int(m_falls.group(1))
        time_limit = 300
        m_min = re.search(r"(\d+)\s*分钟", text or "")
        if m_min:
            time_limit = int(m_min.group(1)) * 60
        if not has_rule_var("checkpoints_reached"):
            rules.append({"type": "win", "condition": f"checkpoints_reached >= {checkpoints}", "desc": "到达终点"})
        if not has_rule_var("falls_count"):
            rules.append({"type": "lose", "condition": f"falls_count >= {falls}", "desc": f"掉落{falls}次以上"})
        if not has_rule_var("timer_fired"):
            rules.append({"type": "lose", "condition": "timer_fired == 1", "desc": "超时"})
        if not has_trigger("checkpoint_reach"):
            triggers.append({"type": "checkpoint_reach", "target": "platform", "action": "reach_checkpoint", "desc": "到达检查点"})
        if not has_trigger("fall_detect"):
            triggers.append({"type": "fall_detect", "target": "ground", "action": "count_fall", "desc": "坠落计数"})
        if has_trigger("timer"):
            for t in triggers:
                if str(t.get("type") or "").lower() == "timer":
                    t["target"] = "countdown"
                    t["action"] = "trigger_lose"
                    t["quantity"] = time_limit
                    break
        else:
            triggers.append({"type": "timer", "target": "countdown", "action": "trigger_lose", "quantity": time_limit, "desc": "限时挑战"})
        _merge_state_defaults(
            spec,
            {"checkpoints_reached": "int", "falls_count": "int", "course_completed": "int"},
            {"checkpoints_reached": 0, "falls_count": 0, "course_completed": 0},
        )

    elif game_type == "quiz":
        pass_score = 7
        m_score = re.search(r"答对\s*(\d+)", text or "")
        if m_score:
            pass_score = int(m_score.group(1))
        q_time = 30
        m_sec = re.search(r"(\d+)\s*秒", text or "")
        if m_sec:
            q_time = int(m_sec.group(1))
        if not has_rule_var("correct_count"):
            rules.append({"type": "win", "condition": f"correct_count >= {pass_score}", "desc": f"答对{pass_score}个以上"})
        if not has_trigger("answer_submit"):
            triggers.append({"type": "answer_submit", "target": "quiz", "action": "check_answer", "desc": "提交答案"})
        if not has_trigger("npc_talk"):
            triggers.append({"type": "npc_talk", "target": "examiner", "action": "ask_question", "desc": "NPC提问"})
        if has_trigger("timer"):
            for t in triggers:
                if str(t.get("type") or "").lower() == "timer":
                    t["target"] = "question_timer"
                    t["action"] = "next_question"
                    t["quantity"] = q_time
                    break
        else:
            triggers.append({"type": "timer", "target": "question_timer", "action": "next_question", "quantity": q_time, "desc": "每题限时"})
        _merge_state_defaults(
            spec,
            {"score": "int", "questions_answered": "int", "correct_count": "int"},
            {"score": 0, "questions_answered": 0, "correct_count": 0},
        )

    elif game_type == "puzzle":
        keys_needed = _extract_first_int(text, 3)
        if not has_rule_var("steps_completed"):
            rules.append({"type": "win", "condition": f"steps_completed >= {keys_needed}", "desc": f"完成{keys_needed}个解谜步骤"})
        if not has_trigger("lever_toggle"):
            triggers.append({"type": "lever_toggle", "target": "puzzle_mechanism", "action": "toggle", "desc": "拉杆/机关操作"})
        if not has_trigger("item_collect"):
            triggers.append({"type": "item_collect", "target": "key", "action": "collect", "desc": "收集钥匙/道具"})
        _merge_state_defaults(
            spec,
            {"steps_completed": "int", "keys_found": "int"},
            {"steps_completed": 0, "keys_found": 0},
        )

    elif game_type == "survival":
        total_waves = _extract_first_int(text, 5)
        if not has_rule_var("waves_survived"):
            rules.append({"type": "win", "condition": f"waves_survived >= {total_waves}", "desc": f"存活{total_waves}波"})
        if not has_rule_var("player_health"):
            rules.append({"type": "lose", "condition": "player_health <= 0", "desc": "生命值归零"})
        if not has_trigger("wave_start"):
            triggers.append({"type": "wave_start", "target": "arena", "action": "spawn_wave", "desc": "波次开始"})
        if not has_trigger("wave_clear"):
            triggers.append({"type": "wave_clear", "target": "arena", "action": "clear_wave", "desc": "波次清除"})
        if not has_trigger("mob_kill"):
            triggers.append({"type": "mob_kill", "target": "hostile", "action": "kill_mob", "desc": "击杀怪物"})
        if not has_trigger("player_damage"):
            triggers.append({"type": "player_damage", "target": "player", "action": "take_damage", "desc": "受到伤害"})
        _merge_state_defaults(
            spec,
            {"waves_survived": "int", "current_wave": "int", "mobs_killed": "int", "player_health": "int", "player_alive": "int"},
            {"waves_survived": 0, "current_wave": 0, "mobs_killed": 0, "player_health": 20, "player_alive": 1},
        )

    elif game_type == "stealth":
        checkpoints = _extract_first_int(text, 3)
        if not has_rule_var("checkpoints_reached"):
            rules.append({"type": "win", "condition": f"checkpoints_reached >= {checkpoints}", "desc": f"到达{checkpoints}个检查点"})
        if not has_rule_var("stealth_broken"):
            rules.append({"type": "lose", "condition": "stealth_broken == 1", "desc": "被发现"})
        if not has_trigger("checkpoint_reach"):
            triggers.append({"type": "checkpoint_reach", "target": "stealth_point", "action": "reach_checkpoint", "desc": "到达检查点"})
        if not has_trigger("detection_alert"):
            triggers.append({"type": "detection_alert", "target": "guard", "action": "detect_player", "desc": "被守卫发现"})
        _merge_state_defaults(
            spec,
            {"checkpoints_reached": "int", "stealth_broken": "int", "times_detected": "int"},
            {"checkpoints_reached": 0, "stealth_broken": 0, "times_detected": 0},
        )

    elif game_type == "racing":
        laps = _extract_first_int(text, 3)
        time_limit = 300
        m_min = re.search(r"(\d+)\s*分钟", text or "")
        if m_min:
            time_limit = int(m_min.group(1)) * 60
        if not has_rule_var("checkpoints_reached"):
            rules.append({"type": "win", "condition": f"checkpoints_reached >= {laps}", "desc": f"完成{laps}圈"})
        if not has_rule_var("timer_fired"):
            rules.append({"type": "lose", "condition": "timer_fired == 1", "desc": "超时"})
        if not has_trigger("checkpoint_reach"):
            triggers.append({"type": "checkpoint_reach", "target": "lap_marker", "action": "complete_lap", "desc": "完成一圈"})
        if not has_trigger("timer"):
            triggers.append({"type": "timer", "target": "race_countdown", "action": "trigger_lose", "quantity": time_limit, "desc": "限时"})
        _merge_state_defaults(
            spec,
            {"checkpoints_reached": "int", "timer_fired": "int"},
            {"checkpoints_reached": 0, "timer_fired": 0},
        )

    elif game_type == "tower_defense":
        total_waves = _extract_first_int(text, 5)
        if not has_rule_var("waves_survived"):
            rules.append({"type": "win", "condition": f"waves_survived >= {total_waves}", "desc": f"防御{total_waves}波敌人"})
        if not has_rule_var("base_health"):
            rules.append({"type": "lose", "condition": "base_health <= 0", "desc": "据点被摧毁"})
        if not has_trigger("wave_start"):
            triggers.append({"type": "wave_start", "target": "spawn_point", "action": "spawn_wave", "desc": "敌人波次开始"})
        if not has_trigger("wave_clear"):
            triggers.append({"type": "wave_clear", "target": "spawn_point", "action": "clear_wave", "desc": "波次清除"})
        if not has_trigger("block_place"):
            triggers.append({"type": "block_place", "target": "defense_tower", "action": "place_tower", "desc": "放置防御塔"})
        _merge_state_defaults(
            spec,
            {"waves_survived": "int", "current_wave": "int", "towers_placed": "int", "base_health": "int"},
            {"waves_survived": 0, "current_wave": 0, "towers_placed": 0, "base_health": 100},
        )

    elif game_type == "build":
        if not has_rule_var("build_complete"):
            rules.append({"type": "win", "condition": "build_complete == 1", "desc": "建造完成"})
        if not has_trigger("block_place"):
            triggers.append({"type": "block_place", "target": "structure", "action": "place_block", "desc": "放置方块"})
        if not has_trigger("structure_match"):
            triggers.append({"type": "structure_match", "target": "blueprint", "action": "check_match", "desc": "结构匹配检测"})
        _merge_state_defaults(
            spec,
            {"blocks_placed": "int", "build_complete": "int", "match_score": "float"},
            {"blocks_placed": 0, "build_complete": 0, "match_score": 0.0},
        )

    spec["rules"] = rules
    spec["triggers"] = triggers


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

    game_type, gt_confidence, gt_warnings = classify_game_type(normalized)
    support_info = GAME_TYPE_SUPPORT_LEVEL.get(game_type, {})
    if not support_info.get("supported", False):
        game_type = "adventure"
        gt_confidence = 0.5

    spec["game_type"] = game_type
    spec["game_type_confidence"] = gt_confidence
    spec["game_type_config"] = _get_default_game_type_config(game_type)

    _apply_game_type_templates(spec, normalized, game_type)

    # 条件标准化 + 状态变量补全
    spec = _normalize_all_conditions(spec)
    spec = _ensure_state_variables(spec)

    if gt_warnings:
        existing_warnings = list(spec.get("_compile_warnings", []))
        existing_warnings.extend(gt_warnings)
        spec["_compile_warnings"] = existing_warnings

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
        "game_type": spec.get("game_type", "adventure"),
        "game_type_confidence": spec.get("game_type_confidence", 0.5),
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

    has_checkpoint_trigger = any(t.get("type") in ("checkpoint_reach", "course_complete") for t in triggers)
    has_checkpoint_condition = any(
        "checkpoints_reached" in str(r.get("condition", "")) or "course_completed" in str(r.get("condition", ""))
        for r in rules
    )
    if has_checkpoint_trigger and not has_checkpoint_condition:
        warnings.append("⚠ 有 checkpoint_reach/course_complete 触发器但没有检查点进度条件（如 checkpoints_reached >= N）。")

    has_answer_trigger = any(t.get("type") == "answer_submit" for t in triggers)
    has_answer_condition = any(
        "correct_count" in str(r.get("condition", "")) or "score" in str(r.get("condition", ""))
        for r in rules
    )
    if has_answer_trigger and not has_answer_condition:
        warnings.append("⚠ 有 answer_submit 触发器但没有答题得分条件（如 correct_count >= N）。")

    has_fall_trigger = any(t.get("type") == "fall_detect" for t in triggers)
    has_fall_condition = any("falls_count" in str(r.get("condition", "")) for r in rules)
    if has_fall_trigger and not has_fall_condition:
        warnings.append("⚠ 有 fall_detect 触发器但没有跌落相关条件（如 falls_count >= N）。")

    has_piece_trigger = any(t.get("type") == "piece_place" for t in triggers)
    has_piece_condition = any(
        "moves_count" in str(r.get("condition", "")) or "winner" in str(r.get("condition", ""))
        for r in rules
    )
    if has_piece_trigger and not has_piece_condition:
        warnings.append("⚠ 有 piece_place 触发器但没有棋盘进度条件（如 moves_count 或 winner）。")

    return warnings
