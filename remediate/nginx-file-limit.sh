#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# NGINX FILE-LIMIT REMEDIATION
# Increases fs.file-max and reloads nginx on the remote server
# ============================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$(dirname "$DIR")/auto-remediation.log"
CONFIG_FILE="$(dirname "$DIR")/config.json"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# ── Safety guard: respect AUTO_REMEDIATE flag ──────────────
AUTO_REMEDIATE=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['webhook']['auto_remediate'])" 2>/dev/null || echo "True")
if [ "$AUTO_REMEDIATE" = "False" ]; then
    echo "[$TIMESTAMP] TRIGGER=\"nginx-file-limit\" STATUS=\"SKIPPED\" REASON=\"auto_remediate=false\"" >> "$LOG_FILE"
    exit 0
fi

REMOTE_IP=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['network']['remote_ip'])")

echo "[$TIMESTAMP] TRIGGER=\"nginx-file-limit\" ACTION=\"ssh-execute\" STATUS=\"STARTING\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"

if python3 "$DIR/ssh_helper.py" "sysctl -w fs.file-max=100000 && systemctl reload nginx"; then
    echo "[$TIMESTAMP] TRIGGER=\"nginx-file-limit\" ACTION=\"sysctl-apply\" STATUS=\"SUCCESS\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"
    echo "Nginx file limits increased and service reloaded on $REMOTE_IP."
else
    echo "[$TIMESTAMP] TRIGGER=\"nginx-file-limit\" ACTION=\"sysctl-apply\" STATUS=\"FAILED\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"
    echo "ERROR: Nginx limit remediation failed on $REMOTE_IP." >&2
    exit 1
fi
