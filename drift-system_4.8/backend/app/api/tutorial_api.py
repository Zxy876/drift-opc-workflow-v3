# backend/app/api/tutorial_api.py
from fastapi import APIRouter
from typing import Dict, Any
from pydantic import BaseModel

from app.core.tutorial import tutorial_system

router = APIRouter(prefix="/tutorial", tags=["Tutorial"])


class TutorialCheckRequest(BaseModel):
    player_id: str
    message: str


@router.post("/start/{player_id}")
def start_tutorial(player_id: str):
    """开始新手教学"""
    result = tutorial_system.start_tutorial(player_id)
    
    return {
        "status": "ok",
        "message": "新手教学已开始",
        "tutorial": result,
        "mc": {
            "tell": [
                "§e✨§l━━━━━━━━━━━━━━━━━━━━━━━━━━━━§r",
                result["title"],
                f"§7{result['description']}",
                "",
                result["instruction"],
                "§e✨§l━━━━━━━━━━━━━━━━━━━━━━━━━━━━§r"
            ],
            "title": {
                "main": "§e✨ 心悦文集",
                "sub": "§7欢迎来到新手教学",
                "fade_in": 10,
                "stay": 80,
                "fade_out": 20
            },
            "sound": {
                "type": "ENTITY_PLAYER_LEVELUP",
                "volume": 1.0,
                "pitch": 1.0
            }
        }
    }


@router.post("/check")
def check_tutorial_progress(request: TutorialCheckRequest):
    """检查玩家输入是否完成教学步骤"""
    result = tutorial_system.check_progress(request.player_id, request.message)
    
    if not result:
        return {
            "status": "no_progress",
            "message": "继续尝试吧"
        }
    
    # 构建响应消息
    mc_commands = [
        {"tell": "§a✓ " + result["success_message"]}
    ]
    
    # 添加奖励指令
    mc_commands.extend(result.get("mc", []))
    
    # 如果有下一步，显示下一步信息
    if "next_step" in result:
        next_step = result["next_step"]
        mc_commands.append({
            "tell": [
                "",
                "§e━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                next_step["title"],
                f"§7{next_step['description']}",
                "",
                next_step["instruction"],
                "§e━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ]
        })
        mc_commands.append({
            "sound": {
                "type": "ENTITY_EXPERIENCE_ORB_PICKUP",
                "volume": 1.0,
                "pitch": 1.2
            }
        })
    else:
        # 教学完成
        mc_commands.append({
            "tell": [
                "",
                "§6✨§l━━━━━━━━━━━━━━━━━━━━━━━━━━━━§r",
                "§6🎉 恭喜完成新手教学！",
                "§7你已经掌握了心悦文集的所有基础功能",
                "",
                "§a现在，开始你的冒险吧！",
                "§7输入 §f'跳到第一关' §7开始正式旅程",
                "§6✨§l━━━━━━━━━━━━━━━━━━━━━━━━━━━━§r"
            ]
        })
        mc_commands.append({
            "title": {
                "main": "§6🎉 教学完成！",
                "sub": "§a祝你冒险愉快",
                "fade_in": 10,
                "stay": 100,
                "fade_out": 20
            }
        })
        mc_commands.append({
            "sound": {
                "type": "UI_TOAST_CHALLENGE_COMPLETE",
                "volume": 1.0,
                "pitch": 1.0
            }
        })
    
    return {
        "status": "ok",
        "result": result,
        "mc": mc_commands
    }


@router.get("/status/{player_id}")
def get_tutorial_status(player_id: str):
    """获取玩家的教学进度"""
    step_info = tutorial_system.get_current_step(player_id)
    
    if not step_info:
        return {
            "status": "not_started",
            "message": "尚未开始教学"
        }
    
    return {
        "status": "ok",
        "current_step": step_info
    }


@router.api_route("/hint/{player_id}", methods=["GET", "POST"])
def get_tutorial_hint(player_id: str):
    """获取当前步骤的提示"""
    hint = tutorial_system.give_hint(player_id)
    
    if not hint:
        return {
            "status": "not_started",
            "message": "尚未开始教学"
        }
    
    return {
        "status": "ok",
        "hint": hint,
        "mc": {
            "tell": f"§b💡 提示：\n{hint}"
        }
    }


@router.post("/skip/{player_id}")
def skip_tutorial(player_id: str):
    """跳过教学"""
    result = tutorial_system.skip_tutorial(player_id)
    
    return {
        "status": "ok",
        **result
    }
