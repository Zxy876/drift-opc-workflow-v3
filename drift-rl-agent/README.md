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

## 新增功能 (Phase 3)

### 观测空间扩展 (F1 + S1)
`player/observation_space.py` 统一定义 64 维观测向量布局；`obs[57:62]` 新增有效信号：
- `obs[57]` 当前 Drift 难度 (0–1)
- `obs[58]` 剩余触发器比例
- `obs[59]` 附近 NPC/玩家检测
- `obs[60]` 任务进度（触发器完成率）
- `obs[61]` 时间压力（剩余时间比例）

### Drift API 集成 (F2)
`player/player_bot.js` 每 5 秒异步拉取 `/story/status/{player_id}`，将 `current_difficulty`、`triggers_remaining`、`total_triggers`、`quest_progress`、`time_limit` 注入到 `getState()` 返回值中，拼接进 obs 向量。

### 新参数支持 (F3 + F4)
- `run_evolution.py` 支持 `--model <path>` 传入训练好的 PPO 权重
- `train_player.py` 从配置文件读取 `num_train_envs` / `num_test_envs`，自动检测多 Bot 冲突并降级

### 动作工具库 (Q2)
`player/action_utils.py` 提供 `flat_to_multi()` / `multi_to_flat()` 双向转换，`play_with_model.py` 和 `meta_agent.py` 均改用此模块，消除代码重复。

### 模型加载健壮性 (Q1)
所有 `actor.load_state_dict(...)` 改为优先 `strict=True`，失败时自动过滤形状不匹配的键后降级 `strict=False`，兼容跨版本 checkpoint。

### Drift 关卡激活 (E1)
`MetaAgent.run_evolution()` 在每次发布新关卡后调用 `POST /story/load/{player_id}/{new_level_id}`，确保 Drift 后端完成关卡切换。

### 发布验证 (E2)
`DesignerAgent._publish_quick()` 发布后等待 2 秒并调用 `get_existing_levels()` 验证关卡已写入，返回值新增 `verified` 字段。

### 语法参考扩充 (E3)
`design_prompts.py` 两个 Prompt 中的语法参考新增：NPC 行为类型、陷阱类型、天气、复活点、关卡阶段、开锁条件等高级语法示例。

### run.sh 增强 (E4)
重写 `run.sh`，支持任意数量额外参数透传（如 `--premium --model checkpoints/xxx.pth`），新增 `cleanup()` trap 确保 Bot 进程在脚本退出时随之终止。

### 新增文件
| 文件 | 用途 |
|------|------|
| `player/observation_space.py` | 观测维度统一定义 + `get_observation_space()` |
| `player/action_utils.py` | `flat_to_multi()` / `multi_to_flat()` |
| `tests/smoke_test.py` | 离线冒烟测试（无需 MC 服务器） |
| `tests/__init__.py` | Python 包标记 |
| `viewer/dashboard.html` | Chart.js 进化可视化看板 |
| `viewer/record.sh` | ffmpeg Bot 视角录制脚本 |

### 运行冒烟测试
```bash
python tests/smoke_test.py
# 无需 MC 服务器，16 项测试 (torch 未装时 4 项跳过)
```
