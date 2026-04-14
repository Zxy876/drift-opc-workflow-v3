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

    # ─── 结果 ───
    print(f"\n{'=' * 40}")
    print(f" 结果: {PASS} 通过, {FAIL} 失败, {SKIP} 跳过")
    print(f"{'=' * 40}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
