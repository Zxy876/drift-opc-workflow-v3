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
import re
import time
from typing import Any, Optional

import requests

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
        wall_timeout: float = 300.0,
    ):
        self.client = client
        self.skill_name = skill
        self.profile = get_profile(skill)
        self.level_id = level_id
        self.player_id = player_id
        self.max_steps = max_steps
        self.tick_interval = tick_interval
        self.wall_timeout = wall_timeout

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

        # 关卡目标（从 Drift 后端获取）
        self.level_goals: list[dict] = []
        self.goal_positions: list[tuple[float, float, float]] = []
        self.current_goal_idx: int = 0
        self._drift_url: str = "http://35.201.132.58:8000"

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
        self.level_goals = []
        self.goal_positions = []
        self.current_goal_idx = 0

        # BUG-C: 预检—先确认 Bot 已连接（最多 30 秒）
        for i in range(30):
            state = self.client.get_state()
            if state.get("error") != "bot_not_ready":
                break
            if i == 0:
                print(f"    [{self.skill_name}] 等待 Bot 连接...")
            time.sleep(1)
        else:
            print(f"    [{self.skill_name}] \u26a0 Bot 30s 未就绪，跳过本局")
            return {"error": "bot_not_ready"}

        # Bot 已连上，再发 reset_level
        # 通过 TCP Bridge 重置关卡
        self.client.reset_level(self.level_id)

        # 等待关卡加载（最多 15 秒）
        for i in range(15):
            time.sleep(1)
            state = self.client.get_state()
            if state.get("error") != "bot_not_ready":
                print(f"    [{self.skill_name}] 关卡已加载 (等待 {i + 1}s)")
                # 加载完成后获取关卡目标
                self._fetch_level_goals(self.level_id, self.player_id)
                return state
        print(f"    [{self.skill_name}] 警告: 关卡加载超时 (15s)，继续执行")
        self._fetch_level_goals(self.level_id, self.player_id)
        return self.client.get_state()

    def play_episode(self) -> dict:
        """
        玩一局完整的关卡

        Returns: 单局结果 dict（兼容 eval_bridge.analyze_play_data 格式）
        """
        state = self.reset()
        start_time = time.time()

        # BUG-C: reset 阶段 Bot 就没就绪，返回无效局标记
        if state.get("error") == "bot_not_ready":
            return {
                "completed": False, "time": 0, "deaths": 0,
                "easy_used": False, "death_causes": [],
                "stuck_positions": [], "exploration": 0,
                "triggers_completed": 0, "skill_level": self.skill_name,
                "error": "bot_not_ready",
            }
        not_ready_count = 0
        max_not_ready = 30  # 连续 30 次 bot_not_ready 就放弃本局

        while self.steps < self.max_steps:
            self.steps += 1

            # 检查单局总时间限制
            if time.time() - start_time > self.wall_timeout:
                print(f"    [{self.skill_name}] 单局超时 ({self.wall_timeout:.0f}s)，结束")
                break

            # 检查结束条件
            if state.get("error") == "bot_not_ready":
                not_ready_count += 1
                if not_ready_count >= max_not_ready:
                    print(f"    [{self.skill_name}] Bot 连续 {max_not_ready} 次未就绪，放弃本局")
                    break
                time.sleep(1)
                state = self.client.get_state()
                continue
            else:
                not_ready_count = 0  # 正常状态时重置计数器

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
            # BUG-B: 清洗无效坐标（None/NaN 用 0 代替）
            pos = [p if isinstance(p, (int, float)) else 0 for p in pos]
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
        # BUG-B: 清洗无效坐标（None/NaN 用 0 代替）
        pos = [p if isinstance(p, (int, float)) else 0 for p in pos]

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

        # ── 3. GOAL-DIRECTED: 根据关卡目标行动 ──
        if self.level_goals and random.random() < self.profile.get("goal_awareness", 0.5):
            goal = self.level_goals[self.current_goal_idx % len(self.level_goals)]
            if goal["type"] == "collect":
                target_items = self._find_goal_items(entities, goal["target"])
                if target_items:
                    self._handle_collect(target_items[0], pos)
                    return
                else:
                    # 附近没有目标物品，可能已全部收集，推进到下一个目标
                    self.current_goal_idx += 1
            elif goal["type"] == "reach" and self.goal_positions:
                tx, ty, tz = self.goal_positions[self.current_goal_idx % len(self.goal_positions)]
                self.client.navigate_to(tx, ty, tz)
                return

        # ── 4. INTERACT: NPC 对话 ──
        npcs = [
            e for e in entities
            if e.get("type") == "player"
            or (e.get("name") or "").upper().startswith("NPC")
            or e.get("type") == "villager"
        ]
        if npcs and self.steps % self.profile["npc_interact_delay"] == 0:
            self._handle_npc(npcs[0], pos)
            return

        # ── 5. COLLECT: 收集附近物品 ──
        items = [
            e for e in entities
            if self._is_collectible(e)
            and self._entity_dist(e) < self.profile["collect_item_dist"]
        ]
        if items:
            self._handle_collect(items[0], pos)
            return

        # ── 6. EXPLORE / IDLE ──
        if self.stuck_counter >= self.profile["stuck_patience"]:
            self._handle_stuck(state)
        else:
            self._handle_smart_explore(state)

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
        """随机探索（回退策略）"""
        if random.random() < 0.1:
            self.client.execute_action({
                "move_forward": 1,
                "jump": 1 if random.random() < 0.15 else 0,
                "look_delta": [random.uniform(-0.5, 0.5), 0],
            })
        else:
            self.client.execute_action({
                "move_forward": 1,
                "jump": 1 if random.random() < 0.05 else 0,
            })

    def _handle_smart_explore(self, state: dict):
        """智能探索：优先向未访问区域或目标坐标移动"""
        pos = state.get("position", [0, 0, 0])
        pos = [p if isinstance(p, (int, float)) else 0 for p in pos]

        # 策略1: 如果有目标坐标，导航过去（到达后推进 idx）
        if self.goal_positions and self.profile.get("use_pathfinder", False):
            target = self.goal_positions[self.current_goal_idx % len(self.goal_positions)]
            dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(pos, target)))
            if dist < 3.0:  # 已到达目标，推进到下一个
                self.current_goal_idx += 1
                return
            self.client.navigate_to(target[0], target[1], target[2])
            return

        # 策略2: 向最少访问的方向移动
        best_dir = self._find_least_visited_direction(pos)
        if best_dir:
            tx, ty, tz = best_dir
            if self.profile.get("use_pathfinder", False):
                self.client.navigate_to(tx, ty, tz)
            else:
                self.client.look_at(tx, ty, tz)
                self.client.execute_action({"move_forward": 1, "sprint": 1})
            return

        # 策略3: 回退到随机探索
        self._handle_explore(state)

    def _find_least_visited_direction(self, pos: list) -> tuple | None:
        """找到周围最少访问的方向（8个方向，步长10格）"""
        best_pos = None
        min_visits = float("inf")

        for angle_deg in range(0, 360, 45):
            angle = math.radians(angle_deg)
            tx = pos[0] + 10 * math.cos(angle)
            tz = pos[2] + 10 * math.sin(angle)
            grid = (int(tx) // 5, int(pos[1]) // 5, int(tz) // 5)
            visit_count = 1 if grid in self.visited_positions else 0
            if visit_count < min_visits:
                min_visits = visit_count
                best_pos = (tx, pos[1], tz)

        return best_pos if min_visits == 0 else None

    def _is_collectible(self, entity: dict) -> bool:
        """判断实体是否为可收集物品（统一逻辑）"""
        etype = entity.get("type", "")
        obj_type = entity.get("objectType", "")
        name = entity.get("name", "").lower()

        if etype in ("object", "item", "other") or obj_type == "Item":
            return True

        collectible_keywords = {"item", "drop", "diamond", "emerald", "gold",
                               "pearl", "star", "dust", "shard", "book", "ingot"}
        if any(kw in name for kw in collectible_keywords):
            return True

        return False

    def _find_goal_items(self, entities: list, target_name: str) -> list:
        """在附近实体中查找匹配关卡目标的物品"""
        try:
            from item_mapping import mc_name_matches_goal
            return [
                e for e in entities
                if self._is_collectible(e)
                and mc_name_matches_goal(e.get("name", ""), target_name)
            ]
        except ImportError:
            pass

        # 回退：简单名称匹配
        ITEM_ALIASES = {
            "pearls": ["ender_pearl", "pearl", "prismarine_shard"],
            "pearl": ["ender_pearl", "pearl", "prismarine_shard"],
            "gems": ["diamond", "emerald", "prismarine_shard"],
            "gem": ["diamond", "emerald", "prismarine_shard"],
            "keys": ["tripwire_hook", "gold_ingot"],
            "key": ["tripwire_hook", "gold_ingot"],
            "runes": ["nether_star", "glowstone_dust", "book"],
            "rune": ["nether_star", "glowstone_dust", "book"],
        }
        target_lower = target_name.lower().rstrip("s")
        search_names = set(ITEM_ALIASES.get(target_lower, [target_lower]) +
                           ITEM_ALIASES.get(target_name.lower(), []))
        search_names.add(target_lower)
        return [
            e for e in entities
            if self._is_collectible(e)
            and any(alias in e.get("name", "").lower() for alias in search_names)
        ]

    def _fetch_level_goals(self, level_id: str, player_id: str):
        """从 Drift 后端获取当前关卡的目标列表"""
        try:
            resp = requests.get(
                f"{self._drift_url}/experience/state/{player_id}",
                timeout=5,
            )
            if resp.ok:
                data = resp.json()
                rules = data.get("active_rules", [])
                for rule in rules:
                    goal = self._parse_rule_to_goal(rule)
                    if goal:
                        self.level_goals.append(goal)

                timeline = data.get("timeline", [])
                for event in timeline:
                    s = event.get("state", {})
                    if "npc_position" in s:
                        p = s["npc_position"]
                        self.goal_positions.append(
                            (p.get("x", 0), p.get("y", 0), p.get("z", 0))
                        )
                if self.level_goals:
                    print(f"    [{self.skill_name}] 获取关卡目标: {self.level_goals}")
        except Exception as e:
            print(f"    [{self.skill_name}] 获取关卡目标失败: {e}")

    def _parse_rule_to_goal(self, rule: dict) -> dict | None:
        """将 Drift 规则解析为 Bot 可理解的目标"""
        condition = rule.get("condition", "")
        rule_type = rule.get("type", "")

        if rule_type == "win":
            m = re.match(r"collected_(\w+)\s*>=\s*(\d+)", condition)
            if m:
                return {"type": "collect", "target": m.group(1), "count": int(m.group(2))}
            m = re.match(r"reached_(\w+)\s*==\s*true", condition)
            if m:
                return {"type": "reach", "target": m.group(1)}

        return None

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
