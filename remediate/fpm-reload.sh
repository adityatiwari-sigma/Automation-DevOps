#!/bin/bash

# Configuration
LOG_FILE="/var/log/auto-remediation.log"
REMOTE_IP="10.10.2.21"
FPM_STATUS_URL="http://$REMOTE_IP/fpm-status?json"
SERVICE_NAME="php8.4-fpm"
SSH_CMD="ssh -o StrictHostKeyChecking=no root@$REMOTE_IP"

if [ "${AUTO_REMEDIATE}" = "false" ]; then
    exit 0
fi


STATUS_JSON=$(curl -s -H "Host: dev.regenics.com" "$FPM_STATUS_URL")
if [ $? -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') TRIGGER=\"fpm-reload\" ACTION=\"check-status\" STATUS=\"FAILED\" REASON=\"curl failed to $REMOTE_IP\"" >> "$LOG_FILE"
    exit 1
fi

ACTIVE_PROCESSES=$(echo "$STATUS_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin).get('active processes', 0))" 2>/dev/null)
MAX_ACTIVE_PROCESSES=$(echo "$STATUS_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin).get('max active processes', 0))" 2>/dev/null)

if [ -z "$ACTIVE_PROCESSES" ] || [ -z "$MAX_ACTIVE_PROCESSES" ] || [ "$MAX_ACTIVE_PROCESSES" -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') TRIGGER=\"fpm-reload\" ACTION=\"parse-json\" STATUS=\"FAILED\"" >> "$LOG_FILE"
    exit 1
fi

SATURATION=$(python3 -c "print($ACTIVE_PROCESSES / $MAX_ACTIVE_PROCESSES)")

# 3. If saturation > 0.90, run systemctl reload php8.4-fpm (fallback to restart) on REMOTE
SATURATED=$(python3 -c "print('true' if $SATURATION > 0.90 else 'false')")

if [ "$SATURATED" = "true" ]; then
    REMEDIATION_ACTION="reload"
    if $SSH_CMD "systemctl reload $SERVICE_NAME" >/dev/null 2>&1; then
        REMEDIATION_STATUS="SUCCESS"
    else
        REMEDIATION_ACTION="restart"
        if $SSH_CMD "systemctl restart $SERVICE_NAME" >/dev/null 2>&1; then
            REMEDIATION_STATUS="SUCCESS"
        else
            REMEDIATION_STATUS="FAILED"
        fi
    fi

    # 4. Append structured line to /var/log/auto-remediation.log
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    echo "$TIMESTAMP TRIGGER=\"fpm-reload\" ACTION=\"remote-$REMEDIATION_ACTION\" STATUS=\"$REMEDIATION_STATUS\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"

    # 5. POST Slack message
    IS_ACTIVE=$($SSH_CMD "systemctl is-active $SERVICE_NAME" 2>/dev/null || echo "unknown")
    if [ -n "$SLACK_WEBHOOK_URL" ]; then
        PAYLOAD=$(cat <<EOF
{
  "text": "🛠 *Remote Auto-Remediation Triggered (dev.regenics.com)*",
  "attachments": [
    {
      "color": "$( [ "$REMEDIATION_STATUS" = "SUCCESS" ] && echo "good" || echo "danger" )",
      "fields": [
        {"title": "Target IP", "value": "$REMOTE_IP", "short": true},
        {"title": "Service", "value": "$SERVICE_NAME", "short": true},
        {"title": "Action", "value": "$REMEDIATION_ACTION", "short": true},
        {"title": "Status", "value": "$REMEDIATION_STATUS", "short": true},
        {"title": "Current State", "value": "$IS_ACTIVE", "short": true}
      ]
    }
  ]
}
EOF
)
        curl -s -X POST -H 'Content-type: application/json' --data "$PAYLOAD" "$SLACK_WEBHOOK_URL" > /dev/null
    fi
fi
