"""
DesignerAgent — LLM 驱动的关卡设计师

根据 PlayerAgent 的评估数据，使用 LLM 改进关卡设计，
然后通过 Drift 后端 API 发布到 MC 服务器。
"""

import json
import os
import time
from typing import Optional

import requests
import yaml
from openai import OpenAI

from design_prompts import LEVEL_IMPROVEMENT_PROMPT, NEW_LEVEL_PROMPT
from eval_bridge import analyze_play_data


class DesignerAgent:
    """LLM 驱动的关卡设计师"""

    def __init__(
        self,
        drift_url: str = "http://35.201.132.58:8000",
        asyncaiflow_url: str = "http://35.201.132.58:8080",
        llm_model: str = "gpt-4",
        config_path: Optional[str] = None,
    ):
        self.drift_url = drift_url.rstrip("/")
        self.async_url = asyncaiflow_url.rstrip("/")
        self.llm_model = llm_model
        self.llm = OpenAI()  # 从环境变量 OPENAI_API_KEY 读取
        self.design_history: list = []

        # 加载配置
        self._load_config(config_path)

    def _load_config(self, config_path: Optional[str]):
        """加载进化参数配置"""
        self.use_premium_threshold = 3
        self.workflow_poll_interval = 5
        self.workflow_timeout = 300

        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__), "..", "configs", "evolution_params.yaml"
            )

        try:
            with open(config_path, "r") as f:
                raw = yaml.safe_load(f) or {}
            designer_cfg = raw.get("designer", {})
            self.use_premium_threshold = designer_cfg.get("use_premium_threshold", 3)
            self.workflow_poll_interval = designer_cfg.get("workflow_poll_interval", 5)
            self.workflow_timeout = designer_cfg.get("workflow_timeout", 300)
        except FileNotFoundError:
            pass

    def generate_improved_design(
        self,
        current_design: str,
        eval_report: dict,
        target_difficulty: int,
    ) -> dict:
        """
        用 LLM 根据评估报告生成改进后的关卡设计

        Returns: {"design_text": str, "difficulty": int, "reasoning": str, "changes": list}
        """
        direction = "低" if eval_report["completion_rate"] < 0.6 else "高"

        prompt = LEVEL_IMPROVEMENT_PROMPT.format(
            current_design=current_design,
            total_episodes=eval_report["total_episodes"],
            completion_rate=eval_report["completion_rate"],
            avg_time=eval_report["avg_time"],
            avg_deaths=eval_report["avg_deaths"],
            easy_usage_rate=eval_report["easy_usage_rate"],
            death_causes=json.dumps(eval_report["death_causes"], ensure_ascii=False),
            stuck_points=json.dumps(eval_report["stuck_points"], ensure_ascii=False),
            target_difficulty=target_difficulty,
            direction=direction,
        )

        resp = self.llm.chat.completions.create(
            model=self.llm_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.7,
        )

        result = json.loads(resp.choices[0].message.content)

        # 记录设计历史
        self.design_history.append({
            "eval_report": eval_report,
            "old_design": current_design,
            "new_design": result,
            "timestamp": time.time(),
        })

        return result

    def generate_new_level(
        self,
        target_difficulty: int = 3,
        theme: str = "森林冒险",
        level_type: str = "探索收集",
    ) -> dict:
        """生成全新关卡设计"""
        prompt = NEW_LEVEL_PROMPT.format(
            target_difficulty=target_difficulty,
            theme=theme,
            level_type=level_type,
        )

        resp = self.llm.chat.completions.create(
            model=self.llm_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.8,
        )

        return json.loads(resp.choices[0].message.content)

    def publish_to_drift(
        self,
        design: dict,
        level_id: str,
        player_id: str,
        use_premium: bool = False,
    ) -> dict:
        """
        发布设计到 Drift 系统

        Quick Publish: POST /story/inject（直接注入）
        Premium Publish: POST /planner/execute（走 AsyncAIFlow 全链路）
        """
        text = design["design_text"]
        difficulty = design.get("difficulty", 3)
        title = design.get("title", f"AI-Designed Level D{difficulty}")

        if use_premium and difficulty >= self.use_premium_threshold:
            return self._publish_premium(text, level_id, player_id, difficulty)
        else:
            return self._publish_quick(text, level_id, player_id, title)

    def _publish_quick(self, text: str, level_id: str, player_id: str, title: str) -> dict:
        """Quick Publish: POST /story/inject"""
        resp = requests.post(
            f"{self.drift_url}/story/inject",
            json={
                "level_id": level_id,
                "title": title,
                "text": text,
                "player_id": player_id,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return {"method": "quick", "result": resp.json()}

    def _publish_premium(self, text: str, level_id: str, player_id: str, difficulty: int) -> dict:
        """Premium Publish: POST /planner/execute → 轮询工作流"""
        resp = requests.post(
            f"{self.async_url}/planner/execute",
            json={
                "issue": text,
                "repo_context": "drift_experience" if difficulty >= 5 else "drift-system",
                "difficulty": difficulty,
                "player_id": player_id,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        workflow_id = (
            data.get("data", {}).get("workflowId")
            or data.get("workflowId")
            or data.get("data", {}).get("workflow_id")
        )

        if not workflow_id:
            return {"method": "premium", "status": "error", "msg": "未获取到 workflowId"}

        # 轮询等待工作流完成
        result = self._wait_for_workflow(workflow_id)
        return {"method": "premium", "workflow_id": workflow_id, "result": result}

    def _wait_for_workflow(self, workflow_id) -> dict:
        """轮询 AsyncAIFlow 工作流状态"""
        start = time.time()
        while time.time() - start < self.workflow_timeout:
            try:
                resp = requests.get(
                    f"{self.async_url}/workflows/{workflow_id}",
                    timeout=10,
                )
                data = resp.json().get("data", resp.json())
                status = (data.get("status") or "").upper()

                if status in ("COMPLETED", "SUCCEEDED"):
                    return {"status": "completed", "data": data}
                if status == "FAILED":
                    return {"status": "failed", "data": data}

            except requests.RequestException:
                pass

            time.sleep(self.workflow_poll_interval)

        return {"status": "timeout"}

    def get_existing_levels(self) -> list:
        """获取 Drift 系统中已有的关卡列表"""
        try:
            resp = requests.get(f"{self.drift_url}/story/levels", timeout=10)
            data = resp.json()
            return data.get("levels", [])
        except Exception:
            return []
