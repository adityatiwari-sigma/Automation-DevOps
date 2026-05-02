#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# NGINX UPSTREAM RESTART — restarts nginx on the remote server
# Use when nginx-file-limit.sh (reload) is insufficient:
# socket errors, upstream connection refused, config reload failed
# ============================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$(dirname "$DIR")/auto-remediation.log"
CONFIG_FILE="$(dirname "$DIR")/config.json"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# ── Safety guard: respect AUTO_REMEDIATE flag ──────────────
AUTO_REMEDIATE=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['webhook']['auto_remediate'])" 2>/dev/null || echo "True")
if [ "$AUTO_REMEDIATE" = "False" ]; then
    echo "[$TIMESTAMP] TRIGGER=\"restart_upstream\" STATUS=\"SKIPPED\" REASON=\"auto_remediate=false\"" >> "$LOG_FILE"
    exit 0
fi

REMOTE_IP=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['network']['remote_ip'])")

echo "[$TIMESTAMP] TRIGGER=\"restart_upstream\" ACTION=\"ssh-execute\" STATUS=\"STARTING\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"

# Test config first — never restart nginx with a broken config
if ! python3 "$DIR/ssh_helper.py" "nginx -t 2>&1"; then
    echo "[$TIMESTAMP] TRIGGER=\"restart_upstream\" ACTION=\"config-test\" STATUS=\"FAILED\" TARGET=\"$REMOTE_IP\" DETAIL=\"nginx -t failed — aborting restart\"" >> "$LOG_FILE"
    echo "ERROR: nginx config test failed on $REMOTE_IP — aborting to avoid outage." >&2
    exit 1
fi

if python3 "$DIR/ssh_helper.py" "systemctl restart nginx && systemctl is-active nginx"; then
    echo "[$TIMESTAMP] TRIGGER=\"restart_upstream\" ACTION=\"service-restart\" STATUS=\"SUCCESS\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"
    echo "Nginx restarted successfully on $REMOTE_IP."
else
    echo "[$TIMESTAMP] TRIGGER=\"restart_upstream\" ACTION=\"service-restart\" STATUS=\"FAILED\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"
    echo "ERROR: Nginx restart failed on $REMOTE_IP." >&2
    exit 1
fi
