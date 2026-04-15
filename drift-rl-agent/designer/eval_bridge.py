"""
评估桥接层 — 将 PlayerAgent 的游玩数据转换为 DesignerAgent 的评估报告
"""

from typing import Any


def analyze_play_data(play_results: list) -> dict:
    """
    分析 PlayerAgent 的游玩数据，产出评估报告

    Args:
        play_results: 每局的结果列表
            [{"completed": bool, "time": float, "deaths": int, "easy_used": bool,
              "death_causes": list, "stuck_positions": list, "exploration": int}, ...]

    Returns:
        评估报告 dict
    """
    if not play_results:
        return {
            "completion_rate": 0.0,
            "avg_time": 0.0,
            "avg_deaths": 0.0,
            "easy_usage_rate": 0.0,
            "death_causes": {},
            "stuck_points": {},
            "total_episodes": 0,
            "avg_exploration": 0.0,
        }

    # BUG-C: 过滤掉 bot_not_ready 等无效局（避免假数据干扰通关率）
    valid = [r for r in play_results if not r.get("error")]
    if not valid:
        return {
            "completion_rate": 0.0, "avg_time": 0.0, "avg_deaths": 0.0,
            "easy_usage_rate": 0.0, "death_causes": {}, "stuck_points": {},
            "total_episodes": len(play_results), "avg_exploration": 0.0,
            "note": "all_episodes_invalid",
        }
    play_results = valid
    total = len(play_results)
    completed = sum(1 for r in play_results if r.get("completed", False))

    # 聚合死因
    death_causes: dict = {}
    for r in play_results:
        for cause in r.get("death_causes", []):
            death_causes[cause] = death_causes.get(cause, 0) + 1
    death_causes = dict(sorted(death_causes.items(), key=lambda x: -x[1])[:5])

    # 聚合卡点
    stuck_points: dict = {}
    for r in play_results:
        for point in r.get("stuck_positions", []):
            if isinstance(point, (list, tuple)) and len(point) >= 3:
                key = f"({point[0]:.0f},{point[1]:.0f},{point[2]:.0f})"
            else:
                key = str(point)
            stuck_points[key] = stuck_points.get(key, 0) + 1
    stuck_points = dict(sorted(stuck_points.items(), key=lambda x: -x[1])[:3])

    return {
        "completion_rate": completed / total,
        "avg_time": sum(r.get("time", 0) for r in play_results) / total,
        "avg_deaths": sum(r.get("deaths", 0) for r in play_results) / total,
        "easy_usage_rate": sum(1 for r in play_results if r.get("easy_used", False)) / total,
        "death_causes": death_causes,
        "stuck_points": stuck_points,
        "total_episodes": total,
        "avg_exploration": sum(r.get("exploration", 0) for r in play_results) / total,
    }


def format_eval_for_llm(eval_report: dict) -> str:
    """将评估报告格式化为 LLM 友好的文本"""
    lines = [
        f"通关率: {eval_report['completion_rate']:.0%} ({eval_report['total_episodes']} 局)",
        f"平均完成时间: {eval_report['avg_time']:.0f}s",
        f"平均死亡次数: {eval_report['avg_deaths']:.1f}",
        f"/easy 使用率: {eval_report['easy_usage_rate']:.0%}",
        f"平均探索度: {eval_report['avg_exploration']:.0f} 格",
    ]

    if eval_report["death_causes"]:
        causes = ", ".join(f"{k}({v}次)" for k, v in eval_report["death_causes"].items())
        lines.append(f"主要死因: {causes}")

    if eval_report["stuck_points"]:
        points = ", ".join(f"{k}({v}次)" for k, v in eval_report["stuck_points"].items())
        lines.append(f"卡点位置: {points}")

    return "\n".join(lines)


def analyze_multi_skill_data(play_results: list) -> dict:
    """
    分析多技能级别的游玩数据，产出增强版评估报告

    Args:
        play_results: 每局结果列表（每条需包含 "skill_level" 字段）

    Returns:
        增强版评估报告（向后兼容 analyze_play_data 的所有字段）
    """
    # 基础报告（兼容旧格式）
    base_report = analyze_play_data(play_results)

    # 按技能级别分组
    by_skill: dict[str, list] = {}
    for r in play_results:
        skill = r.get("skill_level", "average")
        by_skill.setdefault(skill, []).append(r)

    # 各技能级别的子报告
    completion_by_skill = {}
    avg_time_by_skill = {}
    for skill, results in by_skill.items():
        sub = analyze_play_data(results)
        completion_by_skill[skill] = sub["completion_rate"]
        avg_time_by_skill[skill] = sub["avg_time"]

    # 难度评估
    avg_cr = completion_by_skill.get("average", base_report["completion_rate"])
    if avg_cr < 0.4:
        assessment = "too_hard"
    elif avg_cr < 0.6:
        assessment = "slightly_hard"
    elif avg_cr <= 0.8:
        assessment = "balanced"
    elif avg_cr <= 0.95:
        assessment = "slightly_easy"
    else:
        assessment = "too_easy"

    base_report.update({
        "completion_by_skill": completion_by_skill,
        "avg_time_by_skill": avg_time_by_skill,
        "primary_skill_used": "average",
        "difficulty_assessment": assessment,
    })

    return base_report


def format_multi_skill_eval(eval_report: dict) -> str:
    """将多技能评估报告格式化为 LLM 友好的文本"""
    lines = [format_eval_for_llm(eval_report)]

    cbs = eval_report.get("completion_by_skill", {})
    if cbs:
        lines.append("\n分技能通关率:")
        for skill, cr in cbs.items():
            lines.append(f"  {skill}: {cr:.0%}")

    assessment = eval_report.get("difficulty_assessment", "")
    if assessment:
        labels = {
            "too_hard": "过难",
            "slightly_hard": "偏难",
            "balanced": "平衡",
            "slightly_easy": "偏简单",
            "too_easy": "过简单",
        }
        lines.append(f"难度评估: {labels.get(assessment, assessment)}")

    return "\n".join(lines)
