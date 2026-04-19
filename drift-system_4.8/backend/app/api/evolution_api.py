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
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/evolution", tags=["evolution"])

# 全局进化会话存储（内存，单实例够用）
_sessions: Dict[str, dict] = {}
_play_sessions: Dict[str, dict] = {}

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


_MAX_SESSION_AGE = 3600 * 24  # 24 小时后自动清理
_MAX_SESSIONS = 50            # 最多保留 50 个会话


def _cleanup_old_sessions() -> None:
    """清理过期已完成会话，避免 _sessions 字典无限增长。"""
    now = time.time()
    expired = [
        sid for sid, s in list(_sessions.items())
        if s["status"] in ("completed", "failed", "error", "stopped")
        and now - s.get("started_at", 0) > _MAX_SESSION_AGE
    ]
    for sid in expired:
        sf = _sessions[sid].get("status_file", "")
        if sf and os.path.exists(sf):
            try:
                os.remove(sf)
            except OSError:
                pass
        del _sessions[sid]

    # 如果仍超限，按 started_at 删除最老的已完成会话
    if len(_sessions) > _MAX_SESSIONS:
        finished = sorted(
            [(sid, s) for sid, s in _sessions.items()
             if s["status"] not in ("starting", "running")],
            key=lambda x: x[1].get("started_at", 0),
        )
        for sid, _ in finished[: len(_sessions) - _MAX_SESSIONS]:
            sf = _sessions[sid].get("status_file", "")
            if sf and os.path.exists(sf):
                try:
                    os.remove(sf)
                except OSError:
                    pass
            del _sessions[sid]


class _StartEvolutionRequest(BaseModel):
    design: str = ""
    level_id: str = ""
    player_id: str = "DriftRLAgent"
    difficulty: int = 3
    episodes: int = 5
    generations: int = 3
    max_steps: int = 2000
    premium: bool = False
    skill: Optional[str] = None
    bot_port: int = 9999


