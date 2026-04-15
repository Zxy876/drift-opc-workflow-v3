#!/bin/bash
# Drift RL Agent — 一键启动脚本
#
# 用法:
#   bash run.sh [LEVEL_ID] [DIFFICULTY] [额外参数...]
#
# 示例:
#   bash run.sh demo_rl_001 3
#   bash run.sh demo_rl_001 5 --premium --skill expert
#   bash run.sh demo_rl_001 3 --curriculum --generations 15
#   bash run.sh demo_rl_001 3 --viewer              # 同时启动 prismarine-viewer
#   额外参数: --premium --curriculum --player-id <ID> --skill <beginner|average|expert> 等
#
# 环境变量:
#   PLAYER_ID   自定义玩家 ID（等同于传入 --player-id <ID>）
#   OPENAI_API_KEY  必须设置

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ─── 参数解析 ─────────────────────────────────────────────
LEVEL="${1:-demo_rl_001}"
DIFFICULTY="${2:-3}"
shift 2 2>/dev/null || true

# 收集其余参数（--premium / --model / --curriculum 等）
EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
    EXTRA_ARGS+=("$1")
    shift
done

# 若设置了 PLAYER_ID 环境变量且 EXTRA_ARGS 中尚未指定 --player-id，则自动加入
if [ -n "$PLAYER_ID" ]; then
    has_player_id=0
    for arg in "${EXTRA_ARGS[@]}"; do
        [[ "$arg" == "--player-id" ]] && has_player_id=1 && break
    done
    [[ $has_player_id -eq 0 ]] && EXTRA_ARGS+=("--player-id" "$PLAYER_ID")
fi

echo "=========================================="
echo " Drift RL Agent — 双环自进化系统（StrategyBot）"
echo " Level: $LEVEL | Difficulty: D$DIFFICULTY"
[[ ${#EXTRA_ARGS[@]} -gt 0 ]] && echo " Extra: ${EXTRA_ARGS[*]}"
echo "=========================================="

# ─── 检查依赖 ─────────────────────────────────────────────
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

# ─── 清理函数 ─────────────────────────────────────────────
BOT_PID=""
VIEWER_PID=""
cleanup() {
    echo ""
    echo "[Cleanup] 停止 Bot (PID: $BOT_PID)..."
    if [ -n "$BOT_PID" ]; then
        kill "$BOT_PID" 2>/dev/null || true
    fi
    if [ -n "$VIEWER_PID" ]; then
        echo "[Cleanup] 停止 Viewer (PID: $VIEWER_PID)..."
        kill "$VIEWER_PID" 2>/dev/null || true
    fi
    echo "=========================================="
    echo " 完成! 查看日志: evolution_logs/"
    echo "=========================================="
}
trap cleanup EXIT INT TERM

# ─── 启动 Mineflayer Bot（后台） ──────────────────────────
echo "[Bot] 启动 Mineflayer Bot + TCP Bridge..."
node player/player_bot.js &
BOT_PID=$!
echo "[Bot] PID: $BOT_PID"

# 等待 Bot 就绪
echo "[Bot] 等待 Bot 连接 MC 服务器..."
sleep 5

# ─── 可选：启动 prismarine-viewer ─────────────────────────
VIEWER_REQUESTED=0
for arg in "${EXTRA_ARGS[@]}"; do
    [[ "$arg" == "--viewer" ]] && VIEWER_REQUESTED=1 && break
done

if [ "$VIEWER_REQUESTED" -eq 1 ]; then
    echo "[Viewer] 启动 prismarine-viewer..."
    node viewer/viewer_server.js &
    VIEWER_PID=$!
    echo "[Viewer] PID: $VIEWER_PID | 浏览器打开 http://localhost:3007"
    # 从 EXTRA_ARGS 中移除 --viewer，避免传给 Python
    FILTERED_ARGS=()
    for arg in "${EXTRA_ARGS[@]}"; do
        [[ "$arg" != "--viewer" ]] && FILTERED_ARGS+=("$arg")
    done
    EXTRA_ARGS=("${FILTERED_ARGS[@]}")
fi

# ─── 启动进化循环 ─────────────────────────────────────────
echo "[Evolution] 启动双环进化循环..."
python meta/run_evolution.py \
    --level "$LEVEL" \
    --difficulty "$DIFFICULTY" \
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
