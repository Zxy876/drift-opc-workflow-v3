"""
进化过程可视化 — 读取 evolution_logs 生成图表

用法: python meta/visualize_evolution.py [--run RUN_ID]
"""

import argparse
import json
import os
import glob


def load_latest_log(log_dir: str = None, run_id: str = None) -> dict:
    """加载最新的进化日志"""
    if log_dir is None:
        log_dir = os.path.join(os.path.dirname(__file__), "..", "evolution_logs")

    if run_id:
        path = os.path.join(log_dir, f"{run_id}_full.json")
    else:
        files = sorted(glob.glob(os.path.join(log_dir, "*_full.json")))
        if not files:
            print("没有找到进化日志文件")
            return {}
        path = files[-1]

    with open(path, "r") as f:
        return json.load(f)


def print_evolution_table(data: dict):
    """打印进化过程表格"""
    if not data or "generations" not in data:
        print("没有进化数据")
        return

    print(f"\n{'=' * 80}")
    print(f" 进化日志: {data.get('run_id', '?')}")
    print(f"{'=' * 80}")
    print(f"{'Gen':>4} | {'通关率':>8} | {'平均时间':>8} | {'平均死亡':>8} | {'Easy%':>8} | {'探索度':>8} | Flow Zone")
    print(f"{'-' * 4}-+-{'-' * 8}-+-{'-' * 8}-+-{'-' * 8}-+-{'-' * 8}-+-{'-' * 8}-+-{'-' * 10}")

    for entry in data["generations"]:
        gen = entry["generation"]
        ev = entry["eval"]
        cr = ev["completion_rate"]
        in_fz = "  YES" if 0.6 <= cr <= 0.8 else "  no"
        print(f"{gen:>4} | {cr:>7.0%} | {ev['avg_time']:>7.0f}s | {ev['avg_deaths']:>8.1f} | {ev['easy_usage_rate']:>7.0%} | {ev['avg_exploration']:>8.0f} | {in_fz}")

    summary = data.get("summary", {})
    print(f"\n最终通关率: {summary.get('final_completion_rate', 0):.0%}")
    print(f"是否达到 Flow Zone: {summary.get('in_flow_zone', False)}")
    print(f"总代数: {summary.get('total_generations', 0)}")


def plot_evolution(data: dict):
    """绘制进化曲线（如果 matplotlib 可用）"""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("[可视化] matplotlib 未安装，跳过图表生成")
        return

    if not data or "generations" not in data:
        return

    gens = [e["generation"] for e in data["generations"]]
    crs = [e["eval"]["completion_rate"] for e in data["generations"]]
    deaths = [e["eval"]["avg_deaths"] for e in data["generations"]]
    times = [e["eval"]["avg_time"] for e in data["generations"]]

    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)

    # 通关率
    axes[0].plot(gens, crs, 'b-o', label='通关率')
    axes[0].axhspan(0.6, 0.8, alpha=0.2, color='green', label='Flow Zone')
    axes[0].set_ylabel('通关率')
    axes[0].legend()
    axes[0].set_ylim(0, 1)
    axes[0].set_title(f"进化过程 — {data.get('run_id', '')}")

    # 平均死亡
    axes[1].plot(gens, deaths, 'r-s', label='平均死亡')
    axes[1].set_ylabel('平均死亡次数')
    axes[1].legend()

    # 平均时间
    axes[2].plot(gens, times, 'g-^', label='平均完成时间')
    axes[2].set_ylabel('平均时间 (s)')
    axes[2].set_xlabel('Generation')
    axes[2].legend()

    plt.tight_layout()
    save_path = os.path.join(
        os.path.dirname(__file__), "..", "evolution_logs",
        f"{data.get('run_id', 'latest')}_plot.png"
    )
    plt.savefig(save_path, dpi=150)
    print(f"[可视化] 图表已保存: {save_path}")
    plt.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=str, default=None, help="指定 run_id")
    args = parser.parse_args()

    data = load_latest_log(run_id=args.run)
    print_evolution_table(data)
    plot_evolution(data)
