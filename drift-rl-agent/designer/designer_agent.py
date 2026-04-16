"""
DesignerAgent — LLM 驱动的关卡设计师

根据 PlayerAgent 的评估数据，使用 LLM 改进关卡设计，
然后通过 Drift 后端 API 发布到 MC 服务器。
"""

import json
import os
import re
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
        llm_model: str = "glm-4",
        config_path: Optional[str] = None,
    ):
        self.drift_url = drift_url.rstrip("/")
        self.async_url = asyncaiflow_url.rstrip("/")
        self.llm_model = llm_model
        self.llm = self._build_llm_client()
        self.design_history: list = []

        # 加载配置
        self._load_config(config_path)

    def _build_llm_client(self) -> OpenAI:
        """构建 LLM 客户端，优先使用 GLM_API_KEY（智谱），其次 OPENAI_API_KEY"""
        glm_key = os.environ.get("GLM_API_KEY")
        if glm_key:
            return OpenAI(
                api_key=glm_key,
                base_url="https://open.bigmodel.cn/api/paas/v4/",
            )
        # 回退到 OpenAI（从环境变量 OPENAI_API_KEY 读取）
        return OpenAI()

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
        max_retries: int = 3,
    ) -> dict:
        """
        用 LLM 根据评估报告生成改进后的关卡设计

        优先使用确定性规则引擎（接近 Flow Zone 时），否则调 LLM。
        Returns: {"design_text": str, "difficulty": int, "reasoning": str, "changes": list}
        """
        hints = eval_report.get("adjustment_hints", [])
        avg_cr = eval_report.get("completion_rate", 0.0)
        fine_tune_mode = 0.5 <= avg_cr <= 0.9

        if fine_tune_mode and hints:
            adjusted = self._apply_deterministic_adjustments(current_design, hints, target_difficulty)
            if adjusted:
                print(f"[Designer] 使用确定性调整（CR={avg_cr:.0%}）: {adjusted['changes']}")
                self.design_history.append({
                    "eval_report": eval_report,
                    "old_design": current_design,
                    "new_design": adjusted,
                    "timestamp": time.time(),
                })
                return adjusted

        direction = "低" if avg_cr < 0.6 else "高"

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
            beginner_cr=eval_report.get("completion_by_skill", {}).get("beginner", eval_report["completion_rate"]),
            average_cr=eval_report.get("completion_by_skill", {}).get("average", eval_report["completion_rate"]),
            expert_cr=eval_report.get("completion_by_skill", {}).get("expert", eval_report["completion_rate"]),
            difficulty_assessment=eval_report.get("difficulty_assessment", "unknown"),
        )

        last_err = None
        for attempt in range(max_retries):
            try:
                resp = self.llm.chat.completions.create(
                    model=self.llm_model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.7,
                )
                result = json.loads(resp.choices[0].message.content)
                if "design_text" not in result or not result["design_text"]:
                    raise ValueError("LLM 响应缺少 design_text 字段")

                # 记录设计历史
                self.design_history.append({
                    "eval_report": eval_report,
                    "old_design": current_design,
                    "new_design": result,
                    "timestamp": time.time(),
                })
                return result

            except Exception as e:
                last_err = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(f"[Designer] LLM 调用失败 (尝试 {attempt + 1}/{max_retries}), {wait}s 后重试: {e}")
                    time.sleep(wait)

        print(f"[Designer] LLM 调用全部失败，使用降级设计: {last_err}")
        return {
            "design_text": current_design,
            "difficulty": target_difficulty,
            "reasoning": "LLM 调用失败，保留原设计",
            "changes": [],
        }

    def _apply_deterministic_adjustments(self, design_text: str, hints: list, difficulty: int) -> dict | None:
        """确定性调整：根据规则引擎的提示直接修改设计文本中的数值，不调 LLM"""
        new_text = design_text
        changes = []

        for hint in hints:
            if hint == "REDUCE_MOB_COUNT":
                def halve_mob(m):
                    n = max(1, int(m.group(1)) // 2)
                    return f"{n} 个 {m.group(2)}"
                new_text = re.sub(
                    r"(\d+)\s*个\s*(怪物|僵尸|骷髅|蜘蛛|苦力怕)",
                    halve_mob, new_text,
                )
                changes.append("怪物数量减半")

            elif hint == "REDUCE_FALL_HAZARDS":
                if "浮空" in new_text and "安全" not in new_text:
                    new_text = new_text.replace("浮空", "低矮浮空（有安全网）")
                    changes.append("降低坠落风险")

            elif hint == "ADD_SUPPLIES":
                if "生命药水" not in new_text:
                    new_text += "\nNPC 补给兵在起点 赠送 3 个 生命药水"
                    changes.append("增加补给NPC")

            elif hint == "INCREASE_DIFFICULTY_SMALL":
                def tighten_time(m):
                    n = max(30, int(int(m.group(1)) * 0.8))
                    return f"在 {n} 秒内"
                new_text = re.sub(r"在\s*(\d+)\s*秒内", tighten_time, new_text)
                changes.append("时间限制收紧20%")

        if changes:
            return {
                "design_text": new_text,
                "difficulty": difficulty,
                "reasoning": f"确定性微调: {', '.join(changes)}",
                "changes": changes,
            }
        return None

    def generate_new_level(
        self,
        target_difficulty: int = 3,
        theme: str = "森林冒险",
        level_type: str = "探索收集",
        max_retries: int = 3,
    ) -> dict:
        """生成全新关卡设计（带重试和降级）"""
        prompt = NEW_LEVEL_PROMPT.format(
            target_difficulty=target_difficulty,
            theme=theme,
            level_type=level_type,
        )

        last_err = None
        for attempt in range(max_retries):
            try:
                resp = self.llm.chat.completions.create(
                    model=self.llm_model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.8,
                )
                result = json.loads(resp.choices[0].message.content)
                if "design_text" not in result or not result["design_text"]:
                    raise ValueError("LLM 响应缺少 design_text 字段")
                return result
            except Exception as e:
                last_err = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(f"[Designer] 新关卡生成失败 (尝试 {attempt + 1}/{max_retries}), {wait}s 后重试: {e}")
                    time.sleep(wait)

        print(f"[Designer] 新关卡生成全部失败，使用默认设计: {last_err}")
        return {
            "design_text": f"在{theme}中进行{level_type}冒险。难度 D{target_difficulty}。",
            "difficulty": target_difficulty,
            "reasoning": "LLM 调用失败，使用默认设计",
            "changes": [],
        }

    def publish_to_drift(
        self,
        design: dict,
        level_id: str,
        player_id: str,
        use_premium: bool = False,
    ) -> dict:
        """
        发布设计到 Drift 系统

        默认使用 Quick Publish，Premium 失败时自动降级。
        """
        text = design["design_text"]
        difficulty = design.get("difficulty", 3)
        title = design.get("title", f"AI-Designed Level D{difficulty}")

        if use_premium and difficulty >= self.use_premium_threshold:
            try:
                return self._publish_premium(text, level_id, player_id, difficulty)
            except Exception as e:
                print(f"[Designer] Premium 发布失败，降级到 Quick: {e}")
                return self._publish_quick(text, level_id, player_id, title)
        else:
            return self._publish_quick(text, level_id, player_id, title)

    def _publish_quick(self, text: str, level_id: str, player_id: str, title: str) -> dict:
        """Quick Publish: POST /story/inject（带 3 次重试）"""
        last_err = None
        for attempt in range(3):
            try:
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
                result = resp.json()

                # 验证关卡是否已成功加入 Drift（E2）
                verified = False
                try:
                    time.sleep(2)
                    levels = self.get_existing_levels()
                    verified = any(
                        lv.get("level_id") == level_id or lv.get("id") == level_id
                        for lv in levels
                    )
                except Exception:
                    pass

                return {"method": "quick", "result": result, "verified": verified}

            except requests.RequestException as e:
                last_err = e
                if attempt < 2:
                    print(f"[Designer] Quick Publish 失败 (尝试 {attempt + 1}/3), 3s 后重试: {e}")
                    time.sleep(3)

        raise requests.RequestException(f"Quick Publish 3 次均失败: {last_err}")

    def _publish_premium(self, text: str, level_id: str, player_id: str, difficulty: int) -> dict:
        """Premium Publish: POST /planner/execute → 轮询工作流（带 3 次重试）"""
        last_err = None
        for attempt in range(3):
            try:
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

            except requests.RequestException as e:
                last_err = e
                if attempt < 2:
                    print(f"[Designer] Premium Publish 失败 (尝试 {attempt + 1}/3), 5s 后重试: {e}")
                    time.sleep(5)

        raise requests.RequestException(f"Premium Publish 3 次均失败: {last_err}")

    def _wait_for_workflow(self, workflow_id) -> dict:
        """轮询 AsyncAIFlow 工作流状态"""
        start = time.time()
        poll_count = 0
        print(f"[Designer] 开始轮询工作流 {workflow_id} (超时={self.workflow_timeout}s)")
        while time.time() - start < self.workflow_timeout:
            poll_count += 1
            try:
                resp = requests.get(
                    f"{self.async_url}/workflows/{workflow_id}",
                    timeout=10,
                )
                data = resp.json().get("data", resp.json())
                status = (data.get("status") or "").upper()
                elapsed = int(time.time() - start)

                if status in ("COMPLETED", "SUCCEEDED"):
                    print(f"[Designer] 工作流完成 ({elapsed}s, {poll_count} 次轮询)")
                    return {"status": "completed", "data": data}
                if status == "FAILED":
                    print(f"[Designer] 工作流失败 ({elapsed}s)")
                    return {"status": "failed", "data": data}

                # 每 30 秒输出一次进度
                if poll_count % 6 == 0:
                    current_step = data.get("currentStep") or data.get("current_step") or "?"
                    print(f"[Designer] 工作流进行中... status={status}, step={current_step}, elapsed={elapsed}s")

            except requests.RequestException as e:
                elapsed = int(time.time() - start)
                if poll_count % 6 == 0:
                    print(f"[Designer] 工作流轮询出错 ({elapsed}s): {e}")

            time.sleep(self.workflow_poll_interval)

        elapsed = int(time.time() - start)
        print(f"[Designer] 工作流超时 ({elapsed}s, {poll_count} 次轮询)")
        return {"status": "timeout"}

    def get_existing_levels(self) -> list:
        """获取 Drift 系统中已有的关卡列表"""
        try:
            resp = requests.get(f"{self.drift_url}/story/levels", timeout=10)
            data = resp.json()
            return data.get("levels", [])
        except Exception:
            return []
