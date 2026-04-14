"""
Drift RL Agent 主入口 — 启动双环自进化系统（StrategyBot 版）

用法：
  python meta/run_evolution.py --level demo_rl_001 --difficulty 3

前提：
  1. node player/player_bot.js 已在运行
  2. MC 服务器 35.201.132.58:25565 已启动
  3. Drift 后端 35.201.132.58:8000 已启动
  4. 环境变量 OPENAI_API_KEY 已设置
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "designer"))

from designer_agent import DesignerAgent
from meta_agent import MetaAgent


def main():
    parser = argparse.ArgumentParser(description="Drift RL Agent — 双环自进化系统（StrategyBot）")
    parser.add_argument("--level", type=str, default="demo_rl_001",
                        help="初始关卡 ID")
    parser.add_argument("--difficulty", type=int, default=3,
                        help="目标难度 D1-D5")
    parser.add_argument("--design", type=str, default=None,
                        help="初始关卡设计描述（如果不提供则使用默认）")
    parser.add_argument("--premium", action="store_true",
                        help="使用 Premium Publish（AsyncAIFlow 全链路）")
    parser.add_argument("--episodes", type=int, default=None,
                        help="每代评估局数（覆盖配置）")
    parser.add_argument("--generations", type=int, default=None,
                        help="最大进化代数（覆盖配置）")
    parser.add_argument("--drift-url", type=str, default="http://35.201.132.58:8000",
                        help="Drift 后端 URL")
    parser.add_argument("--async-url", type=str, default="http://35.201.132.58:8080",
                        help="AsyncAIFlow URL")
    parser.add_argument("--bot-port", type=int, default=9999,
                        help="Mineflayer Bot TCP Bridge 端口")
    parser.add_argument("--curriculum", action="store_true",
                        help="启用课程学习：从 D1 开始逐步升级")
    parser.add_argument("--skill", type=str, default=None,
                        choices=["beginner", "average", "expert"],
                        help="单一技能级别（默认: 多级别评估）")
    parser.add_argument("--player-id", type=str, default="DriftRLAgent",
                        help="玩家 ID（发送给 Drift 后端和 Bot）")
    args = parser.parse_args()

    import signal

    def _sigint_handler(signum, frame):
        print("\n[Evolution] 收到中断信号，正在保存日志并退出...")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _sigint_handler)

    # 默认设计描述（按目标难度选择）
    default_designs = {
        1: "在平坦的草原上收集 3 颗蓝色宝石。NPC 向导在出生点附近提供提示。",
        2: "在森林中收集 5 颗宝石，打败 2 只僵尸。NPC 猎人在森林入口提供弓箭。",
        3: "在浮空岛上收集 5 颗宝石，打败 3 只骷髅弓箭手，到达山顶的传送门。NPC 法师在中途提供治疗。在 120 秒内完成。",
        4: "在地下洞穴中收集 8 颗符文，打败 5 只洞穴蜘蛛和 1 只末影人。NPC 矿工在第一个岔路口等待。在 180 秒内完成。解锁 3 道石门需要对应颜色的钥匙。",
        5: "在海底神殿中收集 10 颗深海珍珠，打败守卫者 Boss（血量 200）。NPC 海洋祭司在入口提供水下呼吸药水。在 240 秒内完成。Boss 每 30 秒召唤 3 只小守卫者。需要激活 4 个海晶灯台才能开启 Boss 房间。",
    }

    if args.design is None:
        args.design = default_designs.get(args.difficulty, default_designs[3])

    # 创建 DesignerAgent
    designer = DesignerAgent(
        drift_url=args.drift_url,
        asyncaiflow_url=args.async_url,
    )

    # 创建 MetaAgent
    meta = MetaAgent(
        designer=designer,
        bot_port=args.bot_port,
        drift_url=args.drift_url,
        single_skill=args.skill,
    )

    # 覆盖配置参数
    if args.episodes:
        meta.episodes_per_eval = args.episodes
    if args.generations:
        meta.max_generations = args.generations

    # 运行进化
    try:
        if args.curriculum:
            # 课程学习模式：D1 → D2 → ... → 目标难度
            print(f"\n[Curriculum] 从 D1 逐步升级到 D{args.difficulty}")
            for d in range(1, args.difficulty + 1):
                design_text = default_designs.get(d, default_designs[3])
                sub_level_id = f"{args.level}_d{d}"
                print(f"\n{'#' * 70}")
                print(f"# Curriculum Stage: D{d}")
                print(f"{'#' * 70}")
                summary = meta.run_evolution(
                    initial_design=design_text,
                    level_id=sub_level_id,
                    player_id=args.player_id,
                    target_difficulty=d,
                    use_premium=args.premium,
                )
                if summary.get("in_flow_zone"):
                    print(f"[Curriculum] D{d} 达到 Flow Zone，升级到 D{d + 1}")
                else:
                    print(f"[Curriculum] D{d} 未达到 Flow Zone，停止升级")
                    break
        else:
            summary = meta.run_evolution(
                initial_design=args.design,
                level_id=args.level,
                player_id=args.player_id,
                target_difficulty=args.difficulty,
                use_premium=args.premium,
            )
            print(f"\n进化摘要: {summary}")
    except KeyboardInterrupt:
        print("\n[Evolution] 用户中断，导出已有日志...")
        if hasattr(meta, 'logger') and meta.logger.entries:
            log_path = meta.logger.export_json()
            print(f"[Evolution] 日志已保存: {log_path}")
        else:
            print("[Evolution] 无进化数据可保存")
        sys.exit(0)


if __name__ == "__main__":
    main()
