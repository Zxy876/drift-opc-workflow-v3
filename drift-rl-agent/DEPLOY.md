# Drift RL Agent — 部署指南

## 环境依赖

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Node.js | 18+ | Mineflayer Bot 运行时 |
| Python | 3.10+ | MetaAgent / DesignerAgent |
| Minecraft Server | 1.20.x (PaperMC) | 世界环境，端口 25565 |
| Drift Backend | — | 地图生成 API，端口 8000 |
| GLM_API_KEY（或 OPENAI_API_KEY） | — | DesignerAgent LLM 调用 |

---

## 快速部署

### 1. 克隆代码

```bash
git clone https://github.com/Zxy876/drift-opc-workflow-v3.git
cd drift-opc-workflow-v3/drift-rl-agent
```

### 2. 安装依赖

```bash
# Node.js 依赖（Mineflayer Bot）
npm install

# Python 依赖（MetaAgent / DesignerAgent）
pip install -r requirements.txt
```

### 3. 配置服务器地址

编辑 `configs/drift_servers.yaml`，填写实际地址：

```yaml
mc_server:
  host: "35.201.132.58"
  port: 25565

drift_backend:
  url: "http://35.201.132.58:8000"
```

### 4. 配置密钥

```bash
export GLM_API_KEY=your-glm-api-key-here
```

---

## 一键 Demo

```bash
bash demo.sh [difficulty] [episodes] [generations]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| difficulty | 3 | 难度等级 (1-5) |
| episodes | 5 | 每代对局数 |
| generations | 3 | 最大进化代数 |

**示例：**

```bash
# 默认 Demo（3代 × 5局）
bash demo.sh

# 快速验证（1代 × 3局）
bash demo.sh 2 3 1

# 深度 Demo（5代 × 5局）
bash demo.sh 4 5 5
```

---

## 手动运行进化循环

```bash
# 启动 Bot（后台）
node player/player_bot.js &

# 运行进化
python3 meta/run_evolution.py \
  --level my_level_001 \
  --difficulty 3 \
  --episodes 5 \
  --generations 3

# 指定技能子集（可选）
python3 meta/run_evolution.py \
  --level my_level_001 \
  --difficulty 3 \
  --episodes 5 \
  --generations 3 \
  --skill combat
```

### run_evolution.py 完整参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--level` | 关卡 ID | — |
| `--difficulty` | 难度 1-5 | 3 |
| `--episodes` | 每代局数 | 5 |
| `--generations` | 最大代数 | 5 |
| `--skill` | 单一技能模式 (beginner/average/expert) | None（全技能） |
| `--player-id` | Bot 玩家 ID | bot_001 |
| `--curriculum` | 课程学习模式 | off |
| `--premium` | 使用高级 LLM (gpt-4o) | False |
| `--drift-url` | Drift Backend URL | configs 中读取 |
| `--bot-port` | TCP Bridge 端口 | 9999 |

---

## 烟雾测试

```bash
python3 tests/smoke_test.py
```

预期：**24 passed, 0 failed**

---

## 故障排查

| 症状 | 原因 | 解决方案 |
|------|------|---------|
| Bot 启动超时 | MC 服务器不可达 | 检查 `configs/drift_servers.yaml` 中 `mc_server.host` 和端口 25565 |
| `Connection refused 9999` | player_bot.js 未运行 | 先 `node player/player_bot.js &` |
| DesignerAgent 无响应 | `OPENAI_API_KEY` 未设置 | `export OPENAI_API_KEY=...` |
| Drift Backend 502/503 | 后端服务未启动 | 检查 `http://YOUR_HOST:8000/health` |
| 技能加载失败 | `configs/skill_profiles.yaml` 缺失 | 确认文件存在且 YAML 语法正确 |
| `smoke_test.py` 失败 | 依赖版本不兼容 | `pip install -r requirements.txt --upgrade` |

---

## 服务架构

```
┌─────────────────────────────────┐
│         Meta Loop（外环）         │
│  run_evolution.py → MetaAgent   │
│  DesignerAgent → Drift Backend  │
└─────────────┬───────────────────┘
              │ 设计参数
┌─────────────▼───────────────────┐
│         Player Loop（内环）       │
│  StrategyBot → BotClient        │
│  player_bot.js ← TCP Bridge     │
│  Mineflayer ← MC Server :25565  │
└─────────────────────────────────┘
```

---

## 生产部署（GCP VM）

```bash
# 一键部署脚本
gcloud compute ssh drift-demo-vm \
  --zone=asia-east1-b \
  --project=gold-mining-workflow \
  --tunnel-through-iap \
  --command='
    set -e
    cd ~/drift-opc-workflow-v3
    git pull origin feature/drift-rl-agent
    cd drift-rl-agent
    npm install
    pip install -r requirements.txt
    python3 tests/smoke_test.py
    echo "部署完成！运行 bash demo.sh 启动 Demo"
  '
```
