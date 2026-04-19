#!/usr/bin/env python3
"""
DriftAgent Play Mode — 轻量级单局游玩（无进化循环）

被 evolution_api.py 的 /evolution/play 端点以子进程方式启动。
通过 BotClient TCP Bridge 连接 player_bot.js，运行 StrategyBot 单局。
决策日记实时写入状态文件供面板轮询。
"""

import argparse
import json
import os
import sys
import time

# 添加 player 目录到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from bot_client import BotClient
from strategy_bot import StrategyBot


def main():
    parser = argparse.ArgumentParser(description="DriftAgent Play Mode")
    parser.add_argument("--level", required=True, help="关卡 ID")
    parser.add_argument("--player-id", default="DriftRLAgent", help="玩家 ID")
    parser.add_argument("--skill", default="average", help="技能档位")
    parser.add_argument("--max-steps", type=int, default=3000, help="最大步数")
    parser.add_argument("--bot-host", default="localhost", help="Bot TCP Bridge 地址")
    parser.add_argument("--bot-port", type=int, default=9999, help="Bot TCP Bridge 端口")
    parser.add_argument("--status-file", default="", help="状态文件路径（供面板轮询）")
    parser.add_argument("--diary-file", default="", help="决策日记文件路径")
    args = parser.parse_args()

    status_file = args.status_file
    diary_file = args.diary_file or f"/tmp/driftagent_diary_{args.level}.json"

    # 初始化日记
    diary = {
        "level_id": args.level,
        "player_id": args.player_id,
        "skill": args.skill,
        "started_at": time.time(),
        "decisions": [],
        "result": None,
    }

    def write_status(phase, **extra):
        if not status_file:
            return
        data = {
            "status": "running",
            "current_phase": phase,
            "level_id": args.level,
            "skill": args.skill,
            "diary_entry_count": len(diary["decisions"]),
            **extra,
        }
        try:
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def write_diary():
        try:
            with open(diary_file, "w", encoding="utf-8") as f:
                json.dump(diary, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    write_status("connecting")
    print(f"[DriftAgent Play] 关卡={args.level} 技能={args.skill} 最大步数={args.max_steps}")

    client = None
    try:
        client = BotClient(host=args.bot_host, port=args.bot_port)
        client.wait_ready(timeout=30)
        print("[DriftAgent Play] Bot 已连接")

        write_status("playing")

        # 创建带日记功能的 StrategyBot
        bot = StrategyBot(
            client=client,
            skill=args.skill,
            level_id=args.level,
            player_id=args.player_id,
            max_steps=args.max_steps,
            broadcast=True,
        )

        # ── 猴子补丁: 拦截 _broadcast 以记录决策日记 ──
        original_broadcast = bot._broadcast

        def diary_broadcast(level, msg):
            diary["decisions"].append({
                "time": time.time(),
                "step": bot.steps,
                "level": level,
                "message": msg,
            })
            # 每 10 条写一次日记文件
            if len(diary["decisions"]) % 10 == 0:
                write_diary()
                write_status("playing", step=bot.steps, decisions=len(diary["decisions"]))
            original_broadcast(level, msg)

        bot._broadcast = diary_broadcast

        # 运行单局
        result = bot.play_episode()
        diary["result"] = result
        diary["finished_at"] = time.time()

        print(
            f"[DriftAgent Play] 完成: 通关={'是' if result.get('completed') else '否'} "
            f"步数={bot.steps} 死亡={result.get('deaths', 0)} 探索={result.get('exploration', 0)}"
        )

        # 广播最终结果
        summary = (
            f"📊 Play 结果: {'通关' if result.get('completed') else '未通关'} | "
            f"步数 {bot.steps} | 死亡 {result.get('deaths', 0)} | 探索 {result.get('exploration', 0)}"
        )
        client.broadcast("INFO", summary)

        # 广播日记摘要
        decision_count = len(diary["decisions"])
        combat_count = sum(1 for d in diary["decisions"] if "战斗" in d["message"] or "攻击" in d["message"])
        collect_count = sum(1 for d in diary["decisions"] if "拾取" in d["message"] or "收集" in d["message"])
        danger_count = sum(1 for d in diary["decisions"] if "危险" in d["message"] or "逃" in d["message"])
        client.broadcast(
            "INFO",
            f"📖 决策日记: {decision_count} 条 | 战斗 {combat_count} | 收集 {collect_count} | 危险 {danger_count}",
        )

        write_diary()
        write_status("completed", result=result)

    except Exception as exc:
        print(f"[DriftAgent Play] 错误: {exc}")
        diary["error"] = str(exc)
        write_diary()
        write_status("error", error=str(exc))
        sys.exit(1)

    finally:
        if client:
            try:
                client.stop_all()
            except Exception:
                pass
            client.disconnect()


if __name__ == "__main__":
    main()
