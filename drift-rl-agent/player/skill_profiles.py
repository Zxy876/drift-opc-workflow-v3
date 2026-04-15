"""
技能档案 — 定义不同水平的虚拟玩家参数

StrategyBot 通过技能档案模拟 beginner / average / expert 玩家行为，
使 DesignerAgent 能获得多维度的难度评估数据。
"""

import os
from typing import Any

import yaml


# 默认技能档案（硬编码兜底）
DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "beginner": {
        "reaction_ticks": 10,        # 看到威胁后多少 tick 才开始反应
        "exploration_radius": 15,    # 主动探索的最大半径（格）
        "combat_engage_dist": 3.0,   # 多近才会主动攻击敌对实体
        "flee_health_threshold": 6,  # 血量低于此值时逃跑
        "stuck_patience": 60,        # 连续多少 tick 没移动才判定卡住
        "use_easy_probability": 0.3, # 卡住/死亡后使用 /easy 的概率
        "use_pathfinder": False,     # 是否使用 pathfinder（新手不会）
        "npc_interact_delay": 15,    # 看到 NPC 后多少 tick 才交互
        "collect_item_dist": 3.0,    # 多近才会去捡物品
    },
    "average": {
        "reaction_ticks": 5,
        "exploration_radius": 30,
        "combat_engage_dist": 4.0,
        "flee_health_threshold": 4,
        "stuck_patience": 40,
        "use_easy_probability": 0.1,
        "use_pathfinder": True,
        "npc_interact_delay": 5,
        "collect_item_dist": 5.0,
    },
    "expert": {
        "reaction_ticks": 2,
        "exploration_radius": 50,
        "combat_engage_dist": 5.0,
        "flee_health_threshold": 2,
        "stuck_patience": 20,
        "use_easy_probability": 0.0,
        "use_pathfinder": True,
        "npc_interact_delay": 2,
        "collect_item_dist": 8.0,
    },
}

# 每代评估中各技能级别的局数分配
EPISODES_PER_SKILL: dict[str, int] = {
    "beginner": 7,
    "average": 7,
    "expert": 6,
}


def load_episodes_per_skill(config_path: str | None = None) -> dict[str, int]:
    """
    从 YAML 加载每代各技能级别的局数分配，加载失败则返回默认值。
    """
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "configs", "skill_profiles.yaml"
        )

    try:
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f) or {}
        eps = raw.get("episodes_per_skill", {})
        if eps and all(isinstance(v, int) and v > 0 for v in eps.values()):
            return eps
    except FileNotFoundError:
        pass

    return dict(EPISODES_PER_SKILL)


def load_skill_profiles(config_path: str | None = None) -> dict[str, dict[str, Any]]:
    """
    从 YAML 配置文件加载技能档案，加载失败则返回默认值。

    配置文件路径默认: configs/skill_profiles.yaml
    """
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "configs", "skill_profiles.yaml"
        )

    try:
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f) or {}
        profiles = raw.get("profiles", {})
        # 用默认值填充缺失字段
        result = {}
        for name in DEFAULT_PROFILES:
            base = dict(DEFAULT_PROFILES[name])
            if name in profiles:
                base.update(profiles[name])
            result[name] = base
        return result
    except FileNotFoundError:
        return dict(DEFAULT_PROFILES)


def get_profile(name: str, config_path: str | None = None) -> dict[str, Any]:
    """获取指定技能档案"""
    profiles = load_skill_profiles(config_path)
    if name not in profiles:
        raise ValueError(f"未知技能档案: {name}，可用: {list(profiles.keys())}")
    return profiles[name]
