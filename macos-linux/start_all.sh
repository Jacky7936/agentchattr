#!/usr/bin/env sh
# agentchattr - Starts Server + Claude + Codex (Bypass) + opens Web UI
cd "$(dirname "$0")/.."

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "Python 3 is required but was not found on PATH."
    exit 1
fi

ensure_venv() {
    if [ -d ".venv" ] && [ ! -x ".venv/bin/python" ]; then
        echo "Recreating .venv for this platform..."
        rm -rf .venv
    fi

    if [ ! -x ".venv/bin/python" ]; then
        echo "Creating virtual environment..."
        "$PYTHON_BIN" -m venv .venv || {
            echo "Error: failed to create .venv with $PYTHON_BIN."
            exit 1
        }
        .venv/bin/python -m pip install -q -r requirements.txt || {
            echo "Error: failed to install Python dependencies."
            exit 1
        }
    fi
}

is_server_running() {
    lsof -i :8300 -sTCP:LISTEN >/dev/null 2>&1 || \
    ss -tlnp 2>/dev/null | grep -q ':8300 '
}

ensure_venv

# 1. 啟動伺服器 (若未運行)
if ! is_server_running; then
    echo "Starting agentchattr server..."
    if [ "$(uname -s)" = "Darwin" ]; then
        osascript -e "tell app \"Terminal\" to do script \"cd '$(pwd)' && .venv/bin/python run.py\"" > /dev/null 2>&1
    else
        .venv/bin/python run.py > data/server.log 2>&1 &
    fi

    # 等待伺服器就緒
    i=0
    while [ "$i" -lt 30 ]; do
        if is_server_running; then
            break
        fi
        sleep 0.5
        i=$((i + 1))
    done
fi

# 2. 在新終端機視窗中啟動 四個 Codex 實例 (Bypass 模式)
echo "Starting Codex #1 (Bypass mode) in new terminal window..."
if [ "$(uname -s)" = "Darwin" ]; then
    osascript -e "tell app \"Terminal\" to do script \"cd '$(pwd)' && .venv/bin/python wrapper.py codex -- --dangerously-bypass-approvals-and-sandbox\"" > /dev/null 2>&1
else
    .venv/bin/python wrapper.py codex -- --dangerously-bypass-approvals-and-sandbox > data/codex_1.log 2>&1 &
fi

echo "Starting Codex #2 (Bypass mode) in another terminal window..."
if [ "$(uname -s)" = "Darwin" ]; then
    osascript -e "tell app \"Terminal\" to do script \"cd '$(pwd)' && .venv/bin/python wrapper.py codex -- --dangerously-bypass-approvals-and-sandbox\"" > /dev/null 2>&1
else
    .venv/bin/python wrapper.py codex -- --dangerously-bypass-approvals-and-sandbox > data/codex_2.log 2>&1 &
fi

echo "Starting Codex #3 (Bypass mode) in another terminal window..."
if [ "$(uname -s)" = "Darwin" ]; then
    osascript -e "tell app \"Terminal\" to do script \"cd '$(pwd)' && .venv/bin/python wrapper.py codex -- --dangerously-bypass-approvals-and-sandbox\"" > /dev/null 2>&1
else
    .venv/bin/python wrapper.py codex -- --dangerously-bypass-approvals-and-sandbox > data/codex_3.log 2>&1 &
fi

echo "Starting Codex #4 (Bypass mode) in another terminal window..."
if [ "$(uname -s)" = "Darwin" ]; then
    osascript -e "tell app \"Terminal\" to do script \"cd '$(pwd)' && .venv/bin/python wrapper.py codex -- --dangerously-bypass-approvals-and-sandbox\"" > /dev/null 2>&1
else
    .venv/bin/python wrapper.py codex -- --dangerously-bypass-approvals-and-sandbox > data/codex_4.log 2>&1 &
fi

# 3. 在新終端機視窗中啟動 額外的 Claude 實例
echo "Starting Claude #2 in another terminal window..."
if [ "$(uname -s)" = "Darwin" ]; then
    osascript -e "tell app \"Terminal\" to do script \"cd '$(pwd)' && .venv/bin/python wrapper.py claude\"" > /dev/null 2>&1
else
    .venv/bin/python wrapper.py claude > data/claude_2.log 2>&1 &
fi

echo "Starting Claude #3 in another terminal window..."
if [ "$(uname -s)" = "Darwin" ]; then
    osascript -e "tell app \"Terminal\" to do script \"cd '$(pwd)' && .venv/bin/python wrapper.py claude\"" > /dev/null 2>&1
else
    .venv/bin/python wrapper.py claude > data/claude_3.log 2>&1 &
fi


# 4. 自動開啟瀏覽器聊天介面
echo "Opening browser to Chat UI..."
if command -v open >/dev/null 2>&1; then
    open http://localhost:8300
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open http://localhost:8300
fi

# 5. 在當前視窗啟動 第一個 Claude
echo "Starting Claude #1 in current terminal window..."
.venv/bin/python wrapper.py claude
