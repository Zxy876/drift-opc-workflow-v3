# evolution_api.py — RL Agent 进化控制 API
#
# 用子进程启动 run_evolution.py，通过共享状态文件实时获取进化进度。
# 轮询方案与面板现有的 AsyncAIFlow 工作流监控保持一致。

import json
import os
import signal
import socket
import subprocess
import threading
import time
import uuid
from typing import Dict

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/evolution", tags=["evolution"])

# 全局进化会话存储（内存，单实例够用）
_sessions: Dict[str, dict] = {}

# drift-rl-agent 相对路径（从本文件向上 5 层到仓库根，再进 drift-rl-agent）
_RL_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "drift-rl-agent")
)
# 兜底：opt backend 路径计算不准时（__file__ 在 /opt），用 HOME 下的标准位置
if not os.path.isdir(_RL_AGENT_DIR):
    _home = os.path.expanduser("~")
    _fallback = os.path.join(_home, "drift-opc-workflow-v3", "drift-rl-agent")
    if os.path.isdir(_fallback):
        _RL_AGENT_DIR = _fallback

# 使用 drift-rl-agent 自己的 venv Python（包含 tianshou/requests/yaml 等依赖）
_VENV_PYTHON = os.path.join(_RL_AGENT_DIR, "venv", "bin", "python3")
if not os.path.exists(_VENV_PYTHON):
    _VENV_PYTHON = "python3"  # fallback


