"""
批量生成 D1-D5 全套关卡并发布到 Drift

用法:
  python designer/batch_generate.py --prefix my_world --publish quick
  python designer/batch_generate.py --prefix my_world --publish premium
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from designer_agent import DesignerAgent


THEMES = {
    1: ("宁静村庄", "新手教学"),
    2: ("迷雾森林", "探索收集"),
    3: ("浮空群岛", "解谜冒险"),
    4: ("地下矿洞", "生存挑战"),
    5: ("深海神殿", "Boss 战"),
}


def batch_generate(args):
    designer = DesignerAgent(
        drift_url=args.drift_url,
        asyncaiflow_url=args.async_url,
        llm_model=args.model,
    )

    results = []
    for d in range(1, 6):
        theme, level_type = THEMES[d]
        level_id = f"{args.prefix}_d{d}"

        print(f"\n{'─' * 50}")
        print(f" D{d}: {theme} ({level_type})")
        print(f" Level ID: {level_id}")
        print(f"{'─' * 50}")

        # 生成设计
        design = designer.generate_new_level(
            target_difficulty=d,
            theme=theme,
            level_type=level_type,
        )
        print(f"[设计] {design.get('title', '?')}")
        print(f"[设计] {design.get('design_text', '')[:100]}...")

        # 发布
        if args.publish != "none":
            use_premium = (args.publish == "premium")
            pub_result = designer.publish_to_drift(
                design, level_id, args.player,
                use_premium=use_premium,
            )
            print(f"[发布] {pub_result.get('method', '?')} — {pub_result.get('result', {}).get('status', 'ok')}")
        else:
            pub_result = {"method": "dry-run"}

        results.append({
            "difficulty": d,
            "level_id": level_id,
            "design": design,
            "publish": pub_result,
        })

        # 避免 API 限速
        time.sleep(2)

    # 保存结果
    output_path = os.path.join(
        os.path.dirname(__file__), "..", "evolution_logs",
        f"batch_{args.prefix}_{int(time.time())}.json"
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n[完成] D1-D5 全套关卡已生成")
    print(f"[完成] 结果保存: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", type=str, default="rl_gen", help="关卡 ID 前缀")
    parser.add_argument("--publish", choices=["quick", "premium", "none"], default="none")
    parser.add_argument("--drift-url", type=str, default=os.environ.get("DRIFT_URL", "http://localhost:8000"))
    parser.add_argument("--async-url", type=str, default=os.environ.get("ASYNCAIFLOW_URL", "http://localhost:8080"))
    parser.add_argument("--player", type=str, default="DriftRLAgent")
    parser.add_argument("--model", type=str, default="gpt-4")
    args = parser.parse_args()
    batch_generate(args)
