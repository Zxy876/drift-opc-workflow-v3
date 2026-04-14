"""
Drift PlayerAgent 训练脚本

使用 Tianshou PPO 在 DriftMineflayerEnv 上训练 RL 策略。

用法：
  python player/train_player.py --level demo_rl_001 --epochs 100

前提：
  1. node player/player_bot.js 已在运行
  2. MC 服务器已启动且可连接
"""

import argparse
import os
from typing import Optional

import gymnasium as gym
import numpy as np
import yaml
import torch
from torch.optim import Adam

# ─── Tianshou 导入（兼容 v0.5 / v1.x）────────────────────
try:
    from tianshou.data import Collector, VectorReplayBuffer
    from tianshou.env import DummyVectorEnv
    from tianshou.policy import PPOPolicy
    from tianshou.trainer import OnpolicyTrainer
    from tianshou.utils.net.common import Net
    from tianshou.utils.net.discrete import Actor, Critic
except ImportError as e:
    raise ImportError(
        f"无法导入 tianshou: {e}\n"
        "请安装: pip install tianshou>=0.5.0\n"
        "如使用 tianshou>=1.0，请参考官方文档调整 import。"
    )

from drift_mineflayer_env import DriftMineflayerEnv
from action_utils import flat_to_multi


class FlatActionWrapper(gym.ActionWrapper):
    """
    将 MultiDiscrete([3,3,2,2,2,7]) 映射为 Discrete(504)
    用于 Tianshou Categorical PPO 训练
    """
    def __init__(self, env):
        super().__init__(env)
        self.orig_nvec = env.action_space.nvec  # [3,3,2,2,2,7]
        self.flat_n = int(np.prod(self.orig_nvec))  # 504
        self.action_space = gym.spaces.Discrete(self.flat_n)

    def action(self, act):
        """Discrete(504) → MultiDiscrete([3,3,2,2,2,7])"""
        return flat_to_multi(int(act))


def load_training_config(config_path: Optional[str] = None) -> dict:
    """加载训练配置"""
    defaults = {
        "algorithm": "PPO",
        "lr": 3e-4,
        "gamma": 0.99,
        "gae_lambda": 0.95,
        "eps_clip": 0.2,
        "vf_coef": 0.5,
        "ent_coef": 0.01,
        "max_grad_norm": 0.5,
        "repeat_per_collect": 10,
        "batch_size": 256,
        "hidden_sizes": [256, 256],
        "buffer_size": 20000,
        "max_epoch": 100,
        "step_per_epoch": 5000,
        "step_per_collect": 2000,
        "episode_per_test": 10,
        "num_train_envs": 1,
        "num_test_envs": 1,
    }

    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "configs", "evolution_params.yaml"
        )

    try:
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f) or {}
        defaults.update(raw.get("player_training", {}))
    except FileNotFoundError:
        pass

    return defaults


def make_env(level_id: str, player_id: str, bot_port: int = 9999):
    """创建环境工厂函数（包含 FlatActionWrapper）"""
    def _make():
        env = DriftMineflayerEnv(
            level_id=level_id,
            player_id=player_id,
            bot_port=bot_port,
        )
        return FlatActionWrapper(env)
    return _make


def train(args):
    """主训练循环"""
    config = load_training_config(args.config)
    if args.epochs is not None:
        config["max_epoch"] = args.epochs

    print(f"[Train] Level: {args.level}")
    print(f"[Train] Config: {config}")

    # 多环境注意：每个环境需要独立的 Bot 实例（不同端口）
    num_train = config.get("num_train_envs", 1)
    num_test = config.get("num_test_envs", 1)
    if num_train > 1:
        print(f"[Train] 警告: num_train_envs={num_train} 需要多个 Bot 实例; 当前自动降级为 1")
        num_train = 1
    if num_test > 1:
        print(f"[Train] 警告: num_test_envs={num_test} 需要多个 Bot 实例; 当前自动降级为 1")
        num_test = 1

    train_envs = DummyVectorEnv(
        [make_env(args.level, "RLAgent_train", 9999) for _ in range(num_train)]
    )
    test_envs = DummyVectorEnv(
        [make_env(args.level, "RLAgent_test", 9999) for _ in range(num_test)]
    )

    obs_shape = (64,)
    # 已经被 FlatActionWrapper 展平为 Discrete(504)
    action_shape = 504

    hidden_sizes = config.get("hidden_sizes", [256, 256])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[Train] Device: {device}")

    net = Net(
        state_shape=obs_shape,
        hidden_sizes=hidden_sizes,
        device=device,
    )

    actor = Actor(net, action_shape=action_shape, device=device).to(device)
    critic = Critic(net, device=device).to(device)

    optim = Adam(
        list(actor.parameters()) + list(critic.parameters()),
        lr=config["lr"],
    )

    policy = PPOPolicy(
        actor=actor,
        critic=critic,
        optim=optim,
        dist_fn=torch.distributions.Categorical,
        discount_factor=config["gamma"],
        gae_lambda=config["gae_lambda"],
        eps_clip=config["eps_clip"],
        vf_coef=config["vf_coef"],
        ent_coef=config["ent_coef"],
        max_grad_norm=config["max_grad_norm"],
        action_space=train_envs.action_space[0],  # 单个 Discrete(504) space
    )

    buffer = VectorReplayBuffer(
        total_size=config["buffer_size"],
        buffer_num=num_train,
    )

    train_collector = Collector(policy, train_envs, buffer)
    test_collector = Collector(policy, test_envs)

    result = OnpolicyTrainer(
        policy=policy,
        train_collector=train_collector,
        test_collector=test_collector,
        max_epoch=config["max_epoch"],
        step_per_epoch=config["step_per_epoch"],
        repeat_per_collect=config["repeat_per_collect"],
        episode_per_test=config["episode_per_test"],
        batch_size=config["batch_size"],
        step_per_collect=config["step_per_collect"],
    ).run()

    print(f"\n[Train] 训练完成!")
    print(f"[Train] 最佳奖励: {result.get('best_reward', 'N/A')}")

    # 保存模型
    save_dir = os.path.join(os.path.dirname(__file__), "..", "checkpoints")
    os.makedirs(save_dir, exist_ok=True)
    actor_path = os.path.join(save_dir, f"player_actor_{args.level}.pth")
    checkpoint_path = os.path.join(save_dir, f"player_policy_{args.level}.pth")
    torch.save(policy.actor.state_dict(), actor_path)
    torch.save({
        "actor": policy.actor.state_dict(),
        "critic": policy.critic.state_dict(),
        "optim": policy.optim.state_dict(),
    }, checkpoint_path)
    print(f"[Train] Actor 已保存: {actor_path}")
    print(f"[Train] 完整检查点已保存: {checkpoint_path}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train Drift PlayerAgent with PPO")
    parser.add_argument("--level", type=str, default="demo_rl_001", help="关卡 ID")
    parser.add_argument("--config", type=str, default=None, help="配置文件路径")
    parser.add_argument("--epochs", type=int, default=None, help="训练 epoch 数（覆盖配置）")
    args = parser.parse_args()
    train(args)
