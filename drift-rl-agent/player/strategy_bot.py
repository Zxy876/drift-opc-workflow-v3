"""
StrategyBot — 分层决策引擎

替代 PPO RL Agent，用规则引擎 + 技能参数模拟不同水平的玩家。
通过 BotClient (TCP Bridge) 与 Mineflayer Bot 交互。

决策优先级（状态机）:
  1. DANGER   — 血量低 → 逃跑 / 吃食物 / /easy
  2. COMBAT   — 附近有敌对实体 → 攻击
  3. INTERACT — 附近有 NPC → 对话
  4. COLLECT  — 附近有可收集物品 → 收集
  5. EXPLORE  — 向未探索区域移动
  6. IDLE     — 随机移动（防卡住）
"""

import math
import random
import time
from typing import Any, Optional

from bot_client import BotClient
from skill_profiles import get_profile, DEFAULT_PROFILES


class StrategyBot:
    """分层决策 Bot — 用技能参数模拟不同水平的玩家"""

    def __init__(
        self,
        client: BotClient,
        skill: str = "average",
        level_id: str = "demo_001",
        player_id: str = "DriftRLAgent",
        max_steps: int = 6000,
        tick_interval: float = 0.05,
    ):
        self.client = client
        self.skill_name = skill
        self.profile = get_profile(skill)
        self.level_id = level_id
        self.player_id = player_id
        self.max_steps = max_steps
        self.tick_interval = tick_interval

        # 运行时状态
        self.steps = 0
        self.visited_positions: set[tuple[int, int, int]] = set()
        self.prev_position: Optional[tuple[float, float, float]] = None
        self.stuck_counter = 0
        self.deaths = 0
        self.easy_used = False
        self.death_causes: list[str] = []
        self.stuck_positions: list[list[float]] = []
        self.triggers_completed = 0
        self.level_completed = False
        self.reaction_cooldown = 0  # 反应延迟计数器

    def reset(self):
        """重置一局的状态"""
        self.steps = 0
        self.visited_positions = set()
        self.prev_position = None
        self.stuck_counter = 0
        self.deaths = 0
        self.easy_used = False
        self.death_causes = []
        self.stuck_positions = []
        self.triggers_completed = 0
        self.level_completed = False
        self.reaction_cooldown = 0

        # 通过 TCP Bridge 重置关卡
        self.client.reset_level(self.level_id)

        # 等待关卡加载（最多 10 秒）
        for _ in range(10):
            time.sleep(1)
            state = self.client.get_state()
            if state.get("error") != "bot_not_ready":
                return state
        print(f"[StrategyBot] 警告: 关卡加载超时 (10s)")
        return self.client.get_state()

    def play_episode(self) -> dict:
        """
        玩一局完整的关卡

        Returns: 单局结果 dict（兼容 eval_bridge.analyze_play_data 格式）
        """
        state = self.reset()
        start_time = time.time()

        while self.steps < self.max_steps:
            self.steps += 1

            # 检查结束条件
            if state.get("error") == "bot_not_ready":
                time.sleep(1)
                state = self.client.get_state()
                continue

            health = state.get("health", 20)

            # 检查死亡
            death_cause = state.get("last_death_cause")
            if death_cause is not None or health <= 0:
                self.deaths += 1
                self.death_causes.append(death_cause or "unknown")
                break  # 本局结束

            # 检查通关
            if state.get("level_completed", False):
                self.level_completed = True
                break

            # 记录触发器
            self.triggers_completed = state.get("triggers_completed", 0)

            # 更新探索记录
            pos = state.get("position", [0, 0, 0])
            grid_pos = (int(pos[0]) // 5, int(pos[1]) // 5, int(pos[2]) // 5)
            self.visited_positions.add(grid_pos)

            # 检测卡住
            if self.prev_position:
                dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(pos, self.prev_position)))
                if dist < 0.5:
                    self.stuck_counter += 1
                else:
                    self.stuck_counter = 0
            self.prev_position = tuple(pos)

            # 执行决策
            self._decide_and_act(state)

            # 等待一个 tick
            time.sleep(self.tick_interval)

            # 获取新状态
            state = self.client.get_state()

        # 停止所有动作
        try:
            self.client.stop_all()
        except Exception:
            pass

        elapsed = time.time() - start_time

        return {
            "completed": self.level_completed,
            "time": elapsed,
            "deaths": self.deaths,
            "easy_used": self.easy_used,
            "death_causes": self.death_causes,
            "stuck_positions": self.stuck_positions,
            "exploration": len(self.visited_positions),
            "triggers_completed": self.triggers_completed,
            "skill_level": self.skill_name,
        }

    def _decide_and_act(self, state: dict):
        """
        分层决策 — 按优先级选择动作

        优先级: DANGER > COMBAT > INTERACT > COLLECT > EXPLORE > IDLE
        """
        # 反应延迟（模拟不同水平玩家的反应速度）
        if self.reaction_cooldown > 0:
            self.reaction_cooldown -= 1
            return

        health = state.get("health", 20)
        entities = state.get("nearby_entities", [])
        pos = state.get("position", [0, 0, 0])

        # ── 1. DANGER: 低血量处理 ──
        if health < self.profile["flee_health_threshold"]:
            self._handle_danger(state)
            return

        # ── 2. COMBAT: 攻击附近敌对实体 ──
        hostiles = [
            e for e in entities
            if e.get("type") in ("mob", "hostile")
            and self._entity_dist(e) < self.profile["combat_engage_dist"]
        ]
        if hostiles:
            self.reaction_cooldown = self.profile["reaction_ticks"]
            self._handle_combat(hostiles[0], pos)
            return

        # ── 3. INTERACT: NPC 对话 ──
        npcs = [
            e for e in entities
            if e.get("type") == "player"
            or (e.get("name") or "").upper().startswith("NPC")
            or e.get("type") == "villager"
        ]
        if npcs and self.steps % self.profile["npc_interact_delay"] == 0:
            self._handle_npc(npcs[0], pos)
            return

        # ── 4. COLLECT: 收集附近物品 ──
        items = [
            e for e in entities
            if e.get("type") == "object" or e.get("type") == "item"
            and self._entity_dist(e) < self.profile["collect_item_dist"]
        ]
        if items:
            self._handle_collect(items[0], pos)
            return

        # ── 5. EXPLORE / IDLE ──
        if self.stuck_counter >= self.profile["stuck_patience"]:
            self._handle_stuck(state)
        else:
            self._handle_explore(state)

    def _handle_danger(self, state: dict):
        """低血量：逃跑 / 吃食物 / 使用 /easy"""
        # 概率使用 /easy
        if not self.easy_used and random.random() < self.profile["use_easy_probability"]:
            self.client.chat("/easy")
            self.easy_used = True
            print(f"    [{self.skill_name}] 使用 /easy（血量低）")
            return

        # 逃跑：反方向跑
        entities = state.get("nearby_entities", [])
        hostiles = [e for e in entities if e.get("type") in ("mob", "hostile")]
        if hostiles:
            # 远离最近的敌人
            h = hostiles[0]
            self.client.execute_action({
                "move_forward": 2 if h.get("rel_z", 0) > 0 else 1,  # 反方向
                "move_strafe": 2 if h.get("rel_x", 0) > 0 else 1,
                "jump": 1,
                "sprint": 1,
                "attack": 0,
            })
        else:
            # 随机移动 + 跳跃（尝试脱离）
            self.client.execute_action({
                "move_forward": 1,
                "jump": 1,
                "sprint": 1,
                "attack": 0,
            })

    def _handle_combat(self, target: dict, pos: list):
        """攻击目标实体"""
        # 看向目标
        tx = pos[0] + target.get("rel_x", 0)
        ty = pos[1] + target.get("rel_y", 0) + 1.5  # 头部高度
        tz = pos[2] + target.get("rel_z", 0)
        self.client.look_at(tx, ty, tz)

        # 靠近 + 攻击
        dist = self._entity_dist(target)
        self.client.execute_action({
            "move_forward": 1 if dist > 2 else 0,
            "jump": 0,
            "sprint": 1 if dist > 3 else 0,
            "attack": 1,
        })

    def _handle_npc(self, npc: dict, pos: list):
        """与 NPC 交互"""
        dist = self._entity_dist(npc)
        if dist > 3:
            # 走向 NPC
            tx = pos[0] + npc.get("rel_x", 0)
            ty = pos[1] + npc.get("rel_y", 0)
            tz = pos[2] + npc.get("rel_z", 0)
            if self.profile["use_pathfinder"]:
                self.client.navigate_to(tx, ty, tz)
            else:
                self.client.execute_action({"move_forward": 1})
        else:
            # 对话
            self.client.chat("/talk 你好，这里有什么任务吗？")

    def _handle_collect(self, item: dict, pos: list):
        """收集附近物品"""
        tx = pos[0] + item.get("rel_x", 0)
        ty = pos[1] + item.get("rel_y", 0)
        tz = pos[2] + item.get("rel_z", 0)
        if self.profile["use_pathfinder"]:
            self.client.navigate_to(tx, ty, tz)
        else:
            # 看向物品方向并走过去
            self.client.look_at(tx, ty, tz)
            self.client.execute_action({"move_forward": 1})

    def _handle_explore(self, state: dict):
        """探索新区域"""
        # 简单探索策略：沿当前朝向前进，偶尔转向
        if random.random() < 0.1:  # 10% 概率随机转向
            self.client.execute_action({
                "move_forward": 1,
                "jump": 1 if random.random() < 0.15 else 0,  # 偶尔跳跃
                "look_delta": [random.uniform(-0.5, 0.5), 0],
            })
        else:
            self.client.execute_action({
                "move_forward": 1,
                "jump": 1 if random.random() < 0.05 else 0,
            })

    def _handle_stuck(self, state: dict):
        """处理卡住状态"""
        pos = state.get("position", [0, 0, 0])
        self.stuck_positions.append(pos)
        self.stuck_counter = 0

        # 概率使用 /easy
        if not self.easy_used and random.random() < self.profile["use_easy_probability"]:
            self.client.chat("/easy")
            self.easy_used = True
            print(f"    [{self.skill_name}] 使用 /easy（卡住）")
            return

        # 大幅转向 + 跳跃 + 后退
        self.client.execute_action({
            "move_forward": 2,  # 后退
            "jump": 1,
            "look_delta": [random.uniform(-2.0, 2.0), 0],
        })
        time.sleep(0.3)

        # 然后向新方向前进
        self.client.execute_action({
            "move_forward": 1,
            "jump": 1,
            "sprint": 1,
            "look_delta": [random.uniform(-1.5, 1.5), 0],
        })

    @staticmethod
    def _entity_dist(entity: dict) -> float:
        """计算实体到 Bot 的距离"""
        rx = entity.get("rel_x", 0)
        ry = entity.get("rel_y", 0)
        rz = entity.get("rel_z", 0)
        return math.sqrt(rx * rx + ry * ry + rz * rz)
