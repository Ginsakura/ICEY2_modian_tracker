#!/usr/bin/env bash

# 遇到未定义变量报错并退出
set -u

# 抓取间隔秒数，默认 60（支持通过第一个位置参数传入，如 ./run.sh 30）
INTERVAL="${1:-60}"

# 切换到脚本所在绝对目录，保证相对路径正确
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# 检查并激活虚拟环境（Linux/Termux 下路径为 .venv/bin/activate）
VENV_ACTIVATE="$SCRIPT_DIR/.venv/bin/activate"
if [ -f "$VENV_ACTIVATE" ]; then
    # shellcheck disable=SC1090
    source "$VENV_ACTIVATE"
else
    echo "警告: 未找到虚拟环境激活脚本: $VENV_ACTIVATE" >&2
fi

# 创建日志目录
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
OUT_LOG="$LOG_DIR/launcher.out.log"
ERR_LOG="$LOG_DIR/launcher.err.log"

# 使用 nohup + 后台运行 (&)，以完全分离的独立进程静默执行
# 已激活虚拟环境，直接调用原生 python
nohup python modian_tracker.py --interval "$INTERVAL" > "$OUT_LOG" 2> "$ERR_LOG" &
PID=$!

echo "已在后台启动抓取进程 (PID: $PID)，抓取间隔 $INTERVAL 秒。"
echo "抓取日志: $SCRIPT_DIR/modian_tracker.log"
echo "启动器日志: $OUT_LOG / $ERR_LOG"
echo "停止命令: kill $PID"
