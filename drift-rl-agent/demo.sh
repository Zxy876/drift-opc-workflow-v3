#!/bin/bash
# Drift RL Agent Demo — 一键启动
#
# 用法: bash demo.sh [difficulty] [episodes] [generations]
#   difficulty:   1-5 (默认 3)
#   episodes:     每代局数 (默认 5, Demo 建议 3-5)
#   generations:  最大代数 (默认 3, Demo 建议 2-3)
#
# 示例:
#   bash demo.sh                 # 默认: D3, 5局/代, 3代
#   bash demo.sh 2 3 1           # 快速 Demo: D2, 3局/代, 1代
#   bash demo.sh 4 5 5           # 深度 Demo: D4, 5局/代, 5代

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

DIFFICULTY=${1:-3}
EPISODES=${2:-5}
GENERATIONS=${3:-3}
LEVEL_ID="demo_$(date +%s)"

# ─── 颜色常量 ────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Drift RL Agent — 双环自进化系统${NC}"
echo -e "${GREEN}  StrategyBot Demo${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}  难度:  D${DIFFICULTY}${NC}"
echo -e "${CYAN}  每代:  ${EPISODES} 局${NC}"
echo -e "${CYAN}  代数:  ${GENERATIONS}${NC}"
echo -e "${CYAN}  关卡:  ${LEVEL_ID}${NC}"
echo -e "${GREEN}========================================${NC}\n"

# ─── 前提检查 ────────────────────────────────────────────
if [ -z "$OPENAI_API_KEY" ]; then
    echo -e "${YELLOW}[Warning] OPENAI_API_KEY 未设置，DesignerAgent 将无法调用 LLM${NC}"
    echo -e "${YELLOW}          运行前请执行: export OPENAI_API_KEY=your-key-here${NC}\n"
fi

# 检查 Node.js 依赖
if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}[Setup] 安装 Node.js 依赖...${NC}"
    npm install --silent
fi

# 检查 Python 依赖
python3 -c "import requests, yaml, openai" 2>/dev/null || {
    echo -e "${YELLOW}[Setup] 安装 Python 依赖...${NC}"
    pip install -r requirements.txt --quiet
}

# ─── 清理函数 ─────────────────────────────────────────────
BOT_PID=""
cleanup() {
    echo -e "\n${YELLOW}[Cleanup] 正在停止 Bot...${NC}"
    if [ -n "$BOT_PID" ] && kill -0 "$BOT_PID" 2>/dev/null; then
        kill "$BOT_PID" 2>/dev/null || true
        echo -e "${YELLOW}[Cleanup] Bot (PID: $BOT_PID) 已停止${NC}"
    fi
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Demo 完成！日志文件在 logs/ 目录下${NC}"
    echo -e "${GREEN}========================================${NC}"
}
trap cleanup EXIT INT TERM

# ─── 启动 Mineflayer Bot（后台） ──────────────────────────
echo -e "${GREEN}[1/2] 启动 Mineflayer Bot + TCP Bridge...${NC}"
node player/player_bot.js > /tmp/drift_bot_demo.log 2>&1 &
BOT_PID=$!

# 等待 TCP Bridge 就绪（最多 15 秒）
echo -n "   等待 TCP Bridge (端口 9999)..."
for i in $(seq 1 15); do
    sleep 1
    if nc -z localhost 9999 2>/dev/null; then
        echo -e " ${GREEN}就绪 (${i}s)${NC}"
        break
    fi
    echo -n "."
    if [ "$i" -eq 15 ]; then
        echo -e " ${RED}超时！${NC}"
        echo -e "${RED}[Error] Bot 未能启动，请检查 MC 服务器连接${NC}"
        echo "Bot 日志: $(tail -5 /tmp/drift_bot_demo.log 2>/dev/null)"
        exit 1
    fi
done

# 检查 Bot 进程仍在运行
if ! kill -0 "$BOT_PID" 2>/dev/null; then
    echo -e "${RED}[Error] Bot 进程已退出${NC}"
    echo "Bot 日志: $(tail -10 /tmp/drift_bot_demo.log 2>/dev/null)"
    exit 1
fi

# ─── 启动进化循环 ─────────────────────────────────────────
echo -e "${GREEN}[2/2] 启动双环进化循环...${NC}\n"

python3 meta/run_evolution.py \
    --level "$LEVEL_ID" \
    --difficulty "$DIFFICULTY" \
    --episodes "$EPISODES" \
    --generations "$GENERATIONS"
