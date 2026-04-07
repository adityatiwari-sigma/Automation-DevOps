#!/bin/bash

# ============================================================
# NGINX FILE-LIMIT REMEDIATION (INSTANT)
# ============================================================

LOG_FILE="/home/adityatiwari/Documents/AOPS/auto-remediation.log"
REMOTE_HOST="root@10.10.2.21"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] TRIGGER=\"nginx-limit\" ACTION=\"ssh-execute\" STATUS=\"STARTING\"" >> "$LOG_FILE"

# Apply sysctl and reload nginx
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$REMOTE_HOST" "sysctl -w fs.file-max=2097152 && systemctl reload nginx"

if [ $? -eq 0 ]; then
    echo "[$TIMESTAMP] TRIGGER=\"nginx-limit\" ACTION=\"sysctl-apply\" STATUS=\"SUCCESS\"" >> "$LOG_FILE"
    echo "Nginx limits increased and service reloaded on 10.10.2.21."
else
    echo "[$TIMESTAMP] TRIGGER=\"nginx-limit\" ACTION=\"sysctl-apply\" STATUS=\"FAILED\"" >> "$LOG_FILE"
    echo "Failed to apply Nginx limits on 10.10.2.21."
fi
