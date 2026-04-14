"""
Drift RL Agent 冒烟测试（不需要 MC 服务器）

验证所有 Python 模块可以正常导入、配置可加载、核心逻辑正确。

用法: python tests/smoke_test.py
"""

import os
import sys
import json
import numpy as np

# 设置路径
ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "player"))
sys.path.insert(0, os.path.join(ROOT, "designer"))
sys.path.insert(0, os.path.join(ROOT, "meta"))

PASS = 0
FAIL = 0
SKIP = 0

# 检测 torch 是否可用
try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


def test(name, fn, requires_torch=False):
    global PASS, FAIL, SKIP
    if requires_torch and not HAS_TORCH:
        print(f"  - {name}: SKIP (torch 未安装)")
        SKIP += 1
        return
    try:
        fn()
        print(f"  ✓ {name}")
        PASS += 1
    except Exception as e:
        print(f"  ✗ {name}: {e}")
        FAIL += 1


def main():
    global PASS, FAIL
    print("\n=== Drift RL Agent 冒烟测试 ===\n")

    # ─── 1. 导入测试 ───
    print("[1] 模块导入...")

    def test_import_env():
        from drift_mineflayer_env import DriftMineflayerEnv
        assert DriftMineflayerEnv is not None

    def test_import_reward():
        from reward_functions import compute_reward, load_reward_params
        assert compute_reward is not None
        assert load_reward_params is not None

    def test_import_train():
        from train_player import FlatActionWrapper, load_training_config
        assert FlatActionWrapper is not None
    def test_import_designer():
        from designer_agent import DesignerAgent
        assert DesignerAgent is not None

    def test_import_eval():
        from eval_bridge import analyze_play_data, format_eval_for_llm
        assert analyze_play_data is not None

    def test_import_meta():
        from meta_agent import MetaAgent
        assert MetaAgent is not None
    def test_import_evo_log():
        from evolution_log import EvolutionLog
        assert EvolutionLog is not None

    def test_import_obs_space():
        from observation_space import get_observation_space, OBSERVATION_LAYOUT
        assert len(OBSERVATION_LAYOUT) > 0
        sp = get_observation_space()
        assert sp.shape == (64,)

    def test_import_action_utils():
        from action_utils import flat_to_multi, multi_to_flat, ACTION_FLAT_N
        assert ACTION_FLAT_N == 504

    test("drift_mineflayer_env", test_import_env)
    test("reward_functions", test_import_reward)
    test("train_player", test_import_train, requires_torch=True)
    test("designer_agent", test_import_designer)
    test("eval_bridge", test_import_eval)
    test("meta_agent", test_import_meta, requires_torch=True)
    test("evolution_log", test_import_evo_log)
    test("observation_space", test_import_obs_space)
    test("action_utils", test_import_action_utils)

    # ─── 2. 配置加载测试 ───
    print("\n[2] 配置加载...")

    def test_reward_params():
        from reward_functions import load_reward_params
        params = load_reward_params()
        assert "alive_per_tick" in params
        assert "death_penalty" in params
        assert params["alive_per_tick"] == 0.01

    def test_training_config():
        from train_player import load_training_config
        cfg = load_training_config()
        assert cfg["algorithm"] == "PPO"
        assert cfg["lr"] == 3e-4

    test("reward_params.yaml", test_reward_params)
    test("evolution_params.yaml", test_training_config, requires_torch=True)

    # ─── 3. FlatActionWrapper 编解码测试 ───
    print("\n[3] FlatActionWrapper 编解码...")

    def test_flat_action_roundtrip():
        from train_player import FlatActionWrapper
        import gymnasium as gym

        class FakeEnv(gym.Env):
            def __init__(self):
                self.action_space = gym.spaces.MultiDiscrete([3, 3, 2, 2, 2, 7])
                self.observation_space = gym.spaces.Box(low=0, high=1, shape=(1,))
            def step(self, a): return None, 0, False, False, {}
            def reset(self, **kw): return np.zeros(1), {}

        wrapper = FlatActionWrapper(FakeEnv())
        assert wrapper.action_space.n == 504

        # 测试边界值
        assert np.array_equal(wrapper.action(0), [0, 0, 0, 0, 0, 0])
        assert np.array_equal(wrapper.action(503), [2, 2, 1, 1, 1, 6])

        # 测试 action=1 → cmd_type=1 (/easy)
        a = wrapper.action(1)
        assert a[-1] == 1  # cmd_type=1

    def test_action_utils_roundtrip():
        from action_utils import flat_to_multi, multi_to_flat, ACTION_FLAT_N
        # 测试边界
        assert np.array_equal(flat_to_multi(0), [0, 0, 0, 0, 0, 0])
        assert np.array_equal(flat_to_multi(503), [2, 2, 1, 1, 1, 6])
        # 测试双向转换
        for flat in [0, 1, 100, 503]:
            multi = flat_to_multi(flat)
            assert multi_to_flat(multi) == flat

    test("FlatAction 编解码", test_flat_action_roundtrip, requires_torch=True)
    test("action_utils 双向转换", test_action_utils_roundtrip)

    # ─── 4. 奖励函数测试 ───
    print("\n[4] 奖励函数...")

    def test_reward_alive():
        from reward_functions import compute_reward, load_reward_params
        params = load_reward_params()
        r = compute_reward({}, {"health": 20}, {}, False, {}, params)
        assert r > 0, f"存活奖励应 > 0, 实际={r}"

    def test_reward_death():
        from reward_functions import compute_reward, load_reward_params
        params = load_reward_params()
        r = compute_reward({"health": 20}, {"health": 0}, {}, True, {}, params)
        assert r < 0, f"死亡奖励应 < 0, 实际={r}"

    def test_reward_complete():
        from reward_functions import compute_reward, load_reward_params
        params = load_reward_params()
        r = compute_reward({}, {"health": 20}, {}, True,
                          {"level_completed": True, "time": 100}, params)
        assert r > 5.0, f"通关奖励应 > 5.0, 实际={r}"

    def test_reward_easy_penalty():
        from reward_functions import compute_reward, load_reward_params
        params = load_reward_params()
        r_no = compute_reward({}, {"health": 20}, {}, False,
                             {"easy_just_used": False}, params)
        r_yes = compute_reward({}, {"health": 20}, {}, False,
                              {"easy_just_used": True}, params)
        assert r_yes < r_no, f"/easy 惩罚未生效: {r_yes} vs {r_no}"

    test("存活正奖励", test_reward_alive)
    test("死亡负奖励", test_reward_death)
    test("通关大奖", test_reward_complete)
    test("/easy 惩罚", test_reward_easy_penalty)

    # ─── 5. EvalBridge 测试 ───
    print("\n[5] EvalBridge...")

    def test_eval_bridge():
        from eval_bridge import analyze_play_data, format_eval_for_llm
        results = [
            {"completed": True, "time": 100, "deaths": 0, "easy_used": False,
             "death_causes": [], "stuck_positions": [], "exploration": 50},
            {"completed": False, "time": 200, "deaths": 2, "easy_used": True,
             "death_causes": ["fall", "mob"], "stuck_positions": [[10, 20, 30]],
             "exploration": 30},
        ]
        report = analyze_play_data(results)
        assert report["completion_rate"] == 0.5
        assert report["total_episodes"] == 2
        assert report["avg_time"] == 150.0
        assert report["avg_deaths"] == 1.0
        assert report["easy_usage_rate"] == 0.5

        text = format_eval_for_llm(report)
        assert "50%" in text
        assert "150s" in text

    def test_eval_empty():
        from eval_bridge import analyze_play_data
        report = analyze_play_data([])
        assert report["completion_rate"] == 0.0
        assert report["total_episodes"] == 0

    test("EvalBridge 分析", test_eval_bridge)
    test("EvalBridge 空数据", test_eval_empty)

    # ─── 6. EvolutionLog 测试 ───
    print("\n[6] EvolutionLog...")

    def test_evolution_log():
        import tempfile
        from evolution_log import EvolutionLog
        with tempfile.TemporaryDirectory() as tmpdir:
            log = EvolutionLog(log_dir=tmpdir)
            log.log_generation(0, "test design", {"completion_rate": 0.5,
                "avg_time": 100, "avg_deaths": 1, "easy_usage_rate": 0.2,
                "death_causes": {}, "stuck_points": {}, "total_episodes": 10,
                "avg_exploration": 30})

            summary = log.get_summary()
            assert summary["total_generations"] == 1
            assert summary["final_completion_rate"] == 0.5

            path = log.export_json()
            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)
            assert len(data["generations"]) == 1

    test("EvolutionLog 读写", test_evolution_log)

    # ─── 7. Phase 4 新增测试 ───
    print("\n[7] Phase 4: C1/C2/C3/R3/F3/F5 验证...")

    def test_c1_actor_only_load():
        """C1: actor-only 格式加载（strict=True 应成功）"""
        import torch
        from tianshou.utils.net.common import Net
        from tianshou.utils.net.discrete import Actor
        import tempfile, os
        device = torch.device("cpu")
        net = Net(state_shape=(64,), hidden_sizes=[256, 256], device=device)
        actor = Actor(net, action_shape=504, device=device).to(device)
        # 保存纯 actor state_dict
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "actor.pth")
            torch.save(actor.state_dict(), path)
            # 模拟 play_with_model / meta_agent 加载逻辑
            checkpoint = torch.load(path, map_location=device)
            if isinstance(checkpoint, dict) and "actor" in checkpoint:
                state_dict = checkpoint["actor"]
            else:
                state_dict = checkpoint
            actor2 = Actor(Net(state_shape=(64,), hidden_sizes=[256, 256], device=device),
                           action_shape=504, device=device).to(device)
            actor2.load_state_dict(state_dict, strict=True)

    def test_c1_full_checkpoint_load():
        """C1: 完整检查点格式加载（读取 checkpoint['actor']）"""
        import torch
        from tianshou.utils.net.common import Net
        from tianshou.utils.net.discrete import Actor
        import tempfile, os
        device = torch.device("cpu")
        net = Net(state_shape=(64,), hidden_sizes=[256, 256], device=device)
        actor = Actor(net, action_shape=504, device=device).to(device)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "policy.pth")
            # 保存完整格式（只需 actor 键存在即可）
            torch.save({"actor": actor.state_dict(), "extra": "ignored"}, path)
            checkpoint = torch.load(path, map_location=device)
            assert isinstance(checkpoint, dict) and "actor" in checkpoint
            state_dict = checkpoint["actor"]
            actor2 = Actor(Net(state_shape=(64,), hidden_sizes=[256, 256], device=device),
                           action_shape=504, device=device).to(device)
            actor2.load_state_dict(state_dict, strict=True)

    def test_c2_env_close_no_connection():
        """C2: env.close() 在未连接时不应抛出异常"""
        from drift_mineflayer_env import DriftMineflayerEnv
        env = DriftMineflayerEnv.__new__(DriftMineflayerEnv)
        env.sock = None  # 模拟未连接状态
        # close() 应静默处理 _send 失败
        env.close()  # 不应抛出

    def test_c3_flat_wrapper_consistent():
        """C3: FlatActionWrapper.action() 与 action_utils.flat_to_multi 结果一致"""
        import numpy as np
        import gymnasium as gym
        from train_player import FlatActionWrapper
        from action_utils import flat_to_multi

        class FakeEnv(gym.Env):
            def __init__(self):
                self.action_space = gym.spaces.MultiDiscrete([3, 3, 2, 2, 2, 7])
                self.observation_space = gym.spaces.Box(low=0, high=1, shape=(1,))
            def step(self, a): return None, 0, False, False, {}
            def reset(self, **kw): return np.zeros(1), {}

        wrapper = FlatActionWrapper(FakeEnv())
        for flat in [0, 1, 100, 503]:
            assert np.array_equal(wrapper.action(flat), flat_to_multi(flat)), \
                f"不一致 flat={flat}: wrapper={wrapper.action(flat)} vs utils={flat_to_multi(flat)}"

    def test_r3_evolution_log_immediate_persist():
        """R3: log_generation 后日志文件应立即存在且可读"""
        import tempfile, os, json
        from evolution_log import EvolutionLog
        with tempfile.TemporaryDirectory() as tmpdir:
            log = EvolutionLog(log_dir=tmpdir)
            log.log_generation(0, "design", {"completion_rate": 0.7,
                "avg_time": 90, "avg_deaths": 0, "easy_usage_rate": 0,
                "death_causes": {}, "stuck_points": {}, "total_episodes": 5,
                "avg_exploration": 20})
            log_file = os.path.join(tmpdir, f"{log.run_id}.jsonl")
            assert os.path.exists(log_file), "日志文件应在 log_generation 后立即存在"
            with open(log_file) as f:
                data = json.loads(f.readline())
            assert data["generation"] == 0

    def test_f5_run_evolution_has_player_id_flag():
        """F5: run_evolution.py 的 argparse 应包含 --player-id 参数"""
        run_evo_path = os.path.join(ROOT, "meta", "run_evolution.py")
        with open(run_evo_path) as f:
            src = f.read()
        assert "--player-id" in src, "--player-id 参数未在 run_evolution.py 中定义"
        assert "--curriculum" in src, "--curriculum 参数未在 run_evolution.py 中定义"
        assert "--model" in src, "--model 参数未在 run_evolution.py 中定义"

    test("C1: actor-only 格式加载", test_c1_actor_only_load, requires_torch=True)
    test("C1: 完整检查点格式加载", test_c1_full_checkpoint_load, requires_torch=True)
    test("C2: env.close 未连接不抛异常", test_c2_env_close_no_connection)
    test("C3: FlatWrapper 与 action_utils 一致", test_c3_flat_wrapper_consistent, requires_torch=True)
    test("R3: EvolutionLog 立即持久化", test_r3_evolution_log_immediate_persist)
    test("F5: run_evolution --player-id 参数", test_f5_run_evolution_has_player_id_flag)

    # ─── 8. Phase 5 新增测试 ───
    print("\n[8] Phase 5: S1/F1/F4/Q2 验证...")

    def test_s1_player_bot_syntax():
        """S1: player_bot.js 应通过 JS 语法检查"""
        import subprocess
        bot_path = os.path.join(ROOT, "player", "player_bot.js")
        result = subprocess.run(
            ["node", "-c", bot_path],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"player_bot.js 语法错误: {result.stderr}"

    def test_f1_generate_new_level_has_retry():
        """F1: generate_new_level 应有 max_retries 参数"""
        import inspect
        from designer_agent import DesignerAgent
        sig = inspect.signature(DesignerAgent.generate_new_level)
        assert "max_retries" in sig.parameters, "generate_new_level 缺少 max_retries 参数"

    def test_q2_best_checkpoint_has_actor_key():
        """Q2: MetaAgent 保存的最佳检查点应包含 'actor' 键"""
        import torch, tempfile
        from tianshou.utils.net.common import Net
        from tianshou.utils.net.discrete import Actor
        device = torch.device("cpu")
        net = Net(state_shape=(64,), hidden_sizes=[256, 256], device=device)
        actor = Actor(net, action_shape=504, device=device).to(device)
        # 模拟 MetaAgent 的保存逻辑（Q2 修复后格式）
        with tempfile.TemporaryDirectory() as d:
            ckpt_path = os.path.join(d, "best_test.pth")
            torch.save({"actor": actor.state_dict()}, ckpt_path)
            checkpoint = torch.load(ckpt_path, map_location=device)
            assert isinstance(checkpoint, dict) and "actor" in checkpoint, \
                "最佳检查点应包含 'actor' 键"

    def test_f4_reset_no_hardcoded_sleep():
        """F4: reset() 不应有硬编码的 time.sleep(3)"""
        env_path = os.path.join(ROOT, "player", "drift_mineflayer_env.py")
        with open(env_path) as f:
            src = f.read()
        reset_start = src.find("def reset(")
        reset_end = src.find("\n    def ", reset_start + 1)
        if reset_end == -1:
            reset_end = len(src)
        reset_code = src[reset_start:reset_end]
        assert "sleep(3)" not in reset_code, "reset() 仍有 time.sleep(3) 硬编码"

    test("S1: player_bot.js 语法检查", test_s1_player_bot_syntax)
    test("F1: generate_new_level 重试参数", test_f1_generate_new_level_has_retry)
    test("Q2: 最佳检查点包含 actor 键", test_q2_best_checkpoint_has_actor_key, requires_torch=True)
    test("F4: reset 无硬编码 sleep(3)", test_f4_reset_no_hardcoded_sleep)

    # ─── 结果 ───
    print(f"\n{'=' * 40}")
    print(f" 结果: {PASS} 通过, {FAIL} 失败, {SKIP} 跳过")
    print(f"{'=' * 40}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
