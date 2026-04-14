"""
进化日志记录器 — 记录每代进化的数据，支持导出分析
"""

import json
import os
import time
from typing import Any, Optional


class EvolutionLog:
    """进化过程日志"""

    def __init__(self, log_dir: str = "evolution_logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.run_id = f"run_{int(time.time())}"
        self.entries: list = []

    def log_generation(
        self,
        generation: int,
        design: str,
        eval_report: dict,
        new_design: Optional[dict] = None,
        publish_result: Optional[dict] = None,
    ):
        """记录一代进化数据"""
        entry = {
            "generation": generation,
            "timestamp": time.time(),
            "design": design,
            "eval": eval_report,
            "new_design": new_design,
            "publish": publish_result,
        }
        self.entries.append(entry)

        # 实时写入文件
        log_file = os.path.join(self.log_dir, f"{self.run_id}.jsonl")
        with open(log_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def get_summary(self) -> dict:
        """获取进化过程摘要"""
        if not self.entries:
            return {"total_generations": 0}

        completion_rates = [e["eval"]["completion_rate"] for e in self.entries]

        return {
            "run_id": self.run_id,
            "total_generations": len(self.entries),
            "completion_rate_trend": completion_rates,
            "best_completion_rate": max(completion_rates),
            "worst_completion_rate": min(completion_rates),
            "final_completion_rate": completion_rates[-1],
            "in_flow_zone": 0.6 <= completion_rates[-1] <= 0.8,
        }

    def export_json(self, path: Optional[str] = None) -> str:
        """导出完整日志为 JSON"""
        if path is None:
            path = os.path.join(self.log_dir, f"{self.run_id}_full.json")

        data = {
            "run_id": self.run_id,
            "summary": self.get_summary(),
            "generations": self.entries,
        }

        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        return path
