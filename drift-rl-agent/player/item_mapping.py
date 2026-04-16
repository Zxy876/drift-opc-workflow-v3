"""
物品映射表 — 设计文本 ↔ MC 实体名 ↔ 内部状态键

确定性映射，不依赖 LLM。
"""
import re

# 设计文本中的中文物品名 → MC 实体/物品 ID 列表
DESIGN_TO_MC: dict[str, list[str]] = {
    "珍珠": ["ender_pearl", "prismarine_shard"],
    "末影珍珠": ["ender_pearl"],
    "宝石": ["emerald", "diamond"],
    "绿宝石": ["emerald"],
    "钻石": ["diamond"],
    "金锭": ["gold_ingot"],
    "铁锭": ["iron_ingot"],
    "钥匙": ["tripwire_hook", "gold_ingot"],
    "符文": ["nether_star", "glowstone_dust", "book"],
    "下界之星": ["nether_star"],
    "荧石粉": ["glowstone_dust"],
    "海晶碎片": ["prismarine_shard"],
    "书": ["book"],
    "书本": ["book"],
    "弓箭": ["bow", "arrow"],
    "弓": ["bow"],
    "盔甲": ["iron_chestplate", "diamond_chestplate", "leather_chestplate"],
    "生命药水": ["potion"],
    "木头": ["oak_log", "spruce_log", "birch_log", "jungle_log"],
    "原木": ["oak_log", "spruce_log", "birch_log", "jungle_log"],
    "火把": ["torch"],
}

# Drift experience state 中的状态键 → MC 实体名
STATE_KEY_TO_MC: dict[str, list[str]] = {
    "collected_pearls": ["ender_pearl", "prismarine_shard", "emerald"],
    "collected_gems": ["emerald", "diamond"],
    "collected_keys": ["tripwire_hook", "gold_ingot"],
    "collected_runes": ["nether_star", "glowstone_dust"],
    "collected_emerald": ["emerald"],
    "collected_diamond": ["diamond"],
    "collected_pearl": ["ender_pearl", "prismarine_shard"],
}


def get_mc_item_names(design_name: str) -> list[str]:
    """从设计文本中的物品名获取所有可能的 MC 物品 ID"""
    design_lower = design_name.lower().strip()

    # 直接匹配中文
    if design_name.strip() in DESIGN_TO_MC:
        return DESIGN_TO_MC[design_name.strip()]

    # 直接匹配英文（小写）
    if design_lower in DESIGN_TO_MC:
        return DESIGN_TO_MC[design_lower]

    # 去掉"收集N个"前缀
    m = re.match(r"收集\d+个(.+)", design_name.strip())
    if m:
        item = m.group(1).strip()
        if item in DESIGN_TO_MC:
            return DESIGN_TO_MC[item]

    # 从状态键查找
    for key, mc_names in STATE_KEY_TO_MC.items():
        stem = key.replace("collected_", "")
        if design_lower == stem or design_lower == stem + "s":
            return mc_names

    # 回退：返回原始名
    return [design_lower]


def mc_name_matches_goal(mc_entity_name: str, goal_target: str) -> bool:
    """判断 MC 实体名是否匹配关卡目标"""
    mc_lower = mc_entity_name.lower()
    goal_lower = goal_target.lower()

    # 直接包含匹配
    if goal_lower in mc_lower or mc_lower in goal_lower:
        return True

    # 通过映射表匹配
    mc_names = get_mc_item_names(goal_target)
    if any(name in mc_lower or mc_lower in name for name in mc_names):
        return True

    # 通过状态键匹配
    state_key = f"collected_{goal_lower}"
    if state_key in STATE_KEY_TO_MC:
        return any(name in mc_lower for name in STATE_KEY_TO_MC[state_key])

    return False
