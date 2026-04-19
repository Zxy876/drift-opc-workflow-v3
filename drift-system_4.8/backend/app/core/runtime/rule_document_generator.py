"""
rule_document_generator.py — RuleDocument 自动生成器
=====================================================
将 ExperienceSpec（机器可执行规则）转换为 RuleDocument（玩家可读文档）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import requests


API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat")


@dataclass
class ItemGuide:
    name: str
    mc_item: str
    appearance: str
    how_to_get: str


@dataclass
class NpcGuide:
    name: str
    mc_entity: str
    behavior: str
    interaction: str


@dataclass
class RuleDocument:
    story_intro: str = ""
    objective: str = ""
    win_condition: str = ""
    lose_condition: str = ""
    time_limit: Optional[int] = None
    items_guide: List[ItemGuide] = field(default_factory=list)
    npc_guide: List[NpcGuide] = field(default_factory=list)
    controls_hint: str = ""
    difficulty_label: str = ""


MATERIAL_DESCRIPTIONS: Dict[str, str] = {
    "EMERALD": "绿色菱形宝石",
    "DIAMOND": "蓝色钻石",
    "GOLD_INGOT": "金色锭块",
    "IRON_INGOT": "银色铁锭",
    "AMETHYST_SHARD": "紫色水晶碎片",
    "NETHER_STAR": "白色发光之星",
    "ECHO_SHARD": "深蓝色回响碎片",
    "HEART_OF_THE_SEA": "蓝色海洋之心",
    "PAPER": "白色纸张",
    "BOOK": "棕色书本",
    "MAP": "纸质地图",
    "COMPASS": "铁制指南针",
    "CLOCK": "金色时钟",
    "NAME_TAG": "标签牌",
    "BLAZE_POWDER": "橙色烈焰粉",
    "ENDER_PEARL": "暗绿色末影珍珠",
    "GHAST_TEAR": "白色恶魂之泪",
    "GLOWSTONE_DUST": "黄色荧石粉",
    "REDSTONE": "红色红石粉",
    "LAPIS_LAZULI": "蓝色青金石",
    "COAL": "黑色煤炭",
    "QUARTZ": "白色石英",
    "PRISMARINE_SHARD": "青色海晶碎片",
    "RABBIT_FOOT": "兔子脚",
    "FEATHER": "白色羽毛",
    "BONE": "白色骨头",
    "STRING": "白色线",
    "SLIME_BALL": "绿色粘液球",
    "SNOWBALL": "白色雪球",
    "ARROW": "箭矢",
    "TOTEM_OF_UNDYING": "金色不死图腾",
}

ENTITY_DESCRIPTIONS: Dict[str, str] = {
    "ZOMBIE": "绿色僵尸，缓慢移动",
    "SKELETON": "白色骷髅，远程射箭",
    "SPIDER": "黑色蜘蛛，快速爬行",
    "CREEPER": "绿色爬行者，靠近爆炸",
    "ENDERMAN": "黑色末影人，高大瞬移",
    "BLAZE": "橙色烈焰人，发射火球",
    "WITCH": "紫衣女巫，投掷药水",
    "PILLAGER": "灰衣掠夺者，持弩攻击",
    "VINDICATOR": "灰衣卫道士，持斧攻击",
    "PHANTOM": "蓝色幻翼，夜间飞行",
    "DROWNED": "蓝色溺尸，水下出没",
    "HUSK": "沙色尸壳，沙漠出没",
    "STRAY": "灰色流浪者，射冰箭",
    "WARDEN": "深蓝色循声守卫，极强",
    "IRON_GOLEM": "铁色铁傀儡，守护型",
    "VILLAGER": "村民，可交易对话",
    "WOLF": "灰色狼，可驯服",
    "CAT": "猫，可驯服",
    "FOX": "橙色狐狸，会偷物品",
}

CONTROLS_TEMPLATES: Dict[str, str] = {
    "item_collect": "靠近物品自动拾取",
    "proximity": "走到指定区域触发事件",
    "npc_talk": "右键点击 NPC 或在聊天框输入关键词对话",
    "timer": "注意右上角倒计时",
    "guard_detect": "远离守卫，不要进入其侦测范围",
    "interact": "右键点击目标物体",
}


def _generate_local_fallback(spec: Dict[str, Any], design_text: str) -> RuleDocument:
    rules = spec.get("rules") or []
    triggers = spec.get("triggers") or []
    npc_hints = spec.get("npc_hints") or []
    beats = spec.get("beats") or []
    state = spec.get("state") or {}

    story_intro = ""
    for beat in beats:
        if beat.get("trigger") == "auto":
            mc_nar = beat.get("mc_narrative") or {}
            story_intro = mc_nar.get("tell", "")
            break
    if not story_intro:
        sentences = re.split(r"[。！？\n.!?]", (design_text or "").strip())
        story_intro = "。".join(s.strip() for s in sentences[:2] if s.strip())
    if not story_intro:
        story_intro = "欢迎进入关卡！"

    win_descs = [r["desc"] for r in rules if r.get("type") == "win" and r.get("desc")]
    objective = "；".join(win_descs) if win_descs else "完成关卡目标"

    win_condition = " 且 ".join(
        r.get("desc", r.get("condition", "")) for r in rules if r.get("type") == "win"
    ) or "达成所有目标"
    lose_condition = " 或 ".join(
        r.get("desc", r.get("condition", "")) for r in rules if r.get("type") == "lose"
    ) or "无失败条件"

    time_limit = None
    initial = state.get("initial_values") or {}
    for key in ("time_left", "timer", "countdown", "time_limit"):
        if key in initial:
            val = initial[key]
            if isinstance(val, (int, float)) and val > 0:
                time_limit = int(val)
                break

    items_guide: List[ItemGuide] = []
    for trigger in triggers:
        if trigger.get("type") == "item_collect":
            mc_mat = str(trigger.get("mc_material") or "EMERALD")
            items_guide.append(
                ItemGuide(
                    name=str(trigger.get("target") or "物品"),
                    mc_item=mc_mat,
                    appearance=MATERIAL_DESCRIPTIONS.get(mc_mat, mc_mat),
                    how_to_get=str(trigger.get("desc") or "靠近自动拾取"),
                )
            )

    npc_guide: List[NpcGuide] = []
    for trigger in triggers:
        ttype = trigger.get("type")
        if ttype in ("guard_detect", "npc_talk"):
            mc_ent = str(trigger.get("mc_entity_type") or "ZOMBIE")
            behavior = str(trigger.get("desc") or "")
            interaction = "被发现即失败" if ttype == "guard_detect" else "右键点击或输入关键词对话"
            npc_guide.append(
                NpcGuide(
                    name=str(trigger.get("target") or "NPC"),
                    mc_entity=mc_ent,
                    behavior=f"{ENTITY_DESCRIPTIONS.get(mc_ent, mc_ent)} — {behavior}".strip(),
                    interaction=interaction,
                )
            )

    if not npc_guide and npc_hints:
        for hint in npc_hints:
            npc_guide.append(
                NpcGuide(
                    name="NPC",
                    mc_entity="VILLAGER",
                    behavior=str(hint),
                    interaction="右键点击或在聊天框对话",
                )
            )

    trigger_types = {str(t.get("type") or "") for t in triggers}
    hints = [CONTROLS_TEMPLATES[tt] for tt in trigger_types if tt in CONTROLS_TEMPLATES]
    controls_hint = " | ".join(hints) if hints else "探索场景，完成目标"

    return RuleDocument(
        story_intro=story_intro,
        objective=objective,
        win_condition=win_condition,
        lose_condition=lose_condition,
        time_limit=time_limit,
        items_guide=items_guide,
        npc_guide=npc_guide,
        controls_hint=controls_hint,
        difficulty_label="",
    )


_RULE_DOC_SYSTEM_PROMPT = """\
你是 Drift RuleDocument 生成器。
输入：ExperienceSpec JSON + 原始设计文本。
输出：一个 JSON 对象，格式如下（只输出 JSON，禁止解释）：
{
  "story_intro": "string（2-4句话的故事背景，第二人称，让玩家代入角色）",
  "objective": "string（一句话任务目标）",
  "win_condition": "string（胜利条件，简明）",
  "lose_condition": "string（失败条件，简明）",
  "items_guide": [
    {"name": "物品语义名", "mc_item": "MC物品名", "appearance": "外观描述", "how_to_get": "获取方式"}
  ],
  "npc_guide": [
    {"name": "NPC名", "mc_entity": "MC实体名", "behavior": "行为描述", "interaction": "互动方式"}
  ],
  "controls_hint": "string（操作提示，用 | 分隔多条）"
}