def _check_bot_bridge(port: int = 9999, timeout: float = 1.0) -> bool:
    """检查 Mineflayer Bot TCP Bridge 是否在监听"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError):
        return False


@router.post("/start")
async def start_evolution(body: dict):
    """
    启动一次进化循环

    body:
      design: str         — 初始关卡设计描述（来自面板 Design 文本框）
      level_id: str       — 关卡 ID
      player_id: str      — 玩家 ID
      difficulty: int     — 目标难度 1-5（默认 3）
      episodes: int       — 每代局数（默认 5）
      generations: int    — 最大代数（默认 3）
      premium: bool       — 是否用 Premium Publish（默认 false）
      skill: str|null     — 单一技能模式（beginner/average/expert，默认 null）

    返回:
      session_id: str     — 进化会话 ID（用于后续轮询）
      status: "started"
    """
    # 前置检查：Bot TCP Bridge 必须在运行
    bot_port = int(body.get("bot_port", 9999))
    if not _check_bot_bridge(bot_port):
        raise HTTPException(
            status_code=503,
            detail=f"Mineflayer Bot 未运行（端口 {bot_port} 不可达）。"
                   "请先在服务器运行: node player/player_bot.js"
        )

    # BUG-3 互斥检查：同一时间只允许一个 Evolution 会话
    running = [
        s for s in _sessions.values()
        if s["status"] in ("starting", "running")
        and s.get("process") and s["process"].poll() is None
    ]
    if running:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"已有进化会话正在运行 (#{running[0]['session_id']})。请先停止现有会话再启动新的。",
                "running_session": running[0]["session_id"]
            }
        )

    session_id = str(uuid.uuid4())[:8]
    status_file = f"/tmp/evolution_{session_id}_status.json"

    # 构建命令（使用 drift-rl-agent 自己的 venv python）
    cmd = [
        _VENV_PYTHON, "meta/run_evolution.py",
        "--level",       body.get("level_id", f"panel_evo_{session_id}"),
        "--difficulty",  str(body.get("difficulty", 3)),
        "--episodes",    str(body.get("episodes", 5)),
        "--generations", str(body.get("generations", 3)),
        "--player-id",   body.get("player_id", "DriftRLAgent"),
        "--status-file", status_file,
    ]
    if body.get("design"):
        cmd += ["--design", body["design"]]
    if body.get("premium"):
        cmd += ["--premium"]
    if body.get("skill"):
        cmd += ["--skill", body["skill"]]
    if body.get("max_steps"):
        cmd += ["--max-steps", str(int(body["max_steps"]))]

    # 初始化会话记录
    _sessions[session_id] = {
        "session_id": session_id,
        "status": "starting",
        "status_file": status_file,
        "process": None,
        "started_at": time.time(),
        "params": {
            "design":      body.get("design", ""),
            "level_id":    body.get("level_id", ""),
            "difficulty":  body.get("difficulty", 3),
            "episodes":    body.get("episodes", 5),
            "generations": body.get("generations", 3),
        },
    }

    # 写初始状态文件
    with open(status_file, "w") as f:
        json.dump({"status": "starting", "generation": -1}, f)

    # 后台线程启动子进程
    def _run():
        try:
            env = os.environ.copy()  # 继承 GLM_API_KEY 等环境变量
            proc = subprocess.Popen(
                cmd,
                cwd=_RL_AGENT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            _sessions[session_id]["process"] = proc
            _sessions[session_id]["status"] = "running"
            _sessions[session_id]["pid"] = proc.pid

            # 收集输出（最后 200 行）并实时打印到日志
            import logging
            logger = logging.getLogger("evolution")

            output_lines: list = []
            for line in proc.stdout:
                stripped = line.rstrip()
                output_lines.append(stripped)
                logger.info(f"[evo#{session_id}] {stripped}")
                if len(output_lines) > 200:
                    output_lines.pop(0)

            proc.wait()
            rc = proc.returncode
            if rc == 0:
                _sessions[session_id]["status"] = "completed"
            elif rc in (-2, -signal.SIGINT, 130):  # SIGINT / Ctrl+C 优雅退出
                _sessions[session_id]["status"] = "stopped"
            else:
                _sessions[session_id]["status"] = "failed"
            _sessions[session_id]["exit_code"] = rc
            _sessions[session_id]["output_tail"] = output_lines[-50:]
        except Exception as exc:
            _sessions[session_id]["status"] = "error"
            _sessions[session_id]["error"] = str(exc)

    threading.Thread(target=_run, daemon=True).start()

    return {"session_id": session_id, "status": "started"}


@router.get("/status/{session_id}")
async def get_evolution_status(session_id: str):
    """
    获取进化进度（面板每 3s 轮询一次）

    返回字段见 run_evolution.py / meta_agent.py _write_status() 结构。
    """
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="session not found")

    session = _sessions[session_id]
    result: dict = {
        "session_id": session_id,
        "status":     session["status"],
        "elapsed":    time.time() - session["started_at"],
        "params":     session.get("params", {}),
    }

    # 读取状态文件（由 run_evolution.py 实时写入）
    status_file = session.get("status_file", "")
    if os.path.exists(status_file):
        try:
            with open(status_file, "r") as f:
                progress = json.load(f)
            result.update(progress)
        except (json.JSONDecodeError, IOError):
            pass

    # 进程已结束时附带输出尾部 + 进化日志
    if session["status"] in ("completed", "failed", "error"):
        result["output_tail"] = session.get("output_tail", [])
        result["exit_code"] = session.get("exit_code")

        log_dir = os.path.join(_RL_AGENT_DIR, "evolution_logs")
        if os.path.isdir(log_dir):
            logs = sorted(os.listdir(log_dir), reverse=True)
            full_logs = [l for l in logs if l.endswith("_full.json")]
            if full_logs:
                try:
                    with open(os.path.join(log_dir, full_logs[0]), "r") as f:
                        result["evolution_log"] = json.load(f)
                except Exception:
                    pass

    return result


@router.post("/stop/{session_id}")
async def stop_evolution(session_id: str):
    """停止正在运行的进化循环（发 SIGINT，触发优雅退出）"""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="session not found")

    session = _sessions[session_id]
    proc = session.get("process")
    if proc and proc.poll() is None:
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()   # SIGKILL 兜底
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        session["status"] = "stopped"
        return {"status": "stopped"}

    return {"status": session["status"], "msg": "process already finished"}


@router.get("/list")
async def list_sessions():
    """列出所有进化会话"""
    return {
        "sessions": [
            {
                "session_id": s["session_id"],
                "status":     s["status"],
                "started_at": s["started_at"],
                "params":     s.get("params", {}),
            }
            for s in _sessions.values()
        ]
    }


import atexit as _atexit

def _cleanup_evolution_processes():
    """后端退出时清理所有仍在运行的进化子进程，避免 orphan"""
    for session in _sessions.values():
        proc = session.get("process")
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

_atexit.register(_cleanup_evolution_processes)
