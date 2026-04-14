# Drift RL Agent — 双环自进化系统

AI 玩家 + AI 关卡设计师的闭环进化系统。

## 架构

```
PlayerAgent (Mineflayer + Tianshou PPO)
    ↓ 游玩数据
EvalBridge (评估桥接)
    ↓ 评估报告
DesignerAgent (LLM + Drift API)
    ↓ 新关卡
MetaAgent (双环控制器) → 循环
```

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    MetaAgent (双环控制器)                      │
│                                                             │
│  ┌──────────────┐       评估报告       ┌────────────────┐    │
│  │ PlayerAgent  │ ──────────────────→ │ DesignerAgent  │    │
│  │ (Mineflayer  │                     │ (LLM GPT-4 +   │    │
│  │  + Tianshou  │ ←────────────────── │  Drift API)    │    │
│  │  PPO)        │     新关卡 ID        └────────────────┘    │
│  └──────┬───────┘                              │            │
│         │ TCP Bridge :9999                     │            │
│         ▼                                      ▼            │
│  ┌──────────────┐                    ┌─────────────────┐    │
│  │ MC Server    │                    │ Drift Backend   │    │
│  │ :25565       │                    │ :8000 / :8080   │    │
│  └──────────────┘                    └─────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## 快速开始

### 1. 安装依赖

```bash
# Python
pip install -r requirements.txt

# Node.js
npm install
```

### 2. 配置

编辑 `configs/drift_servers.yaml` 中的服务器地址。

设置环境变量：
```bash
export OPENAI_API_KEY=your-key-here
```

### 3. 启动 Bot

```bash
node player/player_bot.js
```

### 4. 训练 PlayerAgent（可选）

```bash
python player/train_player.py --level demo_rl_001
```

### 5. 运行进化

```bash
python meta/run_evolution.py --level demo_rl_001 --difficulty 3
```

### 6. 查看 Bot 视角（可选）

```bash
node viewer/viewer_server.js
# 浏览器打开 http://localhost:3007
```

## 配置说明

| 文件 | 用途 |
|------|------|
| `configs/drift_servers.yaml` | 服务器地址、Bot 用户名、LLM 模型 |
| `configs/reward_params.yaml` | 奖励函数参数（可热调参） |
| `configs/evolution_params.yaml` | 进化参数 + PPO 训练超参数 |

## API 参考

### Drift 后端 (35.201.132.58:8000)
- `GET /story/levels` — 获取关卡列表
- `POST /story/inject` — Quick Publish（直接注入关卡）
- `GET /story/difficulty/{player_id}` — 获取玩家当前难度
- `POST /story/load/{player_id}/{level_id}` — 加载关卡
- `POST /story/auto-advance/{player_id}` — 自动过关
- `GET /story/state/{player_id}` — 获取玩家状态

### AsyncAIFlow (35.201.132.58:8080)
- `POST /planner/execute` — Premium Publish（AI 工作流全链路）
- `GET /workflows/{id}` — 查询工作流状态

## TCP Bridge 协议

Python ↔ Node.js 通过 TCP 9999 端口通信，每条消息为 JSON + `\n`。

支持的命令类型：

| type | 说明 | 参数 |
|------|------|------|
| `ping` | 检查 Bot 就绪状态 | — |
| `get_state` | 获取完整游戏状态 | — |
| `action` | 执行低层动作 | `action: {move_forward, move_strafe, jump, attack, use_item}` |
| `command` | 发送 MC 聊天命令 | `text: "/level demo_rl_001"` |
| `reset` | 重置关卡 | `level_id: "demo_rl_001"` |
| `navigate_to` | Pathfinder 导航 | `x, y, z` |
| `stop_all` | 停止所有动作 | — |

## 进化停止条件

通关率连续 3 代在 **Flow Zone [60%, 80%]** 时自动停止。

可通过 `configs/evolution_params.yaml` 调整：
```yaml
evolution:
  flow_zone_min: 0.6
  flow_zone_max: 0.8
  flow_zone_streak_target: 3
```

## 新增功能 (Phase 2)

- **一键启动**: `bash run.sh demo_rl_001 3`
- **课程学习**: `python meta/run_evolution.py --curriculum --difficulty 5`
- **批量生成**: `python designer/batch_generate.py --prefix my_world --publish quick`
- **模型推理**: `python player/play_with_model.py --model checkpoints/player_ppo.pth`
- **进化可视化**: `python meta/visualize_evolution.py`
