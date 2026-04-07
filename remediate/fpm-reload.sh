#!/bin/bash

# ============================================================
# PHP-FPM REMEDIATION (INSTANT)
# ============================================================

LOG_FILE="/home/adityatiwari/Documents/AOPS/auto-remediation.log"
REMOTE_HOST="root@10.10.2.21"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] TRIGGER=\"fpm-reload\" ACTION=\"ssh-execute\" STATUS=\"STARTING\"" >> "$LOG_FILE"

# Execute reload via SSH
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$REMOTE_HOST" "systemctl reload php*-fpm"

if [ $? -eq 0 ]; then
    echo "[$TIMESTAMP] TRIGGER=\"fpm-reload\" ACTION=\"service-reload\" STATUS=\"SUCCESS\"" >> "$LOG_FILE"
    echo "PHP-FPM Reloaded successfully on 10.10.2.21."
else
    echo "[$TIMESTAMP] TRIGGER=\"fpm-reload\" ACTION=\"service-reload\" STATUS=\"FAILED\"" >> "$LOG_FILE"
    echo "Failed to reload PHP-FPM on 10.10.2.21."
fi
