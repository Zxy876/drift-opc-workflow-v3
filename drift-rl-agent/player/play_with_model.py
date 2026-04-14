"""
用训练好的 PPO 模型玩指定关卡（推理模式）

用法:
  python player/play_with_model.py --model checkpoints/player_ppo_demo.pth --level demo_rl_001

前提: node player/player_bot.js 已在运行
"""

import argparse
import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(__file__))

from drift_mineflayer_env import DriftMineflayerEnv
from action_utils import flat_to_multi

try:
    from tianshou.utils.net.common import Net
    from tianshou.utils.net.discrete import Actor
except ImportError:
    print("请安装 tianshou>=0.5.0,<1.0.0")
    sys.exit(1)


def play(args):
    device = torch.device("cpu")

    # 加载模型
    net = Net(state_shape=(64,), hidden_sizes=[256, 256], device=device)
    actor = Actor(net, action_shape=504, device=device).to(device)
    checkpoint = torch.load(args.model, map_location=device)
    # C1: 支持 actor-only 格式和完整检查点格式
    if isinstance(checkpoint, dict) and "actor" in checkpoint:
        state_dict = checkpoint["actor"]
    else:
        state_dict = checkpoint
    # Q1: 优先尝试 strict=True，如果失败则过滤不匹配的键并降级
    try:
        actor.load_state_dict(state_dict, strict=True)
    except RuntimeError:
        filtered = {
            k: v for k, v in state_dict.items()
            if k in actor.state_dict() and actor.state_dict()[k].shape == v.shape
        }
        actor.load_state_dict(filtered, strict=False)
        print(f"[Play] 和 loose 加载模型（跨版本兼容）")
    actor.eval()
    print(f"[Play] 模型已加载: {args.model}")

    # 创建环境
    env = DriftMineflayerEnv(
        level_id=args.level,
        player_id=args.player_id,
        bot_port=args.port,
    )

    for ep in range(args.episodes):
        obs, _ = env.reset()
        total_reward = 0.0
        done = False
        steps = 0

        while not done:
            obs_tensor = torch.FloatTensor(obs).unsqueeze(0)
            with torch.no_grad():
                logits, _ = actor(obs_tensor)
                flat_action = logits.argmax(dim=1).item()
            action = flat_to_multi(flat_action)
            obs, reward, terminated, truncated, info = env.step(action)
            total_reward += reward
            done = terminated or truncated
            steps += 1

        status = "PASS" if info.get("completed") else "FAIL"
        print(f"  Episode {ep + 1}/{args.episodes}: {status} | "
              f"Steps={steps} | Reward={total_reward:.1f} | "
              f"Deaths={info.get('deaths', 0)} | "
              f"Exploration={info.get('exploration', 0)}")

    env.close()
    print("[Play] 完成")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, required=True, help="模型文件路径")
    parser.add_argument("--level", type=str, default="demo_rl_001")
    parser.add_argument("--player-id", type=str, default="DriftRLAgent",
                        help="玩家 ID（发送给 Drift 后端和 Bot）")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--episodes", type=int, default=5)
    args = parser.parse_args()
    play(args)
