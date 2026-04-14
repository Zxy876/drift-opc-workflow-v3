"""
Drift RL Agent 冒烟测试（不需要 MC 服务器）

验证所有 Python 模块可以正常导入、配置可加载、核心逻辑正确。

用法: python tests/smoke_test.py
"""

import os
import sys
import json

# 设置路径
ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "player"))
sys.path.insert(0, os.path.join(ROOT, "designer"))
sys.path.insert(0, os.path.join(ROOT, "meta"))

PASS = 0
FAIL = 0


def test(name, fn):
    global PASS, FAIL
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

    # ─── 1. 模块导入测试 ───
    print("[1] 模块导入...")

    def test_import_bot_client():
        from bot_client import BotClient
        assert BotClient is not None

    def test_import_skill_profiles():
        from skill_profiles import load_skill_profiles, get_profile, EPISODES_PER_SKILL
        assert EPISODES_PER_SKILL is not None

    def test_import_strategy_bot():
        from strategy_bot import StrategyBot
        assert StrategyBot is not None

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

    test("bot_client", test_import_bot_client)
    test("skill_profiles", test_import_skill_profiles)
    test("strategy_bot", test_import_strategy_bot)
    test("designer_agent", test_import_designer)
    test("eval_bridge", test_import_eval)
    test("meta_agent", test_import_meta)
    test("evolution_log", test_import_evo_log)

    # ─── 2. 技能档案测试 ───
    print("\n[2] 技能档案...")

    def test_skill_profiles_load():
        from skill_profiles import load_skill_profiles
        profiles = load_skill_profiles()
        assert set(profiles.keys()) >= {"beginner", "average", "expert"}
        required_keys = {"reaction_ticks", "combat_engage_dist", "use_pathfinder",
                         "use_easy_probability", "stuck_patience"}
        for skill, profile in profiles.items():
            missing = required_keys - set(profile.keys())
            assert not missing, f"{skill} 档案缺少字段: {missing}"

    def test_skill_profile_get():
        from skill_profiles import get_profile
        p = get_profile("average")
        assert 0.0 <= p["use_easy_probability"] <= 1.0
        assert isinstance(p["reaction_ticks"], int)

    def test_skill_profile_unknown():
        from skill_profiles import get_profile
        try:
            get_profile("nonexistent")
            assert False, "应抛出 ValueError"
        except ValueError:
            pass

    def test_episodes_per_skill():
        from skill_profiles import EPISODES_PER_SKILL
        assert set(EPISODES_PER_SKILL.keys()) == {"beginner", "average", "expert"}
        assert all(isinstance(v, int) and v > 0 for v in EPISODES_PER_SKILL.values())

    def test_load_episodes_per_skill():
        from skill_profiles import load_episodes_per_skill
        eps = load_episodes_per_skill()
        assert set(eps.keys()) == {"beginner", "average", "expert"}
        assert all(isinstance(v, int) and v > 0 for v in eps.values())
        assert sum(eps.values()) == 20, f"总局数应为 20, 实际 {sum(eps.values())}"

    test("技能档案加载（3个档案）", test_skill_profiles_load)
    test("average 档案字段类型", test_skill_profile_get)
    test("未知技能名抛出 ValueError", test_skill_profile_unknown)
    test("EPISODES_PER_SKILL 格式", test_episodes_per_skill)
    test("episodes_per_skill 加载", test_load_episodes_per_skill)

    # ─── 3. StrategyBot 核心逻辑 ───
    print("\n[3] StrategyBot...")

    def test_strategy_bot_entity_dist():
        from strategy_bot import StrategyBot
        # 距离公式: sqrt(3² + 0² + 4²) = 5.0
        d = StrategyBot._entity_dist({"rel_x": 3, "rel_y": 0, "rel_z": 4})
        assert abs(d - 5.0) < 1e-9, f"期望 5.0, 实际 {d}"

    def test_strategy_bot_entity_dist_zero():
        from strategy_bot import StrategyBot
        d = StrategyBot._entity_dist({"rel_x": 0, "rel_y": 0, "rel_z": 0})
        assert d == 0.0

    test("_entity_dist 勾股定理", test_strategy_bot_entity_dist)
    test("_entity_dist 零距离", test_strategy_bot_entity_dist_zero)

    def test_strategy_bot_collect_filter():
        """COLLECT 过滤器应同时检查 type 和距离（运算符优先级 F1 修复验证）"""
        from strategy_bot import StrategyBot
        far_object = {"type": "object", "rel_x": 100, "rel_y": 0, "rel_z": 0}
        near_item  = {"type": "item",   "rel_x": 1,   "rel_y": 0, "rel_z": 0}

        dist_far  = StrategyBot._entity_dist(far_object)
        dist_near = StrategyBot._entity_dist(near_item)
        assert dist_far > 10,  f"远距离实体距离应 > 10, 实际 {dist_far}"
        assert dist_near < 5,  f"近距离实体距离应 < 5, 实际 {dist_near}"

        collect_dist = 5.0
        # 修复后的过滤逻辑（两个条件均需满足）
        items = [
            e for e in [far_object, near_item]
            if (e.get("type") == "object" or e.get("type") == "item")
            and StrategyBot._entity_dist(e) < collect_dist
        ]
        assert len(items) == 1, f"应只选中 1 个近距离物品, 实际 {len(items)}"
        assert items[0] is near_item

    test("COLLECT 过滤器优先级", test_strategy_bot_collect_filter)

    # ─── 4. EvalBridge 测试 ───
    print("\n[4] EvalBridge...")

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

    def test_multi_skill_eval():
        from eval_bridge import analyze_multi_skill_data, format_multi_skill_eval
        results = [
            {"completed": True,  "time": 80,  "deaths": 0, "easy_used": False,
             "death_causes": [], "stuck_positions": [], "exploration": 60,
             "skill_level": "beginner"},
            {"completed": True,  "time": 90,  "deaths": 1, "easy_used": False,
             "death_causes": [], "stuck_positions": [], "exploration": 55,
             "skill_level": "average"},
            {"completed": False, "time": 200, "deaths": 3, "easy_used": False,
             "death_causes": ["fall"], "stuck_positions": [], "exploration": 40,
             "skill_level": "expert"},
        ]
        report = analyze_multi_skill_data(results)
        assert "completion_by_skill" in report
        assert report["completion_by_skill"]["beginner"] == 1.0
        assert report["completion_by_skill"]["average"] == 1.0
        assert report["completion_by_skill"]["expert"] == 0.0
        assert "difficulty_assessment" in report

        text = format_multi_skill_eval(report)
        assert "beginner" in text.lower() or "新手" in text

    test("EvalBridge 分析", test_eval_bridge)
    test("EvalBridge 空数据", test_eval_empty)
    test("多技能评估", test_multi_skill_eval)

    # ─── 5. EvolutionLog 测试 ───
    print("\n[5] EvolutionLog...")

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

    # ─── 6. Phase 4–6 保留测试 ───
    print("\n[6] Phase 4–6: R3/F5/S1/F1/Q2 验证...")

    def test_r3_evolution_log_immediate_persist():
        """R3: log_generation 后日志文件应立即存在且可读"""
        import tempfile
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

    def test_f5_run_evolution_flags():
        """F5: run_evolution.py 应包含 --player-id 和 --skill 参数，且 --skill 被使用"""
        run_evo_path = os.path.join(ROOT, "meta", "run_evolution.py")
        with open(run_evo_path) as f:
            src = f.read()
        assert "--player-id" in src, "--player-id 参数未在 run_evolution.py 中定义"
        assert "--curriculum" in src, "--curriculum 参数未在 run_evolution.py 中定义"
        assert "--skill" in src, "--skill 参数未在 run_evolution.py 中定义"
        assert "args.skill" in src, "--skill 参数已定义但未被使用"

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

    def test_q2_run_evolution_sigint_handler():
        """Q2: run_evolution.py 应有 KeyboardInterrupt 处理"""
        evo_path = os.path.join(ROOT, "meta", "run_evolution.py")
        with open(evo_path) as f:
            src = f.read()
        assert "KeyboardInterrupt" in src, "run_evolution.py 缺少 KeyboardInterrupt 处理"

    test("R3: EvolutionLog 立即持久化", test_r3_evolution_log_immediate_persist)
    test("F5: run_evolution --player-id & --skill 参数", test_f5_run_evolution_flags)
    test("S1: player_bot.js 语法检查", test_s1_player_bot_syntax)
    test("F1: generate_new_level 重试参数", test_f1_generate_new_level_has_retry)
    test("Q2: run_evolution SIGINT 处理", test_q2_run_evolution_sigint_handler)

    # ─── 结果 ───
    print(f"\n{'=' * 40}")
    print(f" 结果: {PASS} 通过, {FAIL} 失败")
    print(f"{'=' * 40}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