@router.post("/start")
async def start_evolution(req: _StartEvolutionRequest):
    """
    启动一次进化循环。字段含义见 _StartEvolutionRequest。

    返回:
      session_id: str     — 进化会话 ID（用于后续轮询）
      status: "started"
    """
    _cleanup_old_sessions()

    # 前置检查：Bot TCP Bridge 必须在运行
    if not _check_bot_bridge(req.bot_port):
        raise HTTPException(
            status_code=503,
            detail=f"Mineflayer Bot 未运行（端口 {req.bot_port} 不可达）。"
                   "请先在服务器运行: node player/player_bot.js"
        )

    # 互斥检查：同一时间只允许一个 Evolution 会话
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
    level_arg = req.level_id or f"panel_evo_{session_id}"
    cmd = [
        _VENV_PYTHON, "meta/run_evolution.py",
        "--level",       level_arg,
        "--difficulty",  str(req.difficulty),
        "--episodes",    str(req.episodes),
        "--generations", str(req.generations),
        "--player-id",   req.player_id,
        "--status-file", status_file,
        "--max-steps",   str(req.max_steps),
    ]
    if req.design:
        cmd += ["--design", req.design]
    if req.premium:
        cmd += ["--premium"]
    if req.skill:
        cmd += ["--skill", req.skill]

    # 初始化会话记录
    _sessions[session_id] = {
        "session_id": session_id,
        "status": "starting",
        "status_file": status_file,
        "process": None,
        "started_at": time.time(),
        "params": {
            "design":      req.design,
            "level_id":    req.level_id,
            "difficulty":  req.difficulty,
            "episodes":    req.episodes,
            "generations": req.generations,
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
                # ── GitHub Projects: 标记进化完成 ──
                try:
                    from app.api.github_projects import update_project_item_status

                    _final_status = "In Flow Zone"
                    if os.path.exists(status_file):
                        try:
                            with open(status_file, "r") as _sf:
                                _evo_data = json.load(_sf)
                            _final_cr = _evo_data.get("completion_rate", 0)
                            if 0.6 <= _final_cr <= 0.8:
                                _final_status = "In Flow Zone"
                            else:
                                _final_status = "Done"
                        except Exception:
                            _final_status = "Done"

                    update_project_item_status(level_arg, _final_status)
                except Exception:
                    pass
            elif rc in (-2, -signal.SIGINT, 130):  # SIGINT / Ctrl+C 优雅退出
                _sessions[session_id]["status"] = "stopped"
            else:
                _sessions[session_id]["status"] = "failed"
            _sessions[session_id]["exit_code"] = rc
            _sessions[session_id]["output_tail"] = output_lines[-50:]
            # 进程完成后清理状态文件
            try:
                if os.path.exists(status_file):
                    os.remove(status_file)
            except OSError:
                pass
        except Exception as exc:
            _sessions[session_id]["status"] = "error"
            _sessions[session_id]["error"] = str(exc)

    threading.Thread(target=_run, daemon=True).start()

    # ── GitHub Projects: 标记关卡为 "Testing" ──
    try:
        from app.api.github_projects import update_project_item_status

        update_project_item_status(level_arg, "Testing")
    except Exception:
        pass

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
    _cleanup_old_sessions()
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


# ── DriftAgent Play Mode ──────────────────────────────────────

class _PlayRequest(BaseModel):
    level_id: str
    player_id: str = "DriftAgent"
    skill: str = "average"
    max_steps: int = 3000
    bot_port: int = 9999


@router.post("/play")
async def start_play(req: _PlayRequest):
    """
    启动 DriftAgent 单局游玩（轻量级，不含进化循环）。

    前端在发布成功后、检测到 player_id == "DriftAgent" 时自动调用。
    """
    if not _check_bot_bridge(req.bot_port):
        raise HTTPException(
            status_code=503,
            detail=f"Mineflayer Bot 未运行（端口 {req.bot_port} 不可达）。"
                   "请先在服务器运行: node player/player_bot.js"
        )

    session_id = str(uuid.uuid4())[:8]
    status_file = f"/tmp/driftagent_play_{session_id}_status.json"
    diary_file = f"/tmp/driftagent_diary_{session_id}.json"

    cmd = [
        _VENV_PYTHON, "player/driftagent_play.py",
        "--level", req.level_id,
        "--player-id", req.player_id,
        "--skill", req.skill,
        "--max-steps", str(req.max_steps),
        "--bot-port", str(req.bot_port),
        "--status-file", status_file,
        "--diary-file", diary_file,
    ]

    _play_sessions[session_id] = {
        "session_id": session_id,
        "status": "starting",
        "status_file": status_file,
        "diary_file": diary_file,
        "process": None,
        "started_at": time.time(),
        "params": {
            "level_id": req.level_id,
            "player_id": req.player_id,
            "skill": req.skill,
        },
    }

    with open(status_file, "w", encoding="utf-8") as f:
        json.dump({"status": "starting"}, f)

    def _run_play():
        try:
            env = os.environ.copy()
            proc = subprocess.Popen(
                cmd,
                cwd=_RL_AGENT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            _play_sessions[session_id]["process"] = proc
            _play_sessions[session_id]["status"] = "running"
            _play_sessions[session_id]["pid"] = proc.pid

            output_lines = []
            for line in proc.stdout:
                stripped = line.rstrip()
                output_lines.append(stripped)
                if len(output_lines) > 100:
                    output_lines.pop(0)

            proc.wait()
            rc = proc.returncode
            _play_sessions[session_id]["status"] = "completed" if rc == 0 else "failed"
            _play_sessions[session_id]["exit_code"] = rc
            _play_sessions[session_id]["output_tail"] = output_lines[-30:]
        except Exception as exc:
            _play_sessions[session_id]["status"] = "error"
            _play_sessions[session_id]["error"] = str(exc)

    threading.Thread(target=_run_play, daemon=True).start()
    return {"session_id": session_id, "status": "started"}


@router.get("/play/status/{session_id}")
async def get_play_status(session_id: str):
    """获取 Play Mode 进度（面板轮询）"""
    if session_id not in _play_sessions:
        raise HTTPException(status_code=404, detail="play session not found")

    session = _play_sessions[session_id]
    result = {
        "session_id": session_id,
        "status": session["status"],
        "elapsed": time.time() - session["started_at"],
        "params": session.get("params", {}),
    }

    sf = session.get("status_file", "")
    if sf and os.path.exists(sf):
        try:
            with open(sf, "r", encoding="utf-8") as f:
                result.update(json.load(f))
        except Exception:
            pass

    df = session.get("diary_file", "")
    if df and os.path.exists(df):
        try:
            with open(df, "r", encoding="utf-8") as f:
                diary = json.load(f)
            result["diary"] = diary
        except Exception:
            pass

    if session["status"] in ("completed", "failed", "error"):
        result["output_tail"] = session.get("output_tail", [])
        result["exit_code"] = session.get("exit_code")

    return result


@router.get("/play/diary/{session_id}")
async def get_play_diary(session_id: str):
    """获取决策日记全文"""
    if session_id not in _play_sessions:
        raise HTTPException(status_code=404, detail="play session not found")

    df = _play_sessions[session_id].get("diary_file", "")
    if df and os.path.exists(df):
        with open(df, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"decisions": [], "result": None}


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
        # 清理状态文件
        sf = session.get("status_file", "")
        if sf and os.path.exists(sf):
            try:
                os.remove(sf)
            except OSError:
                pass

    for session in _play_sessions.values():
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

        sf = session.get("status_file", "")
        if sf and os.path.exists(sf):
            try:
                os.remove(sf)
            except OSError:
                pass

        df = session.get("diary_file", "")
        if df and os.path.exists(df):
            try:
                os.remove(df)
            except OSError:
                pass

_atexit.register(_cleanup_evolution_processes)
