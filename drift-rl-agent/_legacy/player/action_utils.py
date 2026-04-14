"""
动作空间编解码工具 — 统一 Flat ↔ Multi 转换逻辑
"""

import numpy as np

# 动作空间维度
ACTION_NVEC = [3, 3, 2, 2, 2, 7]
ACTION_FLAT_N = int(np.prod(ACTION_NVEC))  # 504


def flat_to_multi(flat_action: int) -> np.ndarray:
    """Discrete(504) → MultiDiscrete([3,3,2,2,2,7])"""
    result = []
    remaining = int(flat_action)
    for n in reversed(ACTION_NVEC):
        result.append(remaining % n)
        remaining //= n
    return np.array(list(reversed(result)), dtype=np.int64)


def multi_to_flat(multi_action) -> int:
    """MultiDiscrete([3,3,2,2,2,7]) → Discrete(504)"""
    flat = 0
    for i, a in enumerate(multi_action):
        multiplier = int(np.prod(ACTION_NVEC[i + 1:])) if i < len(ACTION_NVEC) - 1 else 1
        flat += int(a) * multiplier
    return flat
