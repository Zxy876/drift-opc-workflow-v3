# Drift RL Agent — 双环自进化系统

AI 玩家 + AI 关卡设计师的闭环进化系统。

## 架构

```
StrategyBot (规则引擎 + 多技能参数)
    ↓ 分技能游玩数据
EvalBridge (评估桥接)
    ↓ 多技能评估报告
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
│  │ StrategyBot  │ ──────────────────→ │ DesignerAgent  │    │
│  │ (规则引擎 +   │   分技能评估报告      │ (LLM GPT-4 +   │    │
│  │  多技能参数)  │ ←────────────────── │  Drift API)    │    │
│  │              │     新关卡 ID        └────────────────┘    │
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
# Node.js (18+)
npm install

# Python 依赖（轻量，无需 GPU / torch）
pip install -r requirements.txt
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

### 4. 运行进化

```bash
# 默认（混合技能档案）
python meta/run_evolution.py --level demo_rl_001 --difficulty 3

# 指定初始技能档案
python meta/run_evolution.py --level demo_rl_001 --difficulty 3 --skill average
```

### 5. 查看 Bot 视角（可选）

```bash
node viewer/viewer_server.js
# 浏览器打开 http://localhost:3007
```

## 配置说明

| 文件 | 用途 |
|------|------|
| `configs/drift_servers.yaml` | 服务器地址、Bot 用户名、LLM 模型 |
| `configs/skill_profiles.yaml` | 三档技能参数（beginner/average/expert） |
| `configs/evolution_params.yaml` | 进化超参数（Flow Zone、世代数等） |

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

> **注意**: 旧版 PPO/RL 代码已删除，当前使用 StrategyBot 架构（见 `meta/` 目录）。

- **一键启动**: `bash run.sh demo_rl_001 3`
- **课程学习**: `python meta/run_evolution.py --curriculum --difficulty 5`
- **批量生成**: `python designer/batch_generate.py --prefix my_world --publish quick`
- **进化可视化**: `python meta/visualize_evolution.py`

## 新增功能 (Phase 3)

> **注意**: 旧版 PPO/RL 代码已删除，当前使用 StrategyBot 架构（见 `meta/` 目录）。
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

## 新增功能 (Phase 4)

> **注意**: 旧版 PPO/RL 代码已删除，当前使用 StrategyBot 架构（见 `meta/` 目录）。

### 模型格式统一 (C1)
所有模型保存/加载统一支持两种格式：
- **actor-only**: `torch.save(actor.state_dict(), path)` — 推理用
- **完整检查点**: `torch.save({"actor": ..., "critic": ..., "optim": ...}, path)` — 训练恢复用

加载时自动检测格式：
```python
checkpoint = torch.load(path)
state_dict = checkpoint["actor"] if isinstance(checkpoint, dict) and "actor" in checkpoint else checkpoint
```

### 环境关闭安全 (C2)
`env.close()` 在 TCP 连接已断开时不再抛出异常。

### LLM 调用健壮性 (F1)
`generate_improved_design()` 和 `generate_new_level()` 均支持 `max_retries` 参数（默认 3 次），指数退避重试，全部失败后降级为默认设计。

### 进化检查点 (F2)
每当出现更高通关率时，MetaAgent 自动保存 `checkpoints/best_{level_id}.pth`。

### 课程学习模型传递 (F3)
`--curriculum` 模式下，每个难度阶段完成后自动将最佳模型传递到下一阶段。

### 容错评估 (F4)
每局游玩独立创建环境，单局失败不影响整体评估。连续 3 局失败自动终止当前代评估。

### 发布重试 (R4)
Quick Publish 和 Premium Publish 均支持 3 次重试。

## 新增功能 (Phase 5)

> **注意**: 旧版 PPO/RL 代码已删除，当前使用 StrategyBot 架构（见 `meta/` 目录）。

### 死亡检测优化 (Q3)
`last_death_cause` 优先于 `health <= 0` 判断，避免重复计入。Bot 侧读取后立即清除。

### 关卡加载验证 (F4)
`reset()` 替换硬编码 `sleep(3)` 为最多 10 秒的验证循环，确保关卡实际加载完毕。

### 工作流进度日志 (F3)
`_wait_for_workflow()` 每 30 秒输出一次工作流状态和当前步骤。

### run.sh 增强 (F5)
支持 `PLAYER_ID` 环境变量，自动传递给 `--player-id`。

## 新增功能 (Phase 6)

> **注意**: 旧版 PPO/RL 代码已删除，当前使用 StrategyBot 架构（见 `meta/` 目录）。

### TensorBoard 训练可视化 (I1)
`train_player.py` 集成 TensorBoard Logger，训练时自动生成事件文件到 `tb_logs/` 目录。
```bash
python player/train_player.py --level demo_rl_001
tensorboard --logdir tb_logs
```

### 一键启动 Viewer (I2)
`run.sh` 支持 `--viewer` 参数，自动启动 prismarine-viewer：
```bash
bash run.sh demo_rl_001 3 --viewer
# 浏览器打开 http://localhost:3007 查看 Bot 视角
```

### 参数一致性 (Q1)
所有脚本统一使用 `--player-id` 参数名（`play_with_model.py`、`train_player.py`、`run_evolution.py`）。

### 优雅退出 (Q2)
Ctrl+C 中断进化循环时自动导出已有日志。

### Dashboard URL 加载 (I3)
进化看板支持通过 URL 加载 JSON/JSONL 日志文件，无需手动上传。

### 运行冒烟测试
```bash
python tests/smoke_test.py
# 无需 MC 服务器，25 项测试 (torch 未装时 9 项跳过)
```

## Phase 8: StrategyBot 重构

### 核心变更

- **移除**: `tianshou`, `gymnasium`, `torch`, `tensorboard` — 依赖从 8 个降至 4 个
- **新增**: `StrategyBot` 规则引擎（6 优先级状态机：危险 > 战斗 > 交互 > 收集 > 探索 > 待机）
- **新增**: 三档技能档案（`beginner` / `average` / `expert`），参数化反应速度、战斗距离、Pathfinder 开关等
- **新增**: 多技能评估 — 每世代对 3 种技能档案分别采集数据，Flow Zone 以 `average` 通关率为基准

### 技能档案

| 技能档案 | reaction_ticks | use_easy_probability | 描述 |
|---------|---------------|---------------------|------|
| `beginner` | 10 | 0.3 | 模拟新手，慢反应，常用 /easy |
| `average` | 5 | 0.1 | 模拟普通玩家，Flow Zone 基准 |
| `expert` | 2 | 0.0 | 模拟高手，快反应，不用 /easy |

### 归档文件

旧版 PPO/RL 文件已删除。

### 冒烟测试
```bash
python tests/smoke_test.py
# 24 项测试，无需 MC 服务器，无需 torch
```
