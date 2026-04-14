"""
MetaAgent — 双环自进化控制器

外环（慢）: DesignerAgent 每轮改进一次关卡设计
内环（快）: PlayerAgent 每轮玩 N 局评估

进化停止条件：通关率连续 3 代在 [60%, 80%]（Flow Zone）
"""

import os
import sys
import time
from typing import Optional

import numpy as np
import torch
import yaml

# 添加 parent 目录到 path（方便 import）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "player"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "designer"))

from drift_mineflayer_env import DriftMineflayerEnv
from designer_agent import DesignerAgent
from eval_bridge import analyze_play_data, format_eval_for_llm
from evolution_log import EvolutionLog


class MetaAgent:
    """双环自进化控制器"""

    def __init__(
        self,
        designer: DesignerAgent,
        bot_host: str = "localhost",
        bot_port: int = 9999,
        drift_url: str = "http://35.201.132.58:8000",
        config_path: Optional[str] = None,
        model_path: Optional[str] = None,  # 新增：训练好的模型路径
    ):
        self.designer = designer
        self.bot_host = bot_host
        self.bot_port = bot_port
        self.drift_url = drift_url
        self.logger = EvolutionLog()
        self.model_path = model_path
        self._policy = None

        # 加载配置
        self._load_config(config_path)

        if model_path and os.path.exists(model_path):
            self._load_policy(model_path)

    def _load_config(self, config_path: Optional[str]):
        """加载进化参数"""
        self.episodes_per_eval = 20
        self.max_generations = 10
        self.flow_zone_min = 0.6
        self.flow_zone_max = 0.8
        self.flow_zone_streak_target = 3

        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__), "..", "configs", "evolution_params.yaml"
            )

        try:
            with open(config_path, "r") as f:
                raw = yaml.safe_load(f) or {}
            evo_cfg = raw.get("evolution", {})
            self.episodes_per_eval = evo_cfg.get("episodes_per_eval", 20)
            self.max_generations = evo_cfg.get("max_generations", 10)
            self.flow_zone_min = evo_cfg.get("flow_zone_min", 0.6)
            self.flow_zone_max = evo_cfg.get("flow_zone_max", 0.8)
            self.flow_zone_streak_target = evo_cfg.get("flow_zone_streak_target", 3)
        except FileNotFoundError:
            pass

    def _load_policy(self, model_path: str):
        """加载训练好的 PPO 策略"""
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "player"))
            from tianshou.utils.net.common import Net
            from tianshou.utils.net.discrete import Actor

            device = torch.device("cpu")
            net = Net(state_shape=(64,), hidden_sizes=[256, 256], device=device)
            actor = Actor(net, action_shape=504, device=device).to(device)
            state_dict = torch.load(model_path, map_location=device)
            actor.load_state_dict(state_dict, strict=False)
            actor.eval()
            self._policy = actor
            print(f"[Meta] 已加载训练模型: {model_path}")
        except Exception as e:
            print(f"[Meta] 加载模型失败，使用随机策略: {e}")
            self._policy = None

    def _decode_action(self, flat_action: int) -> np.ndarray:
        """Discrete(504) → MultiDiscrete([3,3,2,2,2,7])"""
        nvec = [3, 3, 2, 2, 2, 7]
        result = []
        remaining = flat_action
        for n in reversed(nvec):
            result.append(remaining % n)
            remaining //= n
        return np.array(list(reversed(result)), dtype=np.int64)

    def run_evolution(
        self,
        initial_design: str,
        level_id: str,
        player_id: str = "DriftRLAgent",
        target_difficulty: int = 3,
        use_premium: bool = False,
    ) -> dict:
        """
        运行完整进化循环

        Returns: 进化日志摘要
        """
        current_design = initial_design
        current_level_id = level_id
        flow_zone_streak = 0

        print(f"\n{'#' * 70}")
        print(f"# Drift RL Agent — 双环自进化系统")
        print(f"# 初始关卡: {level_id}")
        print(f"# 目标难度: D{target_difficulty}")
        print(f"# 每代评估: {self.episodes_per_eval} 局")
        print(f"# 最大代数: {self.max_generations}")
        print(f"{'#' * 70}\n")

        for gen in range(self.max_generations):
            print(f"\n{'=' * 60}")
            print(f" Generation {gen}")
            print(f"{'=' * 60}")

            # ─── 内环：PlayerAgent 游玩 N 局 ───
            print(f"\n[Player] 开始游玩 {self.episodes_per_eval} 局...")
            play_results = self._run_player_episodes(current_level_id, player_id)

            # ─── 评估 ───
            eval_report = analyze_play_data(play_results)
            cr = eval_report["completion_rate"]
            print(f"\n[Eval] 通关率: {cr:.0%}")
            print(f"[Eval] 平均时间: {eval_report['avg_time']:.0f}s")
            print(f"[Eval] 平均死亡: {eval_report['avg_deaths']:.1f}")
            print(f"[Eval] /easy 使用率: {eval_report['easy_usage_rate']:.0%}")
            print(f"[Eval] 评估报告:\n{format_eval_for_llm(eval_report)}")

            # 检查 Flow Zone
            if self.flow_zone_min <= cr <= self.flow_zone_max:
                flow_zone_streak += 1
                print(f"\n[Meta] ✓ Flow Zone! (连续 {flow_zone_streak}/{self.flow_zone_streak_target})")
                if flow_zone_streak >= self.flow_zone_streak_target:
                    print(f"\n[Meta] 达到稳定 Flow Zone，进化完成！")
                    self.logger.log_generation(gen, current_design, eval_report)
                    break
            else:
                flow_zone_streak = 0
                direction = "偏低" if cr < self.flow_zone_min else "偏高"
                print(f"\n[Meta] ✗ 未在 Flow Zone (通关率{direction})")

            # ─── 外环：DesignerAgent 改进关卡 ───
            print(f"\n[Designer] 正在用 LLM 改进关卡设计...")
            new_design = self.designer.generate_improved_design(
                current_design, eval_report, target_difficulty
            )
            print(f"[Designer] 改进理由: {new_design.get('reasoning', '—')}")
            print(f"[Designer] 改动: {new_design.get('changes', [])}")

            # 发布到 Drift
            new_level_id = f"{level_id.split('_gen')[0]}_gen{gen + 1}"
            print(f"\n[Designer] 发布新关卡: {new_level_id}")
            publish_result = self.designer.publish_to_drift(
                new_design, new_level_id, player_id,
                use_premium=use_premium,
            )
            print(f"[Designer] 发布结果: {publish_result.get('method', '?')}")

            # 记录进化日志
            self.logger.log_generation(
                gen, current_design, eval_report, new_design, publish_result
            )

            # 更新状态
            current_design = new_design["design_text"]
            current_level_id = new_level_id

        # 导出完整日志
        log_path = self.logger.export_json()
        summary = self.logger.get_summary()
        print(f"\n{'=' * 60}")
        print(f" 进化完成!")
        print(f" 总代数: {summary['total_generations']}")
        print(f" 最终通关率: {summary.get('final_completion_rate', 0):.0%}")
        print(f" 是否在 Flow Zone: {summary.get('in_flow_zone', False)}")
        print(f" 日志: {log_path}")
        print(f"{'=' * 60}")

        return summary

    def _run_player_episodes(self, level_id: str, player_id: str) -> list:
        """
        运行 N 局 PlayerAgent 游玩

        简化版：直接用 DriftMineflayerEnv（随机策略）
        完整版：加载训练好的 PPO 模型后替换 action = policy(obs)
        """
        env = DriftMineflayerEnv(
            bot_host=self.bot_host,
            bot_port=self.bot_port,
            drift_url=self.drift_url,
            level_id=level_id,
            player_id=player_id,
        )

        results = []
        for ep in range(self.episodes_per_eval):
            obs, _ = env.reset()
            episode_reward = 0.0
            done = False

            while not done:
                if self._policy is not None:
                    # 使用训练好的策略
                    obs_tensor = torch.FloatTensor(obs).unsqueeze(0)
                    with torch.no_grad():
                        logits, _ = self._policy(obs_tensor)
                        flat_action = logits.argmax(dim=1).item()
                    action = self._decode_action(flat_action)
                else:
                    # 随机策略
                    action = env.action_space.sample()
                obs, reward, terminated, truncated, info = env.step(action)
                episode_reward += reward
                done = terminated or truncated

            result = {
                "episode": ep,
                "completed": info.get("completed", False),
                "time": info.get("time", 0),
                "deaths": info.get("deaths", 0),
                "easy_used": info.get("easy_used", False),
                "death_causes": info.get("death_causes", []),
                "stuck_positions": info.get("stuck_positions", []),
                "exploration": info.get("exploration", 0),
                "total_reward": episode_reward,
            }
            results.append(result)

            status = "PASS" if result["completed"] else "FAIL"
            print(f"  Episode {ep + 1}/{self.episodes_per_eval}: {status} "
                  f"({result['time']:.0f}s, {result['deaths']} deaths, "
                  f"reward={episode_reward:.1f})")

        env.close()
        return results
