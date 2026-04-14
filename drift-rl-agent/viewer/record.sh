#!/bin/bash
# Drift RL Agent — 录屏脚本
# 用法: bash viewer/record.sh [输出文件名] [持续时间(秒)]
#
# 前提: node viewer/viewer_server.js 已在运行
# 依赖: ffmpeg (sudo apt install ffmpeg)

set -e

OUTPUT="${1:-evolution_recording_$(date +%Y%m%d_%H%M%S).mp4}"
DURATION="${2:-300}"  # 默认 5 分钟
VIEWER_URL="${VIEWER_URL:-http://localhost:3007}"

echo "=========================================="
echo " Drift RL Agent — 录屏"
echo " 输出: $OUTPUT"
echo " 时长: ${DURATION}s"
echo " Viewer: $VIEWER_URL"
echo "=========================================="

# 检查 ffmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "[Error] 需要 ffmpeg: sudo apt install ffmpeg"
    exit 1
fi

# 方法1: 如果有 X11 display（VM 环境）
if [ -n "$DISPLAY" ]; then
    echo "[Record] 使用 X11 屏幕录制..."
    # 先打开浏览器
    google-chrome --new-window "$VIEWER_URL" &
    sleep 3
    # 录制屏幕
    ffmpeg -video_size 1920x1080 -framerate 30 -f x11grab -i "$DISPLAY" \
           -t "$DURATION" -c:v libx264 -preset fast -crf 23 \
           "$OUTPUT" -y
else
    echo "[Record] 无 X11 display，使用 headless 截图模式..."
    echo "[Record] 请在有 GUI 的环境中运行，或手动录制 $VIEWER_URL"
    echo "[Record] 替代方案: 用 OBS 录制浏览器中的 prismarine-viewer 页面"
    exit 1
fi

echo "[Record] 录制完成: $OUTPUT"
