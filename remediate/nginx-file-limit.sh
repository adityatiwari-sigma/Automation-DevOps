#!/bin/bash

# Configuration
LOG_FILE="/var/log/auto-remediation.log"
REMOTE_IP="10.10.2.21"
NGINX_CONF="/etc/nginx/nginx.conf"
SYSCTL_CONF="/etc/sysctl.d/99-auto-remediation.conf"
SSH_CMD="ssh -o StrictHostKeyChecking=no root@$REMOTE_IP"

# 1. Exit 0 immediately if AUTO_REMEDIATE=false
if [ "${AUTO_REMEDIATE}" = "false" ]; then
    exit 0
fi

# 2. Run sysctl -w fs.file-max=100000 on REMOTE
$SSH_CMD "sysctl -w fs.file-max=100000" >/dev/null 2>&1

# 3. Persist it to /etc/sysctl.d/99-auto-remediation.conf on REMOTE
$SSH_CMD "mkdir -p /etc/sysctl.d && echo 'fs.file-max=100000' > $SYSCTL_CONF"

# 4. If worker_rlimit_nofile in /etc/nginx/nginx.conf is < 65535, patch it in-place on REMOTE
PATCH_APPLIED="false"
# Check existing rlimit on remote
RLIMIT=$($SSH_CMD "grep 'worker_rlimit_nofile' $NGINX_CONF" | awk '{print $2}' | tr -d ';' | head -n 1)

if [ -z "$RLIMIT" ] || [ "$RLIMIT" -lt 65535 ]; then
    # Backup and patch on remote
    $SSH_CMD "cp $NGINX_CONF $NGINX_CONF.bak"
    
    if [ -z "$RLIMIT" ]; then
        # Add after worker_processes
        $SSH_CMD "if grep -q 'worker_processes' $NGINX_CONF; then sed -i '/worker_processes/a worker_rlimit_nofile 65535;' $NGINX_CONF; else sed -i '1i worker_rlimit_nofile 65535;' $NGINX_CONF; fi"
    else
        # Replace
        $SSH_CMD "sed -i 's/worker_rlimit_nofile [0-9]*/worker_rlimit_nofile 65535/' $NGINX_CONF"
    fi
    
    # 5. Run nginx -t to validate, revert if test fails
    if $SSH_CMD "nginx -t" >/dev/null 2>&1; then
        PATCH_APPLIED="true"
        $SSH_CMD "rm $NGINX_CONF.bak"
    else
        $SSH_CMD "mv $NGINX_CONF.bak $NGINX_CONF"
        echo "$(date '+%Y-%m-%d %H:%M:%S') TRIGGER=\"nginx-file-limit\" ACTION=\"patch-nginx-remote\" STATUS=\"FAILED\" TARGET=\"$REMOTE_IP\" REASON=\"nginx -t validation failed\"" >> "$LOG_FILE"
    fi
fi

# 6. Run systemctl reload nginx (fallback to restart) on REMOTE
REMEDIATION_ACTION="reload"
if $SSH_CMD "systemctl reload nginx" >/dev/null 2>&1; then
    REMEDIATION_STATUS="SUCCESS"
else
    REMEDIATION_ACTION="restart"
    if $SSH_CMD "systemctl restart nginx" >/dev/null 2>&1; then
        REMEDIATION_STATUS="SUCCESS"
    else
        REMEDIATION_STATUS="FAILED"
    fi
fi

# 7. Log and POST Slack message
CURRENT_FILE_MAX=$($SSH_CMD "sysctl -n fs.file-max" 2>/dev/null || echo "unknown")
NGINX_STATE=$($SSH_CMD "systemctl is-active nginx" 2>/dev/null || echo "unknown")
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "$TIMESTAMP TRIGGER=\"nginx-file-limit\" ACTION=\"remote-$REMEDIATION_ACTION\" STATUS=\"$REMEDIATION_STATUS\" TARGET=\"$REMOTE_IP\" FILE_MAX=\"$CURRENT_FILE_MAX\"" >> "$LOG_FILE"

if [ -n "$SLACK_WEBHOOK_URL" ]; then
    PAYLOAD=$(cat <<EOF
{
  "text": "🚀 *Remote Nginx File Limits Tuned (IP: $REMOTE_IP)*",
  "attachments": [
    {
      "color": "$( [ "$REMEDIATION_STATUS" = "SUCCESS" ] && echo "good" || echo "danger" )",
      "fields": [
        {"title": "Target IP", "value": "$REMOTE_IP", "short": true},
        {"title": "fs.file-max", "value": "$CURRENT_FILE_MAX", "short": true},
        {"title": "Nginx Status", "value": "$NGINX_STATE", "short": true},
        {"title": "Config Patch", "value": "$PATCH_APPLIED", "short": true},
        {"title": "Action", "value": "remote-$REMEDIATION_ACTION", "short": true}
      ]
    }
  ]
}
EOF
)
    curl -s -X POST -H 'Content-type: application/json' --data "$PAYLOAD" "$SLACK_WEBHOOK_URL" > /dev/null
fi