规则：
- story_intro 必须贴合原始设计文本的主题和氛围
- objective 必须从 rules 中 type=win 的条目推导
- items_guide 和 npc_guide 必须与 triggers 中的 target/mc_material/mc_entity_type 严格对应
- 禁止超过 500 tokens
"""


def _generate_via_llm(spec: Dict[str, Any], design_text: str) -> Optional[RuleDocument]:
    if not API_KEY:
        return None

    user_content = json.dumps(
        {
            "experience_spec": {
                "rules": spec.get("rules", []),
                "triggers": spec.get("triggers", []),
                "state": spec.get("state", {}),
                "npc_hints": spec.get("npc_hints", []),
                "beats": spec.get("beats", []),
            },
            "design_text": (design_text or "")[:800],
        },
        ensure_ascii=False,
        indent=2,
    )

    try:
        response = requests.post(
            f"{BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": _RULE_DOC_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "max_tokens": 500,
            },
            timeout=(10, 30),
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return None

        items: List[ItemGuide] = []
        for item in parsed.get("items_guide") or []:
            if isinstance(item, dict):
                items.append(
                    ItemGuide(
                        name=str(item.get("name") or ""),
                        mc_item=str(item.get("mc_item") or ""),
                        appearance=str(item.get("appearance") or ""),
                        how_to_get=str(item.get("how_to_get") or ""),
                    )
                )

        npcs: List[NpcGuide] = []
        for npc in parsed.get("npc_guide") or []:
            if isinstance(npc, dict):
                npcs.append(
                    NpcGuide(
                        name=str(npc.get("name") or ""),
                        mc_entity=str(npc.get("mc_entity") or ""),
                        behavior=str(npc.get("behavior") or ""),
                        interaction=str(npc.get("interaction") or ""),
                    )
                )

        return RuleDocument(
            story_intro=str(parsed.get("story_intro") or ""),
            objective=str(parsed.get("objective") or ""),
            win_condition=str(parsed.get("win_condition") or ""),
            lose_condition=str(parsed.get("lose_condition") or ""),
            time_limit=None,
            items_guide=items,
            npc_guide=npcs,
            controls_hint=str(parsed.get("controls_hint") or ""),
        )
    except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError):
        return None


def generate_rule_document(
    spec: Dict[str, Any],
    design_text: str = "",
    *,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """生成 RuleDocument，优先 LLM，失败回退本地。"""
    doc: Optional[RuleDocument] = None

    if use_llm and API_KEY:
        doc = _generate_via_llm(spec, design_text)

    if doc is None:
        doc = _generate_local_fallback(spec, design_text)

    if doc.time_limit is None:
        state = spec.get("state") or {}
        initial = state.get("initial_values") or {}
        for key in ("time_left", "timer", "countdown", "time_limit"):
            if key in initial:
                val = initial[key]
                if isinstance(val, (int, float)) and val > 0:
                    doc.time_limit = int(val)
                    break

    return rule_document_to_dict(doc)


def rule_document_to_dict(doc: RuleDocument) -> Dict[str, Any]:
    return {
        "story_intro": doc.story_intro,
        "objective": doc.objective,
        "win_condition": doc.win_condition,
        "lose_condition": doc.lose_condition,
        "time_limit": doc.time_limit,
        "items_guide": [asdict(item) for item in doc.items_guide],
        "npc_guide": [asdict(npc) for npc in doc.npc_guide],
        "controls_hint": doc.controls_hint,
        "difficulty_label": doc.difficulty_label,
    }


def rule_document_to_mc_tells(doc_dict: Dict[str, Any]) -> List[str]:
    """将 RuleDocument 转为 Minecraft 聊天消息列表。"""
    tells: List[str] = []

    intro = str(doc_dict.get("story_intro") or "")
    if intro:
        tells.append(f"§6§l【故事背景】§r§f {intro}")

    objective = str(doc_dict.get("objective") or "")
    if objective:
        tells.append(f"§e§l【任务目标】§r§e {objective}")

    win = str(doc_dict.get("win_condition") or "")
    if win:
        tells.append(f"§a§l【胜利条件】§r§a {win}")

    lose = str(doc_dict.get("lose_condition") or "")
    if lose:
        tells.append(f"§c§l【失败条件】§r§c {lose}")

    time_limit = doc_dict.get("time_limit")
    if isinstance(time_limit, (int, float)) and time_limit > 0:
        tells.append(f"§b§l【时间限制】§r§b {int(time_limit)} 秒")

    items = doc_dict.get("items_guide") or []
    if isinstance(items, list) and items:
        item_lines: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            appearance = str(item.get("appearance") or "")
            how_to_get = str(item.get("how_to_get") or "")
            item_lines.append(f"  §7· {name}（{appearance}）— {how_to_get}")
        if item_lines:
            tells.append("§d§l【物品图鉴】§r\n" + "\n".join(item_lines))

    npcs = doc_dict.get("npc_guide") or []
    if isinstance(npcs, list) and npcs:
        npc_lines: List[str] = []
        for npc in npcs:
            if not isinstance(npc, dict):
                continue
            name = str(npc.get("name") or "")
            behavior = str(npc.get("behavior") or "")
            interaction = str(npc.get("interaction") or "")
            npc_lines.append(f"  §7· {name} — {behavior}（{interaction}）")
        if npc_lines:
            tells.append("§5§l【NPC 指南】§r\n" + "\n".join(npc_lines))

    controls = str(doc_dict.get("controls_hint") or "")
    if controls:
        tells.append(f"§9§l【操作提示】§r§9 {controls}")

    return tells


def rule_document_to_mc_commands(doc_dict: Dict[str, Any]) -> List[str]:
    """兼容别名：返回与 tells 相同的字符串序列。"""
    return rule_document_to_mc_tells(doc_dict)
