#!/usr/bin/env bash
# ==============================================================================
# Cloudflare Tunnel Manager for Agent Router Web GUI
# Exposes local web server (default port 8080) to the internet securely via HTTPS
# ==============================================================================

PORT="${2:-8080}"
ACTION="${1:-start}"
PID_FILE="/tmp/agent_router_tunnel.pid"
LOG_FILE="$(pwd)/tunnel.log"
URL_FILE="/tmp/agent_router_tunnel.url"

# Locate cloudflared binary
CLOUDFLARED_BIN=""
if command -v cloudflared &>/dev/null; then
    CLOUDFLARED_BIN="$(command -v cloudflared)"
elif [ -f "$HOME/.local/bin/cloudflared" ]; then
    CLOUDFLARED_BIN="$HOME/.local/bin/cloudflared"
elif [ -f "/usr/local/bin/cloudflared" ]; then
    CLOUDFLARED_BIN="/usr/local/bin/cloudflared"
fi

if [ -z "$CLOUDFLARED_BIN" ]; then
    echo -e "\033[1;31m[ERROR]\033[0m cloudflared binary not found!"
    echo "Install it via: curl -fsSL https://pkg.cloudflare.com/cloudflared-ascii.repo"
    exit 1
fi

start_tunnel() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo -e "\033[1;33m[WARN]\033[0m Tunnel is already running with PID $(cat "$PID_FILE")"
        if [ -f "$URL_FILE" ]; then
            echo -e "\033[1;32m[URL]\033[0m $(cat "$URL_FILE")"
        fi
        return 0
    fi

    echo -e "\033[1;36m[INFO]\033[0m Starting Cloudflare Tunnel for http://127.0.0.1:${PORT}..."
    rm -f "$LOG_FILE" "$URL_FILE"

    "$CLOUDFLARED_BIN" tunnel --url "http://127.0.0.1:${PORT}" > "$LOG_FILE" 2>&1 &
    TUNNEL_PID=$!
    echo "$TUNNEL_PID" > "$PID_FILE"

    # Wait for the tunnel URL to appear in logs
    echo -n "Waiting for public URL"
    FOUND_URL=""
    for i in {1..30}; do
        if [ -f "$LOG_FILE" ]; then
            FOUND_URL=$(grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' "$LOG_FILE" | head -n 1)
            if [ -n "$FOUND_URL" ]; then
                echo "$FOUND_URL" > "$URL_FILE"
                break
            fi
        fi
        echo -n "."
        sleep 0.5
    done
    echo ""

    if [ -n "$FOUND_URL" ]; then
        echo -e "\033[1;32m============================================================\033[0m"
        echo -e "\033[1;32m  Tunnel successfully established!\033[0m"
        echo -e "\033[1;37m  Public URL: \033[1;36m${FOUND_URL}\033[0m"
        echo -e "\033[1;37m  Local URL:  \033[1;33mhttp://127.0.0.1:${PORT}\033[0m"
        echo -e "\033[1;32m============================================================\033[0m"
    else
        echo -e "\033[1;33m[WARN]\033[0m Tunnel started with PID ${TUNNEL_PID}, but URL not yet extracted."
        echo "Check log file: ${LOG_FILE}"
    fi
}

stop_tunnel() {
    if [ -f "$PID_FILE" ]; then
        PID="$(cat "$PID_FILE")"
        if kill -0 "$PID" 2>/dev/null; then
            echo -e "\033[1;36m[INFO]\033[0m Stopping tunnel process (PID: $PID)..."
            kill "$PID" 2>/dev/null
            sleep 1
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID" 2>/dev/null
            fi
            echo -e "\033[1;32m[OK]\033[0m Tunnel stopped."
        else
            echo -e "\033[1;33m[INFO]\033[0m Process $PID is not running."
        fi
        rm -f "$PID_FILE" "$URL_FILE"
    else
        echo -e "\033[1;33m[INFO]\033[0m No tunnel PID file found."
    fi
}

status_tunnel() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo -e "\033[1;32m[STATUS]\033[0m Tunnel is RUNNING (PID: $(cat "$PID_FILE"))"
        if [ -f "$URL_FILE" ]; then
            echo -e "\033[1;37m[URL]\033[0m $(cat "$URL_FILE")"
        elif [ -f "$LOG_FILE" ]; then
            URL=$(grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' "$LOG_FILE" | head -n 1)
            [ -n "$URL" ] && echo -e "\033[1;37m[URL]\033[0m $URL"
        fi
    else
        echo -e "\033[1;31m[STATUS]\033[0m Tunnel is STOPPED"
    fi
}

get_url() {
    if [ -f "$URL_FILE" ]; then
        cat "$URL_FILE"
    elif [ -f "$LOG_FILE" ]; then
        grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' "$LOG_FILE" | head -n 1
    else
        echo "Tunnel URL not available"
    fi
}

case "$ACTION" in
    start)
        start_tunnel
        ;;
    stop)
        stop_tunnel
        ;;
    restart)
        stop_tunnel
        sleep 1
        start_tunnel
        ;;
    status)
        status_tunnel
        ;;
    url)
        get_url
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|url} [port]"
        exit 1
        ;;
esac
