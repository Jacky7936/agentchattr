#!/usr/bin/env sh
# agentchattr - Stops all agentchattr server, wrapper processes, and tmux sessions
cd "$(dirname "$0")/.."

echo "Stopping agentchattr..."

# 1. 關閉在 8300 埠號運行的後端伺服器
PORT_PIDS=$(lsof -t -i :8300)
if [ -n "$PORT_PIDS" ]; then
    echo "Killing server on port 8300..."
    kill -9 $PORT_PIDS >/dev/null 2>&1
fi

# 2. 結束所有 wrapper.py 和 run.py 的 Python 行程
echo "Killing Python wrappers and running scripts..."
pkill -f "wrapper.py" >/dev/null 2>&1
pkill -f "run.py" >/dev/null 2>&1

# 3. 清理所有跟 agentchattr 有關的 tmux 會話
if command -v tmux >/dev/null 2>&1; then
    echo "Cleaning up tmux sessions..."
    tmux list-sessions -F '#S' 2>/dev/null | grep 'agentchattr' | while read -r session; do
        echo "Killing tmux session: $session"
        tmux kill-session -t "$session" >/dev/null 2>&1
    done
fi

echo "All agentchattr processes stopped!"
