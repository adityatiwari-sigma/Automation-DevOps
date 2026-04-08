#!/bin/bash

# ============================================================
# NGINX FILE-LIMIT REMEDIATION (INSTANT)
# ============================================================

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_FILE="$(dirname "$DIR")/auto-remediation.log"
ENV_FILE="$(dirname "$DIR")/.env"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE"
fi

REMOTE_IP="${REMOTE_IP:-10.10.2.21}"
SSH_USER="${SSH_USER:-root}"
SSH_PASSWORD="${SSH_PASSWORD:-}"
SUDO_PASSWORD="${SUDO_PASSWORD:-}"

echo "[$TIMESTAMP] TRIGGER=\"nginx-limit\" ACTION=\"ssh-execute\" STATUS=\"STARTING\"" >> "$LOG_FILE"

# Apply sysctl and reload nginx
python3 "$DIR/ssh_helper.py" "$REMOTE_IP" "$SSH_USER" "$SSH_PASSWORD" "$SUDO_PASSWORD" "sysctl -w fs.file-max=100000 && systemctl reload nginx"

if [ $? -eq 0 ]; then
    echo "[$TIMESTAMP] TRIGGER=\"nginx-limit\" ACTION=\"sysctl-apply\" STATUS=\"SUCCESS\"" >> "$LOG_FILE"
    echo "Nginx limits increased and service reloaded on $REMOTE_IP."
else
    echo "[$TIMESTAMP] TRIGGER=\"nginx-limit\" ACTION=\"sysctl-apply\" STATUS=\"FAILED\"" >> "$LOG_FILE"
    echo "Failed to apply Nginx limits on $REMOTE_IP."
    exit 1
fi
