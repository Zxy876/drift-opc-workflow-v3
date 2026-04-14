"""
Mineflayer Bot TCP Bridge 客户端

从 player_bot.js 的 TCP Bridge (端口 9999) 获取游戏状态、发送动作。
这是 StrategyBot 和 MetaAgent 与 MC 世界交互的唯一通道。
"""

import json
import socket
import time
from typing import Optional


class BotClient:
    """TCP Bridge 客户端 — 连接 player_bot.js"""

    def __init__(self, host: str = "localhost", port: int = 9999, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None

    def connect(self):
        """建立 TCP 连接"""
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self.timeout)
            self._sock.connect((self.host, self.port))

    def disconnect(self):
        """断开连接"""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def send(self, data: dict) -> dict:
        """发送命令并接收响应（带 3 次自动重连）"""
        for attempt in range(3):
            try:
                self.connect()
                msg = json.dumps(data) + "\n"
                self._sock.sendall(msg.encode())

                response = b""
                while not response.endswith(b"\n"):
                    chunk = self._sock.recv(8192)
                    if not chunk:
                        raise ConnectionError("Bot 连接断开")
                    response += chunk

                return json.loads(response.decode().strip())
            except (ConnectionError, socket.error, OSError) as e:
                self.disconnect()
                if attempt == 2:
                    raise ConnectionError(f"Bot 连接失败（3 次重试后）: {e}")
                time.sleep(1)

    def ping(self) -> bool:
        """检查 Bot 是否就绪"""
        try:
            resp = self.send({"type": "ping"})
            return resp.get("ready", False)
        except Exception:
            return False

    def wait_ready(self, timeout: float = 10.0) -> bool:
        """等待 Bot 就绪"""
        start = time.time()
        while time.time() - start < timeout:
            if self.ping():
                return True
            time.sleep(0.5)
        raise TimeoutError(f"Bot 未就绪（等待 {timeout}s）")

    def get_state(self) -> dict:
        """获取完整游戏状态"""
        return self.send({"type": "get_state"})

    def execute_action(self, action: dict):
        """执行低层动作（移动/跳跃/攻击等）"""
        self.send({"type": "action", "action": action})

    def chat(self, text: str):
        """发送 MC 聊天命令"""
        self.send({"type": "command", "text": text})

    def reset_level(self, level_id: str):
        """重置关卡"""
        self.send({"type": "reset", "level_id": level_id})

    def navigate_to(self, x: float, y: float, z: float):
        """Pathfinder 导航到指定坐标"""
        self.send({"type": "navigate_to", "x": x, "y": y, "z": z})

    def look_at(self, x: float, y: float, z: float):
        """看向指定坐标"""
        self.send({"type": "look_at", "x": x, "y": y, "z": z})

    def stop_all(self):
        """停止所有动作"""
        self.send({"type": "stop_all"})

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        try:
            self.stop_all()
        except Exception:
            pass
        self.disconnect()
