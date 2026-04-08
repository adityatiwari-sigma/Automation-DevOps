#!/bin/bash

# ============================================================
# PHP-FPM REMEDIATION (INSTANT)
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

echo "[$TIMESTAMP] TRIGGER=\"fpm-reload\" ACTION=\"ssh-execute\" STATUS=\"STARTING\"" >> "$LOG_FILE"

# Execute reload via Python SSH Helper
python3 "$DIR/ssh_helper.py" "$REMOTE_IP" "$SSH_USER" "$SSH_PASSWORD" "$SUDO_PASSWORD" "systemctl reload php8.4-fpm"

if [ $? -eq 0 ]; then
    echo "[$TIMESTAMP] TRIGGER=\"fpm-reload\" ACTION=\"service-reload\" STATUS=\"SUCCESS\"" >> "$LOG_FILE"
    echo "PHP-FPM Reloaded successfully on $REMOTE_IP."
else
    echo "[$TIMESTAMP] TRIGGER=\"fpm-reload\" ACTION=\"service-reload\" STATUS=\"FAILED\"" >> "$LOG_FILE"
    echo "Failed to reload PHP-FPM on $REMOTE_IP."
    exit 1
fi
