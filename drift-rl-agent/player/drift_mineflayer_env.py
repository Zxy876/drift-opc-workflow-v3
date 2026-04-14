"""
Drift Mineflayer Gymnasium 环境

通过 TCP 连接 player_bot.js（Mineflayer Bot），将 MC 交互封装为标准 Gymnasium 接口。
Tianshou 直接使用这个环境进行 RL 训练。
"""

import json
import socket
import time
from typing import Any, Optional

import numpy as np
import yaml
import gymnasium as gym
from gymnasium import spaces

from reward_functions import compute_reward, load_reward_params


class DriftMineflayerEnv(gym.Env):
    """
    Drift MC 关卡的 Gymnasium 环境。

    观测空间：64 维浮点向量（位置、血量、附近实体、探索度等）
    动作空间：MultiDiscrete([3, 3, 2, 2, 2, 7])
              [前进/后退, 左/右, 跳, 攻击, 使用物品, 命令类型]
    """

    metadata = {"render_modes": ["human"]}

    # MC 命令映射
    COMMANDS = {
        0: None,                              # 不发命令
        1: "/easy",                           # 降低难度
        2: "/replay",                         # 重玩本关
        3: "/advance",                        # 推进下一关
        4: "/talk 你好，这里有什么任务吗？",    # NPC 对话
        5: "/levels",                         # 查看关卡列表
        6: "/create 简单测试关卡",             # 创建关卡（高级动作）
    }

    def __init__(
        self,
        bot_host: str = "localhost",
        bot_port: int = 9999,
        drift_url: str = "http://35.201.132.58:8000",
        level_id: str = "demo_rl_001",
        player_id: str = "DriftRLAgent",
        max_steps: int = 6000,
        config_path: Optional[str] = None,
    ):
        super().__init__()

        self.bot_host = bot_host
        self.bot_port = bot_port
        self.drift_url = drift_url
        self.level_id = level_id
        self.player_id = player_id
        self.max_steps = max_steps

        # 加载奖励参数
        self.reward_params = load_reward_params(config_path)

        # 观测空间：64 维向量
        self.observation_space = spaces.Box(
            low=-1e4, high=1e4, shape=(64,), dtype=np.float32
        )

        # 动作空间
        self.action_space = spaces.MultiDiscrete([3, 3, 2, 2, 2, 7])

        # 状态跟踪
        self.sock: Optional[socket.socket] = None
        self.steps = 0
        self.prev_state: Optional[dict] = None
        self.visited_positions: set = set()
        self.prev_triggers = 0
        self.episode_deaths = 0
        self.episode_easy_used = False
        self.episode_death_causes: list = []
        self.episode_stuck_positions: list = []

    def _connect(self):
        """建立到 Mineflayer Bot 的 TCP 连接"""
        if self.sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(10)
            self.sock.connect((self.bot_host, self.bot_port))

    def _disconnect(self):
        """断开连接"""
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _send(self, data: dict) -> dict:
        """发送命令到 Bot 并接收响应"""
        self._connect()
        msg = json.dumps(data) + "\n"
        self.sock.sendall(msg.encode())

        response = b""
        while not response.endswith(b"\n"):
            chunk = self.sock.recv(8192)
            if not chunk:
                raise ConnectionError("Bot 连接断开")
            response += chunk

        return json.loads(response.decode().strip())

    def _wait_for_bot(self, timeout: float = 10.0):
        """等待 Bot 就绪"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = self._send({"type": "ping"})
                if resp.get("ready"):
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        raise TimeoutError("Bot 未就绪")

    def _state_to_obs(self, state: dict) -> np.ndarray:
        """将 Mineflayer 状态转换为 64 维观测向量"""
        obs = np.zeros(64, dtype=np.float32)

        # 位置 (3)
        pos = state.get("position", [0, 0, 0])
        obs[0:3] = pos

        # 健康/食物 (2)
        obs[3] = state.get("health", 20)
        obs[4] = state.get("food", 20)

        # 视角 (2)
        obs[5] = state.get("yaw", 0)
        obs[6] = state.get("pitch", 0)

        # 附近实体 (10 * 4 = 40)
        entities = state.get("nearby_entities", [])
        for i, e in enumerate(entities[:10]):
            base = 7 + i * 4
            obs[base] = e.get("rel_x", 0)
            obs[base + 1] = e.get("rel_y", 0)
            obs[base + 2] = e.get("rel_z", 0)
            obs[base + 3] = e.get("health", 0)

        # 背包物品数量 (1)
        obs[47] = len(state.get("inventory", []))

        # 时间进度 (1)
        obs[48] = self.steps / self.max_steps

        # 探索度 (1)
        obs[49] = min(len(self.visited_positions) / 100.0, 1.0)

        # 速度 (3)
        vel = state.get("velocity", [0, 0, 0])
        obs[50:53] = vel

        # 在地面上 (1)
        obs[53] = 1.0 if state.get("on_ground", True) else 0.0

        # 触发器完成数 (1)
        obs[54] = state.get("triggers_completed", 0)

        # 关卡完成标志 (1)
        obs[55] = 1.0 if state.get("level_completed", False) else 0.0

        # 附近方块数量 (1)
        obs[56] = min(len(state.get("nearby_blocks", [])), 50) / 50.0

        # 预留 (57-63)

        return obs

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.steps = 0
        self.prev_triggers = 0
        self.visited_positions = set()
        self.episode_deaths = 0
        self.episode_easy_used = False
        self.episode_death_causes = []
        self.episode_stuck_positions = []

        # 等待 Bot 就绪
        self._wait_for_bot()

        # 重置关卡
        self._send({"type": "reset", "level_id": self.level_id})
        time.sleep(3)  # 等待关卡加载

        # 获取初始状态
        state = self._send({"type": "get_state"})
        self.prev_state = state
        obs = self._state_to_obs(state)

        return obs, {}

    def step(self, action):
        self.steps += 1

        # 解码动作
        move_fwd, move_strafe, jump, attack, use_item, cmd_type = action

        # 执行低层动作
        action_dict = {
            "move_forward": int(move_fwd),
            "move_strafe": int(move_strafe),
            "jump": int(jump),
            "attack": int(attack),
            "use_item": int(use_item),
        }
        self._send({"type": "action", "action": action_dict})

        # 执行高层命令（如果有）
        cmd = self.COMMANDS.get(int(cmd_type))
        if cmd:
            self._send({"type": "command", "text": cmd})
            if cmd == "/easy":
                self.episode_easy_used = True

        # 短暂等待（让 MC 服务器处理）
        time.sleep(0.05)  # 50ms ≈ 1 tick

        # 获取新状态
        state = self._send({"type": "get_state"})

        # 记录访问位置（用于探索奖励）
        pos = state.get("position", [0, 0, 0])
        grid_size = self.reward_params.get("position_grid_size", 5)
        grid_pos = tuple(int(x) // grid_size for x in pos)
        new_area = grid_pos not in self.visited_positions
        self.visited_positions.add(grid_pos)

        # 触发器进度
        current_triggers = state.get("triggers_completed", 0)
        triggers_delta = current_triggers - self.prev_triggers
        self.prev_triggers = current_triggers

        # NPC 交互检测
        npc_interacted = int(cmd_type) == 4  # /talk 命令

        # 死亡检测
        died = state.get("health", 20) <= 0 or state.get("last_death_cause") is not None
        if died:
            self.episode_deaths += 1
            cause = state.get("last_death_cause", "unknown")
            self.episode_death_causes.append(cause)

        # 通关检测
        level_completed = state.get("level_completed", False)

        # 卡点检测（连续 100 步在同一网格）
        if self.steps > 100 and self.steps % 100 == 0:
            if len(self.visited_positions) < self.steps // 200:
                self.episode_stuck_positions.append(pos)

        # 计算奖励
        info = {
            "triggers_completed": triggers_delta,
            "prev_triggers_completed": 0,
            "new_area_discovered": new_area,
            "npc_interacted": npc_interacted,
            "level_completed": level_completed,
        }
        reward = compute_reward(
            self.prev_state or {}, state, action_dict, died or level_completed, info,
            self.reward_params
        )

        # 结束条件
        terminated = died or level_completed
        truncated = self.steps >= self.max_steps

        # 补充 info
        info.update({
            "completed": level_completed,
            "time": self.steps / 20.0,
            "deaths": self.episode_deaths,
            "easy_used": self.episode_easy_used,
            "death_causes": self.episode_death_causes,
            "stuck_positions": self.episode_stuck_positions,
            "exploration": len(self.visited_positions),
        })

        self.prev_state = state
        obs = self._state_to_obs(state)
        return obs, reward, terminated, truncated, info

    def close(self):
        self._send({"type": "stop_all"})
        self._disconnect()
