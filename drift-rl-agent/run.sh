#!/bin/bash
# Drift RL Agent — 一键启动脚本
# 用法: bash run.sh [LEVEL_ID] [DIFFICULTY] [--premium]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 默认参数
LEVEL="${1:-demo_rl_001}"
DIFFICULTY="${2:-3}"

echo "=========================================="
echo " Drift RL Agent — 双环自进化系统"
echo " Level: $LEVEL | Difficulty: D$DIFFICULTY"
echo "=========================================="

# 检查依赖
echo "[Setup] 检查 Node.js 依赖..."
if [ ! -d "node_modules" ]; then
    npm install
fi

echo "[Setup] 检查 Python 依赖..."
pip install -r requirements.txt --quiet 2>/dev/null || echo "[Warn] pip install 失败，请手动安装"

# 检查 OPENAI_API_KEY
if [ -z "$OPENAI_API_KEY" ]; then
    echo "[Error] 请设置 OPENAI_API_KEY 环境变量"
    echo "  export OPENAI_API_KEY=your-key-here"
    exit 1
fi

# 启动 Mineflayer Bot（后台）
echo "[Bot] 启动 Mineflayer Bot + TCP Bridge..."
node player/player_bot.js &
BOT_PID=$!
echo "[Bot] PID: $BOT_PID"

# 等待 Bot 就绪
echo "[Bot] 等待 Bot 连接 MC 服务器..."
sleep 5

# 启动进化循环
echo "[Evolution] 启动双环进化循环..."
python meta/run_evolution.py \
    --level "$LEVEL" \
    --difficulty "$DIFFICULTY" \
    ${3:+"$3"}

# 清理
echo "[Cleanup] 停止 Bot..."
kill $BOT_PID 2>/dev/null || true

echo "=========================================="
echo " 完成! 查看日志: evolution_logs/"
echo "=========================================="
