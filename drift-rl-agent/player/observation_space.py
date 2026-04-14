"""
Drift RL Agent 观测空间定义

提案设计的观测空间规格。
当前实现使用 Box(64,) 的扁平化版本（兼容 Tianshou PPO）。
"""

import numpy as np
from gymnasium import spaces

# ─── 提案版：结构化观测空间（用于文档参考）──────────────
OBSERVATION_SPACE_STRUCTURED = spaces.Dict({
    # 玩家基础状态
    "position": spaces.Box(low=-1e4, high=1e4, shape=(3,), dtype=np.float32),
    "health": spaces.Box(low=0, high=20, shape=(1,), dtype=np.float32),
    "food": spaces.Box(low=0, high=20, shape=(1,), dtype=np.float32),
    "on_ground": spaces.Discrete(2),

    # 周围环境
    "nearby_blocks": spaces.Box(low=0, high=255, shape=(15, 15, 15), dtype=np.uint8),
    "nearby_entities": spaces.Box(low=-100, high=100, shape=(10, 5), dtype=np.float32),
    "inventory": spaces.Box(low=0, high=255, shape=(36, 2), dtype=np.int32),

    # Drift 特有
    "current_difficulty": spaces.Discrete(6),
    "triggers_remaining": spaces.Discrete(20),
    "npc_nearby": spaces.Discrete(2),
    "quest_progress": spaces.Box(low=0, high=1, shape=(1,), dtype=np.float32),
    "time_elapsed": spaces.Box(low=0, high=6000, shape=(1,), dtype=np.float32),
})


# ─── 实用版：64 维扁平向量 ──────────────────────────────
# 与 DriftMineflayerEnv._state_to_obs() 对应
OBSERVATION_DIM = 64

OBSERVATION_LAYOUT = {
    "position":             (0, 3),    # x, y, z
    "health":               (3, 4),    # 0-20
    "food":                 (4, 5),    # 0-20
    "yaw":                  (5, 6),    # radians
    "pitch":                (6, 7),    # radians
    "nearby_entities":      (7, 47),   # 10 entities × 4 features (rel_x, rel_y, rel_z, health)
    "inventory_count":      (47, 48),  # 背包物品数量
    "time_progress":        (48, 49),  # steps / max_steps
    "exploration":          (49, 50),  # visited_positions / 100
    "velocity":             (50, 53),  # vx, vy, vz
    "on_ground":            (53, 54),  # 0 or 1
    "triggers_completed":   (54, 55),  # count
    "level_completed":      (55, 56),  # 0 or 1
    "nearby_blocks_ratio":  (56, 57),  # count / 50
    "current_difficulty":   (57, 58),  # D / 5.0
    "triggers_remaining":   (58, 59),  # count / 20.0
    "npc_nearby":           (59, 60),  # 0 or 1
    "quest_progress":       (60, 61),  # 0-1
    "time_pressure":        (61, 62),  # remaining_time / time_limit
    "reserved":             (62, 64),  # 预留扩展
}


def get_observation_space() -> spaces.Box:
    """返回实用版观测空间"""
    return spaces.Box(
        low=-1e4, high=1e4,
        shape=(OBSERVATION_DIM,),
        dtype=np.float32,
    )
