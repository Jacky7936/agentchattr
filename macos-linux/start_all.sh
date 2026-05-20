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

SERVER_URL="http://192.168.50.201:8300"
SERVER_CMD="printf 'YES\\n' | .venv/bin/python run.py --allow-network"

# Detect if running inside a CMux terminal session
IS_CMUX=0
CMUX_BIN="cmux"

if [ -n "$CMUX_WORKSPACE_ID" ] && [ -n "$CMUX_SOCKET_PATH" ]; then
    if command -v cmux >/dev/null 2>&1; then
        IS_CMUX=1
    elif [ -x "/Applications/cmux.app/Contents/Resources/bin/cmux" ]; then
        CMUX_BIN="/Applications/cmux.app/Contents/Resources/bin/cmux"
        IS_CMUX=1
    elif [ -x "/Applications/CMux.app/Contents/Resources/bin/cmux" ]; then
        CMUX_BIN="/Applications/CMux.app/Contents/Resources/bin/cmux"
        IS_CMUX=1
    fi
fi

run_in_new_cmux_tab() {
    local cmd="$1"
    local name="$2"
    echo "Starting $name in new cmux tab..."
    local surface_id
    surface_id=$("$CMUX_BIN" new-surface)

    # Extract only the surface ID reference (e.g. "surface:11") from the output
    surface_id=$(echo "$surface_id" | grep -o -E "surface:[0-9]+")

    # Wait for the terminal shell in the new tab to initialize before sending keys
    sleep 0.5

    if [ -n "$surface_id" ]; then
        "$CMUX_BIN" rename-tab --surface "$surface_id" "$name" >/dev/null 2>&1
        "$CMUX_BIN" send --surface "$surface_id" "cd '$(pwd)' && $cmd"
        "$CMUX_BIN" send-key --surface "$surface_id" "Return"
    else
        "$CMUX_BIN" new-surface --focus true >/dev/null
        sleep 0.5
        "$CMUX_BIN" rename-tab "$name" >/dev/null 2>&1
        "$CMUX_BIN" send "cd '$(pwd)' && $cmd"
        "$CMUX_BIN" send-key "Return"
    fi
}

start_agent() {
    local cmd="$1"
    local name="$2"
    local log_file="$3"

    if [ "$IS_CMUX" -eq 1 ]; then
        run_in_new_cmux_tab "$cmd" "$name"
    else
        echo "Starting $name..."
        if [ "$(uname -s)" = "Darwin" ]; then
            osascript -e "tell app \"Terminal\" to do script \"cd '$(pwd)' && $cmd\"" > /dev/null 2>&1
        else
            $cmd > "$log_file" 2>&1 &
        fi
    fi
}

# 1. 啟動伺服器 (若未運行)
if ! is_server_running; then
    if [ "$IS_CMUX" -eq 1 ]; then
        run_in_new_cmux_tab "$SERVER_CMD" "Server"
    else
        echo "Starting agentchattr server..."
        if [ "$(uname -s)" = "Darwin" ]; then
            osascript -e "tell app \"Terminal\" to do script \"cd '$(pwd)' && $SERVER_CMD\"" > /dev/null 2>&1
        else
            sh -c "$SERVER_CMD" > data/server.log 2>&1 &
        fi
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
start_agent ".venv/bin/python wrapper.py codex --profile codex-planner --dangerously-bypass-approvals-and-sandbox" "Codex Planner" "data/codex_planner.log"
start_agent ".venv/bin/python wrapper.py codex --profile codex-builder --dangerously-bypass-approvals-and-sandbox" "Codex Builder" "data/codex_builder.log"
start_agent ".venv/bin/python wrapper.py codex --profile codex-reviewer --dangerously-bypass-approvals-and-sandbox" "Codex Reviewer" "data/codex_reviewer.log"
start_agent ".venv/bin/python wrapper.py codex --profile codex-architect --dangerously-bypass-approvals-and-sandbox" "Codex Architect" "data/codex_architect.log"

# 3. 在新終端機視窗中啟動 額外的 Claude 實例
start_agent ".venv/bin/python wrapper.py claude --profile claude-reviewer" "Claude Reviewer" "data/claude_reviewer.log"
start_agent ".venv/bin/python wrapper.py claude --profile claude-researcher" "Claude Researcher" "data/claude_researcher.log"

# 4. 自動開啟瀏覽器聊天介面
echo "Opening browser to Chat UI..."
if [ "$IS_CMUX" -eq 1 ]; then
    "$CMUX_BIN" browser open "$SERVER_URL" --focus false >/dev/null 2>&1 || {
        open "$SERVER_URL" >/dev/null 2>&1
    }
else
    if command -v open >/dev/null 2>&1; then
        open "$SERVER_URL"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$SERVER_URL"
    fi
fi

# 5. 在當前視窗啟動 第一個 Claude
if [ "$IS_CMUX" -eq 1 ]; then
    "$CMUX_BIN" rename-tab "Claude Designer" >/dev/null 2>&1
fi
echo "Starting Claude Designer in current terminal window..."
.venv/bin/python wrapper.py claude --profile claude-designer
