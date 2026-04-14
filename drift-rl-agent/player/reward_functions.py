"""
Drift RL Agent 奖励函数库

所有参数从 configs/reward_params.yaml 加载，支持动态调参。
"""

import os
from typing import Optional

import yaml


def load_reward_params(config_path: Optional[str] = None) -> dict:
    """加载奖励参数配置"""
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "configs", "reward_params.yaml"
        )

    defaults = {
        "alive_per_tick": 0.01,
        "time_penalty_per_tick": -0.001,
        "trigger_completed": 2.0,
        "new_area_discovered": 0.5,
        "npc_interacted": 1.0,
        "level_completed": 10.0,
        "time_bonus_max": 5.0,
        "death_penalty": -5.0,
        "health_change_scale": 0.1,
        "low_health_threshold": 5,
        "low_health_penalty": -0.1,
        "easy_command_penalty": -0.5,
        "max_steps": 6000,
        "position_grid_size": 5,
    }

    try:
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f) or {}
        defaults.update(raw.get("rewards", {}))
        defaults.update(raw.get("environment", {}))
    except FileNotFoundError:
        pass

    return defaults


def compute_reward(
    prev_state: dict,
    curr_state: dict,
    action: dict,
    done: bool,
    info: dict,
    params: dict,
) -> float:
    """
    计算单步奖励

    Args:
        prev_state: 上一步的 Bot 状态
        curr_state: 当前 Bot 状态
        action: 执行的动作
        done: 是否结束
        info: 额外信息（triggers_completed, new_area_discovered, etc.）
        params: 奖励参数

    Returns:
        reward: 浮点数奖励值
    """
    reward = 0.0

    # 1. 基础存活奖励
    reward += params.get("alive_per_tick", 0.01)

    # 2. 时间惩罚（防止无意义徘徊）
    reward += params.get("time_penalty_per_tick", -0.001)

    # 3. 触发器进度奖励
    triggers = info.get("triggers_completed", 0)
    reward += triggers * params.get("trigger_completed", 2.0)

    # 4. 探索奖励
    if info.get("new_area_discovered", False):
        reward += params.get("new_area_discovered", 0.5)

    # 5. NPC 交互奖励
    if info.get("npc_interacted", False):
        reward += params.get("npc_interacted", 1.0)

    # 6. 通关大奖 + 时间奖励
    if info.get("level_completed", False):
        reward += params.get("level_completed", 10.0)
        max_steps = params.get("max_steps", 6000)
        time_ratio = max(0.0, (max_steps - info.get("time", 0) * 20) / max_steps)
        reward += time_ratio * params.get("time_bonus_max", 5.0)

    # 7. 死亡惩罚
    curr_health = curr_state.get("health", 20)
    if curr_health <= 0:
        reward += params.get("death_penalty", -5.0)

    # 8. 血量变化
    prev_health = prev_state.get("health", 20) if prev_state else 20
    health_delta = curr_health - prev_health
    reward += health_delta * params.get("health_change_scale", 0.1)

    # 9. 低血量持续惩罚
    if 0 < curr_health < params.get("low_health_threshold", 5):
        reward += params.get("low_health_penalty", -0.1)

    # 10. /easy 命令惩罚（鼓励挑战高难度）
    if info.get("easy_just_used", False):
        reward += params.get("easy_command_penalty", -0.5)

    return reward
