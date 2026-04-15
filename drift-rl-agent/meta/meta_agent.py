"""
MetaAgent — 双环自进化控制器

外环（慢）: DesignerAgent 每轮改进一次关卡设计
内环（快）: StrategyBot 每轮玩 N 局评估

进化停止条件：通关率连续 3 代在 [60%, 80%]（Flow Zone）
"""

import os
import random
import sys
import time
from typing import Optional

import requests
import yaml

# 添加 parent 目录到 path（方便 import）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "player"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "designer"))

from bot_client import BotClient
from strategy_bot import StrategyBot
from skill_profiles import load_skill_profiles, load_episodes_per_skill, EPISODES_PER_SKILL
from designer_agent import DesignerAgent
from eval_bridge import analyze_multi_skill_data, format_multi_skill_eval
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
        single_skill: Optional[str] = None,
        status_file: Optional[str] = None,
    ):
        self.designer = designer
        self.bot_host = bot_host
        self.bot_port = bot_port
        self.drift_url = drift_url
        self.single_skill = single_skill
        self.status_file = status_file
        self.logger = EvolutionLog()

        # 进化状态追踪（供 _write_status 使用）
        self._current_gen: int = -1
        self._current_design: str = ""
        self._history: list = []

        # 加载配置
        self._load_config(config_path)

        # 加载技能档案（供 run_evolution 打印摘要使用）
        self.skill_profiles = load_skill_profiles()
        self.episodes_per_skill_map = load_episodes_per_skill()

    def _write_status(self, data: dict):
        """写入状态文件供面板轮询（无 status_file 时静默跳过）"""
        if not self.status_file:
            return
        try:
            import json
            with open(self.status_file, "w") as f:
                json.dump(data, f, ensure_ascii=False, default=str)
        except Exception:
            pass

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
        history: list = []

        print(f"\n{'#' * 70}")
        print(f"# Drift RL Agent — 双环自进化系统（StrategyBot）")
        print(f"# 初始关卡: {level_id}")
        print(f"# 目标难度: D{target_difficulty}")
        print(f"# 每代评估: {self.episodes_per_eval} 局")
        print(f"# 最大代数: {self.max_generations}")
        print(f"# 技能档案: {list(self.skill_profiles.keys())}")
        print(f"{'#' * 70}\n")

        for gen in range(self.max_generations):
            print(f"\n{'=' * 60}")
            print(f" Generation {gen}")
            print(f"{'=' * 60}")

            # 更新追踪属性，供 _run_player_episodes 中写状态使用
            self._current_gen = gen
            self._current_design = current_design
            self._history = history

            self._write_status({
                "status": "running",
                "current_phase": "playing",
                "generation": gen,
                "total_generations": self.max_generations,
                "episode": 0,
                "total_episodes": self.episodes_per_eval,
                "design_text": current_design,
                "history": history,
            })

            # ─── 内环：StrategyBot 游玩 N 局 ───
            print(f"\n[Player] 开始游玩 {self.episodes_per_eval} 局（多技能级别）...")
            play_results = self._run_player_episodes(current_level_id, player_id)

            # ─── 评估 ───
            eval_report = analyze_multi_skill_data(play_results)
            cr = eval_report["completion_rate"]
            avg_cr = eval_report.get("completion_by_skill", {}).get("average", cr)

            print(f"\n[Eval] 整体通关率: {cr:.0%}")
            print(f"[Eval] 中等玩家通关率: {avg_cr:.0%}")
            print(f"[Eval] 平均时间: {eval_report['avg_time']:.0f}s")
            print(f"[Eval] 平均死亡: {eval_report['avg_deaths']:.1f}")
            print(f"[Eval] /easy 使用率: {eval_report['easy_usage_rate']:.0%}")
            print(f"[Eval] 难度评估: {eval_report.get('difficulty_assessment', '—')}")
            print(f"[Eval] 评估报告:\n{format_multi_skill_eval(eval_report)}")

            # 追加历史记录
            history.append({"generation": gen, "completion_rate": cr, "avg_cr": avg_cr})
            self._history = history

            self._write_status({
                "status": "running",
                "current_phase": "evaluating",
                "generation": gen,
                "total_generations": self.max_generations,
                "completion_rate": cr,
                "flow_zone": self.flow_zone_min <= avg_cr <= self.flow_zone_max,
                "flow_zone_streak": flow_zone_streak + (
                    1 if self.flow_zone_min <= avg_cr <= self.flow_zone_max else 0
                ),
                "skill_results": eval_report.get("completion_by_skill", {}),
                "difficulty_assessment": eval_report.get("difficulty_assessment", ""),
                "design_text": current_design,
                "history": history,
            })

            # 检查 Flow Zone（以 average 技能通关率为基准）
            if self.flow_zone_min <= avg_cr <= self.flow_zone_max:
                flow_zone_streak += 1
                print(f"\n[Meta] ✓ Flow Zone! (连续 {flow_zone_streak}/{self.flow_zone_streak_target})")
                if flow_zone_streak >= self.flow_zone_streak_target:
                    print(f"\n[Meta] 达到稳定 Flow Zone，进化完成！")
                    self.logger.log_generation(gen, current_design, eval_report)
                    self._write_status({
                        "status": "completed",
                        "generation": gen,
                        "total_generations": gen + 1,
                        "completion_rate": cr,
                        "flow_zone": True,
                        "flow_zone_streak": flow_zone_streak,
                        "design_text": current_design,
                        "history": history,
                    })
                    break
            else:
                flow_zone_streak = 0
                direction = "偏低" if avg_cr < self.flow_zone_min else "偏高"
                print(f"\n[Meta] ✗ 未在 Flow Zone (中等玩家通关率{direction})")

            # ─── 外环：DesignerAgent 改进关卡 ───
            print(f"\n[Designer] 正在用 LLM 改进关卡设计...")
            self._write_status({
                "status": "running",
                "current_phase": "designing",
                "generation": gen,
                "total_generations": self.max_generations,
                "completion_rate": cr,
                "design_text": current_design,
                "history": history,
            })
            new_design = self.designer.generate_improved_design(
                current_design, eval_report, target_difficulty
            )
            print(f"[Designer] 改进理由: {new_design.get('reasoning', '—')}")
            print(f"[Designer] 改动: {new_design.get('changes', [])}")

            # 发布到 Drift
            new_level_id = f"{level_id.split('_gen')[0]}_gen{gen + 1}"
            print(f"\n[Designer] 发布新关卡: {new_level_id}")
            self._write_status({
                "status": "running",
                "current_phase": "publishing",
                "generation": gen,
                "total_generations": self.max_generations,
                "new_design": new_design.get("design_text", ""),
                "design_text": current_design,
                "history": history,
            })
            publish_result = self.designer.publish_to_drift(
                new_design, new_level_id, player_id,
                use_premium=use_premium,
            )
            print(f"[Designer] 发布结果: {publish_result.get('method', '?')}")

            # 通知 Drift 加载新关卡
            try:
                load_url = f"{self.drift_url}/story/load/{player_id}/{new_level_id}"
                resp = requests.post(load_url, timeout=10)
                if resp.ok:
                    print(f"[Meta] Drift 已加载新关卡: {new_level_id}")
                else:
                    print(f"[Meta] 关卡加载请求返回 {resp.status_code}")
            except requests.RequestException as e:
                print(f"[Meta] 关卡加载请求失败(可忽略): {e}")

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

        # 如果 Flow Zone break 时未写 completed（超代上限退出），补写一次
        self._write_status({
            "status": "completed",
            "generation": self._current_gen,
            "total_generations": self._current_gen + 1,
            "completion_rate": summary.get("final_completion_rate", 0),
            "flow_zone": summary.get("in_flow_zone", False),
            "design_text": current_design,
            "history": history,
            "summary": summary,
        })

        return summary

    def _run_player_episodes(self, level_id: str, player_id: str) -> list:
        """
        运行 N 局 StrategyBot 游玩（多技能级别）

        按 EPISODES_PER_SKILL 分配各技能级别的局数。
        """
        results = []
        consecutive_failures = 0
        max_consecutive = 3

        # 按技能级别分配局数
        schedule = []
        if self.single_skill:
            # 单技能模式：所有局都用指定技能
            schedule = [self.single_skill] * self.episodes_per_eval
        else:
            for skill, count in self.episodes_per_skill_map.items():
                for _ in range(count):
                    schedule.append(skill)
            random.shuffle(schedule)  # 打乱顺序，避免系统偏差

        for ep, skill in enumerate(schedule):
            client = None
            try:
                client = BotClient(host=self.bot_host, port=self.bot_port)
                bot = StrategyBot(
                    client=client,
                    skill=skill,
                    level_id=level_id,
                    player_id=player_id,
                )
                result = bot.play_episode()
                result["episode"] = ep
                results.append(result)
                consecutive_failures = 0

                status = "PASS" if result["completed"] else "FAIL"
                print(f"  Episode {ep + 1}/{len(schedule)} [{skill}]: {status} "
                      f"({result['time']:.0f}s, {result['deaths']} deaths, "
                      f"explore={result['exploration']})")

                # 每局结束写状态文件
                self._write_status({
                    "status": "running",
                    "current_phase": "playing",
                    "generation": self._current_gen,
                    "total_generations": self.max_generations,
                    "episode": ep + 1,
                    "total_episodes": len(schedule),
                    "design_text": self._current_design,
                    "history": self._history,
                })

            except Exception as e:
                consecutive_failures += 1
                print(f"  Episode {ep + 1}/{len(schedule)} [{skill}]: ERROR — {e}")
                if consecutive_failures >= max_consecutive:
                    print(f"[Meta] 连续 {max_consecutive} 局出错，提前终止本代评估")
                    break

            finally:
                if client is not None:
                    client.disconnect()

        return results
